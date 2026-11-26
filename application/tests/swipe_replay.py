"""Headless settings swipe on a recorded take (Plan Cordée P5).

Plays a take through the real ``FrameProcessor`` on the GPU/TensorRT show path, at the setting the take was
recorded with (its ``.meta`` config; ``--config latest`` = the project's newest saved config) and the
empty-wall plate the app had loaded then, feeding the swipe's collector exactly as the app does (the tap in
``FrameProcessor``), until ~30 frames where the live setting leaves a dancer without a box are kept.  Then it
runs the same candidate runner and ``core.swipe.decide`` the app runs, prints the table and writes JSON.

    python tests/swipe_replay.py --project jeudi0809_mur2 --slot 8 --engine-dir models --out swipe.json

``--concurrent`` runs it the way the app does instead: the take keeps playing in real time (``--fps``) on the
main thread while the ``SwipeController`` works on its worker thread, and the live frame time is reported
per phase (``--pace-ms 0`` = candidate inferences back to back, for comparison).

Runs on Windows too (cmd.exe, cp1252 console: ASCII output only).  On Linux it puts the venv's
``nvidia/*/lib`` first on ``LD_LIBRARY_PATH`` itself (re-exec once, before torch loads) -- it does not rely on
``replay.py`` for that; it reuses replay.py's processor builder only after that setup, when importing it
re-execs nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def _bootstrap_gpu_env() -> None:
    """Linux: the venv's CUDA/cuDNN libs before the system ones (run.sh does the same), via one re-exec."""
    os.environ.setdefault("YOLO_AUTOINSTALL", "0")      # never pip-install anything mid-run
    if not sys.platform.startswith("linux") or os.environ.get("_WD_LD_BOOTSTRAPPED"):
        return
    os.environ["_WD_LD_BOOTSTRAPPED"] = "1"              # also tells replay.py not to re-exec at import
    libs = sorted(Path(sys.prefix).glob("lib/python*/site-packages/nvidia/*/lib"))
    cur = os.environ.get("LD_LIBRARY_PATH", "")
    if not libs or all(str(p) in cur.split(os.pathsep) for p in libs):
        return
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(str(p) for p in libs) + (os.pathsep + cur if cur else "")
    os.execv(sys.executable, [sys.executable] + sys.argv)


_bootstrap_gpu_env()

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent / "src", _HERE.parent, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np  # noqa: E402

import replay as R  # noqa: E402  (no re-exec: the environment is already set up above)
from core.input_shape import parse_shape  # noqa: E402
from core.swipe import Candidate, SwipeParams  # noqa: E402
from core.swipe_runner import (CandidateRunner, ExclusionCells, SwipeInput,  # noqa: E402
                               install_capture_guards, parse_candidate, plan_candidates, run_swipe)


def say(text: str = "") -> None:
    """ASCII only (cp1252 consoles)."""
    print(str(text).replace("×", "x").encode("ascii", "replace").decode("ascii"), flush=True)


def plate_for(project_dir: Path, started_at: str):
    """The plate the app had loaded when the take started: the newest plates/plate_<stamp>.npz before it
    (the app loads plates/latest.npz = the last one captured)."""
    stamp = started_at.replace("-", "").replace(":", "").replace("T", "_")[:15]
    best = None
    for p in sorted((project_dir / "plates").glob("plate_*.npz")):
        if p.stem[6:] <= stamp:
            best = p
    return best


def pct(values, q):
    v = sorted(values)
    if not v:
        return None
    return float(v[min(len(v) - 1, int(round(q / 100.0 * (len(v) - 1))))])


