"""X4h (hub walldance#t-055, follow-up seen on the laptop 27/11): the dancer step and the band never measure against
an empty-wall picture that is not this view.

On the show laptop on 27/11 the project's empty-wall picture was not of the view (readiness: « FAIL empty wall: the
empty-wall picture is not this wall ... »); the step started anyway and ended « someone moves on the wall but the
detector saw her in 0 of 200 frames » with nobody there, and the band read « dancer ~637 px » with no dancer.

- the step does not start while the reference watch says the picture is the cause (not this wall, moved, the
  referee's refusals, too dark) or the camera no longer fits it (X5's hand change, X6's exposure rule): one toast,
  the Calibrate page's status line the same, the remote's events the same, nothing changed;
- the cause appearing while it measures ends it as a cancel (everything put back) with one line;
- the band says « not measured » with the reason, keeps nothing measured in that state, and measures again once the
  picture lines up;
- a good picture changes nothing (the X4 numbers), and no picture at all is as before: the step and the band measure
  on the boxes alone (``test_no_empty_wall_picture_at_all_is_as_before``).

The real CalibrationFlows on X4's own harness (test_x4_dancer_step), a real plate and the real ReferenceWatch with
real checks (test_h4_reference_watch's synthetic wall), the app's real band tick and UI adapter. CPU only.
"""
import copy
import dataclasses
import time
from types import SimpleNamespace

import numpy as np
import pytest

import test_h4_reference_watch as H4                 # the synthetic wall and its frames
import test_x4_dancer_step as X4                     # the step's harness (the real CalibrationFlows on stubs)
from core import reference_watch as RW
from core import dancer_step as DS                    # X4h's names read in the tests: the module imports on
                                                      # a code root without them (the no-picture pin runs there)
from core.foreground import CleanPlate
from runtime import api

NOT_THIS_WALL = ("A dancer, 10 s: not started, the empty-wall picture is not this wall - CALIBRATE on the empty "
                 "wall (2 Calibrate) first, then A DANCER, 10 s")
MOVED_LINE = ("A dancer, 10 s: not started, the camera moved since the empty-wall picture - check the zone and the "
              "mask (1 Rig), then CALIBRATE on the empty wall (2 Calibrate), then A DANCER, 10 s")
EXPOSURE_LINE = ("A dancer, 10 s: not started, the empty-wall picture no longer fits the camera (plate taken at "
                 "25.0 ms, the camera is at 8.8 ms) - CALIBRATE on the empty wall (2 Calibrate) first, then A DANCER, "
                 "10 s")
STOPPED_LINE = ("A dancer, 10 s: stopped, the empty-wall picture is not this wall; everything put back - CALIBRATE "
                "on the empty wall (2 Calibrate) first, then A DANCER, 10 s")
BAND_LINE = "dancer at the network: not measured (the empty-wall picture is not this wall)"
PLATE_THEN = "CALIBRATE on the empty wall (2 Calibrate) first, then A DANCER, 10 s"
PLATE_THEN_DARK = ("light the wall as for the show, then CALIBRATE on the empty wall (2 Calibrate), then "
                   "A DANCER, 10 s")


@pytest.fixture(scope="module")
def wall():
    sc = H4._scene(0)
    plate = CleanPlate.from_frames([H4._frame(sc, seed=k) for k in range(9)], source="test")
    plate.created = time.strftime("%Y-%m-%dT%H:%M:%S")
    return SimpleNamespace(scene=sc, plate=plate,
                           same=H4._frame(sc, seed=65),
                           other=H4._frame(H4._scene(1), seed=57),
                           moved=H4._frame(sc, H4._shift(6.0, 0.0), seed=66))


def _watch(wall, frame, plate=None):
    """The real watch, its verdict on ``plate`` from two real checks against ``frame`` (an alert is shown on its
    second check, a match at once)."""
    w = RW.ReferenceWatch(run_async=False)
    plate = wall.plate if plate is None else plate
    w.submit(0.0, plate, frame, H4.ROI)
    w.submit(RW.CONFIRM_S, plate, frame, H4.ROI)
    return w


def _on_plate(flows, proc, watch, plate):
    """The flows' processor carries this plate (FrameProcessor._fg_detector.plate) and the app's watch."""
    proc._fg_detector = SimpleNamespace(plate=plate)
    proc._plate_invalid = None
    flows._reference_watch = watch


