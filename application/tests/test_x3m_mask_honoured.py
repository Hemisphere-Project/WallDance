"""X3m (hub walldance#t-027, seen on the show laptop 27/11): Calibrate's « someone in view » honours the
Exclusion Mask, as RUN does.

27/11 10:30, the real camera: Calibrate stopped on a shape of the room the detector took for a person (x 351,
y 1668, h 301 on the 1260 x 1824 portrait picture, conf 0.34 in 6 of 7 frames) and stopped again with the shape
in 9, then 20 masked cells (``ExcludeAt``): the emptiness watch and the ladder read ``last_raw_dets``, the boxes
from before RUN's exclusion step.  Now a box whose centre sits in an excluded cell -- the pipeline's own test,
``FrameProcessor._zone_ok_fn`` -- is no one in view and no ghost against an enhancement, and the log says it was
left out.  Flow-level tests driven as X3's own (tests/test_x3_emptiness_first.py: its rig, its fake processor),
with the real ``_zone_ok_fn`` / ``_resolve_roi`` of ``core.pipeline.FrameProcessor`` and a real
``ExclusionMaskBuilder`` holding the cells as ``ExcludeAt`` paints them (manual adds).
"""
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("torch")                         # core.pipeline, as the A6 real-processor tests

import test_x3_emptiness_first as x3                 # noqa: E402  X3's rig and fake processor
from core.calibration import ExclusionMaskBuilder    # noqa: E402
from core.empty_wall import (OBJECT_WAYS_OUT, PERSON_FRAMES, PERSON_SETTLE, EmptyWallCheck,  # noqa: E402
                             MaskedBoxes)
from core.pipeline import FrameProcessor, ProcessingSettings   # noqa: E402

ADVICE = ("If it is a person: clear the wall. If it is an object: exclude it (1 Rig > Exclusion Mask) or move "
          "the Region of Interest off it. Then press Calibrate again.")
# the 27/11 case: the shape's box, the picture, the grid (90 deg input: cols / rows swapped), the 9 cells
SHAPE = (0.34, 351.0, 1668.0, 301.0)
LAPTOP_FRAME = (1824, 1260)                           # rows, cols: the 1260 x 1824 portrait picture
LAPTOP_ROI = (0, 0, 1260, 1824)                       # « ROI 1260x1824 at (0, 0) ... (100 % of it) »
LAPTOP_GRID = (10, 16)
LAPTOP_CELLS = [(1, 13), (1, 14), (1, 15), (2, 13), (2, 14), (2, 15), (3, 13), (3, 14), (3, 15)]


class _MaskProc(x3._Proc):
    """X3's fake processor with the pipeline's own Exclusion Mask test: the real methods, a real mask."""
    _zone_ok_fn = FrameProcessor._zone_ok_fn
    _resolve_roi = FrameProcessor._resolve_roi

    def __init__(self, roi=x3.ROI, grid=(16, 10), cells=()):
        super().__init__()
        x, y, w, h = roi
        self.settings = SimpleNamespace(roi_enabled=True, roi_x=x, roi_y=y, roi_w=w, roi_h=h)
        self._exclusion = ExclusionMaskBuilder(grid=grid)
        self._exclusion.set_cells(grid, [], manual_add=list(cells))      # what ExcludeAt paints


def _rig(monkeypatch, proc, frame=None, roi=None, **kw):
    """X3's rig around ``proc``; ``frame`` (rows, cols) and ``roi`` for another picture than X3's 120 x 200."""
    monkeypatch.setattr(x3, "_Proc", lambda: proc)
    flows, _p, plates = x3._rig(**kw)
    if frame is not None:
        img = np.full(frame, 40, np.uint8)
        flows.last_raw_frame = lambda: img
        flows.roi_source_size = lambda: (frame[1], frame[0])
        flows.get_effective_roi = lambda w, h: roi
    return flows, plates


