"""P6 (Plan Cordee), the automatic swipe's key off (swipe_auto: off, a project's default): the build behaves as it
did before P6 -- P5's Apply (STANDBY only, the reload; in RUN P5's refusal), P5's words on the Apply button, its
tooltip, the swipe button's tooltip and the readiness row, and a remote Apply STANDBY only.  Turning the key off
in RUN leaves nothing of P6 under way: a seamless switch asked before and not yet swapped in does not happen.  A
project that brings the key on says so, once.  On CPU with fakes.
"""
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from test_seamless_switch import L1280, L1920, X1280, _Loader, _Port, _live  # noqa: F401
from test_swipe_auto_live import _bg, _looking_bg, _look_frames, swipe_auto_unlocked  # noqa: F401

P5_RUN_TOAST = "Apply: stop the show first - Apply reloads the model, a few seconds without tracking"
P5_APPLY_TIP = ("STANDBY only: switches the model / network input to the swipe's\n"
                "pick -- the engine reloads, a few seconds without tracking, as\n"
                "when you change the model by hand.  Not saved: Save the project\n"
                "to keep it.")
P5_SWIPE_TIP_END = "nothing switches\nuntil you Apply, in STANDBY.  Press again to cancel."


def _app():
    pytest.importorskip("dearpygui")
    import app as appmod
    return appmod


def _apply_fake(state, mode, *, seamless_why=None):
    """An app around a real automatic swipe and seamless switch (in ``mode``), whose winner's engine is built and
    runs at the live input: P6 would switch between two frames.  Every way into a switch is watched."""
    appmod = _app()
    from runtime import api
    from runtime.api import SystemState
    live = {"v": _live()}
    loader = _Loader()
    bg, proc, _ = _bg(live, loader=loader, mode=mode)
    sw = bg.switch
    started, asked, reloads, events = [], [], [], []
    sw.start = lambda: started.append("worker")
    real_request = bg.request_switch
    bg.request_switch = lambda *a, **k: asked.append(a) or real_request(*a, **k)
    if seamless_why is not None:
        sw.can_switch = lambda w: seamless_why
    fake = SimpleNamespace(
        system_state=getattr(SystemState, state), bus=SimpleNamespace(publish=events.append),
        _swipe_shown="operator", _swipe_live_setting=lambda: live["v"], _swipe_pending_apply=None,
        swipe=SimpleNamespace(applicable=lambda lv: (None, L1280, None)), swipe_switch=sw, swipe_bg=bg,
        configs=SimpleNamespace(_current_project="jeudi0809_mur2"),
        models=SimpleNamespace(request_setting=lambda m, i, s, on_done=None: reloads.append((m, i, s)) or None,
                               ui=SimpleNamespace(sync_model_combo=lambda n: None)),
        _sync=lambda *a: None, _swipe_apply_done=lambda ok: None)
    fake._apply_settings_swipe_p5 = lambda: appmod.WallDanceApp._apply_settings_swipe_p5(fake)
    appmod.WallDanceApp._cb_apply_settings_swipe(fake)
    return SimpleNamespace(api=api, events=events, asked=asked, started=started, reloads=reloads, loader=loader,
                           sw=sw, bg=bg)


# ----------------------------------------------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------------------------------------------
def test_key_off_apply_in_run_is_p5s_refusal_and_starts_no_switch():
    """The winner's engine built at the live input, the show running, the key off: P5's refusal word for word, and
    no switch at all -- no request, no worker thread, no engine load."""
    r = _apply_fake("RUN", "off")
    assert [e.message for e in r.events if isinstance(e, r.api.Toast)] == [P5_RUN_TOAST]
    assert r.asked == [] and r.started == [] and r.loader.loads == [] and r.reloads == []
    assert r.sw.busy() is None and r.sw.state == "idle"


