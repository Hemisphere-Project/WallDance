#!/usr/bin/env python3
"""
GIL-Contention Diagnostic Test for IDS Camera Stalls
=====================================================

Hypothesis:  IDS Cockpit (pure C++) never shows 1.65 s stalls, only occasional
frame drops under GPU load.  Our Python app sees ~1650 ms stalls.  The Python
GIL may be starving the acquisition thread: when the GIL is held by CPU-bound
work (numpy/cv2/Ultralytics pre-/post-processing), IDS SDK calls in the acq
thread (WaitForFinishedBuffer, BufferToImage, QueueBuffer) are delayed.  With
16 buffers at 20 fps = 800 ms of buffering headroom, GIL delays > 800 ms can
cause the camera to run out of queued buffers, triggering a USB3 link-level
recovery that takes exactly ~1650 ms.

Test levels
-----------
    G0  Baseline — acq thread only, no contention.
    G1  GIL-heavy CPU load (tight numpy loop) in a THREAD (shares GIL).
    G2  GIL-free GPU load (CUDA matmul, releases GIL) in a THREAD.
    G3  Same CPU load as G1, but in a SEPARATE PROCESS (own GIL).
    G4  Realistic YOLO pipeline in a thread (Ultralytics predict).
    G5  Realistic YOLO pipeline in a subprocess via shared memory.

Additionally, the acquisition loop instruments every IDS SDK call to measure
GIL wait / call latency, so we can directly observe GIL-induced delays.

Expected outcome if hypothesis is correct
------------------------------------------
    G0  ≈ 0 stalls   (no contention)
    G1  MANY stalls   (GIL starvation)
    G2  few stalls    (CUDA releases GIL)
    G3  ≈ 0 stalls   (separate GIL)
    G4  many stalls   (Ultralytics CPU work holds GIL)
    G5  few stalls    (subprocess decouples GIL)

Usage:
    cd application
    python test_gil_contention.py [--duration 60] [--levels 0,1,2,3,4,5]
                                  [--yolo-model yolo11m-pose] [--imgsz 800]
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import multiprocessing
import os
import queue as _queue
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Add src/ to path
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_SCRIPT_DIR, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# ---------------------------------------------------------------------------
# IDS Peak SDK
# ---------------------------------------------------------------------------
IDS_PEAK_AVAILABLE = False
try:
    from ids_peak import ids_peak
    from ids_peak_ipl import ids_peak_ipl
    from ids_peak import ids_peak_ipl_extension
    IDS_PEAK_AVAILABLE = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# PyTorch / CUDA
# ---------------------------------------------------------------------------
TORCH_AVAILABLE = False
CUDA_AVAILABLE = False
try:
    import torch
    TORCH_AVAILABLE = True
    CUDA_AVAILABLE = torch.cuda.is_available()
except ImportError:
    torch = None

# ---------------------------------------------------------------------------
# YOLO (optional)
# ---------------------------------------------------------------------------
YOLO_AVAILABLE = False
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    pass

import cv2


# ===========================================================================
#  Data
# ===========================================================================

@dataclass
class StallEvent:
    frame_idx: int
    gap_s: float
    timestamp: float


@dataclass
class GilTiming:
    """Timing for one IDS SDK call.  We record wall time before the call and
    after the call.  If the GIL is held by another thread, we expect the
    wall time to be significantly larger than the actual C++ call duration."""
    call_name: str          # "WaitForBuffer", "BufferToImage", "QueueBuffer"
    wall_ms: float          # wall-clock duration of the call
    frame_idx: int


@dataclass
class LevelResult:
    level: str
    description: str
    duration_s: float
    frame_count: int
    stall_count: int
    stalls: List[StallEvent] = field(default_factory=list)
    avg_fps: float = 0.0
    avg_gap_ms: float = 0.0
    max_gap_ms: float = 0.0
    timeout_count: int = 0
    notes: str = ""
    # GIL timing statistics
    gil_timings: Dict[str, List[float]] = field(default_factory=dict)


# ===========================================================================
#  GenTL path helper
# ===========================================================================

def ensure_gentl_path():
    if os.environ.get("GENICAM_GENTL64_PATH"):
        return
    import glob as _glob
    candidates = [
        "/opt/ids/ids-peak", "/opt/ids/ids-peak/cti",
        "/opt/ids/ids-peak/lib", "/usr/local/ids/ids-peak",
    ]
    cti_dirs = set()
    for base in candidates:
        for g in ["*.cti", "*/*.cti"]:
            for p in _glob.glob(os.path.join(base, g)):
                cti_dirs.add(os.path.dirname(p))
    if cti_dirs:
        os.environ["GENICAM_GENTL64_PATH"] = ":".join(sorted(cti_dirs))


# ===========================================================================
#  IDS acquisition core  (same as isolation test)
# ===========================================================================

class IDSAcquisitionCore:
    def __init__(self, buffer_count: int = 16, max_fps: float = 20.0):
        self.buffer_count = buffer_count
        self.max_fps = max_fps
        self._device = None
        self._datastream = None
        self._node_map = None
        self._ds_nodemap = None
        self.width = 0
        self.height = 0
        self.fps = 0.0
        self.pixel_format = ""

    def open(self) -> bool:
        if not IDS_PEAK_AVAILABLE:
            print("[IDS] Peak SDK not available")
            return False

        ensure_gentl_path()
        ids_peak.Library.Initialize()

        dm = ids_peak.DeviceManager.Instance()
        dm.Update()

        target = None
        for dev in dm.Devices():
            if dev.IsOpenable():
                target = dev
                break

        if target is None:
            print("[IDS] No openable camera found")
            return False

        self._device = target.OpenDevice(ids_peak.DeviceAccessType_Exclusive)
        self._node_map = self._device.RemoteDevice().NodeMaps()[0]
        nm = self._node_map

        print(f"[IDS] Opened: {target.ModelName()} (SN: {target.SerialNumber()})")

        # Continuous free-run
        try:
            acq_mode = nm.FindNode("AcquisitionMode")
            acq_mode.SetCurrentEntry(acq_mode.FindEntry("Continuous"))
        except Exception:
            pass
        try:
            try:
                nm.FindNode("TriggerSelector").SetCurrentEntry(
                    nm.FindNode("TriggerSelector").FindEntry("FrameStart"))
            except Exception:
                pass
            nm.FindNode("TriggerMode").SetCurrentEntry(
                nm.FindNode("TriggerMode").FindEntry("Off"))
        except Exception:
            pass

        # Pixel format: prefer Mono8
        pf_node = nm.FindNode("PixelFormat")
        avail = []
        for entry in pf_node.Entries():
            if entry.AccessStatus() != ids_peak.NodeAccessStatus_NotAvailable:
                avail.append(entry.SymbolicValue())
        if "Mono8" in avail:
            pf_node.SetCurrentEntry(pf_node.FindEntry("Mono8"))
        elif avail:
            pf_node.SetCurrentEntry(pf_node.FindEntry(avail[0]))
        self.pixel_format = pf_node.CurrentEntry().SymbolicValue()
        print(f"[IDS] PixelFormat: {self.pixel_format}")

        # Full resolution
        try:
            w_node = nm.FindNode("Width")
            h_node = nm.FindNode("Height")
            w_node.SetValue(w_node.Maximum())
            h_node.SetValue(h_node.Maximum())
        except Exception:
            pass
        self.width = nm.FindNode("Width").Value()
        self.height = nm.FindNode("Height").Value()
        print(f"[IDS] Resolution: {self.width}x{self.height}")

        # FPS
        try:
            fps_node = nm.FindNode("AcquisitionFrameRate")
            target_fps = min(self.max_fps, fps_node.Maximum())
            fps_node.SetValue(target_fps)
            self.fps = fps_node.Value()
        except Exception:
            self.fps = self.max_fps
        print(f"[IDS] FPS: {self.fps:.1f}")

        # Exposure auto
        try:
            auto_node = nm.FindNode("ExposureAuto")
            auto_node.SetCurrentEntry(auto_node.FindEntry("Continuous"))
        except Exception:
            pass

        # DeviceLinkThroughputLimit
        try:
            tl_node = nm.FindNode("DeviceLinkThroughputLimit")
            print(f"[IDS] DeviceLinkThroughputLimit: {tl_node.Value()/1e6:.0f} MB/s")
        except Exception:
            pass

        # Data stream
        ds_list = self._device.DataStreams()
        if ds_list.empty():
            print("[IDS] No data streams")
            return False
        self._datastream = ds_list[0].OpenDataStream()
        self._ds_nodemap = self._datastream.NodeMaps()[0]

        # NewestOnly
        try:
            handling = self._ds_nodemap.FindNode("StreamBufferHandlingMode")
            handling.SetCurrentEntry(handling.FindEntry("NewestOnly"))
        except Exception:
            pass

        self._allocate_buffers(self.buffer_count)
        return True

    def _allocate_buffers(self, count: int):
        ds = self._datastream
        ds.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
        for buf in ds.AnnouncedBuffers():
            ds.RevokeBuffer(buf)
        payload_size = self._node_map.FindNode("PayloadSize").Value()
        for _ in range(count):
            buf = ds.AllocAndAnnounceBuffer(payload_size)
            ds.QueueBuffer(buf)
        print(f"[IDS] Allocated {count} buffers ({payload_size} bytes each)")

    def start_acquisition(self):
        self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
        for buf in self._datastream.AnnouncedBuffers():
            self._datastream.QueueBuffer(buf)
        self._datastream.StartAcquisition()
        self._node_map.FindNode("AcquisitionStart").Execute()

    def stop_acquisition(self):
        try:
            self._datastream.KillWait()
        except Exception:
            pass
        try:
            self._node_map.FindNode("AcquisitionStop").Execute()
        except Exception:
            pass
        try:
            self._datastream.StopAcquisition(ids_peak.AcquisitionStopMode_Default)
        except Exception:
            pass

    def close(self):
        self.stop_acquisition()
        if self._datastream:
            try:
                self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
                for buf in self._datastream.AnnouncedBuffers():
                    self._datastream.RevokeBuffer(buf)
            except Exception:
                pass
            self._datastream = None
        self._node_map = None
        self._device = None
        try:
            ids_peak.Library.Close()
        except Exception:
            pass

    def unpack_frame(self, buffer) -> Optional[np.ndarray]:
        """Extract mono8 frame from IDS buffer (returns buffer immediately)."""
        try:
            ipl_img = ids_peak_ipl_extension.BufferToImage(buffer)
            raw = ipl_img.get_numpy_1D().copy()
        finally:
            self._datastream.QueueBuffer(buffer)
        pf = self.pixel_format.lower()
        pixels = self.width * self.height
        if "mono10g40" in pf:
            groups = raw.reshape(-1, 5)
            return groups[:, :4].reshape(self.height, self.width).copy()
        if "mono12g24" in pf:
            groups = raw.reshape(-1, 3)
            return groups[:, :2].reshape(self.height, self.width).copy()
        if "mono8" in pf and raw.size == pixels:
            return raw.reshape(self.height, self.width).copy()
        return raw[:pixels].reshape(self.height, self.width).copy() if raw.size >= pixels else None


# ===========================================================================
#  GIL-instrumented acquisition loop
# ===========================================================================

STALL_THRESHOLD_S = 0.4
SEVERE_STALL_THRESHOLD_S = 1.0
WARMUP_S: float = 5.0


def run_acquisition_loop_instrumented(
    ids: IDSAcquisitionCore,
    duration_s: float,
    *,
    on_frame: Optional[callable] = None,
    label: str = "test",
) -> LevelResult:
    """Acquisition loop that instruments GIL wait time.

    Each IDS SDK C-extension call (WaitForFinishedBuffer, BufferToImage,
    get_numpy_1D, QueueBuffer) is timed individually.  The gap between
    requesting the call and returning from it is the wall time, which
    includes any time the thread spent waiting for the GIL.

    Architecture:
        - Acq thread: tight loop with timing instrumentation
        - GPU worker thread: processes frames via on_frame callback
        - Main thread: sleeps for duration
    """
    warmup_s = WARMUP_S
    total_run = warmup_s + duration_s
    timeout_ms = max(150, min(500, int(5000.0 / max(1.0, ids.fps))))

    result = LevelResult(level=label, description="", duration_s=duration_s,
                         frame_count=0, stall_count=0)
    stalls: List[StallEvent] = []
    warmup_stalls: List[StallEvent] = []
    gaps: List[float] = []

    # GIL timing storage: lists of wall-clock ms per SDK call
    # We only record calls that take > 5ms (to avoid noise).
    gil_wait_ms = {"BufferToImage": [], "memcpy": [], "QueueBuffer": []}
    GIL_RECORD_THRESHOLD_MS = 5.0

    stop_event = threading.Event()
    frame_q: _queue.Queue = _queue.Queue(maxsize=4)

    gpu_errors = []

    def gpu_worker():
        while not stop_event.is_set():
            try:
                mono = frame_q.get(timeout=0.1)
            except _queue.Empty:
                continue
            if mono is None:
                break
            if on_frame is not None:
                try:
                    on_frame(mono)
                except Exception as e:
                    if len(gpu_errors) < 5:
                        gpu_errors.append(str(e))
                        print(f"  [{label}] on_frame error: {e}")

    def acq_loop():
        acq_start = time.perf_counter()
        last_frame_time = acq_start
        frame_idx = 0
        warmup_frames = 0
        timeouts = 0
        ds = ids._datastream

        while not stop_event.is_set():
            # --- WaitForFinishedBuffer (releases GIL internally, C++ blocks) ---
            try:
                buffer = ds.WaitForFinishedBuffer(timeout_ms)
            except Exception as e:
                if "timeout" in str(e).lower():
                    timeouts += 1
                    continue
                if stop_event.is_set():
                    break
                continue

            now = time.perf_counter()
            gap = now - last_frame_time
            last_frame_time = now
            frame_idx += 1
            in_warmup = (now - acq_start) < warmup_s

            # Stall detection
            if not in_warmup:
                gaps.append(gap)
                if gap > STALL_THRESHOLD_S:
                    stalls.append(StallEvent(frame_idx, gap, now))
            else:
                warmup_frames += 1
                if gap > STALL_THRESHOLD_S:
                    warmup_stalls.append(StallEvent(frame_idx, gap, now))

            # --- BufferToImage (C++ call, needs GIL) ---
            t0 = time.perf_counter()
            try:
                ipl_img = ids_peak_ipl_extension.BufferToImage(buffer)
            except Exception:
                try:
                    ds.QueueBuffer(buffer)
                except Exception:
                    pass
                continue
            t1 = time.perf_counter()
            bti_ms = (t1 - t0) * 1000.0
            if bti_ms > GIL_RECORD_THRESHOLD_MS and not in_warmup:
                gil_wait_ms["BufferToImage"].append(bti_ms)

            # --- get_numpy_1D().copy() (C++ call + memcpy, needs GIL) ---
            t0 = time.perf_counter()
            try:
                raw = ipl_img.get_numpy_1D().copy()
            except Exception:
                try:
                    ds.QueueBuffer(buffer)
                except Exception:
                    pass
                continue
            t1 = time.perf_counter()
            mc_ms = (t1 - t0) * 1000.0
            if mc_ms > GIL_RECORD_THRESHOLD_MS and not in_warmup:
                gil_wait_ms["memcpy"].append(mc_ms)

            # --- QueueBuffer (C++ call, needs GIL) ---
            t0 = time.perf_counter()
            ds.QueueBuffer(buffer)
            t1 = time.perf_counter()
            qb_ms = (t1 - t0) * 1000.0
            if qb_ms > GIL_RECORD_THRESHOLD_MS and not in_warmup:
                gil_wait_ms["QueueBuffer"].append(qb_ms)

            # --- Unpack (numpy, CPU, holds GIL) ---
            pf = ids.pixel_format.lower()
            pixels = ids.width * ids.height
            if "mono8" in pf and raw.size == pixels:
                mono = raw.reshape(ids.height, ids.width)
            elif "mono10g40" in pf:
                groups = raw.reshape(-1, 5)
                mono = groups[:, :4].reshape(ids.height, ids.width)
            else:
                mono = raw[:pixels].reshape(ids.height, ids.width) if raw.size >= pixels else None

            if mono is not None:
                try:
                    frame_q.put_nowait(mono)
                except _queue.Full:
                    try:
                        frame_q.get_nowait()
                    except _queue.Empty:
                        pass
                    try:
                        frame_q.put_nowait(mono)
                    except _queue.Full:
                        pass

        result.timeout_count = timeouts
        result.notes = (f"warmup={warmup_s:.0f}s ({warmup_frames} frames, "
                        f"{len(warmup_stalls)} stalls ignored)")

    # --- Launch threads ---
    gpu_thread = threading.Thread(target=gpu_worker, name=f"GpuWorker-{label}", daemon=True)
    acq_thread = threading.Thread(target=acq_loop, name=f"AcqGIL-{label}", daemon=True)

    ids.start_acquisition()
    gpu_thread.start()
    acq_thread.start()

    time.sleep(total_run)
    stop_event.set()
    ids._datastream.KillWait()
    acq_thread.join(timeout=3.0)
    try:
        frame_q.put(None, timeout=1.0)
    except _queue.Full:
        pass
    gpu_thread.join(timeout=3.0)
    ids.stop_acquisition()

    # --- Stats ---
    result.frame_count = len(gaps)
    result.stalls = stalls
    result.stall_count = len(stalls)
    if gaps:
        result.avg_gap_ms = np.mean(gaps) * 1000
        result.max_gap_ms = np.max(gaps) * 1000
        result.avg_fps = result.frame_count / duration_s
    result.gil_timings = {k: list(v) for k, v in gil_wait_ms.items()}
    if gpu_errors:
        result.notes += f" | {len(gpu_errors)} gpu_worker errors"

    return result


# ===========================================================================
#  GIL contention workloads
# ===========================================================================

class GilHeavyCpuLoad:
    """Runs tight CPU-bound numpy loops that hold the GIL.

    This simulates what Ultralytics does internally during pre-/post-
    processing: numpy operations, cv2 calls, list comprehensions — all of
    which hold the GIL continuously.

    The loop alternates between:
    - 10ms of tight numpy work (matrix ops, argsort, etc.) — holds GIL
    - 0.5ms yield (time.sleep(0.0005)) — brief GIL release

    Effective GIL hold ratio: ~95 %
    """

    def __init__(self, hold_ms: float = 10.0, yield_ms: float = 0.5):
        self._running = False
        self._thread = None
        self._hold_s = hold_ms / 1000.0
        self._yield_s = yield_ms / 1000.0

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="GIL-heavy-CPU")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _loop(self):
        """Tight CPU-bound work that refuses to release the GIL."""
        # Pre-allocate arrays
        a = np.random.randn(1024, 1024).astype(np.float32)
        b = np.random.randn(1024, 1024).astype(np.float32)
        while self._running:
            t0 = time.perf_counter()
            # Hold GIL: numpy matmul + argsort + cv2 resize — all CPU/GIL
            while time.perf_counter() - t0 < self._hold_s:
                _ = np.dot(a, b)
                _ = np.argsort(a.ravel()[:10000])
                _ = cv2.resize(a, (256, 256))
            # Brief yield to allow other threads a chance
            time.sleep(self._yield_s)


class GilFreeCudaLoad:
    """Runs GPU matmul that releases the GIL during kernel execution.

    torch.mm and torch.cuda.synchronize() both release the GIL while the
    GPU is working.  This should NOT starve the acquisition thread.
    """

    def __init__(self, size: int = 2048):
        self._running = False
        self._thread = None
        self._size = size

    def start(self):
        if not CUDA_AVAILABLE:
            print("[GilFreeCuda] CUDA not available, skipping")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="GIL-free-CUDA")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _loop(self):
        a = torch.randn(self._size, self._size, device='cuda')
        b = torch.randn(self._size, self._size, device='cuda')
        while self._running:
            _ = torch.mm(a, b)         # Releases GIL during GPU kernel
            torch.cuda.synchronize()   # Releases GIL while waiting


class MultiprocessCpuLoad:
    """Same numpy workload as GilHeavyCpuLoad, but in a SEPARATE PROCESS.

    Since each Python process has its own GIL, this CPU work should NOT
    affect the acquisition thread at all.
    """

    def __init__(self):
        self._process = None

    def start(self):
        self._process = multiprocessing.Process(
            target=self._worker, daemon=True, name="CPU-separate-process")
        self._process.start()
        print(f"[MultiprocessCPU] Started PID={self._process.pid}")

    def stop(self):
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=3.0)
            print("[MultiprocessCPU] Stopped")
        self._process = None

    @staticmethod
    def _worker():
        """Runs in a separate process — has its own GIL."""
        a = np.random.randn(1024, 1024).astype(np.float32)
        b = np.random.randn(1024, 1024).astype(np.float32)
        while True:
            _ = np.dot(a, b)
            _ = np.argsort(a.ravel()[:10000])


# ===========================================================================
#  Subprocess YOLO worker (G5)
# ===========================================================================

def _yolo_subprocess_target(
    shm_name: str, frame_shape: Tuple[int, int],
    model_path: str, imgsz: int,
    ready_event_name: str,
    stop_event_name: str,
    new_frame_event_name: str,
):
    """Run YOLO inference in a subprocess, reading frames from shared memory.

    Communication:
        - Shared memory block: holds one (H, W) uint8 mono frame
        - new_frame_event: set by main process when a new frame is ready
        - ready_event: set by this process when it has consumed the frame
        - stop_event: set by main process to signal shutdown
    """
    import multiprocessing.shared_memory as shm_mod
    # We need to import in the subprocess
    try:
        from ultralytics import YOLO as _YOLO
        import cv2 as _cv2
        import numpy as _np

        sm = shm_mod.SharedMemory(name=shm_name)
        h, w = frame_shape
        model = _YOLO(model_path)

        # Warmup
        dummy = _np.zeros((h, w, 3), dtype=_np.uint8)
        for _ in range(3):
            model.predict(dummy, imgsz=imgsz, verbose=False, device='cuda')
        print(f"[YOLO subprocess PID={os.getpid()}] Warmup done")

        # Named events — we use simple file-based signaling with polling
        # since Windows named events aren't directly available in Python stdlib.
        # We'll poll the shared memory header byte instead.
        # Header layout: byte[0] = frame_ready flag, byte[1] = stop flag
        frame_data_offset = 64  # first 64 bytes are header

        while True:
            # Check stop flag
            if sm.buf[1] != 0:
                break

            # Check new frame flag
            if sm.buf[0] == 0:
                time.sleep(0.001)
                continue

            # Read frame
            frame = _np.ndarray(
                (h, w), dtype=_np.uint8,
                buffer=sm.buf[frame_data_offset:frame_data_offset + h * w]
            ).copy()

            # Clear frame_ready flag
            sm.buf[0] = 0

            # Run YOLO
            bgr = _cv2.cvtColor(frame, _cv2.COLOR_GRAY2BGR)
            results = model.predict(bgr, imgsz=imgsz, verbose=False, device='cuda')
            if results and results[0].keypoints is not None:
                _ = results[0].keypoints.data.cpu()

        sm.close()
        print(f"[YOLO subprocess PID={os.getpid()}] Exiting")

    except Exception as e:
        print(f"[YOLO subprocess] Error: {e}")
        import traceback
        traceback.print_exc()


class YoloSubprocessWorker:
    """Manages a subprocess that runs YOLO inference.

    Frames are passed via shared memory.  The main process GIL is not
    affected by YOLO's CPU-bound preprocessing/postprocessing.
    """

    def __init__(self, height: int, width: int, model_path: str, imgsz: int):
        self.height = height
        self.width = width
        self.model_path = model_path
        self.imgsz = imgsz
        self._shm = None
        self._process = None
        self._frame_data_offset = 64

    def start(self):
        import multiprocessing.shared_memory as shm_mod
        shm_size = self._frame_data_offset + self.height * self.width
        self._shm = shm_mod.SharedMemory(create=True, size=shm_size)
        # Clear header
        self._shm.buf[0] = 0  # frame_ready
        self._shm.buf[1] = 0  # stop

        self._process = multiprocessing.Process(
            target=_yolo_subprocess_target,
            args=(
                self._shm.name,
                (self.height, self.width),
                self.model_path,
                self.imgsz,
                "", "", "",
            ),
            daemon=True,
        )
        self._process.start()
        print(f"[YoloSubprocess] Started PID={self._process.pid}, shm={self._shm.name}")
        # Wait for subprocess to warm up
        time.sleep(10.0)
        print("[YoloSubprocess] Warmup wait done")

    def submit_frame(self, mono: np.ndarray):
        """Copy frame to shared memory and signal subprocess."""
        if self._shm is None:
            return
        offset = self._frame_data_offset
        nbytes = self.height * self.width
        self._shm.buf[offset:offset + nbytes] = mono.tobytes()[:nbytes]
        self._shm.buf[0] = 1  # signal frame ready

    def stop(self):
        if self._shm is not None:
            self._shm.buf[1] = 1  # signal stop
        if self._process and self._process.is_alive():
            self._process.join(timeout=5.0)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=2.0)
        if self._shm is not None:
            self._shm.close()
            self._shm.unlink()
            self._shm = None
        print("[YoloSubprocess] Stopped")


# ===========================================================================
#  Model path finder
# ===========================================================================

def find_model(name: str) -> str:
    """Find a YOLO model file (searches application/ and models/)."""
    candidates = [
        os.path.join(_SCRIPT_DIR, name),
        os.path.join(_SCRIPT_DIR, f"{name}.pt"),
        os.path.join(_SCRIPT_DIR, "..", "models", name),
        os.path.join(_SCRIPT_DIR, "..", "models", f"{name}.pt"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return f"{name}.pt"  # fallback, let ultralytics download


# ===========================================================================
#  Test levels
# ===========================================================================

def level_G0_baseline(ids: IDSAcquisitionCore, duration: float) -> LevelResult:
    """G0: Baseline — acq thread only, no contention."""
    print(f"\n  [G0] Running {duration}s baseline (no contention)...")
    result = run_acquisition_loop_instrumented(ids, duration, label="G0_baseline")
    result.description = "Baseline: acq thread only, no other work"
    return result


def level_G1_gil_heavy(ids: IDSAcquisitionCore, duration: float) -> LevelResult:
    """G1: GIL-heavy CPU load in a thread (shares GIL)."""
    print(f"\n  [G1] Running {duration}s with GIL-heavy numpy CPU load in thread...")
    load = GilHeavyCpuLoad(hold_ms=10.0, yield_ms=0.5)
    load.start()
    time.sleep(0.5)

    result = run_acquisition_loop_instrumented(ids, duration, label="G1_gil_heavy")
    result.description = "GIL-heavy: tight numpy+cv2 loop in thread (holds GIL ~95%)"

    load.stop()
    return result


def level_G2_gil_free(ids: IDSAcquisitionCore, duration: float) -> LevelResult:
    """G2: GIL-free CUDA load in a thread (GPU releases GIL)."""
    print(f"\n  [G2] Running {duration}s with GIL-free CUDA matmul in thread...")
    load = GilFreeCudaLoad(size=2048)
    load.start()
    time.sleep(0.5)

    result = run_acquisition_loop_instrumented(ids, duration, label="G2_gil_free_cuda")
    result.description = "GIL-free: CUDA matmul in thread (releases GIL during GPU work)"

    load.stop()
    return result


def level_G3_multiprocess_cpu(ids: IDSAcquisitionCore, duration: float) -> LevelResult:
    """G3: Same CPU load as G1, but in a separate process (own GIL)."""
    print(f"\n  [G3] Running {duration}s with CPU load in SEPARATE PROCESS...")
    load = MultiprocessCpuLoad()
    load.start()
    time.sleep(0.5)

    result = run_acquisition_loop_instrumented(ids, duration, label="G3_multiprocess_cpu")
    result.description = "Multiprocess CPU: same numpy load in separate process (own GIL)"

    load.stop()
    return result


def level_G4_yolo_thread(ids: IDSAcquisitionCore, duration: float,
                          model_path: str, imgsz: int) -> LevelResult:
    """G4: Realistic YOLO pipeline in a thread (same GIL)."""
    if not YOLO_AVAILABLE:
        r = LevelResult("G4_yolo_thread", "SKIPPED — ultralytics not installed",
                         duration, 0, 0)
        return r

    print(f"\n  [G4] Loading YOLO model for in-thread inference...")
    model = YOLO(model_path)
    dummy = np.zeros((ids.height, ids.width, 3), dtype=np.uint8)
    for _ in range(3):
        model.predict(dummy, imgsz=imgsz, verbose=False, device='cuda')
    print(f"  [G4] YOLO warmup done. Running {duration}s with YOLO in GPU worker thread...")

    pinned = torch.empty(ids.height, ids.width, dtype=torch.uint8).pin_memory() if CUDA_AVAILABLE else None

    def on_frame(mono):
        # Upload (small GIL touch for pinned copy)
        if pinned is not None:
            t = torch.from_numpy(mono)
            pinned.copy_(t)
            _ = pinned.cuda(non_blocking=True)

        # YOLO predict: this is the main GIL-contention source
        # Ultralytics internally does: cv2.resize, np.ascontiguousarray,
        # torch.from_numpy (all GIL-holding), then GPU inference (GIL-free),
        # then post-processing (GIL-holding again).
        bgr = cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)
        results = model.predict(bgr, imgsz=imgsz, verbose=False, device='cuda')
        if results and results[0].keypoints is not None:
            _ = results[0].keypoints.data.cpu()

    result = run_acquisition_loop_instrumented(ids, duration,
                                                on_frame=on_frame,
                                                label="G4_yolo_thread")
    result.description = (f"YOLO in thread: {os.path.basename(model_path)} @{imgsz} "
                          f"(same GIL — pre/post holds GIL)")

    del pinned, model
    gc.collect()
    if CUDA_AVAILABLE:
        torch.cuda.empty_cache()
    return result


def level_G5_yolo_subprocess(ids: IDSAcquisitionCore, duration: float,
                              model_path: str, imgsz: int) -> LevelResult:
    """G5: YOLO inference in a subprocess via shared memory (separate GIL)."""
    if not YOLO_AVAILABLE:
        r = LevelResult("G5_yolo_subprocess", "SKIPPED — ultralytics not installed",
                         duration, 0, 0)
        return r

    print(f"\n  [G5] Starting YOLO subprocess (separate GIL)...")
    worker = YoloSubprocessWorker(ids.height, ids.width, model_path, imgsz)
    worker.start()

    print(f"  [G5] Running {duration}s with YOLO in subprocess...")

    def on_frame(mono):
        worker.submit_frame(mono)

    result = run_acquisition_loop_instrumented(ids, duration,
                                                on_frame=on_frame,
                                                label="G5_yolo_subprocess")
    result.description = (f"YOLO in subprocess: {os.path.basename(model_path)} @{imgsz} "
                          f"via shared memory (separate GIL)")

    worker.stop()
    gc.collect()
    if CUDA_AVAILABLE:
        torch.cuda.empty_cache()
    return result


# ===========================================================================
#  Result display
# ===========================================================================

def print_result(r: LevelResult):
    severe = sum(1 for s in r.stalls if s.gap_s >= SEVERE_STALL_THRESHOLD_S)
    print(f"\n  ┌── {r.level} {'─' * max(1, 56 - len(r.level))}")
    print(f"  │ {r.description}")
    print(f"  │ Duration: {r.duration_s:.0f}s | Frames: {r.frame_count} | "
          f"FPS: {r.avg_fps:.1f}")
    print(f"  │ Stalls : {r.stall_count} total, {severe} severe (≥1s)")
    print(f"  │ Gaps   : avg={r.avg_gap_ms:.1f}ms, max={r.max_gap_ms:.0f}ms")
    print(f"  │ Timeouts: {r.timeout_count}")
    if r.notes:
        print(f"  │ Notes  : {r.notes}")

    # GIL timing summary
    if r.gil_timings:
        print(f"  │")
        print(f"  │ GIL-instrumented SDK call timings (calls > {5.0:.0f}ms):")
        for call_name, times in r.gil_timings.items():
            if times:
                arr = np.array(times)
                print(f"  │   {call_name:15s}: n={len(arr):4d}, "
                      f"p50={np.percentile(arr,50):6.1f}ms, "
                      f"p95={np.percentile(arr,95):6.1f}ms, "
                      f"p99={np.percentile(arr,99):6.1f}ms, "
                      f"max={np.max(arr):6.1f}ms")
            else:
                print(f"  │   {call_name:15s}: (all < 5ms — no GIL delays)")
    print(f"  └{'─' * 58}")


def print_summary_table(results: List[LevelResult]):
    print(f"\n{'='*78}")
    print(f"  GIL CONTENTION DIAGNOSTIC — SUMMARY")
    print(f"{'='*78}")
    print(f"  {'Level':<25s} {'FPS':>5s} {'Stalls':>7s} {'Severe':>7s} "
          f"{'MaxGap':>8s} {'MaxGIL':>8s}")
    print(f"  {'─'*25} {'─'*5} {'─'*7} {'─'*7} {'─'*8} {'─'*8}")
    for r in results:
        severe = sum(1 for s in r.stalls if s.gap_s >= SEVERE_STALL_THRESHOLD_S)
        # Max GIL delay across all SDK calls
        max_gil = 0.0
        for times in r.gil_timings.values():
            if times:
                max_gil = max(max_gil, max(times))
        max_gil_str = f"{max_gil:.0f}ms" if max_gil > 0 else "-"
        print(f"  {r.level:<25s} {r.avg_fps:5.1f} {r.stall_count:7d} "
              f"{severe:7d} {r.max_gap_ms:7.0f}ms {max_gil_str:>8s}")
    print(f"  {'─'*25} {'─'*5} {'─'*7} {'─'*7} {'─'*8} {'─'*8}")


def print_verdict(results: Dict[str, LevelResult]):
    """Analyse results and print a verdict on GIL contention."""
    print(f"\n{'='*78}")
    print(f"  VERDICT")
    print(f"{'='*78}")

    g0 = results.get("G0_baseline")
    g1 = results.get("G1_gil_heavy")
    g2 = results.get("G2_gil_free_cuda")
    g3 = results.get("G3_multiprocess_cpu")
    g4 = results.get("G4_yolo_thread")
    g5 = results.get("G5_yolo_subprocess")

    verdicts = []

    # Compare G1 (GIL-heavy thread) vs G0 (baseline)
    if g0 and g1:
        if g1.stall_count > g0.stall_count + 2:
            verdicts.append(
                f"  ✗ GIL-heavy CPU work INCREASES stalls: "
                f"G0={g0.stall_count} → G1={g1.stall_count}  "
                f"(GIL contention confirmed)")
        else:
            verdicts.append(
                f"  ✓ GIL-heavy CPU work does NOT increase stalls: "
                f"G0={g0.stall_count} → G1={g1.stall_count}  "
                f"(GIL not the bottleneck)")

    # Compare G2 (GIL-free CUDA) vs G1 (GIL-heavy)
    if g1 and g2:
        if g2.stall_count < g1.stall_count - 2:
            verdicts.append(
                f"  ✓ GIL-free CUDA has fewer stalls than GIL-heavy: "
                f"G1={g1.stall_count} → G2={g2.stall_count}  "
                f"(supports GIL hypothesis)")
        else:
            verdicts.append(
                f"  ~ GIL-free CUDA similar to GIL-heavy: "
                f"G1={g1.stall_count} → G2={g2.stall_count}")

    # Compare G3 (multiprocess CPU) vs G1 (GIL-heavy thread)
    if g1 and g3:
        if g3.stall_count < g1.stall_count - 2:
            verdicts.append(
                f"  ✓ Separate-process CPU has fewer stalls: "
                f"G1={g1.stall_count} → G3={g3.stall_count}  "
                f"(GIL isolation helps — confirms GIL as root cause)")
        else:
            verdicts.append(
                f"  ~ Separate process similar: "
                f"G1={g1.stall_count} → G3={g3.stall_count}")

    # Compare G5 (YOLO subprocess) vs G4 (YOLO thread)
    if g4 and g5:
        if g5.stall_count < g4.stall_count - 2:
            verdicts.append(
                f"  ✓ YOLO subprocess has fewer stalls than YOLO thread: "
                f"G4={g4.stall_count} → G5={g5.stall_count}  "
                f"(multiprocessing is the fix)")
        else:
            verdicts.append(
                f"  ~ YOLO subprocess similar: "
                f"G4={g4.stall_count} → G5={g5.stall_count}")

    # Overall
    print()
    for v in verdicts:
        print(v)

    # GIL delay analysis
    print()
    for name, r in results.items():
        max_calls = {}
        for call_name, times in r.gil_timings.items():
            if times:
                max_calls[call_name] = max(times)
        if max_calls:
            worst_call = max(max_calls.items(), key=lambda x: x[1])
            if worst_call[1] > 50:
                print(f"  ⚠ {name}: SDK call '{worst_call[0]}' peaked at "
                      f"{worst_call[1]:.0f}ms (expected <5ms)")

    print()
    # Final conclusion
    confirms = sum(1 for v in verdicts if "✗" in v or "confirms" in v.lower()
                   or "GIL contention confirmed" in v)
    denies = sum(1 for v in verdicts if "NOT" in v)

    if confirms >= 2:
        print("  ══════════════════════════════════════════════════════════")
        print("  CONCLUSION: GIL contention IS the root cause of stalls.")
        print("  The fix: move IDS acquisition to a separate process,")
        print("  or move YOLO pre/post-processing out of the GIL.")
        print("  ══════════════════════════════════════════════════════════")
    elif denies >= 2:
        print("  ══════════════════════════════════════════════════════════")
        print("  CONCLUSION: GIL contention is NOT the primary cause.")
        print("  Stalls occur regardless of GIL load. The root cause is")
        print("  likely PCIe bus contention or USB3 controller firmware.")
        print("  ══════════════════════════════════════════════════════════")
    else:
        print("  ══════════════════════════════════════════════════════════")
        print("  CONCLUSION: Mixed results. GIL may contribute but is not")
        print("  the sole factor. Further investigation needed.")
        print("  ══════════════════════════════════════════════════════════")


# ===========================================================================
#  Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="GIL-Contention Diagnostic Test for IDS Camera Stalls")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="Seconds per test level (default: 60)")
    parser.add_argument("--warmup", type=float, default=5.0,
                        help="Warmup seconds (stalls ignored, default: 5)")
    parser.add_argument("--levels", type=str, default="0,1,2,3,4,5",
                        help="Comma-separated levels to run (default: 0,1,2,3,4,5)")
    parser.add_argument("--yolo-model", type=str, default="yolo11m-pose",
                        help="YOLO model name (default: yolo11m-pose)")
    parser.add_argument("--imgsz", type=int, default=800,
                        help="YOLO inference resolution (default: 800)")
    parser.add_argument("--buffer-count", type=int, default=16,
                        help="IDS buffer count (default: 16)")
    parser.add_argument("--stall-threshold", type=float, default=0.4,
                        help="Stall threshold in seconds (default: 0.4)")
    parser.add_argument("--log", type=str, default="test_gil_contention.log",
                        help="Log file name (default: test_gil_contention.log)")
    args = parser.parse_args()

    # --- Tee stdout/stderr to log file ---
    log_path = os.path.join(_SCRIPT_DIR, args.log)

    class Tee:
        def __init__(self, stream, log_file):
            self._stream = stream
            self._log = log_file
        def write(self, data):
            self._stream.write(data)
            self._stream.flush()
            self._log.write(data)
            self._log.flush()
        def flush(self):
            self._stream.flush()
            self._log.flush()

    _log_fh = open(log_path, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, _log_fh)
    sys.stderr = Tee(sys.__stderr__, _log_fh)

    global STALL_THRESHOLD_S, WARMUP_S, SEVERE_STALL_THRESHOLD_S
    STALL_THRESHOLD_S = args.stall_threshold
    WARMUP_S = args.warmup

    requested_levels = [int(x) for x in args.levels.split(",")]

    print("=" * 78)
    print("  GIL-CONTENTION DIAGNOSTIC TEST FOR IDS CAMERA STALLS")
    print("=" * 78)
    print(f"  Date             : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Duration/level   : {args.duration}s")
    print(f"  Warmup (ignored) : {WARMUP_S:.0f}s")
    print(f"  Stall threshold  : {STALL_THRESHOLD_S*1000:.0f}ms")
    print(f"  Buffer count     : {args.buffer_count}")
    print(f"  YOLO model       : {args.yolo_model} @ imgsz={args.imgsz}")
    print(f"  Levels           : {requested_levels}")
    print(f"  CUDA available   : {CUDA_AVAILABLE}")
    print(f"  YOLO available   : {YOLO_AVAILABLE}")
    print(f"  Python           : {sys.version}")
    print(f"  Log file         : {log_path}")
    if TORCH_AVAILABLE:
        print(f"  PyTorch          : {torch.__version__}")
        if CUDA_AVAILABLE:
            print(f"  CUDA device      : {torch.cuda.get_device_name(0)}")
    print()

    print("  HYPOTHESIS: Python GIL starvation causes IDS USB3 stalls.")
    print("  IDS Cockpit (C++) never shows 1.65s stalls ⟹ not hardware.")
    print("  If GIL-heavy CPU thread (G1) causes more stalls than")
    print("  GIL-free CUDA (G2) or separate-process CPU (G3),")
    print("  then GIL contention is confirmed as the root cause.")
    print()

    # --- Open camera ---
    ids = IDSAcquisitionCore(buffer_count=args.buffer_count, max_fps=20.0)
    if not ids.open():
        print("\nFATAL: Could not open IDS camera. Exiting.")
        return 1

    print(f"\n  Camera: {ids.width}x{ids.height} @ {ids.fps:.1f}fps, {ids.pixel_format}\n")

    # --- Level dispatch ---
    model_path = find_model(args.yolo_model)

    level_funcs = {
        0: ("G0_baseline",           level_G0_baseline,         {}),
        1: ("G1_gil_heavy",          level_G1_gil_heavy,        {}),
        2: ("G2_gil_free_cuda",      level_G2_gil_free,         {}),
        3: ("G3_multiprocess_cpu",   level_G3_multiprocess_cpu, {}),
        4: ("G4_yolo_thread",        level_G4_yolo_thread,
            {"model_path": model_path, "imgsz": args.imgsz}),
        5: ("G5_yolo_subprocess",    level_G5_yolo_subprocess,
            {"model_path": model_path, "imgsz": args.imgsz}),
    }

    all_results: List[LevelResult] = []
    result_map: Dict[str, LevelResult] = {}

    for lvl in requested_levels:
        if lvl not in level_funcs:
            print(f"[WARN] Unknown level {lvl}, skipping")
            continue

        lbl, fn, kwargs = level_funcs[lvl]

        print(f"\n{'='*68}")
        print(f"  LEVEL G{lvl}: {fn.__doc__.strip().split(chr(10))[0]}")
        print(f"{'='*68}")

        # Pause between levels for USB recovery
        time.sleep(3.0)

        result = fn(ids, args.duration, **kwargs)
        all_results.append(result)
        result_map[result.level] = result
        print_result(result)

    # --- Summary ---
    print_summary_table(all_results)

    # Stall details
    print("\n  STALL DETAILS:")
    for r in all_results:
        if r.stalls:
            print(f"\n  {r.level}:")
            for s in r.stalls[:15]:
                severity = "SEVERE" if s.gap_s >= SEVERE_STALL_THRESHOLD_S else "stall"
                print(f"    frame {s.frame_idx:5d}: {s.gap_s*1000:.0f}ms [{severity}]")
            if len(r.stalls) > 15:
                print(f"    ... and {len(r.stalls) - 15} more")

    # --- Verdict ---
    print_verdict(result_map)

    # --- Cleanup ---
    ids.close()

    print(f"\n  Done. Camera closed. Log: {log_path}")

    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    _log_fh.close()

    return 0


if __name__ == "__main__":
    # Required for multiprocessing on Windows
    multiprocessing.freeze_support()
    sys.exit(main() or 0)
