"""The seamless switch on a recorded take (Plan Cordee P6), headless.

Plays a take through the real ``FrameProcessor`` on the GPU/TensorRT show path (``swipe_replay``'s setup: the
take's own config and plate, ``--set`` overrides), with the app's ``SeamlessSwitch`` beside it -- its worker
thread loads, the switch is drained between two frames exactly as the main loop does (``drain``).

* ``--swap-to l@1920x1056 --swap-at-s 60``: the operator's Apply at that take time -- the engine loads beside the
  live one on the switch's worker thread while the take plays on, the loop swaps it in between two frames.  Records
  every frame's emitted ids and points, the frame times, the GPU memory with one and two engines resident.
* no ``--swap-to``: the same take without a switch, the reference.
* ``--compare REF.json RUN.json``: ids and emitted points around the switch frame, frame by frame (no GPU).
* ``--realtime``: pace the take at ``--fps`` (the frame times beside a load or a run are then the show's);
  otherwise frames go back to back (the take clock still runs at ``--fps``: the policy's windows are take time).

    python tests/switch_replay.py --project jeudi0809_mur2 --slot 7 --max-s 120 --out ref.json
    python tests/switch_replay.py --project jeudi0809_mur2 --slot 7 --max-s 120 --realtime \\
        --swap-to l@1920x1056 --size-change --swap-at-s 60 --out swap.json
    python tests/switch_replay.py --compare ref.json swap.json

ASCII output only.  Linux: the venv's CUDA libs come first (``swipe_replay``'s one re-exec, before torch).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent / "src", _HERE.parent, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _compare(ref_path: str, run_path: str, before: int = 10, after: int = 60) -> dict:
    """Ids and emitted points of RUN against REF around RUN's switch frame (both runs from the same start)."""
    ref, run = json.loads(Path(ref_path).read_text()), json.loads(Path(run_path).read_text())
    rf = {r["f"]: r for r in ref["frames"]}
    sw = run.get("switch") or {}
    s = sw.get("frame")
    if s is None:
        print("no switch in the run")
        return {}

    def pts(r):
        return {int(k): tuple(v) for k, v in (r.get("xy") or {}).items()}

    def steps(frames, f):
        a, b = frames.get(f - 1), frames.get(f)
        if a is None or b is None:
            return {}
        pa, pb = pts(a), pts(b)
        return {i: math.dist(pa[i], pb[i]) for i in pa.keys() & pb.keys()}
    runf = {r["f"]: r for r in run["frames"]}
    rows, same_ids, n, div_max, gaps = [], 0, 0, [], []
    for f in range(s - before, s + after + 1):
        a, b = rf.get(f), runf.get(f)
        if a is None or b is None:
            continue
        n += 1
        ia, ib = sorted(pts(a)), sorted(pts(b))
        same_ids += ia == ib
        pa, pb = pts(a), pts(b)
        d = [math.dist(pa[i], pb[i]) for i in pa.keys() & pb.keys()]
        st_ref, st_run = steps(rf, f), steps(runf, f)
        rows.append({"f": f, "rel": f - s, "ids_ref": ia, "ids_run": ib,
                     "div_max": round(max(d), 2) if d else None,
                     "step_ref_max": round(max(st_ref.values()), 2) if st_ref else None,
                     "step_run_max": round(max(st_run.values()), 2) if st_run else None,
                     "model": b.get("label")})
        if f >= s and d:
            div_max.append(max(d))
    ref_steps = sorted(v for f in rf for v in steps(rf, f).values())
    p99 = ref_steps[int(0.99 * (len(ref_steps) - 1))] if ref_steps else None

    def rel_steps(frames, f):                  # each id's step in % of its emitted box height (this frame)
        st, b = steps(frames, f), frames.get(f) or {}
        hh = {int(k): v for k, v in (b.get("h") or {}).items()}
        return {i: 100.0 * v / hh[i] for i, v in st.items() if hh.get(i)}
    ref_rel = sorted(v for f in rf for v in rel_steps(rf, f).values())
    p99_rel = ref_rel[int(0.99 * (len(ref_rel) - 1))] if ref_rel else None
    at_switch = rel_steps(runf, s)
    jump_rel = max(at_switch.values()) if at_switch else None
    window = [f for f in range(s - before, s + after + 1) if f in rf and f in runf]
    gaps = [f for f in window if rf[f].get("xy") and not runf[f].get("xy")]
    emitted = sum(1 for f in window if runf[f].get("xy"))
    print(f"switch at frame {s} (take {sw.get('t')} s): {sw.get('old')} -> {sw.get('new')}")
    print(f"ids equal to the run without a switch on {same_ids} / {n} frames from {before} before to {after} after")
    pre = [r for r in rows if r["rel"] < 0]
    print(f"before the switch: emitted points identical on {sum(1 for r in pre if not r['div_max'])} / {len(pre)} "
          f"frames (max {max([r['div_max'] or 0 for r in pre], default=0):.2f} px)")
    print("   rel  ids(ref)        ids(run)        |run-ref| max px  step ref max  step run max  model")
    for r in rows:
        if -3 <= r["rel"] <= 10 or r["ids_ref"] != r["ids_run"]:
            print(f"  {r['rel']:4d}  {str(r['ids_ref']):<15} {str(r['ids_run']):<15} {str(r['div_max']):>14} "
                  f"{str(r['step_ref_max']):>13} {str(r['step_run_max']):>13}  {r['model']}")
    print(f"the run without a switch: a dancer's frame-to-frame step p99 = {p99:.2f} px" if p99 else "")
    if jump_rel is not None and p99_rel is not None:
        print(f"jump at the switch frame: {jump_rel:.2f} % of the dancer's height (the take's own frame-to-frame "
              f"p99 without a switch: {p99_rel:.2f} %)")
    print(f"OSC stream: emitted on {emitted} / {len(window)} frames around the switch; frames where the run "
          f"emitted nothing and the run without a switch did: {len(gaps)}")
    out = {"switch": sw, "frames_compared": n, "ids_equal_frames": same_ids, "rows": rows,
           "ref_step_p99": p99, "div_after_max": round(max(div_max), 2) if div_max else None,
           "jump_at_switch_pct_height": round(jump_rel, 2) if jump_rel is not None else None,
           "ref_step_p99_pct_height": round(p99_rel, 2) if p99_rel is not None else None,
           "osc_gaps": gaps, "osc_emitted": emitted, "osc_window": len(window)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="The seamless switch on a take (P6)")
    ap.add_argument("--compare", nargs=2, metavar=("REF", "RUN"))
    ap.add_argument("--project")
    ap.add_argument("--slot", type=int)
    ap.add_argument("--video")
    ap.add_argument("--projects-dir", default=None)
    ap.add_argument("--config", choices=("meta", "latest"), default="meta")
    ap.add_argument("--plate", default=None)
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--start-s", type=float, default=0.0)
    ap.add_argument("--max-s", type=float, default=600.0)
    ap.add_argument("--loops", type=int, default=1,
                    help="play the take this many times in a row (the take clock runs on; a scene cut at each seam)")
    ap.add_argument("--fps", type=float, default=16.94, help="the take's frame clock (the camera's, 08/10)")
    ap.add_argument("--frames", type=int, default=None)
    ap.add_argument("--engine-dir", default=None)
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--swap-to", default=None, help="the operator's Apply of this setting, e.g. l@1920x1056")
    ap.add_argument("--swap-at-s", type=float, default=60.0)
    ap.add_argument("--fail-first", action="store_true",
                    help="the switched-in engine raises on its first live frame (the rollback)")
    ap.add_argument("--broken-engine", default=None,
                    help="load this file as the winner's engine instead (a load that fails: the rollback)")
    ap.add_argument("--then-good-at-s", type=float, default=None,
                    help="Apply the same setting again at this take time (no injection): the fault clears")
    ap.add_argument("--dismiss-at-s", type=float, default=None, help="the operator's Dismiss at this take time")
    ap.add_argument("--size-change", action="store_true",
                    help="allow the switch to another input size (the app's is off since gate a: STANDBY reload)")
    ap.add_argument("--no-pin", action="store_true",
                    help="experiment: a size switch WITHOUT the pinned tracker space (what it would do)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if args.compare:
        out = _compare(*args.compare)
        if args.out:
            Path(args.out).write_text(json.dumps(out, indent=1))
        return out
    if args.engine_dir:
        os.environ["WD_ENGINE_DIR"] = args.engine_dir
    if not args.video and (not args.project or args.slot is None):
        sys.exit("need --project and --slot, or --video")
    return play(args)


def play(args):
    import swipe_replay as S                 # re-execs once (CUDA libs) before torch loads
    import cv2
    import torch
    import replay as R
    from core.ops_monitor import check_swipe
    from core.pipeline import FrameProcessor
    from core.swipe_runner import parse_candidate
    from runtime.seamless_switch import SeamlessSwitch
    from runtime.swipe_controller import LiveSetting
    say = S.say
    video, meta, config, source, plate = S.load_setup(args)
    proc, clock, current = S.build(args, config, video)
    xf = R.input_transform_for(config)
    fps = float(args.fps)
    ep = S.engine_path_fn()
    say(f"[p6] take {video.name}: live {current.label} ({source}), plate {plate.name if plate else 'NONE'}, "
        f"{'real time' if args.realtime else 'back to back'}")

    class Port:
        """The model controller's swap_live / restore_live, for a processor without an app."""
        def __init__(self):
            self.current, self.back = current, None

        def swap_live(self, model, runner, cand, on_fail=None, on_ok=None):
            if args.fail_first and not injected:
                injected.append(1)                    # the first switch only: a later Apply is a good one
                real = runner

                def boom(*a, **k):
                    raise RuntimeError("injected: the engine raised on its first live frame")
                runner = boom
                runner.model = model
                del real
            old = proc.adopt_model(model, runner, net_input=(int(cand.imgsz), cand.shape),
                                   on_fail=on_fail, on_ok=on_ok)
            self.back, self.current = self.current, cand
            if args.no_pin:
                proc.unpin_tracker_input()
            return old

        def restore_live(self, old=None):
            if self.back is not None:
                self.current, self.back = self.back, None
    injected = []
    port = Port()
    frame_wh = [0, 0]

    def live():
        s = proc.settings
        return LiveSetting(args.project or "take", port.current, id(proc.model), True,
                           roi=(bool(s.roi_enabled), int(s.roi_x), int(s.roi_y), int(s.roi_w), int(s.roi_h)),
                           plate=proc.plate_identity, plate_ok=not proc._plate_invalid, frame=tuple(frame_wh))
    events = []

    def notify(kind, payload):
        events.append({"kind": kind, "t": clock["t"], "text": payload.get("text")})
        say(f"[p6] {clock['t']:7.1f} s  {kind}: {payload.get('text')}")
    engine_path = ep
    if args.broken_engine:
        engine_path = lambda c: args.broken_engine  # noqa: E731
    sw = SeamlessSwitch(processor=lambda: proc, live=live, engine_path=engine_path, port=port, notify=notify,
                        period_s=0.5, size_change=args.size_change)
    bg = None
    target = parse_candidate(args.swap_to, current.imgsz) if args.swap_to else None

    def gpu_used_mb():
        """This process's GPU memory (NVML; the device's used memory without it: other processes count then)."""
        try:
            import pynvml
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(torch.cuda.current_device())
            for p in pynvml.nvmlDeviceGetComputeRunningProcesses(h):
                if p.pid == os.getpid():
                    return round(p.usedGpuMemory / 2 ** 20, 1)
        except Exception:  # noqa: BLE001
            pass
        free, total = torch.cuda.mem_get_info()
        return round((total - free) / 2 ** 20, 1)
    mem = {"start": gpu_used_mb()}
    done = []
    cap = cv2.VideoCapture(str(video))
    start = int(round(args.start_s * fps))
    if start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    period = 1.0 / fps
    nxt = time.perf_counter()
    frames, i = [], 0
    requested_at = loaded_at = switch = first_dancer = None
    again, dismissed, seams, rows = [], [], [], []
    resident_seen = False
    n_max = int(round(args.max_s * fps))
    while i < n_max:
        ok, frame = cap.read()
        if not ok and len(seams) + 1 < args.loops:       # --loops: from the start frame again, the clock runs on
            seams.append(i)
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            ok, frame = cap.read()
        if not ok:
            break
        frame = xf.apply(frame)
        frame_wh[:] = [frame.shape[1], frame.shape[0]]
        clock["t"] = (start + i) / fps
        if args.then_good_at_s is not None and requested_at is not None and not again \
                and i >= int(round(args.then_good_at_s * fps)):
            again.append(i)
            fault_before = sw.health()
            why = sw.request(target, f"switching to {target.label} (Apply, again)",
                             on_done=lambda ok_, text: done.append((ok_, text, i)))
            say(f"[p6] {clock['t']:7.1f} s  Apply {target.label} again: " + (why or "under way")
                + f"; fault before: {fault_before!r}")
        if args.dismiss_at_s is not None and not dismissed and i >= int(round(args.dismiss_at_s * fps)):
            dismissed.append((sw.health(), i))
            sw.dismiss()                                # the app's Dismiss: the switch's fault and the swipe's bans
            if bg is not None:
                bg.dismiss()
            say(f"[p6] {clock['t']:7.1f} s  Dismiss: fault {dismissed[0][0]!r} -> {sw.health()!r}")
        if target is not None and requested_at is None and i >= int(round(args.swap_at_s * fps)):
            why = (bg.request_switch if bg is not None else sw.request)(
                target, f"switching to {target.label} (Apply)", on_done=lambda ok_, text: done.append((ok_, text, i)))
            say(f"[p6] {clock['t']:7.1f} s  Apply {target.label}: " + (why or "under way"))
            requested_at = i
            mem["before_apply"] = gpu_used_mb()
        if sw._request is not None and loaded_at is None:
            loaded_at = i
            mem["two_engines"] = gpu_used_mb()
        if switch is not None and "after_switch" not in mem and i >= switch["frame"] + 40:
            mem["after_switch"] = gpu_used_mb()           # 2 s on: the old engine freed
        if bg is not None and bg.resident is not None and not resident_seen:
            resident_seen = True
            mem["with_resident"] = gpu_used_mb()
        drained = sw.drain()                            # the main loop's pump, between two frames
        if drained and sw.last_switch is not None and (switch is None or sw.last_switch is not switch.get("_row")):
            switch = {"_row": sw.last_switch, "frame": i, "t": round(clock["t"], 2), "old": sw.last_switch["old"],
                      "new": sw.last_switch["new"], "auto": sw.last_switch["auto"]}
        running = bool(bg is not None and bg.running)
        t0 = time.perf_counter()
        tracked, _disp, timing, _lat = proc.process(frame, need_preview=False, frame_number=i)
        ms = (time.perf_counter() - t0) * 1000.0
        emitted = list(proc.last_emitted or [])
        xy = {int(t.track_id): [round(v, 2) for v in FrameProcessor.sent_xy(t)] for t in emitted}
        hh = {int(t.track_id): round(float(t.bbox[3]), 1) for t in emitted if getattr(t, "bbox", None) is not None}
        pool = len(proc.swipe_snapshot()[0])
        if sw.health() != (rows[-1]["fault"] if rows else None):   # the readiness row the app builds from it
            row = check_swipe(fault=sw.health(), fault_recovered=True)
            rows.append({"f": i, "t": round(clock["t"], 2), "fault": sw.health(), "live": port.current.label,
                         "row": row.status if row else None})
            say(f"[p6] {clock['t']:7.1f} s  readiness row: {rows[-1]['row']} ({sw.health()!r}), live "
                f"{port.current.label}")
        if first_dancer is None and (pool or xy):
            first_dancer = i
        frames.append({"f": i, "t": round(clock["t"], 3), "ms": round(ms, 2), "yolo_ms": round(timing.get("yolo", 0.0), 2),
                       "xy": xy, "h": hh, "pool": pool, "label": port.current.label, "bg_running": running,
                       "tracker_input": list(proc.tracker_input) if proc.tracker_input else None})
        i += 1
        if args.realtime:
            nxt += period
            wait = nxt - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            else:
                nxt = time.perf_counter()
    cap.release()
    if bg is not None:
        bg.stop(5.0)
    sw.stop(5.0)
    mem["end"] = gpu_used_mb()
    sw.service()                                        # frees what waits (the old live engine), after a frame
    mem["after_free"] = gpu_used_mb()
    out = {"take": str(video), "live": current.label, "fps": fps, "realtime": bool(args.realtime),
           "first_dancer_frame": first_dancer,
           "first_dancer_t": round((start + first_dancer) / fps, 2) if first_dancer is not None else None,
           "frames": frames, "events": events, "mem_mb": mem, "done": done,
           "history": [{k: v for k, v in h.items() if isinstance(v, (str, int, float, bool, type(None)))}
                       for h in sorted(list(sw.history) + (list(bg.history) if bg else []),
                                       key=lambda h: h.get("wall", 0))],
           "inferences": bg.inferences if bg else 0, "fault": sw.fault, "live_at_end": port.current.label,
           "dismissed": dismissed, "again_frame": again[0] if again else None, "loop_seams": seams,
           "readiness_rows": rows}
    if switch is not None:
        s = switch["frame"]
        switch.pop("_row")
        win = [f for f in frames if s <= f["f"] <= s + 5]
        prev = next((f for f in frames if f["f"] == s - 1), None)
        switch["frame_ms_switch_and_5_after"] = [f["ms"] for f in win]
        switch["yolo_ms_switch_and_5_after"] = [f["yolo_ms"] for f in win]
        switch["ids_before"] = sorted(prev["xy"]) if prev else None
        switch["ids_at"] = sorted(win[0]["xy"]) if win else None
        out["switch"] = switch
    if requested_at is not None:
        out["apply"] = {"requested_frame": requested_at, "loaded_frame": loaded_at,
                        "load_s_take": round(((loaded_at or requested_at) - requested_at) / fps, 2)}
    loads = [h for h in out["history"] if h.get("kind") == "load"]
    out["loads"] = loads

    def dist(v):
        v = sorted(v)
        if not v:
            return None
        return {"n": len(v), "p50": round(S.pct(v, 50), 1), "p95": round(S.pct(v, 95), 1), "max": round(max(v), 1)}
    out["frame_ms"] = {"outside_runs": dist([f["ms"] for f in frames if not f["bg_running"]]),
                       "during_runs": dist([f["ms"] for f in frames if f["bg_running"]])}
    if requested_at is not None:
        lo, hi = requested_at, (switch["frame"] if switch else loaded_at or requested_at)
        out["frame_ms"]["while_loading_beside"] = dist([f["ms"] for f in frames if lo <= f["f"] <= hi])
        out["frame_ms"]["before_apply"] = dist([f["ms"] for f in frames if f["f"] < lo])
        if switch:
            out["yolo_ms"] = {"before_switch": dist([f["yolo_ms"] for f in frames if f["f"] < switch["frame"]]),
                              "after_switch": dist([f["yolo_ms"] for f in frames if f["f"] > switch["frame"] + 5])}
    say(f"[p6] frames {len(frames)}, inferences {out['inferences']}, loads {len(loads)}, fault {sw.fault!r}, "
        f"first dancer at {out['first_dancer_t']} s")
    for h in out["history"]:
        if h.get("kind") in ("look", "advice", "switch", "load", "release", "free", "fault", "manual-change"):
            say("[p6]   " + ", ".join(f"{k}={v}" for k, v in h.items() if k != "wall" and v is not None))
    say(f"[p6] GPU used MB: {mem}")
    for k, v in out["frame_ms"].items():
        if v:
            say(f"[p6] live frame ms {k:<22} n={v['n']:5d} p50={v['p50']:6.1f} p95={v['p95']:6.1f} max={v['max']:6.1f}")
    if switch:
        say(f"[p6] switch at frame {switch['frame']} ({switch['t']} s) {switch['old']} -> {switch['new']}: frame ms "
            f"{switch['frame_ms_switch_and_5_after']}, yolo ms {switch['yolo_ms_switch_and_5_after']}, ids "
            f"{switch['ids_before']} -> {switch['ids_at']}")
    if out.get("yolo_ms"):
        say(f"[p6] network ms before / after the switch: {out['yolo_ms']}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, indent=0))
        say(f"wrote {args.out}")
    return out


if __name__ == "__main__":
    main()
