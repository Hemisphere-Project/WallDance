"""F4 (Plan Cordée rev 2, #t-056): a held slot sits on a foreground group.

A point no fresh YOLO skeleton backs (coasting, fg, belt, or live on a track without a skeleton this frame) must
lie on a body-sized foreground group; without one for 1 s the slot lets go, and a track without a fresh skeleton
cannot take a slot off a group.  Without a valid plate the slot layer is exactly the one before.
Pure unit tests on synthetic tracks and foreground groups (no GPU, no recordings)."""
import numpy as np

from core.foreground import FgBlob, FgFrame
from core.identity_slots import (IdentitySlots, SlotCandidate, SlotParams, STATE_COASTING, STATE_FG,
                                 STATE_LIVE)

FPS = 20.0
H = 200.0


def cand(key, x, y, *, hits=50, fss=0, tsu=0, h=H):
    return SlotCandidate(key=key, x=float(x), y=float(y), w=0.4 * h, h=h, hits=hits, fss=fss, tsu=tsu)


def group(x, y, area_h2=0.25, w=80.0, hgt=200.0):
    return FgBlob(x=x, y=y, area=area_h2 * H * H, mass=1.0, bbox=(x - w / 2, y - hgt / 2, x + w / 2, y + hgt / 2))


class Fg:
    """FgFrame stand-in: the given groups; support 1 inside any group's box."""

    def __init__(self, blobs=()):
        self.valid = True
        self.blobs = list(blobs)

    def support(self, cx, cy, h, wfrac=0.45):
        return 1.0 if any(b.bbox[0] <= cx <= b.bbox[2] and b.bbox[1] <= cy <= b.bbox[3] for b in self.blobs) else 0.0


def _p(**kw):
    base = dict(max_dancers=1, coast_s=3.0, min_streak=1, entry_min_streak=1)
    base.update(kw)
    return SlotParams(**base)


def _state(out, sid=1):
    for o in out:
        if o.slot_id == sid:
            return o.state
    return None


def _live_moving(s, n=30, fg=True):
    """n frames live on a fresh skeleton, walking 2 px/frame on her own group (the slot earns its reputation)."""
    for i in range(n):
        x = 340 + 2 * i
        s.update([cand(1, x, 300)], i / FPS, fg=Fg([group(x, 300)]) if fg else None)
    return 340 + 2 * (n - 1)


def test_a_held_slot_on_no_group_lets_go_after_1s_and_coasts_as_before_without_a_plate():
    s = IdentitySlots(_p())
    _live_moving(s)
    with_plate = [_state(s.update([], (30 + i) / FPS, fg=Fg())) for i in range(80)]
    assert with_plate[:20] == [STATE_COASTING] * 20            # 1 s held on nothing ...
    assert set(with_plate[20:]) == {None}                     # ... then it lets go (coast_s is 3 s)
    s2 = IdentitySlots(_p())
    _live_moving(s2, fg=False)
    no_plate = [_state(s2.update([], (30 + i) / FPS)) for i in range(80)]
    assert no_plate[:59] == [STATE_COASTING] * 59 and no_plate[62] is None   # the full 3 s coast


def test_a_held_slot_follows_its_group_beyond_1s():
    s = IdentitySlots(_p())
    x0 = _live_moving(s)
    outs = [s.update([], (30 + i) / FPS, fg=Fg([group(x0 + i, 300)])) for i in range(100)]
    assert all(_state(o) == STATE_FG for o in outs)           # 5 s on her group, following it
    assert abs(outs[-1][0].raw_x - (x0 + 99)) < 2


def test_a_bridged_track_climbing_off_the_group_lets_go_and_cannot_take_the_slot_back():
    """The 08/10 « point above the pair »: YOLO loses her, the tracker keeps her track alive on motion blobs
    (no skeleton) and it climbs a swinging rope away from her body's group."""
    def run(plate):
        s = IdentitySlots(_p(fg_veto=0.0))      # veto off: F4's own bind rule is what keeps the track out
        for i in range(30):
            s.update([cand(1, 400, 300)], i / FPS, fg=Fg([group(400, 300)]) if plate else None)
        outs = []
        for k in range(70):
            c = cand(1, 400, 300 - 10 * k, fss=k + 1)            # 10 px/frame up the rope, no skeleton
            outs.append(s.update([c], (30 + k) / FPS, fg=Fg([group(400, 300)]) if plate else None))
        return s, outs

    s, outs = run(True)
    st = [_state(o) for o in outs]
    # on her group while within its box grown by 0.25 h (y >= 150): k <= 15; then 1 s, then it lets go
    assert st[:36] == [STATE_LIVE] * 36 and set(st[36:]) == {None}
    assert s.counters.get("fg_anchor_drops") == 1 and s.counters.get("fg_anchor_wait", 0) > 0
    _s, outs = run(False)
    assert all(_state(o) == STATE_LIVE for o in outs)           # no plate: the shipped behaviour
    # back on a fresh skeleton, on a group: the track takes its slot again
    s, _outs = run(True)
    out = s.update([cand(1, 400, 300)], 100 / FPS, fg=Fg([group(400, 300)]))
    assert _state(out) == STATE_LIVE


