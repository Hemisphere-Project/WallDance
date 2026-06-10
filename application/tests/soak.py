"""Long-run soak harness over looped playback (TODO Phase 7, ops cluster).

Drives the full CPU (or GPU) FrameProcessor pipeline over a corpus recording
for hours, chunked so progress is visible and a stall is loud:

- one process, model loaded ONCE (repeated in-process YOLO loads leak - the
  Phase 1 lesson), video looped with a tracker reset per loop (mirroring the
  app's per-playback-loop reset);
- per chunk: wall time, fps, RSS, CUDA memory, tracker metrics
  (ghost/swap/zero-detection via the replay summary), one line appended to
  progress.jsonl and printed;
- a sentinel thread dumps all stacks if no frame advances for
  --stall-timeout seconds;
- SUMMARY.md at the end (also on Ctrl+C) with RSS slope (MB/h), fps trend,
  and a verdict line.

Monitor from another shell:
    Get-Content -Wait -Tail 3 <out>\\progress.jsonl

Smoke run (~10 min):  python tests/soak.py --scenario hangar-floor --minutes 10 --chunk-frames 300
Full soak (4 h):      python tests/soak.py --scenario hangar-floor --hours 4
"""
import argparse
import faulthandler
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import replay  # noqa: E402  (sets up src/ on sys.path)
import cv2  # noqa: E402


# --------------------------------------------------------------------------
# Resource probes (no hard dependency on psutil)
# --------------------------------------------------------------------------

def rss_bytes() -> int:
    """Resident set size of this process; 0 if unknown."""
    try:
        import psutil  # opportunistic - not a project dependency
        return psutil.Process().memory_info().rss
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes as wt

            class PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wt.DWORD),
                    ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            if ctypes.windll.psapi.GetProcessMemoryInfo(
                    ctypes.windll.kernel32.GetCurrentProcess(),
                    ctypes.byref(pmc), pmc.cb):
                return int(pmc.WorkingSetSize)
        except Exception:
            pass
        return 0
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return 0


def cuda_mem_mb():
    """(allocated_mb, reserved_mb); (0, 0) without CUDA."""
    try:
        import torch
        if torch.cuda.is_available():
            return (torch.cuda.memory_allocated() / 1e6,
                    torch.cuda.memory_reserved() / 1e6)
    except Exception:
        pass
    return 0.0, 0.0


def _empty_cuda_cache():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _slope_per_hour(points):
    """Least-squares slope of (elapsed_h, value) pairs; 0 with <2 points."""
    n = len(points)
    if n < 2:
        return 0.0
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    return (n * sxy - sx * sy) / denom if denom else 0.0


# --------------------------------------------------------------------------
# Stall sentinel
# --------------------------------------------------------------------------