def _state(flows, settings):
    return (settings.person_height_px, settings.enhance_enabled, flows.enhancer.gamma, flows.enhancer.clahe_clip,
            copy.deepcopy(flows.calibration_state))


def _refused_and_nothing_changed(flows, proc, settings, before, line):
    assert not flows._calibrating and not flows.dancer_busy and flows._dancer is None
    assert flows.ui.show_toast.call_count == 1 and flows.ui.show_toast.call_args[0][0] == line
    flows.ui.set_calibrate_note.assert_called_once_with(line)             # the Calibrate page's line: the same,
    flows.ui.set_calibrate_status.assert_not_called()                     # a note: its button stays CALIBRATE
    assert _state(flows, settings) == before                              # nothing changed
    flows.ui.sync_slider.assert_not_called()
    flows.ui.sync_checkbox.assert_not_called()
    assert X4._main_loop(flows, proc, settings) == 0                      # no frame is measured


# ------------------------------------------------------------------------------------------ the step refuses
def test_the_step_does_not_start_on_a_picture_that_is_not_this_wall(wall, capsys):
    watch = _watch(wall, wall.other)
    assert watch.current(wall.plate).status == RW.OTHER_WALL               # the laptop's readiness of 27/11
    flows, proc, settings = X4._flows(X4._helps)
    _on_plate(flows, proc, watch, wall.plate)
    before = _state(flows, settings)
    flows._cb_dancer_step()
    _refused_and_nothing_changed(flows, proc, settings, before, NOT_THIS_WALL)
    assert NOT_THIS_WALL in capsys.readouterr().out


def test_the_step_does_not_start_on_a_picture_the_camera_moved_from(wall):
    watch = _watch(wall, wall.moved)
    assert watch.current(wall.plate).status == RW.MOVED
    flows, proc, settings = X4._flows(X4._helps)
    _on_plate(flows, proc, watch, wall.plate)
    before = _state(flows, settings)
    flows._cb_dancer_step()
    _refused_and_nothing_changed(flows, proc, settings, before, MOVED_LINE)


def test_the_step_does_not_start_on_a_picture_of_another_exposure(wall):
    """X6's rule (CleanPlate.camera_mismatch, the one RUN applies) and X5's hand change: the same refusal."""
    plate = dataclasses.replace(wall.plate, camera={"exposure_us": 25000.0, "gain_db": 0.0})
    watch = _watch(wall, wall.same, plate)
    assert watch.current(plate).status == RW.MATCHES                     # it lines up: the exposure alone refuses
    flows, proc, settings = X4._flows(X4._helps)
    _on_plate(flows, proc, watch, plate)
    proc._camera_state = lambda: (8800.0, 0.0)
    before = _state(flows, settings)
    flows._cb_dancer_step()
    _refused_and_nothing_changed(flows, proc, settings, before, EXPOSURE_LINE)
    flows, proc, settings = X4._flows(X4._helps)
    _on_plate(flows, proc, watch, plate)
    proc._plate_invalid = "exposure changed by hand - take the empty wall again"     # app._on_camera_changed_by_hand
    before = _state(flows, settings)
    flows._cb_dancer_step()
    _refused_and_nothing_changed(flows, proc, settings, before,
                                 "A dancer, 10 s: not started, the empty-wall picture no longer fits the camera "
                                 "(exposure changed by hand) - " + PLATE_THEN)


def test_an_open_card_is_left_as_it_is_by_a_refused_press(wall):
    flows, proc, settings = X4._flows(X4._helps)
    flows._cb_dancer_step()
    X4._main_loop(flows, proc, settings)
    assert flows.dancer_busy and flows._dancer_pending is not None        # measured on a good picture: the card
    pending, held = flows._dancer_pending, _state(flows, settings)
    _on_plate(flows, proc, _watch(wall, wall.other), wall.plate)
    flows._cb_dancer_step()                                               # pressed again: refused, nothing changes
    assert flows._dancer_pending is pending and _state(flows, settings) == held and not flows._calibrating


