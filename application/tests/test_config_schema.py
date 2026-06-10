"""Schema v2 (lighting profiles) — migration, flatten/structure round-trip, validation."""
import copy

import config_schema as cs


def _v1_config():
    return {
        "camera_source": "0",
        "model": "yolo11l-pose",
        "use_tensorrt": True,
        "confidence": 0.25,
        "yolo_imgsz": 960,
        "person_height_px": 200,
        "gamma": 1.2,
        "clahe_clip": 3.0,
        "mog2_var_threshold": 16.0,
        "mog2_scale": 0.75,
        "ids_gain_db": 12.0,
        "ids_exposure_us": 20000.0,
        "exclusion_grid": [16, 10],
        "exclusion_cells": [[3, 4]],
        "osc_ip": "127.0.0.1",
        "osc_port": 9000,
        "_meta": {"project": "demo"},
    }


# ---------------------------------------------------------------- migration

def test_migrate_v1_moves_profile_keys():
    out = cs.migrate(_v1_config())
    assert out["config_version"] == cs.SCHEMA_VERSION
    assert out["active_profile"] == "show"
    assert set(out["profiles"]) == set(cs.PROFILE_NAMES)
    # profile keys moved out of the top level
    for key in cs.PROFILE_KEYS:
        assert key not in out
    assert out["profiles"]["show"]["confidence"] == 0.25
    assert out["profiles"]["show"]["ids_gain_db"] == 12.0
    # rehearsal seeded as a copy of show
    assert out["profiles"]["rehearsal"] == out["profiles"]["show"]
    # shared keys stay at the top level
    assert out["model"] == "yolo11l-pose"
    assert out["osc_port"] == 9000
    assert out["_meta"]["project"] == "demo"


def test_migrate_v2_idempotent():
    once = cs.migrate(_v1_config())
    twice = cs.migrate(copy.deepcopy(once))
    assert twice == once


def test_migrate_v2_fills_missing_profile():
    cfg = cs.migrate(_v1_config())
    del cfg["profiles"]["rehearsal"]
    out = cs.migrate(cfg)
    assert out["profiles"]["rehearsal"] == out["profiles"]["show"]


def test_migrate_bad_active_profile_reset():
    cfg = cs.migrate(_v1_config())
    cfg["active_profile"] = "nonsense"
    assert cs.migrate(cfg)["active_profile"] == cs.DEFAULT_PROFILE


# ------------------------------------------------------------ flatten/split

def test_flatten_v1_passthrough():
    v1 = _v1_config()
    flat = cs.flatten(v1)
    for k, v in v1.items():
        assert flat[k] == v, k


def test_flatten_picks_active_profile():
    cfg = cs.migrate(_v1_config())
    cfg["profiles"]["rehearsal"]["confidence"] = 0.6
    cfg["profiles"]["rehearsal"]["gamma"] = 2.0
    cfg["active_profile"] = "rehearsal"
    flat = cs.flatten(cfg)
    assert flat["confidence"] == 0.6
    assert flat["gamma"] == 2.0
    assert flat["model"] == "yolo11l-pose"
    assert "profiles" not in flat and "active_profile" not in flat


def test_split_profile():
    v1 = _v1_config()
    shared, profile = cs.split_profile(v1)
    assert set(profile) == {k for k in cs.PROFILE_KEYS if k in v1}
    assert "model" in shared and "confidence" not in shared


# ------------------------------------------------------- structure (saving)

def test_structure_round_trip():
    v1 = _v1_config()
    structured = cs.migrate(v1)
    profiles = structured["profiles"]
    flat = cs.flatten(structured)
    # operator edits a profile value live, then saves
    flat["confidence"] = 0.4
    out = cs.structure(flat, profiles, "show")
    assert out["profiles"]["show"]["confidence"] == 0.4
    # the inactive bundle is carried along unchanged
    assert out["profiles"]["rehearsal"]["confidence"] == 0.25
    assert out["config_version"] == cs.SCHEMA_VERSION
    # flatten(structure(...)) returns the operator's values
    assert cs.flatten(out)["confidence"] == 0.4


def test_structure_bad_active_falls_back():
    flat = cs.flatten(_v1_config())
    out = cs.structure(flat, {}, "bogus")
    assert out["active_profile"] == cs.DEFAULT_PROFILE
    assert set(out["profiles"]) == set(cs.PROFILE_NAMES)


# ------------------------------------------------------------- validation