def load_setup(args):
    projects = Path(args.projects_dir) if args.projects_dir else R.PROJECTS_DIR
    pdir = projects / args.project
    if args.video:
        video = Path(args.video)
    else:
        recs = sorted(list((pdir / "recordings").glob(f"slot_{args.slot}_*.avi"))
                      + list((pdir / "recordings").glob(f"slot_{args.slot}_*.mp4")))
        plain = [r for r in recs if len(r.stem.split("_")) == 4]      # slot_<n>_<date>_<time>, not copies
        recs = plain or recs
        if not recs:
            sys.exit(f"no recording for {args.project} slot {args.slot}")
        video = recs[-1]
    meta = {}
    mpath = Path(str(video) + ".meta")
    if mpath.exists():
        meta = json.loads(mpath.read_text())
    if args.config == "meta" and meta.get("config"):
        import core.config_schema as config_schema
        config = config_schema.flatten(json.loads(json.dumps(meta["config"])))
        source = f"the take's .meta ({mpath.name})"
    else:
        cfgs = sorted((f for f in pdir.glob("*.json") if not f.name.startswith("_")),
                      key=lambda f: f.stat().st_mtime, reverse=True)
        if not cfgs:
            sys.exit(f"no saved config for {args.project}")
        import core.config_schema as config_schema
        config = config_schema.flatten(json.loads(cfgs[0].read_text()))
        source = f"the project's newest config ({cfgs[0].name})"
    R.apply_overrides(config, args.sets)
    plate = Path(args.plate) if args.plate else None
    if plate is None and config.get("fg_plate"):
        plate = pdir / str(config["fg_plate"])
    if plate is None and meta.get("started_at"):
        plate = plate_for(pdir, meta["started_at"])
    if plate is None and (pdir / "plates" / "latest.npz").exists():
        plate = pdir / "plates" / "latest.npz"
    if plate is not None:
        config["fg_plate_path"] = str(plate)
    config, _filled = R.fill_detection_defaults(config)
    return video, meta, config, source, plate


def build(args, config, video):
    model, imgsz = config["model"], int(config["yolo_imgsz"])
    proc = R._build_processor(config, model, imgsz, use_gpu_path=True, use_trt=True)
    if not proc.gpu_path_active:
        sys.exit("GPU path not active")
    R.install_camera_state(proc, video)
    proc.tracker.reset()
    proc.settings.swipe_collect = True
    if args.frames:
        proc.swipe_params = SwipeParams(frames=int(args.frames))
    clock = {"t": 0.0}
    proc.set_output_clock(lambda: clock["t"])
    logdir = tempfile.mkdtemp(prefix="wd_swipe_")
    proc.tracker.logger.start_session(logdir)
    current = Candidate(model, imgsz, tuple(proc.settings.yolo_shape) if proc.settings.yolo_shape else None)
    return proc, clock, current


def engine_path_fn():
    d = R.engine_dir()
    return lambda c: str(d / c.engine_file)


def plan_for(args, proc, current, images):
    inp = SwipeInput.from_processor(proc)
    fh, fw = images[0].shape[:2]
    _x, _y, roi_w, roi_h = inp.roi(fw, fh)
    net_px = None
    if proc.dancer_height and proc.last_lb_scale:
        net_px = float(proc.dancer_height[0]) * float(proc.last_lb_scale)
    only = None
    if args.candidates:
        only = [parse_candidate(t, current.imgsz) for t in args.candidates.split(",") if t.strip()]
    ep = engine_path_fn()
    plan = plan_candidates(current, roi_w, roi_h, lambda c: os.path.exists(ep(c)),
                           dancer_net_px=net_px, only=only, all_sizes=args.all_sizes)
    return inp, plan, (roi_w, roi_h), net_px


