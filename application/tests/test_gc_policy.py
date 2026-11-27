"""U3 (#t-029): no full garbage collection in the middle of a show (core/gc_policy.py).

The policy runs against a fake ``gc`` module (thresholds, callbacks, collections observed), the app's moments
through their real call sites (main loop start-up and render stage, project switch, model load), and two checks
on the real interpreter: the third threshold after start, and no automatic full collection on a churning heap.
"""
from __future__ import annotations

import ast
import gc
import importlib
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import gc_policy
from core.gc_policy import THRESHOLD2, VALVE_DEFAULT, GcPolicy, wall_empty

SRC = Path(__file__).resolve().parents[1] / "src"
TESTS = Path(__file__).resolve().parent


class FakeGc:
    """The ``gc`` surface the policy uses; ``collect`` drives the callbacks like CPython does."""

    def __init__(self, gen_sizes=(0, 0, 400_000)):
        self.threshold = (700, 10, 10)
        self.callbacks = []
        self.collects = 0
        self.sizes = list(gen_sizes)

    def get_threshold(self):
        return self.threshold

    def set_threshold(self, *t):
        self.threshold = tuple(t)

    def get_count(self):
        return (0, 0, 0)

    def get_objects(self, generation=None):
        return [None] * self.sizes[generation]

    def run(self, generation, collected=0):
        """One collection the interpreter started by itself."""
        for cb in list(self.callbacks):
            cb("start", {"generation": generation, "collected": 0, "uncollectable": 0})
        for cb in list(self.callbacks):
            cb("stop", {"generation": generation, "collected": collected, "uncollectable": 0})

    def collect(self):
        self.collects += 1
        self.run(2)
        return 3


def _policy(**kw):
    fake = FakeGc(**kw)
    lines = []
    p = GcPolicy(gc_module=fake, log=lines.append, trace=False)
    return p, fake, lines


@pytest.fixture
def app_policy(monkeypatch):
    """The process-wide policy replaced by one on a fake gc (the real interpreter is left alone)."""
    p, fake, lines = _policy()
    monkeypatch.setattr(gc_policy, "POLICY", p)
    return p, fake, lines


def _full_lines(lines):
    return [ln for ln in lines if ln.startswith("[GC] full collection (")]


# --------------------------------------------------------------------------------------------- the policy
def test_install_raises_only_the_third_threshold_and_uninstall_restores():
    p, fake, _ = _policy()
    p.install()
    assert fake.threshold == (700, 10, THRESHOLD2)
    assert p._on_gc in fake.callbacks
    p.uninstall()
    assert fake.threshold == (700, 10, 10) and fake.callbacks == []


def test_start_collects_once_now_and_logs_one_line():
    p, fake, lines = _policy()
    p.start()
    assert fake.collects == 1
    full = _full_lines(lines)
    assert len(full) == 1 and full[0].startswith("[GC] full collection (start-up): ")
    assert " ms, 3 objects freed, 400000 in gen 2" in full[0]
    assert p.total == 400_000 and p.pending == 0
    assert p.valve_bound() == 100_000


def test_requests_before_install_are_ignored():
    """A replay (or a test) that never starts the policy: a load's request is a no-op."""
    p, fake, _ = _policy()
    p.request("model load")
    p.install()
    assert p.tick(in_run=False, wall_empty=True) is None
    assert fake.collects == 0


def test_a_request_is_served_at_once_outside_run():
    p, fake, lines = _policy()
    p.start()
    p.request("project load")
    p.request("model load")
    p.request("model load")
    assert p.tick(in_run=False, wall_empty=False) == "project load, model load"
    assert fake.collects == 2
    assert _full_lines(lines)[-1].startswith("[GC] full collection (project load, model load): ")
    assert p.tick(in_run=False, wall_empty=True) is None      # served once
    assert fake.collects == 2


def test_a_request_in_run_waits_for_the_empty_wall():
    p, fake, _ = _policy()
    p.start()
    p.request("model load")
    for _ in range(50):
        assert p.tick(in_run=True, wall_empty=False) is None
    assert fake.collects == 1
    assert p.tick(in_run=True, wall_empty=True) == "model load"
    assert fake.collects == 2


