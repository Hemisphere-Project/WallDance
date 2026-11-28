"""A new project starts from the code defaults, not from the previous project's session
(A2, #t-023 follow-up; found by two reviews, 27/11).

« New project » and « Start blank » (ConfigManager._cb_project_new / _cb_project_blank) used to
apply only the camera rig of the last project (a partial apply): every other project key kept
the value the session held, and the new project's first Save wrote it -- a remote support line
left on, a camera link throttled at 124 MB/s, the previous project's model, ROI, OSC target...

The rule now: a new project carries ON PURPOSE a short list of keys
(config_schema.NEW_PROJECT_CARRIED_KEYS, from the last used project: the camera and how it is
mounted, and -- the owner, 27/11 -- the venue's set-up: where the visuals listen, osc_ip/osc_port,
and the camera link's limit, ids_throughput_limit_mbps) and starts from the CODE DEFAULT for every
other key: the app's own startup state (what a fresh launch saves), captured by ConfigManager
before any project applies.  The remote support line is not venue set-up: it is reset.

The REAL path, headless: a real WallDanceApp (CPU: CUDA hidden, no model load, no camera open),
the real ConfigManager, its real project switch to load the previous project, the app's real
apply and its real saveable config (what « the running app » holds and what Save writes).
Stubbed: the model load (no GPU), the TensorRT probe, the camera connect (no camera).

Set WD_A2_TABLE=<path> to write the key-by-key table (default / previous / after) as JSON.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("torch")
from core import config_schema                     # noqa: E402
from core.config_store import ConfigStore           # noqa: E402

# The rig a new project carries on purpose (the named list beside the schema; the test falls back
# to the rig the code inherited before that list had a name, so it runs on older code too).
# The venue's set-up a new project carries (the owner, 27/11), at values away from the defaults.
VENUE = {"osc_ip": "10.0.0.7", "osc_port": 9100, "ids_throughput_limit_mbps": 124.0}
CARRIED = tuple(getattr(config_schema, "NEW_PROJECT_CARRIED_KEYS",
                        (*config_schema.RIG_INHERIT_KEYS, "rig", *VENUE)))

# Saved but not a setting of the project: the preview's render scale is computed from the window
# layout (app._apply_config_without_model ignores it on load), so no project sets or keeps it.
NOT_A_PROJECT_SETTING = ("preview_scale",)

# The previous project: every key moved away from its code default to a valid value.
MOVED = {
    "bg_subtract_enabled": True, "bg_subtract_sensitivity": 55, "blur_budget_ms": 40.0,
    "brightness_threshold": 90, "calibration_state": {"gamma": {"by": "test", "at": "2026-11-27"}},
    "camera_stall_hold": False, "clahe_clip": 4.5, "coast_s": 3.5, "confidence": 0.4,
    "denoise_strength": 0.5, "enhance_enabled": False, "enhance_force": False, "enhance_lite": True,
    "exclusion_cells": [[1, 2], [3, 4]], "exclusion_grid": [8, 5], "exclusion_manual_add": [[0, 0]],
    "exclusion_manual_remove": [[5, 4]], "fg_clip_aware": False, "fg_enabled": False, "fp16": False,
    "gamma": 1.8, "greyscale": True, "identity_slots_enabled": False,
    "input_fps_cap": True, "max_dancers": 3, "max_persons": 4,
    "model": "yolo11l-pose", "mog2_scale": 0.75, "mog2_var_threshold": 20.0,
    "motion_sensitivity": 0.3, "osc_enabled": False, "osc_send_state": True, "person_height_max_ratio": 3.0, "person_height_min_ratio": 0.4,
    "person_height_px": 260, "preview_enabled": False, "preview_fps_cap": True, "preview_scale": 0.5,
    "roi_enabled": True, "roi_x": 100, "roi_y": 50, "roi_w": 1200, "roi_h": 800,
    "sensitivity": 70.0, "sensitivity_conf_seed": 0.4, "sensitivity_var_anchor": 20.0,
    "show_ball": False, "show_bbox": False, "show_ids": False, "show_keypoints": True,
    "show_skeleton": False, "show_trails": True, "slot_filter_input": "raw", "smart_hold": False,
    "stability": 1.0, "static_ghost_guard": False, "static_release_s": 5.0, "support_line": True,
    "swipe_auto": "off", "swipe_collect": False, "tracker_ghost_skeleton_age": 6,
    "tracker_intermittent_confirm": False,
    "tracker_max_age": 90, "tracker_smoothing": 3, "tracking_mode": "motion_first",
    "use_ir_belt": False, "use_tensorrt": False, "yolo_imgsz": 960, "yolo_shape": "1280x736",
}
# ... and its rig (what stays mounted between scenes).
RIG = {"camera_source": "ids", "ids_ratio": 1.37, "ids_exposure_us": 25000.0, "ids_gain_db": 12.0,
       "input_mirror": True, "input_rotation": 180,
       "rig": {"lens": "Tamron M118FM06 (6 mm)", "focal_mm": 6.0, "filter": "MidOpt BP850",
               "camera_distance_m": 30.0, "markers": "4 cuffs"}}
# The on-camera part of the rig sheet is carried; where the camera stands and what the dancers
# wear are per scene (config_schema.RIG_SHEET_INHERIT_FIELDS).
RIG_CARRIED = dict(RIG, rig={"lens": "Tamron M118FM06 (6 mm)", "focal_mm": 6.0,
                             "filter": "MidOpt BP850"})


def _moved(key, default):
    """A valid value away from the default: the table above, else by type (a key added to the
    schema later is moved too: a bool flips, a number moves, a string changes)."""
    if key in MOVED:
        return MOVED[key]
    if isinstance(default, bool):
        return not default
    if isinstance(default, (int, float)):
        return type(default)(default + 1)
    if isinstance(default, str):
        return default + "-moved"
    return default


def _norm(value):
    """Compare as the file does (tuples are lists in JSON)."""
    return json.loads(json.dumps(value))


def _app(tmp_path, monkeypatch):
    """A real WallDanceApp, headless and on the CPU, at its startup state (the picker shown)."""
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)     # no GPU
    from app import WallDanceApp
    from core import model_manager
    monkeypatch.setattr(model_manager, "is_tensorrt_available", lambda: False)   # no TRT prompt
    import runtime.config_manager as config_manager
    # Never the real projects folder (t-060): the app's store and its recorder's project folders
    # (a save or a switch creates <project>/recordings) live in tmp_path.
    store = ConfigStore(config_dir=str(tmp_path / "projects"),
                        last_project_file=str(tmp_path / "projects" / "last_project.txt"))
    monkeypatch.setattr(config_manager, "ConfigStore", lambda *a, **k: store)
    app = WallDanceApp()
    assert app.configs.config_store is store
    app.recorder.projects_dir = str(tmp_path / "projects")
    loads, connects, limits = [], [], []

    def load(name, force_pt=False):                                     # no model, no GPU
        loads.append((name, force_pt))
        app.models.current_model_name = name.replace(".pt", "").replace(".engine", "")  # as it does
        return True

    def connect(source, retry_on_fail=True):                            # no camera
        connects.append(source)
        limits.append(app.cameras.ids_throughput_limit_mbps)   # what this open hands the camera
        app.camera.state.source = source
        return True

    app.models._load_model_with_progress = load
    app.cameras._attempt_camera_connect = connect
    # P6: a new project starts the automatic swipe at the default (suggest): its mode is the app's, its looker
    # thread is not started here -- nothing in these tests looks at the wall
    app.swipe_bg.start = lambda: None
    # The startup door: the picker shows before any project applies (main_loop's startup).
    app.configs._show_startup_project_picker()
    return SimpleNamespace(app=app, mgr=app.configs, store=app.configs.config_store,
                           loads=loads, connects=connects, limits=limits)


def _drive(tmp_path, monkeypatch, door, extra=None, switch=True, via_api=False):
    """Startup -> the previous project loaded through the real switch -> New project / Start
    blank (+ its first Save) -> (startup, previous, after-in-the-app, after-in-the-file).
    switch=False: the previous project is only on disk, the last used one (the launch door).
    via_api=True: New / Start blank as the picker's buttons send them (ui.adapter submits the
    command; the app's handler runs the manager's door, which sets P6's mode itself)."""
    s = _app(tmp_path, monkeypatch)
    startup = _norm(s.app._get_saveable_config())
    previous = {k: _moved(k, v) for k, v in startup.items() if k not in CARRIED}
    previous.update(RIG, **VENUE, **(extra or {}))
    path = s.store.save("previous", config_schema.structure(previous, {}, "show"))
    if switch:
        assert s.mgr._execute_project_switch(path)
        # fp16 is saved but no project load applies it: the session holds it (moved here by hand)
        s.app.settings.use_fp16 = previous["fp16"]
    else:
        s.store.remember_last_project("previous")
    held = _norm(s.app._get_saveable_config())
    opens_before = len(s.connects)
    if via_api:
        from runtime import api
        s.app.api.submit(api.NewProject(name="fresh") if door == "new" else api.StartBlankProject())
        s.app.api.drain()
        if door == "blank":
            s.mgr._cb_do_save_config("fresh")                           # the first Save
    elif door == "new":
        assert s.mgr._cb_project_new("fresh")
    else:
        assert s.mgr._cb_project_blank()
        s.mgr._cb_do_save_config("fresh")                               # the first Save
    live = _norm(s.app._get_saveable_config())
    saved_path = s.store.latest_for_project("fresh")
    assert saved_path and os.path.dirname(os.path.dirname(saved_path)).startswith(str(tmp_path))
    with open(saved_path) as f:
        saved = _norm(config_schema.flatten(json.load(f)))
    return SimpleNamespace(s=s, startup=startup, default=_new_default(startup), previous=_norm(previous),
                           held=held, live=live, saved=saved, opens_before=opens_before)


