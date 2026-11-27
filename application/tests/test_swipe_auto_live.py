"""P6 (Plan Cordee): the autonomous swipe beside the show, on CPU with fakes.

* the automatic switch goes through the seamless switch: the resident goes live, the old live model becomes the
  resident at the same input; the operator's Apply takes the resident when it is the winner (no second load) and
  lets it go first when it is not (two engines at most);
* the looks: nothing while the wall is empty or the app is busy; mode off releases the resident;
* the project key ``swipe_auto``: absent from a project file while off, absent = off.
"""
import dataclasses
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.swipe import Candidate
from test_seamless_switch import L1280, L1920, X1280, _Loader, _Port, _Runner, _live  # noqa: F401
from test_seamless_switch import _BgProc as _SwitchProc


@pytest.fixture
def swipe_auto_unlocked(monkeypatch):
    """Every mode of the automatic swipe released (``core.config.SWIPE_AUTO_RELEASED``), for a test that turns it on
    through the app's doors (the combo's callback, a project load).  The build ships off and suggest: never
    autouse."""
    import core.config
    monkeypatch.setattr(core.config, "SWIPE_AUTO_RELEASED", ("off", "suggest", "auto"))


@pytest.fixture
def swipe_auto_suggest_only(monkeypatch):
    """Off and suggest released, not auto: the build as it ships since 27/11 (suggest released), set explicitly."""
    import core.config
    monkeypatch.setattr(core.config, "SWIPE_AUTO_RELEASED", ("off", "suggest"))


@pytest.fixture
def swipe_auto_off_only(monkeypatch):
    """Off the only mode released: the build as it shipped until 27/11, for a test of the fully locked behaviour."""
    import core.config
    monkeypatch.setattr(core.config, "SWIPE_AUTO_RELEASED", ("off",))


class _BgProc(_SwitchProc):
    """... with the tap's kept frames, as the background swipe looks at them."""

    def __init__(self):
        super().__init__()
        self.frames = []
        self.timing = {"yolo": 6.0}     # the live call's time: the « slower » rule refuses a winner without it

    def swipe_fresh(self, after_t=None):
        return [f for f in self.frames if after_t is None or float(f.t) > after_t], self.clock


def _bg(live, *, loader=None, port=None, busy=lambda: None, mode="off", engine_exists=lambda p: True, **kw):
    """``kw``: more of BackgroundSwipe's arguments (``in_run``, ``wall_empty``, ``gpu_used``); without them the app
    reads as out of RUN (STANDBY): the second engine loads at the first look."""
    pytest.importorskip("torch")
    from runtime.seamless_switch import SeamlessSwitch
    from runtime.swipe_background import BackgroundSwipe
    events = []
    proc = _BgProc()
    lock = threading.RLock()
    sw = SeamlessSwitch(processor=lambda: proc, live=lambda: live["v"], engine_path=lambda c: c.engine_file,
                        port=port or _Port(), notify=lambda k, p: events.append((k, p)),
                        runner_factory=loader or _Loader(), engine_exists=engine_exists, engine_lock=lock,
                        size_change=True)               # the operator's Apply of another size: the mechanism
    sw.start = lambda: None
    bg = BackgroundSwipe(processor=lambda: proc, live=lambda: live["v"], engine_path=lambda c: c.engine_file,
                         busy=busy, switch=sw, notify=lambda k, p: events.append((k, p)),
                         mode=mode, look_s=999.0, runner_factory=loader or _Loader(),
                         engine_exists=engine_exists, engine_lock=lock, **kw)
    bg.start = lambda: None                              # the test drives the looker by hand (``tick``)
    return bg, proc, events


