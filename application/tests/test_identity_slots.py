"""Identity-slot output layer (CONT-6): pure unit tests on synthetic tracks."""
import math

import numpy as np
import pytest

from core.identity_slots import (
    IdentitySlots, OneEuro2D, SlotCandidate, SlotParams, STATE_BELT,
    STATE_COASTING, STATE_LIVE, STATE_LOST, candidate_from_track,
    params_from_config, stability_params)

FPS = 20.0
H = 200.0


def cand(key, x, y, *, hits=50, fss=0, tsu=0, h=H, zone_ok=True):
    return SlotCandidate(key=key, x=float(x), y=float(y), w=0.4 * h, h=h,
                         hits=hits, fss=fss, tsu=tsu, zone_ok=zone_ok)


def run(slots, frames, start=0):
    """frames: list of candidate lists; returns the per-frame outputs."""
    out = []
    for i, cands in enumerate(frames):
        out.append(slots.update(cands, (start + i) / FPS))
    return out


def ids(outs):
    return sorted(o.slot_id for o in outs)


def by_id(outs):
    return {o.slot_id: o for o in outs}


# --------------------------------------------------------------------------- #
# One-Euro
# --------------------------------------------------------------------------- #
def test_one_euro_smooths_noise_at_rest_and_follows_speed():
    rng = np.random.default_rng(0)
    mc, beta = stability_params(0.5)
    f = OneEuro2D(mc, beta)
    raw, out = [], []
    for i in range(200):
        x = np.array([500.0, 500.0]) + rng.normal(0, 4.0, 2)
        raw.append(x)
        out.append(f(x, i / FPS, scale=H))
    d2 = lambda s: np.sqrt(np.mean([np.sum((s[i - 1] - 2 * s[i] + s[i + 1]) ** 2)
                                    for i in range(50, 199)]))
    assert d2(out) < 0.3 * d2(raw)
    # fast ramp (2 h/s): the filter keeps up within a fraction of a body
    f2 = OneEuro2D(mc, beta)
    for i in range(60):
        y = f2(np.array([100.0 + i * 2 * H / FPS, 300.0]), i / FPS, scale=H)
    assert abs(y[0] - (100.0 + 59 * 2 * H / FPS)) < 0.25 * H


def test_stability_is_monotonic():
    lo, hi = stability_params(0.0), stability_params(1.0)
    mid = stability_params(0.5)
    assert lo[0] > mid[0] > hi[0] and lo[1] > mid[1] > hi[1]
    assert stability_params(-3) == lo
    # extended range (2026-10-06): 1..2 keeps getting calmer, clamps at 2; 0..1 is unchanged
    x2 = stability_params(2.0)
    assert hi[0] > stability_params(1.5)[0] > x2[0] and hi[1] > stability_params(1.5)[1] > x2[1]
    assert x2 == pytest.approx((0.05, 0.5)) and stability_params(9) == stability_params(3.0)
    assert stability_params(3.0) == pytest.approx((0.0125, 0.125))
    assert hi == pytest.approx((0.2, 2.0))


# --------------------------------------------------------------------------- #
# Binding, churn, coasting, entry
# --------------------------------------------------------------------------- #
def test_id_churn_keeps_one_slot_id():
    """The tracker re-mints the dancer every few frames: the slot id stays 1."""
    s = IdentitySlots(SlotParams(max_dancers=2, min_streak=1, entry_min_streak=1))
    frames = []
    for i in range(120):
        key = 10 + i // 15                    # a new tracker id every 15 frames
        frames.append([cand(key, 400 + i, 300)])
    outs = run(s, frames)
    assert all(ids(o) == [1] for o in outs[1:])
    assert all(o[0].state == STATE_LIVE for o in outs[1:])


def test_coasting_then_lost_after_coast_s():
    p = SlotParams(max_dancers=1, coast_s=1.0, min_streak=1, entry_min_streak=1)
    s = IdentitySlots(p)
    run(s, [[cand(1, 400, 300)] for _ in range(10)])
    outs = run(s, [[] for _ in range(30)], start=10)
    states = [o[0].state if o else STATE_LOST for o in outs]
    assert states[0] == STATE_COASTING
    n_coast = sum(st == STATE_COASTING for st in states)
    assert abs(n_coast - int(1.0 * FPS)) <= 1        # ~coast_s of frames
    assert states[-1] == STATE_LOST and outs[-1] == []


