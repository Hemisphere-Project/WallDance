"""H4 (#t-063): the references are watched (core/reference_watch.py and its readers).

Clauses (the unit's own gate, the real-material sweep is tmp_analysis/r2-1126/gates/h4/run_gate.sh):
  * check_reference on synthetic walls: pure shifts of 0.25 to 10 px in several directions -- the estimate
    within half a pixel from 2 px up, « matches » under half a pixel, « moved » from MOVED_PX (the F2 line);
    a small rotation and a zoom read as moved; the brightness scaled and gamma-bent read as matches; a flat or
    dark wall reads unknown; a dancer-sized blob with exclude=None does not read as moved; another wall reads
    other_wall; the plate in memory (not its file) is the one compared;
  * ReferenceWatch: when a check is due, off the frame path (a worker thread), the verdict held in one place,
    dropped with its plate; the referee hook (three refusals in a row raise the alert); the prompt;
  * the readers: the readiness « empty wall » row and the health line's « empty wall » source say the cause, and
    a moved / other-wall verdict names the line over « a body on the wall has had no point » (still listed);
    the « references » provenance row; the main loop's tick and line, headless; the app's readiness hunk;
    Calibrate records the model and the network input.
"""
from __future__ import annotations

import importlib.util
import sys
import threading
import time
import types
from datetime import datetime
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from core import ops_monitor as OM
from core import reference_watch as RW
from core.foreground import CleanPlate
from core.ops_monitor import (BareGroupWatch, CauseResult, CheckResult, check_empty_wall,
                              check_references, health_line, health_plate_row, reference_state, with_instruction)
from core.reference_watch import (MATCHES, MOVED, MOVED_PX, OTHER_WALL, REFUSED, UNKNOWN, ReferenceVerdict,
                                  ReferenceWatch, check_reference, exclude_boxes, plate_problem)
from runtime import api

W, H = 960, 1280                     # the synthetic frame (portrait, like the façade's crop)
ROI = (60, 40, 840, 1200)


def _scene(seed: int = 0) -> np.ndarray:
    """A façade-like wall at full resolution (float): texture at three scales, window-like blocks, two
    vertical lines (ladders), and an IR oval that goes to white."""
    rng = np.random.default_rng(seed)
    img = np.full((H, W), 60.0, np.float32)
    for s, a in ((2, 4.0), (8, 8.0), (30, 10.0)):
        n = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), s)
        img += a * n / n.std()
    for _ in range(45):
        x, y = int(rng.integers(0, W - 140)), int(rng.integers(0, H - 140))
        w, h = (int(v) for v in rng.integers(25, 140, 2))
        img[y:y + h, x:x + w] += float(rng.uniform(-35, 45))
    for x in (int(W * 0.12), int(W * 0.86)):
        img[:, x:x + 6] += 50.0
    yy, xx = np.mgrid[0:H, 0:W]
    d = ((xx - W * 0.45) / (W * 0.28)) ** 2 + ((yy - H * 0.35) / (H * 0.16)) ** 2
    img *= (1.0 + 4.0 * np.exp(-2.0 * d)).astype(np.float32)
    return img