def test_validate_clamps_and_warns():
    flat = cs.flatten(_v1_config())
    flat["confidence"] = 7.0
    flat["gamma"] = 0.0
    out, warnings = cs.validate_flat(flat)
    assert out["confidence"] == 0.95
    assert out["gamma"] == 0.2
    assert len(warnings) == 2


def test_validate_in_range_silent():
    out, warnings = cs.validate_flat(cs.flatten(_v1_config()))
    assert warnings == []
    assert out["confidence"] == 0.25


def test_validate_drops_junk():
    flat = {"confidence": "not-a-number", "yolo_imgsz": "huge"}
    out, warnings = cs.validate_flat(flat)
    assert "confidence" not in out
    assert "yolo_imgsz" not in out
    assert len(warnings) == 2


def test_validate_imgsz_snaps_to_preset():
    out, warnings = cs.validate_flat({"yolo_imgsz": 1000})
    assert out["yolo_imgsz"] == 960
    assert warnings


def test_validate_preserves_int_type():
    out, _ = cs.validate_flat({"person_height_px": 200, "osc_port": 9000})
    assert isinstance(out["person_height_px"], int)
    assert isinstance(out["osc_port"], int)


def test_sensitivity_var_anchor_profile_scoped_and_clamped():
    # Bug #8: the calibrated anchor is persisted alongside the live macro
    # output; it is lighting-coupled, so it must travel with the profile.
    assert "sensitivity_var_anchor" in cs.PROFILE_KEYS
    out, warnings = cs.validate_flat({"sensitivity_var_anchor": 1.0})
    assert out["sensitivity_var_anchor"] == 4.0
    assert warnings


# ------------------------------------------------- cross-field / structural


def test_validate_swaps_inverted_height_ratios():
    # _RANGES pins min <= 1.0 <= max today; the explicit ordering check is
    # defense-in-depth should those ranges ever loosen.
    out, warnings = cs.validate_flat(
        {"person_height_min_ratio": 0.9, "person_height_max_ratio": 1.0})
    assert out["person_height_min_ratio"] <= out["person_height_max_ratio"]
    assert warnings == []  # in-range, ordered: silent


def test_validate_roi_drops_non_numeric():
    # The apply path int()-coerces ROI keys raw; junk must not crash the load.
    out, warnings = cs.validate_flat({"roi_x": "abc", "roi_y": None, "roi_w": 100})
    assert "roi_x" not in out
    assert out["roi_w"] == 100
    assert len(warnings) == 1  # None is treated as absent, only roi_x warns


def test_validate_roi_drops_impossible_rect():
    out, warnings = cs.validate_flat({"roi_x": -5, "roi_w": 0, "roi_h": 240})
    assert "roi_x" not in out
    assert "roi_w" not in out
    assert out["roi_h"] == 240
    assert len(warnings) == 2


def test_validate_roi_coerces_numeric_strings():
    out, warnings = cs.validate_flat({"roi_x": "10", "roi_w": 640.0})
    assert out["roi_x"] == 10
    assert out["roi_w"] == 640
    assert warnings == []


def test_validate_exclusion_mask_well_formed_silent():
    out, warnings = cs.validate_flat(
        {"exclusion_grid": [16, 10], "exclusion_cells": [[3, 4], [0, 0]]})
    assert out["exclusion_grid"] == [16, 10]
    assert warnings == []


def test_validate_exclusion_mask_malformed_dropped_whole():
    # set_cells() indexes grid[0]/grid[1] and each cell pair - malformed
    # shapes crashed the project load before validation covered them.
    for bad in (
        {"exclusion_grid": "16x10", "exclusion_cells": [[3, 4]]},
        {"exclusion_grid": [16], "exclusion_cells": [[3, 4]]},
        {"exclusion_grid": [16, 0], "exclusion_cells": [[3, 4]]},
        {"exclusion_grid": [16, 10], "exclusion_cells": [[3, 4], [5]]},
        {"exclusion_grid": [16, 10], "exclusion_cells": [["a", "b"]]},
        {"exclusion_grid": [16, 10], "exclusion_cells": {"3": 4}},
    ):
        out, warnings = cs.validate_flat(dict(bad))
        assert "exclusion_grid" not in out, bad
        assert "exclusion_cells" not in out, bad
        assert len(warnings) == 1, bad


def test_validate_exclusion_cells_alone_ok():
    # Cells without a grid: the apply path falls back to the default grid.
    out, warnings = cs.validate_flat({"exclusion_cells": [[1, 2]]})
    assert out["exclusion_cells"] == [[1, 2]]
    assert warnings == []
