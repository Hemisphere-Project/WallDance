"""X4c (hub walldance#t-055): the dancer step on the tip 8023250, where it meets what landed after its base.

- the garbage-collection policy (U3, core/gc_policy.py): the step runs as a calibration (``_calibrating``), so its
  end -- finished on its card, cancelled or dropped -- is a « Calibrate ended » moment: a full collection is
  requested then, and none runs while it measures;
- the clean start (A2, app._cb_project_start_clean), on the real switch path (test_project_switch_clean's
  harness: the real ConfigManager, CameraController and MainLoop pumps): a project switch about to run cancels a
  step that measures, or its card, and puts everything back, then the switch loads the other project and starts
  clean; a step the clean start meets after the load is dropped, nothing of the old project put back; a camera
  source change cancels it (its frames were of the other view); New project saves the project as it was before
  the step, never the step's unkept values;
- the Scene Calibration card's « Wall light » line (X2 follow-up): the step never starts while the empty-wall
  picture is being taken (a dancer would stand on it, and the card waiting for its verdict would lose it); the
  step's card is an event of its own; the empty-wall check X4 runs with the enhancement ON still gives the card
  the readiness row's verdict;
- the plate's camera facts (X6 follow-up): stamped when Calibrate takes the plate, whatever X4 switched; the
  step's Keep writes the held plate with them.
"""
import copy
import inspect
from types import SimpleNamespace

import pytest

import test_x4_dancer_step as X4                      # the step's harness (the real CalibrationFlows on stubs)
from core import gc_policy
from test_gc_policy import _policy                     # a policy on a fake gc

HER = (200.0, 100.0, 240.0, 300.0, 0.6)               # a 200 px dancer inside X2b's zone (x0, y0, x1, y1, conf)


# ------------------------------------------------------------------ the gc policy: the end of the step

@pytest.fixture
def app_policy(monkeypatch):
    p, fake, lines = _policy()
    monkeypatch.setattr(gc_policy, "POLICY", p)
    p.start()                                          # the app is up: the start-up collection
    return p, fake, lines


def _gc_app(flows, state="STANDBY"):
    return SimpleNamespace(system_state=SimpleNamespace(name=state), calibration=flows,
                           processor=SimpleNamespace(slot_states=lambda: [], _plate_capture=None))


def _full(lines):
    return [ln for ln in lines if ln.startswith("[GC] full collection")]


def test_the_end_of_the_dancer_step_requests_a_collection_and_none_runs_while_it_measures(app_policy):
    p, fake, lines = app_policy
    flows, proc, settings = X4._flows(X4._helps)
    app = _gc_app(flows)
    flows._cb_dancer_step()
    p.request("model load")                            # a moment queued while the step measures waits for its end
    n = 0
    while flows._calibrating and n < 400:              # the main loop: a frame, the step, the render stage's end
        proc.render(settings, flows.enhancer)
        flows._step_calibration([], 50.0)
        gc_policy.app_tick(app, [])
        n += 1
        if flows._calibrating:
            assert fake.collects == 1, f"a full collection while the step measures (frame {n})"
    assert flows._dancer_pending is not None           # it ended on its card
    assert fake.collects == 2 and "Calibrate ended" in _full(lines)[-1] and "model load" in _full(lines)[-1]
    flows._cb_dancer_decision(False)
    for _ in range(5):
        gc_policy.app_tick(app, [])
    assert fake.collects == 2                          # the card's answer is no new moment


@pytest.mark.parametrize("end", ["second press", "project changed", "stall"])
def test_a_step_that_stops_requests_one_too(app_policy, end):
    p, fake, lines = app_policy
    flows, proc, settings = X4._flows(X4._helps)
    app = _gc_app(flows)
    flows._cb_dancer_step()
    for _ in range(30):
        proc.render(settings, flows.enhancer)
        flows._step_calibration([], 50.0)
        gc_policy.app_tick(app, [])
    assert fake.collects == 1
    if end == "second press":
        flows._cb_dancer_step()
    elif end == "project changed":
        flows.configs._current_project = "another"
        flows.dancer_watch()
    else:
        import time
        flows.dancer_watch(now=time.monotonic() + 6.0)
    assert not flows._calibrating
    gc_policy.app_tick(app, [])
    assert fake.collects == 2 and "Calibrate ended" in _full(lines)[-1]


def test_in_run_the_collection_at_the_end_of_the_step_waits_for_the_empty_wall(app_policy):
    p, fake, lines = app_policy
    flows, proc, settings = X4._flows(X4._helps)
    app = _gc_app(flows, state="RUN")
    flows._cb_dancer_step()
    n = 0
    while flows._calibrating and n < 400:
        proc.render(settings, flows.enhancer)
        flows._step_calibration([], 50.0)
        gc_policy.app_tick(app, [object()])            # she is tracked on the wall, to the step's end and after
        n += 1
    assert flows._dancer_pending is not None
    gc_policy.app_tick(app, [object()])
    assert fake.collects == 1
    gc_policy.app_tick(app, [])
    assert fake.collects == 2 and "Calibrate ended" in _full(lines)[-1]


