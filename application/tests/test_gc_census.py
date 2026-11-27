"""U3 (#t-029, the follow-up of the gc policy): a census of what grows, in a traced session only.

``core/gc_policy.py``'s ``TracedGcPolicy`` (``WD_GC_TRACE=1``, a DEV slot) counts the live objects by type right
after each full collection the policy makes and logs what grew and who holds it.  Here: on the real interpreter,
a type made to grow on purpose comes out as the top grower with its holder; untraced, the collection path reads,
calls and allocates nothing of the census; the census runs only inside the policy's own collections, never on a
RUN frame; its lines stay bounded; a census failure never escapes.
"""
from __future__ import annotations

import gc
import sys
import tracemalloc
from pathlib import Path

from core import gc_policy
from core.gc_policy import CENSUS_LINE_MAX, CENSUS_NAME_MAX, GcPolicy, TracedGcPolicy, _census_type_name
from test_gc_policy import FakeGc


class _Throwaway:
    pass


class _Marker:
    pass


class _Keeper:
    def __init__(self):
        self.items = []


_HELD: list = []


def _census(lines):
    return [ln for ln in lines if ln.startswith("[GC] census ")]


class _CountingGc(FakeGc):
    """FakeGc that records every generation ``get_objects`` is asked for (None = the whole heap: the census)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.asked = []

    def get_objects(self, generation=None):
        self.asked.append(generation)
        return super().get_objects(generation)


def test_a_traced_policy_is_the_census_one_and_an_untraced_one_is_not(monkeypatch):
    assert type(GcPolicy(gc_module=FakeGc(), trace=True)) is TracedGcPolicy
    assert type(GcPolicy(gc_module=FakeGc(), trace=False)) is GcPolicy
    monkeypatch.delenv("WD_GC_TRACE", raising=False)
    monkeypatch.setenv("WD_SLOT", "dev")
    assert type(GcPolicy(gc_module=FakeGc())) is TracedGcPolicy
    monkeypatch.setenv("WD_SLOT", "live")
    assert type(GcPolicy(gc_module=FakeGc())) is GcPolicy


def test_after_a_full_collection_the_census_names_the_grower_and_its_holder():
    lines = []
    p = GcPolicy(gc_module=gc, log=lines.append, trace=True)
    try:
        p.collect("start-up")
        first = _census(lines)
        assert len(first) == 1 and first[0].startswith("[GC] census #1 (start-up): ")
        assert "the baseline of the next censuses" in first[0]
        _HELD.extend(_Throwaway() for _ in range(10_000))
        lines.clear()
        p.collect("valve")
        name = _census_type_name(_Throwaway)
        assert lines[0].startswith("[GC] full collection (valve): ")      # the census follows the collection
        c = _census(lines)
        assert c[0].startswith("[GC] census #2 (valve): ") and " since #1 " in c[0]
        assert c[1] == f"[GC] census #2 grew since #1: 1. {name} 10000 +10000"
        holders = [ln for ln in c if " holders of " in ln]
        assert len(holders) <= gc_policy.CENSUS_HOLDERS
        assert holders[0] == (f"[GC] census #2 holders of {name} (5 newest): "
                              f"5x builtins.list <- module {__name__}._HELD")
        assert all(len(ln) <= CENSUS_LINE_MAX for ln in lines)
        lines.clear()
        p.collect("STANDBY")                    # nothing grew since: said, and the growth since the first kept
        c = _census(lines)
        assert not [ln for ln in c if " grew since #2: " in ln and f" {name} " in ln]
        assert f"[GC] census #3 grew since #1: 1. {name} 10000 +10000" in c
    finally:
        _HELD.clear()


def test_the_holder_s_own_holder_is_named_by_the_attribute_it_sits_under():
    lines = []
    keeper = _Keeper()
    p = GcPolicy(gc_module=gc, log=lines.append, trace=True)
    p.collect("start-up")
    keeper.items.extend(_Marker() for _ in range(6_000))
    p.collect("project load")
    name = _census_type_name(_Marker)
    held = [ln for ln in _census(lines) if f" holders of {name} " in ln]
    assert held == [f"[GC] census #2 holders of {name} (5 newest): "
                    f"5x builtins.list <- {_census_type_name(_Keeper)}.items"]
    del keeper


def test_untraced_the_policy_reads_and_calls_nothing_of_the_census():
    reads = []

    class Recording(GcPolicy):
        def __getattribute__(self, name):
            reads.append(name)
            return object.__getattribute__(self, name)

    fake, lines, calls = _CountingGc(gen_sizes=(100, 900, 400_000)), [], []
    p = Recording(gc_module=fake, log=lines.append, trace=False)
    assert type(p) is Recording and not [k for k in vars(p) if "census" in k]

    def prof(frame, event, arg):
        if event == "call":
            calls.append(frame.f_code.co_name)
        elif event == "c_call":
            calls.append(getattr(arg, "__name__", "?"))

    sys.setprofile(prof)
    try:
        p.start()                                        # the start-up collection
        fake.run(0)
        fake.run(1)
        fake.run(2)                                      # one the interpreter started by itself
        p.request("STANDBY")
        p.tick(in_run=False, wall_empty=True)            # a requested one
        p.pending = 10 ** 6
        p.tick(in_run=True, wall_empty=False)            # the valve trips, a dancer on the wall: waits
        p.tick(in_run=True, wall_empty=True)             # the valve's collection
    finally:
        sys.setprofile(None)
    assert fake.collects == 3 and len(p.collections) == 3
    assert not [r for r in reads if "census" in r.lower()]
    assert not [c for c in calls if "census" in c.lower() or c in ("get_referrers", "get_referents", "nlargest")]
    assert None not in fake.asked                        # the whole heap is never asked for
    assert not _census(lines) and len([ln for ln in lines if ln.startswith("[GC] full collection (")]) == 3


def _census_code_lines():
    src = Path(gc_policy.__file__).read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(src, 1) if ln.startswith("class TracedGcPolicy("))
    end = next(i for i, ln in enumerate(src, 1) if ln.startswith("POLICY = GcPolicy()"))
    return start, end


def _allocs_in_census_code(before, after):
    start, end = _census_code_lines()
    out = 0
    for d in after.compare_to(before, "lineno"):
        fr = d.traceback[0]
        if fr.filename == gc_policy.__file__ and start <= fr.lineno < end and d.count_diff > 0:
            out += d.count_diff
    return out


def _collect_lines():
    import inspect
    src, first = inspect.getsourcelines(GcPolicy.collect)
    return range(first, first + len(src))


def test_untraced_a_real_collection_allocates_nothing_more():
    """The real interpreter, untraced: what a collection leaves allocated comes only from ``GcPolicy.collect``'s
    own lines (the tuple it keeps in ``collections``), nothing in the census code, and the gc allocation count
    after it is the same at every collection (nothing accumulates).  Traced, the census code does allocate (the
    counts by type it keeps): the measure can see it."""
    off = GcPolicy(gc_module=gc, log=[].append, trace=False)
    on = GcPolicy(gc_module=gc, log=[].append, trace=True)
    counts = []
    for _ in range(3):
        gc.collect()
        bare = gc.get_count()[0]
        off.collect("x")
        counts.append(gc.get_count()[0] - bare)
    assert len(set(counts)) == 1, counts
    tracemalloc.start()
    try:
        s0 = tracemalloc.take_snapshot()
        off.collect("x")
        s1 = tracemalloc.take_snapshot()
        on.collect("x")
        s2 = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()
    kept = [d.traceback[0].lineno for d in s1.compare_to(s0, "lineno")
            if d.traceback[0].filename == gc_policy.__file__ and d.count_diff > 0]
    assert kept and set(kept) <= set(_collect_lines()), kept
    assert _allocs_in_census_code(s0, s1) == 0
    assert _allocs_in_census_code(s1, s2) > 0


def test_the_census_runs_only_inside_the_policy_s_own_collections(monkeypatch):
    calls = []
    monkeypatch.setattr(TracedGcPolicy, "_census", lambda self, reason: calls.append(reason))
    fake, lines = FakeGc(gen_sizes=(100, 900, 400_000)), []
    p = GcPolicy(gc_module=fake, log=lines.append, trace=True)
    p.start()
    assert calls == ["start-up"]
    for _ in range(300):                                 # RUN frames, a dancer on the wall: the valve trips, waits
        fake.run(0)
        fake.run(1)
        assert p.tick(in_run=True, wall_empty=False) is None
    fake.run(2)                                          # a full collection the interpreter started: no census
    p.tick(in_run=True, wall_empty=False)
    assert calls == ["start-up"] and [ln for ln in lines if ln.startswith("[GC] valve: ")]
    assert p.tick(in_run=True, wall_empty=True) == "valve"
    assert calls == ["start-up", "valve"] and fake.collects == len(p.collections) == 2
    for _ in range(300):                                 # RUN frames with nothing due: no census
        fake.run(0)
        p.tick(in_run=True, wall_empty=True)
    assert calls == ["start-up", "valve"]


def test_census_lines_stay_bounded():
    long_type = type("L" * 400, (), {})
    lines = []
    p = GcPolicy(gc_module=gc, log=lines.append, trace=True)
    p.collect("start-up")
    _HELD.extend(long_type() for _ in range(3_000))
    try:
        p.collect("valve")
    finally:
        _HELD.clear()
    c = _census(lines)
    assert c and all(len(ln) <= CENSUS_LINE_MAX for ln in lines)
    assert any(("L" * (CENSUS_NAME_MAX - len(__name__) - 1)) in ln for ln in c)


def test_a_census_failure_never_escapes():
    """FakeGc cannot list the whole heap: the census says so in one line and the collection stands."""
    lines = []
    p = GcPolicy(gc_module=FakeGc(), log=lines.append, trace=True)
    p.start()
    assert p.installed and len(p.collections) == 1
    assert [ln for ln in lines if ln.startswith("[GC] census #1 failed: TypeError")]
    p.uninstall()