def print_table(outcome, args):
    d = outcome.to_dict()
    say("")
    say(f"current setting : {d['current']}")
    say(f"kept frames     : {d['frames']} ({d['lost_frames']} with a dancer bare at the live setting)")
    say(f"verdict         : {d.get('reason', '-')}")
    if d.get("live_error"):
        say(f"no verdict      : the live setting could not be re-run: {d['live_error']}")
    say(f"winner          : {d.get('winner')}  switch={d.get('switch')}")
    say("")
    say(f"{'setting':<18}{'coverage':>9}{'loose/fr':>10}{'ms p50':>8}{'load s':>8}{'frames':>8}  note")
    for r in d["rows"]:
        cov = r.get("coverage")
        loose = r.get("loose_per_frame")
        ms = r.get("ms_p50")
        load = r.get("load_s")
        say(f"{r['label']:<18}{(f'{100 * cov:.1f} %' if cov is not None else '-'):>9}"
            f"{(f'{loose:.3f}' if loose is not None else '-'):>10}"
            f"{(f'{ms:.1f}' if ms is not None else '-'):>8}"
            f"{(f'{load:.2f}' if load is not None else '-'):>8}"
            f"{r.get('frames_run', 0):>8}  {r.get('note', '') if not r.get('ok', True) else ''}")
    for s in d["skipped"]:
        say(f"  {s['text']}")
    return d