# ------------------------------------------------------------------ the clean start (A2) on the real switch path

def _values(flows):
    return (float(flows.enhancer.gamma), float(flows.enhancer.clahe_clip), bool(flows.settings.enhance_enabled),
            int(flows.settings.person_height_px))


def _switch_rig(tmp_path, monkeypatch):
    """test_project_switch_clean's session with the real CalibrationFlows of X4's harness as the app's
    ``calibration``, on the session's real ConfigManager (its project, its queued switch)."""
    import test_project_switch_clean as T
    s = T._session(tmp_path, monkeypatch)
    flows, dproc, dset = X4._flows(X4._helps)
    flows.configs = s.mgr
    s.app.calibration = flows
    s.flows, s.dproc, s.dset, s.T = flows, dproc, dset, T
    s.before = _values(flows)
    T._run(s, 0.5)                                     # the camera flows: the old project, an empty wall
    return s


def _measuring(s, frames=200):
    """The step started, then frames until the enhancement it tries is ON: the live values are the step's."""
    s.flows._cb_dancer_step()
    for _ in range(frames):
        s.dproc.render(s.dset, s.flows.enhancer)
        s.flows._step_calibration([], 50.0)
        if s.dset.enhance_enabled:
            break
    assert s.flows._calibrating and s.flows._dancer is not None and _values(s.flows) != s.before


def _card(s):
    """The step ran to its card: its values set live, its provenance stamped, Keep not pressed."""
    s.flows._cb_dancer_step()
    X4._main_loop(s.flows, s.dproc, s.dset)
    assert s.flows._dancer_pending is not None and "dancer_step" in s.flows.calibration_state
    assert _values(s.flows) != s.before


START = {"measuring": _measuring, "card": _card}


def _put_back(s):
    assert not s.flows.dancer_busy and not s.flows._calibrating
    assert _values(s.flows) == s.before and "dancer_step" not in s.flows.calibration_state


@pytest.mark.parametrize("what", list(START))
def test_a_project_switch_cancels_the_step_puts_it_back_then_starts_clean(tmp_path, monkeypatch, what):
    s = _switch_rig(tmp_path, monkeypatch)
    START[what](s)
    s.mgr._cb_do_load_config(s.paths["showB"])        # Load: the switch is queued for the loop's pumps
    src = inspect.getsource(s.T.ml.MainLoop._tick)
    assert src.index("dancer_watch") < src.index("self._tick_pumps()")    # one tick: the watch, then the pumps
    s.flows.dancer_watch()
    _put_back(s)                                       # the old project as it was before the step
    assert "stopped by a project switch" in s.flows.ui.show_toast.call_args[0][0]
    assert s.T._tick(s) == "pump"                      # the switch
    assert s.mgr._current_project == "showB" and len(s.osc.clears) == 1     # A2: it started clean
    assert not s.flows.dancer_busy and _values(s.flows) == s.before


def test_a_step_the_clean_start_meets_after_the_load_is_dropped_nothing_put_back(tmp_path, monkeypatch, capsys):
    s = _switch_rig(tmp_path, monkeypatch)
    loaded = {"gamma": {"source": "aim", "ts": "2026-12-12 20:00", "epoch": 9.0}}

    def apply(cfg):                                    # what app._apply_config_without_model does to the provenance
        s.applied.append(cfg)
        s.flows.calibration_state = loaded
    s.mgr.apply_config = apply
    _measuring(s)
    s.mgr._pending_project_switch = s.paths["showB"]   # a switch the watch did not see (the pumps alone)
    assert s.T._tick(s) == "pump"
    assert not s.flows.dancer_busy and not s.flows._calibrating
    assert s.flows.calibration_state is loaded and "dancer_step" not in loaded    # nothing of the old project
    assert "nothing put back" in capsys.readouterr().out
    assert s.mgr._current_project == "showB" and len(s.osc.clears) == 1


@pytest.mark.parametrize("what", list(START))
def test_a_camera_source_change_cancels_the_step_and_puts_it_back(tmp_path, monkeypatch, what):
    s = _switch_rig(tmp_path, monkeypatch)
    START[what](s)
    s.cam.arm(0.0, 1.0)
    s.cams._cb_camera_change("1")                      # another view (CameraController, as the app wires it)
    _put_back(s)                                       # its frames were of the other view: never decided on them
    assert "stopped by a change of camera or project" in s.flows.ui.show_toast.call_args[0][0]
    assert len(s.osc.clears) == 1                      # A2: the new view started clean


@pytest.mark.parametrize("what", list(START))
def test_new_project_saves_the_project_as_before_the_step(tmp_path, monkeypatch, what):
    import test_view_change_clean as V
    s = _switch_rig(tmp_path, monkeypatch)
    saved = []

    def saveable():                                    # the live values a save writes (app._get_saveable_config)
        f = s.flows
        cfg = {"gamma": float(f.enhancer.gamma), "clahe": float(f.enhancer.clahe_clip),
               "enhance_enabled": bool(f.settings.enhance_enabled),
               "person_height_px": int(f.settings.person_height_px),
               "calibration_state": copy.deepcopy(f.calibration_state)}
        saved.append(cfg)
        return dict(cfg)
    s.mgr.saveable_config = saveable
    START[what](s)
    V._new_project(s)                                  # Picker « New »: the default model, the clean start, the save
    assert s.mgr._current_project == "showC" and saved
    _put_back(s)
    last = saved[-1]
    assert (last["gamma"], last["clahe"], last["enhance_enabled"], last["person_height_px"]) == s.before
    assert "dancer_step" not in last["calibration_state"]


