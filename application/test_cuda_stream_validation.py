#!/usr/bin/env python3
"""
Validation test: CUDA stream mitigation vs baseline.

Run 1: focused on whether a dedicated CUDA stream truly eliminates stalls.

Runs 4 tests × 60s each:
  A) L0 baseline (no GPU) — hardware stall rate
  B) L2 upload (default stream) — contention baseline
  C) L2 upload + CUDA stream — the promising M4 mitigation
  D) L4 YOLO + CUDA stream — realistic workload with mitigation

This tests the hypothesis that a dedicated CUDA stream isolates USB3 DMA
from GPU DMA, eliminating PCIe contention stalls.
"""

from __future__ import annotations
import argparse, os, sys, threading, time, gc
import queue as _queue
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "src"))

# -- IDS --
from ids_peak import ids_peak
from ids_peak_ipl import ids_peak_ipl
from ids_peak import ids_peak_ipl_extension

# -- Torch --
import torch
import torch.nn.functional as F

# -- YOLO --
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

STALL_THRESHOLD_S = 0.4
SEVERE_THRESHOLD_S = 1.0
WARMUP_S = 3.0


@dataclass
class StallEvent:
    frame_idx: int
    gap_s: float


@dataclass
class RunResult:
    label: str
    description: str
    duration_s: float
    frame_count: int = 0
    stall_count: int = 0
    severe_count: int = 0
    stalls: List[StallEvent] = field(default_factory=list)
    avg_fps: float = 0.0
    max_gap_ms: float = 0.0
    avg_gap_ms: float = 0.0
    timeouts: int = 0
    warmup_stalls: int = 0


def ensure_gentl_path():
    if os.environ.get("GENICAM_GENTL64_PATH"):
        return
    import glob as _glob
    for base in ["/opt/ids/ids-peak", "/opt/ids/ids-peak/cti"]:
        for g in ["*.cti", "*/*.cti"]:
            for p in _glob.glob(os.path.join(base, g)):
                os.environ.setdefault("GENICAM_GENTL64_PATH", os.path.dirname(p))


class Camera:
    """Minimal IDS camera wrapper."""
    def __init__(self, buffer_count=16, max_fps=20.0):
        self.buffer_count = buffer_count
        self.max_fps = max_fps
        self._device = None
        self._datastream = None
        self._node_map = None
        self.width = self.height = 0
        self.pixel_format = ""

    def open(self):
        ensure_gentl_path()
        ids_peak.Library.Initialize()
        dm = ids_peak.DeviceManager.Instance()
        dm.Update()
        for dev in dm.Devices():
            if dev.IsOpenable():
                self._device = dev.OpenDevice(ids_peak.DeviceAccessType_Exclusive)
                break
        if not self._device:
            print("No camera found")
            return False
        self._node_map = self._device.RemoteDevice().NodeMaps()[0]
        nm = self._node_map

        # Continuous free-run
        try:
            am = nm.FindNode("AcquisitionMode")
            am.SetCurrentEntry(am.FindEntry("Continuous"))
        except Exception: pass
        try:
            nm.FindNode("TriggerMode").SetCurrentEntry(
                nm.FindNode("TriggerMode").FindEntry("Off"))
        except Exception: pass

        # Pixel format
        pf = nm.FindNode("PixelFormat")
        avail = [e.SymbolicValue() for e in pf.Entries()
                 if e.AccessStatus() != ids_peak.NodeAccessStatus_NotAvailable]
        if "Mono8" in avail:
            pf.SetCurrentEntry(pf.FindEntry("Mono8"))
        self.pixel_format = pf.CurrentEntry().SymbolicValue()

        # Full res
        try:
            nm.FindNode("Width").SetValue(nm.FindNode("Width").Maximum())
            nm.FindNode("Height").SetValue(nm.FindNode("Height").Maximum())
        except Exception: pass
        self.width = nm.FindNode("Width").Value()
        self.height = nm.FindNode("Height").Value()

        # FPS
        try:
            fps_n = nm.FindNode("AcquisitionFrameRate")
            fps_n.SetValue(min(self.max_fps, fps_n.Maximum()))
        except Exception: pass

        # Exposure auto
        try:
            nm.FindNode("ExposureAuto").SetCurrentEntry(
                nm.FindNode("ExposureAuto").FindEntry("Continuous"))
        except Exception: pass

        # Datastream
        self._datastream = self._device.DataStreams()[0].OpenDataStream()
        ds_nm = self._datastream.NodeMaps()[0]
        try:
            h = ds_nm.FindNode("StreamBufferHandlingMode")
            h.SetCurrentEntry(h.FindEntry("NewestOnly"))
        except Exception: pass

        # Buffers
        self._alloc_bufs(self.buffer_count)
        print(f"Camera: {self.width}x{self.height} {self.pixel_format}")
        return True

    def _alloc_bufs(self, n):
        ds = self._datastream
        ds.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
        for b in ds.AnnouncedBuffers():
            ds.RevokeBuffer(b)
        ps = self._node_map.FindNode("PayloadSize").Value()
        for _ in range(n):
            ds.QueueBuffer(ds.AllocAndAnnounceBuffer(ps))

    def start(self):
        self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
        for b in self._datastream.AnnouncedBuffers():
            self._datastream.QueueBuffer(b)
        self._datastream.StartAcquisition()
        self._node_map.FindNode("AcquisitionStart").Execute()

    def stop(self):
        try: self._datastream.KillWait()
        except: pass
        try: self._node_map.FindNode("AcquisitionStop").Execute()
        except: pass
        try: self._datastream.StopAcquisition(ids_peak.AcquisitionStopMode_Default)
        except: pass

    def close(self):
        self.stop()
        if self._datastream:
            try:
                self._datastream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
                for b in self._datastream.AnnouncedBuffers():
                    self._datastream.RevokeBuffer(b)
            except: pass
        self._device = self._datastream = self._node_map = None
        try: ids_peak.Library.Close()
        except: pass

    def unpack(self, buffer) -> Optional[np.ndarray]:
        try:
            ipl = ids_peak_ipl_extension.BufferToImage(buffer)
            raw = ipl.get_numpy_1D().copy()
        finally:
            self._datastream.QueueBuffer(buffer)
        pf = self.pixel_format.lower()
        px = self.width * self.height
        if "mono10g40" in pf:
            return raw.reshape(-1, 5)[:, :4].reshape(self.height, self.width).copy()
        if "mono8" in pf and raw.size == px:
            return raw.reshape(self.height, self.width).copy()
        return raw[:px].reshape(self.height, self.width).copy() if raw.size >= px else None