# ------------------------------------------------------------------------------------------------------------
def run_sequential(args):
    import cv2
    video, meta, config, source, plate = load_setup(args)
    proc, clock, current = build(args, config, video)
    xf = R.input_transform_for(config)
    say(f"[swipe] take {video.name}: config from {source}; live {current.label} "
        f"conf {config.get('confidence')}; plate {plate.name if plate else 'NONE'}; "
        f"engines {R.engine_dir()}")
    fps = float(args.fps)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        sys.exit(f"cannot open {video}")
    start = int(round(args.start_s * fps))
    if start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    max_frames = int(round(args.max_s * fps)) if args.max_s else None
    col = proc.swipe_collector
    taps_idle, taps_kept, taps_full, proc_ms = [], [], [], []
    t_wall0 = time.perf_counter()
    played = 0
    fg_valid = 0
    ready_at = None                       # frames played when the lost pool filled
    play_wall = None
    while max_frames is None or played < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        frame = xf.apply(frame)
        clock["t"] = (start + played) / fps
        last_before = proc._swipe_last_t
        t0 = time.perf_counter()
        _tracks, _enh, timing, _lat = proc.process(frame, need_preview=False, frame_number=played)
        proc_ms.append((time.perf_counter() - t0) * 1000.0)
        fg_valid += bool(proc.last_fg is not None and proc.last_fg.valid)
        kept = proc._swipe_last_t != last_before
        if "swipe_tap" in timing:
            (taps_kept if kept else taps_full if ready_at is not None else taps_idle).append(
                float(timing["swipe_tap"]))
        played += 1
        if ready_at is None and col.ready:
            ready_at = played
            play_wall = time.perf_counter() - t_wall0
        if ready_at is not None and played - ready_at >= int(round(args.after_ready_s * fps)):
            break
    cap.release()
    if play_wall is None:
        play_wall = time.perf_counter() - t_wall0
    frames, lost, _prog, ready = proc.swipe_snapshot()
    played_total = played
    played = ready_at if ready_at is not None else played
    say(f"[swipe] played {played} frames until ready ({played / fps:.1f} s of take, {play_wall:.1f} s wall; "
        f"{played_total} in all), "
        f"foreground valid on {fg_valid}; kept {len(frames)} frames, {lost} lost, ready={ready}")
    if not frames:
        sys.exit("no frame kept: no valid foreground with a dancer")
    images = [f.image for f in frames]
    observations = [f.obs for f in frames]
    abs_frames = [int(round(f.t * fps)) for f in frames]
    inp, plan, (roi_w, roi_h), net_px = plan_for(args, proc, current, images)
    say(f"[swipe] ROI {roi_w}x{roi_h}; dancer at the network "
        + (f"~{net_px:.0f} px" if net_px else "not measured") + f"; {len(plan.candidates)} candidates")
    for t in plan.skipped_text():
        say(f"  {t}")
    install_capture_guards()
    fh, fw = images[0].shape[:2]
    exclusion = ExclusionCells.from_processor(proc, inp.roi(fw, fh))
    if exclusion is not None:
        say(f"[swipe] the operator's excluded cells: {len(exclusion.cells)} on a {exclusion.gx}x{exclusion.gy} grid")
    runner = CandidateRunner(inp, bg_subtractor=proc.bg_subtractor)
    params = col.params
    outcome = run_swipe(images, observations, plan, current, runner, engine_path_fn(), params,
                        progress=lambda text, f: say(f"[swipe] {text}"), lost_frames=lost,
                        lost=[bool(f.lost) for f in frames], lost_share=proc.swipe_lost_share(),
                        exclusion=exclusion)
    take_s = played / fps
    outcome.timing["collect_s"] = take_s
    d = print_table(outcome, args)
    tm = outcome.timing
    split = {"take_s_until_ready": round(take_s, 2), "play_wall_s": round(play_wall, 2),
             "load_s": round(tm["load_s"], 2), "infer_s": round(tm["infer_s"], 2),
             "prep_s": round(tm["prep_s"], 2), "release_s": round(tm["release_s"], 2),
             "swipe_s": round(tm["run_s"], 2), "total_s": round(take_s + tm["run_s"], 2)}
    say("")
    say(f"wall-clock split: {split['take_s_until_ready']} s of take until ready; swipe {split['swipe_s']} s = "
        f"load {split['load_s']} + inference {split['infer_s']} + prep {split['prep_s']} + release "
        f"{split['release_s']} (+ scoring); total {split['total_s']} s")
    tap = {"frames_not_kept": len(taps_idle), "frames_kept": len(taps_kept),
           "frames_pool_full_not_kept": len(taps_full),
           "not_kept_ms_p50": pct(taps_idle, 50), "not_kept_ms_p99": pct(taps_idle, 99),
           "not_kept_ms_max": max(taps_idle) if taps_idle else None,
           "pool_full_ms_p50": pct(taps_full, 50), "pool_full_ms_p99": pct(taps_full, 99),
           "pool_full_ms_max": max(taps_full) if taps_full else None,
           "kept_ms_p50": pct(taps_kept, 50), "kept_ms_max": max(taps_kept) if taps_kept else None,
           "process_ms_p50": pct(proc_ms, 50)}

    def _ms(v, f=".4f"):
        return format(v, f) if v is not None else "-"
    say(f"tap cost: not kept p50 {_ms(tap['not_kept_ms_p50'])} ms, p99 {_ms(tap['not_kept_ms_p99'])}, max "
        f"{_ms(tap['not_kept_ms_max'], '.3f')} ({tap['frames_not_kept']} frames); pool full, not kept p50 "
        f"{_ms(tap['pool_full_ms_p50'])} ms, p99 {_ms(tap['pool_full_ms_p99'])}, max "
        f"{_ms(tap['pool_full_ms_max'], '.3f')} ({tap['frames_pool_full_not_kept']} frames); kept p50 "
        f"{_ms(tap['kept_ms_p50'], '.2f')} ms, max {_ms(tap['kept_ms_max'], '.2f')} ({tap['frames_kept']} frames); "
        f"the whole frame p50 {tap['process_ms_p50']:.1f} ms")
    out = {"take": str(video), "config_source": source, "plate": str(plate) if plate else None,
           "engine_dir": str(R.engine_dir()), "fps": fps, "start_s": args.start_s,
           "live": current.label, "confidence": config.get("confidence"),
           "kept_abs_frames": abs_frames, "swipe": d, "split": split, "tap": tap}
    if args.check_night:
        out["night_check"] = check_night(args, outcome, abs_frames, current)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, indent=1))
        say(f"wrote {args.out}")
    return out