# ------------------------------------------------------------------ the Scene Calibration card (X2) and the plate (X6)

def _x2b(tmp_path, **kw):
    import test_x2b_card_wall_light as x2b
    r = x2b._rig(tmp_path, playing=True, **kw)
    r.app.settings.enhance_enabled = False             # the dancer step's snapshot reads it (app.py: the settings)
    r.x2b = x2b
    return r


def _dancer_to_card(r, frames=400):
    """A DANCER, 10 s with her on the wall: her box on every processed frame until the step ends."""
    r.flows._cb_dancer_step()
    assert r.flows._calibrating
    n = 0
    while r.flows._calibrating and n < frames:
        r.proc._feed_plate_capture(r.frame)
        r.proc.last_raw_boxes, r.proc.last_fg, r.proc.last_lb_scale = [HER], None, 1.0
        r.flows._step_calibration([], 30.0)
        n += 1
    assert not r.flows._calibrating and r.flows._dancer_pending is not None


def test_the_step_waits_for_the_empty_wall_picture_and_its_card_is_its_own(tmp_path):
    r = _x2b(tmp_path, kind="hot", wall_check=False)
    x2b = r.x2b
    x2b._calibrate(r)
    assert r.proc.plate_status == "capturing"          # Calibrate's card came first: it waits for its verdict
    assert x2b._light_lines(x2b._cards(r)[-1]) == [x2b.MEASURING]
    r.flows._cb_dancer_step()                          # A DANCER pressed at once
    assert r.flows._dancer is None and not r.flows._calibrating
    toasts = [e.message for e in r.events if isinstance(e, r.api.Toast)]
    assert toasts[-1].startswith("The empty-wall picture is being taken")
    x2b._frames(r, 60)                                 # the picture ends: the card comes again, with the verdict
    cards = x2b._cards(r)
    assert r.proc.plate_status == "ready" and len(cards) == 2
    assert x2b._light_lines(cards[1]) == [x2b._row_line(r)] and cards[1].startswith(cards[0].rsplit("\n", 1)[0])
    _dancer_to_card(r)                                 # now the step runs; it ends on ITS card
    (card,) = [e for e in r.events if isinstance(e, r.api.DancerStepCard)]
    assert card.line.startswith("Set: person height 200 px") and "Wall light" not in card.line
    assert x2b._cards(r) == cards                      # the Scene Calibration card is not published again


@pytest.mark.parametrize("off_by", ["hand", "the step's none"])
def test_the_empty_wall_check_with_the_enhancement_on_still_gives_the_card_its_verdict(tmp_path, off_by):
    r = _x2b(tmp_path, kind="hot", wall_check=True)
    x2b = r.x2b
    if off_by != "hand":
        r.flows.calibration_state["dancer_step"] = {"source": "dancers", "ts": "2026-12-12 19:50", "epoch": 1.0,
                                                    "enh": "none"}
    x2b._calibrate(r)
    syncs = [e.value for e in r.events if isinstance(e, r.api.ControlSync) and e.name == "enhance"]
    assert syncs[:1] == [True]                         # X4: ON while the check tests its settings
    assert r.app.settings.enhance_enabled is (off_by != "hand")   # by hand: off again; the step's none: stays on
    cards = x2b._every_card_has_one_line(r)
    (line,) = x2b._light_lines(cards[-1])
    assert line == x2b._row_line(r) and line.startswith("Wall light: RED - ")
    exp, gain = r.app.cameras.exposure_gain_now()      # X6: stamped as Calibrate took it, before any Save
    assert (r.proc._fg_detector.plate.camera["exposure_us"], r.proc._fg_detector.plate.camera["gain_db"]) == (exp, gain)


def test_the_steps_keep_writes_calibrates_plate_with_the_camera_it_was_taken_at(tmp_path):
    from core.foreground import CleanPlate
    r = _x2b(tmp_path, kind="flat", wall_check=True)
    r.x2b._calibrate(r)
    r.x2b._frames(r, 60)
    assert r.proc.plate_status == "ready" and r.flows._pending_plate is not None     # held until Save (A6 ②)
    taken = dict(r.proc._fg_detector.plate.camera)
    assert (taken["exposure_us"], taken["gain_db"]) == r.app.cameras.exposure_gain_now()
    r.app.cameras._cb_ids_exposure_change(taken["exposure_us"] + 15000.0)   # the camera moves after Calibrate
    _dancer_to_card(r)
    assert not (tmp_path / "proj").exists()            # nothing written before Keep
    r.flows._cb_dancer_decision(True)                  # Keep: the project saved, Calibrate's held plate written
    written = CleanPlate.load(str(tmp_path / "proj" / "plates" / "latest.npz"))
    assert written.camera == taken