def run_test(cam: Camera, duration_s: float, label: str, description: str,
             on_frame=None) -> RunResult:
    """Run acq loop with two-thread model, measure stalls."""
    warmup = WARMUP_S
    total = warmup + duration_s
    timeout_ms = 250
    result = RunResult(label=label, description=description, duration_s=duration_s)
    stalls, warmup_stall_count, gaps = [], [0], []
    stop = threading.Event()
    frame_q = _queue.Queue(maxsize=4)

    def gpu_worker():
        while not stop.is_set():
            try:
                mono = frame_q.get(timeout=0.1)
            except _queue.Empty:
                continue
            if mono is None:
                break
            if on_frame:
                try:
                    on_frame(mono)
                except Exception as e:
                    print(f"  [{label}] on_frame error: {e}")

    def acq_loop():
        acq_start = time.perf_counter()
        last = acq_start
        idx = 0
        to = 0
        while not stop.is_set():
            try:
                buf = cam._datastream.WaitForFinishedBuffer(timeout_ms)
            except Exception as e:
                if "timeout" in str(e).lower():
                    to += 1
                    continue
                if stop.is_set(): break
                continue
            now = time.perf_counter()
            gap = now - last
            last = now
            idx += 1
            in_warmup = (now - acq_start) < warmup
            if in_warmup:
                if gap > STALL_THRESHOLD_S:
                    warmup_stall_count[0] += 1
            else:
                gaps.append(gap)
                if gap > STALL_THRESHOLD_S:
                    stalls.append(StallEvent(idx, gap))
            try:
                mono = cam.unpack(buf)
            except:
                continue
            if mono is not None:
                try:
                    frame_q.put_nowait(mono)
                except _queue.Full:
                    try: frame_q.get_nowait()
                    except: pass
                    try: frame_q.put_nowait(mono)
                    except: pass
        result.timeouts = to

    cam.start()
    gt = threading.Thread(target=gpu_worker, daemon=True)
    at = threading.Thread(target=acq_loop, daemon=True)
    gt.start(); at.start()
    time.sleep(total)
    stop.set()
    cam._datastream.KillWait()
    at.join(3)
    try: frame_q.put(None, timeout=1)
    except: pass
    gt.join(3)
    cam.stop()

    result.frame_count = len(gaps)
    result.stalls = stalls
    result.stall_count = len(stalls)
    result.severe_count = sum(1 for s in stalls if s.gap_s >= SEVERE_THRESHOLD_S)
    result.warmup_stalls = warmup_stall_count[0]
    if gaps:
        result.avg_gap_ms = np.mean(gaps) * 1000
        result.max_gap_ms = np.max(gaps) * 1000
        result.avg_fps = result.frame_count / duration_s
    return result