class StallSentinel:
    """Daemon thread: if no frame advances for timeout_s, dump all stacks."""

    def __init__(self, timeout_s: float, on_stall=None):
        self.timeout_s = timeout_s
        self.on_stall = on_stall
        self._hb_time = time.monotonic()
        self._hb_frame = -1
        self._last_fired_frame = -1
        self.stall_events = 0
        self._stop = threading.Event()
        self._thread = None

    def beat(self, frame_no: int):
        self._hb_time = time.monotonic()
        self._hb_frame = frame_no

    def _run(self):
        while not self._stop.wait(5.0):
            age = time.monotonic() - self._hb_time
            if age >= self.timeout_s and self._hb_frame != self._last_fired_frame:
                self._last_fired_frame = self._hb_frame
                self.stall_events += 1
                print(f"[Soak][STALL] no frame for {age:.0f}s "
                      f"(stuck after frame {self._hb_frame}) - thread stacks follow",
                      flush=True)
                try:
                    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
                except Exception:
                    pass
                if self.on_stall:
                    self.on_stall(age)

    def start(self):
        self._thread = threading.Thread(target=self._run, name="SoakSentinel",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


# --------------------------------------------------------------------------
# Soak runner
# --------------------------------------------------------------------------

def run_soak(args) -> dict:
    manifest_path = _HERE / "scenarios" / f"{args.scenario}.json"
    manifest = json.loads(manifest_path.read_text())
    config = replay.scenario_config(manifest)
    video = replay._find_recording(manifest["project"], manifest["slot"])
    if video is None:
        raise SystemExit(f"no recording for {manifest['project']} "
                         f"slot {manifest['slot']}")
    replay.check_fingerprint(manifest, video)

    model_name = args.model or config.get("model", "yolo11x-pose")
    imgsz = int(args.imgsz or config.get("yolo_imgsz", 1280))
    target_s = (args.hours or 0.0) * 3600.0 + (args.minutes or 0.0) * 60.0
    if target_s <= 0 and not args.max_chunks:
        raise SystemExit("give a duration (--hours/--minutes) or --max-chunks")

    out_dir = Path(args.out) if args.out else (
        replay.REPO / "tmp_analysis" /
        f"soak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    chunks_dir = out_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    progress_path = out_dir / "progress.jsonl"

    print(f"[Soak] scenario={args.scenario} video={video.name} "
          f"model={model_name} imgsz={imgsz} path={'gpu' if args.gpu else 'cpu'}")
    print(f"[Soak] out={out_dir}")
    print(f"[Soak] target={'%.2f h' % (target_s / 3600) if target_s else ''}"
          f"{' max_chunks=%d' % args.max_chunks if args.max_chunks else ''} "
          f"chunk={args.chunk_frames} frames")

    proc = replay._build_processor(config, model_name, imgsz,
                                   use_gpu_path=args.gpu)
    if args.gpu and not proc.gpu_path_active:
        raise SystemExit("GPU path requested but unavailable")
    proc.tracker.reset()

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open video: {video}")
    window_start = args.start or 0
    if window_start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, window_start)

    sentinel = StallSentinel(args.stall_timeout)
    sentinel.start()

    chunks = []
    start_mono = time.monotonic()
    started_at = datetime.now().isoformat(timespec="seconds")
    total_frames = 0
    loops = 0
    window_frames = 0  # frames consumed inside the current window pass
    interrupted = False

    def elapsed_s():
        return time.monotonic() - start_mono

    try:
        chunk_idx = 0
        while True:
            if target_s and elapsed_s() >= target_s:
                break
            if args.max_chunks and chunk_idx >= args.max_chunks:
                break
            chunk_dir = chunks_dir / f"chunk_{chunk_idx:04d}"
            proc.tracker.logger.start_session(str(chunk_dir))
            chunk_t0 = time.monotonic()
            n = 0
            while n < args.chunk_frames:
                if args.frames and window_frames >= args.frames:
                    ok, frame = False, None  # window exhausted -> loop
                else:
                    ok, frame = cap.read()
                if not ok:
                    # End of file / window: loop like the app's playback loop
                    # (seek to the window start + tracker reset per loop).
                    cap.set(cv2.CAP_PROP_POS_FRAMES, window_start)
                    proc.tracker.reset()
                    loops += 1
                    window_frames = 0
                    continue
                proc.process(frame, need_preview=False, frame_number=total_frames)
                n += 1
                total_frames += 1
                window_frames += 1
                sentinel.beat(total_frames)
            proc.tracker.logger.close()
            wall = time.monotonic() - chunk_t0
            summary = replay._summary_from_log(
                str(chunk_dir), video.name, model_name, imgsz, 0, n, [])
            summary.pop("per_frame", None)
            _empty_cuda_cache()
            alloc_mb, resv_mb = cuda_mem_mb()
            rec = {
                "chunk": chunk_idx,
                "frames": n,
                "wall_s": round(wall, 1),
                "fps": round(n / wall, 2) if wall > 0 else 0.0,
                "rss_mb": round(rss_bytes() / 1e6, 1),
                "cuda_alloc_mb": round(alloc_mb, 1),
                "cuda_resv_mb": round(resv_mb, 1),
                "ghost_tracks": summary["ghost_tracks"],
                "swap_count": summary["swap_count"],
                "zero_detection_frames": summary["zero_detection_frames"],
                "loops": loops,
                "elapsed_h": round(elapsed_s() / 3600, 3),
            }
            chunks.append(rec)
            with open(progress_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            eta_h = 0.0
            if target_s:
                eta_h = max(0.0, (target_s - elapsed_s()) / 3600)
            print(f"[Soak] chunk {chunk_idx} fps={rec['fps']} "
                  f"rss={rec['rss_mb']:.0f}MB "
                  f"cuda={rec['cuda_alloc_mb']:.0f}/{rec['cuda_resv_mb']:.0f}MB "
                  f"ghosts={rec['ghost_tracks']} swaps={rec['swap_count']} "
                  f"elapsed={rec['elapsed_h']:.2f}h eta={eta_h:.2f}h",
                  flush=True)
            # A chunk that took >3x the median of its predecessors is news.
            if len(chunks) >= 4:
                med = sorted(c["wall_s"] for c in chunks[:-1])[len(chunks[:-1]) // 2]
                if wall > 3 * med:
                    print(f"[Soak][SLOW] chunk {chunk_idx} took {wall:.0f}s "
                          f"(median {med:.0f}s)", flush=True)
            chunk_idx += 1
    except KeyboardInterrupt:
        interrupted = True
        print("[Soak] interrupted - writing partial summary", flush=True)
    finally:
        sentinel.stop()
        cap.release()
        try:
            proc.tracker.logger.close()
        except Exception:
            pass
        result = _write_summary(
            out_dir, args, manifest, video, model_name, imgsz, chunks,
            loops, total_frames, elapsed_s(), started_at, interrupted,
            sentinel.stall_events)
    return result


def _write_summary(out_dir, args, manifest, video, model_name, imgsz, chunks,
                   loops, total_frames, elapsed, started_at, interrupted,
                   stall_events) -> dict:
    fps_vals = [c["fps"] for c in chunks]
    rss_pts = [(c["elapsed_h"], c["rss_mb"]) for c in chunks]
    resv_pts = [(c["elapsed_h"], c["cuda_resv_mb"]) for c in chunks]
    rss_slope = _slope_per_hour(rss_pts)
    resv_slope = _slope_per_hour(resv_pts)

    fps_degraded = False
    if len(fps_vals) >= 8:
        q = max(1, len(fps_vals) // 4)
        first_q = sum(fps_vals[:q]) / q
        last_q = sum(fps_vals[-q:]) / q
        fps_degraded = last_q < 0.85 * first_q

    leak_suspect = rss_slope > 50.0 and elapsed > 1800
    verdict = "PASS"
    notes = []
    if interrupted:
        notes.append("interrupted before target")
    if stall_events:
        verdict = "FAIL"
        notes.append(f"{stall_events} stall event(s)")
    if leak_suspect:
        verdict = "FAIL"
        notes.append(f"RSS slope {rss_slope:.0f} MB/h")
    if fps_degraded:
        verdict = "FAIL"
        notes.append("fps degraded >15% first->last quartile")

    result = {
        "verdict": verdict,
        "interrupted": interrupted,
        "elapsed_h": round(elapsed / 3600, 3),
        "chunks": len(chunks),
        "frames": total_frames,
        "loops": loops,
        "stall_events": stall_events,
        "rss_slope_mb_h": round(rss_slope, 1),
        "cuda_resv_slope_mb_h": round(resv_slope, 1),
        "fps_mean": round(sum(fps_vals) / len(fps_vals), 2) if fps_vals else 0,
        "fps_min": min(fps_vals) if fps_vals else 0,
    }

    try:
        import torch
        env = (f"python {sys.version.split()[0]}, torch {torch.__version__}, "
               f"cuda={'yes' if torch.cuda.is_available() else 'no'}")
    except Exception:
        env = f"python {sys.version.split()[0]}"

    lines = [
        f"# Soak run - {args.scenario}",
        "",
        f"- started: {started_at}  |  elapsed: {result['elapsed_h']:.2f} h"
        f"  |  verdict: **{verdict}**" + (f"  ({'; '.join(notes)})" if notes else ""),
        f"- env: {env}",
        f"- scenario: {args.scenario} (project={manifest['project']}, "
        f"slot={manifest['slot']}, video={video.name})",
        f"- model: {model_name} @ imgsz {imgsz}, "
        f"path: {'gpu' if args.gpu else 'cpu'}, "
        f"chunk: {args.chunk_frames} frames",
        f"- totals: {total_frames} frames, {len(chunks)} chunks, "
        f"{loops} playback loops, {stall_events} stalls",
        f"- RSS: {chunks[0]['rss_mb']:.0f} -> {chunks[-1]['rss_mb']:.0f} MB "
        f"(max {max(c['rss_mb'] for c in chunks):.0f}), "
        f"slope {rss_slope:+.1f} MB/h" if chunks else "- RSS: n/a",
        f"- CUDA reserved: {chunks[0]['cuda_resv_mb']:.0f} -> "
        f"{chunks[-1]['cuda_resv_mb']:.0f} MB (slope {resv_slope:+.1f} MB/h)"
        if chunks else "- CUDA: n/a",
        f"- fps: mean {result['fps_mean']}, min {result['fps_min']}"
        + (", degraded first->last quartile" if fps_degraded else ""),
        "",
        "| chunk | frames | wall s | fps | RSS MB | CUDA a/r MB | ghosts | swaps | zero-det |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in chunks:
        lines.append(
            f"| {c['chunk']} | {c['frames']} | {c['wall_s']} | {c['fps']} "
            f"| {c['rss_mb']:.0f} | {c['cuda_alloc_mb']:.0f}/{c['cuda_resv_mb']:.0f} "
            f"| {c['ghost_tracks']} | {c['swap_count']} "
            f"| {c['zero_detection_frames']} |")
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[Soak] {verdict} - summary: {out_dir / 'SUMMARY.md'}", flush=True)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Chunked long-run soak over looped playback")
    ap.add_argument("--scenario", default="hangar-floor",
                    help="scenario manifest name (tests/scenarios/<name>.json)")
    ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--max-chunks", type=int, default=0)
    ap.add_argument("--chunk-frames", type=int, default=2000)
    ap.add_argument("--model", default=None,
                    help="override the manifest's pinned model")
    ap.add_argument("--imgsz", type=int, default=0,
                    help="override the manifest's pinned imgsz")
    ap.add_argument("--gpu", action="store_true", help="use the GPU path")
    ap.add_argument("--start", type=int, default=0,
                    help="window start frame (default: whole file)")
    ap.add_argument("--frames", type=int, default=0,
                    help="window length in frames (default: whole file)")
    ap.add_argument("--stall-timeout", type=float, default=120.0)
    ap.add_argument("--out", default=None,
                    help="output dir (default tmp_analysis/soak_<timestamp>)")
    args = ap.parse_args(argv)
    result = run_soak(args)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
