"""Clean-plate foreground (core/foreground.py) and its slot-layer rules (2026-10-07):
ghost veto, foreground hold, belt backing (the belt-only cap restarts on evidence)."""
import math

import numpy as np
import pytest

from core.foreground import CleanPlate, FgBlob, FgFrame, ForegroundDetector, PlateCapture
from core.identity_slots import (IdentitySlots, SlotCandidate, SlotParams, STATE_BELT,
                                 STATE_COASTING, STATE_FG, STATE_LIVE)

FPS = 20.0
H = 200.0


# --------------------------------------------------------------------------- #
# Foreground detection
# --------------------------------------------------------------------------- #
def _wall(rng, w=800, h=600, level=20.0, noise=1.5):
    return np.clip(level + rng.normal(0, noise, (h, w)), 0, 255).astype(np.uint8)


def test_plate_round_trip(tmp_path):
    rng = np.random.default_rng(1)
    plate = CleanPlate.from_frames([_wall(rng) for _ in range(12)], source="test")
    assert plate.frame_size == (800, 600) and plate.plate.shape == (150, 200)
    assert 0.05 <= plate.sigma < 1.0                       # noise / 4 after the downscale
    back = CleanPlate.load(plate.save(tmp_path / "p.npz"))
    assert back.frame_size == plate.frame_size and np.allclose(back.plate, plate.plate)


def test_empty_wall_is_empty_and_a_body_is_found():
    rng = np.random.default_rng(2)
    det = ForegroundDetector(CleanPlate.from_frames([_wall(rng) for _ in range(12)]))
    fg = det.process(_wall(rng), 0, 0, (800, 600))
    assert fg.valid and fg.blobs == [] and fg.fg_ratio == 0.0
    frame = _wall(rng)
    frame[200:400, 300:360] = 60                           # a lit body, 200 px tall
    fg = det.process(frame, 0, 0, (800, 600))
    assert len(fg.blobs) == 1
    b = fg.blobs[0]
    assert abs(b.x - 330) < 6 and abs(b.y - 300) < 6
    assert fg.support(330, 300, 200) > 0.5 and fg.support(600, 300, 200) == 0.0


def test_roi_crop_is_aligned_on_the_plate_grid():
    rng = np.random.default_rng(3)
    det = ForegroundDetector(CleanPlate.from_frames([_wall(rng) for _ in range(12)]))
    frame = _wall(rng)
    frame[200:400, 300:360] = 60
    x0, y0 = 101, 53                                       # ROI origin not a multiple of 4
    fg = det.process(frame[y0:500, x0:700], x0, y0, (800, 600))
    assert len(fg.blobs) == 1 and abs(fg.blobs[0].x - 330) < 6 and abs(fg.blobs[0].y - 300) < 6


def test_global_light_change_is_normalised_and_a_changed_scene_is_stale():
    rng = np.random.default_rng(4)
    det = ForegroundDetector(CleanPlate.from_frames([_wall(rng, level=30) for _ in range(12)]))
    dim = _wall(rng, level=15)                              # lights at half: no foreground
    fg = det.process(dim, 0, 0, (800, 600))
    assert fg.valid and abs(fg.gain - 0.5) < 0.05 and fg.blobs == []
    other = _wall(rng, level=30)
    other[:, :500] = 90                                     # most of the scene changed
    fg = det.process(other, 0, 0, (800, 600))
    assert not fg.valid and fg.reason == "plate stale"


def test_plate_from_another_camera_crop_is_ignored():
    rng = np.random.default_rng(5)
    det = ForegroundDetector(CleanPlate.from_frames([_wall(rng) for _ in range(12)]))
    fg = det.process(_wall(rng, w=600, h=800), 0, 0, (600, 800))
    assert not fg.valid and fg.reason == "plate size"
    assert fg.support(10, 10, 100) == 1.0                   # no evidence -> no veto


def test_plate_capture_counts_frames():
    rng = np.random.default_rng(6)
    cap = PlateCapture(n_frames=10)
    done = [cap.add(np.dstack([_wall(rng)] * 3)) for _ in range(10)]
    assert done[-1] and not any(done[:-1]) and cap.progress == 1.0
    assert cap.plate().frame_size == (800, 600)


# --------------------------------------------------------------------------- #
# Slot-layer rules (synthetic FgFrame)
# --------------------------------------------------------------------------- #
def cand(key, x, y, *, hits=50, fss=0, tsu=0, h=H):
    return SlotCandidate(key=key, x=float(x), y=float(y), w=0.4 * h, h=h, hits=hits, fss=fss, tsu=tsu)


class Fg:
    """FgFrame stand-in: blobs at given points; support 1 inside a body box around them."""

    def __init__(self, pts=()):
        self.valid = True
        self.blobs = [FgBlob(x=x, y=y, area=0.25 * H * H, mass=1.0, bbox=(x - 40, y - 100, x + 40, y + 100))
                      for x, y in pts]

    def support(self, cx, cy, h, wfrac=0.45):
        return 1.0 if any(abs(b.x - cx) < 0.3 * h and abs(b.y - cy) < 0.5 * h for b in self.blobs) else 0.0