def _cell(proc, x, y, w=120, h=200):
    """The excluded-cell grid's (col, row) under an original-px point (normalized over the ROI, as the pipeline)."""
    x0, y0, x1, y1 = proc._resolve_roi(w, h)
    return proc._exclusion.cell_at((x - x0) / (x1 - x0), (y - y0) / (y1 - y0))


def _around(c, r, n=1):
    return [(c + i, r + j) for i in range(-n, n + 1) for j in range(-n, n + 1)]


SHE_CELLS = _around(*_cell(_MaskProc(), x3.SHE[1], x3.SHE[2]))           # the 9 cells around her centre
FAR_CELLS = _around(*_cell(_MaskProc(), 85.0, 170.0))                     # bottom right, far from her


# ---- (c) the 27/11 case ---------------------------------------------------------------------------------------
def test_the_27_11_shape_in_the_nine_cells_the_laptop_masked_is_not_counted():
    proc = FrameProcessor(model=None, settings=ProcessingSettings(          # the real processor (A6's CPU rig)
        confidence=0.15, imgsz=640, use_fp16=False, enhance_enabled=False, enhance_lite=False,
        enhance_force=False, person_height_px=110, use_gpu_path=False))
    proc.settings.roi_enabled = True
    proc.settings.roi_x, proc.settings.roi_y, proc.settings.roi_w, proc.settings.roi_h = LAPTOP_ROI
    w, h = LAPTOP_FRAME[1], LAPTOP_FRAME[0]
    assert proc._zone_ok_fn(w, h)(SHAPE[1], SHAPE[2]) is True                # no mask: it counts, as on 27/11
    proc.set_exclusion(LAPTOP_GRID, [], manual_add=LAPTOP_CELLS)             # the real processor's own door
    zone_ok = proc._zone_ok_fn(w, h)
    assert zone_ok(SHAPE[1], SHAPE[2]) is False                              # RUN drops it
    tally = MaskedBoxes()
    assert tally.keep([SHAPE], zone_ok) == [] and tally.boxes == 1
    assert "x=351 y=1668 h=301" in tally.note() and "conf=0.34" in tally.note()


def test_the_27_11_case_on_the_flow_calibrates_to_its_end_with_the_mask_and_stops_without(monkeypatch, capsys):
    proc = _MaskProc(LAPTOP_ROI, LAPTOP_GRID, LAPTOP_CELLS)
    flows, _plates = _rig(monkeypatch, proc, LAPTOP_FRAME, LAPTOP_ROI)
    flows._cb_calibrate()
    used = x3._run(flows, proc, lambda i, f: [SHAPE])                        # the shape on every frame
    r = flows._wall_result
    assert flows.last_stop is None and r is not None and r.clean and r.kept_current
    assert used > PERSON_SETTLE + PERSON_FRAMES and len(r.tried) == 1       # the ladder did not step down
    flows.ui.show_calibration_result_dialog.assert_called_once()
    out = capsys.readouterr().out
    assert "[Calibrate] the wall is empty: a person in 0 of" in out and "ignored (Exclusion Mask)" in out
    assert "[EmptyWall] kept gamma=1.21 clahe=2.5 clean=True" in out
    # the same box, no mask: the stop of 27/11, now with both ways out
    proc2 = _MaskProc(LAPTOP_ROI, LAPTOP_GRID, ())
    flows2, _p = _rig(monkeypatch, proc2, LAPTOP_FRAME, LAPTOP_ROI)
    flows2._cb_calibrate()
    x3._run(flows2, proc2, lambda i, f: [SHAPE])
    assert flows2.last_stop == ("Someone is in view, bottom left of the wall (x 351, y 1668): Calibrate stopped "
                                "and changed nothing. " + ADVICE)
    assert "ignored" not in capsys.readouterr().out


