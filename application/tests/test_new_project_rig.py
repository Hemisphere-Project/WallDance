"""A new project ("Start blank") keeps the camera rig of the last used project.

2026-10-08 field bug: Start blank + save reopened the IDS at the code's crop ratio
1.0 (portrait 1488x1528) while every project ran 1.37 (landscape 1776x1304), since
every IDS open uses the "project's" ratio (9659349/d21be77).  A blank start now
inherits the RIG (camera, crop, exposure/gain, mirror/rotation, the on-camera rig
sheet) of the most recently used project; the SCENE (ROI, mask, gamma/CLAHE/MOG2,
sensitivity, calibration_state, distances/focus/markers/notes) does not inherit.
Plus the rig-sheet default: no 8 mm lens claimed by the code.

Headless: a real ConfigManager + CameraController + the app's real
_apply_config_without_model on a duck-typed app; a fake IDS camera.
"""
import json
import os
import time
import types
from types import SimpleNamespace

import pytest

pytest.importorskip("torch")                      # runtime.config_manager / app -> camera stack
from app import WallDanceApp                       # noqa: E402
from camera.ids_camera import CameraSource         # noqa: E402
from core import config as cfgmod                  # noqa: E402
from core import config_schema                     # noqa: E402
from core.config_store import ConfigStore          # noqa: E402
from core.input_transform import IDENTITY          # noqa: E402
from runtime import api                            # noqa: E402
from runtime.camera_controller import CameraController  # noqa: E402
from runtime.config_manager import ConfigManager   # noqa: E402

RIG_SHEET = {"lens": "Tamron M118FM06 (6 mm)", "focal_mm": 6.0, "f_number": 1.4,
             "filter": "MidOpt BP850", "illuminator": "2x 850 nm 100 W",
             "illuminator_offset_cm": 40.0, "focus_m": 30.0, "camera_distance_m": 30.0,
             "camera_height_m": 4.0, "markers": "belt", "notes": "night 0710"}
SCENE = {"gamma": 0.8, "clahe_clip": 3.0, "mog2_var_threshold": 16.0, "mog2_scale": 0.5,
         "roi_enabled": True, "roi_x": 100, "roi_y": 50, "roi_w": 1200, "roi_h": 900,
         "roi_source_w": 1776, "roi_source_h": 1304, "exclusion_grid": [16, 10],
         "exclusion_cells": [[1, 1]], "sensitivity": 70.0, "confidence": 0.2,
         "calibration_state": {"gamma": {"by": "calib2"}}}


def _project(ratio=1.37, source="ids", rig=None, mirror=True):
    flat = dict(SCENE, camera_source=source, model="yolo11x-pose", yolo_imgsz=1280,
                use_tensorrt=True, ids_ratio=ratio, ids_gain_db=36.0,
                ids_exposure_us=25000.0, input_mirror=mirror, input_rotation=0,
                rig=dict(RIG_SHEET if rig is None else rig))
    return config_schema.structure(flat, {}, "show")


class FakeIds:
    """UnifiedCamera stand-in: the crop is whatever crop_ratio says at open()."""

    def __init__(self):
        self.crop_ratio = None
        self.is_open = False
        self.width = self.height = 0
        self.source_type = None
        self.opened = []
        self.calls = []

    def close(self):
        self.is_open = False

    def open(self, source):
        self.opened.append((source, self.crop_ratio))
        wide = (self.crop_ratio or cfgmod.IDS_RATIO) > 1.2
        self.width, self.height = (1776, 1304) if wide else (1488, 1528)
        self.source_type = CameraSource.IDS_PEAK
        self.is_open = True
        return True

    def update_crop_ratio(self, ratio):
        self.calls.append(("crop", ratio))
        return False                         # not streaming yet: the next open uses it

    def __getattr__(self, name):             # set_gain / set_exposure / *_auto / set_input_transform
        if name.startswith("set_"):
            return lambda *a: self.calls.append((name, *a))
        raise AttributeError(name)