def test_coasting_slot_rebinds_new_track_near_prediction():
    s = IdentitySlots(SlotParams(max_dancers=2, min_streak=2, entry_min_streak=2))
    run(s, [[cand(1, 400, 300)] for _ in range(10)])
    run(s, [[] for _ in range(5)], start=10)           # 0.25 s gap
    outs = run(s, [[cand(7, 430, 300)] for _ in range(5)], start=15)
    assert ids(outs[-1]) == [1]                        # same slot, not slot 2
    assert outs[-1][0].key == 7 and outs[-1][0].state == STATE_LIVE


def test_lost_slot_reused_by_entering_dancer_anywhere():
    s = IdentitySlots(SlotParams(max_dancers=2, coast_s=0.5, min_streak=1,
                                 entry_min_streak=3, entry_min_hits=1))
    run(s, [[cand(1, 100, 300)] for _ in range(10)])
    run(s, [[] for _ in range(20)], start=10)          # slot 1 lost
    outs = run(s, [[cand(9, 1500, 900)] for _ in range(6)], start=30)
    assert ids(outs[-1]) == [1]                        # id 1 reused, far away
    assert outs[1] == []                               # entry needs a streak


def test_second_dancer_gets_second_slot_and_extra_tracks_are_capped():
    s = IdentitySlots(SlotParams(max_dancers=2, min_streak=1, entry_min_streak=1))
    outs = run(s, [[cand(1, 100, 300), cand(2, 900, 300), cand(3, 1600, 300)]
                   for _ in range(10)])
    assert ids(outs[-1]) == [1, 2]                     # N is a cap
    keys = {o.key for o in outs[-1]}
    assert len(keys) == 2 and keys <= {1, 2, 3}


# --------------------------------------------------------------------------- #
# Anti-ghost
# --------------------------------------------------------------------------- #
def test_unestablished_tracks_never_bind():
    p = SlotParams(max_dancers=2, entry_min_streak=5, entry_min_hits=12,
                   entry_max_fss=40)
    s = IdentitySlots(p)
    # fresh track (hits too low), a blob-born track (never a skeleton) and a
    # track in an excluded zone: none may open a slot.
    frames = [[cand(1, 100, 300, hits=3 + i), cand(2, 600, 300, fss=999),
               cand(3, 1200, 300, zone_ok=False)] for i in range(8)]
    outs = run(s, frames)
    assert all(o == [] for o in outs[:8])
    # once track 1 has enough hits (and a streak) it opens slot 1
    outs = run(s, [[cand(1, 100, 300, hits=20)] for _ in range(6)], start=8)
    assert ids(outs[-1]) == [1]


def test_duplicate_track_on_an_emitted_dancer_cannot_bind():
    s = IdentitySlots(SlotParams(max_dancers=2, min_streak=1, entry_min_streak=1,
                                 dup_bind_h=0.5))
    outs = run(s, [[cand(1, 500, 300), cand(2, 500 + 0.2 * H, 310)] for _ in range(10)])
    assert ids(outs[-1]) == [1]                        # never two slots on one dancer


def test_converging_live_slots_merge_after_hold():
    p = SlotParams(max_dancers=2, min_streak=1, entry_min_streak=1,
                   merge_h=0.35, merge_hold_s=0.3)
    s = IdentitySlots(p)
    run(s, [[cand(1, 300, 300), cand(2, 900, 300)] for _ in range(5)])
    # track 2 (blob-fed: no skeleton) slides onto dancer 1 -- a hijacked /
    # duplicate track: its slot is merged away after the hold
    frames = [[cand(1, 300, 300), cand(2, max(310, 900 - 60 * i), 300, fss=20)]
              for i in range(30)]
    outs = run(s, frames, start=5)
    assert ids(outs[-1]) == [1]
    assert outs[-1][0].key == 1 and s.counters["merges"] >= 1