@pytest.mark.parametrize("cause", ["refused", "dark"])
def test_the_watchs_other_plate_alerts_refuse_too(wall, cause):
    if cause == "refused":                                                # the swipe's referee, three times
        plate, watch = wall.plate, _watch(wall, wall.same)
        for _ in range(RW.REFUSALS_ALERT):
            watch.note_referee_refusal("no group under her")
        why, then = "the referee refused the empty-wall picture", PLATE_THEN
    else:                                                                 # the 14/10 10:02 plate
        plate = H4._dark(wall.plate)
        watch = RW.ReferenceWatch(run_async=False)
        assert watch.note_plate(plate, H4.ROI) is not None
        why, then = "the empty-wall picture is too dark for the foreground", PLATE_THEN_DARK
    proc = SimpleNamespace(_fg_detector=SimpleNamespace(plate=plate), _plate_invalid=None,
                           settings=SimpleNamespace(fg_enabled=True))
    c = DS.plate_cause(proc, watch)
    assert (c.why, c.then) == (why, then)
    proc.settings.fg_enabled = False                                      # the foreground off: nothing measured on it
    assert DS.plate_cause(proc, watch) is None


def test_the_remote_start_gets_the_same_refusal_as_its_events(wall):
    """StartDancerStep through the remote API, the app's runtime registration and its CalibrationUiPort: the remote
    client reads the refusal in the event ring (the toast and the Calibrate status line), nothing changed."""
    appmod = X4._app_mod()
    from services.remote_api import RemoteApi
    bus, rt = api.EventBus(), api.RuntimeAPI()
    bus.ui_ready = True
    remote = RemoteApi(rt, bus, token="t", roots={}, state_fn=lambda: "standby")
    remote.control_enabled = True
    flows, proc, settings = X4._flows(X4._helps)
    flows.ui = appmod._CalibrationUiAdapter(SimpleNamespace(bus=bus))
    _on_plate(flows, proc, _watch(wall, wall.other), wall.plate)
    shell = SimpleNamespace(api=rt, calibration=flows)
    _register_like_the_app(appmod, shell)
    before = _state(flows, settings)
    code, body = remote.submit("StartDancerStep", {})
    assert code == 200 and body["queued"] == "StartDancerStep"
    rt.drain()
    events = remote.events_since(0, 0.0, None)["events"]
    assert [e["message"] for e in events if e["type"] == "Toast"] == [NOT_THIS_WALL]
    assert [(e["text"], e["note"]) for e in events if e["type"] == "CalibProgress"] == [(NOT_THIS_WALL, True)]
    assert _state(flows, settings) == before and not flows.dancer_busy


def _register_like_the_app(appmod, shell):
    """The app's own registration line for StartDancerStep (app.py ``_register_commands``), run on ``shell``."""
    import inspect
    src = inspect.getsource(appmod.WallDanceApp)
    line = next(ln.strip() for ln in src.splitlines() if "reg(api.StartDancerStep" in ln)
    assert line.startswith("reg(api.StartDancerStep, lambda c: self.calibration._cb_dancer_step())"), line
    shell.api.register(api.StartDancerStep, lambda c: shell.calibration._cb_dancer_step())


# ------------------------------------------------------------------------------------- the cause during the step
# The watch's checks come from the app's own main loop (runtime/main_loop.py ``_reference_tick``, once a second),
# never from the test: X4's flows and processor stand in the loop's app (H4's ``_loop_app``), the step's frames
# run between the ticks and ``dancer_watch`` before each frame, as ``MainLoop._tick`` orders them.
NOT_KEPT_LINE = ("A dancer, 10 s: not kept, the empty-wall picture is not this wall; everything put back - CALIBRATE "
                 "on the empty wall (2 Calibrate) first, then A DANCER, 10 s")


def _in_the_loop(wall, flows, proc, frame, monkeypatch):
    ml = H4._import_main_loop()
    clk = H4._Clock()
    monkeypatch.setattr(ml, "time", SimpleNamespace(monotonic=clk, time=clk, perf_counter=clk))
    app = H4._loop_app([], wall.plate, frame)
    proc._plate_capture, proc.dancer_height = None, None
    app.processor, app.calibration = proc, flows                           # the loop reads the step's own objects
    _on_plate(flows, proc, app._reference_watch, wall.plate)
    seen = []
    real_submit = app._reference_watch.submit

    def spy(now, plate, frame, roi=None, exclude=None, why=""):
        seen.append((why, exclude, flows._dancer is not None))
        return real_submit(now, plate, frame, roi, exclude, why)
    app._reference_watch.submit = spy
    return ml.MainLoop(app), clk, app, seen


