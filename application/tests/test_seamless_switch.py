"""P6 (Plan Cordee): the seamless switch -- the operator's Apply without a reload -- on CPU with fakes.

* the tracker's space pinned across a size switch (``FrameProcessor.adopt_model``): the network's detections land
  where the old input would have put them, a failed first frame puts model, input and pin back;
* the model controller's bookkeeping follows the switch and comes back after a failure;
* ``SeamlessSwitch``: loaded beside the live model on its worker thread, swapped between two frames by the loop,
  never live when it does not load, would run PyTorch or raises (then THE fault, with what runs and what to do).
"""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from core.swipe import Candidate

X1280 = Candidate("yolo11x-pose", 1280)
L1280 = Candidate("yolo11l-pose", 1280)
L1920 = Candidate("yolo11l-pose", 1280, (1920, 1056))     # (height, width), the mur2 rectangle
ROI = {"enabled": True, "x": 135, "y": 3, "w": 965, "h": 1771}


def _live(cand=X1280, model_id=1, trt=True, project="jeudi0809_mur2"):
    from runtime.swipe_controller import LiveSetting
    return LiveSetting(project, cand, model_id, trt, roi=(True, 135, 3, 965, 1771), plate=(1, "t"), plate_ok=True,
                       frame=(1260, 1824), enhance=(False, False, True, 2.5, 1.2), exclusion=((10, 16), ()))


