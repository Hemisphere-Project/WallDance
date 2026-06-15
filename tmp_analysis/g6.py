"""G6 — clean-plate / var recovery: does the MOG2 var-sweep give the same answer on a
dancers-PRESENT window as on an emptier (recording-start) window of the SAME recording?
If yes, the unified C-next 'one dancers pass' can derive var without a separate empty-stage pass.
CPU-only (SceneCalibrator var x scale FP sweep; no YOLO). Reuses corpus_survey.scene_block.
Run:  application/.venv/Scripts/python.exe tmp_analysis/g6.py
"""
import sys, json
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cv2
import corpus_survey as cs  # sets up application/src + tests on sys.path on import

# name, project, slot, early_start (emptiest), present_start (dancers-present)
CASES = [
    ("hangar-floor",   "3_TANGO_HANGAR-whitebg2",   3, 0, 1500),
    ("texture-duo",    "1_TANGO_HANGAR-texturedbg", 5, 0, 1000),
    ("dark-crowd",     "0-TEST-verydark",           5, 0, 200),
    ("outdoor-sitter", "7_TANGO_TOGO-day",          9, 0, 2500),
]

def brief(r):
    if not r:
        return None
    return {k: r.get(k) for k in (
        "var_threshold", "mog2_scale", "var_fp_rate", "var_saturated",
        "noise_sigma", "brightness_mean")}

out = []
for name, proj, slot, early, present in CASES:
    rec = {"name": name, "project": proj, "slot": slot}
    vid = cs.find_recording(proj, slot)
    if vid is None:
        rec["error"] = "no recording"; out.append(rec); continue
    cap = cv2.VideoCapture(str(vid))
    if not cap.isOpened():
        rec["error"] = "cannot open"; out.append(rec); continue
    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    rec[f"early@{early}"] = brief(cs.scene_block(cap, early, None, fps))
    rec[f"present@{present}"] = brief(cs.scene_block(cap, present, None, fps))
    cap.release()
    out.append(rec)
    print(f"[g6] {name}: early={rec.get(f'early@{early}')}  present={rec.get(f'present@{present}')}", flush=True)

Path(HERE / "g6").mkdir(exist_ok=True)
Path(HERE / "g6" / "result.json").write_text(json.dumps(out, indent=2))
print("\nwrote tmp_analysis/g6/result.json")
