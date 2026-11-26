"""F2 · The plate is honest (Plan Cordée rev 2; hub walldance#t-053 ④).

(a) « plate stale » stays on screen until a new plate is taken: the foreground latches it, the pipeline's status
    keeps it, the « empty wall » line updates itself and a persistent alert stays in the strip; only a new plate
    clears them.
(b) the slow plate update never absorbs a still dancer: a foreground group that held a person keeps her pixels
    out of the update after YOLO and her slot have let her go, until she moves on; a change no person stood in
    is absorbed as before.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from core.foreground import CleanPlate, FgParams, ForegroundDetector
from core.ops_monitor import HealthMonitor

SIZE = (800, 600)


def _wall(rng, w=800, h=600, level=20.0, noise=1.5):
    return np.clip(level + rng.normal(0, noise, (h, w)), 0, 255).astype(np.uint8)


def _det(rng, **params):
    return ForegroundDetector(CleanPlate.from_frames([_wall(rng) for _ in range(12)]), FgParams(**params))


def _stale(walls):
    f = walls()
    f[:, :500] = 120                                         # most of the scene changed (light, lens)
    return f


def _near(fg, x, y, tol=12.0):
    return any(abs(b.x - x) < tol and abs(b.y - y) < tol for b in fg.blobs)


class _Walls:
    """Empty-wall frames drawn once and cycled (the noise draw dominates these tests' run time)."""

    def __init__(self, rng, k=9):
        self.frames, self.i = [_wall(rng) for _ in range(k)], 0

    def __call__(self, _rng=None):
        self.i += 1
        return self.frames[self.i % len(self.frames)].copy()


# --------------------------------------------------------------------------- #
# (a) the stale latch
# --------------------------------------------------------------------------- #
def test_a_stale_scene_latches_and_stays_latched_until_a_new_plate():
    rng = np.random.default_rng(21)
    det, walls = _det(rng), _Walls(rng)
    assert not det.stale_latched and det.stale_since == ""
    for _ in range(25):                                      # > 1 s of a stale scene
        assert det.process(_stale(walls), 0, 0, SIZE).reason == "plate stale"
        det.update_plate([])
    assert det.stale_latched and det.stale_since
    since = det.stale_since
    for _ in range(300):                                     # the scene matches again: the frames are valid...
        assert det.process(walls(), 0, 0, SIZE).valid
        det.update_plate([])
    assert det.stale_latched and det.stale_since == since    # ...the flag does not go away by itself
    assert not _det(rng).stale_latched                       # a new plate (a new detector) starts clean


def test_a_passer_by_does_not_latch_a_sustained_flicker_does():
    rng = np.random.default_rng(22)
    det, walls = _det(rng), _Walls(rng)
    for _ in range(10):                                      # a few stale frames, then the wall again: no latch
        for _ in range(4):
            det.process(_stale(walls), 0, 0, SIZE)
        for _ in range(12):
            det.process(walls(), 0, 0, SIZE)
    assert not det.stale_latched
    det = _det(rng)
    for _ in range(40):                                      # stale two frames in three: latches (leaky count)
        det.process(_stale(walls), 0, 0, SIZE)
        det.process(_stale(walls), 0, 0, SIZE)
        det.process(walls(), 0, 0, SIZE)
    assert det.stale_latched


def _processor():
    pytest.importorskip("torch")
    from core.pipeline import FrameProcessor, ProcessingSettings
    return FrameProcessor(model=None, settings=ProcessingSettings(
        confidence=0.3, imgsz=640, use_fp16=False, enhance_enabled=False, enhance_lite=False,
        enhance_force=False, person_height_px=200, use_gpu_path=False))


def _gray(level_left, level_right=120):
    g = np.full((400, 200), level_right, np.uint8)
    g[:, :120] = level_left
    return g


def test_the_processor_keeps_stale_until_a_new_plate_and_foreground_off_shows_off():
    proc = _processor()
    plate = CleanPlate(plate=np.full((100, 50), 120.0, np.float32), sigma=0.5, frame_size=(200, 400))
    proc.set_clean_plate(plate)
    proc._belt_gray_offset = (0, 0)
    proc._belt_gray = _gray(120)
    assert proc._fg_frame(200, 400) is not None and proc.plate_status == "ready"
    proc._belt_gray = _gray(40)                              # 60 % of the wall went dark: stale
    for _ in range(25):
        assert proc._fg_frame(200, 400) is None and proc.last_fg.reason == "plate stale"
    assert proc.plate_status == "stale"
    proc._belt_gray = _gray(120)                             # the scene is back: valid frames, still « stale »
    for _ in range(50):
        assert proc._fg_frame(200, 400) is not None
    assert proc.plate_status == "stale"
    proc.settings.fg_enabled = False                         # the foreground off: « off », nothing evaluated
    assert proc._fg_frame(200, 400) is None and proc.last_fg is None and proc.plate_status == "off"
    proc.settings.fg_enabled = True
    proc.set_clean_plate(plate)                              # Calibrate / « capture empty wall »
    assert proc._fg_frame(200, 400) is not None and proc.plate_status == "ready"


def test_health_plate_alert_once_per_episode_cleared_by_a_new_plate():
    hm = HealthMonitor()
    assert hm.plate_tick("") == [] and hm.pop_cleared() == []
    first = hm.plate_tick("stale since 22:15:03")
    assert [a.kind for a in first] == ["plate_stale"] and "22:15:03" in first[0].message
    assert hm.plate_tick("stale since 22:15:03") == []       # no re-ring: the strip keeps it
    assert hm.pop_cleared() == []
    assert hm.plate_tick("") == [] and hm.pop_cleared() == ["plate_stale"]   # a new plate ends the episode
    assert [a.kind for a in hm.plate_tick("exposure changed by hand")] == ["plate_stale"]


def test_the_main_loop_updates_the_empty_wall_line_and_raises_the_alert():
    import runtime.main_loop as ml
    det = SimpleNamespace(stale_since="")
    published, alerts = [], []
    proc = SimpleNamespace(_fg_detector=det, _plate_invalid=None, settings=SimpleNamespace(fg_enabled=True))
    status = {"text": "empty wall: ready"}
    app = SimpleNamespace(last_fps_time=0.0, processor=proc, _health=HealthMonitor(),
                          _plate_status_text=lambda: status["text"],
                          _publish_plate_status=lambda: published.append(status["text"]),
                          bus=SimpleNamespace(ui_ready=True, publish=alerts.append),
                          tracker=SimpleNamespace(logger=SimpleNamespace(log=lambda *a, **k: None)))
    loop = ml.MainLoop(app)
    loop._plate_tick()
    loop._plate_tick()
    assert published == ["empty wall: ready"] and alerts == []          # published once, then only on change
    det.stale_since, status["text"] = "22:15:03", "empty wall: scene changed - Calibrate on the empty wall"
    loop._plate_tick()
    loop._plate_tick()
    assert published[-1].startswith("empty wall: scene changed") and len(published) == 2
    assert [a.kind for a in alerts] == ["plate_stale"]                  # once per episode
    proc.settings.fg_enabled = False                                     # foreground off: no stale alert
    loop._plate_tick()
    assert app._health.pop_cleared() == ["plate_stale"]


def test_the_alerts_strip_keeps_plate_stale():
    pytest.importorskip("dearpygui")
    from ui.adapter import STRIP_ALERTS
    assert "plate_stale" in STRIP_ALERTS and "light_changed" in STRIP_ALERTS


# --------------------------------------------------------------------------- #
# (b) the slow update and a still dancer
# --------------------------------------------------------------------------- #
def _body(frame, x, y, w=60, h=200, dn=30):
    """A dancer, +``dn`` over the wall, centred on (x, y)."""
    x0, y0 = int(x - w / 2), int(y - h / 2)
    frame[y0:y0 + h, x0:x0 + w] = np.clip(frame[y0:y0 + h, x0:x0 + w].astype(np.int16) + dn, 0, 255)
    return frame


def _box(x, y, h=200):
    return (x - 0.3 * h, y - 0.6 * h, x + 0.3 * h, y + 0.6 * h)     # pipeline._fg_protect's shape


def _still_dancer_run(rng, hold: bool, still_blind=400):
    """She walks in seen by YOLO, stops, YOLO sees her 2 s more, then loses her for ``still_blind`` frames
    (the update runs on every frame: 400 updates are 80 times what absorbs a +30 DN body)."""
    det = _det(rng, update_every=1, update_alpha=0.02, hold_groups=hold)
    _wall = _Walls(rng)
    for i in range(30):
        x = 200 + 5 * i
        det.process(_body(_wall(rng), x, 300), 0, 0, SIZE)
        det.update_plate([_box(x, 300)])
    for _ in range(40):
        det.process(_body(_wall(rng), 350, 300), 0, 0, SIZE)
        det.update_plate([_box(350, 300)])
    seen = []
    for _ in range(still_blind):
        fg = det.process(_body(_wall(rng), 350, 300), 0, 0, SIZE)
        seen.append(_near(fg, 350, 300))
        det.update_plate([])
    return det, seen


def test_a_still_dancer_yolo_lost_keeps_her_group_until_she_moves_on():
    rng = np.random.default_rng(31)
    det, seen = _still_dancer_run(rng, hold=True)
    assert all(seen)                                         # her group on every frame of the stillness
    _wall = _Walls(rng)
    for i in range(50):                                      # she moves on (no YOLO), then leaves the wall
        det.process(_body(_wall(rng), 350 + 5 * i, 300), 0, 0, SIZE)
        det.update_plate([])
    for _ in range(30):
        fg = det.process(_wall(rng), 0, 0, SIZE)
        det.update_plate([])
    assert fg.valid and fg.blobs == []                       # no imprint where she hung, nor along her way
    lamp = _wall(rng)
    lamp[200:400, 320:380] += 30                             # later a lasting change at her old place...
    for _ in range(400):
        fg = det.process(lamp, 0, 0, SIZE)
        det.update_plate([])
    assert fg.blobs == []                                    # ...is absorbed: the hold ended when she left


def test_without_the_hold_the_same_still_dancer_is_absorbed():
    """The rule shipped before (``hold_groups=False``): the update eats her once nothing protects her."""
    rng = np.random.default_rng(31)
    _det_, seen = _still_dancer_run(rng, hold=False)
    assert seen[0] and not seen[-1]


def test_a_change_no_person_stood_in_is_still_absorbed():
    rng = np.random.default_rng(32)
    det = _det(rng, update_every=1, update_alpha=0.02)
    _wall = _Walls(rng)
    for _ in range(400):
        frame = _body(_wall(rng), 350, 300)                  # a protected dancer...
        frame[100:200, 600:760] += 40                        # ...and a lamp switched on far from her
        fg = det.process(frame, 0, 0, SIZE)
        det.update_plate([_box(350, 300)])
    assert len(fg.blobs) == 1 and _near(fg, 350, 300)


def test_a_large_change_touching_a_still_dancer_is_not_held_whole():
    rng = np.random.default_rng(33)
    det, seen = _still_dancer_run(rng, hold=True, still_blind=40)
    assert all(seen)
    _wall = _Walls(rng)
    for _ in range(400):                                     # a lit area appears against her side (13 % of the ROI)
        frame = _body(_wall(rng), 350, 300)
        frame[150:450, 380:600] += 30
        fg = det.process(frame, 0, 0, SIZE)
        det.update_plate([])
    assert fg.valid and _near(fg, 350, 300, tol=25)          # she is still there...
    m, ds = fg.mask, fg.ds
    assert m[150 // ds:450 // ds, 420 // ds:600 // ds].mean() < 0.02   # ...the area beyond her rim was absorbed


def test_a_speck_she_touched_is_absorbed_once_she_has_left():
    """A small lasting change merged with a held dancer (a knot, a stain: mur2 7 has one where a dancer
    hung) is not carried after she moved on: without a protect box only a body-sized group stays held."""
    rng = np.random.default_rng(35)
    det, seen = _still_dancer_run(rng, hold=True, still_blind=40)
    assert all(seen)
    _wall = _Walls(rng)

    def frame(x):
        f = _body(_wall(), x, 300)
        f[300:316, 380:396] += 30                            # the speck, against her side while she hangs
        return f

    for _ in range(40):                                      # still, YOLO-blind, the speck merged with her
        det.process(frame(350), 0, 0, SIZE)
        det.update_plate([])
    for i in range(60):                                      # she moves on, seen again
        det.process(frame(350 - 3 * i), 0, 0, SIZE)
        det.update_plate([_box(350 - 3 * i, 300)])
    for _ in range(300):
        fg = det.process(frame(170), 0, 0, SIZE)
        det.update_plate([_box(170, 300)])
    assert _near(fg, 170, 300) and not _near(fg, 388, 308)  # she is there; the speck went into the plate


def test_each_analysed_frame_is_blended_once():
    rng = np.random.default_rng(36)
    det = _det(rng, update_every=1, update_alpha=0.5)
    lamp = _wall(rng)
    lamp[100:200, 600:760] = 60
    det.process(lamp, 0, 0, SIZE)
    assert det.update_plate([])
    before = det._live.copy()
    for _ in range(10):                                      # no new frame analysed: nothing to blend
        assert not det.update_plate([])
    assert np.array_equal(det._live, before)


def test_a_refused_plate_is_not_updated_from_the_last_analysed_frame():
    """X6 refuses the plate (the camera went to another exposure): the pipeline still calls update_plate
    after every frame; it must not keep blending the last frame it analysed, a dancer in it, into the plate."""
    proc = _processor()
    plate = CleanPlate(plate=np.full((100, 50), 120.0, np.float32), sigma=0.5, frame_size=(200, 400),
                       camera={"exposure_us": 25000.0, "gain_db": 30.0})
    proc.set_clean_plate(plate)
    det = proc._fg_detector
    det.p.update_every, det.p.update_alpha = 1, 0.5
    now = {"v": (25000.0, 30.0)}
    proc.set_camera_state(lambda: now["v"])
    g = np.full((400, 200), 120, np.uint8)
    g[100:300, 80:120] = 200                                 # a dancer, nothing protects her
    proc._belt_gray, proc._belt_gray_offset = g, (0, 0)
    assert proc._fg_frame(200, 400) is not None
    det.update_plate([])                                     # the pipeline's call after a valid frame
    before = det._live.copy()
    now["v"] = (8800.0, 30.0)                                # another exposure: the plate is refused
    for _ in range(30):
        assert proc._fg_frame(200, 400) is None and proc.last_fg is not None
        assert not det.update_plate([])                      # the pipeline's call (last_fg is set)
    assert np.array_equal(det._live, before)


def test_the_hold_costs_little():
    import time
    rng = np.random.default_rng(34)
    det = _det(rng, update_every=1)
    frames = [_body(_wall(rng), 350, 300) for _ in range(5)]
    t0 = time.perf_counter()
    for i in range(100):
        det.process(frames[i % 5], 0, 0, SIZE)
        det.update_plate([_box(350, 300)] if i < 50 else [])
    per_frame = (time.perf_counter() - t0) / 100 * 1000
    assert per_frame < 25.0                                   # process + update on 800 x 600, a generous bound