def test_key_off_apply_in_standby_is_p5s_reload(capsys):
    """STANDBY, the key off, the engine built: P5's reload (the model-switch path), never the seamless switch;
    P5's log line and progress line."""
    r = _apply_fake("STANDBY", "off")
    assert r.reloads == [(L1280.model, L1280.imgsz, L1280.shape)]
    assert r.asked == [] and r.started == [] and r.loader.loads == []
    prog = [e for e in r.events if isinstance(e, r.api.SettingsSwipeProgress)]
    assert [(p.state, p.text) for p in prog] == [("applying", "switching to l @ 1280...")]
    assert "[Swipe] applying l @ 1280\n" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["suggest", "auto"])
def test_key_on_apply_in_run_is_p6s_seamless_switch(mode):
    """Regression guard: the key on, the same Apply is P6's -- loaded beside the live model, between two frames."""
    r = _apply_fake("RUN", mode)
    assert r.asked and r.asked[0][0] == L1280 and r.reloads == []
    assert r.started == ["worker"] and r.sw.busy() == "a switch is already under way"


def test_key_off_apply_button_label_and_tooltip_are_p5s():
    """The Apply button the verdict shows: P5's label and tooltip with the key off, the engine built or not."""
    appmod = _app()
    live = {"v": _live()}
    bg, _, _ = _bg(live, mode="off")
    fake = SimpleNamespace(swipe_bg=bg, swipe_switch=bg.switch,
                           swipe=SimpleNamespace(outcome=SimpleNamespace(result=SimpleNamespace(
                               winner=L1280, switch=True))))
    hint = appmod.WallDanceApp._swipe_apply_hint(fake, L1920)
    assert hint == {"apply_seamless": False, "apply_label": "Apply l @ 1920 x 1056 (STANDBY)",
                    "apply_tip": P5_APPLY_TIP}
    assert appmod.WallDanceApp._swipe_seamless_winner(fake) is None        # the readiness row: P5's wording
    bg.set_mode("suggest")                                                 # regression guard: P6's, the key on
    hint = appmod.WallDanceApp._swipe_apply_hint(fake, L1920)
    assert hint["apply_seamless"] and hint["apply_label"] == "Apply l @ 1920 x 1056"
    assert appmod.WallDanceApp._swipe_seamless_winner(fake) == L1280


def _readiness_fake(mode):
    """An app whose operator's verdict recommends l @ 1280 (its engine built, the live input: seamless with the key
    on), with a ban the automatic swipe holds from earlier."""
    appmod = _app()
    from runtime.swipe_controller import SwipeController
    live = {"v": _live()}
    bg, _, _ = _bg(live, mode=mode)
    bg._banned[L1920.key] = L1920.engine_file
    result = SimpleNamespace(winner=L1280, switch=True, reason="l @ 1280 covers 91 % of the dancers")
    ctrl = SimpleNamespace(outcome=SimpleNamespace(result=result), fault=None, fault_recovered=False,
                           _advice_live=lambda live_now: True)
    fake = SimpleNamespace(swipe_bg=bg, swipe_switch=bg.switch, _swipe_plate_ready=lambda: True,
                           _swipe_live_setting=lambda: live["v"],
                           swipe=SimpleNamespace(outcome=ctrl.outcome,
                                                 readiness=lambda **kw: SwipeController.readiness(ctrl, **kw)))
    fake._swipe_seamless_winner = lambda: appmod.WallDanceApp._swipe_seamless_winner(fake)
    return appmod, fake, bg


def test_key_off_readiness_row_is_p5s():
    """The readiness row of an operator's verdict that recommends a switch: P5's, word for word, with the key off
    -- the engine built, and a ban left from when the key was on (the automatic swipe tries nothing now)."""
    appmod, fake, bg = _readiness_fake("off")
    # the swipe's own readiness without the key: P5's (the engine built makes no difference)
    row = fake.swipe.readiness(plate_ready=True, live_now=None, seamless=True)
    assert row.detail == (
        "l @ 1280 covers 91 % of the dancers - in STANDBY press 'Apply l @ 1280 (STANDBY)', under Try other settings")
    row = appmod.WallDanceApp._swipe_readiness_row(fake)
    assert row.status == "warn" and row.detail == (
        "l @ 1280 covers 91 % of the dancers - in STANDBY press 'Apply l @ 1280 (STANDBY)', under Try other settings")
    bg.switch.fail("the switch to l @ 1280 failed (RuntimeError: x): x @ 1280 still runs - rebuild it")
    assert appmod.WallDanceApp._swipe_readiness_row(fake).detail.startswith("the switch to l @ 1280 failed")