def test_nothing_is_collected_while_busy():
    p, fake, _ = _policy()
    p.start()
    p.request("STANDBY")
    assert p.tick(in_run=False, wall_empty=True, busy=True) is None
    assert p.tick(in_run=False, wall_empty=True) == "STANDBY"


def test_no_collection_without_a_moment():
    """Frames with and without a dancer, in RUN and STANDBY: no request, no valve -> never a collection."""
    p, fake, lines = _policy()
    p.start()
    for i in range(2000):
        p.tick(in_run=i % 3 != 0, wall_empty=i % 2 == 0)
    assert fake.collects == 1 and len(_full_lines(lines)) == 1


def test_gen1_survivors_count_as_waiting_in_gen2():
    p, fake, _ = _policy(gen_sizes=(500, 4000, 400_000))
    p.start()
    fake.run(1, collected=300)                                 # 4500 examined, 4200 survive into gen 2
    fake.run(0, collected=10)                                  # gen 0: its survivors stay young
    assert p.pending == 4200
    assert p.stats[1][0] == 1 and p.stats[0][0] == 1
    fake.run(2)                                                # anyone's full collection empties it
    assert p.pending == 0


def test_the_valve_waits_for_an_empty_wall_and_logs_once():
    p, fake, lines = _policy(gen_sizes=(0, 30_000, 400_000))
    p.start()
    p.tick(in_run=True, wall_empty=False)                      # the first live frame: it built nothing
    bound = p.valve_bound()
    while p.pending < bound:
        fake.run(1)
    held = [(1, "coasting", 0.4), (2, "lost", 3.0)]
    proc = SimpleNamespace(slot_states=lambda: held)
    for _ in range(100):                                       # a dancer tracked, then a slot still held
        assert p.tick(in_run=True, wall_empty=wall_empty([object()], proc)) is None
        assert p.tick(in_run=True, wall_empty=wall_empty([], proc)) is None
    valve = [ln for ln in lines if ln.startswith("[GC] valve: ")]
    assert len(valve) == 1 and f"(bound {bound})" in valve[0]
    assert fake.collects == 1
    held[0] = (1, "lost", 0.0)
    assert p.tick(in_run=True, wall_empty=wall_empty([], proc)) == "valve"
    assert fake.collects == 2 and p.pending == 0


def test_the_valve_bound_before_any_measure():
    p, _, _ = _policy()
    assert p.valve_bound() == VALVE_DEFAULT


def test_wall_empty_in_doubt_is_not_empty():
    def boom():
        raise RuntimeError("no slots")
    assert wall_empty([], SimpleNamespace(slot_states=boom)) is False
    assert wall_empty([], None) is True
    assert wall_empty([], SimpleNamespace(slot_states=lambda: [])) is True


def test_a_full_collection_outside_the_policy_is_logged_between_frames():
    p, fake, lines = _policy()
    p.start()
    fake.run(2, collected=7)                                    # e.g. a library's own gc.collect()
    assert not [ln for ln in lines if "outside the policy" in ln]   # never printed inside a collection
    p.tick(in_run=True, wall_empty=False)
    out = [ln for ln in lines if "outside the policy" in ln]
    assert len(out) == 1 and "7 objects freed" in out[0]