def _new_default(startup):
    """What a new project starts from: the startup state, but the automatic swipe at the build's default
    (P6, 28/11: core.swipe_auto.default_mode) -- the startup state holds off, the mode before any project
    applies, and a new project does not start from that one."""
    out = dict(startup)
    if "swipe_auto" in out:
        from core.swipe_auto import default_mode
        out["swipe_auto"] = default_mode()
    return out


def _table(d, door):
    out = {}
    for key in sorted(d.startup):
        if key in NOT_A_PROJECT_SETTING:
            continue
        out[key] = {"rig": key in CARRIED, "default": d.default[key], "previous": d.held.get(key),
                    "after": d.live.get(key), "saved": d.saved.get(key),
                    "verdict": ("not moved by the previous project" if d.held.get(key) == d.default[key]
                                else "reset to its default" if d.live.get(key) == d.default[key]
                                and d.saved.get(key) == d.default[key]
                                else "inherited from the session")}
    target = os.environ.get("WD_A2_TABLE")
    if target:
        try:
            with open(target) as f:
                both = json.load(f)
        except (OSError, ValueError):
            both = {}
        both[door] = out
        with open(target, "w") as f:
            json.dump(both, f, indent=1, sort_keys=True)
    return out


def _check(d, door, own=()):
    """``own``: keys the caller checks itself (left out of the two checks on the previous project)."""
    table = _table(d, door)
    # the previous project really moved the session (else the check below proves nothing)
    unmoved = [k for k, row in table.items() if not row["rig"] and k not in own
               and row["verdict"] == "not moved by the previous project"]
    assert unmoved == [], f"the previous project did not move {unmoved}"
    inherited = {k: (row["previous"], row["after"], row["saved"]) for k, row in table.items()
                 if not row["rig"] and k not in own and row["verdict"] != "reset to its default"}
    assert inherited == {}, f"{door}: keys kept from the previous project's session: {inherited}"
    # the rig and the venue on the named list are carried, in the app and in the file
    for key, value in {**RIG_CARRIED, **VENUE}.items():
        assert d.live[key] == _norm(value), (door, key, d.live[key])
        assert d.saved[key] == _norm(value), (door, key, d.saved[key])
    return table