def check_night(args, outcome, abs_frames, current):
    """One candidate's boxes against a plain replay at that setting (the night's JSONL, ``low`` boxes at conf
    >= 0.02 in original px): matched by IoU, the largest coordinate / confidence differences."""
    cand = parse_candidate(args.check_candidate, current.imgsz)
    run = outcome.run_for(cand)
    if run is None or run.error:
        say(f"[check] {cand.label} did not run")
        return {"candidate": cand.label, "error": "did not run"}
    want = {a: i for i, a in enumerate(abs_frames)}
    night = {}
    with open(args.check_night) as f:
        for line in f:
            i = line.find('"abs_frame":')
            if i < 0:
                continue
            a = int(line[i + 12:].split(",", 1)[0].split("}", 1)[0].strip())
            if a in want:
                night[a] = json.loads(line)
    rows = []
    for a, k in want.items():
        rec = night.get(a)
        if rec is None:
            continue
        mine = [b for b in run.boxes[k] if b[4] >= 0.02]
        theirs = [r[:5] for r in rec.get("low", [])]
        rows.append(_compare(a, mine, theirs))
    if not rows:
        say("[check] no kept frame in the night records")
        return {"candidate": cand.label, "error": "no common frame"}
    first = rows[0]
    say("")
    say(f"[check] {cand.label} vs the night's plain replay, frame {first['abs_frame']}: "
        f"{first['mine']} boxes here, {first['night']} there, {first['matched']} matched (IoU >= 0.5), "
        f"max |dxy| {first['max_dxy']:.2f} px, max |dconf| {first['max_dconf']:.4f}")
    for m, n in first["pairs"][:6]:
        say(f"   here  {[round(v, 1) for v in m[:4]]} {m[4]:.3f}")
        say(f"   night {[round(v, 1) for v in n[:4]]} {n[4]:.3f}")
    agg = {"frames": len(rows), "boxes_here": sum(r["mine"] for r in rows),
           "boxes_night": sum(r["night"] for r in rows), "matched": sum(r["matched"] for r in rows),
           "max_dxy": max(r["max_dxy"] for r in rows), "max_dconf": max(r["max_dconf"] for r in rows),
           "unmatched_here_max_conf": max((r["unmatched_here_max_conf"] for r in rows), default=0.0),
           "unmatched_night_max_conf": max((r["unmatched_night_max_conf"] for r in rows), default=0.0),
           "frames_identical_boxes": sum(1 for r in rows if r["mine"] == r["night"] == r["matched"]
                                         and r["max_dxy"] < 0.11)}
    say(f"[check] over {agg['frames']} kept frames: {agg['boxes_here']} boxes here, {agg['boxes_night']} "
        f"there, {agg['matched']} matched; max |dxy| {agg['max_dxy']:.2f} px, max |dconf| "
        f"{agg['max_dconf']:.4f}; unmatched max conf here {agg['unmatched_here_max_conf']:.3f}, night "
        f"{agg['unmatched_night_max_conf']:.3f}; {agg['frames_identical_boxes']} frames equal to 0.1 px")
    return {"candidate": cand.label, "first": {k: v for k, v in first.items() if k != "pairs"},
            "first_pairs": first["pairs"][:10], "all": agg}


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def _compare(a, mine, theirs):
    used = set()
    pairs = []
    for m in sorted(mine, key=lambda b: -b[4]):
        best, bi = 0.0, None
        for j, n in enumerate(theirs):
            if j in used:
                continue
            v = _iou(m, n)
            if v > best:
                best, bi = v, j
        if bi is not None and best >= 0.5:
            used.add(bi)
            pairs.append((list(m), list(theirs[bi])))
    um = [m for m in mine if not any(m is p[0] or list(m) == p[0] for p in pairs)]
    un = [n for j, n in enumerate(theirs) if j not in used]
    return {"abs_frame": a, "mine": len(mine), "night": len(theirs), "matched": len(pairs),
            "max_dxy": max((max(abs(p[0][i] - p[1][i]) for i in range(4)) for p in pairs), default=0.0),
            "max_dconf": max((abs(p[0][4] - p[1][4]) for p in pairs), default=0.0),
            "unmatched_here_max_conf": max((m[4] for m in um), default=0.0),
            "unmatched_night_max_conf": max((n[4] for n in un), default=0.0),
            "pairs": pairs}


