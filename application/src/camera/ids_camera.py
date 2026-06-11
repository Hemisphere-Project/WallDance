"""
IDS Peak Camera Integration for WallDance.

Optimized for lowest glass-to-GPU latency:
- IDS U3-34E0XCP-M-GL (4MP Sony IMX664 Starvis 2, Monochrome)
- Mono8 preferred; packed Mono10/12 unpacked to Mono8 when needed
- Newest-frame-only buffer strategy (drop stale frames)
- GPU-accelerated Mono8 → BGR expansion via pinned-memory async DMA

Pipeline:
    IDS Camera (Mono8, or Mono10/12 → unpacked to Mono8)
        │
        ▼ [torch: GPU upload + normalize + expand to BGR]
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
import cv2
import numpy as np

try:
    from core.config import CAMERA_WIDTH as APP_CAMERA_WIDTH, CAMERA_HEIGHT as APP_CAMERA_HEIGHT, CAMERA_FPS as APP_CAMERA_FPS
    from core.config import IDS_MAX_FPS as APP_IDS_MAX_FPS
    from core.config import IDS_USER_SET as APP_IDS_USER_SET
    from core.config import IDS_CROP_PIXELS as APP_IDS_CROP_PIXELS
    from core.config import IDS_RATIO as APP_IDS_RATIO
    from core.config import IDS_AUTO_EXPOSURE_LIMIT_US as APP_IDS_AUTO_EXPOSURE_LIMIT_US
except Exception:
    APP_CAMERA_WIDTH = 1920
    APP_CAMERA_HEIGHT = 1080
    APP_CAMERA_FPS = 30
    APP_IDS_MAX_FPS = 25
    APP_IDS_USER_SET = ""
    APP_IDS_CROP_PIXELS = 0
    APP_IDS_RATIO = 1.0
    APP_IDS_AUTO_EXPOSURE_LIMIT_US = 0

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


import math as _math


IDS_EXPOSURE_WARNING_FPS = 20.0
IDS_EXPOSURE_MIN_FPS = 15.0


def max_exposure_for_fps(min_fps: float) -> float:
    """Return the theoretical max exposure time that still sustains min_fps."""
    min_fps = float(min_fps)
    if min_fps <= 0:
        return 0.0
    return 1_000_000.0 / min_fps


def clamp_exposure_for_min_fps(
    exposure_us: float,
    min_fps: float = IDS_EXPOSURE_MIN_FPS,
) -> float:
    """Clamp manual exposure so it cannot theoretically push FPS below min_fps."""
    exposure_us = float(exposure_us)
    if exposure_us <= 0:
        return 0.0
    return min(exposure_us, max_exposure_for_fps(min_fps))


def exposure_limited_fps(exposure_us: float) -> float:
    """Return the FPS ceiling implied by a given exposure time."""
    exposure_us = float(exposure_us)
    if exposure_us <= 0:
        return float("inf")
    return 1_000_000.0 / exposure_us


def compute_crop_from_budget(pixel_budget: int, ratio: float,
                             sensor_w: int = 0, sensor_h: int = 0) -> Tuple[int, int]:
    """Derive crop (W, H) from a pixel budget and aspect ratio.

    Args:
        pixel_budget: Maximum number of pixels (W*H).  0 = no crop.
        ratio:        Desired W / H.
        sensor_w:     Sensor width for clamping (0 = unclamped).
        sensor_h:     Sensor height for clamping (0 = unclamped).

    Returns:
        (crop_w, crop_h) – both > 0 when budget > 0, else (0, 0).
    """
    if pixel_budget <= 0:
        return (0, 0)
    ratio = max(0.01, float(ratio))
    h = _math.sqrt(pixel_budget / ratio)
    w = ratio * h
    crop_w = int(w)
    crop_h = int(h)
    # Clamp to sensor bounds if provided
    if sensor_w > 0:
        crop_w = min(crop_w, sensor_w)
    if sensor_h > 0:
        crop_h = min(crop_h, sensor_h)
    return (max(1, crop_w), max(1, crop_h))


@dataclass 
class IDSCameraSettings:
    """Settings for IDS camera acquisition."""
    # Resolution (0 = max available)
    width: int = 0
    height: int = 0

    # On-device ROI crop — pixel-budget model.
    # crop_pixels = max W*H pixel count (0 = full sensor).
    # crop_ratio  = desired W/H (1.0 = square).
    # Actual (W, H) derived via compute_crop_from_budget().
    crop_pixels: int = 0
    crop_ratio: float = 1.0
    
    # Frame rate (0 = max available)
    target_fps: float = 30.0
    
    # Exposure (0 = auto, otherwise microseconds)
    exposure_us: float = 0.0  # 0 = auto
    exposure_auto: bool = True
    
    # Gain (0 = auto, otherwise dB)
    gain_db: float = 0.0  # 0 = minimum
    gain_auto: bool = False
    
    # Buffer strategy
    buffer_count: int = 16  # Large pool so camera always has empties to write into
    newest_only: bool = True  # Drop old frames, keep only newest
    
    # Pixel format preference (will try in order)
    prefer_high_bit_depth: bool = True  # Prefer Mono10/12 over Mono8

    # Auto-exposure upper limit (µs). 0 = no limit.
    # Prevents auto-exposure from choosing values so long that
    # the frame rate drops below target_fps.
    auto_exposure_limit_us: float = 0.0

    # Manual fallbacks when auto controls are unavailable
    fallback_exposure_us: float = 10000.0
    fallback_gain_db: float = 6.0

    # Load a camera UserSet on open (e.g. "UserSet1").
    # Empty string = don't load any UserSet.
    user_set: str = ""


class IDSCamera:
    """
    IDS Peak camera wrapper optimized for low-latency acquisition.
    
    Features:
    - Automatic camera discovery and connection
    - Mono8 preferred; packed Mono10/12 unpacked to Mono8 when needed
    - GPU-accelerated Mono8 → BGR expansion via pinned-memory async DMA
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
        
        # Image converter for Mono formats
        self._converter: Optional[ids_peak_ipl.ImageConverter] = None
        
        # Threading
        self._acquire_thread: Optional[threading.Thread] = None
        self._acquire_running: bool = False
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None  # Mono8/Mono16 numpy
        self._latest_timestamp: float = 0.0
        self._frame_ready: bool = False
        self._acquire_error: Optional[str] = None
        
        # Dedicated CUDA stream for frame uploads — reduces PCIe contention
        # between USB3 DMA (camera) and GPU DMA (upload/inference).
        # See docs/IDS_STALL_CONCLUSIONS.md for test data.
        self._upload_stream: Optional['torch.cuda.Stream'] = None
        if CUDA_AVAILABLE:
            self._upload_stream = torch.cuda.Stream()

        # Stall detection
        self._stall_threshold_s: float = 0.4   # gap > this = stall
        self._stall_count: int = 0
        self._last_acq_frame_time: float = 0.0

        # Set during update_crop_ratio so read()/read_gpu() return
        # (True, None) instead of (False, None) while reconfiguring.
        self._reconfiguring: bool = False

        # Callback for recording
        self._frame_callback: Optional[Callable[[np.ndarray], None]] = None
        
        # Initialize IDS Peak library (idempotent)
        self._acquire_library()

        # Watchdog timing
        self._last_nonempty_read_time: float = time.perf_counter()
    
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
            print(f"[IDSCamera] GENICAM_GENTL64_PATH set to: {value}")
        else:
            print(
                "[IDSCamera] GENICAM_GENTL64_PATH not set and no CTI found. "
                "Install IDS Peak or export GENICAM_GENTL64_PATH to the CTI directory."
            )

    @classmethod
    def _ensure_library_initialized(cls) -> None:
        """Initialize IDS Peak library once per process (idempotent)."""
        with cls._library_lock:
            if not cls._library_initialized:
                cls._ensure_gentl_path()
                ids_peak.Library.Initialize()
                cls._library_initialized = True

    @classmethod
    def _acquire_library(cls) -> None:
        """Ensure library is initialized and bump refcount."""
        cls._ensure_library_initialized()
        with cls._library_lock:
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

    @classmethod
    def _release_ids_library_fully(cls) -> None:
        """Force-close the IDS Peak library regardless of refcount.

        Used before opening the same physical device via a different API
        (e.g. OpenCV / DirectShow) to release any GenTL transport locks
        that would otherwise cause a native crash.
        The library will be lazily re-initialised on next IDS access.
        """
        with cls._library_lock:
            if cls._library_initialized:
                try:
                    ids_peak.Library.Close()
                except Exception:
                    pass
                cls._library_initialized = False
                cls._library_refcount = 0
                print("[IDSCamera] Library force-closed for OpenCV handover")
    
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

            # Use NewestOnly buffer handling so the SDK auto-recycles old
            # filled buffers back to the input queue.  This guarantees the
            # camera always has empty buffers to write into, preventing the
            # periodic stream starvation observed at full resolution.
            try:
                ds_nodemap = self._datastream.NodeMaps()[0]
                handling_node = ds_nodemap.FindNode("StreamBufferHandlingMode")
                handling_node.SetCurrentEntry(handling_node.FindEntry("NewestOnly"))
                print("[IDSCamera] StreamBufferHandlingMode: NewestOnly")
            except Exception as e:
                print(f"[IDSCamera] Could not set NewestOnly buffer handling: {e}")

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

        # --- UserSet (load saved camera configuration) ---
        if self.settings.user_set:
            try:
                us_sel = nm.FindNode("UserSetSelector")
                us_sel.SetCurrentEntry(us_sel.FindEntry(self.settings.user_set))
                nm.FindNode("UserSetLoad").Execute()
                nm.FindNode("UserSetLoad").WaitUntilDone()
                print(f"[IDSCamera] Loaded UserSet: {self.settings.user_set}")
            except Exception as e:
                print(f"[IDSCamera] Could not load UserSet '{self.settings.user_set}': {e}")

        # --- Acquisition Mode / Trigger ---
        # Force free-running mode (prevents accidental triggered-capture stalls).
        try:
            acq_mode = nm.FindNode("AcquisitionMode")
            acq_mode.SetCurrentEntry(acq_mode.FindEntry("Continuous"))
            print("[IDSCamera] Acquisition mode: Continuous")
        except Exception:
            pass

        try:
            try:
                trig_sel = nm.FindNode("TriggerSelector")
                trig_sel.SetCurrentEntry(trig_sel.FindEntry("FrameStart"))
            except Exception:
                pass
            trig_mode = nm.FindNode("TriggerMode")
            trig_mode.SetCurrentEntry(trig_mode.FindEntry("Off"))
            print("[IDSCamera] Trigger: Off (free-running)")
        except Exception:
            pass
        
        # --- Pixel Format ---
        # ALWAYS prefer Mono8 to avoid packed-format IPL conversion which
        # holds the acquisition buffer during Convert (causes stream stalls
        # at full resolution).  Fall back to packed formats only if Mono8 is
        # truly unavailable.
        pixel_format_node = nm.FindNode("PixelFormat")

        available_formats = []
        for entry in pixel_format_node.Entries():
            sym = entry.SymbolicValue()
            status = entry.AccessStatus()
            if status != ids_peak.NodeAccessStatus_NotAvailable:
                available_formats.append(sym)
        print(f"[IDSCamera] Available pixel formats: {available_formats}")

        selected_format = None

        # --- Priority 1: Mono8 (zero conversion, instant buffer return) ---
        for symbolic in available_formats:
            if symbolic.lower() == "mono8":
                selected_format = symbolic
                print(f"[IDSCamera] Mono8 found in available formats!")
                break

        # --- Priority 2: High-bit-depth preference or fallback ---
        if selected_format is None:
            def _pick_by_tokens(token_groups):
                for tokens in token_groups:
                    for symbolic in available_formats:
                        low = symbolic.lower()
                        if any(token in low for token in tokens):
                            return symbolic
                return None

            if self.settings.prefer_high_bit_depth:
                selected_format = _pick_by_tokens([
                    ["mono12"], ["mono10"], ["mono8"],
                ])
            else:
                selected_format = _pick_by_tokens([
                    ["mono8"], ["mono10"], ["mono12"],
                ])

        if selected_format is None and available_formats:
            selected_format = available_formats[0]

        if selected_format:
            try:
                pixel_format_node.SetCurrentEntry(
                    pixel_format_node.FindEntry(selected_format)
                )
            except Exception:
                pass  # Already set above for the Mono8-direct path
            self.state.pixel_format = selected_format
            print(f"[IDSCamera] Pixel format SET: {selected_format}")
        
        # --- Resolution / ROI ---
        width_node = nm.FindNode("Width")
        height_node = nm.FindNode("Height")

        # Reset offset to (0,0) first so Width/Height have full range
        try:
            nm.FindNode("OffsetX").SetValue(nm.FindNode("OffsetX").Minimum())
            nm.FindNode("OffsetY").SetValue(nm.FindNode("OffsetY").Minimum())
        except Exception:
            pass

        # Query full sensor size (with offsets at minimum, max W/H = sensor size)
        sensor_w = width_node.Maximum()
        sensor_h = height_node.Maximum()
        # Cache sensor size for runtime crop-ratio changes
        self._sensor_w = sensor_w
        self._sensor_h = sensor_h

        # Determine target size: budget+ratio crop takes priority, then explicit w/h, then max
        crop_w, crop_h = compute_crop_from_budget(
            self.settings.crop_pixels, self.settings.crop_ratio,
            sensor_w, sensor_h,
        )
        if crop_w > 0 and crop_h > 0:
            target_w = min(crop_w, sensor_w)
            target_h = min(crop_h, sensor_h)
        else:
            target_w = min(self.settings.width, sensor_w) if self.settings.width > 0 else sensor_w
            target_h = min(self.settings.height, sensor_h) if self.settings.height > 0 else sensor_h

        # Snap to increment and clamp to [Minimum, Maximum]
        w_inc = max(1, width_node.Increment())
        h_inc = max(1, height_node.Increment())
        target_w = max(width_node.Minimum(), (target_w // w_inc) * w_inc)
        target_h = max(height_node.Minimum(), (target_h // h_inc) * h_inc)

        width_node.SetValue(target_w)
        height_node.SetValue(target_h)

        # Center the ROI on the sensor
        if crop_w > 0 and crop_h > 0:
            self._center_roi(nm, sensor_w, sensor_h, target_w, target_h)

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
        
        # --- Device-Link Throughput Limit ---
        # Log the camera's default value for diagnostics but do NOT change it.
        # Empirically, both raising (300 → stall/2 s) and lowering (125 →
        # stall/5 s) made stalls worse than the camera default (162 → 17 s).
        try:
            tl_node = nm.FindNode("DeviceLinkThroughputLimit")
            print(f"[IDSCamera] DeviceLinkThroughputLimit: "
                  f"{tl_node.Value()/1e6:.0f} MB/s (keeping default, "
                  f"max {tl_node.Maximum()/1e6:.0f})")
        except Exception as e:
            print(f"[IDSCamera] Could not read DeviceLinkThroughputLimit: {e}")
        
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

                # Apply upper limit on auto-exposure to guarantee minimum FPS
                if self.settings.exposure_auto and self.settings.auto_exposure_limit_us > 0:
                    limit_us = self.settings.auto_exposure_limit_us
                    applied = False
                    # Try GenICam standard nodes for auto-exposure upper bound
                    for node_name in ("ExposureAutoUpperLimit",
                                      "AutoExposureTimeUpperLimit",
                                      "ExposureAutoMax"):
                        try:
                            limit_node = nm.FindNode(node_name)
                            clamped = max(limit_node.Minimum(),
                                          min(limit_us, limit_node.Maximum()))
                            limit_node.SetValue(clamped)
                            print(f"[IDSCamera] Auto-exposure upper limit: "
                                  f"{clamped:.0f} µs (via {node_name})")
                            applied = True
                            break
                        except Exception:
                            continue
                    if not applied:
                        # Fallback: disable auto, clamp manual exposure to limit
                        try:
                            exp_node = nm.FindNode("ExposureTime")
                            clamped = max(exp_node.Minimum(),
                                          min(limit_us, exp_node.Maximum()))
                            exp_node.SetValue(clamped)
                            print(f"[IDSCamera] Auto-exposure limit node not found; "
                                  f"set manual exposure to {clamped:.0f} µs as upper bound")
                            # Re-enable auto — on many IDS cameras, setting
                            # ExposureTime while auto is active acts as a hint/cap.
                            try:
                                auto_re = nm.FindNode("ExposureAuto")
                                auto_re.SetCurrentEntry(auto_re.FindEntry("Continuous"))
                            except Exception:
                                pass
                        except Exception as e:
                            print(f"[IDSCamera] Could not apply exposure limit: {e}")
            
            if not self.settings.exposure_auto:
                exp_node = nm.FindNode("ExposureTime")
                requested_exp = self.settings.exposure_us if self.settings.exposure_us > 0 else self.settings.fallback_exposure_us
                requested_exp = clamp_exposure_for_min_fps(requested_exp)
                target_exp = max(exp_node.Minimum(), min(requested_exp, exp_node.Maximum()))
                exp_node.SetValue(target_exp)
                self.settings.exposure_us = exp_node.Value()
                print(f"[IDSCamera] Exposure: Manual {exp_node.Value():.0f} µs")

            try:
                self.state.exposure_us = nm.FindNode("ExposureTime").Value()
                print(f"[IDSCamera] Current exposure reading: {self.state.exposure_us:.0f} µs "
                      f"({self.state.exposure_us/1000:.1f} ms, "
                      f"max FPS from exposure: {1_000_000/max(1,self.state.exposure_us):.1f})")
            except Exception:
                pass
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
                    print("[IDSCamera] Auto gain not available, using manual")
                    self.settings.gain_auto = False

            if not self.settings.gain_auto:
                gain_node = nm.FindNode("Gain")
                requested_gain = self.settings.gain_db if self.settings.gain_db > 0 else self.settings.fallback_gain_db
                target_gain = max(gain_node.Minimum(), min(requested_gain, gain_node.Maximum()))
                gain_node.SetValue(target_gain)
                print(f"[IDSCamera] Gain: Manual {gain_node.Value():.1f} dB")

            try:
                gain_node = nm.FindNode("Gain")
                self.state.gain_db = gain_node.Value()
            except Exception:
                pass
        except Exception as e:
            print(f"[IDSCamera] Could not configure gain: {e}")

    # ------------------------------------------------------------------
    # ROI helpers (shared by _configure_camera and update_crop_ratio)
    # ------------------------------------------------------------------
    def _center_roi(self, nm, sensor_w: int, sensor_h: int,
                    target_w: int, target_h: int) -> None:
        """Center the ROI on the sensor after Width/Height have been set."""
        try:
            ox_node = nm.FindNode("OffsetX")
            oy_node = nm.FindNode("OffsetY")
            ideal_ox = (sensor_w - target_w) // 2
            ideal_oy = (sensor_h - target_h) // 2
            ox_inc = max(1, ox_node.Increment())
            oy_inc = max(1, oy_node.Increment())
            center_ox = max(ox_node.Minimum(), min((ideal_ox // ox_inc) * ox_inc, ox_node.Maximum()))
            center_oy = max(oy_node.Minimum(), min((ideal_oy // oy_inc) * oy_inc, oy_node.Maximum()))
            ox_node.SetValue(center_ox)
            oy_node.SetValue(center_oy)
            print(f"[IDSCamera] ROI: {target_w}x{target_h} centered at offset ({center_ox}, {center_oy})"
                  f"  [sensor {sensor_w}x{sensor_h}]")
        except Exception as e:
            print(f"[IDSCamera] Could not center ROI offset: {e}")

    def update_crop_ratio(self, ratio: float) -> bool:
        """Change the crop aspect ratio at runtime (stop → reconfigure → restart).

        The pixel budget (self.settings.crop_pixels) is kept constant; only
        the W/H derived from *ratio* change.

        Args:
            ratio: New W/H ratio (clamped to 0.5 – 2.0).

        Returns:
            True if the camera is now acquiring at the new resolution.
        """
        ratio = max(0.5, min(2.0, float(ratio)))
        if not self.state.is_open or self._node_map is None:
            self.settings.crop_ratio = ratio
            return False

        if self.settings.crop_pixels <= 0:
            print("[IDSCamera] update_crop_ratio: no pixel budget set, ignoring")
            return False

        self.settings.crop_ratio = ratio

        nm = self._node_map
        sensor_w = getattr(self, '_sensor_w', 0)
        sensor_h = getattr(self, '_sensor_h', 0)
        if sensor_w <= 0 or sensor_h <= 0:
            print("[IDSCamera] update_crop_ratio: sensor size unknown")
            return False

        # 1. Stop acquisition (thread, datastream, camera)
        was_acquiring = self.state.is_acquiring
        self._reconfiguring = True
        if was_acquiring:
            self.stop_acquisition()

        try:
            # 2. Reset offsets so Width/Height have full range
            try:
                nm.FindNode("OffsetX").SetValue(nm.FindNode("OffsetX").Minimum())
                nm.FindNode("OffsetY").SetValue(nm.FindNode("OffsetY").Minimum())
            except Exception:
                pass

            # 3. Compute new crop dimensions
            crop_w, crop_h = compute_crop_from_budget(
                self.settings.crop_pixels, ratio, sensor_w, sensor_h,
            )
            target_w = min(crop_w, sensor_w)
            target_h = min(crop_h, sensor_h)

            width_node = nm.FindNode("Width")
            height_node = nm.FindNode("Height")
            w_inc = max(1, width_node.Increment())
            h_inc = max(1, height_node.Increment())
            target_w = max(width_node.Minimum(), (target_w // w_inc) * w_inc)
            target_h = max(height_node.Minimum(), (target_h // h_inc) * h_inc)

            width_node.SetValue(target_w)
            height_node.SetValue(target_h)

            # 4. Center ROI
            self._center_roi(nm, sensor_w, sensor_h, target_w, target_h)

            self.state.width = width_node.Value()
            self.state.height = height_node.Value()
            print(f"[IDSCamera] Crop ratio {ratio:.2f} → {self.state.width}x{self.state.height}")

            # 5. Re-allocate buffers (payload size may have changed)
            self._allocate_buffers()

            # 6. Restart acquisition
            if was_acquiring:
                ok = self.start_acquisition()
                self._reconfiguring = False
                return ok
            self._reconfiguring = False
            return True

        except Exception as e:
            print(f"[IDSCamera] update_crop_ratio failed: {e}")
            self._reconfiguring = False
            # Best-effort restart
            if was_acquiring:
                try:
                    self.start_acquisition()
                except Exception:
                    pass
            return False

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
        """Background thread: continuously acquire frames.

        Flow: WaitForBuffer → memcpy raw bytes → QueueBuffer → numpy unpack.
        For packed IDS formats (Mono10g40IDS, Mono12g24IDS) we do a fast
        numpy-only unpack to Mono8 AFTER the buffer is returned, so the
        buffer hold time is just the memcpy (~1-2 ms).
        """
        print("[IDSCamera] Acquisition thread started")

        # Timeout = 5 frame periods, clamped to [150, 500] ms.
        # Shorter timeout → faster stall recovery (retry sooner).
        frame_period_ms = 1000.0 / max(1.0, self.state.fps)
        timeout_ms = max(150, min(500, int(5.0 * frame_period_ms)))
        print(f"[IDSCamera] WaitForBuffer timeout: {timeout_ms} ms")
        consecutive_errors = 0
        last_timeout_log = 0.0

        # Detect pixel format once — stays constant for the session.
        pf_name = self.state.pixel_format.lower()  # e.g. "mono10g40ids"
        width = self.state.width
        height = self.state.height
        print(f"[IDSCamera] Acq loop: pf={self.state.pixel_format}, {width}x{height}")

        self._last_acq_frame_time = time.perf_counter()

        while self._acquire_running:
            try:
                buffer = self._datastream.WaitForFinishedBuffer(timeout_ms)

                if not self._acquire_running:
                    self._datastream.QueueBuffer(buffer)
                    break

                # === FAST PATH: copy raw bytes, return buffer, unpack ===
                try:
                    ipl_image = ids_peak_ipl_extension.BufferToImage(buffer)
                    raw = ipl_image.get_numpy_1D().copy()      # ~1-2 ms memcpy
                finally:
                    self._datastream.QueueBuffer(buffer)       # buffer free!

                # Unpack packed format → (H, W) uint8 using numpy only
                frame = self._unpack_raw(raw, width, height, pf_name)

                if frame is not None:
                    consecutive_errors = 0
                    timestamp = time.perf_counter()

                    # ---- Stall detection ----
                    gap = timestamp - self._last_acq_frame_time
                    self._last_acq_frame_time = timestamp
                    if gap > self._stall_threshold_s:
                        self._stall_count += 1
                        severity = "SEVERE" if gap >= 1.0 else "stall"
                        try:
                            n_q = self._datastream.NumBuffersQueued()
                            n_a = len(self._datastream.AnnouncedBuffers())
                            pool = f"queued={n_q}/{n_a}"
                        except Exception:
                            pool = "?"
                        print(f"[IDSCamera] USB3 {severity}: {gap*1000:.0f}ms gap "
                              f"(#{self._stall_count}, {pool})")

                    with self._frame_lock:
                        if self.settings.newest_only and self._frame_ready:
                            self.state.dropped_frames += 1

                        self._latest_frame = frame
                        self._latest_timestamp = timestamp
                        self._frame_ready = True
                        self.state.frame_count += 1
                        self.state.last_frame_time = timestamp

                    if self._frame_callback is not None:
                        try:
                            bgr = self._mono_to_bgr_cpu(frame)
                            self._frame_callback(bgr)
                        except Exception as e:
                            print(f"[IDSCamera] Frame callback error: {e}")

            except ids_peak.Exception as e:
                if self._acquire_running:
                    if "timeout" in str(e).lower():
                        now = time.perf_counter()
                        if now - last_timeout_log >= 2.0:
                            last_timeout_log = now
                            # Log buffer pool health for stall diagnosis
                            try:
                                n_announced = len(self._datastream.AnnouncedBuffers())
                                n_queued = self._datastream.NumBuffersQueued()
                                print(f"[IDSCamera] Buffer timeout — "
                                      f"queued={n_queued}/{n_announced}")
                            except Exception:
                                print("[IDSCamera] Buffer timeout (waiting…)")
                        continue
                    consecutive_errors += 1
                    if consecutive_errors == 1 or consecutive_errors % 20 == 0:
                        print(f"[IDSCamera] Acquisition error ({consecutive_errors}): {e}")
                    if consecutive_errors >= 100:
                        self._acquire_error = str(e)
                        break
                    time.sleep(0.002)
                    continue
                break
            except Exception as e:
                if self._acquire_running:
                    consecutive_errors += 1
                    if consecutive_errors == 1 or consecutive_errors % 20 == 0:
                        print(f"[IDSCamera] Unexpected error ({consecutive_errors}): {e}")
                    if consecutive_errors >= 100:
                        self._acquire_error = str(e)
                        break
                    time.sleep(0.002)
                    continue
                break

        print("[IDSCamera] Acquisition thread finished")

    # ------------------------------------------------------------------
    # Fast numpy-only format unpacking (no IPL dependency)
    # ------------------------------------------------------------------

    @staticmethod
    def _unpack_raw(
        raw: np.ndarray, width: int, height: int, pf_name: str
    ) -> Optional[np.ndarray]:
        """Unpack raw packed bytes into a (H, W) uint8 mono frame.

        Supports:
        - mono10g40ids: 5 bytes → 4 pixels (10-bit). Bytes 0-3 are MSBs,
          byte 4 holds 2-bit LSBs. Taking bytes 0-3 = right-shift by 2 =
          Mono8 with <1 LSB error.
        - mono12g24ids: 3 bytes → 2 pixels (12-bit). Byte layout TBD; for
          now falls back to IPL Convert.
        - mono8: direct reshape.

        All work happens on a COPY of the buffer data, so no IDS buffer is
        held during this call.
        """
        try:
            pixels = width * height

            if "mono10g40" in pf_name:
                # Mono10g40IDS: 5 bytes per 4 pixels.
                # Bytes 0-3 = top 8 bits of pixels 0-3 (identical to >>2).
                # Just take the first 4 bytes of each 5-byte group.
                groups = raw.reshape(-1, 5)
                mono8 = groups[:, :4].reshape(height, width).copy()
                return mono8

            if "mono12g24" in pf_name:
                # Mono12g24IDS: 3 bytes per 2 pixels (12-bit packed).
                # Layout: [MSB_p0, MSB_p1, LSBs] where byte2 holds
                #   p0_lsb[3:0] in bits 3:0, p1_lsb[3:0] in bits 7:4.
                # Taking bytes 0,1 = right-shift by 4 = Mono8.
                groups = raw.reshape(-1, 3)
                mono8 = groups[:, :2].reshape(height, width).copy()
                return mono8

            if "mono8" in pf_name:
                if raw.size == pixels:
                    return raw.reshape(height, width).copy()

            # Unknown format
            print(f"[IDSCamera] Unknown pixel format: {pf_name}")
            return None

        except Exception as e:
            print(f"[IDSCamera] Unpack error ({pf_name}): {e}")
            return None

    # ------------------------------------------------------------------
    # Frame Reading
    # ------------------------------------------------------------------
    # CPU frame cache for zero-download preview (Strategy B+).
    # read_gpu() populates this with the CPU BGR frame produced from the
    # same mono8 used for the GPU upload.  The main loop can retrieve it
    # via get_last_cpu_frame() for preview without any GPU→CPU transfer.
    _cached_cpu_bgr: Optional[np.ndarray] = None

    def get_last_cpu_frame(self) -> Optional[np.ndarray]:
        """Return the last CPU BGR frame cached during read_gpu()."""
        return self._cached_cpu_bgr

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Read latest frame as BGR numpy array.
        
        This is the standard OpenCV-compatible interface.
        
        Returns:
            (True, BGR frame) if available
            (False, None) if error or not ready
        """
        if self._acquire_error is not None:
            return False, None
        
        if not self.state.is_open or (not self.state.is_acquiring and not self._reconfiguring):
            return False, None
        
        with self._frame_lock:
            if not self._frame_ready or self._latest_frame is None:
                return True, None  # Open but no frame yet
            
            # Convert Mono (8/16-bit) to BGR8 for OpenCV-compatible path
            frame_mono8 = self._latest_frame
            
            if self.settings.newest_only:
                # Clear the ready flag so we know if we're getting stale frames
                self._frame_ready = False
        
        # Convert to BGR8 outside lock
        bgr = self._mono_to_bgr_cpu(frame_mono8)
        self._last_nonempty_read_time = time.perf_counter()
        return True, bgr
    
    def read_gpu(self) -> Tuple[bool, Optional['torch.Tensor']]:
        """Read latest frame as GPU tensor (1, 3, H, W) float32.
        
        Optimized path for GPU pipeline:
        - Mono8/Mono16 → GPU upload → normalize → expand to 3 channels
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
        
        if not self.state.is_open or (not self.state.is_acquiring and not self._reconfiguring):
            return False, None
        
        with self._frame_lock:
            if not self._frame_ready or self._latest_frame is None:
                return True, None
            
            frame_mono8 = self._latest_frame
            
            if self.settings.newest_only:
                self._frame_ready = False
        
        # Convert Mono (8/16-bit) → GPU tensor (1, 3, H, W) outside lock
        gpu_tensor = self._mono_to_gpu_bgr(frame_mono8)
        
        # Cache CPU BGR frame for STANDBY preview (avoids 49 MB GPU→CPU download).
        # The mono→BGR conversion is cheap (~1 ms).
        self._cached_cpu_bgr = self._mono_to_bgr_cpu(frame_mono8)
        
        self._last_nonempty_read_time = time.perf_counter()
        return True, gpu_tensor

    def _mono_to_bgr_cpu(self, mono: np.ndarray) -> np.ndarray:
        """Convert Mono8 to BGR uint8 for preview/recording."""
        if mono.dtype != np.uint8:
            mono = cv2.convertScaleAbs(mono, alpha=255.0 / 65535.0)
        return cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)

    def _mono_to_gpu_bgr(self, mono: np.ndarray) -> 'torch.Tensor':
        """Convert Mono8 to GPU tensor (1, 3, H, W) float32 [0, 1].
        
        Optimized path:
        1. Upload Mono to GPU via pinned memory (async DMA)
        2. Expand to 3 channels (pseudo-BGR)
        3. Normalize to [0, 1]
        
        Uses pinned memory + non_blocking=True for async H2D transfer.
        """
        # Pin+upload: pinned memory enables async DMA on the PCIe bus
        # Allocate/reuse pinned buffer for consistent frame sizes
        mono_tensor = torch.from_numpy(mono)  # (H, W) uint8, CPU
        if (
            not hasattr(self, '_pinned_buffer')
            or self._pinned_buffer.shape != mono_tensor.shape
            or self._pinned_buffer.dtype != mono_tensor.dtype
        ):
            self._pinned_buffer = torch.empty_like(mono_tensor).pin_memory()
        self._pinned_buffer.copy_(mono_tensor)
        
        # Async transfer to GPU on a DEDICATED CUDA stream.
        # This reduces PCIe bus contention with USB3 DMA by scheduling
        # the H2D copy on a separate hardware queue.  Verified to reduce
        # stalls from ~3/min → ~0.5/min in isolation tests.
        if self._upload_stream is not None:
            with torch.cuda.stream(self._upload_stream):
                gpu_mono = self._pinned_buffer.cuda(non_blocking=True)
            # Wait for async upload to complete before downstream ops on the
            # default stream — prevents reading stale/partial data.
            self._upload_stream.synchronize()
        else:
            gpu_mono = self._pinned_buffer.cuda(non_blocking=True)  # fallback
        
        # Convert to float32 [0, 1] on GPU (always Mono8 → /255)
        mono_float = gpu_mono.float().mul_(1.0 / 255.0).clamp_(0.0, 1.0)  # (H, W)
        
        # Expand to 3 channels: (H, W) → (1, 3, H, W)
        # .expand() is a view (no memory copy), but downstream ops need contiguous
        # Use .repeat() for a single small allocation that is contiguous
        return mono_float.unsqueeze(0).unsqueeze(0).expand(1, 3, -1, -1).contiguous()
    
    # ------------------------------------------------------------------
    # Controls (runtime adjustable)
    # ------------------------------------------------------------------
    def _renegotiate_fps(self) -> None:
        """Push AcquisitionFrameRate back up to target after exposure changes.

        IDS cameras lower the HW max FPS when exposure is long, but do NOT
        automatically raise it back when exposure is reduced.  We re-read
        the (now higher) Maximum and set the frame rate to
        min(target_fps, new_max).
        """
        if self._node_map is None:
            return
        try:
            fps_node = self._node_map.FindNode("AcquisitionFrameRate")
            new_max = fps_node.Maximum()
            desired = min(self.settings.target_fps, new_max) if self.settings.target_fps > 0 else new_max
            if abs(fps_node.Value() - desired) > 0.1:
                fps_node.SetValue(desired)
                self.state.fps = fps_node.Value()
                print(f"[IDSCamera] FPS re-negotiated: {self.state.fps:.1f} (max {new_max:.1f})")
        except Exception as e:
            print(f"[IDSCamera] Could not re-negotiate FPS: {e}")

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
            clamped_request = clamp_exposure_for_min_fps(exposure_us)
            if clamped_request < float(exposure_us):
                print(
                    f"[IDSCamera] Exposure request {float(exposure_us):.0f} µs exceeds "
                    f"{IDS_EXPOSURE_MIN_FPS:.0f} FPS limit; clamping to {clamped_request:.0f} µs"
                )
            target = max(exp_node.Minimum(), min(clamped_request, exp_node.Maximum()))
            exp_node.SetValue(target)
            self.state.exposure_us = exp_node.Value()
            self.settings.exposure_us = self.state.exposure_us
            self.settings.exposure_auto = False

            # Re-negotiate FPS upward: after reducing exposure the HW max FPS
            # increases, but AcquisitionFrameRate stays at the old clamped value
            # unless we explicitly push it back up.
            self._renegotiate_fps()
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
            # Some IDS models do not expose ExposureAuto in this node map.
            # Keep manual mode without spamming hard errors.
            self.settings.exposure_auto = False
            print("[IDSCamera] ExposureAuto node unavailable; staying in manual exposure mode")
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
            # Some IDS models do not expose GainAuto in this node map.
            self.settings.gain_auto = False
            print("[IDSCamera] GainAuto node unavailable; staying in manual gain mode")
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
    def has_error(self) -> bool:
        """Check if acquisition has encountered an error."""
        return self._acquire_error is not None

    def get_last_frame_age_s(self) -> float:
        """Return seconds since the last successfully read frame."""
        now = time.perf_counter()
        if self._last_nonempty_read_time <= 0:
            return float("inf")
        return max(0.0, now - self._last_nonempty_read_time)

    def get_last_acquired_age_s(self) -> float:
        """Return seconds since the last frame acquired by IDS thread."""
        now = time.perf_counter()
        last = self.state.last_frame_time
        if last <= 0:
            return float("inf")
        return max(0.0, now - last)

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
    
    Provides consistent interface for the rest of the application.
    """
    
    def __init__(self, threaded: bool = True):
        """Initialize unified camera.
        
        Args:
            threaded: Use threaded capture for OpenCV (ignored for IDS)
        """
        self._threaded = threaded
        
        self._ids_camera: Optional[IDSCamera] = None
        self._cv_camera = None  # Will be CameraManager if needed
        self._source_type: Optional[CameraSource] = None
        
        # State
        self.is_open: bool = False
        self.width: int = 0
        self.height: int = 0
        self.fps: float = 0.0
    
    def open(self, source: str = "ids") -> bool:
        """Open camera.
        
        Args:
            source: "ids" for any IDS camera, or integer/path for OpenCV
                   
        Returns:
            True if successful
        """
        self.close()
        
        # Determine source type
        if source.startswith("ids"):
            return self._open_ids()

        return self._open_opencv(source)
    
    def _open_ids(self) -> bool:
        """Open IDS camera."""
        if not IDS_PEAK_AVAILABLE:
            print("[UnifiedCamera] IDS Peak SDK not available")
            return False
        
        try:
            settings = IDSCameraSettings(
                width=0,
                height=0,
                crop_pixels=APP_IDS_CROP_PIXELS,
                crop_ratio=APP_IDS_RATIO,
                target_fps=float(max(1.0, min(float(APP_CAMERA_FPS), float(APP_IDS_MAX_FPS)))),
                exposure_auto=True,
                gain_auto=True,
                newest_only=True,
                prefer_high_bit_depth=False,
                user_set=APP_IDS_USER_SET,
                auto_exposure_limit_us=float(APP_IDS_AUTO_EXPOSURE_LIMIT_US),
            )
            self._ids_camera = IDSCamera(settings)
            
            if not self._ids_camera.open(None):
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
        """Open OpenCV camera.

        When the IDS Peak SDK has been loaded (even if no IDS camera is
        currently open), its GenTL transport layer may hold exclusive
        access to the USB device.  Attempting to open the same physical
        camera via DirectShow while GenTL is active can cause a native
        crash.  To avoid this we fully close the IDS Peak library before
        touching OpenCV, and re-initialise it lazily on next IDS access.
        """
        # --- Release IDS Peak library to free USB transport locks -------
        if IDS_PEAK_AVAILABLE:
            try:
                IDSCamera._release_ids_library_fully()
            except Exception as exc:
                print(f"[UnifiedCamera] Warning: could not release IDS library: {exc}")
            import time as _t
            # USB stack needs time to release device handles after GenTL closes.
            # 0.3 s was too short on some machines — 0.8 s is safer.
            _t.sleep(0.8)

        # Lazy import to avoid circular dependency
        from camera.camera_manager import CameraManager

        try:
            self._cv_camera = CameraManager(threaded=self._threaded)

            if not self._cv_camera.open(source):
                # Retry with explicit backend based on OS
                import sys
                if sys.platform == 'win32':
                    print("[UnifiedCamera] Default backend failed, retrying with DSHOW...")
                    import time as _t2
                    _t2.sleep(0.3)  # extra settle before retry
                    self._cv_camera = CameraManager(threaded=self._threaded)
                    if not self._cv_camera.open(source, backend=cv2.CAP_DSHOW):
                        self._cv_camera = None
                        return False
                elif sys.platform.startswith('linux'):
                    print("[UnifiedCamera] Default backend failed, retrying with V4L2...")
                    self._cv_camera = CameraManager(threaded=self._threaded)
                    if not self._cv_camera.open(source, backend=cv2.CAP_V4L2):
                        self._cv_camera = None
                        return False
                else:
                    self._cv_camera = None
                    return False

            self._source_type = CameraSource.OPENCV
            self.is_open = True
            self.width = self._cv_camera.state.width
            self.height = self._cv_camera.state.height
            self.fps = 30.0  # Assumed

            print(f"[UnifiedCamera] Opened OpenCV camera: {self.width}x{self.height}")
            return True

        except Exception as e:
            print(f"[UnifiedCamera] Failed to open OpenCV camera: {e}")
            import traceback; traceback.print_exc()
            self._cv_camera = None
            return False
    
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
    
    def get_last_cpu_frame(self) -> Optional[np.ndarray]:
        """Return last CPU BGR frame cached during read_gpu() (IDS only).
        
        Used for zero-download preview: the GPU tensor goes to YOLO while
        this cached CPU frame provides preview without any GPU→CPU transfer.
        Returns None if not IDS or no frame cached yet.
        """
        if self._source_type == CameraSource.IDS_PEAK:
            return self._ids_camera.get_last_cpu_frame()
        return None
    
    @property
    def source_type(self) -> Optional[CameraSource]:
        """Get current source type."""
        return self._source_type
    
    def has_error(self) -> bool:
        """Check for capture errors."""
        if self._source_type == CameraSource.IDS_PEAK and self._ids_camera is not None:
            return self._ids_camera.has_error()
        elif self._source_type == CameraSource.OPENCV and self._cv_camera is not None:
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

    def update_crop_ratio(self, ratio: float) -> bool:
        """Change on-device crop ratio at runtime (IDS only).

        Returns True if successful.  After success the caller should
        re-read self.width / self.height and update the preview.
        """
        if self._source_type != CameraSource.IDS_PEAK or self._ids_camera is None:
            return False
        ok = self._ids_camera.update_crop_ratio(ratio)
        if ok:
            self.width = self._ids_camera.state.width
            self.height = self._ids_camera.state.height
        return ok

    # Diagnostics helpers (no-op values for non-IDS sources)
    def get_last_frame_age_s(self) -> float:
        if self._source_type == CameraSource.IDS_PEAK and self._ids_camera is not None:
            return self._ids_camera.get_last_frame_age_s()
        return float("inf")

    def get_last_acquired_age_s(self) -> float:
        if self._source_type == CameraSource.IDS_PEAK and self._ids_camera is not None:
            return self._ids_camera.get_last_acquired_age_s()
        return float("inf")

    def get_ids_counters(self) -> Tuple[int, int]:
        if self._source_type == CameraSource.IDS_PEAK and self._ids_camera is not None:
            return (int(self._ids_camera.state.frame_count), int(self._ids_camera.state.dropped_frames))
        return (0, 0)

    def stop_acquisition(self) -> None:
        """Stop IDS acquisition (no-op for OpenCV).

        Call before entering playback to eliminate USB3 DMA traffic and
        prevent PCIe bus contention with CUDA uploads.
        """
        if self._source_type == CameraSource.IDS_PEAK and self._ids_camera is not None:
            self._ids_camera.stop_acquisition()

    def start_acquisition(self) -> bool:
        """Restart IDS acquisition after playback (no-op for OpenCV).

        Returns True on success or when not applicable.
        """
        if self._source_type == CameraSource.IDS_PEAK and self._ids_camera is not None:
            return self._ids_camera.start_acquisition()
        return True


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
        unified = UnifiedCamera()
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