def test_dancers_in_contact_with_skeletons_are_not_merged():
    """Two tracks that both keep a fresh skeleton are two bodies (a duo in
    contact): no merge, however long they stay close."""
    p = SlotParams(max_dancers=2, min_streak=1, entry_min_streak=1, merge_hold_s=0.3)
    s = IdentitySlots(p)
    run(s, [[cand(1, 300, 300), cand(2, 900, 300)] for _ in range(5)])
    frames = [[cand(1, 300, 300), cand(2, max(330, 900 - 60 * i), 300)] for i in range(40)]
    outs = run(s, frames, start=5)
    assert all(ids(o) == [1, 2] for o in outs)


def test_crossing_dancers_are_not_merged():
    """Two live dancers crossing quickly (< merge_hold_s) keep both slots."""
    p = SlotParams(max_dancers=2, min_streak=1, entry_min_streak=1, merge_hold_s=0.4)
    s = IdentitySlots(p)
    frames = [[cand(1, 200 + 40 * i, 300), cand(2, 1000 - 40 * i, 300)] for i in range(21)]
    outs = run(s, frames)
    assert all(ids(o) == [1, 2] for o in outs[1:])


def test_hidden_bound_track_keeps_slot_live_but_bounded():
    p = SlotParams(max_dancers=1, coast_s=0.5, hidden_max_s=1.0, min_streak=1,
                   entry_min_streak=1)
    s = IdentitySlots(p)
    run(s, [[cand(1, 400, 300)] for _ in range(5)])
    # the tracker hides track 1 (frozen gate) but keeps updating it
    outs = []
    for i in range(40):
        outs.append(s.update([], (5 + i) / FPS, hidden={1: cand(1, 400, 300, fss=50)}))
    states = [o[0].state if o else STATE_LOST for o in outs]
    assert states[:20].count(STATE_LIVE) == 20         # followed for hidden_max_s
    assert STATE_COASTING in states[20:] and states[-1] == STATE_LOST


def test_same_track_returns_to_its_lost_slot():
    p = SlotParams(max_dancers=2, coast_s=0.3, min_streak=1, entry_min_streak=1)
    s = IdentitySlots(p)
    run(s, [[cand(4, 400, 300)] for _ in range(5)])
    run(s, [[] for _ in range(10)], start=5)           # lost
    s.configure(entry_min_streak=50, entry_min_hits=999)   # no new entry possible
    outs = run(s, [[cand(4, 800, 300, fss=200)]], start=15)
    assert ids(outs[-1]) == [1]                        # no re-establishment needed


def test_teleporting_bound_track_is_unbound():
    s = IdentitySlots(SlotParams(max_dancers=1, min_streak=1, entry_min_streak=1,
                                 jump_h=2.0))
    run(s, [[cand(1, 300, 300)] for _ in range(5)])
    outs = run(s, [[cand(1, 300 + 5 * H, 300)]], start=5)
    assert outs[0][0].state == STATE_COASTING and s.counters["unbind_jump"] == 1


# --------------------------------------------------------------------------- #
# Belt hook
# --------------------------------------------------------------------------- #
class FakeBelt:
    """Belt 0.3 h below the centroid of a dancer standing at x0."""

    def __init__(self, x0, y0, on=True):
        self.x0, self.y0, self.on, self.calls = x0, y0, on, 0

    def __call__(self, slot_id, x, y, gate_px):
        self.calls += 1
        bx, by = self.x0, self.y0 + 0.3 * H
        if self.on and math.hypot(x - bx, y - by) <= gate_px:
            return (bx, by, 0.9)
        return None


def test_belt_keeps_a_slot_alive_at_the_learned_offset():
    p = SlotParams(max_dancers=1, coast_s=0.5, min_streak=1, entry_min_streak=1)
    s = IdentitySlots(p)
    belt = FakeBelt(400, 300)
    for i in range(30):
        s.update([cand(1, 400, 300)], i / FPS, belt=belt)
    outs = [s.update([], (30 + i) / FPS, belt=belt) for i in range(60)]
    assert all(o and o[0].state == STATE_BELT for o in outs)
    assert abs(outs[-1][0].raw_y - 300) < 1.0          # belt + learned offset
    belt.on = False
    outs = [s.update([], (90 + i) / FPS, belt=belt) for i in range(20)]
    assert outs[0][0].state == STATE_COASTING and outs[-1] == []


