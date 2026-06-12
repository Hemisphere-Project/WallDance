"""Tests for the Calibrate All wizard state machine (ui/wizard_state.py).

Headless: the state machine is renderer-free by design (the tablet client
reuses it), so these tests never touch dpg. Also includes the house-style
static check that every command the wizard can emit exists in runtime.api
and is registered in app.py.
"""
import pathlib
import re

from runtime import api
from ui import wizard_state as ws
from ui.wizard_state import WizardState

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


def _types(cmds):
    return [type(c) for c in cmds]


# ======================================================================
# Happy path: scene → report → dancers → pool → apply → save
# ======================================================================

def test_full_happy_path():
    w = WizardState()
    assert not w.active
    assert w.open() == []
    assert w.step == ws.INTRO

    assert _types(w.start_scene()) == [api.StartCalibration]
    assert w.step == ws.SCENE_RUNNING

    w.on_progress("Calibrating exposure...")
    w.on_progress("Calibrating 60%")
    assert w.progress_text == "Calibrating 60%"

    # success: flows publish CalibProgress(None) then the report card,
    # back-to-back within one drain
    w.on_progress(None)
    assert w.step == ws.SCENE_ENDED
    assert w.on_report_card("median height 212px") is True
    assert w.step == ws.SCENE_REPORT
    assert w.scene_summary == "median height 212px"

    assert w.continue_to_dancers() == []
    assert w.step == ws.DANCERS_READY

    assert _types(w.start_dancers()) == [api.StartDancersRun]
    assert w.step == ws.DANCERS_RUNNING

    w.on_progress("Dancers 40%")
    w.on_progress(None)
    assert w.step == ws.DANCERS_ENDED
    rows = [{"path": "a.json", "label": "run A", "stale": False},
            {"path": "b.json", "label": "run B", "stale": True}]
    assert w.on_pool_changed(rows, "height 210px") is True
    assert w.step == ws.POOL_REVIEW
    assert w.default_selection() == ["a.json"]

    cmds = w.apply_pool(["a.json"])
    assert _types(cmds) == [api.ApplyCalib2]
    assert cmds[0].selection == ["a.json"]
    assert w.step == ws.POOL_REVIEW          # holds until the applied card

    assert w.on_report_card("Dancer calibration (pooled)") is True
    assert w.step == ws.APPLIED
    assert w.applied_summary == "Dancer calibration (pooled)"

    assert _types(w.save_project()) == [api.SaveConfig]
    assert w.step == ws.CLOSED
    assert not w.active


# ======================================================================
# Routing: an inactive wizard consumes nothing (classic dialogs keep working)
# ======================================================================

def test_inactive_wizard_consumes_nothing():
    w = WizardState()
    assert w.on_report_card("x") is False
    assert w.on_pool_changed([], "p") is False
    w.on_progress("Calibrating 10%")     # no crash, no state
    assert not w.active


def test_intro_does_not_consume_foreign_events():
    """A calibration started from the classic buttons while the wizard sits
    on the intro step still gets the classic dialogs."""
    w = WizardState()
    w.open()
    assert w.on_report_card("classic calib1") is False
    assert w.on_pool_changed([], "classic calib2") is False


# ======================================================================
# Cancel / ended-without-result paths
# ======================================================================

def test_cancel_scene_run_toggles_and_returns_to_intro():
    w = WizardState()
    w.open()
    w.start_scene()
    assert _types(w.cancel_scene_run()) == [api.StartCalibration]
    assert w.step == ws.INTRO
    # restart works
    assert _types(w.start_scene()) == [api.StartCalibration]


def test_scene_run_ended_without_report_allows_retry():
    w = WizardState()
    w.open()
    w.start_scene()
    w.on_progress(None)                 # cancelled/stalled: nothing follows
    assert w.step == ws.SCENE_ENDED
    assert _types(w.start_scene()) == [api.StartCalibration]
    assert w.step == ws.SCENE_RUNNING


def test_cancel_dancers_run_returns_to_ready():
    w = WizardState()
    w.open()
    w.start_scene()
    w.on_progress(None)
    w.on_report_card("ok")
    w.continue_to_dancers()
    w.start_dancers()
    assert _types(w.cancel_dancers_run()) == [api.StartDancersRun]
    assert w.step == ws.DANCERS_READY