# ---- (a) the flow: a box in an excluded cell, the same box unmasked, a dancer outside the mask --------------------
def test_someone_inside_the_mask_on_every_frame_lets_calibrate_run_to_its_end(monkeypatch):
    """A person (or a thing) standing INSIDE a masked zone during Calibrate: Calibrate goes on, as RUN ignores
    that zone; the enhancement is the one the empty wall gives."""
    proc = _MaskProc(cells=SHE_CELLS)
    flows, plates = _rig(monkeypatch, proc)
    flows._cb_calibrate()
    used = x3._run(flows, proc, lambda i, f: [x3.SHE])
    r = flows._wall_result
    assert flows.last_stop is None and r is not None and r.clean and r.kept_current and len(r.tried) == 1
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (1.2147, 2.5)
    assert used > PERSON_SETTLE + PERSON_FRAMES and plates == ["calibrate"]  # the picture of the whole zone
    assert r.masked is not None and r.masked.boxes == r.masked.frames > 0   # every frame held her, left out
    # the empty wall, nobody: the same end
    proc0 = _MaskProc(cells=SHE_CELLS)
    flows0, _p = _rig(monkeypatch, proc0)
    flows0._cb_calibrate()
    used0 = x3._run(flows0, proc0, lambda i, f: [])
    r0 = flows0._wall_result
    assert (used0, r0.gamma, r0.clahe, r0.clean, len(r0.tried)) == (used, r.gamma, r.clahe, True, 1)


def test_the_same_box_with_no_mask_stops_as_before_with_the_same_line_plus_the_advice(monkeypatch):
    old_flows, old_proc, _ = x3._rig()                                       # X3's processor: no mask test at all
    old_flows._cb_calibrate()
    old_used = x3._run(old_flows, old_proc, lambda i, f: [x3.SHE])
    proc = _MaskProc(cells=())                                               # the pipeline's test, nothing masked
    flows, plates = _rig(monkeypatch, proc)
    flows._cb_calibrate()
    used = x3._run(flows, proc, lambda i, f: [x3.SHE])
    assert used == old_used <= PERSON_SETTLE + 10 and not flows._calibrating and plates == []
    assert flows.last_stop == old_flows.last_stop == (
        "Someone is in view, middle left of the wall (x 30, y 95): Calibrate stopped and changed nothing. " + ADVICE)
    flows.ui.set_calibrate_note.assert_called_with("Stopped: someone in view, middle left of the wall (x 30, y 95)")


def test_a_dancer_outside_the_mask_still_stops_calibrate_with_a_mask_elsewhere(monkeypatch, capsys):
    proc = _MaskProc(cells=FAR_CELLS)
    flows, plates = _rig(monkeypatch, proc)
    flows._cb_calibrate()
    used = x3._run(flows, proc, lambda i, f: [x3.SHE, (0.9, 85.0, 170.0, 40.0)])   # she + a masked thing
    assert used <= PERSON_SETTLE + 10 and not flows._calibrating and plates == []
    assert "middle left of the wall (x 30, y 95)" in flows.last_stop          # her place, not the masked thing's
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (1.2147, 2.5)
    line = [s for s in capsys.readouterr().out.splitlines() if s.startswith("[Calibrate] stopped:")][-1]
    assert "ignored (Exclusion Mask): " in line and "strongest conf=0.90 at x=85 y=170 h=40" in line


def test_the_ladder_does_not_step_down_for_a_box_in_the_mask(monkeypatch, capsys):
    """« YOLO now and then sees a person on the EMPTY wall »: a weak box (over the check's limit, under the live
    confidence: no person, a ghost) in a masked cell on every frame of the check."""
    ghost = (0.13, x3.SHE[1], x3.SHE[2], x3.SHE[3])
    in_check = lambda i, f: [ghost] if f._wall_check is not None else []    # noqa: E731
    proc = _MaskProc(cells=())                                               # unmasked: down to the floor
    flows, _p = _rig(monkeypatch, proc)
    flows._cb_calibrate()
    x3._run(flows, proc, in_check)
    r = flows._wall_result
    assert r is not None and not r.clean and len(r.tried) > 1 and flows.enhancer.gamma < 1.2147
    proc = _MaskProc(cells=SHE_CELLS)                                        # masked: the first rung kept
    flows, _p = _rig(monkeypatch, proc)
    flows._cb_calibrate()
    x3._run(flows, proc, in_check)
    r = flows._wall_result
    assert r.clean and r.kept_current and len(r.tried) == 1 and r.tried[0].max_conf == 0.0
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (1.2147, 2.5)
    assert "ignored (Exclusion Mask)" in r.log_line() and "ignored (Exclusion Mask)" in capsys.readouterr().out