@pytest.mark.parametrize("mode", ["suggest", "auto"])
def test_key_on_readiness_row_is_p6s(mode):
    """Regression guard: the key on, the ban says so, and without it the row is the seamless Apply's.  (It needs the
    row's own method, new with the key-off fix: it cannot run before it.)"""
    appmod, fake, bg = _readiness_fake(mode)
    assert appmod.WallDanceApp._swipe_readiness_row(fake).detail == (
        f"the automatic swipe will not try {L1920.engine_file} again until you press Dismiss")
    bg._banned.clear()
    assert appmod.WallDanceApp._swipe_readiness_row(fake).detail == (
        "l @ 1280 covers 91 % of the dancers - press 'Apply l @ 1280' (under Try other settings): it switches "
        "between two frames, the show runs on")


def test_key_off_gui_shows_p5s_apply_and_swipe_tooltips():
    """The GUI: P5's Apply label on one line (as it was), P5's tooltips while the key is off; the swipe button's
    tooltip follows the mode's combo."""
    dpg = pytest.importorskip("dearpygui.dearpygui")
    import gui_builder
    from gui import WallDanceGUI
    dpg.create_context()
    try:
        noop = lambda *a, **k: None  # noqa: E731
        mock = SimpleNamespace(config={}, _on_check_readiness=noop, _on_calibrate=noop, _on_capture_plate=noop,
                               _on_dryrun=noop, _on_swipe=noop, _on_swipe_apply=noop, _on_swipe_auto=noop,
                               _on_swipe_dismiss=noop, _btn_standby_theme=dpg.add_theme())
        with dpg.window(label="keyoff"):
            gui_builder.build_phase_wall(mock)
        assert dpg.get_value("swipe_apply_tip") == P5_APPLY_TIP
        assert dpg.get_value("swipe_btn_tip").endswith(P5_SWIPE_TIP_END)
        g = SimpleNamespace()
        WallDanceGUI.show_swipe_result(g, {"reason": "r", "switch": True, "winner": "l @ 1920 x 1056",
                                           "apply_seamless": False, "apply_label": "Apply l @ 1920 x 1056 (STANDBY)",
                                           "apply_tip": P5_APPLY_TIP})
        cfg = dpg.get_item_configuration("swipe_apply_btn")
        assert cfg["label"] == "Apply l @ 1920 x 1056 (STANDBY)" and cfg["show"]
        WallDanceGUI.sync_combo(None, "swipe_auto", "Auto: suggest")
        assert dpg.get_value("swipe_btn_tip").endswith("nothing switches\nuntil you Apply.  Press again to cancel.")
        WallDanceGUI.sync_combo(None, "swipe_auto", "Auto: off")
        assert dpg.get_value("swipe_btn_tip").endswith(P5_SWIPE_TIP_END)
    finally:
        dpg.destroy_context()


def test_a_remote_apply_is_standby_only_and_the_key_stays_control():
    """A remote Apply: STANDBY only, whatever the key (P5's class).  The key itself: control -- setting it off
    during a show stays possible (the failure sentences ask for it)."""
    from services.remote_api import POLICY, check_policy
    assert POLICY["ApplySettingsSwipe"] == "heavy"
    assert check_policy("ApplySettingsSwipe", {}, "run", control_enabled=True) is not None
    assert check_policy("ApplySettingsSwipe", {}, "standby", control_enabled=False) is None
    assert POLICY["SetSwipeAuto"] == "control"
    assert check_policy("SetSwipeAuto", {"mode": "off"}, "run", control_enabled=True) is None