def test_the_collection_count_moves_by_one_per_full_collection_and_never_on_a_refused_or_deferred_request():
    """``collected_n`` (the main loop compares it around a tick, P6): +1 at each full collection the policy runs;
    unmoved by a request refused (before install, after uninstall) or deferred (busy, a dancer on the wall), by a
    tick with nothing due, and by a collection the interpreter started by itself; never reset."""
    p, fake, _ = _policy()
    assert p.collected_n == 0
    p.request("model load")                                     # before install: refused
    assert p.tick(in_run=False, wall_empty=True) is None and p.collected_n == 0
    p.start()
    assert p.collected_n == 1                                   # the start-up collection
    p.request("model load")
    assert p.tick(in_run=False, wall_empty=True, busy=True) is None and p.collected_n == 1     # busy: deferred
    for _ in range(5):
        assert p.tick(in_run=True, wall_empty=False) is None    # a dancer: deferred
    assert p.collected_n == 1
    assert p.tick(in_run=True, wall_empty=True) == "model load" and p.collected_n == 2
    assert p.tick(in_run=False, wall_empty=True) is None and p.collected_n == 2              # nothing due
    fake.run(2, collected=7)                                    # not the policy's
    p.collect("by hand")
    assert p.collected_n == 3 and fake.collects == 3
    p.uninstall()
    p.request("STANDBY")                                        # after uninstall: refused
    assert p.tick(in_run=False, wall_empty=True) is None and p.collected_n == 3



def test_collecting_is_true_from_the_start_of_the_policys_full_collection_to_its_end():
    """``collecting`` (read by an engine's release, P6): true inside a full collection the policy makes -- seen by
    the gc callbacks at its start and its end --, false before and after, false after one that raised, never true
    for a collection the interpreter started by itself."""
    p, fake, _ = _policy()
    seen = []
    fake.callbacks.append(lambda phase, info: seen.append((phase, p.collecting)))
    assert p.collecting is False
    p.start()                                                   # the start-up collection
    assert seen == [("start", True), ("stop", True)] and p.collecting is False
    seen.clear()
    fake.run(2)                                                 # not the policy's
    assert seen == [("start", False), ("stop", False)]

    def boom():
        raise RuntimeError("gc failed")
    fake.collect = boom
    with pytest.raises(RuntimeError):
        p.collect("by hand")
    assert p.collecting is False

# ------------------------------------------------------------------------------- the app's moments (hooks)
def _app(state="RUN", calibrating=False, slots=()):
    return SimpleNamespace(last_fps_time=0.0, system_state=SimpleNamespace(name=state),
                           calibration=SimpleNamespace(_calibrating=calibrating, _calibrating2=False),
                           processor=SimpleNamespace(slot_states=lambda: list(slots), _plate_capture=None))


def test_entering_standby_requests_and_leaving_it_does_not(app_policy):
    p, fake, lines = app_policy
    p.start()
    app = _app("RUN")
    gc_policy.app_tick(app, [object()])
    app.system_state = SimpleNamespace(name="STANDBY")
    gc_policy.app_tick(app, [])
    assert fake.collects == 2 and _full_lines(lines)[-1].startswith("[GC] full collection (STANDBY): ")
    app.system_state = SimpleNamespace(name="RUN")
    for _ in range(20):
        gc_policy.app_tick(app, [object()])
        gc_policy.app_tick(app, [])
    assert fake.collects == 2


def test_calibrate_end_requests_and_waits_for_its_end(app_policy):
    p, fake, lines = app_policy
    p.start()
    app = _app("STANDBY", calibrating=True)
    for _ in range(5):
        gc_policy.app_tick(app, [])
    assert fake.collects == 1
    app.calibration._calibrating = False
    gc_policy.app_tick(app, [])
    assert fake.collects == 2 and "(Calibrate ended)" in _full_lines(lines)[-1]


def test_a_tick_error_never_escapes(app_policy):
    p, _, lines = app_policy
    p.start()
    gc_policy.app_tick(object(), None)                         # no attributes at all
    gc_policy.app_tick(SimpleNamespace(system_state=None, calibration=None, processor=None), [])


def test_project_load_requests(app_policy):
    from runtime.config_manager import ConfigManager
    p, fake, _ = app_policy
    p.start()
    wd = SimpleNamespace(push_busy=lambda what: None, pop_busy=lambda: None)
    me = SimpleNamespace(watchdog=lambda: wd, _execute_project_switch_impl=lambda path: True)
    assert ConfigManager._execute_project_switch(me, "/x/p.json") is True
    assert p._requests == ["project load"]


