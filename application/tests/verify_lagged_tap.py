"""Lagged-tap trajectory verification on real footage (Track X, X-2 checkpoint).

Runs the REAL FrameProcessor (TRT GPU show path) on a scenario with the lagged
tap ENABLED, capturing BOTH OSC streams via a fake OSC client:

  * causal  /walldance/dancer/centroid          (EMA-smoothed, zero look-ahead)
  * lagged  /walldance/dancer_lagged/centroid    (RTS-smoothed, L frames late)

Then it asserts the two checkpoint properties on real data:
  1. frame delay  causal→lagged  == L   (per-track cross-correlation peak)
  2. lagged centroid is SMOOTHER than causal (lower jitter / acceleration RMS)

and, as the output-only A/B, rebuilds the tracker-internal metric summary with
the lagged tap ON and compares it to the committed golden (the lagged tap must
not perturb the tracker → byte-identical metrics).

Not a pytest test (needs TRT + GPU + the recording, like replay.py).  Run:
  .venv/Scripts/python.exe tests/verify_lagged_tap.py \
        --scenario tests/scenarios/hangar-aerial.json --L 4
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
for p in (str(_SRC), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import cv2  # noqa: E402

import replay  # tests/replay.py — reuse its processor build + config plumbing  # noqa: E402
import scoring  # noqa: E402
from core.osc_output import OSCSender  # noqa: E402


GOLDEN_DIR = _HERE / "golden"
_LEAN_KEYS = ("frames_processed", "real_tracks", "marginal_tracks",
              "ghost_tracks", "total_tracks", "swap_count", "gate_rejections",
              "zero_detection_frames", "avg_detections")


class _RecordingClient:
    """Fake pythonosc client: records (frame, address, args) instead of sending."""

    def __init__(self):
        self.frame = 0
        self.msgs = []

    def send_message(self, address, args):
        self.msgs.append((self.frame, address, list(args)))


def _capturing_sender(rec):
    osc = OSCSender.__new__(OSCSender)  # bypass __init__ (no real UDP socket)
    osc.enabled = True
    osc.ip, osc.port = "capture", 0
    osc.client = rec
    return osc


def _series_by_id(rec, address):
    """address -> {track_id: {frame: (x, y)}} for the matching centroid stream."""
    out = {}
    for frame, addr, args in rec.msgs:
        if addr != address or len(args) < 3:
            continue
        tid = int(args[0])
        out.setdefault(tid, {})[frame] = (float(args[1]), float(args[2]))
    return out


def _best_delay(lagged, causal, max_d):
    """d>=0 maximizing corr(lagged[N], causal[N-d]) over common frames + axis."""
    frames = sorted(set(lagged) & set(causal))
    if len(frames) < 12:
        return None
    best_d, best_c = None, -np.inf
    for axis in (0, 1):
        lg = np.array([lagged[f][axis] for f in frames])
        cz = {f: causal[f][axis] for f in causal}
        if lg.std() < 1e-9:
            continue
        for d in range(max_d + 1):
            a, b = [], []
            for i, f in enumerate(frames):
                if (f - d) in cz:
                    a.append(lagged[f][axis])
                    b.append(cz[f - d])
            if len(a) < 12:
                continue
            a, b = np.array(a), np.array(b)
            if a.std() < 1e-9 or b.std() < 1e-9:
                continue
            c = float(np.corrcoef(a, b)[0, 1])
            if c > best_c:
                best_c, best_d = c, d
    return best_d


def _jitter(series):
    """RMS of the centroid's discrete 2nd difference (acceleration) — a
    look-ahead-agnostic smoothness metric.  Lower = smoother."""
    frames = sorted(series)
    if len(frames) < 5:
        return None
    xy = np.array([series[f] for f in frames])
    acc = np.diff(xy, n=2, axis=0)
    return float(np.sqrt(np.mean(np.sum(acc ** 2, axis=1))))


def run(scenario_path, L, frames=None, use_trt=False):
    scenario = scoring.load_scenario(scenario_path)
    config = replay.scenario_config(scenario)
    video = replay._find_recording(scenario["project"], scenario["slot"])
    if not video:
        raise SystemExit(f"no recording for {scenario['project']} slot {scenario['slot']}")
    model_name = config.get("model", "yolo11x-pose")
    imgsz = int(config.get("yolo_imgsz", 1280))
    start = int(scenario.get("start", 0))
    n_frames = frames if frames is not None else scenario.get("frames")

    # CPU FP32 by default: deterministic + matches the committed (non-TRT)
    # goldens, so the output-only A/B is exact.  --trt is the show-faithful path
    # but is FP16/non-deterministic, so its A/B vs the CPU golden is skipped.
    proc = replay._build_processor(config, model_name, imgsz, use_trt=use_trt)
    if use_trt and not proc.gpu_path_active:
        raise SystemExit("TRT/GPU path unavailable")
    proc.tracker.reset()
    proc.settings.output_smoothing_l = int(L)
    proc.settings.output_lagged_enabled = True
    proc.settings.osc_enabled = True
    rec = _RecordingClient()
    proc.attach_osc(_capturing_sender(rec))

    tmp = tempfile.mkdtemp(prefix="wd_lagged_")
    proc.tracker.logger.start_session(tmp)
    cap = cv2.VideoCapture(video)
    if start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    processed = 0
    raw = {}  # RAW centroid (centroid_raw, pixel space) from the process() return
    try:
        while n_frames is None or processed < n_frames:
            ok, frame = cap.read()
            if not ok:
                break
            rec.frame = processed
            tracks, _enh, _timing, _lat = proc.process(
                frame, need_preview=False, frame_number=processed)
            for st in tracks:
                if st.centroid_raw is not None:
                    raw.setdefault(int(st.track_id), {})[processed] = (
                        float(st.centroid_raw[0]), float(st.centroid_raw[1]))
            processed += 1
    finally:
        cap.release()
        proc.tracker.logger.close()

    summary = replay._summary_from_log(
        tmp, Path(video).name, model_name, imgsz, start, processed, [])
    return rec, summary, processed, raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--L", type=int, default=4)
    ap.add_argument("--frames", type=int, default=None)
    ap.add_argument("--trt", action="store_true",
                    help="show-faithful TRT FP16 path (non-deterministic → "
                         "the CPU-golden A/B is skipped)")
    ap.add_argument("--golden", default=None,
                    help="golden JSON to A/B the internal summary against "
                         "(default: golden/<scenario name>.json)")
    args = ap.parse_args()

    rec, summary, processed, raw = run(args.scenario, args.L, args.frames,
                                       use_trt=args.trt)
    causal = _series_by_id(rec, "/walldance/dancer/centroid")
    lagged = _series_by_id(rec, "/walldance/dancer_lagged/centroid")

    print(f"\n=== Lagged-tap verification "
          f"({'TRT' if args.trt else 'CPU'}, L={args.L}, {processed} frames) ===")
    print(f"causal tracks: {sorted(causal)}   lagged tracks: {sorted(lagged)}")
    print("(meta/latency_ms is published by the runtime loop, not this harness)")

    # Per-track delay + smoothness on the longest-lived shared tracks.  The
    # frame delay is measured against the RAW centroid (lagged[N] ≈ raw[N-L]);
    # measuring against the causal *EMA* tap would read L minus the EMA's own
    # ~1-frame group delay.
    shared = sorted(set(causal) & set(lagged) & set(raw),
                    key=lambda t: -len(set(lagged[t]) & set(raw[t])))
    delays, smoother = [], []
    for tid in shared:
        common = len(set(lagged[tid]) & set(raw[tid]))
        if common < 2 * args.L + 10:
            continue
        d_raw = _best_delay(lagged[tid], raw[tid], max_d=args.L + 6)
        d_caus = _best_delay(lagged[tid], causal[tid], max_d=args.L + 6)
        jc, jl = _jitter(causal[tid]), _jitter(lagged[tid])
        delays.append(d_raw)
        better = jl is not None and jc is not None and jl < jc
        smoother.append(better)
        ratio = (jl / jc) if (jc and jl is not None) else float("nan")
        print(f"  track {tid}: frames~{common}  delay vs raw={d_raw} "
              f"(expect {args.L}; vs causal-EMA={d_caus})  "
              f"jitter causal={jc:.4f} lagged={jl:.4f}  "
              f"{'SMOOTHER' if better else 'NOT smoother'} ({ratio:.2f}x)")

    # Output-only A/B: internal summary must match the golden (CPU path only —
    # the golden is FP32 CPU; TRT FP16 is non-deterministic).
    gpath = Path(args.golden) if args.golden else \
        GOLDEN_DIR / (Path(args.scenario).stem + ".json")
    ab_ok = None
    if args.trt:
        print("\noutput-only A/B: skipped (TRT FP16 ≠ CPU golden; run without --trt)")
    elif gpath.exists():
        golden = json.loads(gpath.read_text())
        diffs = {k: (golden.get(k), summary.get(k))
                 for k in _LEAN_KEYS if golden.get(k) != summary.get(k)}
        ab_ok = not diffs
        print(f"\noutput-only A/B vs {gpath.name} (lagged tap ON): "
              f"{'IDENTICAL' if ab_ok else 'DIFFERS ' + json.dumps(diffs)}")
    else:
        print(f"\n(no golden at {gpath}; skipping A/B)")

    ok_delay = bool(delays) and all(d == args.L for d in delays)
    ok_smooth = bool(smoother) and all(smoother)
    print("\nVERDICT:")
    print(f"  frame delay == L : {'PASS' if ok_delay else 'FAIL'}  ({delays})")
    print(f"  lagged smoother  : {'PASS' if ok_smooth else 'FAIL'}")
    if ab_ok is not None:
        print(f"  output-only A/B  : {'PASS' if ab_ok else 'FAIL'}")
    passed = ok_delay and ok_smooth and (ab_ok is not False)
    print(f"\n{'ALL CHECKS PASS' if passed else 'CHECK FAILED'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