class _Runner:
    """A PoseRunner stand-in: follows its model, raises when told to."""

    def __init__(self, model, fail=False):
        self.model, self.fail, self.calls = model, fail, []

    def __call__(self, tensor, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("the engine raised on its first frame")
        return [f"results of {self.model}"]


class _Tracker:
    def __init__(self, tracks=("t1", "t2"), frame_count=50):
        self.tracks, self._dormant, self.frame_count = list(tracks), [], frame_count

    def reset(self):
        pytest.fail("the tracker must not be reset by a switch")


def _proc(model="x1280", runner=None, shape=None):
    """A FrameProcessor with only what the switch touches (no GPU, no camera)."""
    from core.pipeline import FrameProcessor, ProcessingSettings
    p = FrameProcessor.__new__(FrameProcessor)
    p.settings = ProcessingSettings(confidence=0.15, imgsz=1280, use_fp16=True, yolo_shape=shape,
                                    enhance_enabled=False, enhance_lite=False, enhance_force=False,
                                    person_height_px=150)
    p.model = model
    p._yolo_runner = runner or _Runner(model)
    p._swap_probation = None
    p._tracker_input = None
    p._frame_tracker_input = None
    p._last_box_confs = {}
    p.tracker = _Tracker()
    return p


# ----------------------------------------------------------------------------------------------------------
# The tracker's space across a size switch
# ----------------------------------------------------------------------------------------------------------
def test_the_tracker_letterbox_is_the_gpu_pipelines_own_arithmetic():
    torch = pytest.importorskip("torch")
    from core.gpu_pipeline import GpuPipeline, GpuFrame
    from core.pipeline import FrameProcessor
    t = torch.zeros((1, 3, ROI["h"], ROI["w"]))
    for imgsz, shape in ((1280, None), (1280, (1920, 1056)), (960, None)):
        _, lb = GpuPipeline._prepare_yolo_input(None, GpuFrame(t, is_bgr=False), imgsz, shape)
        assert FrameProcessor._letterbox_for((imgsz, shape), ROI) == lb


def test_detections_of_the_new_input_land_where_the_old_input_puts_them():
    """x @ 1280 -> l @ 1920 x 1056: a dancer's keypoints and box seen by the new network, converted into the
    tracker's (old) space, are where the old network's letterbox puts the same ROI point -- and back to the same
    camera pixel through the tracker's letterbox."""
    from core.pipeline import FrameProcessor
    p = _proc()
    old_lb = FrameProcessor._letterbox_for((1280, None), ROI)
    new_lb = FrameProcessor._letterbox_for((1280, (1920, 1056)), ROI)
    roi_pts = np.array([[100.0, 200.0], [480.5, 900.25], [964.0, 1770.0]])

    def letterbox(pts, lb):
        return pts * lb["scale"] + np.array([lb["pad_x"], lb["pad_y"]])
    kpts = letterbox(roi_pts, new_lb).astype(np.float32)
    x0, y0 = letterbox(np.array([[300.0, 400.0]]), new_lb)[0]
    bbox = np.array([x0, y0, 120.0 * new_lb["scale"], 520.0 * new_lb["scale"]], dtype=np.float32)
    p._last_box_confs = {FrameProcessor._bbox_conf_key(bbox): 0.61}
    (k, conf, b), = p._to_tracker_space([(kpts, np.ones(3), bbox)], new_lb, old_lb)
    assert k.dtype == np.float32 and b.dtype == np.float32
    np.testing.assert_allclose(k, letterbox(roi_pts, old_lb), atol=1e-3)
    np.testing.assert_allclose(b[:2], letterbox(np.array([[300.0, 400.0]]), old_lb)[0], atol=1e-3)
    np.testing.assert_allclose(b[2:], [120.0 * old_lb["scale"], 520.0 * old_lb["scale"]], atol=1e-3)
    back = (k - np.array([old_lb["pad_x"], old_lb["pad_y"]])) / old_lb["scale"]
    np.testing.assert_allclose(back, roi_pts, atol=1e-2)               # the same camera pixel
    assert p._last_box_confs == {FrameProcessor._bbox_conf_key(b): 0.61}   # the box keeps its confidence


def test_a_size_switch_pins_the_tracker_and_the_way_back_unpins_it():
    p = _proc()
    new = _Runner("l1920")
    old = p.adopt_model("l1920", new, net_input=(1280, (1920, 1056)))
    assert old[0] == "x1280" and p.model == "l1920"
    assert (p.settings.imgsz, p.settings.yolo_shape) == (1280, (1920, 1056))      # the next frame's tensor
    assert p.tracker_input == (1280, None)                                         # the tracker stays
    assert p._tracker_letterbox(ROI)["target_w"] == 1280
    for _ in range(3):
        assert p._run_yolo(None) == ["results of l1920"]
    assert p._swap_probation is None and p.tracker_input == (1280, None)
    p.adopt_model("x1280", _Runner("x1280"), net_input=(1280, None))               # back at the tracker's input
    assert p.tracker_input == (1280, None) and p._tracker_letterbox(ROI) is None
    assert p.tracker_input is None                                                 # nothing to convert any more


def test_a_reset_tracker_follows_the_network_again():
    p = _proc()
    p.adopt_model("l1920", _Runner("l1920"), net_input=(1280, (1920, 1056)))
    assert p._tracker_letterbox(ROI) is not None
    p.tracker.tracks, p.tracker.frame_count = [], 0                                 # Reset tracker / a new take
    assert p._tracker_letterbox(ROI) is None and p.tracker_input is None


def test_a_same_input_switch_converts_nothing():
    p = _proc()
    p.adopt_model("l1280", _Runner("l1280"), net_input=(1280, None))
    assert p.tracker_input is None and p._swap_probation["old_input"] is None


def test_a_model_raising_on_its_first_frame_gives_the_frame_back_to_the_old_one():
    """Same input: the old model takes that very frame; the tracker is not touched; the handler gets the
    failed pair to free it where it is safe."""
    old_runner = _Runner("x1280")
    p = _proc(runner=old_runner)
    tracker = p.tracker
    failed = []
    p.adopt_model("l1280", _Runner("l1280", fail=True), net_input=(1280, None),
                  on_fail=lambda exc, pair: failed.append((str(exc), pair[0])),
                  on_ok=lambda: pytest.fail("not ok"))
    assert p._run_yolo(None) == ["results of x1280"]                      # this frame, by the old model
    assert p.model == "x1280" and p._yolo_runner is old_runner and p._swap_probation is None
    assert failed == [("the engine raised on its first frame", "l1280")]
    assert p.tracker is tracker and tracker.tracks == ["t1", "t2"]
    assert p._run_yolo(None) == ["results of x1280"]


def test_a_failed_size_switch_puts_model_input_and_pin_back():
    """Another input: that frame's tensor is the new size -- no detection for it, the tracker stays in its own
    space for it (``_frame_tracker_input``), and the next frame is the old input's."""
    p = _proc()
    failed = []
    p.adopt_model("l1920", _Runner("l1920", fail=True), net_input=(1280, (1920, 1056)),
                  on_fail=lambda exc, pair: failed.append(pair[0]))
    assert p._run_yolo(None) == []
    assert (p.model, p.settings.imgsz, p.settings.yolo_shape) == ("x1280", 1280, None)
    assert p.tracker_input is None and p._frame_tracker_input == (1280, None) and failed == ["l1920"]
    assert p.tracker.tracks == ["t1", "t2"]


def test_the_switch_is_undone_when_the_bookkeeping_fails():
    p = _proc()
    p.adopt_model("l1920", _Runner("l1920"), net_input=(1280, (1920, 1056)))
    p.revert_swap()
    assert (p.model, p.settings.yolo_shape, p.tracker_input, p._swap_probation) == ("x1280", None, None, None)


# ----------------------------------------------------------------------------------------------------------
# The model controller's side (the combo, the badge, what Save writes)
# ----------------------------------------------------------------------------------------------------------
def _controller(proc):
    from runtime.model_controller import ModelController
    mc = ModelController.__new__(ModelController)
    mc.processor = lambda: proc
    mm = SimpleNamespace(imgsz=1280, shape=None, _loaded_shape=None, is_using_tensorrt=lambda: True)
    mm.set_imgsz = lambda v: setattr(mm, "imgsz", v)
    mm.set_shape = lambda v: setattr(mm, "shape", tuple(v) if v else None)
    mc.model_manager = mm
    mc.ui = SimpleNamespace(available=False)
    mc.model, mc.current_model_name, mc.current_model = "x1280", "yolo11x-pose", "yolo11x-pose.engine"
    return mc


def test_the_bookkeeping_follows_a_switch_and_comes_back_after_a_failure():
    p = _proc()
    mc = _controller(p)
    mc.swap_live("l1920", _Runner("l1920", fail=True), L1920)
    mm = mc.model_manager
    assert (mc.current_model_name, mm.imgsz, mm.shape, mm._loaded_shape) == ("yolo11l-pose", 1280, (1920, 1056),
                                                                              (1920, 1056))
    p._run_yolo(None)                                                     # its first frame raises
    mc.restore_live(X1280)
    assert (mc.model, mc.current_model_name, mm.shape, mm._loaded_shape) == ("x1280", "yolo11x-pose", None, None)


# ----------------------------------------------------------------------------------------------------------
# The switch: load beside, swap between two frames
# ----------------------------------------------------------------------------------------------------------
class _BgProc:
    def __init__(self):
        from core.pipeline import ProcessingSettings
        self.settings = ProcessingSettings(confidence=0.15, imgsz=1280, use_fp16=True, enhance_enabled=False,
                                           enhance_lite=False, enhance_force=False, person_height_px=150)
        self.frame_done = threading.Event()
        self.frame_done_t = 0.0
        self.model = "x1280"
        self._swipe_key = "k"
        self.timing = {}
        self.clock = 100.0

    def _swipe_setting_key(self):
        return "k"

    def loop_clock(self):
        return self.clock

    def live_infer_args(self):
        return dict(imgsz=1280, conf=0.15, iou=0.7, half=True)


class _Port:
    def __init__(self, live_pair=("x1280", "x1280-runner")):
        self.calls, self.live_pair, self.hooks = [], live_pair, None

    def swap_live(self, model, runner, cand, on_fail=None, on_ok=None):
        self.calls.append(("swap", model, cand))
        self.hooks = (on_fail, on_ok)
        old, self.live_pair = self.live_pair, (model, runner)
        return old

    def restore_live(self, old_cand=None):
        self.calls.append(("restore", old_cand))


class _Loader:
    def __init__(self, fail=None, fmt="engine"):
        self.loads, self.fail, self.fmt = [], fail, fmt

    def __call__(self, proc):
        return self

    def load(self, path, cand, model=None):
        self.loads.append(path)
        if self.fail:
            raise RuntimeError(self.fail)
        m = SimpleNamespace(name=cand.key, predictor=SimpleNamespace(model=SimpleNamespace(format=self.fmt)))
        return m, _Runner(m), 0.4


def _sw(live, *, loader=None, port=None, engine_exists=lambda p: True, size_change=True):
    pytest.importorskip("torch")
    from runtime.seamless_switch import SeamlessSwitch
    events = []
    proc = _BgProc()
    sw = SeamlessSwitch(processor=lambda: proc, live=lambda: live["v"], engine_path=lambda c: c.engine_file,
                        port=port or _Port(), notify=lambda k, p: events.append((k, p)),
                        runner_factory=loader or _Loader(), engine_exists=engine_exists,
                        engine_lock=threading.RLock(), size_change=size_change)
    sw.start = lambda: None                              # the test drives the worker by hand (``service``)
    return sw, proc, events


def test_an_apply_is_seamless_only_with_a_built_engine_on_tensorrt():
    live = {"v": _live()}
    sw, _, _ = _sw(live, engine_exists=lambda p: p != L1920.engine_file)
    assert sw.can_switch(L1280) is None
    assert sw.can_switch(L1920) == f"its engine is not built on this machine ({L1920.engine_file})"
    live["v"] = _live(trt=False)
    assert sw.can_switch(L1280) == "the live model runs PyTorch"
    live["v"] = None
    assert sw.can_switch(L1280) == "a model load is under way"


def test_another_input_size_is_the_standby_reload_until_a_gate_passes():
    """Gate (a), 26/11: switched to another input size between two frames, a dancer's point stood still and then
    stepped beyond the take's own p99 -- another input size is the STANDBY reload; the same input is seamless."""
    from runtime import seamless_switch
    assert seamless_switch.SIZE_CHANGE_SEAMLESS is False
    live = {"v": _live()}
    sw, _, _ = _sw(live, size_change=seamless_switch.SIZE_CHANGE_SEAMLESS)
    assert sw.can_switch(L1280) is None
    assert sw.can_switch(L1920) == "another input size"
    assert sw.request(L1920, "Apply") == "another input size" and sw.state == "idle"
    live["v"] = _live(cand=L1920, model_id=2)            # back from 1920 x 1056 to 1280: another size too
    assert sw.can_switch(X1280) == "another input size"


def test_the_operator_apply_loads_beside_and_switches_between_two_frames():
    live = {"v": _live()}
    loader, port = _Loader(), _Port()
    sw, proc, events = _sw(live, loader=loader, port=port)
    done = []
    assert sw.request(L1920, "switching to l @ 1920 x 1056 (Apply)",
                             on_done=lambda ok, text: done.append((ok, text))) is None
    assert sw.state == "loading" and sw.request(L1920, "again") == "a switch is already under way"
    sw.service()                                            # the worker: load + warm at the winner's input
    assert loader.loads == [L1920.engine_file] and sw._request is not None and port.calls == []
    model = sw._request["prepared"].model
    assert sw._request["prepared"].runner.calls[-1]["imgsz"] == (1920, 1056)     # warmed at its own input
    assert sw.drain() is True                       # the loop, between two frames
    assert port.calls == [("swap", model, L1920)] and sw._probation
    assert sw._drop == [("x1280", "x1280-runner")]       # another input: the old live model goes
    port.hooks[1]()                                      # its first frames ran
    assert done == [(True, done[0][1])] and done[0][1].startswith("switched to l @ 1920 x 1056 at ")
    assert sw.fault is None and sw.state == "idle"
    live["v"] = _live(cand=L1920, model_id=2)
    sw.service()                                            # after a live frame of the new model: freed
    assert sw._drop == []
    assert [e for e, _ in events].count("switched") == 1


def test_an_engine_that_does_not_load_never_becomes_live(capsys):
    """An engine file TensorRT cannot read: plain words for the operator, the raw error in the log."""
    live = {"v": _live()}
    port = _Port()
    sw, _, events = _sw(live, loader=_Loader(fail="Serialization assertion failed"), port=port)
    done = []
    sw.request(L1920, "Apply", on_done=lambda ok, text: done.append((ok, text)))
    sw.service()
    assert port.calls == [] and sw._request is None
    text = ("the switch to l @ 1920 x 1056 failed before it began (the engine file could not be read): "
            f"x @ 1280 still runs - rebuild {L1920.engine_file} (extra/build_engines) before trying it again")
    assert sw.fault == text and sw.health() == text and done == [(False, text)]
    assert ("fault", {"text": text}) in events
    assert "RuntimeError: Serialization assertion failed" in capsys.readouterr().out      # the log keeps it
    sw.dismiss()                                         # the operator's acknowledgement
    assert sw.health() is None


def test_only_an_unreadable_engine_file_gets_the_plain_words():
    """mgr-ran's TensorRT on a truncated engine: deserialize returns None and ultralytics fails on it (d_broken,
    26/11) -- the engine file could not be read; any other load error is said as it is."""
    from runtime.seamless_switch import ENGINE_UNREADABLE, load_failure
    none = AttributeError("'NoneType' object has no attribute 'create_execution_context'")
    assert load_failure(none) == ENGINE_UNREADABLE == "the engine file could not be read"
    assert load_failure(RuntimeError("IRuntime::deserializeCudaEngine failed")) == ENGINE_UNREADABLE
    assert load_failure(RuntimeError("CUDA out of memory")) == "an error, written in the log"
    assert load_failure(AttributeError("'X' object has no attribute 'y'")) == \
        "an error, written in the log"


def test_an_engine_that_would_run_pytorch_never_becomes_live():
    live = {"v": _live()}
    port = _Port()
    sw, _, _ = _sw(live, loader=_Loader(fmt="pt"), port=port)
    sw.request(L1280, "Apply")
    sw.service()
    assert port.calls == [] and "it would run PyTorch, not its TensorRT engine" in sw.fault
    assert "x @ 1280 still runs" in sw.fault


def test_a_switch_that_raises_on_its_first_frame_is_the_fault_and_the_old_model_runs():
    """The real processor and model controller: the new engine raises on its first live frame -- the old model
    takes the frame back, the tracker is untouched, the bookkeeping comes back, the fault says what runs."""
    p = _proc(runner=_Runner("x1280"))
    mc = _controller(p)
    live = {"v": _live()}
    loader = _Loader()
    sw, _, _ = _sw(live, loader=loader, port=mc)
    done = []
    sw.request(L1920, "Apply", on_done=lambda ok, text: done.append(ok))
    sw.service()
    sw._request["prepared"].runner.fail = True          # its first live frame raises
    assert sw.drain()
    assert p._run_yolo(None) == []                        # that frame: no detection, the old input next
    assert p.model == "x1280" and (p.settings.yolo_shape, mc.current_model_name) == (None, "yolo11x-pose")
    assert p.tracker.tracks == ["t1", "t2"] and done == [False]
    assert sw.fault.startswith("the switch to l @ 1920 x 1056 failed (its first live frames failed): x @ 1280 "
                               "still runs - keep it for now")
    assert p._run_yolo(None) == ["results of x1280"]


class _Engine:
    """Stands for an engine's GPU memory: freed when this object goes."""


class _TrtModel:
    """An ultralytics model as PoseRunner sees it (its predictor's fast path, the regular call), whose engine
    raises once ``fail`` is set -- in the fast path, then in the regular call, as a TensorRT failure does."""

    def __init__(self):
        flag = self.flag = SimpleNamespace(fail=False)
        self.engine = _Engine()

        def inference(x):
            if flag.fail:
                raise RuntimeError("TensorRT inference execution failed")
            return x
        self.predictor = SimpleNamespace(
            args=SimpleNamespace(conf=None, iou=None, visualize=False, augment=False, embed=None, save=False,
                                 save_txt=False, save_crop=False, show=False),
            source_type=SimpleNamespace(tensor=True), batch=(["t"],), done_warmup=True, _lock=threading.Lock(),
            model=SimpleNamespace(format="engine"), preprocess=lambda x: x, inference=inference,
            postprocess=lambda preds, x, orig: ["results"])

    def __call__(self, tensor, *, imgsz, conf, iou, half, verbose):
        self.predictor.args.conf, self.predictor.args.iou = conf, iou
        if self.flag.fail:
            raise RuntimeError("TensorRT inference execution failed")
        return ["results"]


def test_an_engine_dropped_after_a_rollback_goes_without_the_cyclic_collector():
    """The rehearsal build holds the full collections during a show: an engine the switch drops must go by
    reference counting alone, or its GPU memory stays held and the next load stacks a second one.  The real
    PoseRunner, warmed by the switch with the live call's arguments (its first live frame on the fast path); its
    engine raises there, then in the regular call; the rollback; the worker frees it after a live frame."""
    import gc
    import weakref
    torch = pytest.importorskip("torch")
    from core.yolo_runner import PoseRunner
    p = _proc(runner=_Runner("x1280"))
    made = []

    class _PoseLoader(_Loader):
        def load(self, path, cand, model=None):
            m = _TrtModel()
            made.append(weakref.ref(m.engine))
            return m, PoseRunner(m), 0.4
    live = {"v": _live()}
    sw, bgproc, _ = _sw(live, loader=_PoseLoader(), port=_controller(p))
    bgproc.live_infer_args = p.live_infer_args            # the processor's own: the fast path's key
    gc.collect()
    gc.disable()
    try:
        sw.request(L1280, "Apply")
        sw.service()                                       # loaded beside, warmed (the regular call)
        sw._request["prepared"].model.flag.fail = True
        assert sw.drain()
        assert p._run_yolo(torch.full((1, 3, 64, 64), 0.5)) == ["results of x1280"]    # rolled back
        assert sw.fault and len(sw._drop) == 1 and made[0]() is not None
        sw.service()                                       # freed by the worker, right after a live frame
        assert not sw._drop
        assert made[0]() is None, "the dropped engine waits for the cyclic collector"
    finally:
        gc.enable()


def test_the_switch_waits_for_a_background_inference_in_flight():
    live = {"v": _live()}
    port = _Port()
    sw, _, _ = _sw(live, port=port)
    sw.request(L1280, "Apply")
    sw.service()
    with sw.guard:                              # another thread is inferring on the prepared model
        assert sw.drain() is False and sw._request is not None
    assert sw.drain() is True and len(port.calls) == 1


def test_a_setting_changed_meanwhile_abandons_the_switch():
    live = {"v": _live()}
    port = _Port()
    sw, _, events = _sw(live, port=port)
    done = []
    sw.request(L1920, "Apply", on_done=lambda ok, text: done.append((ok, text)))
    sw.service()
    live["v"] = _live(model_id=9)                        # a reload happened in between
    assert sw.drain() is True and port.calls == []
    assert done == [(False, "not switched to l @ 1920 x 1056: the setting changed meanwhile")]
    assert sw.fault is None                              # not a failure: nothing was tried


def test_a_failed_seamless_switch_is_a_warn_row():
    """The previous setting never stopped (the new engine never became live): a notice to acknowledge, not a show
    that is not ready -- ``warn``, the fault's own sentence; none once acknowledged."""
    from runtime.swipe_controller import SwipeController
    c = SwipeController(processor=lambda: None, live=lambda: None, engine_path=str, plate_ready=lambda: True)
    text = ("the switch to l @ 1280 failed (RuntimeError: x): x @ 1280 still runs - rebuild "
            "yolo11l-pose_1280.engine (extra/build_engines) before trying it again")
    row = c.readiness(plate_ready=True, live_now=None, other_fault=text)
    assert row.status == "warn" and row.detail == text
    assert c.readiness(plate_ready=True, live_now=None, other_fault=None) is None
