"""cProfile of the post-YOLO chain only (no stage wrappers -> clean attribution)."""
import argparse, json, sys, time, cProfile, pstats, io
from pathlib import Path
APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "tests")); sys.path.insert(0, str(APP / "src")); sys.path.insert(0, str(APP))
import replay, scoring, cv2, numpy as np
ap = argparse.ArgumentParser()
ap.add_argument("--scenario", required=True); ap.add_argument("--frames", type=int, default=300)
ap.add_argument("--plate", action="store_true"); ap.add_argument("--set", dest="sets", action="append", default=[])
ap.add_argument("--out", required=True)
a = ap.parse_args()
m = json.loads(Path(a.scenario).read_text())
cfg = replay.scenario_config(m)
cfg.setdefault("max_dancers", max(1, scoring.max_expected(scoring.load_scenario(a.scenario))))
replay.apply_overrides(cfg, a.sets)
cfg, _ = replay.fill_detection_defaults(cfg)
video = replay._find_recording(m["project"], m["slot"])
proc = replay._build_processor(cfg, "yolo11x-pose", 1280, use_gpu_path=True, use_trt=True)
proc.tracker.reset()
clock = replay._attach_frame_clock(proc, float(m.get("fps") or 20.0))
logdir = Path(a.out + "_log"); logdir.mkdir(parents=True, exist_ok=True)
proc.tracker.logger.start_session(str(logdir))
from core.osc_output import OSCSender
proc.attach_osc(OSCSender("127.0.0.1", 59999)); proc.settings.osc_enabled = True
cap = cv2.VideoCapture(str(video)); cap.set(cv2.CAP_PROP_POS_FRAMES, m["start"])
if a.plate:
    from core.foreground import CleanPlate
    fr = []
    for i in range(a.frames + 20):
        ok, f = cap.read()
        if not ok: break
        if i % 8 == 0: fr.append(np.ascontiguousarray(f[:, :, 0]))
    proc.set_clean_plate(CleanPlate.from_frames(fr, source="take-median")); proc.settings.fg_enabled = True
    cap.set(cv2.CAP_PROP_POS_FRAMES, m["start"])
prof = cProfile.Profile()
_ppc = proc._post_yolo_chain
state = {"on": False, "n": 0}
def _p(*x, **k):
    if not state["on"]:
        return _ppc(*x, **k)
    prof.enable()
    try:
        return _ppc(*x, **k)
    finally:
        prof.disable(); state["n"] += 1
proc._post_yolo_chain = _p
for i in range(a.frames + 20):
    ok, f = cap.read()
    if not ok: break
    clock["frame"] = i
    state["on"] = i >= 20
    proc.process(f, need_preview=False, frame_number=i)
cap.release(); proc.tracker.logger.close()
prof.dump_stats(a.out + ".prof")
n = state["n"]
for sort in ("cumulative", "tottime"):
    s = io.StringIO(); ps = pstats.Stats(prof, stream=s); ps.sort_stats(sort).print_stats(38)
    txt = s.getvalue()
    print(f"==== sort={sort}  frames={n}  (divide totals by {n} for per-frame)")
    print("\n".join(l[:170] for l in txt.splitlines()[4:]))