def _session(tmp_path, *, current_source="0", camera_open=False):
    """ConfigManager wired to a real CameraController and the app's real
    _apply_config_without_model (on a duck-typed app)."""
    events, toasts = [], []
    store = ConfigStore(config_dir=str(tmp_path / "projects"),
                        last_project_file=str(tmp_path / "projects" / "last_project.txt"))
    uni = FakeIds()
    camera = SimpleNamespace(
        state=SimpleNamespace(is_open=camera_open, source=current_source, available=[],
                              unavailable=[], width=0, height=0),
        cap=None, set_input_transform=lambda t: None, close=lambda: None)
    cams = CameraController(camera=camera, unified_camera=uni, use_unified=True,
                            ui=SimpleNamespace(available=False),
                            preview_geometry=lambda w, h: events.append(("geometry", w, h)),
                            repush_preview_size=lambda: None, is_running=lambda: True)
    connect = cams._attempt_camera_connect

    def _connect(source, retry_on_fail=True):
        events.append(("connect", source))
        return connect(source, retry_on_fail)

    cams._attempt_camera_connect = _connect
    bus = api.EventBus()
    syncs = []
    bus.subscribe(syncs.append)
    app = SimpleNamespace(
        cameras=cams, camera=camera, unified_camera=uni, _use_unified_camera=True,
        rig_sheet=dict(cfgmod.RIG_DEFAULTS), input_transform=IDENTITY,
        recorder=SimpleNamespace(set_input_transform=lambda t: None, is_playing=False),
        recording=SimpleNamespace(_apply_playback_dimensions=lambda: None),
        calibration=SimpleNamespace(calibration_state={}),
        roi=SimpleNamespace(roi_edit_mode=True, _sync_roi_ui=lambda: None,
                            _set_roi_rect=lambda *a, **k: events.append(("roi", a))),
        processor=SimpleNamespace(bg_subtractor=SimpleNamespace(has_reference=False),
                                  set_motion_var_threshold=lambda v: events.append(("mog2", v))),
        settings=SimpleNamespace(bg_subtract_enabled=False),
        bus=bus,
        _camera_preview_geometry=lambda w, h: None,
        _request_reprocess=lambda: None,
        sensitivity=50.0, osc_enabled=False,
    )
    for name in ("_apply_config_without_model", "_set_input_transform",
                 "_sync_input_transform_ui", "_sync", "_sync_rig_sheet"):
        setattr(app, name, types.MethodType(getattr(WallDanceApp, name), app))
    applied = []

    def apply(cfg):
        applied.append(dict(cfg))
        app._apply_config_without_model(cfg)

    def load_default_model():
        events.append(("model_load",))
        return True

    mgr = ConfigManager(
        models=SimpleNamespace(_load_default_model_startup=load_default_model),
        cameras=cams, recording=None, recorder=None, camera=camera, unified_camera=uni,
        use_unified=True, settings=None,
        ui=SimpleNamespace(available=True,
                           show_toast=lambda m, duration, color: toasts.append(m)),
        watchdog=lambda: None, apply_config=apply, saveable_config=lambda: {},
        request_reprocess=lambda: None)
    mgr.config_store = store
    return SimpleNamespace(mgr=mgr, store=store, app=app, cams=cams, uni=uni,
                           events=events, toasts=toasts, applied=applied, syncs=syncs)


def test_blank_start_opens_the_ids_at_the_last_projects_crop(tmp_path, capsys):
    s = _session(tmp_path)
    s.store.save("mur30m-0710", _project())
    assert s.mgr._cb_project_blank()
    # the field bug: the IDS opened at crop 1.0 (portrait). Now: 1.37, one open
    assert s.uni.opened == [("ids", 1.37)]
    assert (s.uni.width, s.uni.height) == (1776, 1304)
    assert s.cams.ids_ratio == 1.37
    # after the model load (which pauses/reopens the current camera)
    assert s.events.index(("model_load",)) < s.events.index(("connect", "ids"))
    # exposure / gain re-applied on the open, mirror on
    assert ("set_exposure", 25000.0) in s.uni.calls and ("set_gain", 36.0) in s.uni.calls
    assert s.app.input_transform.mirror is True
    out = capsys.readouterr().out
    assert "Camera rig inherited from 'mur30m-0710'" in out and "crop 1.37" in out
    assert s.toasts and "mur30m-0710" in s.toasts[0] and "crop 1.37" in s.toasts[0]