def _p(**kw):
    base = dict(max_dancers=1, coast_s=0.5, min_streak=1, entry_min_streak=1)
    base.update(kw)
    return SlotParams(**base)


def test_foreground_holds_a_dancer_yolo_lost_and_follows_the_blob():
    s = IdentitySlots(_p())
    for i in range(30):                                     # live, blob 20 px above the centroid
        s.update([cand(1, 400, 300)], i / FPS, fg=Fg([(400, 280)]))
    outs = [s.update([], (30 + i) / FPS, fg=Fg([(400 + i, 280)])) for i in range(100)]
    assert all(o and o[0].state == STATE_FG for o in outs)  # 5 s, far beyond the 0.5 s hold
    assert abs(outs[-1][0].raw_x - 499) < 2 and abs(outs[-1][0].raw_y - 300) < 2   # learned offset
    outs = [s.update([], (130 + i) / FPS, fg=Fg()) for i in range(20)]
    assert outs[0][0].state == STATE_COASTING and outs[-1] == []


def test_foreground_hold_is_capped_and_never_takes_another_dancers_blob():
    s = IdentitySlots(_p(max_dancers=2, fg_max_s=2.0))
    for i in range(30):
        s.update([cand(1, 400, 300), cand(2, 600, 300)], i / FPS, fg=Fg([(400, 300), (600, 300)]))
    # dancer 1 lost by YOLO and walks onto dancer 2's blob: slot 1 must not take it
    outs = [s.update([cand(2, 600, 300)], (30 + i) / FPS, fg=Fg([(600, 300)])) for i in range(5)]
    assert [o.state for o in outs[-1] if o.slot_id == 1] in ([STATE_COASTING], [])
    s2 = IdentitySlots(_p(fg_max_s=2.0))
    for i in range(30):
        s2.update([cand(1, 400, 300)], i / FPS, fg=Fg([(400, 300)]))
    outs = [s2.update([], (30 + i) / FPS, fg=Fg([(400, 300)])) for i in range(80)]
    states = [o[0].state if o else None for o in outs]
    assert states[0] == STATE_FG and STATE_FG not in states[45:] and states[-1] is None


def test_ghost_veto_a_track_without_foreground_cannot_take_a_slot():
    s = IdentitySlots(_p())
    outs = [s.update([cand(7, 900, 300)], i / FPS, fg=Fg([(400, 300)])) for i in range(40)]
    assert all(o == [] for o in outs)                      # a static figure: no foreground there
    assert s.counters["fg_veto"] > 0
    outs = [s.update([cand(8, 400, 300)], (40 + i) / FPS, fg=Fg([(400, 300)])) for i in range(5)]
    assert outs[-1] and outs[-1][0].state == STATE_LIVE   # the real dancer binds
    s2 = IdentitySlots(_p())                               # no plate: no veto
    outs = [s2.update([cand(7, 900, 300)], i / FPS) for i in range(5)]
    assert outs[-1] and outs[-1][0].state == STATE_LIVE


class FakeBelt:
    def __init__(self, x0, y0):
        self.x0, self.y0 = x0, y0

    def __call__(self, slot_id, x, y, gate_px):
        bx, by = self.x0, self.y0 + 0.3 * H
        return (bx, by, 0.9) if math.hypot(x - bx, y - by) <= gate_px else None


def _belt_run(p, after_live, hidden_fn=None, fg_fn=None, frames=400):
    s = IdentitySlots(p)
    belt = FakeBelt(400, 300)
    for i in range(30):
        s.update([cand(1, 400, 300)], i / FPS, belt=belt)
    states = []
    for i in range(frames):
        t = (30 + i) / FPS
        hidden = hidden_fn(i) if hidden_fn else None
        fg = fg_fn(i) if fg_fn else None
        o = s.update([], t, belt=belt, hidden=hidden, fg=fg)
        states.append(o[0].state if o else None)
    return states


def test_belt_only_hold_is_capped_without_evidence():
    states = _belt_run(_p(belt_max_s=8.0), 30)
    assert states[0] == STATE_BELT
    assert STATE_BELT not in states[170:]                  # 8 s cap, then coast, then lost
    assert states[-1] is None


def test_belt_hold_restarts_while_yolo_still_sees_the_dancer_now_and_then():
    # the bound track stays alive (the tracker de-confirmed it: not reported) and gets a
    # skeleton every 10 frames -- the night-take still dancer
    hidden = lambda i: {1: cand(1, 400, 300, fss=i % 10, tsu=1)}
    states = _belt_run(_p(belt_max_s=8.0), 30, hidden_fn=hidden)
    assert all(st == STATE_BELT for st in states)          # 20 s, never capped


def test_belt_hold_restarts_on_foreground():
    states = _belt_run(_p(belt_max_s=8.0, fg_hold=False), 30, fg_fn=lambda i: Fg([(400, 300)]))
    assert all(st == STATE_BELT for st in states)
    # a stale skeleton (beyond belt_refresh_fss) does not back it
    hidden = lambda i: {1: cand(1, 400, 300, fss=100 + i, tsu=1)}
    states = _belt_run(_p(belt_max_s=8.0), 30, hidden_fn=hidden)
    assert STATE_BELT not in states[170:]