def _frame(sc, M=None, gain=1.0, gamma=1.0, noise=2.0, seed=1) -> np.ndarray:
    """The camera's mono uint8 frame of scene ``sc`` moved by the 2x3 map ``M`` (a wall feature at p is seen at
    M p), its light scaled by ``gain`` and bent by ``gamma``, with sensor noise."""
    img = sc if M is None else cv2.warpAffine(sc, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    img = np.clip(img, 0, 255) * gain
    if gamma != 1.0:
        img = 255.0 * (np.clip(img, 0, 255) / 255.0) ** gamma
    img = img + np.random.default_rng(seed).normal(0, noise, img.shape)
    return np.clip(np.round(img), 0, 255).astype(np.uint8)


def _shift(dx, dy):
    return np.float32([[1, 0, dx], [0, 1, dy]])


@pytest.fixture(scope="module")
def wall():
    sc = _scene(0)
    plate = CleanPlate.from_frames([_frame(sc, seed=k) for k in range(9)], source="test")
    plate.created = time.strftime("%Y-%m-%dT%H:%M:%S")
    return SimpleNamespace(scene=sc, plate=plate)


# ---------------------------------------------------------------------------------------------- the check itself

@pytest.mark.parametrize("s", [0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0])
def test_a_pure_shift_is_measured_and_named(wall, s):
    for k, ang in enumerate((0, 135, 270)):
        dx, dy = s * np.cos(np.radians(ang)), s * np.sin(np.radians(ang))
        v = check_reference(wall.plate, _frame(wall.scene, _shift(dx, dy), seed=40 + k), ROI)
        assert v.status in (MATCHES, MOVED) and v.score is not None and v.score > 0.9, v
        err = float(np.hypot(v.dx - dx, v.dy - dy))
        if s >= 2.0:
            assert err <= 0.5 and abs(v.shift_px - s) <= 0.5, (s, ang, v)
        if s < 0.5:
            assert v.status == MATCHES and v.shift_px < MOVED_PX, (s, ang, v)
        if s >= MOVED_PX + 0.5:
            assert v.status == MOVED and "the camera moved about" in v.instruction, (s, ang, v)
        if v.status == MOVED:
            assert v.instruction.split(" - ")[-1] == RW.FIX_MOVED


def test_the_moved_line_sits_between_half_a_pixel_and_the_first_shift_f2_feels():
    """The measured F2 line (tmp_analysis/r2-1126/gates/h4/shift_f2.json): more than 0.10 groups a frame on
    nobody from 3 px sideways; « matches » is required under half a pixel."""
    assert 0.5 < MOVED_PX <= 3.0 - 0.3


def test_a_large_shift_is_found_by_the_coarse_search(wall):
    v = check_reference(wall.plate, _frame(wall.scene, _shift(60, -40), seed=50), ROI)
    assert v.status == MOVED and abs(v.dx - 60) < 0.5 and abs(v.dy + 40) < 0.5
    assert "since the empty-wall picture (the wall 60 px right, 40 px higher in the picture)" in v.instruction


@pytest.mark.parametrize("M", [cv2.getRotationMatrix2D((W / 2, H / 2), 0.3, 1.0),
                               cv2.getRotationMatrix2D((W / 2, H / 2), 0.0, 1.005)], ids=["rotation", "zoom"])
def test_a_small_rotation_or_zoom_reads_as_moved(wall, M):
    v = check_reference(wall.plate, _frame(wall.scene, M, seed=51), ROI)
    assert v.status == MOVED and v.model == "affine" and v.shift_px >= MOVED_PX, v
    assert abs(v.dx) < 0.5 and abs(v.dy) < 0.5                     # about the centre: the corners moved


@pytest.mark.parametrize("gain,gamma", [(0.3, 1.0), (0.5, 1.0), (2.0, 1.0), (3.0, 1.0),
                                        (1.0, 0.6), (1.0, 0.8), (1.0, 1.3), (1.0, 1.7), (0.5, 1.5)])
def test_another_light_still_matches(wall, gain, gamma):
    v = check_reference(wall.plate, _frame(wall.scene, gain=gain, gamma=gamma, seed=52), ROI)
    assert v.status == MATCHES and v.shift_px < 0.5, v


def test_a_flat_or_dark_wall_is_unknown_never_green(wall):
    flat = check_reference(wall.plate, _frame(np.full((H, W), 120, np.float32), seed=53), ROI)
    dark = check_reference(wall.plate, _frame(wall.scene, gain=0.005, noise=1.0, seed=54), ROI)
    for v in (flat, dark):
        assert v.status == UNKNOWN and v.shift_px is None and v.structure < RW.STRUCTURE_MIN, v
        assert v.instruction.startswith("not checked against the camera: the camera's picture is too dark")
        assert reference_state(v)[0] == "skip"
    flat_plate = CleanPlate.from_frames([_frame(np.full((H, W), 90, np.float32), seed=k) for k in range(3)])
    v = check_reference(flat_plate, _frame(wall.scene, seed=55), ROI)
    assert v.status == UNKNOWN and "the empty-wall picture is too dark or too flat" in v.instruction


def test_a_dancer_in_view_without_exclude_is_not_moved(wall):
    f = _frame(wall.scene, seed=56)
    cv2.ellipse(f, (400, 500), (28, 75), 0, 0, 360, 215, -1)            # a lit body, ~150 px tall
    cv2.ellipse(f, (600, 300), (28, 75), 10, 0, 360, 30, -1)            # a dark one
    v = check_reference(wall.plate, f, ROI)
    assert v.status == MATCHES and v.shift_px < 0.5, v
    ex = check_reference(wall.plate, f, ROI, exclude=[(360, 410, 440, 590), (560, 210, 640, 390)])
    assert ex.status == MATCHES and ex.shift_px < 0.5 and ex.valid_share < v.valid_share


def test_another_wall_is_named(wall):
    for seed in (1, 2, 3):
        v = check_reference(wall.plate, _frame(_scene(seed), seed=57), ROI)
        assert v.status == OTHER_WALL and v.shift_px is None, v
        assert v.instruction == ("the empty-wall picture is not this wall: nothing in it lines up with the camera - "
                                 + RW.FIX_PLATE)


def test_the_plate_compared_is_the_one_in_memory(wall, tmp_path):
    """A plate retaken or swapped mid-session (held until Save) is the one compared, not its file."""
    path = tmp_path / "latest.npz"
    other = CleanPlate.from_frames([_frame(_scene(4), seed=k) for k in range(3)])
    other.save(path)                                                    # the file holds another wall
    v = check_reference(wall.plate, _frame(wall.scene, seed=58), ROI)
    assert v.status == MATCHES
    assert check_reference(CleanPlate.load(path), _frame(wall.scene, seed=58), ROI).status == OTHER_WALL


def test_the_inputs_it_takes_and_never_raises(wall):
    f = _frame(wall.scene, seed=59)
    bgr = np.repeat(f[:, :, None], 3, axis=2)                          # the camera's BGR copy (R == G == B)
    assert check_reference(wall.plate, bgr, ROI).status == MATCHES
    assert check_reference(wall.plate, f, None).status == MATCHES        # no ROI: the whole frame
    assert check_reference(None, f, ROI).status == UNKNOWN
    assert check_reference(wall.plate, None, ROI).status == UNKNOWN
    assert check_reference(wall.plate, f, (0, 0, 20, 20)).status == UNKNOWN          # a zone too small
    assert check_reference(wall.plate, f, ROI, exclude=[(0, 0, W, H)]).status == UNKNOWN   # all hidden
    assert check_reference(object(), f, ROI).status == UNKNOWN          # garbage: unknown, never raises
    v = check_reference(wall.plate, f, ROI)
    d = v.to_dict()
    assert d["status"] == MATCHES and set(d) >= {"shift_px", "dx", "dy", "structure", "score", "instruction"}
    assert all(ord(c) < 128 for c in v.instruction)


def test_another_crop_is_said(wall):
    """A plate of another camera crop (X6 refuses it too): compared over their common part, and said."""
    f = _frame(wall.scene, seed=60)[: H - 64, :]
    v = check_reference(wall.plate, f, (60, 40, 840, 1100))
    assert v.status == MOVED and "(another crop: 960x1280, camera 960x1216)" in v.instruction
    ex = check_reference(wall.plate, f, (60, 40, 840, 1100), [(0, 0, 9, 9)])
    assert len(v.instruction) <= 300 and len(ex.instruction) <= 300


def test_the_cost_of_one_check_on_the_show_frame(wall):
    """One call on a 1824 x 1260 frame (the façade crop, portrait), off the frame path: say it."""
    sc = cv2.resize(wall.scene, (1260, 1824))
    big = np.clip(sc + np.random.default_rng(3).normal(0, 2, sc.shape), 0, 255).astype(np.uint8)
    plate = CleanPlate.from_frames([big, big])
    ms = []
    for _ in range(3):
        t0 = time.perf_counter()
        check_reference(plate, big, (135, 3, 965, 1771))
        ms.append((time.perf_counter() - t0) * 1000)
    print(f"check_reference on 1824 x 1260: {np.median(ms):.0f} ms (median of 3)")
    assert np.median(ms) < 3000                                          # a slow tick's budget, even loaded


# ----------------------------------------------------------------------------------------------- the watch

def test_the_watch_checks_at_load_after_calibrate_and_every_few_seconds(wall):
    w = ReferenceWatch(run_async=False)
    f = _frame(wall.scene, seed=61)
    assert w.due(0.0, None, in_run=False) is None                        # no plate: nothing to check
    assert w.due(0.0, wall.plate, in_run=False) == "plate"               # at load, as soon as frames arrive
    assert w.submit(0.0, wall.plate, f, ROI) and w.current().status == MATCHES
    assert w.due(1.0, wall.plate, in_run=False) is None
    assert w.due(1.0 + RW.STANDBY_EVERY_S, wall.plate, in_run=False) == "tick"
    assert w.due(1.0 + RW.RUN_EVERY_S, wall.plate, in_run=True) == "tick"   # every few seconds in RUN
    assert w.due(2.0, wall.plate, in_run=True, busy=True) is None         # never while Calibrate runs
    assert w.due(2.5, wall.plate, in_run=True) == "after Calibrate"
    w.submit(2.5, wall.plate, f, ROI)
    other = CleanPlate.from_frames([f, f])                                # Calibrate took a new plate
    assert w.current(other) is None and w.due(3.0, other, in_run=True) == "plate"
    w.submit(3.0, other, f, ROI)
    assert w.current(wall.plate) is None and w.current(other).status == MATCHES
    w.forget()
    assert w.current() is None


def test_an_alert_is_shown_on_its_second_check_a_match_at_once(wall):
    w = ReferenceWatch(run_async=False)
    good, moved = _frame(wall.scene, seed=65), _frame(wall.scene, _shift(6.0, 0.0), seed=66)
    w.submit(0.0, wall.plate, moved, ROI)
    assert w.current() is None                                            # a first alert: not shown yet ...
    assert w.due(0.5, wall.plate, in_run=True) is None
    assert w.due(RW.CONFIRM_S, wall.plate, in_run=True) == "confirm"      # ... checked again a second later
    w.submit(RW.CONFIRM_S, wall.plate, good, ROI)                         # one frame was a fluke: never shown
    assert w.current().status == MATCHES and w.due(2.0, wall.plate, in_run=True) is None
    w.submit(10.0, wall.plate, moved, ROI)
    assert w.current().status == MATCHES                                  # one alert after a match: held
    w.submit(11.0, wall.plate, moved, ROI)
    assert w.current().status == MOVED                                    # two in a row: shown
    w.submit(16.0, wall.plate, moved, ROI)
    assert w.current().status == MOVED                                    # it stays while it holds
    w.submit(21.0, wall.plate, good, ROI)
    assert w.current().status == MATCHES                                  # a match clears it at once


def test_the_watch_runs_off_the_frame_path_and_drops_a_stale_result(wall, monkeypatch):
    gate = threading.Event()
    seen = []

    def slow(plate, frame, roi, exclude):
        seen.append(threading.current_thread().name)
        gate.wait(5.0)
        return ReferenceVerdict(0.1, 0.2, MATCHES, "x")
    monkeypatch.setattr(RW, "check_reference", slow)
    w = ReferenceWatch(run_async=True)
    f = np.zeros((H, W), np.uint8)
    t0 = time.perf_counter()
    assert w.submit(0.0, wall.plate, f, ROI)
    assert time.perf_counter() - t0 < 0.5                                # the caller does not wait
    assert not w.submit(0.1, wall.plate, f, ROI)                          # one check at a time
    f[:] = 255                                                            # the camera's buffer goes on: copied
    gate.set()
    assert w.wait(5.0) and w.current().status == MATCHES and seen == ["ReferenceWatch"]
    gate.clear()
    w.submit(1.0, wall.plate, f, ROI)
    swapped = CleanPlate.from_frames([f, f])
    w.submit(1.1, swapped, f, ROI)                                        # refused: one runs
    with w._lock:
        w._plate = swapped                                                # the plate changed meanwhile
    gate.set()
    w.wait(5.0)
    assert w.current(wall.plate) is None                                  # its result read the old plate


def test_three_referee_refusals_in_a_row_raise_the_plate_alert(wall):
    w = ReferenceWatch(run_async=False)
    w.submit(0.0, wall.plate, _frame(wall.scene, seed=62), ROI)
    assert w.note_referee_refusal("plate stale") == 1
    w.note_referee_refusal("too few dancer groups")
    assert w.current().status == MATCHES                                  # two: recorded, counted, no alert
    w.note_referee_accept()
    w.note_referee_refusal("a")
    w.note_referee_refusal("b")
    assert w.current().status == MATCHES
    w.note_referee_refusal("the plate does not describe the wall")
    v = w.current()
    assert v.status == REFUSED and v.alert
    assert "3 times in a row (the plate does not describe the wall)" in v.instruction
    assert reference_state(v)[0] == "fail"
    w.submit(1.0, CleanPlate.from_frames([_frame(wall.scene, seed=1)] * 2), _frame(wall.scene, seed=63), ROI)
    assert w.current().status == MATCHES                                  # a new plate starts again


def test_the_module_hook_records_and_counts_only(monkeypatch):
    w = ReferenceWatch(run_async=False)
    monkeypatch.setattr(RW, "WATCH", w)
    assert RW.note_referee_refusal("x") == 1 and RW.note_referee_refusal("y") == 2
    assert w.current() is None                                            # nothing checked: no verdict made up
    RW.note_referee_accept()
    assert w.refusals == 0


def test_the_prompt_when_the_wall_is_provably_empty(wall):
    w = ReferenceWatch(run_async=False)
    still = _frame(wall.scene, seed=64)
    t = 0.0
    for _ in range(int(RW.EMPTY_S) + 2):                                  # 1 Hz, nobody, a still picture (the
        w.note_scene(t, still, ROI, detections=0)                         # first sample has none to compare to)
        t += 1.0
    assert w.empty_for(t - 1.0) >= RW.EMPTY_S
    assert w.prompt(t - 1.0, "") is None                                  # a good plate: no prompt
    p = w.prompt(t - 1.0, "the camera moved since the empty-wall picture",
                 ReferenceVerdict(4.0, 0.1, MOVED, "x"), in_run=True)
    assert p == RW.PROMPT_MOVED and p.startswith("the wall is empty now: STANDBY")
    assert w.prompt(t - 1.0, "no empty-wall picture") == RW.PROMPT_RUN
    assert w.take_toast(p) == p and w.take_toast(p) is None              # one toast per episode
    w.note_scene(t, still, ROI, detections=1)                             # a detection: not empty
    assert w.prompt(t, "no empty-wall picture") is None and w.take_toast(None) is None
    for k in range(int(RW.EMPTY_S) + 1):                                  # a moving picture is not still
        moving = still.copy()
        cv2.rectangle(moving, (100 + 40 * k, 300), (180 + 40 * k, 500), 250, -1)
        w.note_scene(t + k, moving, ROI, detections=0)
    assert w.prompt(t + RW.EMPTY_S, "no empty-wall picture") is None
    for k in range(int(RW.EMPTY_S) + 1):                                  # STANDBY: the model does not run
        w.note_scene(t + 10 + k, still, ROI, detections=None)
    assert w.prompt(t + 10 + RW.EMPTY_S, "no empty-wall picture") is None


def test_plate_problem_names_why_the_plate_must_be_retaken():
    assert plate_problem(status="ready") == ""
    assert plate_problem(status="none") == "no empty-wall picture"
    assert "another camera crop" in plate_problem(status="size")
    assert "stale" in plate_problem(status="ready", stale_since="21:40:00")
    assert plate_problem(status="ready", verdict=ReferenceVerdict(3, 0.1, MOVED, "")) == \
        "the camera moved since the empty-wall picture"
    assert "not this wall" in plate_problem(status="stale", verdict=ReferenceVerdict(None, 0.1, OTHER_WALL, ""))
    assert plate_problem(status="ready", verdict=ReferenceVerdict(None, 0.0, UNKNOWN, "")) == ""


def test_exclude_reads_the_boxes_and_the_body_sized_groups():
    from core.foreground import FgBlob, FgFrame
    proc = SimpleNamespace(
        last_raw_boxes=[(100.0, 200.0, 160.0, 350.0, 0.8)],
        last_fg=FgFrame(np.zeros((4, 4), np.uint8), 0.0, 0.0, 4, blobs=[
            FgBlob(500, 600, 2000.0, 1.0, (480, 540, 520, 660)),           # a body
            FgBlob(300, 900, 4000.0, 1.0, (290, 100, 310, 1700)),          # a rim along an edge: kept in
            FgBlob(700, 700, 100.0, 1.0, (695, 695, 705, 705))]))          # a speck
    ex = exclude_boxes(proc, body_h=120.0, zone_area=1000.0 * 1700.0)
    assert ex[0] == pytest.approx((91.0, 177.5, 169.0, 372.5))
    assert ex[1:] == [(472.0, 532.0, 528.0, 668.0)]
    assert exclude_boxes(proc, body_h=120.0, zone_area=1000.0) == ex[:1]  # the groups would hide the zone
    proc.last_fg.valid = False
    assert exclude_boxes(proc, body_h=120.0) == ex[:1]
    assert exclude_boxes(SimpleNamespace()) == []


# ----------------------------------------------------------------------------------- the readiness rows, the line

def _v(status, shift=None, instruction=""):
    if not instruction:
        if status == MOVED:
            instruction = RW._verdict_text(MOVED, shift or 4.0, -(shift or 4.0), 0.0)
        elif status == UNKNOWN:
            instruction = RW._unknown_text("the camera's picture is too dark or too flat to compare", RW.TODO_DARK)
        else:
            instruction = RW._verdict_text(status, shift)
    return ReferenceVerdict(shift, 0.15, status, instruction)


def test_the_readiness_empty_wall_row_says_the_cause():
    head = "empty wall: ready (10-08 21:36, 1260x1824)"
    assert check_empty_wall(status="ready", detail=head).status == "ok"                    # not watched: as before
    ok = check_empty_wall(status="ready", detail=head, reference=_v(MATCHES, 0.1))
    assert ok.status == "ok" and ok.detail == head + ", lines up with the camera (0.1 px)"
    moved = check_empty_wall(status="ready", detail=head, reference=_v(MOVED, 4.0))
    assert moved.status == "fail"
    assert moved.detail == ("the camera moved about 4 px since the empty-wall picture (the wall 4 px left in the "
                            "picture): the zone and the exclusion mask moved with it - STANDBY, check the zone and "
                            "mask (1 Rig), then CALIBRATE on the empty wall, Save to project")
    other = check_empty_wall(status="stale", detail="empty wall: scene changed", reference=_v(OTHER_WALL))
    assert other.status == "fail" and other.detail.startswith("the empty-wall picture is not this wall")
    for ref in (_v(UNKNOWN), "pending"):                                                     # never green
        r = check_empty_wall(status="ready", detail=head, reference=ref)
        assert r.status == "skip" and r.detail.startswith(head + "; not checked against the camera")
    light = check_empty_wall(status="ready", detail=head, light_ratio=0.2, reference=_v(MATCHES, 0.1))
    assert light.status == "warn" and "lines up with the camera" in light.detail             # the light row stays
    assert check_empty_wall(status="none", reference="pending").status == "warn"            # no plate: as before


def test_the_health_line_names_the_plate_over_the_bare_body():
    bare = BareGroupWatch()
    for k in range(80):                                                   # 4 s of a group with no point
        bare.update(k / 20.0, [(500.0, 900.0, 4000.0)], [], body_h=120.0)
    rows = [CheckResult("camera", "ok", "ids"), bare.state(4.0)]
    plain = health_line(rows + [health_plate_row(status="ready")])
    assert plain.source == "points" and "a body on the wall has had no point" in plain.text   # before H4
    row = health_plate_row(status="ready", reference=_v(MOVED, 4.0))
    assert isinstance(row, CauseResult) and row.status == "fail"
    line = health_line(rows + [row])
    assert line.state == "fail" and line.source == "empty wall"
    assert line.text.startswith("FAIL - empty wall: the camera moved about 4 px since the empty-wall picture (the")
    assert line.text.endswith("(+1: points)")                            # the other signal is not hidden
    assert line.instruction == RW.FIX_MOVED
    other = health_line(rows + [health_plate_row(status="stale", reference=_v(OTHER_WALL))])
    assert other.source == "empty wall" and "not this wall" in other.text
    refused = ReferenceVerdict(None, 0.1, REFUSED, f"the referee refused it 3 times in a row (x) - {RW.FIX_PLATE}")
    assert health_line(rows + [health_plate_row(status="ready", reference=refused)]).source == "empty wall"
    camera_down = CheckResult("camera", "fail", "not open - check the camera's USB3 cable and power")
    assert health_line([camera_down, bare.state(4.0), row]).source == "empty wall"   # a cause first, then rank


def test_the_health_line_row_for_each_verdict():
    assert health_plate_row(status="ready", reference=_v(MATCHES, 0.2)).status == "ok"
    unk = health_plate_row(status="ready", reference=_v(UNKNOWN))
    assert unk.status == "skip" and unk.detail.startswith("ready; not checked against the camera")
    assert health_plate_row(status="ready", reference=None).detail == "ready"            # no verdict yet: as before
    stale = health_plate_row(status="stale", stale_since="21:40:00", reference=_v(MATCHES, 0.2))
    assert stale.status == "fail" and stale.detail.startswith("stale since 21:40:00")     # F2's latch stays
    assert health_plate_row(status="off", reference=_v(MOVED, 4.0)).status == "warn"      # not in use: as before


def test_the_prompt_replaces_the_rows_instruction():
    r = health_plate_row(status="none", prompt=RW.PROMPT_RUN)
    assert r.status == "warn" and r.detail == "none - " + RW.PROMPT_RUN
    m = health_plate_row(status="ready", reference=_v(MOVED, 4.0), prompt=RW.PROMPT_MOVED)
    assert isinstance(m, CauseResult) and m.detail.endswith(" - " + RW.PROMPT_MOVED)
    assert health_line([m]).instruction == RW.PROMPT_MOVED
    assert with_instruction(CheckResult("x", "warn", "a fact"), "do it").detail == "a fact - do it"


def test_the_references_row_says_where_they_come_from():
    now = datetime(2026, 12, 12, 19, 0)
    plate = SimpleNamespace(created="2026-12-12T18:40:00")
    same = dict(model="yolo11l-pose", input="1920x1056")
    ok = check_references(plate=plate, calibrated_at="2026-12-12 18:39", calibrated_with=same,
                          model="yolo11l-pose", net_input="1920x1056", session_day="2026-12-12", now=now)
    assert ok.status == "ok" and ok.name == "references"
    assert ok.detail == ("empty wall taken 2026-12-12 18:40 (20 min ago); Calibrate 2026-12-12 18:39 with "
                         "yolo11l-pose @ 1920x1056; runs yolo11l-pose @ 1920x1056")
    old = check_references(plate=SimpleNamespace(created="2026-10-08T21:36:18"), calibrated_at="2026-10-08 21:32",
                           calibrated_with=None, model="yolo11l-pose", net_input="1920x1056",
                           session_day="2026-12-12", now=now)
    assert old.status == "warn" and "(65 days ago)" in old.detail and "(its model not recorded)" in old.detail
    assert "the empty-wall picture is from another day (2026-10-08)" in old.detail
    assert old.detail.endswith(" - " + OM.REFERENCES_HOW)
    model = check_references(plate=plate, calibrated_at="2026-12-12 18:39",
                             calibrated_with=dict(model="yolo11x-pose", input="1280"), model="yolo11l-pose",
                             net_input="1920x1056", session_day="2026-12-12", now=now)
    assert model.status == "warn" and "Calibrate was made with yolo11x-pose, yolo11l-pose runs now" in model.detail
    assert "Calibrate was made at 1280, the network runs at 1920x1056" in model.detail
    late = check_references(plate=SimpleNamespace(created="2026-12-13T00:20:00"), calibrated_at="2026-12-13 00:19",
                            calibrated_with=same, model="yolo11l-pose", net_input="1920x1056",
                            session_day="2026-12-12", session_start=datetime(2026, 12, 12, 18, 0),
                            now=datetime(2026, 12, 13, 0, 30))
    assert late.status == "ok"                                           # taken in this session, past midnight
    none = check_references(plate=None, calibrated_at="", model="m", net_input="1280", session_day="2026-12-12")
    assert none.status == "warn" and "no empty-wall picture" in none.detail and "no Calibrate recorded" in none.detail
    assert all(ord(c) < 128 for r in (ok, old, model, none) for c in r.detail)


# ------------------------------------------------------------------------------------------ the main loop, headless

def _import_main_loop():
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


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _loop_app(events, plate, frame, plate_status="ready"):
    from core.foreground import FgBlob, FgFrame

    class _Proc:
        def __init__(self):
            self.last_fg = None
            self.last_emitted = []
            self.last_raw_boxes = []
            self.last_raw_dets = []
            self.plate_status = plate_status
            self.stall = SimpleNamespace(stalls=0)
            self.osc = SimpleNamespace(send_errors=0)
            self.dancer_height = (120.0, 40, 0.0)
            self._plate_invalid = None
            self.plate_refusal = None
            self._plate_capture = None
            self._fg_detector = SimpleNamespace(plate=plate, stale_since="")

        @staticmethod
        def sent_xy(tr):
            return float(tr.x), float(tr.y)

        def _zone_ok_fn(self, w, h):
            return lambda x, y: True

        def new_frame(self, groups):
            self.last_fg = FgFrame(np.zeros((4, 4), np.uint8), 0.0, 0.0, 4,
                                   blobs=[FgBlob(x, y, a, 1.0, (x - 20, y - 60, x + 20, y + 60)) for x, y, a in groups])

    return SimpleNamespace(
        last_fps_time=0.0, processor=_Proc(), fps=19.8, _last_raw_frame=frame,
        _reference_watch=ReferenceWatch(run_async=False),
        camera=SimpleNamespace(state=SimpleNamespace(is_open=True, source="ids")),
        recorder=SimpleNamespace(is_playing=False),
        cameras=SimpleNamespace(_camera_reconnecting=False),
        calibration=SimpleNamespace(_calibrating=False),
        models=SimpleNamespace(_model_loaded=True, _trt_requested=True,
                               model_manager=SimpleNamespace(is_using_tensorrt=lambda: True)),
        settings=SimpleNamespace(person_height_px=150, roi_enabled=True, roi_x=ROI[0], roi_y=ROI[1], roi_w=ROI[2],
                                 roi_h=ROI[3]),
        osc_enabled=True, osc_ip="127.0.0.1", osc_port=9000,
        _health=OM.HealthMonitor(gpu_stats_fn=None),
        _readiness_setup_rows=lambda: [],
        bus=SimpleNamespace(publish=lambda e: events.append(e)))


def test_the_main_loop_checks_at_load_and_the_line_names_a_moved_plate(wall, monkeypatch):
    ml = _import_main_loop()
    clk = _Clock()
    monkeypatch.setattr(ml, "time", SimpleNamespace(monotonic=clk, time=clk, perf_counter=clk))
    events = []
    moved = _frame(wall.scene, _shift(-6.0, 0.0), seed=70)               # the camera knocked 6 px
    app = _loop_app(events, wall.plate, moved)
    loop = ml.MainLoop(app)
    loop._reference_tick(False)                                           # STANDBY: at load, frames arrived
    assert app._reference_watch.last_why == "plate" and app._reference_watch.current(wall.plate) is None
    clk.t += RW.CONFIRM_S
    loop._reference_tick(False)                                           # the alert's second check, a second on
    v = app._reference_watch.current(wall.plate)
    assert v is not None and v.status == MOVED and app._reference_watch.last_why == "confirm"
    proc = app.processor
    for _ in range(int(3.5 * 20)):                                        # its rims: a « body » with no point
        proc.new_frame([(500.0, 900.0, 4000.0)])
        loop._health_line_frame()
        clk.t += 0.05
    loop._health_line_tick(True)
    e = events[-1]
    assert isinstance(e, api.HealthLine) and e.state == "fail" and e.data["source"] == "empty wall"
    assert e.text.startswith("FAIL - empty wall: the camera moved about 6 px since the empty-wall picture (the "
                             "wall 6 px left in the picture)")
    assert any(r["name"] == "points" and r["status"] == "fail" for r in e.data["rows"])   # still listed


def test_the_main_loop_in_run_leaves_out_the_boxes_and_prompts_once(wall, monkeypatch):
    ml = _import_main_loop()
    clk = _Clock()
    monkeypatch.setattr(ml, "time", SimpleNamespace(monotonic=clk, time=clk, perf_counter=clk))
    events = []
    still = _frame(wall.scene, _shift(0.0, 8.0), seed=71)                 # moved 8 px down, nobody in view
    app = _loop_app(events, wall.plate, still)
    seen = []
    real_submit = app._reference_watch.submit

    def spy(now, plate, frame, roi=None, exclude=None, why=""):
        seen.append((why, exclude))
        return real_submit(now, plate, frame, roi, exclude, why)
    app._reference_watch.submit = spy
    app.processor.last_raw_boxes = [(100.0, 200.0, 160.0, 350.0, 0.7)]
    toasts = []
    for k in range(int(RW.EMPTY_S) + 3):                                  # RUN, 1 Hz: no detection, still
        loop = loop if k else ml.MainLoop(app)
        loop._reference_tick(True)
        loop._health_line_tick(True)
        toasts += [e for e in events if isinstance(e, api.Toast)]
        events.clear()
        clk.t += 1.0
    assert seen and seen[0][0] == "plate" and seen[0][1][0] == pytest.approx((91.0, 177.5, 169.0, 372.5))
    assert app._reference_watch.current().status == MOVED
    assert len(toasts) == 1 and toasts[0].message.startswith("The camera moved since the empty-wall picture - "
                                                             "the wall is empty now: STANDBY")
    loop._health_line_tick(True)
    assert events[-1].data["instruction"] == RW.PROMPT_MOVED and events[-1].data["source"] == "empty wall"
    assert " - " + RW.PROMPT_MOVED in events[-1].text


def test_the_main_loop_watch_never_breaks_the_loop(capsys):
    ml = _import_main_loop()
    loop = ml.MainLoop(SimpleNamespace(last_fps_time=0.0, processor=SimpleNamespace()))
    loop._reference_tick(True)
    assert loop._reference_line(None, "ready", True, 0.0) == (None, None)
    assert "[Health] the reference watch failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------- the app, Calibrate

def _app_mod():
    pytest.importorskip("torch")                      # app imports the pipeline
    import app
    return app


def test_the_readiness_report_reads_the_watch_and_says_the_provenance(wall):
    A = _app_mod().WallDanceApp
    watch = ReferenceWatch(run_async=True)
    moved = _frame(wall.scene, _shift(5.0, 0.0), seed=72)
    stub = SimpleNamespace(
        _reference_watch=watch, _last_raw_frame=moved, _light_ratio=None, unified_camera=None,
        recorder=SimpleNamespace(is_playing=False),
        processor=SimpleNamespace(_fg_detector=SimpleNamespace(plate=wall.plate), plate_status="ready",
                                  _plate_capture=None),
        settings=SimpleNamespace(roi_enabled=True, roi_x=ROI[0], roi_y=ROI[1], roi_w=ROI[2], roi_h=ROI[3],
                                 yolo_shape=(1920, 1056), imgsz=1280),
        models=SimpleNamespace(current_model_name="yolo11l-pose"),
        calibration=SimpleNamespace(_calibrating=False, calibration_state={
            "gamma": {"source": "aim", "ts": "2026-12-12 18:39", "epoch": 2.0},
            "network": {"source": "calibrate", "ts": "2026-12-12 18:39", "epoch": 2.0, "model": "yolo11x-pose",
                        "input": "1280"}}),
        _plate_status_text=lambda: "empty wall: ready (12-12 18:40, 960x1280)")
    results = [CheckResult("calibration", "ok", "saved"), CheckResult("empty wall", "ok", "empty wall: ready"),
               CheckResult("disk", "ok", "plenty")]
    A._reference_readiness(stub, results)                                 # no verdict yet: read now
    names = [r.name for r in results]
    assert names == ["calibration", "empty wall", "references", "disk"]
    assert results[1].status == "fail"
    assert results[1].detail.startswith("the camera moved about 5 px since the empty-wall picture (the wall 5 px right")
    ref = results[2]
    assert ref.status == "warn" and "Calibrate was made with yolo11x-pose, yolo11l-pose runs now" in ref.detail
    assert "Calibrate was made at 1280, the network runs at 1920x1056" in ref.detail
    watch.forget()
    stub.system_state = api.SystemState.RUN                               # in RUN the loop is never held
    results = [CheckResult("empty wall", "ok", "empty wall: ready")]
    t0 = time.perf_counter()
    A._reference_readiness(stub, results)
    assert time.perf_counter() - t0 < 1.0 and results[0].status == "skip"
    assert "not checked against the camera yet" in results[0].detail
    del stub.system_state
    stub._last_raw_frame = None
    watch.forget()
    results = [CheckResult("empty wall", "ok", "empty wall: ready")]
    A._reference_readiness(stub, results)                                 # no picture: not checked, never green
    assert results[0].status == "skip" and "not checked against the camera yet" in results[0].detail
    broken = SimpleNamespace()
    results = []
    A._reference_readiness(broken, results)
    assert results[0].name == "references" and results[0].status == "skip"


def test_calibrate_records_the_model_and_the_network_input():
    from unittest.mock import MagicMock
    from core.calibration import CalibrationResult
    from runtime.calibration_flows import CalibrationFlows
    settings = SimpleNamespace(person_height_px=150, imgsz=1280, yolo_shape=(1920, 1056), confidence=0.15)
    flows = CalibrationFlows(
        processor=MagicMock(), enhancer=MagicMock(gamma=1.0, clahe_clip=2.0), tracker=MagicMock(),
        settings=settings, recorder=MagicMock(), camera=MagicMock(), unified_camera=MagicMock(), use_unified=False,
        models=SimpleNamespace(current_model_name="yolo11l-pose", _model_loaded=True), cameras=MagicMock(),
        configs=MagicMock(), ui=MagicMock(available=False), last_raw_frame=lambda: None,
        roi_source_size=lambda: (1260, 1824), get_effective_roi=lambda w, h: (0, 0, w, h),
        reset_sensitivity_anchor=lambda **k: None, sync_mask_ui=lambda: None, request_reprocess=lambda: None,
        imgsz_change=lambda v: None)
    flows.wall_check_enabled = False
    res = CalibrationResult()
    res.var_ok, res.var_threshold, res.mog2_scale, res.noise_sigma = True, 16.0, 0.7, 2.0
    flows._apply_calibration(res)
    net = flows.calibration_state["network"]
    assert net["model"] == "yolo11l-pose" and net["input"] == "1920x1056" and net["source"] == "calibrate"
    settings.yolo_shape = None
    flows._apply_calibration(res)
    assert flows.calibration_state["network"]["input"] == "1280"


# ------------------------------------------------------------- item 7: a plate that makes the foreground blind
def _dark(plate):
    """The plate of the same wall captured on a dark picture (the 14/10 10:02 case: its brightest part ~13 DN)."""
    import dataclasses
    return dataclasses.replace(plate, plate=(plate.plate * 0.05).astype(np.float32), sigma=0.07)


def test_the_plate_signal_refuses_a_plate_too_dark_for_the_foreground(wall):
    ok = RW.plate_signal(wall.plate, ROI)
    assert ok.usable and ok.text == "" and ok.ratio >= 3 * RW.PLATE_SNR_MIN
    bad = RW.plate_signal(_dark(wall.plate), ROI)
    assert not bad.usable and bad.p99 <= 13.0 and bad.noise == 6.0 and bad.ratio < RW.PLATE_SNR_MIN
    assert bad.text.startswith("the empty-wall picture is too dark for the foreground (its brightest part 1")
    assert bad.text.endswith(" - " + RW.FIX_DARK) and bad.text.isascii() and bad.text.count(" - ") == 1
    assert RW.plate_signal(None) is None
    assert not RW.plate_signal(SimpleNamespace(plate=None, ds=4, sigma=1.0)).usable     # unreadable: not usable


def test_the_plate_signal_on_the_plates_on_disk():
    """The line between the 14/10 10:02 plate (blind: 0 of 169 dancers) and the lit plate of 08/10."""
    root = "/data/WallDance/projects/jeudi0809_mur2/plates/"
    try:
        blind, lit = (CleanPlate.load(root + n) for n in ("plate_20261014_100200.npz", "plate_20261008_213618.npz"))
    except (OSError, FileNotFoundError):
        pytest.skip("the 08/10 plates are not on this machine")
    zone = (135, 3, 965, 1771)
    b, l_ = RW.plate_signal(blind, zone), RW.plate_signal(lit, zone)
    assert not b.usable and b.ratio < RW.PLATE_SNR_MIN / 1.4 and l_.usable and l_.ratio > 10 * RW.PLATE_SNR_MIN


def test_a_dark_plate_is_the_cause_in_readiness_and_on_the_line(wall):
    w = ReferenceWatch(run_async=False)
    dark = _dark(wall.plate)
    sig = w.note_plate(dark, ROI)
    assert sig is not None and not sig.usable and w.note_plate(dark, ROI) is None      # read once per plate
    v = w.current(dark)
    assert v.status == RW.DARK_PLATE and v.alert and v.instruction == sig.text
    assert w.current(wall.plate) is None                                                # another plate: not this one
    row = check_empty_wall(status="ready", detail="empty wall: ready", reference=v)
    assert row.status == "fail" and row.detail == sig.text                              # readiness says the cause
    bare = BareGroupWatch()
    for k in range(80):
        bare.update(k / 20.0, [(500.0, 900.0, 4000.0)], [], body_h=120.0)
    line = health_line([bare.state(4.0), health_plate_row(status="ready", reference=v)])
    assert line.source == "empty wall" and line.instruction == RW.FIX_DARK
    assert RW.plate_problem(status="ready", verdict=v) == "the empty-wall picture is too dark for the foreground"
    w._empty_since = 0.0
    assert w.prompt(100.0, RW.plate_problem(status="ready", verdict=v), v) == RW.PROMPT_DARK
    assert w.note_plate(wall.plate, ROI) is None and w.current(wall.plate) is None       # a usable plate: no alert


def test_the_foreground_that_sees_nothing_names_the_plate(wall):
    w = ReferenceWatch(run_async=False)
    p = wall.plate
    for _ in range(RW.BLIND_TICKS - 1):
        assert not w.note_foreground(p, 2, 0)
    assert not w.note_foreground(p, None, 0) and not w.note_foreground(p, 2, None)     # not counted, not reset
    assert not w.note_foreground(p, 0, 0) and w.blind_ticks == RW.BLIND_TICKS - 1       # an empty wall: neither
    assert w.note_foreground(p, 1, 0)
    v = w.current(p)
    assert v.status == RW.BLIND and v.alert and v.instruction.endswith(" - " + RW.FIX_DARK)
    assert v.instruction.startswith(f"the foreground found no one on {RW.BLIND_TICKS} checks while the detector saw")
    assert health_plate_row(status="ready", reference=v).status == "fail"
    assert not w.note_foreground(p, 2, 1) and w.current(p) is None                      # one group: it sees again
    for _ in range(RW.BLIND_TICKS):
        w.note_foreground(p, 2, 0)
    assert w.current(p).status == RW.BLIND
    assert not w.note_foreground(_dark(p), 2, 0) and w.blind_ticks == 1                 # another plate: from zero
    groups = SimpleNamespace(last_fg=SimpleNamespace(valid=True, blobs=[SimpleNamespace(area=4000.0),
                                                                        SimpleNamespace(area=50.0)]),
                             last_raw_dets=[(0.8, 1.0, 1.0, 100.0), (0.2, 1.0, 1.0, 100.0), (0.41, 0, 0, 90.0)])
    assert RW.foreground_groups(groups) == 1 and RW.confident_dancers(groups) == 2
    assert RW.foreground_groups(SimpleNamespace(last_fg=SimpleNamespace(valid=False, blobs=[]))) is None


def test_the_main_loop_says_a_dark_plate_once_and_a_blind_foreground(wall, monkeypatch):
    ml = _import_main_loop()
    clk = _Clock()
    monkeypatch.setattr(ml, "time", SimpleNamespace(monotonic=clk, time=clk, perf_counter=clk))
    events = []
    app = _loop_app(events, _dark(wall.plate), _frame(wall.scene, seed=72))
    loop = ml.MainLoop(app)
    toasts = []
    for _ in range(3):
        loop._reference_tick(False)
        toasts += [e for e in events if isinstance(e, api.Toast)]
        events.clear()
        clk.t += 1.0
    assert len(toasts) == 1 and toasts[0].message.startswith("The empty-wall picture is too dark for the foreground")
    loop._health_line_tick(False)
    assert events[-1].data["source"] == "empty wall" and events[-1].data["instruction"] == RW.FIX_DARK
    # a usable plate, RUN: dancers detected, the foreground finds no group, tick after tick
    events.clear()
    app = _loop_app(events, wall.plate, _frame(wall.scene, seed=73))
    proc = app.processor
    proc.last_raw_dets = [(0.85, 500.0, 900.0, 300.0)]
    loop = ml.MainLoop(app)
    for _ in range(RW.BLIND_TICKS):
        proc.new_frame([])
        loop._reference_tick(True)
        clk.t += 1.0
    assert app._reference_watch.current(wall.plate).status == RW.BLIND
    loop._health_line_tick(True)
    assert events[-1].data["source"] == "empty wall" and "found no one" in events[-1].text
    proc.new_frame([(500.0, 900.0, 4000.0)])                                  # a group again
    loop._reference_tick(True)
    assert app._reference_watch.current(wall.plate).status == MATCHES