def test_only_the_rig_is_inherited_not_the_scene(tmp_path):
    s = _session(tmp_path)
    s.store.save("mur30m-0710", _project())
    s.mgr._cb_project_blank()
    assert len(s.applied) == 1
    cfg = s.applied[0]
    # a PARTIAL apply (no model / camera_source key): nothing else is reset or set
    assert set(cfg) == {"ids_ratio", "ids_exposure_us", "ids_gain_db", "input_mirror",
                        "input_rotation", "rig"}
    for key in SCENE:
        assert key not in cfg, key
    assert ("roi",) not in [e[:1] for e in s.events] and not any(e[0] == "mog2" for e in s.events)
    assert s.app.calibration.calibration_state == {}
    # the on-camera rig sheet; distance / height / focus / markers / notes are per scene
    assert s.app.rig_sheet == {"lens": "Tamron M118FM06 (6 mm)", "focal_mm": 6.0,
                               "f_number": 1.4, "filter": "MidOpt BP850",
                               "illuminator": "2x 850 nm 100 W",
                               "illuminator_offset_cm": 40.0}
    synced = {e.name: e.value for e in s.syncs if isinstance(e, api.ControlSync)}
    assert synced["rig.lens"] == "Tamron M118FM06 (6 mm)" and synced["rig.markers"] is None
    assert synced["ids_ratio"] == 1.37


def test_the_last_project_pointer_wins_over_a_newer_save(tmp_path):
    s = _session(tmp_path)
    s.store.save("old-venue", _project(ratio=1.6))
    s.store.save("newer-save", _project(ratio=1.0))
    s.store.remember_last_project("old-venue")          # the one loaded last
    s.mgr._cb_project_blank()
    assert s.cams.ids_ratio == 1.6


def test_no_pointer_falls_back_to_the_most_recently_saved_project(tmp_path):
    s = _session(tmp_path)
    old = s.store.save("a-old", _project(ratio=1.6))
    past = time.time() - 3600
    os.utime(old, (past, past))
    s.store.save("b-new", _project(ratio=1.33))
    os.remove(s.store.last_project_file)
    s.mgr._cb_project_blank()
    assert s.cams.ids_ratio == 1.33


def test_an_unreadable_last_project_falls_through_to_the_next(tmp_path, capsys):
    s = _session(tmp_path)
    s.store.save("good", _project(ratio=1.33))
    bad_dir = tmp_path / "projects" / "broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / "broken_20261008_120000.json").write_text('{"ids_ratio": 1.')   # cut short
    s.store.remember_last_project("broken")
    s.mgr._cb_project_blank()
    assert s.cams.ids_ratio == 1.33
    assert "from 'good'" in capsys.readouterr().out


def test_no_previous_project_keeps_the_code_defaults(tmp_path, capsys):
    s = _session(tmp_path)
    assert s.mgr._cb_project_blank()
    assert s.applied == [] and s.toasts == []
    assert s.cams.ids_ratio == cfgmod.IDS_RATIO
    assert s.app.rig_sheet == {}
    assert ("connect", "ids") not in s.events
    assert "No previous project" in capsys.readouterr().out


def test_the_open_camera_is_not_reopened_when_the_source_matches(tmp_path):
    s = _session(tmp_path, current_source="ids", camera_open=True)
    s.store.save("p", _project())
    s.mgr._cb_project_blank()
    assert not any(e[0] == "connect" for e in s.events)
    assert ("crop", 1.37) in s.uni.calls                 # the live stream is re-cropped instead


def test_an_untouched_legacy_8mm_sheet_is_not_inherited_as_the_lens(tmp_path, capsys):
    s = _session(tmp_path)
    s.store.save("mur30m-0710", _project(rig=config_schema.LEGACY_RIG_PREFILL))
    s.mgr._cb_project_blank()
    assert s.app.rig_sheet == {"filter": "MidOpt BP850"}     # no 8 mm claim
    assert "old code pre-fill" in capsys.readouterr().out