def print_table(results: List[RunResult]):
    print(f"\n{'='*110}")
    print(f"  {'Label':<30s} {'FPS':>6}  {'Stalls':>7}  {'Severe':>7}  {'Rate':>14}  {'MaxGap':>8}  {'TO':>5}  {'WarmStall':>9}")
    print(f"{'-'*110}")
    for r in results:
        if r.frame_count > 0 and r.stall_count > 0:
            rate = f"1/{r.frame_count // r.stall_count}"
        elif r.frame_count > 0:
            rate = f"0/{r.frame_count}"
        else:
            rate = "N/A"
        print(f"  {r.label:<30s} {r.avg_fps:6.1f}  {r.stall_count:7d}  {r.severe_count:7d}  {rate:>14}  {r.max_gap_ms:7.0f}ms  {r.timeouts:5d}  {r.warmup_stalls:9d}")
    print(f"{'='*110}\n")


def find_model(name):
    root = os.path.dirname(_SCRIPT_DIR)
    for d in [os.path.join(root, "models"), _SCRIPT_DIR]:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return name


def main():
    parser = argparse.ArgumentParser(description="CUDA stream validation test")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--yolo-model", type=str, default="yolo26x-pose.pt")
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--warmup", type=float, default=3.0)
    parser.add_argument("--log", type=str, default="test_validation.log")
    parser.add_argument("--repeat", type=int, default=2,
                        help="Repeat each test N times for statistical confidence")
    args = parser.parse_args()

    global WARMUP_S
    WARMUP_S = args.warmup

    log_path = os.path.join(_SCRIPT_DIR, args.log)

    class Tee:
        def __init__(self, stream, log_file):
            self._s = stream; self._l = log_file
        def write(self, data):
            self._s.write(data); self._s.flush()
            self._l.write(data); self._l.flush()
        def flush(self):
            self._s.flush(); self._l.flush()

    _log_fh = open(log_path, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, _log_fh)
    sys.stderr = Tee(sys.__stderr__, _log_fh)

    print(f"Logging to: {log_path}")
    print(f"Duration: {args.duration}s per test, warmup: {WARMUP_S}s, repeats: {args.repeat}")
    print(f"YOLO model: {args.yolo_model} @ imgsz={args.imgsz}")
    print()

    cam = Camera(buffer_count=16, max_fps=20.0)
    if not cam.open():
        return 1

    pinned = torch.empty(cam.height, cam.width, dtype=torch.uint8).pin_memory()
    cuda_stream = torch.cuda.Stream()

    # YOLO model
    model_path = find_model(args.yolo_model)
    model = None
    if YOLO_AVAILABLE:
        import cv2
        print(f"Loading YOLO: {model_path}")
        model = YOLO(model_path)
        dummy = np.zeros((cam.height, cam.width, 3), dtype=np.uint8)
        for _ in range(3):
            model.predict(dummy, imgsz=args.imgsz, verbose=False)
        print("YOLO warmup done")

    all_results = []

    for rep in range(args.repeat):
        print(f"\n{'#'*70}")
        print(f"  REPEAT {rep+1}/{args.repeat}")
        print(f"{'#'*70}")

        # --- A: Baseline (no GPU) ---
        print(f"\n--- A: Baseline (no GPU) ---")
        time.sleep(2)
        r = run_test(cam, args.duration, f"A_baseline_r{rep+1}", "No GPU work")
        all_results.append(r)
        print(f"  {r.stall_count} stalls ({r.severe_count} severe), {r.avg_fps:.1f} fps, max_gap={r.max_gap_ms:.0f}ms")

        # --- B: Upload (default stream) ---
        print(f"\n--- B: Pinned upload (default stream) ---")
        time.sleep(2)
        def on_frame_b(mono):
            t = torch.from_numpy(mono)
            pinned.copy_(t)
            _ = pinned.cuda(non_blocking=True)
        r = run_test(cam, args.duration, f"B_upload_default_r{rep+1}", "Pinned upload, default CUDA stream", on_frame=on_frame_b)
        all_results.append(r)
        print(f"  {r.stall_count} stalls ({r.severe_count} severe), {r.avg_fps:.1f} fps, max_gap={r.max_gap_ms:.0f}ms")

        # --- C: Upload (dedicated CUDA stream) ---
        print(f"\n--- C: Pinned upload (dedicated CUDA stream) ---")
        time.sleep(2)
        def on_frame_c(mono):
            t = torch.from_numpy(mono)
            pinned.copy_(t)
            with torch.cuda.stream(cuda_stream):
                _ = pinned.cuda(non_blocking=True)
        r = run_test(cam, args.duration, f"C_upload_stream_r{rep+1}", "Pinned upload, dedicated CUDA stream", on_frame=on_frame_c)
        all_results.append(r)
        print(f"  {r.stall_count} stalls ({r.severe_count} severe), {r.avg_fps:.1f} fps, max_gap={r.max_gap_ms:.0f}ms")

        # --- D: YOLO + CUDA stream ---
        if model:
            print(f"\n--- D: YOLO + dedicated CUDA stream ---")
            time.sleep(2)
            preview_interval = 1.0 / 10.0
            last_preview = [0.0]
            def on_frame_d(mono):
                t = torch.from_numpy(mono)
                pinned.copy_(t)
                with torch.cuda.stream(cuda_stream):
                    gpu_t = pinned.cuda(non_blocking=True)
                bgr = cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)
                results = model.predict(bgr, imgsz=args.imgsz, verbose=False, device='cuda')
                if results and results[0].keypoints is not None:
                    _ = results[0].keypoints.data.cpu()
                now = time.perf_counter()
                if now - last_preview[0] >= preview_interval:
                    last_preview[0] = now
                    ft = gpu_t.float().unsqueeze(0).unsqueeze(0)
                    h, w = ft.shape[-2:]
                    nh, nw = max(1, int(h * 0.35)), max(1, int(w * 0.35))
                    res = F.interpolate(ft, size=(nh, nw), mode='area')
                    _ = res.clamp_(0, 255).byte().cpu().numpy()
            r = run_test(cam, args.duration, f"D_yolo_stream_r{rep+1}",
                        "YOLO + upload + preview, dedicated CUDA stream", on_frame=on_frame_d)
            all_results.append(r)
            print(f"  {r.stall_count} stalls ({r.severe_count} severe), {r.avg_fps:.1f} fps, max_gap={r.max_gap_ms:.0f}ms")

        # --- E: YOLO + default stream (control) ---
        if model:
            print(f"\n--- E: YOLO + default stream (control) ---")
            time.sleep(2)
            last_preview2 = [0.0]
            def on_frame_e(mono):
                t = torch.from_numpy(mono)
                pinned.copy_(t)
                gpu_t = pinned.cuda(non_blocking=True)
                bgr = cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)
                results = model.predict(bgr, imgsz=args.imgsz, verbose=False, device='cuda')
                if results and results[0].keypoints is not None:
                    _ = results[0].keypoints.data.cpu()
                now = time.perf_counter()
                if now - last_preview2[0] >= preview_interval:
                    last_preview2[0] = now
                    ft = gpu_t.float().unsqueeze(0).unsqueeze(0)
                    h, w = ft.shape[-2:]
                    nh, nw = max(1, int(h * 0.35)), max(1, int(w * 0.35))
                    res = F.interpolate(ft, size=(nh, nw), mode='area')
                    _ = res.clamp_(0, 255).byte().cpu().numpy()
            r = run_test(cam, args.duration, f"E_yolo_default_r{rep+1}",
                        "YOLO + upload + preview, default CUDA stream", on_frame=on_frame_e)
            all_results.append(r)
            print(f"  {r.stall_count} stalls ({r.severe_count} severe), {r.avg_fps:.1f} fps, max_gap={r.max_gap_ms:.0f}ms")

    # Summary
    print_table(all_results)

    # Stall details
    print("STALL DETAILS:")
    for r in all_results:
        if r.stalls:
            print(f"\n  {r.label}:")
            for s in r.stalls[:10]:
                sev = "SEVERE" if s.gap_s >= SEVERE_THRESHOLD_S else "stall"
                print(f"    frame {s.frame_idx:4d}: {s.gap_s*1000:.0f}ms [{sev}]")

    # Conclusions
    print("\nANALYSIS:")
    baseline_stalls = [r.stall_count for r in all_results if r.label.startswith("A_")]
    stream_stalls = [r.stall_count for r in all_results if r.label.startswith("C_")]
    default_stalls = [r.stall_count for r in all_results if r.label.startswith("B_")]
    yolo_stream = [r.stall_count for r in all_results if r.label.startswith("D_")]
    yolo_default = [r.stall_count for r in all_results if r.label.startswith("E_")]

    print(f"  Baseline (no GPU):       stalls = {baseline_stalls}")
    print(f"  Upload (default stream): stalls = {default_stalls}")
    print(f"  Upload (CUDA stream):    stalls = {stream_stalls}")
    if yolo_stream:
        print(f"  YOLO (CUDA stream):      stalls = {yolo_stream}")
    if yolo_default:
        print(f"  YOLO (default stream):   stalls = {yolo_default}")

    if sum(stream_stalls) <= sum(baseline_stalls) * 0.5:
        print("\n  >>> CUDA stream REDUCES stalls compared to baseline")
    elif sum(stream_stalls) >= sum(baseline_stalls) * 1.5:
        print("\n  >>> CUDA stream has NO benefit or makes things worse")
    else:
        print("\n  >>> CUDA stream effect is within noise of baseline")

    cam.close()
    del pinned, model
    gc.collect()
    torch.cuda.empty_cache()

    print(f"\nDone. Log: {log_path}")
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    _log_fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
