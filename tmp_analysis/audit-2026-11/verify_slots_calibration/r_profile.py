import sys
sys.dont_write_bytecode = True
from core import config_schema as cs
flat = {"model": "yolo11x-pose", "camera_source": "ids", "confidence": 0.15, "gamma": 0.73, "clahe_clip": 2.5,
        "ids_exposure_us": 25000.0, "ids_gain_db": 30.0, "mog2_var_threshold": 8.0, "mog2_scale": 0.7,
        "sensitivity": 50.0, "sensitivity_conf_seed": 0.15, "sensitivity_var_anchor": 8.0,
        "calibration_state": {"gamma": {"source": "aim", "ts": "2026-10-06 21:20", "epoch": 1.0}},
        "fg_enabled": True, "fg_plate": "plates/latest.npz", "person_height_px": 127}
shared, profile = cs.split_profile(flat)
print("profile bundle keys :", sorted(profile))
print("shared keys         :", sorted(shared))
bundle = dict(profile)                      # what _cb_profile_switch hands to apply_config
full_config = "model" in bundle or "camera_source" in bundle
print("apply_config(bundle): full_config =", full_config,
      "| 'calibration_state' in bundle =", "calibration_state" in bundle,
      "-> calibration_state untouched, _load_project_plate() not called (app.py:1322-1324, :1497-1498)")
st = cs.structure(flat, {"show": profile}, "show")
print("structured: profiles =", {k: sorted(v) for k, v in st["profiles"].items()})
print("            rehearsal seeded as a copy of show:", st["profiles"].get("rehearsal") == st["profiles"].get("show"))
