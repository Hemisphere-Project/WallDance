"""identity-output#6 / motion-calibration#3: Calibrate with a person in view, then again on a clean wall.
CalibrationFlows with the same MagicMock wiring as tests/test_calibration_flows.py (_flows / _wall_flows)."""
import sys
sys.dont_write_bytecode = True
from unittest.mock import MagicMock
import numpy as np
from core.calibration import CalibrationResult
from runtime.calibration_flows import CalibrationFlows

def _flows():
    settings = MagicMock(); settings.person_height_px = 150; settings.imgsz = 960
    enhancer = MagicMock(); enhancer.gamma = 1.0
    flows = CalibrationFlows(
        processor=MagicMock(), enhancer=enhancer, tracker=MagicMock(), settings=settings,
        recorder=MagicMock(), camera=MagicMock(), unified_camera=MagicMock(), use_unified=False,
        models=MagicMock(), cameras=MagicMock(), configs=MagicMock(), ui=MagicMock(available=False),
        last_raw_frame=lambda: None, roi_source_size=lambda: (1280, 720),
        get_effective_roi=lambda w, h: (0, 0, 1280, 720), reset_sensitivity_anchor=lambda **k: None,
        sync_mask_ui=lambda: None, request_reprocess=lambda: None, imgsz_change=lambda v: None)
    return flows, settings

def scene(clahe):
    r = CalibrationResult(); r.var_ok = True; r.var_threshold = 16.0; r.mog2_scale = 0.7
    r.clahe_value = clahe; r.noise_sigma = 2.0
    return r

def run_check(flows, ghost):
    n = 0
    for _ in range(2000):
        if flows._wall_check is None:
            break
        c = ghost(flows.enhancer.gamma, flows.enhancer.clahe_clip)
        flows.processor.last_raw_dets = [(c, 300.0, 400.0, 150.0)] if c > 0 else []
        flows._step_calibration([], 50.0)
        n += 1
    return n

flows, settings = _flows()
plates = []
flows.capture_plate = lambda src: plates.append(src)
flows.models._model_loaded = True
flows.recorder.is_playing = True          # playback path: no servo, no hardware
flows.recorder.playback_camera = None
flows.unified_camera = None
settings.confidence = 0.15                # D27 / D33 default
flows.processor.last_raw_dets = []

# --- the project's working calibration: gamma 1.30 / CLAHE 2.5, stamped by an earlier Calibrate
flows.enhancer.gamma, flows.enhancer.clahe_clip = 1.30, 2.5
flows.calibration_state = {"gamma": {"source": "aim", "ts": "x", "epoch": 1.0}}
print("before:            gamma %.2f CLAHE %.1f" % (flows.enhancer.gamma, flows.enhancer.clahe_clip))

# --- Calibrate #1 with a rigger in view: YOLO sees a person (0.6) whatever the enhancement
flows._cb_calibrate()
print("run 1 keep-first candidate:", flows._pre_enhancement)
flows.enhancer.gamma = 1.50               # the scene seed (seed_gamma) replaces it during the window
flows._apply_calibration(scene(clahe=2.5))
print("run 1 plate capture started at apply (before the wall verdict):", plates)
print("run 1 live confidence during the check:", settings.confidence)
n = run_check(flows, lambda g, c: 0.6)
r = flows._wall_result
print("run 1 (person in view): %d frames, clean=%s -> gamma %.2f CLAHE %.1f" % (n, r.clean, flows.enhancer.gamma, flows.enhancer.clahe_clip))
print("   tried:", [(round(c.gamma, 2), c.clahe, f"{c.ghost_frames}/{c.frames}") for c in r.tried])
print("   stamped:", sorted(flows.calibration_state), "| confidence back:", settings.confidence)

# --- Calibrate #2 on the now EMPTY wall (the toast's advice: remove the object, calibrate again)
flows._cb_calibrate()
print("run 2 keep-first candidate:", flows._pre_enhancement)
flows.enhancer.gamma = 1.50               # the scene seed again
flows._apply_calibration(scene(clahe=2.5))
n = run_check(flows, lambda g, c: 0.0)
r = flows._wall_result
print("run 2 (clean wall):     %d frames, clean=%s kept_current=%s -> gamma %.2f CLAHE %.1f" % (n, r.clean, r.kept_current, flows.enhancer.gamma, flows.enhancer.clahe_clip))
print("   " + r.summary().splitlines()[-1])

# --- control: what a clean first Calibrate gives from the same seed, no incumbent
f2, s2 = _flows(); f2.capture_plate = None; s2.confidence = 0.15; f2.processor.last_raw_dets = []
f2.enhancer.gamma, f2.enhancer.clahe_clip = 1.50, 2.5
f2._apply_calibration(scene(clahe=2.5)); run_check(f2, lambda g, c: 0.0)
print("control (clean wall, no incumbent): gamma %.2f CLAHE %.1f" % (f2.enhancer.gamma, f2.enhancer.clahe_clip))

# --- cancel: playback Calibrate sets exposure/gain from the take + seeds gamma; cancel restores nothing
f3, s3 = _flows(); f3.models._model_loaded = True; f3.recorder.is_playing = True
f3.recorder.playback_camera = {"exposure_us": 25000.0, "gain_db": 36.0}; f3.unified_camera = None
f3.enhancer.gamma, f3.enhancer.clahe_clip = 0.73, 2.5
f3.last_raw_frame = lambda: np.full((100, 100), 20, np.uint8)
f3._cb_calibrate()
g_seed = f3.enhancer.gamma
f3._cb_calibrate()                         # second press = cancel
print("cancel: gamma 0.73 -> seeded %.2f -> after cancel %.2f; exposure calls %s; gain calls %s" % (
    g_seed, f3.enhancer.gamma, f3.cameras._cb_ids_exposure_change.call_args_list, f3.cameras._cb_ids_gain_change.call_args_list))

# --- a passer-by: someone crosses the frame for 0.75 s (15 frames) at the start of the wall check
f4, s4 = _flows(); f4.capture_plate = None; s4.confidence = 0.15; f4.processor.last_raw_dets = []
f4.models._model_loaded = True; f4.recorder.is_playing = True; f4.recorder.playback_camera = None; f4.unified_camera = None
f4.enhancer.gamma, f4.enhancer.clahe_clip = 1.30, 2.5
f4.calibration_state = {"gamma": {"source": "aim", "ts": "x", "epoch": 1.0}}
f4._cb_calibrate(); f4.enhancer.gamma = 1.50
f4._apply_calibration(scene(clahe=2.5))
k = {"n": 0}
def passer(g, c):
    k["n"] += 1
    return 0.6 if k["n"] <= 15 else 0.0
n = run_check(f4, passer)
r = f4._wall_result
print("passer-by 15 frames: %d frames, clean=%s kept_current=%s -> gamma %.2f CLAHE %.1f (was 1.30 / 2.5)" % (
    n, r.clean, r.kept_current, f4.enhancer.gamma, f4.enhancer.clahe_clip))
print("   " + " | ".join(r.summary().splitlines()[1:]))