class BatchBelt:
    """One fixed belt; ``batch`` gives it to the nearest query only (as
    BeltDetector.detect_near does)."""

    def __init__(self, bx, by):
        self.bx, self.by, self.batches = bx, by, 0

    def batch(self, queries):
        self.batches += 1
        best = min(queries, key=lambda q: math.hypot(q[1] - self.bx, q[2] - self.by))
        sid, x, y, gate, bw = best
        if math.hypot(x - self.bx, y - self.by) > gate:
            return {}
        return {sid: (self.bx, self.by, 0.9)}

    def __call__(self, *a):           # pragma: no cover - batch is preferred
        raise AssertionError("per-slot call while batch exists")


def test_belt_queries_are_batched_one_belt_one_slot():
    p = SlotParams(max_dancers=2, coast_s=0.5, min_streak=1, entry_min_streak=1)
    s = IdentitySlots(p)
    belt = BatchBelt(400, 360)
    for i in range(20):
        s.update([cand(1, 400, 300), cand(2, 1200, 300)], i / FPS, belt=belt)
    assert belt.batches == 20                        # one call per frame
    a, b = s.slots
    assert a.belt_seen >= 5 and b.belt_offset is None   # only slot 1 owns the belt
    outs = s.update([cand(2, 1200, 300)], 20 / FPS, belt=belt)
    assert by_id(outs)[1].state == STATE_BELT and by_id(outs)[2].state == STATE_LIVE


def test_glint_with_inconsistent_offset_never_holds_a_slot():
    """A fixed bright spot near a MOVING dancer: the offset never settles, so
    the belt is never trusted to hold the slot."""
    p = SlotParams(max_dancers=1, coast_s=0.5, min_streak=1, entry_min_streak=1,
                   belt_gate_h=2.0)
    s = IdentitySlots(p)
    glint = FakeBelt(500, 300 - 0.3 * H)             # 'belt' fixed at (500, 300)
    for i in range(30):
        s.update([cand(1, 400 + 8 * i, 300)], i / FPS, belt=glint)
    assert s.slots[0].belt_seen < p.belt_min_learn
    outs = s.update([], 30 / FPS, belt=glint)
    assert outs[0].state == STATE_COASTING


def test_failing_belt_hook_is_a_no_op():
    s = IdentitySlots(SlotParams(max_dancers=1, min_streak=1, entry_min_streak=1))

    def boom(*_a):
        raise RuntimeError("detector crashed")
    for i in range(10):
        outs = s.update([cand(1, 400, 300)], i / FPS, belt=boom)
    assert outs[0].state == STATE_LIVE


# --------------------------------------------------------------------------- #
# Config / adapters
# --------------------------------------------------------------------------- #
def test_configure_resizes_and_retunes():
    s = IdentitySlots(SlotParams(max_dancers=2))
    s.configure(max_dancers=4, stability=1.0, coast_s=2.5)
    assert len(s.slots) == 4 and s.p.coast_s == 2.5
    assert s.slots[0].filt.min_cutoff == pytest.approx(stability_params(1.0)[0])
    s.configure(max_dancers=1)
    assert [x.sid for x in s.slots] == [1]


def test_params_from_config_and_candidate_from_track():
    p = params_from_config({"max_dancers": 3, "stability": 0.2, "coast_s": 2})
    assert (p.max_dancers, p.stability, p.coast_s) == (3, 0.2, 2.0)

    class ST:
        track_id = 5
        bbox = np.array([100.0, 50.0, 80.0, 200.0])
        smoothed_centroid = np.array([140.0, 150.0])
        centroid_raw = np.array([141.0, 151.0])
        frames_since_skeleton = 2
        hits = 30
        age = 40
        time_since_update = 0
        feed_src = "yolo"
    c = candidate_from_track(ST())
    assert (c.key, c.x, c.y, c.h, c.hits, c.fss) == (5, 140.0, 150.0, 200.0, 30, 2)
    assert candidate_from_track(ST(), position="raw").x == 141.0