def _seconds(loop, clk, flows, proc, settings, n, fps=16):
    """``n`` seconds of the app in STANDBY: the reference tick once a second, ``fps`` frames between (1/16 s:
    the clock adds up exactly)."""
    for _ in range(n):
        loop._reference_tick(False)
        for _ in range(fps):
            flows.dancer_watch()
            if flows._calibrating:
                proc.render(settings, flows.enhancer)
                flows._step_calibration([], 50.0)
            clk.t += 1.0 / fps


def test_a_press_before_the_watch_confirms_is_ended_by_its_confirmation(wall, monkeypatch):
    """The reviewer's case: the first check after the project loads says « not this wall » and holds it for its
    second check (nothing shown yet); A DANCER, 10 s pressed in that second starts -- and the watch's
    confirmation, run by the loop through the step, ends it put back before it offers a card."""
    flows, proc, settings = X4._flows(X4._helps)
    loop, clk, app, seen = _in_the_loop(wall, flows, proc, wall.other, monkeypatch)
    watch = app._reference_watch
    loop._reference_tick(False)                                            # at load: the first check
    assert watch.last_why == "plate" and watch.current(wall.plate) is None # held, unconfirmed
    before = _state(flows, settings)
    clk.t += 0.25
    flows._cb_dancer_step()
    assert flows._dancer is not None and flows._calibrating                # nothing confirmed: it starts
    _seconds(loop, clk, flows, proc, settings, 3)
    assert [(w, step) for w, _e, step in seen] == [("plate", False), ("confirm", True)]
    assert watch.current(wall.plate).status == RW.OTHER_WALL
    assert not flows._calibrating and flows._dancer is None and not flows.dancer_busy
    assert _state(flows, settings) == before                               # everything put back, nothing set
    assert flows.ui.show_toast.call_args[0][0] == STOPPED_LINE
    flows.ui.set_calibrate_note.assert_called_with(STOPPED_LINE)
    flows.ui.show_dancer_card.assert_not_called()
    flows.configs._cb_save_config.assert_not_called()
    assert X4._main_loop(flows, proc, settings) == 0


def test_the_camera_turned_away_during_the_step_ends_it_on_the_loops_checks(wall, monkeypatch):
    """A good picture when it starts; the camera turned onto another wall two seconds in: the loop's own checks
    through the step (RUN's cadence and RUN's exclude: her boxes left out) end it before its 200 frames."""
    flows, proc, settings = X4._flows(X4._helps)
    loop, clk, app, seen = _in_the_loop(wall, flows, proc, wall.same, monkeypatch)
    watch = app._reference_watch
    loop._reference_tick(False)
    assert watch.current(wall.plate).status == RW.MATCHES
    before = _state(flows, settings)
    flows._cb_dancer_step()
    _seconds(loop, clk, flows, proc, settings, 2)
    assert flows._dancer is not None                                       # it measures on
    app._last_raw_frame = wall.other
    _seconds(loop, clk, flows, proc, settings, 6)
    in_step = [(w, e) for w, e, step in seen if step]
    assert [w for w, _e in in_step] == ["tick", "confirm"]
    assert in_step[0][1][0] == pytest.approx((174.0, 285.0, 226.0, 415.0))   # her box, grown 15 %, left out
    assert not flows.dancer_busy and _state(flows, settings) == before
    assert flows.ui.show_toast.call_args[0][0] == STOPPED_LINE
    flows.ui.show_dancer_card.assert_not_called()


def test_a_good_picture_checked_through_the_step_and_keep_on_a_card_the_watch_turned_against(wall, monkeypatch):
    """A good picture, a dancer on it: the loop's checks through the step say it lines up and change nothing --
    X4's own numbers on the card.  Then the camera turns onto another wall while the card waits: the watch
    confirms it, and Keep saves nothing -- everything put back, one line."""
    import cv2
    with_her = wall.same.copy()
    cv2.ellipse(with_her, (400, 500), (28, 75), 0, 0, 360, 215, -1)        # a lit body (H4's dancer in view)
    flows, proc, settings = X4._flows(X4._helps)
    loop, clk, app, seen = _in_the_loop(wall, flows, proc, with_her, monkeypatch)
    watch = app._reference_watch
    loop._reference_tick(False)
    before = _state(flows, settings)
    flows._cb_dancer_step()
    _seconds(loop, clk, flows, proc, settings, 13)              # 200 frames
    assert [w for w, _e, step in seen if step] == ["tick", "tick"]          # checked twice through the step
    assert watch.current(wall.plate).status == RW.MATCHES
    assert flows._dancer is None and flows._dancer_pending is not None      # its card
    assert settings.person_height_px == 100 and settings.enhance_enabled is True
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (1.21, 2.5)
    app._last_raw_frame = wall.other
    _seconds(loop, clk, flows, proc, settings, 12)
    assert watch.current(wall.plate).status == RW.OTHER_WALL
    flows._cb_dancer_decision(True)                                        # Keep and save
    flows.configs._cb_save_config.assert_not_called()
    assert not flows.dancer_busy and _state(flows, settings) == before
    assert flows.ui.show_toast.call_args[0][0] == NOT_KEPT_LINE
    flows.ui.set_calibrate_note.assert_called_with(NOT_KEPT_LINE)