def test_model_load_requests(app_policy):
    pytest.importorskip("ultralytics")
    from runtime.model_controller import ModelController
    p, fake, _ = app_policy
    p.start()
    wd = SimpleNamespace(push_busy=lambda what: None, pop_busy=lambda: None)

    def fails(name, force_pt):
        raise RuntimeError("engine")
    me = SimpleNamespace(watchdog=lambda: wd, _load_model_with_progress_impl=lambda name, force_pt: True)
    assert ModelController._load_model_with_progress(me, "yolo11x-pose") is True
    assert p._requests == ["model load"]
    p._requests.clear()
    me._load_model_with_progress_impl = fails                  # a failed load also left garbage behind
    with pytest.raises(RuntimeError):
        ModelController._load_model_with_progress(me, "yolo11x-pose")
    assert p._requests == ["model load"]


def _import_main_loop():
    """As test_main_loop_guards: without torch/ultralytics, against a one-attribute ``core.pipeline`` stub."""
    if importlib.util.find_spec("torch") and importlib.util.find_spec("ultralytics"):
        import runtime.main_loop as ml
        return ml
    import core
    stub = types.ModuleType("core.pipeline")
    stub.ScaledTrack = object
    sys.modules["core.pipeline"] = stub
    try:
        import runtime.main_loop as ml
    finally:
        sys.modules.pop("core.pipeline", None)
        if getattr(core, "pipeline", None) is stub:
            delattr(core, "pipeline")
    return ml


def _startup_app():
    calls = []
    store = SimpleNamespace(read_last_project=lambda: None, list_projects=lambda: [])
    app = SimpleNamespace(
        last_fps_time=0.0,
        cameras=SimpleNamespace(_do_camera_refresh=lambda: None, _attempt_camera_connect=lambda s: True),
        camera=SimpleNamespace(state=SimpleNamespace(source=0)),
        ui=SimpleNamespace(create_gui=lambda c: None, setup_viewport=lambda r: None),
        _get_gui_config=lambda: {},
        roi=SimpleNamespace(_sync_roi_ui=lambda: None),
        configs=SimpleNamespace(config_store=store, _current_project=None),
        _startup_review=SimpleNamespace(config_path=None, project=None),
        models=SimpleNamespace(_load_default_model_startup=lambda: calls.append("model") or True),
        recorder=SimpleNamespace(set_project=lambda p: None),
        recording=SimpleNamespace(_update_recording_ui=lambda: None, _apply_startup_review_mode=lambda: None),
        processor=None)
    return app, calls


def test_after_app_start_the_third_threshold_is_large_and_the_startup_collection_ran(app_policy, monkeypatch):
    ml = _import_main_loop()
    p, fake, lines = app_policy
    monkeypatch.setattr(ml, "WEB_MONITOR_ENABLED", False)
    monkeypatch.setattr(ml, "REMOTE_API_ENABLED", False)
    app, calls = _startup_app()
    loop = ml.MainLoop(app)
    assert fake.threshold == (700, 10, 10) and fake.collects == 0
    assert loop._startup() is True
    assert calls == ["model"]                                   # the model loaded (and warmed) before
    assert fake.threshold == (700, 10, THRESHOLD2)
    assert fake.collects == 1 and _full_lines(lines)[0].startswith("[GC] full collection (start-up): ")


def test_the_render_stage_serves_a_request_between_frames(app_policy):
    ml = _import_main_loop()
    p, fake, lines = app_policy
    p.start()
    app = _app("RUN")
    app.ui = SimpleNamespace(render_frame_raw=lambda: None)
    app.timing = {}
    loop = ml.MainLoop(app)
    p.request("model load")
    loop._tick_render(ml._Tick(tracked=[object()]))             # a dancer: it waits
    assert fake.collects == 1
    loop._tick_render(ml._Tick(tracked=[]))
    assert fake.collects == 2 and "(model load)" in _full_lines(lines)[-1]