def test_a_held_slot_on_no_group_takes_a_dancer_nobody_holds_after_half_a_second():
    """Held on nothing, the slot is most likely on nobody: a dancer YOLO sees, on her group, that no slot holds
    may take it wherever she is -- once the slot has been on no group for 0.5 s (a group's one-frame dropout is
    not enough).  Without a plate it coasts, the newcomer outside its rebind gate."""
    def run(plate):
        s = IdentitySlots(_p())
        _live_moving(s, fg=plate)
        return [s.update([cand(5, 1100, 300)], (30 + i) / FPS, fg=Fg([group(1100, 300)]) if plate else None)
                for i in range(20)]

    outs = run(True)
    assert [_state(o) for o in outs[:10]] == [STATE_COASTING] * 10  # held on no group since f30: 0.45 s at f39
    assert all(o[0].state == STATE_LIVE and o[0].key == 5 and o[0].slot_id == 1 for o in outs[10:])
    assert all(_state(o) == STATE_COASTING for o in run(False))


def test_a_bridged_live_slot_on_no_group_is_not_handed_over_it_lets_go():
    """mur2 8 f2831: a slot live on a bridged track (no skeleton) whose group fell under the size floor was taken
    over the next frame by another dancer's track 3.7 h away.  A live slot is not handed over: it lets go after
    1 s on no group like any held slot (and only then may another dancer enter its id)."""
    s = IdentitySlots(_p())
    for i in range(30):
        s.update([cand(1, 400, 300)], i / FPS, fg=Fg([group(400, 300)]))
    outs = []
    for k in range(25):
        c = [cand(1, 400, 300, fss=k + 1), cand(5, 1100, 300)]      # her track bridged, her group gone
        outs.append(s.update(c, (30 + k) / FPS, fg=Fg([group(1100, 300)])))
    assert all(_state(o) == STATE_LIVE and o[0].key == 1 for o in outs[:20])
    assert s.counters.get("fg_anchor_reacquires", 0) == 0 and s.counters.get("fg_anchor_drops") == 1


def test_a_held_slot_is_not_handed_to_a_track_beside_a_held_dancer():
    """mur2 7 f8581 / mur2 8 f2831: the newcomer sat 0.55 h from the other slot's point (outside dup_bind_h's
    0.5 h): the slot jumped onto the dancer who already had a point, while the other dancer had none.  A held
    slot is handed over only to a track with no other slot's point within 1 h."""
    s = IdentitySlots(_p(max_dancers=2))
    # dancer 2's group is wide (arms, rope): her second track sits 0.7 h from her point, 4.6 h from dancer 1's
    # last point (outside the 3 h rebind gate: only F4's hand-over could reach it)
    pair = [group(100, 300), FgBlob(x=1000, y=300, area=0.5 * H * H, mass=1.0, bbox=(850, 200, 1150, 400))]
    for i in range(30):
        s.update([cand(1, 100, 300), cand(2, 900, 300)], i / FPS, fg=Fg(pair))
    keys = []
    for k in range(20):                                             # dancer 1 gone; dancer 2 has a second track
        out = s.update([cand(2, 900, 300), cand(7, 900 + 0.7 * H, 300)], (30 + k) / FPS, fg=Fg(pair[1:]))
        keys.append({o.slot_id: o.key for o in out})
    assert all(kk.get(2) == 2 for kk in keys)
    assert all(kk.get(1) != 7 for kk in keys)                         # held 1 s, never on dancer 2's duplicate
    assert s.counters.get("fg_anchor_reacquires", 0) == 0


def test_a_held_slot_is_not_handed_to_a_track_the_tracker_only_predicts():
    """mur2 8 f2831: the newcomer was a track the tracker had not updated for 4 frames (tsu 4, no skeleton for 4):
    a held slot is handed over only to a track updated this frame with a fresh skeleton."""
    for fss, tsu in ((3, 3), (0, 2), (2, 0)):
        s = IdentitySlots(_p())
        _live_moving(s)
        outs = [s.update([cand(5, 1100, 300, fss=fss, tsu=tsu)], (30 + i) / FPS, fg=Fg([group(1100, 300)]))
                for i in range(20)]
        assert all(_state(o) == STATE_COASTING for o in outs), (fss, tsu)
        assert s.counters.get("fg_anchor_reacquires", 0) == 0