def test_a_hand_change_of_exposure_while_it_measures_ends_it_too(wall):
    flows, proc, settings = X4._flows(X4._helps)
    _on_plate(flows, proc, _watch(wall, wall.same), wall.plate)
    before = _state(flows, settings)
    flows._cb_dancer_step()
    X4._main_loop(flows, proc, settings, frames=40)
    proc._plate_invalid = "gain changed by hand - take the empty wall again"        # X5 (app.py)
    flows.dancer_watch()
    assert not flows.dancer_busy and _state(flows, settings) == before
    assert flows.ui.show_toast.call_args[0][0] == ("A dancer, 10 s: stopped, the empty-wall picture no longer fits "
                                                   "the camera (gain changed by hand); everything put back - "
                                                   + PLATE_THEN)


# --------------------------------------------------------------------------------------------------- the band
def _band_app(proc, watch):
    A = X4._app_mod().WallDanceApp
    events = []
    bus = api.EventBus()
    bus.subscribe(events.append)
    stub = SimpleNamespace(processor=proc, bus=bus, _reference_watch=watch,
                           settings=SimpleNamespace(confidence=0.15, imgsz=1280, yolo_shape=None),
                           roi=SimpleNamespace(state=SimpleNamespace(stored_roi=lambda: (135, 3, 965, 1771, 1296, 1776))))
    return A, stub, events


def _feed(A, stub, proc, r, t0, n=60):
    for i in range(n):
        proc.last_raw_boxes = [X4._box(r, 0.4)]                  # a new list: a newly processed frame
        proc.last_fg = X4._fg([(0.0, 0.0, 400.0, 800.0)])        # edges against the wrong picture: one huge group
        A._dancer_band_tick(stub, now=t0 + i * 0.05)


def test_the_band_says_not_measured_on_a_picture_that_is_not_this_wall_then_measures_again(wall):
    watch = _watch(wall, wall.other)
    r = (200.0, 300.0, 230.0, 404.0)
    proc = SimpleNamespace(last_raw_boxes=None, last_fg=None, last_lb_scale=0.7228,
                           _fg_detector=SimpleNamespace(plate=wall.plate), _plate_invalid=None)
    A, stub, events = _band_app(proc, watch)
    _feed(A, stub, proc, r, 0.0)
    bands = [e for e in events if isinstance(e, api.DancerBand)]
    assert bands and all(b.state == "skip" and b.text == BAND_LINE and b.net_px is None for b in bands)
    assert len(stub._dancer_band._s) == 0                                  # nothing measured against it is kept
    watch.submit(20.0, wall.plate, wall.same, H4.ROI)                       # the picture lines up again
    assert watch.current(wall.plate).status == RW.MATCHES
    _feed(A, stub, proc, r, 10.0)
    last = [e for e in events if isinstance(e, api.DancerBand)][-1]
    assert last.state != "skip" and last.net_px is not None and last.text.startswith("dancer ~")


# ---------------------------------------------------------------------------- a good picture; no picture at all
def test_a_good_picture_changes_nothing(wall):
    """The X4 step on a picture the watch says lines up: X4's own numbers (test_the_step_runs_without_run_...)."""
    flows, proc, settings = X4._flows(X4._helps)
    _on_plate(flows, proc, _watch(wall, wall.same), wall.plate)
    flows._cb_dancer_step()
    assert X4._main_loop(flows, proc, settings) == 200
    flows.dancer_watch()
    assert flows.dancer_busy and settings.person_height_px == 100 and settings.enhance_enabled is True
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (1.21, 2.5)
    assert flows.calibration_state["dancer_step"]["enh"] == "1.21/2.5"