def test_the_automatic_switch_keeps_the_old_live_model_beside_at_the_same_input(swipe_auto_unlocked):  # (27/11) auto: released
    """mode auto: the resident (the other model at the live input) goes live, the old live model becomes the
    resident -- the way back is as cheap."""
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    port = _Port()
    bg, _, events = _bg(live, port=port, mode="auto")
    m, r = SimpleNamespace(name="l1280"), _Runner("l1280")
    bg.resident = Resident(L1280, m, r, bg._resident_key(live["v"]), 0.4)
    adv = SimpleNamespace(kind="apply", level="definite", winner=L1280, seamless=True, streak=2,
                          text="switching to l @ 1280 (it covers 20 % more of the dancers)", why_not_auto=None)
    bg._act(adv, SimpleNamespace(reason="l @ 1280 covers 90 %", winner=L1280, current=X1280), live["v"])
    assert bg.state == "switching" and events[-1][0] == "switching"         # the alert
    bg.switch.service()                                  # warmed at the live input
    assert bg.switch.drain()
    assert port.calls == [("swap", m, L1280)]
    assert bg.resident.candidate == X1280 and bg.resident.model == "x1280" and bg.switch._drop == []
    port.hooks[1]()
    assert dict(events)["switched"]["auto"] is True and bg.switch.fault is None
    assert bg.auto.mode == "auto" and bg._expect == L1280                     # not taken for a change by hand


def test_the_operators_apply_takes_the_resident_when_it_is_the_winner():
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    loader, port = _Loader(), _Port()
    bg, _, _ = _bg(live, loader=loader, port=port, mode="suggest")
    m, r = SimpleNamespace(name="l1280"), _Runner("l1280")
    bg.resident = Resident(L1280, m, r, bg._resident_key(live["v"]), 0.4)
    assert bg.request_switch(L1280, "Apply") is None
    bg.switch.service()
    assert loader.loads == [] and bg.switch.drain() and port.calls == [("swap", m, L1280)]


def test_the_operators_apply_of_another_input_lets_the_resident_go_first():
    """Two engines at most: the resident (the other model at the live input) is freed before the winner loads."""
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    loader, port = _Loader(), _Port()
    bg, _, _ = _bg(live, loader=loader, port=port, mode="suggest")
    bg.resident = Resident(L1280, "l1280", _Runner("l1280"), bg._resident_key(live["v"]), 0.4)
    assert bg.request_switch(L1920, "Apply") is None
    assert bg.resident is None and bg.switch._drop == [("l1280", bg.switch._drop[0][1])]
    bg.switch.service()                                  # freed (after a live frame), then loaded
    assert bg.switch._drop == [] and loader.loads == [L1920.engine_file] and bg.switch._request is not None


def test_a_failed_automatic_switch_turns_the_next_one_into_a_suggestion():
    live = {"v": _live()}
    bg, _, _ = _bg(live, mode="auto")
    bg.switch.fail("the automatic switch to l @ 1280 failed (RuntimeError: x): x @ 1280 still runs - set the "
                   "automatic swipe to off")
    assert bg.switch.health().endswith("set the automatic swipe to off")


# ----------------------------------------------------------------------------------------------------------
# A whole look: frames where the live model leaves the dancer bare, the other model at the live input sees her
# ----------------------------------------------------------------------------------------------------------
DANCER = (500.0, 500.0, 1000.0)
ON_HER = (450.0, 400.0, 550.0, 600.0, 0.6)


class _LookRunner(_Loader):
    """The other model sees the dancer on every frame (and the live one left her bare on the lost ones)."""

    def __init__(self):
        super().__init__()
        self.inp = SimpleNamespace(roi=lambda w, h: (0, 0, w, h))
        self.infers = 0
        self.infer_boxes = [ON_HER]

    def infer(self, images, cand, runner, *, cancelled=None, pace=None, on_frame=None):
        from core.swipe_runner import CandidateRun
        run = CandidateRun(cand)
        for _ in images:
            if cancelled is not None and cancelled():
                run.error = "cancelled"
                break
            if pace is not None:
                pace()
            run.boxes.append(list(self.infer_boxes))
            run.ms.append(6.0)
            self.infers += 1
        return run


def _look_frames(n=150, lost=100, t0=0.0):
    """``n`` kept frames with a dancer, 1 s apart; the live setting left her bare on the first ``lost``."""
    import numpy as np
    from core.swipe import Observation, SwipeFrame
    img = np.zeros((64, 48), np.uint8)
    return [SwipeFrame(t0 + i, img, Observation(groups=[DANCER]), i < lost, () if i < lost else (ON_HER,))
            for i in range(n)]


