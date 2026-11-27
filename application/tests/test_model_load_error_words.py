"""A failed model load keeps the exception's WORDS only (A2 follow-up, #t-023, 27/11).

``ModelController._load_model_with_progress`` loads in a thread and hands the outcome back in a
dict.  Keeping the exception object there made a cycle: the dict -> the exception -> its
traceback -> the loading thread's frame -> its closure cell -> the dict, and through the
traceback the frames of the failed load and everything they held (the half-built model, its GPU
memory) until a garbage collection (the same cycle core/yolo_runner.py lost in the swipe work).
CPU only: a load that raises, a weakref to an object of the failing frame, the collector OFF."""
from __future__ import annotations

import gc
import time
import weakref
from types import SimpleNamespace

import pytest

WORDS = "engine deserialization failed: CUDA out of memory"


class _HalfBuilt:
    """Stands for the model the failed load was building (and its GPU memory)."""


def _controller(tmp_path, refs, calls):
    pytest.importorskip("ultralytics")
    from runtime.model_controller import ModelController

    ui = SimpleNamespace(
        available=True, render_frame=lambda: None, update_gpu_stats=lambda: None,
        show_model_loading_modal=lambda msg: None, hide_model_loading_modal=lambda: None,
        update_model_loading_progress=lambda msg, p, detail="", animate=False:
            calls.append(("progress", msg, detail)),
        show_toast=lambda msg, duration=0.0, color=None: calls.append(("toast", msg)),
        update_engine_type_badge=lambda trt: None)
    camera = SimpleNamespace(snapshot=lambda: (False, None), close=lambda: None,
                             reopen_and_flush=lambda source: None)
    mc = ModelController(models_dir=str(tmp_path), ui=ui, camera=camera, processor=lambda: None,
                         watchdog=lambda: SimpleNamespace(push_busy=lambda n: None,
                                                          pop_busy=lambda: None),
                         restore_playback_dims=lambda: None, update_topbar=lambda: None)

    def load_model(name, force_pt=False):
        half = _HalfBuilt()                       # held by this frame when the load raises
        refs.append(weakref.ref(half))
        raise RuntimeError(WORDS)

    mc.model_manager.load_model = load_model
    mc.model_manager.set_progress_callback = lambda cb: None
    return mc


@pytest.mark.parametrize("force_pt", [True, False], ids=["pt", "trt_then_pt_fallback"])
def test_a_failed_load_frees_what_it_built_without_a_collection(tmp_path, monkeypatch, capsys,
                                                                force_pt):
    real_sleep = time.sleep
    monkeypatch.setattr(time, "sleep", lambda s: real_sleep(0.001))   # the modal's pacing
    refs, calls = [], []
    mc = _controller(tmp_path, refs, calls)
    gc.collect()
    gc.disable()
    try:
        assert mc._load_model_with_progress("yolo11x-pose", force_pt=force_pt) is False
        assert refs and len(refs) == (1 if force_pt else 2)
        alive = [r() is not None for r in refs]
    finally:
        gc.enable()
    assert alive == [False] * len(refs), "a failed load's frames survive until a collection"
    # the words the operator and the log read are the same as before
    out = capsys.readouterr().out
    assert f"Failed to load model: {WORDS}" in out
    assert ("progress", f"Error: {WORDS}", "Will retry with PyTorch model") in calls
    if not force_pt:
        assert f"Fallback also failed: {WORDS}" in out
    assert mc._model_loading is False and mc._model_loaded is False