def test_an_old_project_without_rig_keys_inherits_what_it_has(tmp_path):
    s = _session(tmp_path)
    s.store.save("kxkm", {"camera_source": "2", "confidence": 0.4})   # v1, webcam, no IDS keys
    s.mgr._cb_project_blank()
    assert s.applied == []                               # nothing but the camera source
    assert ("connect", "2") in s.events
    assert s.cams.ids_ratio == cfgmod.IDS_RATIO


# --------------------------------------------------------------------------- #
# The rig-sheet default and its validation (fix 2)
# --------------------------------------------------------------------------- #
def test_the_rig_sheet_default_claims_no_hardware():
    assert cfgmod.RIG_DEFAULTS == {}


def test_legacy_prefill_drop_is_exact_and_idempotent():
    flat, warnings = config_schema.validate_flat({"rig": dict(config_schema.LEGACY_RIG_PREFILL)})
    assert flat["rig"] == {"filter": "MidOpt BP850"} and len(warnings) == 1
    again, warnings = config_schema.validate_flat(flat)
    assert again["rig"] == {"filter": "MidOpt BP850"} and warnings == []
    # any operator entry: the sheet is data, left alone (even an 8 mm lens)
    edited = dict(config_schema.LEGACY_RIG_PREFILL, camera_distance_m=25.0)
    flat, warnings = config_schema.validate_flat({"rig": edited})
    assert flat["rig"] == edited and warnings == []
    six = {"lens": "Tamron M118FM06 (6 mm)", "focal_mm": 6.0}
    assert config_schema.validate_flat({"rig": six}) == ({"rig": six}, [])


def test_lens_and_focal_unknown_are_valid():
    assert config_schema.sanitize_rig({"lens": "", "focal_mm": 0}) == ({}, [])
    assert config_schema.sanitize_rig_value("focal_mm", None) == (None, None)


def test_ids_ratio_is_validated_on_load():
    flat, warnings = config_schema.validate_flat({"ids_ratio": "junk"})
    assert "ids_ratio" not in flat and warnings
    flat, warnings = config_schema.validate_flat({"ids_ratio": 3.0})
    assert flat["ids_ratio"] == 2.0 and warnings
    assert config_schema.validate_flat({"ids_ratio": 1.37}) == ({"ids_ratio": 1.37}, [])


def test_inherited_rig_subset():
    rig = config_schema.inherited_rig(config_schema.flatten(_project()))
    assert rig["camera_source"] == "ids" and rig["ids_ratio"] == 1.37
    assert rig["ids_exposure_us"] == 25000.0 and rig["ids_gain_db"] == 36.0
    assert set(rig["rig"]) == set(config_schema.RIG_SHEET_INHERIT_FIELDS)
    # no rig sheet in the project -> no "rig" key (the new project keeps its empty sheet)
    assert "rig" not in config_schema.inherited_rig({"ids_ratio": 1.37})
    assert json.dumps(rig)                               # plain data


# --- the picker's "New" button (2026-10-08): name it, defaults + last rig, saved at once ---

def _record_new_project_side_effects(s):
    saved, shown = [], []
    s.mgr._cb_do_save_config = lambda project: saved.append(project)
    s.mgr._show_startup_project_picker = lambda: shown.append(True)
    return saved, shown


def test_new_project_is_saved_under_its_name_with_the_last_rig(tmp_path):
    s = _session(tmp_path)
    s.store.save("mur30m-0710", _project())
    saved, shown = _record_new_project_side_effects(s)
    assert s.mgr._cb_project_new("show 0810")
    assert saved == ["show_0810"]                 # saved at once: never works in 'default'
    assert s.uni.opened == [("ids", 1.37)]         # the blank start's inherited crop
    assert ("model_load",) in s.events
    assert not shown


@pytest.mark.parametrize("name", ["mur30m-0710", "", "   ", "default"])
def test_new_project_refuses_a_taken_or_missing_name(tmp_path, name):
    s = _session(tmp_path)
    s.store.save("mur30m-0710", _project())
    saved, shown = _record_new_project_side_effects(s)
    assert not s.mgr._cb_project_new(name)
    assert saved == [] and ("model_load",) not in s.events
    assert shown == [True]                        # back to the picker
    assert s.toasts