def test_a_handed_over_slot_starts_at_rest():
    """mur2 8 f2832: after a 3.5 h hand-over the old held point -> new track step became a speed of thousands of
    px/s, and when the new track was not updated the next frame the slot coasted across the empty wall.  The
    hand-over starts at rest: the slot waits where the newcomer is.  (The held slot is in ``fg`` state on a
    rope-sized blob -- big enough for the fg hold, too small for a body -- so its velocity is still measured.)"""
    s = IdentitySlots(_p())
    x0 = _live_moving(s)
    rope = FgBlob(x=x0, y=300, area=0.03 * H * H, mass=1.0, bbox=(x0 - 8, 240, x0 + 8, 360))
    i, took, states = 0, None, []
    while took is None and i < 40:
        out = s.update([cand(5, 1100, 300)], (30 + i) / FPS, fg=Fg([rope, group(1100, 300)]))
        if out and out[0].key == 5:
            took = i
        else:
            states.append(_state(out))
        i += 1
    assert took is not None and states[-1] == STATE_FG              # the hand-over happened (3.7 h) from fg
    xs = []
    for k in range(1, 6):                                           # the tracker only predicts her now
        out = s.update([cand(5, 1100, 300, fss=k, tsu=k)], (30 + i) / FPS, fg=Fg([rope, group(1100, 300)]))
        xs.append(out[0].raw_x)
        i += 1
    assert max(abs(x - 1100) for x in xs) < 5, xs


def test_a_rope_sized_group_does_not_hold_a_slot():
    s = IdentitySlots(_p())
    x0 = _live_moving(s)
    rope = FgBlob(x=x0, y=300, area=0.03 * H * H, mass=1.0, bbox=(x0 - 8, 240, x0 + 8, 360))
    st = [_state(s.update([], (30 + i) / FPS, fg=Fg([rope]))) for i in range(60)]
    assert None not in st[:20] and set(st[20:]) == {None}     # it may follow the sliver for 1 s, no longer


def test_a_held_slot_on_a_shared_group_keeps_its_id():
    """Two dancers in contact under one group: the one YOLO lost coasts on the shared group (its blob is the
    other slot's, so it is not followed) -- still on a group, so F4 keeps it until the coast ends."""
    s = IdentitySlots(_p(max_dancers=2))
    pair = FgBlob(x=440, y=300, area=0.5 * H * H, mass=1.0, bbox=(300, 200, 580, 400))
    for i in range(30):
        s.update([cand(1, 380, 300), cand(2, 500, 300)], i / FPS, fg=Fg([pair]))
    st = [_state(s.update([cand(2, 500, 300)], (30 + i) / FPS, fg=Fg([pair]))) for i in range(50)]
    assert set(st) <= {STATE_FG, STATE_COASTING}               # 2.5 s, beyond F4's 1 s


def test_the_miss_clock_restarts_when_the_group_comes_back():
    def run(gap):
        s = IdentitySlots(_p())
        x0 = _live_moving(s)
        st, t = [], 30
        for n, fg in ((gap, Fg()), (10, Fg([group(x0, 300)])), (gap, Fg())):
            for _ in range(n):
                st.append(_state(s.update([], t / FPS, fg=fg)))
                t += 1
        return st

    assert None not in run(16)                                 # 0.8 s off, back on, 0.8 s off: kept
    assert run(22)[21] is None                                 # 1.1 s off: let go


def test_a_yolo_backed_slot_is_not_held():
    s = IdentitySlots(_p())
    for i in range(30):
        s.update([cand(1, 400, 300)], i / FPS, fg=Fg([group(400, 300)]))
    st = [_state(s.update([cand(1, 400 + 5 * i, 300)], (30 + i) / FPS, fg=Fg([group(400, 300)])))
          for i in range(60)]
    assert st == [STATE_LIVE] * 60                             # a fresh skeleton each frame: not F4's


def _scenario_outputs(fg_of, **kw):
    """A mixed scenario -- two dancers, YOLO gaps, bridged stretches, a ghost, coasts and re-entries -- run
    with ``fg_of(i)`` as the foreground; the per-frame outputs as tuples."""
    rng = np.random.default_rng(7)
    s = IdentitySlots(_p(max_dancers=2, **kw))
    rows = []
    for i in range(600):
        cands = []
        if i % 97 < 70:
            cands.append(cand(1, 300 + 100 * np.sin(i / 40.0), 400 + rng.normal(0, 2), fss=0 if i % 13 else 4))
        if 50 <= i % 211 < 180:
            cands.append(cand(2 + (i // 211), 700 + rng.normal(0, 3), 500 - (i % 211), fss=(i % 211) // 30))
        if 300 <= i < 340:
            cands.append(cand(99, 1000, 200, fss=0))
        out = s.update(cands, i / FPS, fg=fg_of(i))
        rows.append([(o.slot_id, o.state, round(o.x, 9), round(o.y, 9), o.key) for o in out])
    return rows


def test_without_a_valid_plate_the_slot_layer_is_the_one_before():
    base = _scenario_outputs(lambda i: None, fg_anchor=False)
    assert _scenario_outputs(lambda i: None) == base                        # no plate
    invalid = FgFrame(None, 0.0, 0.0, 4, valid=False, reason="plate stale")
    assert _scenario_outputs(lambda i: invalid) == base                     # stale / refused / another crop
    # and a valid plate does change it (the scenario has held points on no group)
    assert (_scenario_outputs(lambda i: Fg(), fg_veto=0.0)
            != _scenario_outputs(lambda i: Fg(), fg_veto=0.0, fg_anchor=False))