def test_a_person_walking_in_during_the_ladder_inside_the_mask_lowers_nothing_outside_stops(monkeypatch):
    in_check = lambda i, f: [x3.SHE] if f._wall_check is not None else []   # noqa: E731
    proc = _MaskProc(cells=SHE_CELLS)
    flows, plates = _rig(monkeypatch, proc, gamma=1.0, clahe=2.0, calibrated=False)
    flows._cb_calibrate()
    x3._run(flows, proc, in_check)
    assert flows.last_stop is None and flows._wall_result.clean and len(flows._wall_result.tried) == 1
    proc = _MaskProc(cells=FAR_CELLS)                                        # the mask elsewhere: X3 as before
    flows, plates = _rig(monkeypatch, proc, gamma=1.0, clahe=2.0, calibrated=False)
    flows._cb_calibrate()
    x3._run(flows, proc, in_check)
    assert flows.last_stop is not None and "middle left of the wall" in flows.last_stop
    assert min(flows.enhancer.seen) >= 1.0 - 1e-9 and (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (1.0, 2.0)


# ---- (1) the advice: both ways out, the mask first ---------------------------------------------------------------
def test_the_card_and_the_toast_name_the_mask_first_then_the_roi():
    assert OBJECT_WAYS_OUT == "exclude it (1 Rig > Exclusion Mask) or move the Region of Interest off it"
    chk = EmptyWallCheck(1.2, 2.5, 0.15, frames=10, settle=0, person_frames=0)
    while not chk.done:
        chk.feed([(0.5, 300.0, 400.0, 150.0)])
    s = chk.result.summary()
    assert ("YOLO now and then sees a person on the EMPTY wall near x=300, y=400, even at the weakest setting "
            "(kept): if an object there looks like a person, exclude it (1 Rig > Exclusion Mask) or move the "
            "Region of Interest off it, then Calibrate again.") in s


def test_the_ladders_toast_names_both_ways_out(monkeypatch):
    ghost = (0.13, x3.SHE[1], x3.SHE[2], x3.SHE[3])
    proc = _MaskProc(cells=())
    flows, _p = _rig(monkeypatch, proc)
    flows._cb_calibrate()
    x3._run(flows, proc, lambda i, f: [ghost] if f._wall_check is not None else [])
    toasts = [c[0][0] for c in flows.ui.show_toast.call_args_list]
    assert ("YOLO now and then sees a person on the EMPTY wall, even at the weakest setting: if an object there "
            "looks like a person, exclude it (1 Rig > Exclusion Mask) or move the Region of Interest off it, "
            "then Calibrate again.") in toasts


def test_masked_boxes_counts_what_it_leaves_out_and_nothing_without_a_mask():
    t = MaskedBoxes()
    assert t.keep([(0.5, 1.0, 2.0, 3.0)], None) == [(0.5, 1.0, 2.0, 3.0)] and t.note() == ""
    ok = lambda x, y: x > 50                                                   # noqa: E731
    assert t.keep([(0.2, 10.0, 2.0, 30.0), (0.6, 60.0, 2.0, 30.0), (0.4, 20.0, 5.0, 31.0)], ok) == [
        (0.6, 60.0, 2.0, 30.0)]
    assert (t.frames, t.hit_frames, t.boxes, t.top) == (2, 1, 2, (0.4, 20.0, 5.0, 31.0))
    assert t.note() == ("ignored (Exclusion Mask): 2 box(es) in excluded cells in 1/2 frames, strongest "
                        "conf=0.40 at x=20 y=5 h=31")
