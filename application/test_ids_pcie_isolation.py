#!/usr/bin/env python3
"""
IDS + PCIe Contention Isolation Test Script
============================================

Incrementally adds PCIe stress to a bare IDS acquisition loop and measures
stall frequency at each level.  Tests G3-chat mitigations (buffer count,
stream recovery, thread priority) at the first level that stalls.

Usage:
    cd application
    python test_ids_pcie_isolation.py [--duration 30] [--yolo-model yolo26x-pose] [--imgsz 1280]

Levels:
    0  Tight IDS loop (no GPU)               — hardware baseline
    1  + constant GPU matmul (background)     — GPU compute load
    2  + pinned upload of each frame to GPU   — CPU→GPU DMA
    3  + GPU→CPU download (simulating preview)— GPU→CPU DMA
    4  + YOLO inference on uploaded tensor     — realistic inference
    5  + DearPyGui-style CPU→GPU texture      — full pipeline simulation

At first stalling level, re-runs with G3-chat mitigations:
    M1  Increase buffer count to 30
    M2  Enable StreamPipeErrorRecoveryCount
    M3  Boost acquisition thread priority (Windows)
    M4  Use CUDA stream for non-blocking transfers
    M5  All mitigations combined

Author: Generated for WallDance IDS investigation
Date:   February 2026
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import os
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Add src/ to path so we can reuse IDS helpers
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
# YOLO (optional, Level 4+)
# ---------------------------------------------------------------------------
YOLO_AVAILABLE = False
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    pass


# ===========================================================================
#  Data structures
# ===========================================================================

@dataclass
class StallEvent:
    """A single stall event."""
    frame_idx: int
    gap_s: float
    timestamp: float


@dataclass
class LevelResult:
    """Result of one test level run."""
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


# ===========================================================================
#  GenTL path helper (reused from ids_camera.py logic)
# ===========================================================================

def ensure_gentl_path():
    """Set GENICAM_GENTL64_PATH if not already set."""
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
#  IDS acquisition core — low-level, no IDSCamera class dependencies
# ===========================================================================

class IDSAcquisitionCore:
    """
    Minimal IDS camera acquisition — opens camera, allocates buffers,
    runs tight acquisition loop.  Designed for isolation testing.
    """

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
        """Open first available IDS camera at full resolution."""
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

        # --- Continuous free-run ---
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

        # --- Pixel format: prefer Mono8 ---
        pf_node = nm.FindNode("PixelFormat")
        avail = []
        for entry in pf_node.Entries():
            if entry.AccessStatus() != ids_peak.NodeAccessStatus_NotAvailable:
                avail.append(entry.SymbolicValue())
        print(f"[IDS] Available formats: {avail}")

        if "Mono8" in avail:
            pf_node.SetCurrentEntry(pf_node.FindEntry("Mono8"))
        elif avail:
            pf_node.SetCurrentEntry(pf_node.FindEntry(avail[0]))

        self.pixel_format = pf_node.CurrentEntry().SymbolicValue()
        print(f"[IDS] PixelFormat: {self.pixel_format}")

        # --- Full resolution ---
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

        # --- FPS ---
        try:
            fps_node = nm.FindNode("AcquisitionFrameRate")
            target_fps = min(self.max_fps, fps_node.Maximum())
            fps_node.SetValue(target_fps)
            self.fps = fps_node.Value()
        except Exception:
            self.fps = self.max_fps
        print(f"[IDS] FPS: {self.fps:.1f}")

        # --- Exposure auto ---
        try:
            auto_node = nm.FindNode("ExposureAuto")
            auto_node.SetCurrentEntry(auto_node.FindEntry("Continuous"))
        except Exception:
            pass

        # --- DeviceLinkThroughputLimit (read only) ---
        try:
            tl_node = nm.FindNode("DeviceLinkThroughputLimit")
            print(f"[IDS] DeviceLinkThroughputLimit: {tl_node.Value()/1e6:.0f} MB/s")
        except Exception:
            pass

        # --- Open data stream ---
        ds_list = self._device.DataStreams()
        if ds_list.empty():
            print("[IDS] No data streams")
            return False
        self._datastream = ds_list[0].OpenDataStream()
        self._ds_nodemap = self._datastream.NodeMaps()[0]

        # --- NewestOnly ---
        try:
            handling = self._ds_nodemap.FindNode("StreamBufferHandlingMode")
            handling.SetCurrentEntry(handling.FindEntry("NewestOnly"))
            print("[IDS] BufferHandling: NewestOnly")
        except Exception as e:
            print(f"[IDS] Could not set NewestOnly: {e}")

        # --- Allocate buffers ---
        self._allocate_buffers(self.buffer_count)

        return True

    def _allocate_buffers(self, count: int):
        """Allocate and queue `count` acquisition buffers."""
        ds = self._datastream
        ds.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
        for buf in ds.AnnouncedBuffers():
            ds.RevokeBuffer(buf)

        payload_size = self._node_map.FindNode("PayloadSize").Value()
        for _ in range(count):
            buf = ds.AllocAndAnnounceBuffer(payload_size)
            ds.QueueBuffer(buf)
        print(f"[IDS] Allocated {count} buffers ({payload_size} bytes each)")

    def reallocate_buffers(self, count: int):
        """Re-allocate buffers (for mitigation testing). Must be called
        while acquisition is stopped."""
        self.buffer_count = count
        self._allocate_buffers(count)

    def set_stream_recovery(self, count: int = 5):
        """Try to enable StreamPipeErrorRecoveryCount (G3-chat mitigation)."""
        try:
            node = self._ds_nodemap.FindNode("StreamPipeErrorRecoveryCount")
            node.SetValue(count)
            print(f"[IDS] StreamPipeErrorRecoveryCount = {count}")
            return True
        except Exception as e:
            print(f"[IDS] StreamPipeErrorRecoveryCount not available: {e}")
            return False

    def start_acquisition(self):
        """Start streaming."""
        # Flush + re-queue all buffers so the input pool is full.
        # After a previous stop, buffers may be stuck in the output queue.
        self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
        for buf in self._datastream.AnnouncedBuffers():
            self._datastream.QueueBuffer(buf)
        self._datastream.StartAcquisition()
        self._node_map.FindNode("AcquisitionStart").Execute()

    def stop_acquisition(self):
        """Stop streaming."""
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
        """Release everything."""
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
        """Extract mono8 frame from IDS buffer (fast path)."""
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

        # fallback
        return raw[:pixels].reshape(self.height, self.width).copy() if raw.size >= pixels else None


# ===========================================================================
#  GPU workload helpers
# ===========================================================================

class GpuMatmulBackground:
    """Runs continuous GPU matmul in a background thread."""

    def __init__(self, size: int = 2048):
        self._running = False
        self._thread = None
        self._size = size

    def start(self):
        if not CUDA_AVAILABLE:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
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
            _ = torch.mm(a, b)
            torch.cuda.synchronize()


def create_pinned_buffer(h: int, w: int, dtype=torch.uint8) -> torch.Tensor:
    """Create a pinned-memory buffer for async GPU upload."""
    return torch.empty(h, w, dtype=dtype).pin_memory()


def upload_pinned(pinned: torch.Tensor, mono: np.ndarray,
                  stream: Optional['torch.cuda.Stream'] = None) -> torch.Tensor:
    """Upload mono numpy frame to GPU via pinned buffer."""
    t = torch.from_numpy(mono)
    pinned.copy_(t)
    if stream is not None:
        with torch.cuda.stream(stream):
            return pinned.cuda(non_blocking=True)
    return pinned.cuda(non_blocking=True)


def download_preview(gpu_tensor: torch.Tensor, scale: float = 0.35) -> np.ndarray:
    """Simulate preview download: resize on GPU, convert to uint8, .cpu()."""
    # Ensure 4D (N, C, H, W) for F.interpolate
    t = gpu_tensor.float()
    if t.dim() == 2:        # (H, W) mono
        t = t.unsqueeze(0).unsqueeze(0)  # → (1, 1, H, W)
    elif t.dim() == 3:      # (C, H, W)
        t = t.unsqueeze(0)               # → (1, C, H, W)
    h, w = t.shape[-2:]
    nh, nw = max(1, int(h * scale)), max(1, int(w * scale))
    resized = torch.nn.functional.interpolate(t, size=(nh, nw), mode='area')
    u8 = resized.clamp_(0, 255).byte()
    return u8.cpu().numpy()


# ===========================================================================
#  Windows thread priority
# ===========================================================================

def set_thread_high_priority():
    """Set current thread to high priority on Windows."""
    if sys.platform != 'win32':
        return False
    try:
        handle = ctypes.windll.kernel32.GetCurrentThread()
        # THREAD_PRIORITY_HIGHEST = 2
        ctypes.windll.kernel32.SetThreadPriority(handle, 2)
        print("[MITIGATION] Thread priority set to HIGHEST")
        return True
    except Exception as e:
        print(f"[MITIGATION] Could not set thread priority: {e}")
        return False


# ===========================================================================
#  Core test runner
# ===========================================================================

STALL_THRESHOLD_S = 0.4  # Gap > this = stall (camera runs at ~20fps = 50ms)
SEVERE_STALL_THRESHOLD_S = 1.0  # "Real" hardware stall


# Default warmup — set from CLI via --warmup
WARMUP_S: float = 3.0

import queue as _queue


def run_acquisition_loop(
    ids: IDSAcquisitionCore,
    duration_s: float,
    *,
    # Level-specific callback — runs in a SEPARATE GPU worker thread
    # (concurrent with acquisition, matching real app architecture).
    on_frame: Optional[callable] = None,
    # Optional: run acquisition in a high-priority thread
    high_priority: bool = False,
    # Optional: CUDA stream for non-blocking uploads
    cuda_stream: Optional['torch.cuda.Stream'] = None,
    label: str = "test",
) -> LevelResult:
    """Run acquisition for `duration_s` seconds, measuring stalls.

    Architecture (matches the real WallDance app):
        Acq thread  — tight loop: WaitForBuffer → memcpy → queue frame
        GPU thread  — concurrent: dequeue frame → on_frame(mono) [GPU work]
        Main thread — sleeps for duration, then signals stop.

    The acq thread NEVER touches the GPU.  All PCIe traffic from on_frame
    runs concurrently in the GPU worker thread, reproducing the real
    PCIe bus contention between USB3 DMA and GPU DMA.

    The first WARMUP_S seconds are acquired normally (callbacks run) but
    stalls during that window are excluded from the reported counts.

    Args:
        ids: Open IDSAcquisitionCore with acquisition started.
        duration_s: How long to measure (excluding warmup).
        on_frame: Callback(mono_np) called for each frame in GPU thread.
        high_priority: Set acq thread to HIGHEST priority (Windows).
        cuda_stream: (unused, kept for API compat).
        label: Label for the result.

    Returns:
        LevelResult with stall statistics.
    """
    warmup_s = WARMUP_S
    total_run = warmup_s + duration_s
    timeout_ms = max(150, min(500, int(5000.0 / max(1.0, ids.fps))))
    result = LevelResult(level=label, description="", duration_s=duration_s,
                         frame_count=0, stall_count=0)
    stalls: List[StallEvent] = []
    warmup_stalls: List[StallEvent] = []
    gaps: List[float] = []

    stop_event = threading.Event()
    # Frame queue: acq thread → GPU worker thread (bounded, drop-oldest)
    frame_q: _queue.Queue = _queue.Queue(maxsize=4)

    # ---- GPU worker thread ----
    gpu_errors = []

    def gpu_worker():
        """Consumer: runs on_frame for each frame, concurrent with acq."""
        while not stop_event.is_set():
            try:
                mono = frame_q.get(timeout=0.1)
            except _queue.Empty:
                continue
            if mono is None:  # poison pill
                break
            if on_frame is not None:
                try:
                    on_frame(mono)
                except Exception as e:
                    if len(gpu_errors) < 5:
                        gpu_errors.append(str(e))
                        print(f"[{label}] on_frame error: {e}")

    # ---- Acquisition thread ----
    def acq_loop():
        if high_priority:
            set_thread_high_priority()

        acq_start = time.perf_counter()
        last_frame_time = acq_start
        frame_idx = 0
        warmup_frames = 0
        timeouts = 0

        while not stop_event.is_set():
            try:
                buffer = ids._datastream.WaitForFinishedBuffer(timeout_ms)
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

            if in_warmup:
                warmup_frames += 1
                if gap > STALL_THRESHOLD_S:
                    warmup_stalls.append(StallEvent(frame_idx, gap, now))
            else:
                gaps.append(gap)
                if gap > STALL_THRESHOLD_S:
                    stalls.append(StallEvent(frame_idx, gap, now))

            # Extract frame and immediately return buffer
            try:
                mono = ids.unpack_frame(buffer)
            except Exception:
                continue

            # Send to GPU worker (non-blocking; drop if full — newest-only)
            if mono is not None:
                try:
                    frame_q.put_nowait(mono)
                except _queue.Full:
                    # Drop oldest, enqueue newest (mimic NewestOnly)
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

    # Start both threads
    gpu_thread = threading.Thread(target=gpu_worker, name=f"GpuWorker-{label}", daemon=True)
    acq_thread = threading.Thread(target=acq_loop, name=f"AcqTest-{label}", daemon=True)

    ids.start_acquisition()
    gpu_thread.start()
    acq_thread.start()

    # Wait for warmup + measurement duration
    time.sleep(total_run)
    stop_event.set()
    ids._datastream.KillWait()
    acq_thread.join(timeout=3.0)
    # Poison pill for GPU worker
    try:
        frame_q.put(None, timeout=1.0)
    except _queue.Full:
        pass
    gpu_thread.join(timeout=3.0)
    ids.stop_acquisition()

    # Compute stats (warmup frames excluded)
    result.frame_count = len(gaps)
    result.stalls = stalls
    result.stall_count = len(stalls)
    if gaps:
        result.avg_gap_ms = np.mean(gaps) * 1000
        result.max_gap_ms = np.max(gaps) * 1000
        result.avg_fps = result.frame_count / duration_s
    if gpu_errors:
        result.notes += f" | {len(gpu_errors)} gpu_worker errors"

    return result


# ===========================================================================
#  Test levels
# ===========================================================================

def level_0_baseline(ids: IDSAcquisitionCore, duration: float) -> LevelResult:
    """L0: Tight IDS loop, no GPU — hardware baseline."""
    result = run_acquisition_loop(ids, duration, label="L0_baseline")
    result.description = "Tight IDS loop, no GPU"
    return result


def level_1_gpu_compute(ids: IDSAcquisitionCore, duration: float) -> LevelResult:
    """L1: + continuous GPU matmul in background."""
    bg = GpuMatmulBackground(size=2048)
    bg.start()
    time.sleep(0.5)  # Let GPU warm up

    result = run_acquisition_loop(ids, duration, label="L1_gpu_compute")
    result.description = "Tight IDS + background GPU matmul (2048x2048)"

    bg.stop()
    return result


def level_2_pinned_upload(ids: IDSAcquisitionCore, duration: float) -> LevelResult:
    """L2: + pinned memory upload of each frame to GPU."""
    pinned = create_pinned_buffer(ids.height, ids.width)

    def on_frame(mono):
        gpu_t = upload_pinned(pinned, mono)
        # Minimal sync: just ensure upload is queued
        # (non_blocking=True means no explicit sync needed for pipelining)

    result = run_acquisition_loop(ids, duration, on_frame=on_frame,
                                  label="L2_pinned_upload")
    result.description = f"IDS + pinned GPU upload ({ids.width}x{ids.height}, ~{ids.width*ids.height/1e6:.1f} MB/frame)"
    del pinned
    return result


def level_3_gpu_download(ids: IDSAcquisitionCore, duration: float) -> LevelResult:
    """L3: + GPU→CPU download simulating preview at 10fps."""
    pinned = create_pinned_buffer(ids.height, ids.width)
    preview_interval = 1.0 / 10.0  # 10 fps preview cap
    last_preview = [0.0]

    def on_frame(mono):
        gpu_t = upload_pinned(pinned, mono)
        now = time.perf_counter()
        if now - last_preview[0] >= preview_interval:
            last_preview[0] = now
            _ = download_preview(gpu_t, scale=0.35)

    result = run_acquisition_loop(ids, duration, on_frame=on_frame,
                                  label="L3_gpu_download")
    result.description = "IDS + pinned upload + GPU→CPU preview @10fps (0.35x)"
    del pinned
    return result


def level_4_yolo(ids: IDSAcquisitionCore, duration: float,
                 model_path: str, imgsz: int) -> LevelResult:
    """L4: + YOLO inference on uploaded tensor."""
    if not YOLO_AVAILABLE:
        r = LevelResult("L4_yolo", "SKIPPED — ultralytics not installed",
                         duration, 0, 0)
        r.notes = "Install ultralytics to test this level"
        return r

    pinned = create_pinned_buffer(ids.height, ids.width)
    preview_interval = 1.0 / 10.0
    last_preview = [0.0]

    # Load YOLO model
    print(f"[L4] Loading YOLO model: {model_path} (imgsz={imgsz})")
    model = YOLO(model_path)
    # Warmup
    dummy = np.zeros((ids.height, ids.width, 3), dtype=np.uint8)
    for _ in range(3):
        model.predict(dummy, imgsz=imgsz, verbose=False)
    print("[L4] YOLO warmup complete")

    import cv2

    def on_frame(mono):
        gpu_t = upload_pinned(pinned, mono)

        # YOLO expects BGR numpy or GPU tensor — use numpy path
        # (this is what the real app does: Ultralytics auto-uploads internally)
        bgr = cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)
        results = model.predict(bgr, imgsz=imgsz, verbose=False, device='cuda')
        # Extract results (forces GPU→CPU sync for ~1.3KB of keypoints)
        if results and results[0].keypoints is not None:
            _ = results[0].keypoints.data.cpu()

        now = time.perf_counter()
        if now - last_preview[0] >= preview_interval:
            last_preview[0] = now
            _ = download_preview(gpu_t, scale=0.35)

    result = run_acquisition_loop(ids, duration, on_frame=on_frame,
                                  label="L4_yolo")
    result.description = f"IDS + upload + YOLO ({os.path.basename(model_path)} @{imgsz}) + preview"
    del pinned, model
    gc.collect()
    if CUDA_AVAILABLE:
        torch.cuda.empty_cache()
    return result


def level_5_texture_upload(ids: IDSAcquisitionCore, duration: float,
                           model_path: str, imgsz: int) -> LevelResult:
    """L5: + simulated DearPyGui texture CPU→GPU upload at 10fps."""
    if not YOLO_AVAILABLE:
        r = LevelResult("L5_full", "SKIPPED — ultralytics not installed",
                         duration, 0, 0)
        return r

    pinned = create_pinned_buffer(ids.height, ids.width)
    preview_interval = 1.0 / 10.0
    last_preview = [0.0]

    model = YOLO(model_path)
    dummy = np.zeros((ids.height, ids.width, 3), dtype=np.uint8)
    for _ in range(3):
        model.predict(dummy, imgsz=imgsz, verbose=False)

    import cv2

    # Simulate DPG texture: pre-allocate a GPU buffer for the texture upload
    tex_h = int(ids.height * 0.35)
    tex_w = int(ids.width * 0.35)
    texture_buf = torch.empty(tex_h, tex_w, 4, dtype=torch.uint8).pin_memory()

    def on_frame(mono):
        gpu_t = upload_pinned(pinned, mono)

        bgr = cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)
        results = model.predict(bgr, imgsz=imgsz, verbose=False, device='cuda')
        if results and results[0].keypoints is not None:
            _ = results[0].keypoints.data.cpu()

        now = time.perf_counter()
        if now - last_preview[0] >= preview_interval:
            last_preview[0] = now
            preview_np = download_preview(gpu_t, scale=0.35)
            # Simulate DPG texture upload: CPU RGBA → pinned → GPU
            rgba = np.zeros((tex_h, tex_w, 4), dtype=np.uint8)
            rgba[..., :1] = preview_np.reshape(tex_h, tex_w, 1)[:tex_h, :tex_w]
            rgba[..., 3] = 255
            texture_buf.copy_(torch.from_numpy(rgba))
            _ = texture_buf.cuda(non_blocking=True)

    result = run_acquisition_loop(ids, duration, on_frame=on_frame,
                                  label="L5_full")
    result.description = f"IDS + upload + YOLO + preview + texture upload (full pipeline sim)"
    del pinned, model, texture_buf
    gc.collect()
    if CUDA_AVAILABLE:
        torch.cuda.empty_cache()
    return result


# ===========================================================================
#  G3-chat mitigations — re-run at first stalling level
# ===========================================================================

def run_mitigations_at_level(
    ids: IDSAcquisitionCore,
    duration: float,
    stalling_level_fn,
    stalling_level_kwargs: dict,
    stalling_level_name: str,
) -> List[LevelResult]:
    """Re-run a stalling level with various G3-chat mitigations.

    Args:
        ids: Open IDSAcquisitionCore (acquisition stopped).
        duration: Seconds per mitigation test.
        stalling_level_fn: The level function to re-run (e.g. level_2_pinned_upload).
        stalling_level_kwargs: kwargs for the level function.
        stalling_level_name: Name for display.

    Returns:
        List of LevelResult for each mitigation.
    """
    results = []

    # --- M1: More buffers (30) ---
    print(f"\n{'='*60}")
    print(f"  MITIGATION M1: 30 buffers (was {ids.buffer_count})")
    print(f"{'='*60}")
    ids.reallocate_buffers(30)
    r = stalling_level_fn(ids, duration, **stalling_level_kwargs)
    r.level = f"M1_buf30@{stalling_level_name}"
    r.description = f"30 buffers — {r.description}"
    results.append(r)
    # Restore
    ids.reallocate_buffers(16)

    # --- M2: StreamPipeErrorRecoveryCount ---
    print(f"\n{'='*60}")
    print(f"  MITIGATION M2: StreamPipeErrorRecoveryCount = 5")
    print(f"{'='*60}")
    recovery_ok = ids.set_stream_recovery(5)
    r = stalling_level_fn(ids, duration, **stalling_level_kwargs)
    r.level = f"M2_recovery@{stalling_level_name}"
    r.description = f"StreamPipeRecovery=5 — {r.description}"
    if not recovery_ok:
        r.notes = "StreamPipeErrorRecoveryCount NOT available on this camera"
    results.append(r)

    # --- M3: High-priority acq thread ---
    print(f"\n{'='*60}")
    print(f"  MITIGATION M3: High-priority acquisition thread")
    print(f"{'='*60}")

    # We need to wrap the level function to inject high_priority=True
    # For this, we modify the run_acquisition_loop call via a wrapper
    original_fn = stalling_level_fn

    def level_with_priority(ids_arg, dur, **kw):
        """Wrap the level to use high-priority acq thread."""
        # We'll re-implement a simplified version that passes high_priority
        pinned = create_pinned_buffer(ids_arg.height, ids_arg.width) if CUDA_AVAILABLE else None

        def on_frame_upload(mono):
            if pinned is not None:
                _ = upload_pinned(pinned, mono)

        result = run_acquisition_loop(
            ids_arg, dur, on_frame=on_frame_upload,
            high_priority=True,
            label=f"M3_priority@{stalling_level_name}"
        )
        result.description = f"High-priority thread — upload loop"
        if pinned is not None:
            del pinned
        return result

    r = level_with_priority(ids, duration)
    results.append(r)

    # --- M4: CUDA stream for non-blocking transfers ---
    print(f"\n{'='*60}")
    print(f"  MITIGATION M4: Dedicated CUDA stream")
    print(f"{'='*60}")
    if CUDA_AVAILABLE:
        cuda_stream = torch.cuda.Stream()
        pinned = create_pinned_buffer(ids.height, ids.width)

        def on_frame_stream(mono):
            _ = upload_pinned(pinned, mono, stream=cuda_stream)

        r = run_acquisition_loop(ids, duration, on_frame=on_frame_stream,
                                 label=f"M4_stream@{stalling_level_name}")
        r.description = "Dedicated CUDA stream for upload"
        del pinned
        results.append(r)
    else:
        r = LevelResult(f"M4_stream@{stalling_level_name}",
                         "SKIPPED — CUDA not available", duration, 0, 0)
        results.append(r)

    # --- M5: All mitigations combined ---
    print(f"\n{'='*60}")
    print(f"  MITIGATION M5: ALL combined (30 buf + recovery + priority + stream)")
    print(f"{'='*60}")
    ids.reallocate_buffers(30)
    ids.set_stream_recovery(5)

    if CUDA_AVAILABLE:
        cuda_stream = torch.cuda.Stream()
        pinned = create_pinned_buffer(ids.height, ids.width)

        def on_frame_all(mono):
            _ = upload_pinned(pinned, mono, stream=cuda_stream)

        r = run_acquisition_loop(ids, duration, on_frame=on_frame_all,
                                 high_priority=True,
                                 label=f"M5_all@{stalling_level_name}")
        r.description = "ALL mitigations: 30buf + recovery + priority + stream"
        del pinned
    else:
        r = run_acquisition_loop(ids, duration, high_priority=True,
                                 label=f"M5_all@{stalling_level_name}")
        r.description = "ALL mitigations (no CUDA): 30buf + recovery + priority"

    results.append(r)
    # Restore defaults
    ids.reallocate_buffers(16)

    return results


# ===========================================================================
#  Reporting
# ===========================================================================

def print_result(r: LevelResult, idx: int = 0):
    """Print a single result line."""
    stall_str = f"{r.stall_count} stalls"
    severe = sum(1 for s in r.stalls if s.gap_s >= SEVERE_STALL_THRESHOLD_S)
    if severe:
        stall_str += f" ({severe} severe ≥1s)"

    rate_str = ""
    if r.frame_count > 0 and r.stall_count > 0:
        rate_str = f"  (1 per {r.frame_count // r.stall_count} frames)"
    elif r.frame_count > 0:
        rate_str = f"  (0 in {r.frame_count} frames)"

    print(f"  [{r.level:30s}]  {r.avg_fps:5.1f} fps | {stall_str:25s}{rate_str}")
    print(f"    {'':30s}   avg_gap={r.avg_gap_ms:.1f}ms  max_gap={r.max_gap_ms:.0f}ms  "
          f"timeouts={r.timeout_count}  frames={r.frame_count}")
    if r.notes:
        print(f"    {'':30s}   NOTE: {r.notes}")
    print(f"    {'':30s}   {r.description}")


def print_summary_table(results: List[LevelResult]):
    """Print final summary table."""
    print("\n")
    print("=" * 100)
    print("  SUMMARY TABLE")
    print("=" * 100)
    print(f"  {'Level':<35s} {'FPS':>6s}  {'Stalls':>7s}  {'Severe':>7s}  "
          f"{'Rate':>14s}  {'MaxGap':>8s}  {'Timeouts':>8s}")
    print("-" * 100)

    for r in results:
        severe = sum(1 for s in r.stalls if s.gap_s >= SEVERE_STALL_THRESHOLD_S)
        if r.frame_count > 0 and r.stall_count > 0:
            rate = f"1/{r.frame_count // r.stall_count} frm"
        elif r.frame_count > 0:
            rate = f"0/{r.frame_count} frm"
        else:
            rate = "N/A"
        print(f"  {r.level:<35s} {r.avg_fps:6.1f}  {r.stall_count:7d}  {severe:7d}  "
              f"{rate:>14s}  {r.max_gap_ms:7.0f}ms  {r.timeout_count:>8d}")

    print("=" * 100)

    # Stall threshold info
    print(f"\n  Stall threshold: >{STALL_THRESHOLD_S*1000:.0f}ms gap between frames")
    print(f"  Severe stall:    >{SEVERE_STALL_THRESHOLD_S*1000:.0f}ms gap (hardware USB3 hang)")
    print()


# ===========================================================================
#  Main
# ===========================================================================

def find_model(model_name: str) -> str:
    """Find the YOLO model file — check models/ dir first, then application/."""
    workspace_root = os.path.dirname(_SCRIPT_DIR)
    candidates = [
        os.path.join(workspace_root, "models", model_name),
        os.path.join(_SCRIPT_DIR, model_name),
        model_name,
    ]
    # Also check for TensorRT engines
    base, ext = os.path.splitext(model_name)
    if ext == ".pt":
        for imgsz_val in [1280, 960, 800, 640]:
            candidates.append(
                os.path.join(workspace_root, "models", f"{base}_{imgsz_val}.engine"))

    for c in candidates:
        if os.path.isfile(c):
            return c

    # Fall back to just the name (let YOLO download if needed)
    return model_name


def main():
    parser = argparse.ArgumentParser(
        description="IDS + PCIe Contention Isolation Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--duration", type=float, default=30.0,
                        help="Seconds per test level (default: 30)")
    parser.add_argument("--yolo-model", type=str, default="yolo26x-pose.pt",
                        help="YOLO model filename (default: yolo26x-pose.pt)")
    parser.add_argument("--imgsz", type=int, default=1280,
                        help="YOLO imgsz (default: 1280)")
    parser.add_argument("--levels", type=str, default="0,1,2,3,4,5",
                        help="Comma-separated levels to run (default: 0,1,2,3,4,5)")
    parser.add_argument("--mitigations", action="store_true", default=True,
                        help="Run G3-chat mitigations at first stalling level (default: True)")
    parser.add_argument("--no-mitigations", action="store_false", dest="mitigations",
                        help="Skip mitigations")
    parser.add_argument("--stall-threshold", type=float, default=0.4,
                        help="Gap (seconds) to count as a stall (default: 0.4)")
    parser.add_argument("--buffer-count", type=int, default=16,
                        help="Initial buffer count (default: 16)")
    parser.add_argument("--warmup", type=float, default=3.0,
                        help="Seconds of warmup to ignore per level (default: 3)")
    parser.add_argument("--log", type=str, default="test_run.log",
                        help="Log file path (default: test_run.log)")

    args = parser.parse_args()

    # --- Tee stdout/stderr to log file ---
    log_path = os.path.join(_SCRIPT_DIR, args.log)

    class Tee:
        """Write to both the original stream and a log file."""
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
    print(f"  Logging to: {log_path}")

    global STALL_THRESHOLD_S, WARMUP_S
    STALL_THRESHOLD_S = args.stall_threshold
    WARMUP_S = args.warmup

    requested_levels = [int(x) for x in args.levels.split(",")]

    print("=" * 70)
    print("  IDS + PCIe Contention Isolation Test")
    print("=" * 70)
    print(f"  Duration per level : {args.duration}s")
    print(f"  Warmup (ignored)   : {WARMUP_S:.0f}s")
    print(f"  Stall threshold    : {STALL_THRESHOLD_S*1000:.0f}ms")
    print(f"  Buffer count       : {args.buffer_count}")
    print(f"  YOLO model         : {args.yolo_model} @ imgsz={args.imgsz}")
    print(f"  Levels             : {requested_levels}")
    print(f"  Mitigations        : {'Yes' if args.mitigations else 'No'}")
    print(f"  CUDA available     : {CUDA_AVAILABLE}")
    print(f"  YOLO available     : {YOLO_AVAILABLE}")
    print()

    # --- Open camera ---
    ids = IDSAcquisitionCore(buffer_count=args.buffer_count, max_fps=20.0)
    if not ids.open():
        print("\nFATAL: Could not open IDS camera. Exiting.")
        return 1

    print(f"\n  Camera ready: {ids.width}x{ids.height} @ {ids.fps:.1f}fps, {ids.pixel_format}\n")

    # --- Level dispatch ---
    model_path = find_model(args.yolo_model)

    level_funcs = {
        0: (level_0_baseline, {}),
        1: (level_1_gpu_compute, {}),
        2: (level_2_pinned_upload, {}),
        3: (level_3_gpu_download, {}),
        4: (level_4_yolo, {"model_path": model_path, "imgsz": args.imgsz}),
        5: (level_5_texture_upload, {"model_path": model_path, "imgsz": args.imgsz}),
    }

    all_results: List[LevelResult] = []
    worst_stalling_level = None
    worst_stall_count = 0

    for lvl in requested_levels:
        if lvl not in level_funcs:
            print(f"[WARN] Unknown level {lvl}, skipping")
            continue

        fn, kwargs = level_funcs[lvl]

        print(f"\n{'='*60}")
        print(f"  LEVEL {lvl}: {fn.__doc__.strip().split(chr(10))[0]}")
        print(f"{'='*60}")

        # Small pause between levels for USB recovery
        time.sleep(2.0)

        result = fn(ids, args.duration, **kwargs)
        all_results.append(result)
        print_result(result)

        # Track the level with the most stalls (prefer levels with GPU, ≥2)
        # so mitigations target the realistic worst-case, not the bare baseline.
        if result.stall_count >= 2 and result.stall_count > worst_stall_count:
            # Prefer GPU-active levels (≥2) over baseline; accept L0/L1 only
            # if no GPU level has stalled yet.
            if lvl >= 2 or worst_stalling_level is None or worst_stalling_level < 2:
                worst_stalling_level = lvl
                worst_stall_count = result.stall_count
                print(f"\n  *** Worst stalling level so far: L{lvl} "
                      f"({result.stall_count} stalls) ***")

    # --- Run mitigations at worst stalling level ---
    if args.mitigations and worst_stalling_level is not None:
        fn, kwargs = level_funcs[worst_stalling_level]

        print(f"\n\n{'#'*70}")
        print(f"  G3-CHAT MITIGATIONS — re-running Level {worst_stalling_level}")
        print(f"{'#'*70}")

        # Stop acquisition for buffer reallocation
        time.sleep(2.0)

        mitigation_results = run_mitigations_at_level(
            ids, args.duration,
            stalling_level_fn=fn,
            stalling_level_kwargs=kwargs,
            stalling_level_name=f"L{worst_stalling_level}",
        )

        for mr in mitigation_results:
            print_result(mr)
            all_results.append(mr)

    elif args.mitigations and worst_stalling_level is None:
        print("\n  No level triggered ≥2 stalls — mitigations skipped.")
        print("  (Try longer --duration or lower --stall-threshold)")

    # --- Final summary ---
    print_summary_table(all_results)

    # Stall details
    print("  STALL DETAILS:")
    for r in all_results:
        if r.stalls:
            print(f"\n  {r.level}:")
            for s in r.stalls[:10]:  # Cap at 10
                severity = "SEVERE" if s.gap_s >= SEVERE_STALL_THRESHOLD_S else "stall"
                print(f"    frame {s.frame_idx:4d}: {s.gap_s*1000:.0f}ms [{severity}]")
            if len(r.stalls) > 10:
                print(f"    ... and {len(r.stalls) - 10} more")

    # --- Cleanup ---
    ids.close()
    print(f"\n  Done. Camera closed. Log saved to: {log_path}")

    # Close log
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    _log_fh.close()

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