# --------------------------------------------------------------------------- #
# (2) the real path: after Start blank and after New project
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("door", ["blank", "new"])
def test_a_new_project_starts_from_the_code_defaults_and_carries_only_the_rig(
        tmp_path, monkeypatch, door):
    d = _drive(tmp_path, monkeypatch, door)
    _check(d, door)
    app = d.s.app
    # the two keys the reviews met, in the running app itself: the support line is reset, the
    # camera link's limit is the venue's (carried, the owner 27/11) -- and so is the OSC target
    assert d.startup["support_line"] is False and d.held["support_line"] is True
    assert app.support_line.enabled is False and d.saved["support_line"] is False
    _venue_carried(d)
    # the default model, at the default size and intent (D33: x @ 1280 on TensorRT)
    assert d.saved["model"] == "yolo11x-pose" and d.saved["yolo_imgsz"] == 1280
    assert d.saved["use_tensorrt"] is True and d.saved["yolo_shape"] == ""
    assert app.settings.imgsz == 1280 and app.models.model_manager.shape is None
    # the inherited camera opened once, after the model load
    assert d.s.connects[-1] == "ids"


def _venue_carried(d):
    """The venue's set-up (VENUE, away from the defaults) in the running app, in the saved file
    and at the camera open that follows: the camera link's limit is handed to that open."""
    app = d.s.app
    assert {k: d.startup[k] for k in VENUE} == {"osc_ip": "127.0.0.1", "osc_port": 9000,
                                                "ids_throughput_limit_mbps": 0.0}
    assert (app.osc_ip, app.osc_port, app.cameras.ids_throughput_limit_mbps) == ("10.0.0.7", 9100, 124.0)
    assert {k: d.saved[k] for k in VENUE} == VENUE and {k: d.live[k] for k in VENUE} == VENUE
    assert len(d.s.connects) > d.opens_before and d.s.limits[d.opens_before:] == [124.0] * (
        len(d.s.connects) - d.opens_before)                  # the new project's own opens