# ------------------------------------------------------------------------------------------------------------
def run_concurrent(args):
    """The app's threading: the take plays on in real time on this thread while the SwipeController works on
    its own; the live frame time per phase (before / collecting / running / after)."""
    import cv2
    from runtime.swipe_controller import LiveSetting, SwipeController
    video, meta, config, source, plate = load_setup(args)
    proc, clock, current = build(args, config, video)
    xf = R.input_transform_for(config)
    fps = float(args.fps)
    pace_s = float(args.pace_ms) / 1000.0
    say(f"[swipe-live] take {video.name}: live {current.label}, plate {plate.name if plate else 'NONE'}, "
        f"pacing {'off' if pace_s <= 0 else f'{args.pace_ms:.0f} ms + after a live pass'}")
    live = LiveSetting(args.project or "take", current, id(proc.model), True)
    ep = engine_path_fn()
    only = ([parse_candidate(t, current.imgsz) for t in args.candidates.split(",") if t.strip()]
            if args.candidates else None)
    events = []

    def runner_factory(inp, p):
        return CandidateRunner(inp, bg_subtractor=p.bg_subtractor)
    ctl = SwipeController(processor=lambda: proc, live=lambda: live, engine_path=ep,
                          plate_ready=lambda: proc.plate_status == "ready",
                          on_progress=lambda s, t, p: (events.append((time.perf_counter(), s, t)),
                                                       say(f"[swipe-live] {s}: {t}")),
                          runner_factory=runner_factory, pace=pace_s > 0, pace_s=pace_s,
                          collect_timeout_s=args.max_s or 600.0)
    import runtime.swipe_controller as sc
    orig = sc.plan_candidates

    def plan_and_say(*a, **k):
        plan = orig(*a, **{**k, "only": only, "all_sizes": args.all_sizes})
        net = k.get("dancer_net_px")
        say(f"[swipe-live] dancer at the network: " + (f"~{net:.0f} px" if net else "not measured")
            + f"; planned: {', '.join(c.label for c in plan.candidates)}")
        for t in plan.skipped_text():
            say(f"  {t}")
        return plan
    sc.plan_candidates = plan_and_say
    cap = cv2.VideoCapture(str(video))
    start = int(round(args.start_s * fps))
    if start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    rows = []          # (phase, process ms, late)
    period = 1.0 / fps
    nxt = time.perf_counter()
    started = False
    done_at = None
    played = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame = xf.apply(frame)
        clock["t"] = (start + played) / fps
        if not started and played >= int(fps):           # the operator presses the button after 1 s
            why = ctl.start()
            if why:
                sys.exit(f"swipe refused: {why}")
            started = True
        phase = ctl.state if started else "before"
        if started and not ctl.busy:
            phase = "after"
            done_at = done_at or time.perf_counter()
        t0 = time.perf_counter()
        proc.process(frame, need_preview=False, frame_number=played)
        ms = (time.perf_counter() - t0) * 1000.0
        rows.append((phase, ms))
        played += 1
        if done_at is not None and time.perf_counter() - done_at > args.after_s:
            break
        nxt += period
        wait = nxt - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        else:
            nxt = time.perf_counter()                       # fell behind: no burst to catch up
    cap.release()
    if ctl.state == "collecting":                    # the take ended before enough lost frames
        say("[swipe-live] the take ended while collecting: cancelled")
        ctl.cancel()
    ctl.join(60)
    stats = {}
    for ph in ("before", "collecting", "running", "after"):
        v = [ms for p, ms in rows if p == ph]
        if v:
            stats[ph] = {"frames": len(v), "p50": round(pct(v, 50), 1), "p95": round(pct(v, 95), 1),
                         "p99": round(pct(v, 99), 1), "max": round(max(v), 1),
                         "over_period": sum(1 for x in v if x > period * 1000.0),
                         "over_100ms": sum(1 for x in v if x > 100.0)}
            say(f"  live frame ms {ph:<10} n={len(v):4d} p50={stats[ph]['p50']:6.1f} p95={stats[ph]['p95']:6.1f} "
                f"p99={stats[ph]['p99']:6.1f} max={stats[ph]['max']:6.1f}  over {period * 1000:.0f} ms: "
                f"{stats[ph]['over_period']}, over 100 ms: {stats[ph]['over_100ms']}")
    d = ctl.outcome.to_dict() if ctl.outcome is not None else {"state": ctl.state, "text": ctl.text}
    if ctl.outcome is not None:
        print_table(ctl.outcome, args)
        tm = ctl.outcome.timing
        say(f"swipe: collect {tm.get('collect_s', 0):.1f} s + run {tm.get('run_s', 0):.1f} s (load "
            f"{tm['load_s']:.1f}, inference {tm['infer_s']:.1f}) = {tm.get('total_s', 0):.1f} s")
    out = {"take": str(video), "mode": "concurrent", "pace_ms": args.pace_ms, "fps": fps,
           "live": current.label, "live_frame_ms": stats, "swipe": d, "state": ctl.state}
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, indent=1))
        say(f"wrote {args.out}")
    return out


