"""
IDS Peak Camera Integration for WallDance.

Optimized for lowest glass-to-GPU latency:
- IDS U3-34E0XCP-M-GL (4MP Sony IMX664 Starvis 2, Monochrome)
- Mono10/Mono12 acquisition for maximum dynamic range in IR low-light
- Newest-frame-only buffer strategy (drop stale frames)
- Zero-copy where possible, GPU-accelerated Mono→BGR conversion

Pipeline:
    IDS Camera (Mono10/12)
        │
        ▼ [IDS Peak IPL: Mono10→Mono8]
    Mono8 numpy array
        │
        ▼ [torch: GPU upload + expand to BGR]
    GPU Tensor (1,3,H,W) float32
        │
        ▼ [existing gpu_pipeline.py]
    YOLO inference

Usage:
    camera = IDSCamera()
    if camera.open():
        while running:
            ret, frame = camera.read()  # Returns BGR numpy or None
            # or for GPU path:
            gpu_tensor = camera.read_gpu()  # Returns (1,3,H,W) GPU tensor
        camera.close()
"""

from __future__ import annotations

import glob
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, List, Optional, Tuple
import numpy as np

# Check for IDS Peak SDK
IDS_PEAK_AVAILABLE = False
try:
    from ids_peak import ids_peak
    from ids_peak_ipl import ids_peak_ipl
    from ids_peak import ids_peak_ipl_extension
    IDS_PEAK_AVAILABLE = True
except ImportError:
    ids_peak = None
    ids_peak_ipl = None
    ids_peak_ipl_extension = None

# Check for PyTorch/CUDA for GPU conversion
TORCH_AVAILABLE = False
CUDA_AVAILABLE = False
try:
    import torch
    TORCH_AVAILABLE = True
    CUDA_AVAILABLE = torch.cuda.is_available()
except ImportError:
    torch = None


class PixelFormat(Enum):
    """Supported pixel formats in priority order."""
    MONO12 = auto()  # Best dynamic range
    MONO10 = auto()  # Good dynamic range
    MONO8 = auto()   # Fastest, least dynamic range


@dataclass
class IDSCameraInfo:
    """Information about a detected IDS camera."""
    model: str
    serial: str
    interface: str
    device_id: str  # Unique ID for opening


@dataclass
class IDSCameraState:
    """Current state of the IDS camera."""
    is_open: bool = False
    is_acquiring: bool = False
    width: int = 0
    height: int = 0
    fps: float = 0.0
    pixel_format: str = ""
    exposure_us: float = 0.0
    gain_db: float = 0.0
    frame_count: int = 0
    dropped_frames: int = 0
    last_frame_time: float = 0.0


@dataclass 
class IDSCameraSettings:
    """Settings for IDS camera acquisition."""
    # Resolution (0 = max available)
    width: int = 0
    height: int = 0
    
    # Frame rate (0 = max available)
    target_fps: float = 30.0
    
    # Exposure (0 = auto, otherwise microseconds)
    exposure_us: float = 0.0  # 0 = auto
    exposure_auto: bool = True
    
    # Gain (0 = auto, otherwise dB)
    gain_db: float = 0.0  # 0 = minimum
    gain_auto: bool = False
    
    # Buffer strategy
    buffer_count: int = 3  # Minimum for smooth acquisition
    newest_only: bool = True  # Drop old frames, keep only newest
    
    # Pixel format preference (will try in order)
    prefer_high_bit_depth: bool = True  # Prefer Mono10/12 over Mono8