def test_close_mid_run_emits_toggle_cancel():
    w = WizardState()
    w.open()
    w.start_scene()
    assert _types(w.close()) == [api.StartCalibration]
    assert not w.active
    w.open()
    w.start_scene()
    w.on_progress(None)
    w.on_report_card("ok")
    w.continue_to_dancers()
    w.start_dancers()
    assert _types(w.close()) == [api.StartDancersRun]
    assert not w.active


def test_close_when_idle_emits_nothing():
    w = WizardState()
    w.open()
    assert w.close() == []


# ======================================================================
# Pool review behaviors
# ======================================================================

def _to_pool_review(w, rows=None):
    w.open()
    w.start_scene()
    w.on_progress(None)
    w.on_report_card("scene ok")
    w.continue_to_dancers()
    w.start_dancers()
    w.on_progress(None)
    w.on_pool_changed(rows or [{"path": "a", "label": "A", "stale": False}], "prop")


def test_add_another_run_loops_back():
    w = WizardState()
    _to_pool_review(w)
    assert w.add_another_run() == []
    assert w.step == ws.DANCERS_READY
    w.start_dancers()
    w.on_progress(None)
    rows2 = [{"path": "a", "label": "A", "stale": False},
             {"path": "b", "label": "B", "stale": False}]
    assert w.on_pool_changed(rows2, "prop2") is True
    assert w.step == ws.POOL_REVIEW
    assert w.default_selection() == ["a", "b"]


def test_rejected_apply_keeps_pool_review():
    """A not-ok proposal only toasts (no report card): the wizard must stay
    on the review step so the selection can change."""
    w = WizardState()
    _to_pool_review(w)
    w.apply_pool(["a"])
    assert w.step == ws.POOL_REVIEW
    # no report card arrives; operator tweaks selection and retries
    cmds = w.apply_pool(["a"])
    assert _types(cmds) == [api.ApplyCalib2]


def test_skip_apply_reaches_summary_then_save():
    w = WizardState()
    _to_pool_review(w)
    assert w.skip_apply() == []
    assert w.step == ws.APPLIED
    assert w.applied_summary is None
    assert _types(w.save_project()) == [api.SaveConfig]


def test_keep_session_closes_without_commands():
    w = WizardState()
    _to_pool_review(w)
    w.skip_apply()
    assert w.keep_session() == []
    assert not w.active


def test_save_from_scene_report_skips_dancers():
    """Saving straight after the scene report is a legal short flow."""
    w = WizardState()
    w.open()
    w.start_scene()
    w.on_progress(None)
    w.on_report_card("scene ok")
    assert _types(w.save_project()) == [api.SaveConfig]
    assert not w.active


# ======================================================================
# Guard rails: actions outside their step are no-ops
# ======================================================================

def test_out_of_step_actions_are_noops():
    w = WizardState()
    w.open()
    assert w.continue_to_dancers() == []
    assert w.start_dancers() == []
    assert w.apply_pool(["x"]) == []
    assert w.skip_apply() == []
    assert w.save_project() == []
    assert w.cancel_scene_run() == []
    assert w.cancel_dancers_run() == []
    assert w.add_another_run() == []
    assert w.step == ws.INTRO


# ======================================================================
# Static: every command the wizard emits exists and is registered in app.py
# (house style, mirrors test_runtime_api.test_adapter_commands_exist...)
# ======================================================================

def test_wizard_commands_exist_and_are_registered():
    wiz_src = (SRC / "ui" / "wizard_state.py").read_text(encoding="utf-8")
    app_src = (SRC / "app.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r"(?<![.\w])api\.(\w+)\(", wiz_src))
    assert emitted, "wizard_state.py should emit seam commands"
    for name in sorted(emitted):
        assert hasattr(api, name), f"wizard emits api.{name} which does not exist"
        cls = getattr(api, name)
        assert issubclass(cls, api.Command)
        assert re.search(rf"api\.{name}\b", app_src), (
            f"wizard emits {name} but app.py never registers it")
