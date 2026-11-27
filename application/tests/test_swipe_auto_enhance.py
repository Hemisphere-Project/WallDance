"""P6 (Plan Cordee): a change of the live enhancement is a change of scene.

What the processor does to the picture before the network pass (``FrameProcessor._sync_gpu_settings``):
enhancement on / lite / forced, its brightness bypass, CLAHE, gamma, greyscale, temporal denoise, background
subtraction in effect and its sensitivity.  The live boxes kept with each frame were made on that picture and a
candidate is inferred on the picture of now: after a change, the automatic swipe's verdicts start over (the
policy's ``reset``) and so does the pool -- the tap's own (P5's collector: ``_swipe_setting_key``) and the
looker's.  (Was the reviewer's ``enhance_probe.py``: look 2 after a change still scored frames kept before it, and
look 1's verdict counted toward « twice in a row »: a switch asked.)  CPU."""
import dataclasses

import numpy as np
import pytest

from test_swipe_auto_rollback import _look, _setup

ENHANCED = (True, False, True, 3.0, 1.6, False, False, 0, 40.0, 0.0)


def test_an_enhancement_change_starts_the_verdicts_and_the_pool_over():
    """Fails before: look 2 runs on frames kept under the old picture, counts look 1's verdict, asks the switch."""
    bg, proc, loader, live, port, tap = _setup(mode="auto")
    assert _look(bg, proc, tap, 150) == "run"
    assert bg.last_advice["why_not_auto"] == "seen 1 time(s), 2 needed" and bg.auto.policy._streak == 1
    live["v"] = dataclasses.replace(live["v"], enhance=ENHANCED)          # Calibrate moved CLAHE and gamma
    assert _look(bg, proc, tap, 300) == "idle"                          # frames kept before the change: never
    assert loader.infers == 150 and bg.switch._want is None and bg.auto.policy._streak == 0
    assert _look(bg, proc, tap, 451) == "run"                           # a whole pool kept after it
    assert bg.last_advice["why_not_auto"] == "seen 1 time(s), 2 needed"  # counted from one again
    assert bg.switch._want is None


def test_the_same_enhancement_keeps_the_streak():
    """Regression guard: nothing changed, look 2 counts look 1 (mode auto asks the switch)."""
    bg, proc, loader, live, port, tap = _setup(mode="auto")
    assert _look(bg, proc, tap, 150) == "run"
    live["v"] = dataclasses.replace(live["v"])
    assert _look(bg, proc, tap, 300) == "run" and bg.last_advice["advice"] == "apply"


# ------------------------------------------------------------------ the tap's key (P5's collector) and the app's
def _change(proc, name):
    s, enh = proc.settings, proc.enhancer
    if name == "clahe_clip":
        enh.clahe_clip = float(enh.clahe_clip) + 0.5
    elif name == "gamma":
        enh.gamma = float(enh.gamma) + 0.2
    elif name in ("brightness_threshold", "bg_subtract_sensitivity"):
        setattr(s, name, int(getattr(s, name)) + 10)
    elif name == "denoise_strength":
        s.denoise_strength = float(s.denoise_strength) + 0.3
    else:
        setattr(s, name, not bool(getattr(s, name)))


PICTURE = ["enhance_enabled", "enhance_lite", "enhance_force", "brightness_threshold", "clahe_clip", "gamma",
           "greyscale", "denoise_strength", "bg_subtract_enabled", "bg_subtract_sensitivity"]


def _app_live(proc):
    import app as appmod
    from types import SimpleNamespace
    fake = SimpleNamespace(
        models=SimpleNamespace(model=object(), _model_loaded=True, _model_loading=False,
                               current_model_name="yolo11x-pose",
                               model_manager=SimpleNamespace(is_using_tensorrt=lambda: True)),
        settings=proc.settings, configs=SimpleNamespace(_current_project="p"),
        roi=SimpleNamespace(state=SimpleNamespace(source_size=(30, 40))), enhancer=proc.enhancer, processor=proc)
    return appmod.WallDanceApp._swipe_live_setting(fake)


@pytest.mark.parametrize("name", PICTURE)
def test_every_setting_that_changes_the_picture_starts_the_tap_over(name):
    """Fails before: the tap's key has no enhancement field -- the frames kept under the old picture stay."""
    from test_swipe_live import _fg, _proc, _tap
    proc, clock = _proc()
    frame = np.dstack([np.full((40, 30), 9, np.uint8)] * 3)
    proc.bg_subtractor.capture_cpu(frame)                               # a background reference: in effect when on
    for i in range(3):
        clock["t"] = float(i)
        _tap(proc, frame, _fg((15, 20, 900)))
    assert len(proc.swipe_snapshot()[0]) == 3
    key = proc._swipe_setting_key()
    _change(proc, name)
    assert proc._swipe_setting_key() != key and proc.swipe_snapshot()[0] == []
    clock["t"] = 3.0
    _tap(proc, frame, _fg((15, 20, 900)))
    assert [f.t for f in proc.swipe_snapshot()[0]] == [3.0]             # starts over from the next frame


@pytest.mark.parametrize("name", PICTURE)
def test_the_looker_sees_the_same_settings_as_the_tap(name):
    """The app's ``LiveSetting.enhance`` (the looker's scene) moves with the same settings as the tap's key.
    Regression guard for all but background subtraction without a reference (not in effect: no change)."""
    pytest.importorskip("dearpygui")
    from test_swipe_live import _proc
    proc, _ = _proc()
    proc.bg_subtractor.capture_cpu(np.dstack([np.full((40, 30), 9, np.uint8)] * 3))
    before = _app_live(proc).enhance
    _change(proc, name)
    assert _app_live(proc).enhance != before


def test_background_subtraction_without_a_reference_changes_nothing():
    """Switched on with no background captured, nothing is subtracted (``GpuPipeline``: ``has_reference``):
    the same picture, the same pool.  Fails before on the app's side only (it read the switch alone)."""
    pytest.importorskip("dearpygui")
    from test_swipe_live import _proc
    proc, _ = _proc()
    key, before = proc._swipe_setting_key(), _app_live(proc).enhance
    proc.settings.bg_subtract_enabled = True
    assert proc._swipe_setting_key() == key and _app_live(proc).enhance == before