def test_no_empty_wall_picture_at_all_is_as_before(wall, capsys):
    """No picture loaded (FrameProcessor._fg_detector None: no foreground, ``last_fg`` None) -- even with a watch
    still holding an alert of another plate: the step starts and measures her on the boxes alone; with no clean
    empty-wall check on record it leaves the enhancement as it is (« none: no clean empty-wall check on record »)
    and says « 2 Calibrate first »; the band gives her height."""
    stale = _watch(wall, wall.other)
    assert stale.current() is not None and stale.current().status == RW.OTHER_WALL
    boxes_only = lambda s, e: ([X4._box(X4.DANCER, 0.6)], [])            # noqa: E731
    flows, proc, settings = X4._flows(boxes_only, ceiling=None)
    proc._fg_detector, flows._reference_watch = None, stale
    real_render = proc.render

    def render(s, e):
        real_render(s, e)
        proc.last_fg = None                                               # no picture: no foreground at all
    proc.render = render
    flows._cb_dancer_step()
    assert "none: no clean empty-wall check on record" in capsys.readouterr().out
    assert X4._main_loop(flows, proc, settings) == 200
    flows.dancer_watch()
    assert flows.dancer_busy and settings.person_height_px == 100        # her box: 100 px
    line, _details = flows.ui.show_dancer_card.call_args[0]
    assert "person height 100 px" in line
    assert "enhancement unchanged (no clean empty-wall check: 2 Calibrate first)" in line
    assert settings.enhance_enabled is False and (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (0.8, 1.0)
    # the band: her height, not « not measured »
    r = (200.0, 300.0, 230.0, 404.0)
    bproc = SimpleNamespace(last_raw_boxes=None, last_fg=None, last_lb_scale=0.7228, _fg_detector=None)
    A, stub, events = _band_app(bproc, stale)
    for i in range(60):
        bproc.last_raw_boxes = [X4._box(r, 0.4)]
        A._dancer_band_tick(stub, now=i * 0.05)
    last = [e for e in events if isinstance(e, api.DancerBand)][-1]
    assert last.state == "warn" and last.net_px == pytest.approx(104 * 0.7228, abs=0.1)


# ------------------------------------------------------------------------------- the line on the Calibrate page
def test_the_calibrate_button_keeps_its_label_under_the_refusal_line():
    """The refusal (or stop) line on the Calibrate page's status line is a note, not progress: the button beside it
    stays CALIBRATE (a press starts Calibrate, as it says) and the line wraps in the panel, read whole.  A flow that
    runs still turns it to CANCEL, as before.  Headless DearPyGui (test_gui_x4_dancer_step's build)."""
    dpg = pytest.importorskip("dearpygui.dearpygui")
    import test_gui_x4_dancer_step as G
    from gui import WallDanceGUI
    from ui.adapter import DpgUiAdapter
    G._build({})
    try:
        me = SimpleNamespace()
        me.set_calibrate_status = lambda t: WallDanceGUI.set_calibrate_status(me, t)
        me.set_calibrate_note = lambda t: WallDanceGUI.set_calibrate_note(me, t)
        handlers = DpgUiAdapter._build_event_handlers(SimpleNamespace(
            api=SimpleNamespace(submit=lambda c: None), gui=me, wizard=SimpleNamespace(on_progress=lambda t: None),
            _sync_control=None,
            _show_state=None, _show_import_progress=None))
        handlers[api.CalibProgress](api.CalibProgress(NOT_THIS_WALL, note=True))   # what the flows publish
        assert dpg.get_item_configuration("calibrate_btn")["label"] == "CALIBRATE"
        assert dpg.get_value("calibrate_status") == NOT_THIS_WALL
        assert dpg.get_item_configuration("calibrate_status")["show"]
        assert dpg.get_item_configuration("calibrate_status")["wrap"] > 0          # wrapped in the panel
        handlers[api.CalibProgress](api.CalibProgress("A dancer, 10 s: 40%"))        # a step runs: CANCEL
        assert dpg.get_item_configuration("calibrate_btn")["label"] == "CANCEL"
        handlers[api.CalibProgress](api.CalibProgress(None))
        assert dpg.get_item_configuration("calibrate_btn")["label"] == "CALIBRATE"
        assert not dpg.get_item_configuration("calibrate_status")["show"]
    finally:
        dpg.destroy_context()
