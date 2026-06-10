"""Generate the re-founded scenario manifests (Phase 0, CORPUS_ANALYSIS §5).

Each manifest pins: the exact config snapshot (flattened project config +
the analysis' calibrated overrides where those scored better), a recording
fingerprint (bytes + meta frame count), fps from the .avi.meta sidecar, the
scene-class pass line, and ground-truth provenance.

Run from application/:  .venv/Scripts/python.exe ../tmp_analysis/gen_manifests.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
APP = REPO / "application"
for p in (str(APP / "tests"), str(APP / "src"), str(APP)):
    if p not in sys.path:
        sys.path.insert(0, p)

import replay  # noqa: E402

SCEN_DIR = APP / "tests" / "scenarios"
DRAFT_DIR = SCEN_DIR / "drafts"
DRAFT_DIR.mkdir(parents=True, exist_ok=True)

PASS_A = {"class": "A", "drop_rate": 0.05, "ghost_rate": 0.05, "longest_drop_s": 1.0}
PASS_B = {"class": "B", "drop_rate": 0.10, "ghost_rate": 0.15, "longest_drop_s": 2.0}
PASS_S = {"class": "S"}  # stress: no pass line, regression-direction only

CAL_COMMON = {"enhance_enabled": True, "enhance_force": False,
              "brightness_threshold": 131, "clahe_clip": 2.5,
              "mog2_var_threshold": 8, "mog2_scale": 0.7,
              "yolo_imgsz": 1280, "greyscale": True}

M = [
    # name, project, slot, start, frames, expected_count, overrides(None=project config),
    # pass, tags, verified, notes
    dict(name="hangar-floor", project="3_TANGO_HANGAR-whitebg2", slot=3,
         start=1500, frames=300, n=1, overrides=None, pas=PASS_A,
         tags=["single", "low_light", "white_bg", "floor", "slow_motion"],
         verified=True,
         notes="Re-founded from residence1-solo_slot3 (same file, operator-confirmed "
               "2026-06-10; old GT verified 2026-06-09: N=1 constant, dancer present "
               "throughout [1500,1800), no drops/over-counts). Config re-pinned to the "
               "project's 2026-06-10 save (the old tuned config was lost in the "
               "project reorganisation)."),
    dict(name="hangar-aerial", project="3_TANGO_HANGAR-whitebg2", slot=4,
         start=1500, frames=300, n=1,
         overrides={**CAL_COMMON, "gamma": 2.2, "confidence": 0.5,
                    "person_height_px": 190, "person_height_min_ratio": 0.52,
                    "person_height_max_ratio": 1.63},
         pas=PASS_A,
         tags=["single", "low_light", "white_bg", "aerial", "small_far",
               "fast_motion", "drops"],
         verified=True,
         notes="Re-founded from residence1-solo_slot4 (same file; old GT verified "
               "2026-06-09: N=1 constant, aerial rope dancer present throughout). The "
               "old GT's drop regions [1515-1524, 1643-1654, 1764-1786] were measured "
               "under the lost legacy config -- historical provenance only. Pinned "
               "config = 2026-06-10 corpus-analysis calibrated settings (auto-gamma "
               "2.2, measured height/ratios, var 8 / scale 0.7)."),
    dict(name="texture-aerial", project="2_TANGO_HANGAR-whitebg", slot=7,
         start=200, frames=600, n=1,
         overrides={**CAL_COMMON, "gamma": 2.0, "confidence": 0.35,
                    "person_height_px": 273, "person_height_min_ratio": 0.46,
                    "person_height_max_ratio": 1.51},
         pas=PASS_A,
         tags=["single", "low_light", "textured_bg", "aerial", "fast_motion",
               "ghost_pressure"],
         verified=False,
         notes="Operator-picked third golden (2026-06-10): aerial dancer on the "
               "heavily textured wall, day-1 evening. Survey: raw cov@.25 0.78, "
               "best-t 0.35, h med 273 (125-413). GT pending the montage pass."),
    dict(name="texture-duo", project="1_TANGO_HANGAR-texturedbg", slot=5,
         start=1000, frames=400, n=2,
         overrides={**CAL_COMMON, "gamma": 1.35, "confidence": 0.15,
                    "person_height_px": 327, "person_height_min_ratio": 0.49,
                    "person_height_max_ratio": 1.5},
         pas=PASS_A,
         tags=["multi", "duo", "occlusion", "textured_bg", "ghost_pressure"],
         verified=False,
         notes="Two dancers moving together (swap/occlusion seed) on the ghost-prone "
               "textured wall. Known fixed ghost spots ~(413,1024) h183 and "
               "(1124,856) h430."),
    dict(name="texture-wallhang", project="1_TANGO_HANGAR-texturedbg", slot=4,
         start=2500, frames=400, n=1, overrides=None, pas=PASS_A,
         tags=["single", "aerial", "textured_bg", "ghost_pressure", "static"],
         verified=False,
         notes="Wall-hanged dancer on the textured wall. Project config pinned "
               "(scored 0.39 vs 0.79 for the naive calibrated set -- low confidence "
               "floods texture ghosts; known-N joint search expected to beat both)."),
    dict(name="white-duo", project="4_TANGO_HANGAR-whitebg3", slot=2,
         start=100, frames=400, n=2,
         overrides={**CAL_COMMON, "gamma": 2.2, "confidence": 0.25,
                    "person_height_px": 206, "person_height_min_ratio": 0.41,
                    "person_height_max_ratio": 1.68},
         pas=PASS_A,
         tags=["multi", "duo", "white_bg", "split_merge"],
         verified=False,
         notes="Duo moving together/apart, white bg. Known fixed ghost spots shared "
               "with the whitebg2 venue: (1225,519) h85, (873,487) h152."),
    dict(name="blur-runner", project="5_TANGO_HANGAR-testflou", slot=6,
         start=900, frames=400, n=2,
         overrides={**CAL_COMMON, "gamma": 2.2, "confidence": 0.5,
                    "person_height_px": 174, "person_height_min_ratio": 0.8,
                    "person_height_max_ratio": 1.78, "roi_enabled": False},
         pas=PASS_B,
         tags=["defocus", "fast_motion", "small_far", "bystander"],
         verified=False,
         notes="Running dancer, heavy defocus. N=2 provisional: a second real person "
               "(white-clad assistant, left equipment area ~(520-590,1100-1130)) is "
               "visible -- confirm during GT pass or define a stage ROI."),
    dict(name="outdoor-night", project="6_TANGO_TOGO-night", slot=1,
         start=0, frames=330, n=1, overrides=None, pas=PASS_B,
         tags=["single", "outdoor", "extreme_dark", "building_bg"],
         verified=False,
         notes="Building facade at night, scene brightness 1.4/255. Project config "
               "pinned (0.29 vs 0.44 calibrated -- enhancement admits fixed-spot "
               "ghosts without an exclusion mask)."),
    dict(name="outdoor-sitter", project="7_TANGO_TOGO-day", slot=9,
         start=2500, frames=400, n=2,
         overrides={**CAL_COMMON, "gamma": 2.2, "confidence": 0.65,
                    "person_height_px": 123, "person_height_min_ratio": 0.8,
                    "person_height_max_ratio": 1.53, "roi_enabled": False},
         pas=PASS_B,
         tags=["multi", "outdoor", "static_person", "small_far", "daylight_ir"],
         verified=False,
         notes="1 walking dancer + 1 static balcony sitter (the static-acquisition "
               "regression scene: 0.50 -> 0.075 with calibrated settings). Bystanders "
               "may enter the frame bottom -- count all visible people or pin a "
               "stage ROI during the GT pass."),
    dict(name="facade-ghosts", project="0-TEST-phones", slot=1,
         start=200, frames=400, n=4, overrides=None, pas=PASS_S,
         tags=["multi", "small_far", "ghost_flood", "phone_source", "stress"],
         verified=False,
         notes="4 tiny dancers on an apartment facade (phone footage, non-rig "
               "source). Ghost-flood + MAX_PERSONS stress case; class S -- no pass "
               "line, regression direction only."),
]

DRAFTS = [
    dict(name="dark-crowd", project="0-TEST-verydark", slot=5,
         start=0, frames=400, n=2, overrides=None, pas=PASS_B,
         tags=["multi", "extreme_dark", "enter_leave", "draft"],
         verified=False,
         notes="DRAFT -- N varies 1-4 (people enter/leave); expected_count=2 is a "
               "placeholder. Needs per-range labels from the GT sheet before "
               "promotion to scenarios/."),
    dict(name="white-walkers", project="4_TANGO_HANGAR-whitebg3", slot=3,
         start=0, frames=400, n=4, overrides=None, pas=PASS_A,
         tags=["multi", "count_stress", "enter_leave", "draft"],
         verified=False,
         notes="DRAFT -- 4-5 test walkers; expected_count=4 is a placeholder. Needs "
               "per-range labels from the GT sheet before promotion."),
]


def build(entry: dict, out_dir: Path) -> Path:
    project, slot = entry["project"], entry["slot"]
    video = replay._find_recording(project, slot)
    if video is None:
        raise SystemExit(f"{entry['name']}: no recording for {project} slot {slot}")
    meta_path = video.with_name(video.name + ".meta")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    fps = meta.get("actual_fps")
    if fps is None:
        import cv2
        cap = cv2.VideoCapture(str(video))
        fps = round(cap.get(cv2.CAP_PROP_FPS) or 20.0, 3)
        cap.release()

    config = replay._latest_config(project)
    if config is None:
        raise SystemExit(f"{entry['name']}: no saved config for {project}")
    config.pop("_meta", None)
    if entry["overrides"]:
        config.update(entry["overrides"])

    manifest = {
        "name": entry["name"],
        "project": project,
        "slot": slot,
        "start": entry["start"],
        "frames": entry["frames"],
        "warmup": 15,
        "fps": fps,
        "expected_count": entry["n"],
        "tags": entry["tags"],
        "pass": entry["pas"],
        "recording_fingerprint": {
            "file": video.name,
            "bytes": video.stat().st_size,
            "frames": meta.get("frames"),
        },
        "ground_truth": {
            "verified": entry["verified"],
            "method": "visual montage sampling (scenarios/README.md protocol)" if entry["verified"]
                      else "PENDING -- operator montage pass on tmp_analysis/gt_sheets/",
            "notes": entry["notes"],
        },
        "config": config,
    }
    out = out_dir / f"{entry['name']}.json"
    out.write_text(json.dumps(manifest, indent=1))
    return out


def main():
    for e in M:
        print("wrote", build(e, SCEN_DIR))
    for e in DRAFTS:
        print("wrote", build(e, DRAFT_DIR))


if __name__ == "__main__":
    main()