def _looking_bg(mode="suggest", **kw):
    live = {"v": _live()}
    loader = _LookRunner()
    bg, proc, events = _bg(live, loader=loader, mode=mode, **kw)
    bg.pace_s = 0.0
    proc.swipe_lost_share = lambda: 0.66
    proc.clock = 200.0
    return bg, proc, events, loader, live


def test_a_look_loads_the_other_model_once_and_suggests_it():
    bg, proc, events, loader, live = _looking_bg()
    proc.frames = _look_frames()
    assert bg.tick() == "run"
    assert loader.loads == [L1280.engine_file] and bg.resident.candidate == L1280      # the other model, here
    assert loader.infers == 150 and bg.inferences == 150
    assert bg.last_advice["kind"] == "advice" and bg.last_look["outcome"]
    assert bg.suggestion is None and bg.last_advice["advice"] == "seen"            # (27/11) once: nothing shown
    proc.frames = _look_frames(150, t0=150.0)
    assert bg.tick() == "run" and loader.infers == 300
    assert bg.suggestion is not None and bg.suggestion["winner_c"] == L1280
    kind, payload = [e for e in events if e[0] == "suggest"][-1]
    assert payload["winner"] == "l @ 1280" and payload["result"]["switch"]
    assert bg.tick() == "idle" and loader.infers == 300 and len(loader.loads) == 1    # its frames are used


def test_a_run_needs_a_full_pool_of_frames_the_last_run_did_not_use():
    bg, proc, events, loader, live = _looking_bg()
    proc.frames = _look_frames(149)
    assert bg.tick() == "idle" and loader.infers == 0                                  # 149: not yet, nothing run
    assert len(loader.loads) == 1             # (the second engine: loaded at the mode's first quiet look, kept)
    proc.frames = _look_frames(150)
    assert bg.tick() == "run" and loader.infers == 150
    proc.clock = 1000.0                                  # long after: the policy would look again ...
    proc.frames = _look_frames(150)[60:] + _look_frames(60, t0=150.0)                # ... but 60 new frames only
    assert bg.tick() == "idle" and loader.infers == 150
    proc.frames = _look_frames(150, t0=150.0)            # a whole new pool
    assert bg.tick() == "run" and loader.infers == 300


def test_the_status_counts_say_what_they_hold():
    """/status's ``counts``: ``lost`` the fresh frames the live setting left a dancer bare on, ``covered`` the
    others (they once carried the fresh total and the lost ones under each other's name)."""
    bg, proc, events, loader, live = _looking_bg()
    proc.frames = _look_frames(120, lost=30)             # under a full pool: counted, nothing run
    assert bg.tick() == "idle"
    assert bg.status()["counts"] == {"lost": 30, "covered": 90}
    live["v"] = dataclasses.replace(live["v"], plate_ok=False)    # a plate that does not hold: nothing counted
    assert bg.tick() == "waiting" and bg.status()["counts"] == {"lost": 0, "covered": 0}


def test_a_run_without_a_verdict_says_nothing():
    """The referee's no verdict (here: the empty-wall picture is not trusted -- boxes on nobody everywhere): no
    suggestion, no toast, a standing suggestion stays (since 27/11 its line no longer invites: one look that did
    not confirm it, ``test_swipe_auto_unconfirmed``)."""
    from core.swipe import Observation, SwipeFrame
    import numpy as np
    bg, proc, events, loader, live = _looking_bg()
    img = np.zeros((64, 48), np.uint8)
    nobody = (50.0, 50.0, 90.0, 120.0, 0.7)              # a box where the picture shows nobody
    proc.frames = [SwipeFrame(float(i), img, Observation(groups=[DANCER]), i < 100, (nobody,)) for i in range(150)]
    loader.infer_boxes = [nobody, ON_HER]
    bg.suggestion = {"text": "earlier", "live_key": live["v"].key, "winner_c": L1280}
    n = len(events)
    assert bg.tick() == "run"
    assert bg.last_advice.get("no_verdict") and bg.last_advice["reason"].startswith("no verdict")
    assert [k for k, p in events[n:] if not p.get("again")] == [] and bg.suggestion["unconfirmed"] == 1