def main(argv=None):
    from core.config import SWIPE_PACE_S
    ap = argparse.ArgumentParser(description="Headless settings swipe on a recorded take (Plan Cordee P5)")
    ap.add_argument("--project", help="project folder name")
    ap.add_argument("--slot", type=int, help="recording slot (the slot's newest take)")
    ap.add_argument("--video", help="explicit take path (overrides --slot)")
    ap.add_argument("--projects-dir", default=None, help="default: <repo>/projects")
    ap.add_argument("--config", choices=("meta", "latest"), default="meta",
                    help="the take's .meta config (the setting it was recorded with; default) or the "
                         "project's newest saved config")
    ap.add_argument("--plate", default=None, help="empty-wall plate .npz (default: the one loaded then)")
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--start-s", type=float, default=0.0)
    ap.add_argument("--max-s", type=float, default=600.0, help="play at most this much take")
    ap.add_argument("--fps", type=float, default=20.0, help="the take's frame clock (default 20)")
    ap.add_argument("--frames", type=int, default=None, help="frames a swipe runs on (default core.swipe's)")
    ap.add_argument("--engine-dir", default=None, help="TensorRT engines (default models/ or $WD_ENGINE_DIR)")
    ap.add_argument("--candidates", default=None, help="restrict, e.g. l@1920x1056,l@1760x960 (the live "
                                                        "setting always runs)")
    ap.add_argument("--all-sizes", action="store_true",
                    help="every model at every suitable size (diagnostics), not the dancer-height rule")
    ap.add_argument("--out", default=None, help="JSON output")
    ap.add_argument("--check-night", default=None, metavar="JSONL",
                    help="compare a candidate's boxes with a plain replay's 'low' boxes")
    ap.add_argument("--check-candidate", default="l@1920x1056")
    ap.add_argument("--concurrent", action="store_true",
                    help="the app's threading: the take plays on in real time beside the swipe")
    ap.add_argument("--pace-ms", type=float, default=SWIPE_PACE_S * 1000.0,
                    help="--concurrent: min interval between candidate inferences, 0 = back to back")
    ap.add_argument("--after-s", type=float, default=2.0, help="--concurrent: play on after the swipe")
    ap.add_argument("--after-ready-s", type=float, default=0.0,
                    help="keep playing this long after the lost pool is full (measures the tap then)")
    args = ap.parse_args(argv)
    if args.engine_dir:
        os.environ["WD_ENGINE_DIR"] = args.engine_dir
    if not args.video and (not args.project or args.slot is None):
        sys.exit("need --project and --slot, or --video")
    if args.video and not args.project:
        args.project = Path(args.video).resolve().parent.parent.name
    return run_concurrent(args) if args.concurrent else run_sequential(args)


if __name__ == "__main__":
    main()