# --------------------------------------------------------------------------- #
# Static-ghost guard + yield (PLAN_25M 2026-10-06)
# --------------------------------------------------------------------------- #
def _ghost_params(**kw):
    base = dict(max_dancers=1, coast_s=0.5, min_streak=1, entry_min_streak=1,
                min_hits=1, entry_min_hits=1, static_guard=True, static_yield=True,
                static_after_s=1.0)
    base.update(kw)
    return SlotParams(**base)


def _ghost(t_i, x=100.0, y=100.0):
    """A fixed figure: sub-pixel box jitter only (0.005 h)."""
    return cand(1, x + (t_i % 2), y, hits=t_i + 1, fss=0)


def _dancer(t_i, i0, x0=600.0, y=400.0, step=12.0, key=2):
    """A moving dancer entering at frame i0 (12 px/frame, fresh skeleton)."""
    return cand(key, x0 + step * (t_i - i0), y, hits=t_i - i0 + 1, fss=0)


def test_static_ghost_yields_its_slot_to_a_moving_dancer_and_never_takes_it_back():
    s = IdentitySlots(_ghost_params())
    frames = [[_ghost(i)] for i in range(40)]                         # 2 s: ghost alone
    frames += [[_ghost(i), _dancer(i, 40)] for i in range(40, 70)]     # a dancer arrives
    frames += [[_ghost(i)] for i in range(70, 110)]                   # the dancer leaves
    outs = run(s, frames)
    assert outs[39] and by_id(outs[39])[1].key == 1                   # ghost held the only slot
    assert by_id(outs[60])[1].key == 2                                # ... then gave it away
    assert s.counters["yields"] >= 1
    assert all(o.key != 1 for f in outs[50:] for o in f)              # and never got it back
    assert outs[-1] == []                                             # dancer gone: no ghost point


def test_guard_off_keeps_the_shipped_behaviour():
    s = IdentitySlots(_ghost_params(static_guard=False, static_yield=False))
    frames = [[_ghost(i)] for i in range(40)]
    frames += [[_ghost(i), _dancer(i, 40)] for i in range(40, 70)]
    outs = run(s, frames)
    assert by_id(outs[-1])[1].key == 1                                # ghost keeps the slot
    assert s.counters["yields"] == 0


def test_a_swaying_still_dancer_never_yields():
    s = IdentitySlots(_ghost_params())
    sway = lambda i: cand(1, 300 + 0.12 * H * math.sin(i / 3.0), 300, hits=i + 1, fss=0)
    frames = [[sway(i)] for i in range(40)]
    frames += [[sway(i), _dancer(i, 40, x0=900.0)] for i in range(40, 80)]
    outs = run(s, frames)
    assert by_id(outs[-1])[1].key == 1
    assert s.counters["yields"] == 0


def test_static_release_drops_a_ghost_without_a_newcomer():
    s = IdentitySlots(_ghost_params(max_dancers=2, static_release_s=1.0))
    outs = run(s, [[_ghost(i)] for i in range(80)])
    assert outs[10] and outs[-1] == []                                # held, then released for good
    assert s.counters["yields"] == 1


def test_raw_filter_input_follows_the_raw_centroid_but_binds_on_the_smoothed_one():
    def step(fi, fss=0):
        s = IdentitySlots(SlotParams(max_dancers=1, min_streak=1, entry_min_streak=1,
                                     min_hits=1, entry_min_hits=1, filter_input=fi))
        c = lambda i: SlotCandidate(key=7, x=100.0, y=100.0, fx=130.0, fy=100.0, w=80, h=H,
                                    hits=i + 1, fss=fss)
        outs = run(s, [[c(i)] for i in range(60)])
        return outs[-1][0]
    assert abs(step("smoothed").x - 100.0) < 1.0
    o = step("raw")
    assert abs(o.x - 130.0) < 1.0 and abs(o.raw_x - 100.0) < 1e-6 and o.key == 7
    assert abs(step("raw_skeleton").x - 130.0) < 1.0
    assert abs(step("raw_skeleton", fss=4).x - 100.0) < 1.0          # no fresh skeleton: smoothed