def test_the_run_judges_at_the_shows_confidence():
    bg, proc, events, loader, live = _looking_bg()
    proc.settings.confidence = 0.27
    assert bg._run_params(proc).conf == 0.27 and bg._run_params(proc).frames == 150


# ----------------------------------------------------------------------------------------------------------
# The looks: an empty wall, a busy app
# ----------------------------------------------------------------------------------------------------------
def test_an_empty_wall_costs_no_inference_and_one_load():
    """No inference on an empty wall; the second engine loads there once (here out of RUN), and stays."""
    live = {"v": _live()}
    loader = _Loader()
    bg, proc, _ = _bg(live, loader=loader, mode="suggest")
    for _ in range(5):
        assert bg.tick() == "idle"
    assert loader.loads == [L1280.engine_file] and bg.resident.candidate == L1280 and bg.inferences == 0


def test_a_busy_app_is_never_looked_at():
    live = {"v": _live()}
    loader = _Loader()
    bg, proc, _ = _bg(live, loader=loader, mode="suggest", busy=lambda: "a calibration")
    proc.frames = _look_frames()
    assert bg.tick() == "busy" and loader.loads == []


def test_mode_off_releases_the_resident_after_a_live_frame():
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    bg, proc, _ = _bg(live, mode="suggest")
    bg.resident = Resident(L1280, "m", "r", bg._resident_key(live["v"]), 0.4)
    bg.set_mode("off")
    bg.tick()
    assert bg.resident is None


def test_without_the_tap_a_model_is_freed_after_a_network_pass():
    """swipe_collect off: no tap key -- the looker frees a model once the live model has run a frame."""
    live = {"v": _live()}
    bg, proc, _ = _bg(live, mode="suggest")
    proc._swipe_key = None
    bg.switch.release(("old", "runner"))
    bg.switch.service()
    assert bg.switch._drop                                # first sight of the live model: not yet
    proc.frame_done_t = 5.0
    bg.switch.service()
    assert bg.switch._drop == []


# ----------------------------------------------------------------------------------------------------------
# The project key: absent while off
# ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["off", "suggest", "auto"])
def test_swipe_auto_is_written_only_when_not_off(mode):
    pytest.importorskip("dearpygui")
    from app import WallDanceApp
    app = MagicMock()
    app.processor.get_exclusion_state.return_value = ((16, 10), [], [], [])
    app.roi.state.stored_roi.return_value = (0, 0, 100, 100, 200, 200)
    app.swipe_bg.mode = mode
    cfg = WallDanceApp._get_saveable_config(app)
    if mode == "off":
        assert "swipe_auto" not in cfg
    else:
        assert cfg["swipe_auto"] == mode


def test_a_project_without_the_key_runs_off_and_a_partial_config_leaves_it(swipe_auto_unlocked):
    pytest.importorskip("dearpygui")
    from app import WallDanceApp
    app = MagicMock()
    WallDanceApp._apply_config_without_model(app, {"swipe_auto": "suggest"})
    app._cb_swipe_auto.assert_called_once_with("suggest", quiet=True)
    app = MagicMock()
    WallDanceApp._apply_config_without_model(app, {"confidence": 0.2})        # not a project file
    app._cb_swipe_auto.assert_not_called()


def test_the_schema_keeps_a_mode_and_drops_anything_else():
    from core.config_schema import validate_flat
    out, warnings = validate_flat({"swipe_auto": "Suggest"})
    assert out["swipe_auto"] == "suggest"
    out, warnings = validate_flat({"swipe_auto": "always"})
    assert "swipe_auto" not in out and any("swipe_auto" in w for w in warnings)


def test_a_change_by_hand_starts_the_dwell_and_a_switch_does_not_count_as_one():
    live = {"v": _live()}
    bg, proc, _ = _bg(live, mode="auto")
    bg.tick()
    live["v"] = _live(cand=L1280, model_id=2)            # the operator chose l @ 1280 in the combo
    bg.tick()
    assert [h["kind"] for h in bg.history].count("manual-change") == 1
    assert bg.auto.policy._switched_at == proc.loop_clock()     # the policy's dwell runs from now
    bg._expect = X1280                                   # ... while our own switch back is not a change by hand
    live["v"] = _live(cand=X1280, model_id=3)
    bg.tick()
    assert [h["kind"] for h in bg.history].count("manual-change") == 1 and bg._expect is None