@pytest.mark.parametrize("door", ["blank", "new"])
def test_at_launch_a_new_project_carries_the_last_projects_rig_and_venue(tmp_path, monkeypatch, door):
    """The door the GUI reaches today: the launch picker, before any project applies (the session is
    the startup state).  The carried keys come from the last used project on disk, every other key
    is the startup state's -- the automatic swipe the build's default (P6)."""
    d = _drive(tmp_path, monkeypatch, door, switch=False)
    assert d.s.loads and d.s.connects[-1] == "ids"
    for key, value in {**RIG_CARRIED, **VENUE}.items():
        assert d.live[key] == _norm(value) and d.saved[key] == _norm(value), (door, key)
    others = {k: (d.live.get(k), d.saved.get(k)) for k in d.startup
              if k not in CARRIED and k not in NOT_A_PROJECT_SETTING
              and not d.live.get(k) == d.saved.get(k) == d.default[k]}
    assert others == {}, f"{door} at launch: keys off their startup state: {others}"
    _venue_carried(d)


def test_the_rig_list_is_short_and_named():
    assert set(CARRIED) == {"camera_source", "ids_ratio", "ids_exposure_us", "ids_gain_db",
                            "input_mirror", "input_rotation", "rig",
                            "osc_ip", "osc_port", "ids_throughput_limit_mbps"}
    assert "support_line" not in CARRIED          # a remote support line is not venue set-up


# --------------------------------------------------------------------------- #
# P6 (« suggest » released 27/11, the default since 28/11, « switch » locked): a new project starts
# with the automatic swipe at the build's default -- core.swipe_auto.default_mode: suggest while the
# build releases it, else off -- whatever the last project had and whatever this build releases, and
# its first Save writes the key.  The startup state holds swipe_auto: off (the looker is built off:
# nothing of it runs before a project applies); a new project does not start from that one.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("released", [None, ("off",), ("off", "suggest")],
                         ids=["shipped", "off-only", "off+suggest"])
@pytest.mark.parametrize("last", ["off", "suggest", "auto"])     # the last project's key: off, suggest, switch
@pytest.mark.parametrize("door", ["blank", "new"])
def test_a_new_project_has_the_automatic_swipe_at_the_default_and_writes_it(tmp_path, monkeypatch, door,
                                                                            last, released):
    pytest.importorskip("core.swipe_auto")                  # only a tree without P6 at all skips
    from core import config as core_config
    if released is not None:                                # else the list the build ships
        monkeypatch.setattr(core_config, "SWIPE_AUTO_RELEASED", released)
    held_mode = last if last in core_config.SWIPE_AUTO_RELEASED else "off"   # what a load accepts
    default = "suggest" if "suggest" in core_config.SWIPE_AUTO_RELEASED else "off"
    # the picker's door (ui.adapter's command; the manager's own door sets the mode)
    d = _drive(tmp_path, monkeypatch, door, extra={"swipe_auto": last}, via_api=True)
    try:
        assert d.startup.get("swipe_auto", "off") == "off"   # before any project: off, nothing runs
        # the previous project really set the session's mode (else the check below proves nothing)
        assert d.held.get("swipe_auto", "off") == held_mode, (door, last, released, d.held.get("swipe_auto"))
        assert d.s.app.swipe_bg.mode == default, \
            f"the new project starts {d.s.app.swipe_bg.mode}, not at the default {default}"
        assert d.live.get("swipe_auto") == default and d.saved.get("swipe_auto") == default, \
            f"the new project's first Save wrote {d.saved.get('swipe_auto', 'no key')}, not {default}"
        _check(d, door + "+swipe", own=("swipe_auto",))
    finally:
        d.s.app.swipe_bg.stop(1.0)
        d.s.app.swipe_switch.stop(1.0)


# --------------------------------------------------------------------------- #
# (3) generic: a key added to the schema later is reset with no change here
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("door", ["blank", "new"])
def test_a_key_added_to_the_schema_later_is_reset_too(tmp_path, monkeypatch, door):
    """A throw-away project key, wired as a new key is (saved by the app, applied when present),
    off by default: a new project starts with it off."""
    from app import WallDanceApp
    save, apply = WallDanceApp._get_saveable_config, WallDanceApp._apply_config_without_model

    def saveable(self):
        return dict(save(self), zz_throwaway_auto=getattr(self, "_zz_throwaway_auto", False))

    def applier(self, config):
        apply(self, config)
        if "zz_throwaway_auto" in config:
            self._zz_throwaway_auto = bool(config["zz_throwaway_auto"])

    monkeypatch.setattr(WallDanceApp, "_get_saveable_config", saveable)
    monkeypatch.setattr(WallDanceApp, "_apply_config_without_model", applier)
    d = _drive(tmp_path, monkeypatch, door)
    assert d.startup["zz_throwaway_auto"] is False and d.held["zz_throwaway_auto"] is True
    assert d.s.app._zz_throwaway_auto is False
    assert d.saved["zz_throwaway_auto"] is False
    _check(d, door + "+throwaway")       # its own table row set (WD_A2_TABLE)