def test_params_from_config_static_and_filter_keys():
    p = params_from_config({"static_ghost_guard": False, "static_release_s": 12,
                            "slot_filter_input": "raw_skeleton"})
    assert not p.static_guard and not p.static_yield
    assert p.static_release_s == 12.0 and p.filter_input == "raw_skeleton"
    p = params_from_config({"static_ghost_guard": True, "slot_filter_input": "bogus"})
    assert p.static_guard and p.static_yield and p.filter_input == "smoothed"


# --------------------------------------------------------------------------- #
# Smart hold (2026-10-06): the hold is earned (skeleton-backed live time AND
# travel since entry); a dancer leaving across the border is released fast.
# --------------------------------------------------------------------------- #
BOUNDS = (0.0, 0.0, 2000.0, 1500.0)


def _hold_run(slots, frames, start=0, bounds=BOUNDS):
    return [slots.update(c, (start + i) / FPS, bounds=bounds) for i, c in enumerate(frames)]


def _lost_after(slots, start, n=200, bounds=BOUNDS):
    """Feed empty frames from `start`; return the seconds the slot stayed emitted."""
    for i in range(n):
        if not slots.update([], (start + i) / FPS, bounds=bounds):
            return i / FPS
    return n / FPS


def test_smart_hold_full_hold_needs_skeleton_time_and_travel():
    p = dict(max_dancers=1, coast_s=5.0, smart_hold=True)
    # a moving, skeleton-backed dancer (2 s, ~1.2 h of travel) earns the full 5 s
    s = IdentitySlots(SlotParams(**p))
    _hold_run(s, [[cand(1, 800 + 6 * i, 700)] for i in range(40)])
    assert 4.8 <= _lost_after(s, 40) <= 5.2
    # a figure that never moves only gets hold_min_s, even with a skeleton
    s = IdentitySlots(SlotParams(**p))
    _hold_run(s, [[cand(1, 800, 700)] for i in range(40)])
    assert 1.4 <= _lost_after(s, 40) <= 1.6
    # a moving blob with no fresh skeleton (fss > 0) never earns more than hold_min_s
    s = IdentitySlots(SlotParams(**p))
    _hold_run(s, [[cand(1, 800 + 6 * i, 700, fss=3)] for i in range(40)])
    assert 1.4 <= _lost_after(s, 40) <= 1.6


def test_smart_hold_releases_a_dancer_leaving_across_the_border():
    p = SlotParams(max_dancers=1, coast_s=5.0, smart_hold=True)
    s = IdentitySlots(p)
    # walks right (1.5 h/s) and is lost 30 px from the right bound
    xs = [1970 - 15 * (39 - i) for i in range(40)]
    _hold_run(s, [[cand(1, x, 700)] for x in xs])
    assert _lost_after(s, 40) <= p.edge_hold_s + 0.1
    # same dancer lost at the border but moving inward / along it keeps the full hold
    s = IdentitySlots(p)
    _hold_run(s, [[cand(1, 1970, 300 + 6 * i)] for i in range(40)])
    assert _lost_after(s, 40) >= 4.8


def test_smart_hold_off_and_short_hold_are_the_shipped_behaviour():
    for p in (SlotParams(max_dancers=1, coast_s=5.0, smart_hold=False),
              SlotParams(max_dancers=1, coast_s=1.0, smart_hold=True)):
        s = IdentitySlots(p)
        _hold_run(s, [[cand(1, 800, 700)] for i in range(10)])
        assert abs(_lost_after(s, 10) - p.coast_s) <= 0.1


def test_smart_hold_reputation_restarts_for_a_new_dancer():
    s = IdentitySlots(SlotParams(max_dancers=1, coast_s=5.0, smart_hold=True))
    _hold_run(s, [[cand(1, 800 + 6 * i, 700)] for i in range(40)])
    assert _lost_after(s, 40) >= 4.8          # slot dropped after its earned 5 s
    # a new track enters the freed slot and is lost again almost at once
    _hold_run(s, [[cand(7, 300, 700)] for i in range(5)], start=200)
    assert _lost_after(s, 205) <= 1.6


def test_params_from_config_smart_hold_and_extended_stability():
    p = params_from_config({"smart_hold": False, "stability": 2.5, "coast_s": 8.0})
    assert p.smart_hold is False and p.coast_s == 8.0
    assert stability_params(p.stability) == pytest.approx(stability_params(2.5))