def test_no_room_for_a_gain_means_no_run_no_inference():
    """The policy's free pre-filter: the live setting's own stored boxes already cover every dancer -- no run, no
    inference (the second engine was loaded at the mode's quiet moment, not for this look), and those frames count
    as looked at."""
    bg, proc, events, loader, live = _looking_bg()
    proc.frames = _look_frames(150, lost=0)              # a box on the dancer on every frame
    skipped = bg.auto.skipped
    assert bg.tick() == "idle" and bg.auto.skipped == skipped + 1
    assert len(loader.loads) == 1 and loader.infers == 0
    assert bg.last_look["look"] == "skipped"
    assert bg.tick() == "idle" and bg.auto.skipped == skipped + 1          # its frames are no longer fresh


def test_two_definite_runs_on_disjoint_frames_switch_in_mode_auto(swipe_auto_unlocked):  # (27/11) auto: released
    """Mode auto, the policy's own rule: a definite winner at the same input twice in a row, on frames no earlier
    run looked at -- the first run suggests (why not yet), the second asks the switch for the resident."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    proc.frames = _look_frames(150)
    assert bg.tick() == "run"
    first = bg.last_advice
    assert first["advice"] == "seen" and first["why_not_auto"] and bg.switch._want is None
    assert bg.auto.policy.fresh(proc.frames) == []       # advise marked them looked at
    proc.frames = _look_frames(150, t0=150.0)
    assert bg.tick() == "run"
    assert bg.last_advice["advice"] == "apply" and bg.switch._want is not None
    assert bg.switch._want["prepared"].candidate == L1280 and bg.switch._want["auto"] is True
    assert [k for k, _ in events].count("switching") == 1


def test_a_background_refusal_is_not_told_to_the_plate_watch_the_operators_press_is(monkeypatch):
    """fix6's RefereeReport, shared with the operator's swipe: the background run's refusal of the empty-wall plate
    is not told to the plate watch (core.reference_watch, a fake here: its alert is for the operator's presses),
    and the operator's swipe on the same pool is told (the looker's refusal was never told)."""
    import sys
    import types
    from runtime.swipe_controller import RefereeReport
    from core.swipe import Observation, SwipeFrame
    import numpy as np
    calls = []
    watch = types.ModuleType("core.reference_watch")
    watch.note_referee_refusal = lambda reason="": calls.append(("refusal", reason)) or len(calls)
    watch.note_referee_accept = lambda: calls.append(("accept", None))
    monkeypatch.setitem(sys.modules, "core.reference_watch", watch)
    bg, proc, events, loader, live = _looking_bg()
    bg.referee = shared = RefereeReport()
    img = np.zeros((64, 48), np.uint8)
    nobody = (50.0, 50.0, 90.0, 120.0, 0.7)
    proc.frames = [SwipeFrame(float(i), img, Observation(groups=[DANCER]), i < 100, (nobody,)) for i in range(150)]
    loader.infer_boxes = [nobody, ON_HER]
    bg.tick()
    assert calls == []
    waits = "the referee does not trust the empty-wall picture: the automatic swipe waits"
    assert bg.last_look["outcome"] == waits
    refused = SimpleNamespace(winner=None, reason="no verdict: every setting puts boxes where the empty-wall "
                                                  "picture shows nobody")
    assert shared.note(refused, proc.frames) == "refusal" and len(calls) == 1  # the operator's press, same pool


def _apply_adv(winner=L1280):
    return SimpleNamespace(kind="apply", level="definite", winner=winner, seamless=True, streak=2,
                           text=f"switching to {winner.label}: {winner.label} covers 95 % of the dancers, x @ 1280 "
                                f"60 %", why_not_auto=None)


def test_a_failed_automatic_switch_is_not_tried_again_until_dismiss(swipe_auto_unlocked):  # (27/11) auto: released
    """An automatic switch that did not happen (here: the engine raises on its first live frame) -- the previous
    setting runs on (a warn row), the fault names the engine, and the automatic swipe stands down for that setting:
    a later definite verdict (a look that ran meanwhile) says nothing until Dismiss."""
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    port = _Port()
    bg, proc, events = _bg(live, port=port, mode="auto")
    from core.swipe import SwipeParams
    from core.swipe_runner import CandidateRun, live_run_from_boxes, score_runs
    fr = _look_frames()
    res = score_runs([f.obs for f in fr], [live_run_from_boxes(X1280, [f.boxes for f in fr]),
                                           CandidateRun(L1280, boxes=[[ON_HER] for _ in fr])], X1280, SwipeParams())
    assert res.winner == L1280
    bg.resident = Resident(L1280, "l1280", _Runner("l1280"), bg._resident_key(live["v"]), 0.4)
    bg._act(_apply_adv(), res, live["v"])
    bg.switch.service()
    assert bg.switch.drain()
    port.hooks[0](RuntimeError("the engine raised on its first frame"), None)       # its first live frame
    text = ("the automatic switch to l @ 1280 failed (its first live frames failed): x @ 1280 "
            "still runs - the automatic swipe will not try l @ 1280 again until you press Dismiss")
    assert bg.switch.health() == text and bg.status()["auto_banned"] == ["yolo11l-pose_1280.engine"]
    bg.switch.dismiss()                                   # the fault row goes ... but not the ban: the bg's Dismiss
    bg.resident = Resident(L1280, "l1280b", _Runner("l1280b"), bg._resident_key(live["v"]), 0.4)
    bg._act(_apply_adv(), res, live["v"])
    assert bg.last_advice["advice"] == "none" and bg.switch._want is None and bg.suggestion is None
    bg.dismiss()                                          # the operator's Dismiss (the app calls both)
    bg._act(_apply_adv(), res, live["v"])
    assert bg.last_advice["advice"] == "apply" and bg.switch._want is not None


def test_a_project_load_lifts_the_ban():
    live = {"v": _live()}
    bg, proc, events = _bg(live, mode="auto")
    bg._banned[L1280.key] = L1280.engine_file
    bg.clear_bans()
    assert bg.status()["auto_banned"] == []


def test_a_background_suggestion_never_makes_a_readiness_row():
    """The a848532 rule: a background suggestion is its line and its toast (« Apply now »), never a readiness row --
    the row is only the operator's own verdict or a failed switch."""
    pytest.importorskip("dearpygui")
    import app as appmod
    from runtime import api
    from runtime.swipe_controller import SwipeController
    events = []
    live = {"v": _live()}
    bg, proc, _ = _bg(live, mode="suggest")
    bg.suggestion = {"winner_c": L1280, "text": "l @ 1280 covers 95 % of the dancers, x @ 1280 60 %: clearly "
                                                "better - Apply now (automatic switching is off)",
                     "live_key": live["v"].key}
    swipe = SwipeController(processor=lambda: proc, live=lambda: live["v"], engine_path=str, plate_ready=lambda: True)
    fake = SimpleNamespace(bus=SimpleNamespace(publish=events.append), swipe_bg=bg, _swipe_shown="",
                           swipe_switch=bg.switch)
    fake._swipe_apply_hint = lambda w: appmod.WallDanceApp._swipe_apply_hint(fake, w)
    appmod.WallDanceApp._swipe_notify(fake, "suggest", {"kind": "suggest", "level": "definite", "winner": "l @ 1280",
                                                         "text": bg.suggestion["text"], "seamless": True,
                                                         "why_not_auto": "automatic switching is off",
                                                         "result": {"reason": "l @ 1280 covers 95 %", "rows": []}})
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["SettingsSwipeResult", "Toast"]
    assert events[1].message == "A better model may be available - see 'Try other settings' (Apply now)"
    assert events[0].result["apply_label"] == "Apply l @ 1280"
    assert swipe.readiness(plate_ready=True, live_now=live["v"], other_fault=bg.switch.health(),
                           seamless=True) is None