# ----------------------------------------------------------------------------------------------------------
# Turning the key off while P6 is under way
# ----------------------------------------------------------------------------------------------------------
def _held_by_the_app(bg):
    """The switch with the hold WallDanceApp.__init__ gives it: the app's own rule on a fake app."""
    appmod = _app()
    rule = getattr(appmod.WallDanceApp, "_swipe_switch_held", None)       # none before the key-off fix
    if rule is not None:
        bg.switch.why_not = lambda req: rule(SimpleNamespace(swipe_bg=bg), req)
    return bg.switch


def test_the_app_gives_the_switch_its_key_off_hold():
    appmod = _app()
    assert "self.swipe_switch.why_not = self._swipe_switch_held" in inspect.getsource(appmod.WallDanceApp.__init__)


def test_key_off_before_the_load_abandons_the_seamless_apply():
    """The operator's seamless Apply asked (the key on), the key off before the worker loads it: no load, no swap,
    the Apply told it did not happen."""
    live = {"v": _live()}
    loader, port = _Loader(), _Port()
    bg, _, events = _bg(live, loader=loader, port=port, mode="suggest")
    sw = _held_by_the_app(bg)
    done = []
    assert bg.request_switch(L1280, "switching to l @ 1280 (Apply)", on_done=lambda ok, t: done.append((ok, t))) \
        is None
    bg.set_mode("off")
    sw.service()
    assert loader.loads == [] and sw._request is None and sw.busy() is None
    assert not sw.drain() and port.calls == []
    assert done == [(False, "not switched to l @ 1280: the automatic swipe is off")] and sw.fault is None


def test_key_off_during_the_load_frees_the_model_and_does_not_warm_it():
    """The key off while the worker loads the winner: the load ends (it cannot be stopped), then no warm-up, no
    swap; the loaded model goes with the models the switch frees."""
    live = {"v": _live()}
    port = _Port()
    holder = {}

    class _OffDuringLoad(_Loader):
        def load(self, path, cand, model=None):
            out = super().load(path, cand, model)
            holder["bg"].set_mode("off")                  # the operator, while TensorRT deserialises
            holder["runner"] = out[1]
            return out
    bg, _, _ = _bg(live, loader=_OffDuringLoad(), port=port, mode="suggest")
    holder["bg"] = bg
    sw = _held_by_the_app(bg)
    done = []
    bg.request_switch(L1280, "switching to l @ 1280 (Apply)", on_done=lambda ok, t: done.append((ok, t)))
    sw.service()
    assert holder["runner"].calls == []                   # never warmed
    assert sw._request is None and not sw.drain() and port.calls == []
    assert len(sw._drop) == 1 and sw._drop[0][1] is holder["runner"]
    assert done == [(False, "not switched to l @ 1280: the automatic swipe is off")]


def test_key_off_between_the_warm_up_and_the_frame_does_not_swap():
    """Prepared (loaded and warmed), the key off before the loop's next frame: no swap, the model freed."""
    live = {"v": _live()}
    port = _Port()
    bg, _, _ = _bg(live, port=port, mode="suggest")
    sw = _held_by_the_app(bg)
    done = []
    bg.request_switch(L1280, "switching to l @ 1280 (Apply)", on_done=lambda ok, t: done.append((ok, t)))
    sw.service()
    assert sw._request is not None
    bg.set_mode("off")
    assert sw.drain() is True and port.calls == [] and sw._probation is None
    assert len(sw._drop) == 1 and sw.busy() is None
    assert done == [(False, "not switched to l @ 1280: the automatic swipe is off")]


def test_an_automatic_switch_asked_after_the_key_went_off_does_not_happen():
    """The look's advice read the mode before the operator set it off: its request comes after -- no swap; the
    resident is the looker's to release, at its next look."""
    from runtime.seamless_switch import Prepared
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    port = _Port()
    bg, _, _ = _bg(live, port=port, mode="auto")
    sw = _held_by_the_app(bg)
    model, runner, _ = _Loader().load(L1280.engine_file, L1280)           # a resident that warms and runs
    bg.resident = r = Resident(L1280, model, runner, bg._resident_key(live["v"]), 0.4)
    bg.set_mode("off")
    assert sw.request(L1280, "switching to l @ 1280: clearly better", auto=True,
                      prepared=Prepared(r.candidate, r.model, r.runner)) is None
    sw.service()
    assert runner.calls == []                               # never warmed
    assert not sw.drain() and port.calls == [] and sw._drop == [] and sw.fault is None
    bg.tick()
    assert bg.resident is None