class IDSCamera:
    """
    IDS Peak camera wrapper optimized for low-latency acquisition.
    
    Features:
    - Automatic camera discovery and connection
    - Mono10/12 → Mono8 conversion via IDS IPL
    - GPU-accelerated Mono8 → BGR expansion
    - Newest-frame-only buffer strategy
    - Threaded acquisition for consistent frame rate
    """
    
    def __init__(self, settings: Optional[IDSCameraSettings] = None):
        """Initialize IDS camera wrapper.
        
        Args:
            settings: Camera settings. If None, uses defaults.
        """
        if not IDS_PEAK_AVAILABLE:
            raise RuntimeError(
                "IDS Peak SDK not available. Install with:\n"
                "  pip install ids-peak ids-peak-ipl\n"
                "Or download from: https://en.ids-imaging.com/ids-peak.html"
            )
        
        self.settings = settings or IDSCameraSettings()
        self.state = IDSCameraState()
        
        # IDS Peak objects
        self._device: Optional[ids_peak.Device] = None
        self._datastream: Optional[ids_peak.DataStream] = None
        self._node_map: Optional[ids_peak.NodeMap] = None
        
        # Image converter for Mono10/12 → Mono8
        self._converter: Optional[ids_peak_ipl.ImageConverter] = None
        
        # Threading
        self._acquire_thread: Optional[threading.Thread] = None
        self._acquire_running: bool = False
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None  # Mono8 numpy
        self._latest_timestamp: float = 0.0
        self._frame_ready: bool = False
        self._acquire_error: Optional[str] = None
        
        # GPU tensor cache (for read_gpu)
        self._gpu_tensor: Optional[torch.Tensor] = None
        
        # Callback for recording
        self._frame_callback: Optional[Callable[[np.ndarray], None]] = None
        
        # Initialize IDS Peak library (idempotent)
        self._acquire_library()
    
    def __del__(self):
        """Cleanup on destruction."""
        self.close()
        try:
            self._release_library()
        except:
            pass

    # ------------------------------------------------------------------
    # Library lifecycle
    # ------------------------------------------------------------------
    _library_lock = threading.Lock()
    _library_initialized = False
    _library_refcount = 0
    _gentl_checked = False
    _gentl_paths_cached: Optional[str] = None

    @classmethod
    def _ensure_gentl_path(cls) -> None:
        """Ensure GENICAM_GENTL64_PATH is set for IDS Peak CTI discovery."""
        if cls._gentl_checked:
            return

        cls._gentl_checked = True

        if os.environ.get("GENICAM_GENTL64_PATH"):
            return

        # Common IDS Peak install locations (Linux)
        candidates = [
            "/opt/ids/ids-peak",
            "/opt/ids/ids-peak/cti",
            "/opt/ids/ids-peak/lib",
            "/opt/ids/ids-peak/bin",
            "/opt/ids/ids-peak/ids-peak",
            "/opt/ids/ids-peak/ids-peak/cti",
            "/usr/local/ids/ids-peak",
            "/usr/local/ids/ids-peak/cti",
            "/usr/local/lib",
            "/usr/lib",
            "/home/*/Programs/ids-peak*",
            "/home/*/Programs/ids-peak*/lib/x86_64-linux-gnu/ids-peak/cti",
        ]

        cti_dirs = set()
        expanded_candidates = []
        for base in candidates:
            if "*" in base:
                expanded_candidates.extend(glob.glob(base))
            else:
                expanded_candidates.append(base)

        for base in expanded_candidates:
            if not os.path.isdir(base):
                continue
            # Look for *.cti in base and one level below
            for path in glob.glob(os.path.join(base, "*.cti")):
                cti_dirs.add(os.path.dirname(path))
            for path in glob.glob(os.path.join(base, "*", "*.cti")):
                cti_dirs.add(os.path.dirname(path))

        if cti_dirs:
            value = ":".join(sorted(cti_dirs))
            os.environ["GENICAM_GENTL64_PATH"] = value
            cls._gentl_paths_cached = value
            print(f"[IDSCamera] GENICAM_GENTL64_PATH set to: {value}")
        else:
            print(
                "[IDSCamera] GENICAM_GENTL64_PATH not set and no CTI found. "
                "Install IDS Peak or export GENICAM_GENTL64_PATH to the CTI directory."
            )

    @classmethod
    def _ensure_library_initialized(cls) -> None:
        """Initialize IDS Peak library once per process."""
        with cls._library_lock:
            if not cls._library_initialized:
                cls._ensure_gentl_path()
                ids_peak.Library.Initialize()
                cls._library_initialized = True

    @classmethod
    def _acquire_library(cls) -> None:
        """Ensure library is initialized and bump refcount."""
        with cls._library_lock:
            if not cls._library_initialized:
                cls._ensure_gentl_path()
                ids_peak.Library.Initialize()
                cls._library_initialized = True
            cls._library_refcount += 1

    @classmethod
    def _release_library(cls) -> None:
        """Decrement refcount and close library when unused."""
        with cls._library_lock:
            if cls._library_refcount > 0:
                cls._library_refcount -= 1
            if cls._library_refcount == 0 and cls._library_initialized:
                ids_peak.Library.Close()
                cls._library_initialized = False
    
    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @staticmethod
    def list_cameras() -> List[IDSCameraInfo]:
        """List all available IDS cameras.
        
        Returns:
            List of IDSCameraInfo for each detected camera.
        """
        if not IDS_PEAK_AVAILABLE:
            return []
        
        cameras = []
        try:
            IDSCamera._ensure_library_initialized()
            # Update device manager
            device_manager = ids_peak.DeviceManager.Instance()
            device_manager.Update()
            
            for device in device_manager.Devices():
                try:
                    info = IDSCameraInfo(
                        model=device.ModelName(),
                        serial=device.SerialNumber(),
                        interface=device.ParentInterface().DisplayName(),
                        device_id=f"{device.SerialNumber()}@{device.ParentInterface().DisplayName()}"
                    )
                    cameras.append(info)
                except Exception as e:
                    print(f"[IDSCamera] Error reading device info: {e}")
        except Exception as e:
            print(f"[IDSCamera] Error listing cameras: {e}")
        
        return cameras
    
    @staticmethod
    def find_camera(serial: Optional[str] = None) -> Optional[IDSCameraInfo]:
        """Find a specific camera or the first available.
        
        Args:
            serial: Serial number to find. If None, returns first camera.
            
        Returns:
            IDSCameraInfo if found, None otherwise.
        """
        cameras = IDSCamera.list_cameras()
        if not cameras:
            return None
        
        if serial is None:
            return cameras[0]
        
        for cam in cameras:
            if cam.serial == serial:
                return cam
        
        return None
    
    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def open(self, serial: Optional[str] = None) -> bool:
        """Open connection to IDS camera.
        
        Args:
            serial: Serial number of camera to open. If None, opens first available.
            
        Returns:
            True if successful, False otherwise.
        """
        if self.state.is_open:
            self.close()
        
        try:
            self._ensure_library_initialized()
            # Find camera
            device_manager = ids_peak.DeviceManager.Instance()
            device_manager.Update()
            
            target_device = None
            for device in device_manager.Devices():
                if serial is None or device.SerialNumber() == serial:
                    # Check if accessible
                    if device.IsOpenable():
                        target_device = device
                        break
            
            if target_device is None:
                print(f"[IDSCamera] No accessible camera found (serial={serial})")
                return False
            
            # Open device
            self._device = target_device.OpenDevice(ids_peak.DeviceAccessType_Exclusive)
            self._node_map = self._device.RemoteDevice().NodeMaps()[0]
            
            print(f"[IDSCamera] Opened: {target_device.ModelName()} (SN: {target_device.SerialNumber()})")
            
            # Configure camera
            self._configure_camera()
            
            # Open data stream
            datastreams = self._device.DataStreams()
            if datastreams.empty():
                print("[IDSCamera] No data streams available")
                self.close()
                return False
            
            self._datastream = datastreams[0].OpenDataStream()
            
            # Allocate and queue buffers
            self._allocate_buffers()
            
            # Create image converter
            self._converter = ids_peak_ipl.ImageConverter()
            
            self.state.is_open = True
            return True
            
        except Exception as e:
            print(f"[IDSCamera] Failed to open camera: {e}")
            self.close()
            return False
    
    def _configure_camera(self) -> None:
        """Configure camera settings (resolution, FPS, exposure, etc.)."""
        nm = self._node_map
        
        # --- Pixel Format ---
        # Try formats in preference order
        formats_to_try = []
        if self.settings.prefer_high_bit_depth:
            formats_to_try = ["Mono12", "Mono10", "Mono8"]
        else:
            formats_to_try = ["Mono8", "Mono10", "Mono12"]
        
        pixel_format_node = nm.FindNode("PixelFormat")
        available_formats = []
        for entry in pixel_format_node.Entries():
            if entry.AccessStatus() != ids_peak.NodeAccessStatus_NotAvailable:
                available_formats.append(entry.SymbolicValue())
        
        selected_format = None
        for fmt in formats_to_try:
            if fmt in available_formats:
                selected_format = fmt
                break
        
        if selected_format is None and available_formats:
            # Fallback: pick the first available format
            selected_format = available_formats[0]

        if selected_format:
            pixel_format_node.SetCurrentEntry(
                pixel_format_node.FindEntry(selected_format)
            )
            self.state.pixel_format = selected_format
            print(f"[IDSCamera] Pixel format: {selected_format}")
        
        # --- Resolution ---
        width_node = nm.FindNode("Width")
        height_node = nm.FindNode("Height")
        
        if self.settings.width > 0:
            target_w = min(self.settings.width, width_node.Maximum())
        else:
            target_w = width_node.Maximum()
        
        if self.settings.height > 0:
            target_h = min(self.settings.height, height_node.Maximum())
        else:
            target_h = height_node.Maximum()
        
        # Respect increment
        w_inc = width_node.Increment()
        h_inc = height_node.Increment()
        target_w = (target_w // w_inc) * w_inc
        target_h = (target_h // h_inc) * h_inc
        
        width_node.SetValue(target_w)
        height_node.SetValue(target_h)
        
        self.state.width = width_node.Value()
        self.state.height = height_node.Value()
        print(f"[IDSCamera] Resolution: {self.state.width}x{self.state.height}")
        
        # --- Frame Rate ---
        try:
            # First, set acquisition frame rate enable if available
            try:
                fps_enable = nm.FindNode("AcquisitionFrameRateEnable")
                fps_enable.SetValue(True)
            except:
                pass
            
            fps_node = nm.FindNode("AcquisitionFrameRate")
            max_fps = fps_node.Maximum()
            target_fps = min(self.settings.target_fps, max_fps) if self.settings.target_fps > 0 else max_fps
            fps_node.SetValue(target_fps)
            self.state.fps = fps_node.Value()
            print(f"[IDSCamera] Frame rate: {self.state.fps:.1f} FPS (max: {max_fps:.1f})")
        except Exception as e:
            print(f"[IDSCamera] Could not set frame rate: {e}")
        
        # --- Exposure ---
        try:
            if self.settings.exposure_auto:
                # Enable auto exposure
                try:
                    auto_node = nm.FindNode("ExposureAuto")
                    auto_node.SetCurrentEntry(auto_node.FindEntry("Continuous"))
                    print("[IDSCamera] Exposure: Auto (Continuous)")
                except:
                    print("[IDSCamera] Auto exposure not available, using manual")
                    self.settings.exposure_auto = False
            
            if not self.settings.exposure_auto and self.settings.exposure_us > 0:
                exp_node = nm.FindNode("ExposureTime")
                target_exp = max(exp_node.Minimum(), 
                               min(self.settings.exposure_us, exp_node.Maximum()))
                exp_node.SetValue(target_exp)
                self.state.exposure_us = exp_node.Value()
                print(f"[IDSCamera] Exposure: {self.state.exposure_us:.0f} µs")
        except Exception as e:
            print(f"[IDSCamera] Could not configure exposure: {e}")
        
        # --- Gain ---
        try:
            if self.settings.gain_auto:
                try:
                    auto_node = nm.FindNode("GainAuto")
                    auto_node.SetCurrentEntry(auto_node.FindEntry("Continuous"))
                    print("[IDSCamera] Gain: Auto (Continuous)")
                except:
                    print("[IDSCamera] Auto gain not available")
            else:
                gain_node = nm.FindNode("Gain")
                if self.settings.gain_db > 0:
                    target_gain = max(gain_node.Minimum(),
                                    min(self.settings.gain_db, gain_node.Maximum()))
                    gain_node.SetValue(target_gain)
                self.state.gain_db = gain_node.Value()
                print(f"[IDSCamera] Gain: {self.state.gain_db:.1f} dB")
        except Exception as e:
            print(f"[IDSCamera] Could not configure gain: {e}")
    
    def _allocate_buffers(self) -> None:
        """Allocate and queue acquisition buffers."""
        if self._datastream is None:
            return
        
        # Flush any existing buffers
        self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
        
        # Revoke old buffers
        for buffer in self._datastream.AnnouncedBuffers():
            self._datastream.RevokeBuffer(buffer)
        
        # Calculate buffer size
        payload_size = self._node_map.FindNode("PayloadSize").Value()
        
        # Allocate new buffers
        for _ in range(self.settings.buffer_count):
            buffer = self._datastream.AllocAndAnnounceBuffer(payload_size)
            self._datastream.QueueBuffer(buffer)
        
        print(f"[IDSCamera] Allocated {self.settings.buffer_count} buffers "
              f"({payload_size} bytes each)")
    
    def close(self) -> None:
        """Close camera connection and cleanup."""
        # Stop acquisition if running
        self.stop_acquisition()
        
        # Stop and flush data stream
        if self._datastream is not None:
            try:
                self._datastream.KillWait()
                self._datastream.StopAcquisition(ids_peak.AcquisitionStopMode_Default)
                self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
                
                # Revoke buffers
                for buffer in self._datastream.AnnouncedBuffers():
                    self._datastream.RevokeBuffer(buffer)
            except:
                pass
            self._datastream = None
        
        # Stop camera acquisition
        if self._node_map is not None:
            try:
                self._node_map.FindNode("AcquisitionStop").Execute()
                self._node_map.FindNode("AcquisitionStop").WaitUntilDone()
            except:
                pass
            self._node_map = None
        
        # Close device
        if self._device is not None:
            self._device = None
        
        self._converter = None
        self.state.is_open = False
        self.state.is_acquiring = False
        print("[IDSCamera] Closed")
    
    # ------------------------------------------------------------------
    # Acquisition
    # ------------------------------------------------------------------
    def start_acquisition(self) -> bool:
        """Start continuous frame acquisition in background thread.
        
        Returns:
            True if started successfully.
        """
        if not self.state.is_open:
            print("[IDSCamera] Cannot start acquisition: camera not open")
            return False
        
        if self.state.is_acquiring:
            return True
        
        try:
            # Start data stream first (some cameras require this order)
            self._datastream.StartAcquisition()
            
            # Start camera acquisition
            self._node_map.FindNode("AcquisitionStart").Execute()
            
            # Start acquisition thread
            self._acquire_error = None
            self._acquire_running = True
            self._acquire_thread = threading.Thread(
                target=self._acquisition_loop,
                name="IDSAcquisition",
                daemon=True
            )
            self._acquire_thread.start()
            
            self.state.is_acquiring = True
            print("[IDSCamera] Acquisition started")
            return True
            
        except Exception as e:
            # Retry with reversed order for compatibility
            try:
                self._datastream.StopAcquisition(ids_peak.AcquisitionStopMode_Default)
            except Exception:
                pass
            try:
                self._node_map.FindNode("AcquisitionStart").Execute()
                self._datastream.StartAcquisition()
                self._acquire_error = None
                self._acquire_running = True
                self._acquire_thread = threading.Thread(
                    target=self._acquisition_loop,
                    name="IDSAcquisition",
                    daemon=True
                )
                self._acquire_thread.start()
                self.state.is_acquiring = True
                print("[IDSCamera] Acquisition started (fallback order)")
                return True
            except Exception as e2:
                print(f"[IDSCamera] Failed to start acquisition: {e2}")
                return False
    
    def stop_acquisition(self) -> None:
        """Stop frame acquisition."""
        if not self.state.is_acquiring:
            return
        
        # Signal thread to stop
        self._acquire_running = False
        
        # Wake up any waiting
        if self._datastream is not None:
            try:
                self._datastream.KillWait()
            except:
                pass
        
        # Wait for thread
        if self._acquire_thread is not None:
            self._acquire_thread.join(timeout=2.0)
            self._acquire_thread = None
        
        # Stop data stream and camera
        if self._datastream is not None:
            try:
                self._datastream.StopAcquisition(ids_peak.AcquisitionStopMode_Default)
            except:
                pass
        
        if self._node_map is not None:
            try:
                self._node_map.FindNode("AcquisitionStop").Execute()
            except:
                pass
        
        self.state.is_acquiring = False
        print("[IDSCamera] Acquisition stopped")
    
    def _acquisition_loop(self) -> None:
        """Background thread: continuously acquire frames."""
        print("[IDSCamera] Acquisition thread started")
        
        # Timeout in ms (slightly longer than frame period)
        timeout_ms = int(2000.0 / max(1.0, self.state.fps))
        
        while self._acquire_running:
            try:
                # Wait for buffer with timeout
                buffer = self._datastream.WaitForFinishedBuffer(timeout_ms)
                
                if not self._acquire_running:
                    # Re-queue and exit
                    self._datastream.QueueBuffer(buffer)
                    break
                
                # Process buffer
                frame_mono8 = self._process_buffer(buffer)
                
                # Re-queue buffer immediately (before any slow operations)
                self._datastream.QueueBuffer(buffer)
                
                if frame_mono8 is not None:
                    timestamp = time.perf_counter()
                    
                    # Update latest frame (thread-safe)
                    with self._frame_lock:
                        # If newest_only and frame exists, count as dropped
                        if self.settings.newest_only and self._frame_ready:
                            self.state.dropped_frames += 1
                        
                        self._latest_frame = frame_mono8
                        self._latest_timestamp = timestamp
                        self._frame_ready = True
                        self.state.frame_count += 1
                        self.state.last_frame_time = timestamp
                    
                    # Callback for recording (outside lock)
                    if self._frame_callback is not None:
                        try:
                            # Convert to BGR for callback
                            bgr = self._mono8_to_bgr_cpu(frame_mono8)
                            self._frame_callback(bgr)
                        except Exception as e:
                            print(f"[IDSCamera] Frame callback error: {e}")
                
            except ids_peak.Exception as e:
                if self._acquire_running:
                    # Check if it's just a timeout
                    if "timeout" in str(e).lower():
                        continue
                    print(f"[IDSCamera] Acquisition error: {e}")
                    self._acquire_error = str(e)
                break
            except Exception as e:
                if self._acquire_running:
                    print(f"[IDSCamera] Unexpected error: {e}")
                    self._acquire_error = str(e)
                break
        
        print("[IDSCamera] Acquisition thread finished")
    
    def _process_buffer(self, buffer) -> Optional[np.ndarray]:
        """Convert IDS buffer to Mono8 numpy array.
        
        Handles Mono10/Mono12 → Mono8 conversion via IDS IPL.
        
        Args:
            buffer: IDS Peak buffer object
            
        Returns:
            Mono8 numpy array (H, W) or None on error
        """
        try:
            # Create IPL image from buffer
            ipl_image = ids_peak_ipl_extension.BufferToImage(buffer)
            
            # Get pixel format
            pixel_format = ipl_image.PixelFormat()
            
            # Convert to Mono8 if needed
            if pixel_format != ids_peak_ipl.PixelFormatName_Mono8:
                # Use pre-allocated converter for efficiency
                ipl_image = self._converter.Convert(
                    ipl_image,
                    ids_peak_ipl.PixelFormatName_Mono8
                )
            
            # Get numpy array (zero-copy if possible)
            # IDS IPL provides direct buffer access
            width = ipl_image.Width()
            height = ipl_image.Height()
            
            # Create numpy array from image data
            # Note: We need to copy here because buffer will be requeued
            data = ipl_image.get_numpy_1D()
            frame = data.reshape((height, width)).copy()
            
            return frame
            
        except Exception as e:
            print(f"[IDSCamera] Buffer processing error: {e}")
            return None
    
    # ------------------------------------------------------------------
    # Frame Reading
    # ------------------------------------------------------------------
    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Read latest frame as BGR numpy array.
        
        This is the standard OpenCV-compatible interface.
        
        Returns:
            (True, BGR frame) if available
            (False, None) if error or not ready
        """
        if self._acquire_error is not None:
            return False, None
        
        if not self.state.is_open or not self.state.is_acquiring:
            return False, None
        
        with self._frame_lock:
            if not self._frame_ready or self._latest_frame is None:
                return True, None  # Open but no frame yet
            
            # Convert Mono8 to BGR
            frame_mono8 = self._latest_frame
            
            if self.settings.newest_only:
                # Clear the ready flag so we know if we're getting stale frames
                self._frame_ready = False
        
        # Convert to BGR outside lock
        bgr = self._mono8_to_bgr_cpu(frame_mono8)
        return True, bgr
    
    def read_mono(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Read latest frame as Mono8 numpy array.
        
        More efficient than read() if you don't need BGR.
        
        Returns:
            (True, Mono8 frame) if available
            (False, None) if error or not ready
        """
        if self._acquire_error is not None:
            return False, None
        
        if not self.state.is_open or not self.state.is_acquiring:
            return False, None
        
        with self._frame_lock:
            if not self._frame_ready or self._latest_frame is None:
                return True, None
            
            frame = self._latest_frame.copy()
            
            if self.settings.newest_only:
                self._frame_ready = False
        
        return True, frame
    
    def read_gpu(self) -> Tuple[bool, Optional['torch.Tensor']]:
        """Read latest frame as GPU tensor (1, 3, H, W) float32.
        
        Optimized path for GPU pipeline:
        - Mono8 → GPU upload → expand to 3 channels
        - Returns tensor ready for YOLO/enhancement
        
        Returns:
            (True, GPU tensor) if available
            (False, None) if error or not ready
        """
        if not CUDA_AVAILABLE:
            print("[IDSCamera] CUDA not available for read_gpu()")
            return False, None
        
        if self._acquire_error is not None:
            return False, None
        
        if not self.state.is_open or not self.state.is_acquiring:
            return False, None
        
        with self._frame_lock:
            if not self._frame_ready or self._latest_frame is None:
                return True, None
            
            frame_mono8 = self._latest_frame
            
            if self.settings.newest_only:
                self._frame_ready = False
        
        # Convert Mono8 → GPU tensor (1, 3, H, W) outside lock
        gpu_tensor = self._mono8_to_gpu_bgr(frame_mono8)
        return True, gpu_tensor
    
    def _mono8_to_bgr_cpu(self, mono8: np.ndarray) -> np.ndarray:
        """Convert Mono8 to BGR on CPU.
        
        Simply expands single channel to 3 identical channels.
        """
        # Stack to create (H, W, 3)
        return np.stack([mono8, mono8, mono8], axis=-1)
    
    def _mono8_to_gpu_bgr(self, mono8: np.ndarray) -> 'torch.Tensor':
        """Convert Mono8 to GPU tensor (1, 3, H, W) float32 [0, 1].
        
        Optimized path:
        1. Upload Mono8 to GPU via pinned memory (async DMA)
        2. Expand to 3 channels (pseudo-BGR)
        3. Normalize to [0, 1]
        
        This is faster than CPU BGR conversion + upload.
        Uses pinned memory + non_blocking=True for async H2D transfer.
        """
        # Pin+upload: pinned memory enables async DMA on the PCIe bus
        # Allocate/reuse pinned buffer for consistent frame sizes
        mono_tensor = torch.from_numpy(mono8)  # (H, W) uint8, CPU
        if not hasattr(self, '_pinned_buffer') or self._pinned_buffer.shape != mono_tensor.shape:
            self._pinned_buffer = torch.empty_like(mono_tensor).pin_memory()
        self._pinned_buffer.copy_(mono_tensor)
        
        # Async transfer to GPU (non-blocking allows CPU to continue)
        gpu_mono = self._pinned_buffer.cuda(non_blocking=True)  # (H, W) uint8
        
        # Convert to float32 [0, 1] on GPU
        mono_float = gpu_mono.float().mul_(1.0 / 255.0)  # (H, W), in-place mul
        
        # Expand to 3 channels: (H, W) → (1, 3, H, W)
        # .expand() is a view (no memory copy), but downstream ops need contiguous
        # Use .repeat() for a single small allocation that is contiguous
        return mono_float.unsqueeze(0).unsqueeze(0).expand(1, 3, -1, -1).contiguous()
    
    # ------------------------------------------------------------------
    # Controls (runtime adjustable)
    # ------------------------------------------------------------------
    def set_exposure(self, exposure_us: float) -> bool:
        """Set exposure time in microseconds.
        
        Args:
            exposure_us: Exposure time in microseconds
            
        Returns:
            True if successful
        """
        if not self.state.is_open or self._node_map is None:
            return False
        
        try:
            # Disable auto exposure first
            try:
                auto_node = self._node_map.FindNode("ExposureAuto")
                auto_node.SetCurrentEntry(auto_node.FindEntry("Off"))
            except:
                pass
            
            exp_node = self._node_map.FindNode("ExposureTime")
            target = max(exp_node.Minimum(), min(exposure_us, exp_node.Maximum()))
            exp_node.SetValue(target)
            self.state.exposure_us = exp_node.Value()
            self.settings.exposure_auto = False
            return True
        except Exception as e:
            print(f"[IDSCamera] Failed to set exposure: {e}")
            return False
    
    def set_exposure_auto(self, enabled: bool) -> bool:
        """Enable/disable auto exposure.
        
        Args:
            enabled: True for auto exposure
            
        Returns:
            True if successful
        """
        if not self.state.is_open or self._node_map is None:
            return False
        
        try:
            auto_node = self._node_map.FindNode("ExposureAuto")
            mode = "Continuous" if enabled else "Off"
            auto_node.SetCurrentEntry(auto_node.FindEntry(mode))
            self.settings.exposure_auto = enabled
            return True
        except Exception as e:
            print(f"[IDSCamera] Failed to set auto exposure: {e}")
            return False
    
    def set_gain(self, gain_db: float) -> bool:
        """Set gain in dB.
        
        Args:
            gain_db: Gain in dB
            
        Returns:
            True if successful
        """
        if not self.state.is_open or self._node_map is None:
            return False
        
        try:
            # Disable auto gain first
            try:
                auto_node = self._node_map.FindNode("GainAuto")
                auto_node.SetCurrentEntry(auto_node.FindEntry("Off"))
            except:
                pass
            
            gain_node = self._node_map.FindNode("Gain")
            target = max(gain_node.Minimum(), min(gain_db, gain_node.Maximum()))
            gain_node.SetValue(target)
            self.state.gain_db = gain_node.Value()
            self.settings.gain_auto = False
            return True
        except Exception as e:
            print(f"[IDSCamera] Failed to set gain: {e}")
            return False
    
    def set_gain_auto(self, enabled: bool) -> bool:
        """Enable/disable auto gain.
        
        Args:
            enabled: True for auto gain
            
        Returns:
            True if successful
        """
        if not self.state.is_open or self._node_map is None:
            return False
        
        try:
            auto_node = self._node_map.FindNode("GainAuto")
            mode = "Continuous" if enabled else "Off"
            auto_node.SetCurrentEntry(auto_node.FindEntry(mode))
            self.settings.gain_auto = enabled
            return True
        except Exception as e:
            print(f"[IDSCamera] Failed to set auto gain: {e}")
            return False
    
    def get_exposure_range(self) -> Tuple[float, float]:
        """Get exposure time range in microseconds.
        
        Returns:
            (min_us, max_us)
        """
        if not self.state.is_open or self._node_map is None:
            return (0.0, 0.0)
        
        try:
            exp_node = self._node_map.FindNode("ExposureTime")
            return (exp_node.Minimum(), exp_node.Maximum())
        except:
            return (0.0, 0.0)
    
    def get_gain_range(self) -> Tuple[float, float]:
        """Get gain range in dB.
        
        Returns:
            (min_db, max_db)
        """
        if not self.state.is_open or self._node_map is None:
            return (0.0, 0.0)
        
        try:
            gain_node = self._node_map.FindNode("Gain")
            return (gain_node.Minimum(), gain_node.Maximum())
        except:
            return (0.0, 0.0)
    
    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def get_actual_fps(self) -> float:
        """Calculate actual FPS from frame timestamps.
        
        Returns:
            Measured FPS or 0 if not enough data.
        """
        # This could be enhanced with a rolling window
        return self.state.fps
    
    def has_error(self) -> bool:
        """Check if acquisition has encountered an error."""
        return self._acquire_error is not None
    
    def get_error(self) -> Optional[str]:
        """Get error message if any."""
        return self._acquire_error
    
    def set_frame_callback(self, callback: Optional[Callable[[np.ndarray], None]]) -> None:
        """Set callback to receive every frame (for recording).
        
        Args:
            callback: Function that receives BGR numpy array
        """
        self._frame_callback = callback


# =============================================================================
# Unified Camera Interface
# =============================================================================

class CameraSource(Enum):
    """Camera source type."""
    OPENCV = auto()
    IDS_PEAK = auto()


class UnifiedCamera:
    """
    Unified camera interface supporting both OpenCV and IDS cameras.
    
    Automatically uses IDS camera if available, falls back to OpenCV.
    Provides consistent interface for the rest of the application.
    """
    
    def __init__(self, prefer_ids: bool = True, threaded: bool = True):
        """Initialize unified camera.
        
        Args:
            prefer_ids: If True, prefer IDS camera over OpenCV
            threaded: Use threaded capture for OpenCV (ignored for IDS)
        """
        self.prefer_ids = prefer_ids
        self._threaded = threaded
        
        self._ids_camera: Optional[IDSCamera] = None
        self._cv_camera = None  # Will be CameraManager if needed
        self._source_type: Optional[CameraSource] = None
        
        # State
        self.is_open: bool = False
        self.width: int = 0
        self.height: int = 0
        self.fps: float = 0.0
    
    def open(self, source: str = "auto") -> bool:
        """Open camera.
        
        Args:
            source: "auto" for automatic, "ids" for IDS, "ids:SERIAL" for specific IDS,
                   or integer/path for OpenCV
                   
        Returns:
            True if successful
        """
        self.close()
        
        # Determine source type
        if source == "auto":
            # Try IDS first if preferred
            if self.prefer_ids and IDS_PEAK_AVAILABLE:
                cameras = IDSCamera.list_cameras()
                if cameras:
                    return self._open_ids(cameras[0].serial)
            # Fall back to OpenCV
            return self._open_opencv("0")
        
        elif source.startswith("ids"):
            # IDS camera
            if source == "ids":
                return self._open_ids(None)
            else:
                # ids:SERIAL format
                serial = source.split(":", 1)[1] if ":" in source else None
                return self._open_ids(serial)
        
        else:
            # OpenCV source
            return self._open_opencv(source)
    
    def _open_ids(self, serial: Optional[str]) -> bool:
        """Open IDS camera."""
        if not IDS_PEAK_AVAILABLE:
            print("[UnifiedCamera] IDS Peak SDK not available")
            return False
        
        try:
            settings = IDSCameraSettings(
                target_fps=30.0,
                exposure_auto=True,
                newest_only=True,
                prefer_high_bit_depth=True,
            )
            self._ids_camera = IDSCamera(settings)
            
            if not self._ids_camera.open(serial):
                self._ids_camera = None
                return False
            
            if not self._ids_camera.start_acquisition():
                self._ids_camera.close()
                self._ids_camera = None
                return False
            
            self._source_type = CameraSource.IDS_PEAK
            self.is_open = True
            self.width = self._ids_camera.state.width
            self.height = self._ids_camera.state.height
            self.fps = self._ids_camera.state.fps
            
            print(f"[UnifiedCamera] Opened IDS camera: {self.width}x{self.height} @ {self.fps:.1f}fps")
            return True
            
        except Exception as e:
            print(f"[UnifiedCamera] Failed to open IDS camera: {e}")
            self._ids_camera = None
            return False
    
    def _open_opencv(self, source: str) -> bool:
        """Open OpenCV camera."""
        # Lazy import to avoid circular dependency
        from camera_manager import CameraManager
        
        self._cv_camera = CameraManager(threaded=self._threaded)
        
        if not self._cv_camera.open(source):
            self._cv_camera = None
            return False
        
        self._source_type = CameraSource.OPENCV
        self.is_open = True
        self.width = self._cv_camera.state.width
        self.height = self._cv_camera.state.height
        self.fps = 30.0  # Assumed
        
        print(f"[UnifiedCamera] Opened OpenCV camera: {self.width}x{self.height}")
        return True
    
    def close(self) -> None:
        """Close camera."""
        if self._ids_camera is not None:
            self._ids_camera.close()
            self._ids_camera = None
        
        if self._cv_camera is not None:
            self._cv_camera.close()
            self._cv_camera = None
        
        self._source_type = None
        self.is_open = False
    
    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Read frame as BGR numpy array.
        
        Returns:
            (True, BGR frame) if available
            (False, None) if error
        """
        if self._source_type == CameraSource.IDS_PEAK:
            return self._ids_camera.read()
        elif self._source_type == CameraSource.OPENCV:
            return self._cv_camera.read()
        else:
            return False, None
    
    def read_gpu(self) -> Tuple[bool, Optional['torch.Tensor']]:
        """Read frame as GPU tensor (1, 3, H, W).
        
        Only available for IDS camera with CUDA.
        Falls back to CPU path for OpenCV.
        
        Returns:
            (True, GPU tensor) if available
            (False, None) if error
        """
        if self._source_type == CameraSource.IDS_PEAK and CUDA_AVAILABLE:
            return self._ids_camera.read_gpu()
        else:
            # Fall back to read() + GPU upload
            ret, frame = self.read()
            if not ret or frame is None:
                return ret, None
            
            if CUDA_AVAILABLE:
                # Convert BGR numpy → GPU tensor
                tensor = torch.from_numpy(frame).cuda()  # (H, W, 3)
                tensor = tensor.permute(2, 0, 1).float() / 255.0  # (3, H, W)
                tensor = tensor.unsqueeze(0)  # (1, 3, H, W)
                return True, tensor
            else:
                return False, None
    
    @property
    def source_type(self) -> Optional[CameraSource]:
        """Get current source type."""
        return self._source_type
    
    def has_error(self) -> bool:
        """Check for capture errors."""
        if self._source_type == CameraSource.IDS_PEAK:
            return self._ids_camera.has_error()
        elif self._source_type == CameraSource.OPENCV:
            return self._cv_camera.has_capture_error()
        return False
    
    def set_frame_callback(self, callback: Optional[Callable[[np.ndarray], None]]) -> None:
        """Set callback for recording."""
        if self._source_type == CameraSource.IDS_PEAK:
            self._ids_camera.set_frame_callback(callback)
        elif self._source_type == CameraSource.OPENCV:
            self._cv_camera.set_frame_callback(callback)
    
    # IDS-specific controls (no-op for OpenCV)
    def set_exposure(self, exposure_us: float) -> bool:
        if self._source_type == CameraSource.IDS_PEAK:
            return self._ids_camera.set_exposure(exposure_us)
        return False
    
    def set_exposure_auto(self, enabled: bool) -> bool:
        if self._source_type == CameraSource.IDS_PEAK:
            return self._ids_camera.set_exposure_auto(enabled)
        return False
    
    def set_gain(self, gain_db: float) -> bool:
        if self._source_type == CameraSource.IDS_PEAK:
            return self._ids_camera.set_gain(gain_db)
        return False
    
    def set_gain_auto(self, enabled: bool) -> bool:
        if self._source_type == CameraSource.IDS_PEAK:
            return self._ids_camera.set_gain_auto(enabled)
        return False


# =============================================================================
# CLI Test
# =============================================================================

def main():
    """Test IDS camera capture."""
    import cv2
    
    print("=" * 60)
    print("IDS Camera Test")
    print("=" * 60)
    
    # List cameras
    print("\nDetecting cameras...")
    cameras = IDSCamera.list_cameras()
    
    if not cameras:
        print("No IDS cameras found. Testing with OpenCV fallback...")
        
        # Test unified camera with OpenCV
        unified = UnifiedCamera(prefer_ids=False)
        if unified.open("0"):
            print(f"Opened OpenCV camera: {unified.width}x{unified.height}")
            
            cv2.namedWindow("Test", cv2.WINDOW_NORMAL)
            
            start_time = time.perf_counter()
            frame_count = 0
            
            while True:
                ret, frame = unified.read()
                if ret and frame is not None:
                    frame_count += 1
                    
                    # Calculate FPS
                    elapsed = time.perf_counter() - start_time
                    fps = frame_count / elapsed if elapsed > 0 else 0
                    
                    # Draw FPS
                    cv2.putText(frame, f"FPS: {fps:.1f}", (10, 30),
                              cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                    
                    cv2.imshow("Test", frame)
                
                if cv2.waitKey(1) == 27:  # ESC
                    break
            
            unified.close()
            cv2.destroyAllWindows()
        return
    
    print(f"\nFound {len(cameras)} camera(s):")
    for cam in cameras:
        print(f"  - {cam.model} (SN: {cam.serial}) on {cam.interface}")
    
    # Open first camera
    print("\nOpening camera...")
    settings = IDSCameraSettings(
        target_fps=30.0,
        exposure_auto=True,
        newest_only=True,
        prefer_high_bit_depth=True,
    )
    camera = IDSCamera(settings)
    
    if not camera.open():
        print("Failed to open camera")
        return
    
    print(f"Camera opened: {camera.state.width}x{camera.state.height} @ {camera.state.fps:.1f}fps")
    print(f"Pixel format: {camera.state.pixel_format}")
    
    # Start acquisition
    if not camera.start_acquisition():
        print("Failed to start acquisition")
        camera.close()
        return
    
    # Display frames
    cv2.namedWindow("IDS Camera", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("IDS Camera", 960, 540)
    
    start_time = time.perf_counter()
    frame_count = 0
    
    print("\nCapturing... Press ESC to exit")
    
    while True:
        ret, frame = camera.read()
        
        if not ret:
            print("Capture error")
            break
        
        if frame is not None:
            frame_count += 1
            
            # Calculate FPS
            elapsed = time.perf_counter() - start_time
            fps = frame_count / elapsed if elapsed > 0 else 0
            
            # Draw stats
            stats = f"FPS: {fps:.1f} | Frames: {frame_count} | Dropped: {camera.state.dropped_frames}"
            cv2.putText(frame, stats, (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            
            cv2.imshow("IDS Camera", frame)
        
        key = cv2.waitKey(1)
        if key == 27:  # ESC
            break
        elif key == ord('e'):
            # Toggle auto exposure
            camera.set_exposure_auto(not camera.settings.exposure_auto)
            print(f"Auto exposure: {camera.settings.exposure_auto}")
    
    # Cleanup
    camera.close()
    cv2.destroyAllWindows()
    
    print(f"\nTotal frames: {frame_count}")
    print(f"Dropped frames: {camera.state.dropped_frames}")


if __name__ == "__main__":
    main()