# -------------------------------------------------------------------------------- the real interpreter
def test_real_interpreter_third_threshold_and_no_automatic_full_collection():
    """On the real ``gc``: after start the third threshold is THRESHOLD2, and a heap churning well past CPython's
    own trigger (pending >= total/4 and 11 gen-1 collections) sees gen-1 collections but no full one."""
    saved = gc.get_threshold()
    lines = []
    p = GcPolicy(log=lines.append, trace=False)
    full = []

    def watch(phase, info):
        if phase == "stop" and info["generation"] == 2:
            full.append(info)
    try:
        p.start()
        assert gc.get_threshold() == (saved[0], saved[1], THRESHOLD2)
        gc.callbacks.append(watch)
        keep = []
        for j in range(400_000):
            x = [j]
            x.append(x)                                         # cyclic garbage
            if j % 4 == 0:
                keep.append([j])                                # survivors: they reach gen 2
        assert p.stats[1][0] > 11 and p.pending > p.total // 4
        assert full == []
    finally:
        if watch in gc.callbacks:
            gc.callbacks.remove(watch)
        p.uninstall()
    assert gc.get_threshold() == saved


def test_import_has_no_effect_on_the_interpreter():
    saved, n_cb = gc.get_threshold(), len(gc.callbacks)
    importlib.reload(gc_policy)
    assert gc.get_threshold() == saved and len(gc.callbacks) == n_cb
    assert gc_policy.POLICY.installed is False


def _local_imports(path: Path):
    """The repo modules a file imports (any depth of the file: lazy imports count), as files."""
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        for n in names:
            rel = Path(*n.split("."))
            for base in (SRC, TESTS):
                for cand in (base / rel.with_suffix(".py"), base / rel / "__init__.py"):
                    if cand.is_file():
                        out.append(cand)
    return out


def test_replays_never_reach_the_policy():
    """The headless replays measure: their numbers must not move.  Nothing they import, at any depth, imports
    the policy (only the app's runtime does: main loop, project switch, model load)."""
    seen, todo = set(), [TESTS / "replay.py", TESTS / "swipe_replay.py", TESTS / "slot_replay.py"]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        todo.extend(_local_imports(f))
    assert SRC / "core" / "pipeline.py" in seen                  # the scan does follow the processor
    assert SRC / "core" / "gc_policy.py" not in seen, sorted(str(f.relative_to(SRC.parent)) for f in seen)


def test_trace_logs_gen1_and_a_summary_each_minute(monkeypatch):
    """WD_GC_TRACE=1 or a DEV-slot session: each gen-1 collection and a summary every TRACE_EVERY_S (the real
    app's cadence, read from its log); off otherwise."""
    now = [0.0]
    fake, lines = FakeGc(gen_sizes=(100, 900, 400_000)), []
    p = GcPolicy(gc_module=fake, log=lines.append, clock=lambda: now[0], trace=True)
    p.start()
    fake.run(1, collected=50)
    p.tick(in_run=True, wall_empty=False)
    assert [ln for ln in lines if ln.startswith("[GC] gen 1: ")][0].endswith("950 to gen 2, ~950 waiting there")
    now[0] = gc_policy.TRACE_EVERY_S + 1.0
    p.tick(in_run=True, wall_empty=False)
    assert [ln for ln in lines if ln.startswith("[GC] 60 s: gen0 0")]
    monkeypatch.delenv("WD_GC_TRACE", raising=False)
    monkeypatch.setenv("WD_SLOT", "live")
    assert GcPolicy(gc_module=FakeGc()).trace is False
    monkeypatch.setenv("WD_SLOT", "dev")
    assert GcPolicy(gc_module=FakeGc()).trace is True


def test_a_first_live_frame_that_still_built_a_lot_is_collected_after_it():
    p, fake, lines = _policy(gen_sizes=(0, 30_000, 400_000))
    p.start()
    fake.run(1)                                                 # the first frame moved 30k objects into gen 2
    assert p.tick(in_run=True, wall_empty=True) == "start-up, the first live frame"
    fake.run(1)
    assert p.tick(in_run=True, wall_empty=True) is None         # only the first frame
    q, fake2, _ = _policy(gen_sizes=(0, 5_000, 400_000))
    q.start()
    fake2.run(1)                                                # a warm model: little built -> nothing more
    assert q.tick(in_run=True, wall_empty=True) is None and fake2.collects == 1