def test_with_the_key_on_the_hold_changes_nothing():
    """Regression guard: the key on, the app's hold lets the operator's seamless Apply through."""
    live = {"v": _live()}
    port = _Port()
    bg, _, _ = _bg(live, port=port, mode="suggest")
    sw = _held_by_the_app(bg)
    bg.request_switch(L1280, "switching to l @ 1280 (Apply)")
    sw.service()
    assert sw.drain() is True and port.calls and port.calls[0][0] == "swap"


def test_key_off_stops_a_look_under_way():
    """Regression guard: the key off during a look -- it stops at its next frame, says nothing, and the resident it
    loaded goes at the next look."""
    bg, proc, events, loader, live = _looking_bg(mode="suggest")
    proc.frames = _look_frames()
    infer = loader.infer

    def off_at_the_third(images, cand, runner, **kw):
        pace = kw.get("pace")

        def paced():
            if loader.infers == 2:
                bg.set_mode("off")
            if pace is not None:
                pace()
        return infer(images, cand, runner, **{**kw, "pace": paced})
    loader.infer = off_at_the_third
    assert bg.tick() == "run"
    assert loader.infers == 3 and bg.suggestion is None and bg.last_look["outcome"] == "cancelled"
    assert not [k for k, _ in events if k in ("suggest", "switching")]
    assert bg.resident is not None
    bg.tick()
    assert bg.resident is None


# ----------------------------------------------------------------------------------------------------------
# A project load that brings the key on
# ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode,word", [("suggest", "suggest"), ("auto", "switch")])
def test_a_project_load_that_turns_the_key_on_says_so_once(mode, word, capsys, swipe_auto_unlocked):
    appmod = _app()
    from runtime import api
    live = {"v": _live()}
    bg, _, _ = _bg(live, mode="off")
    events = []
    fake = MagicMock()
    fake.swipe_bg, fake.bus = bg, SimpleNamespace(publish=events.append)
    fake._SWIPE_AUTO_LABELS = appmod.WallDanceApp._SWIPE_AUTO_LABELS
    fake._cb_swipe_auto = lambda m, quiet=False: appmod.WallDanceApp._cb_swipe_auto(fake, m, quiet=quiet)
    appmod.WallDanceApp._apply_config_without_model(fake, {"swipe_auto": mode})
    assert bg.mode == mode
    toasts = [e.message for e in events if isinstance(e, api.Toast)]
    assert toasts == [f"Automatic swipe: {word} (from the project)"]
    assert capsys.readouterr().out.count(f"[SwipeAuto] mode {mode} (from the project)") == 1


def test_a_project_load_with_the_key_off_says_nothing(capsys):
    """Regression guard: off (or absent: a full project) -- silent, as before."""
    appmod = _app()
    from runtime import api
    live = {"v": _live()}
    bg, _, _ = _bg(live, mode="suggest")
    events = []
    fake = MagicMock()
    fake.swipe_bg, fake.bus = bg, SimpleNamespace(publish=events.append)
    fake._SWIPE_AUTO_LABELS = appmod.WallDanceApp._SWIPE_AUTO_LABELS
    fake._cb_swipe_auto = lambda m, quiet=False: appmod.WallDanceApp._cb_swipe_auto(fake, m, quiet=quiet)
    appmod.WallDanceApp._apply_config_without_model(fake, {"swipe_auto": "off"})
    assert bg.mode == "off"
    assert not [e for e in events if isinstance(e, api.Toast)]
    assert "SwipeAuto" not in capsys.readouterr().out
