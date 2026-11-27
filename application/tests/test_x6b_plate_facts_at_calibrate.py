"""X6 follow-up (#t-027, 27/11): the empty-wall picture knows its exposure from the moment Calibrate takes it.

Since A6 ② the plate a Calibrate takes is held, unwritten, until « Save to project », and the camera facts X6 ②
stores in it (plate.camera: exposure_us, gain_db, crop, roi) were stamped only by the app's writer, at Save.
So from the end of Calibrate to Save the plate in use said nothing of its camera, and the readiness row
« plate exposure » that runs by itself after Calibrate told the technician « Calibrate on the empty wall ».

Through the real Calibrate flow (the X2b harness: runtime/calibration_flows.py, the real FrameProcessor plate
capture, the app's own capture callback WallDanceApp._cb_capture_plate), the app's real readiness rows
(WallDanceApp._readiness_setup_rows), on a played take and on the camera path (a fake camera that reports an
exposure and a gain):

- right after Calibrate, BEFORE Save, the row is ok and names the exposure and gain the camera had when the
  plate was taken; after Save it reads the same, and the file holds the same facts;
- a camera that moved after Calibrate is refused with both values, by the row and by RUN, before and after Save;
- a cancelled Calibrate restores the previous plate with its previous facts (none for a pre-X6 file: it still
  warns, honestly);
- the minute-save (A2's restart path) writes the facts too.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from test_x2b_card_wall_light import H, ROI, W, _rig   # the X2b harness: the real Calibrate flow

OLD_CREATED = "2026-11-20T21:36:18"     # the project's plate before this Calibrate


def _setup(tmp_path, *, playing: bool, wall_check: bool = False):
    """X2b's rig, plus what the app's readiness rows read; one settings object for the app, the flows and
    the processor, as app.py builds them (WallDanceApp.settings is the FrameProcessor's ProcessingSettings)."""
    r = _rig(tmp_path, playing=playing, kind="flat", wall_check=wall_check)
    s = r.proc.settings
    for k in ("roi_enabled", "roi_x", "roi_y", "roi_w", "roi_h", "yolo_shape"):
        setattr(s, k, getattr(r.app.settings, k))
    s.fg_plate_path = ""
    r.app.settings = r.flows.settings = s
    app = r.app
    app.camera = SimpleNamespace(state=SimpleNamespace(is_open=r.camera_path))
    app.recorder = r.flows.recorder
    app.cameras._camera_reconnecting = False
    app.roi = SimpleNamespace(state=SimpleNamespace(source_size=(W, H), stored_roi=lambda: ROI))
    app.enhancer = r.flows.enhancer
    app.timing = {}
    r.proc.set_camera_state(lambda: app.cameras.exposure_gain_now())     # app.py: RUN's per-frame refusal
    return r


def _project_plate(r, tmp_path, **camera):
    """The project's plates/latest.npz, loaded as at project open; ``camera`` empty = a file saved before X6
    (no camera key at all)."""
    from core.foreground import CleanPlate
    d = tmp_path / "proj" / "plates"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "latest.npz"
    pixels = np.full((H // 4, W // 4), 60.0, np.float32)
    if camera:
        CleanPlate(plate=pixels, sigma=0.5, frame_size=(W, H), frames=40, created=OLD_CREATED,
                   source="calibrate:proj", camera=dict(camera)).save(path)
    else:
        np.savez_compressed(path, plate=pixels, sigma=0.5, frame_size=np.array((W, H)), ds=4, frames=40,
                            created=OLD_CREATED, source="calibrate:proj")
    r.app.settings.fg_plate_path = str(path)
    r.proc._fg_loaded_path = None
    assert r.proc._ensure_plate() is not None and r.proc._fg_detector.plate.created == OLD_CREATED
    assert r.proc._fg_detector.plate.camera == dict(camera)
    return path


def _calibrate(r, frames=800, cancel_when_held=False):
    """CALIBRATE pressed, then frames (the processor feeds its plate capture, then Calibrate steps) until
    Calibrate has ended and its plate is taken.  Returns the camera's (exposure_us, gain_db) on the frame the
    plate capture ended: the facts the plate must carry.  ``cancel_when_held``: CALIBRATE pressed again (A6's
    cancel) on the first frame Calibrate still runs with its plate taken and held."""
    r.flows._cb_calibrate()
    at = None
    for _ in range(frames):
        capturing = r.proc._plate_capture is not None
        r.proc._feed_plate_capture(r.frame)
        if capturing and r.proc._plate_capture is None:
            at = r.app.cameras.exposure_gain_now()
        if cancel_when_held and r.flows._calibrating and r.flows._pending_plate is not None:
            r.held_camera = dict(r.proc._fg_detector.plate.camera)      # the plate in use as it is cancelled
            r.flows._cb_calibrate()
            break
        if (not r.flows._calibrating and r.flows._scene_pending is None and r.flows._emptiness is None
                and r.proc._plate_capture is None):
            break
        r.flows._step_calibration([], 30.0)
    assert not r.flows._calibrating and at is not None
    return at


def _row(r):
    """The « plate exposure » row as the app's readiness report builds it."""
    import app as app_mod
    (row,) = [x for x in app_mod.WallDanceApp._readiness_setup_rows(r.app) if x.name == "plate exposure"]
    return row


def _taken(exp, gain):
    return f"taken at {exp / 1000:.1f} ms / {gain:.1f} dB on {W}x{H}"


def _facts(exp, gain):
    return {"exposure_us": exp, "gain_db": gain, "crop": [W, H], "roi": list(ROI)}


def _held(r):
    """This Calibrate's plate is the one in use, held until Save (A6 ②): nothing written to the project."""
    pending = r.flows._pending_plate
    return pending is not None and pending[0] is r.proc._fg_detector.plate


# ------------------------------------------------------------------ the row right after Calibrate, before Save

@pytest.mark.parametrize("playing", [True, False], ids=["take", "camera"])
def test_right_after_calibrate_before_save_the_row_names_the_camera_the_plate_was_taken_at(tmp_path, playing):
    from core.foreground import CleanPlate
    r = _setup(tmp_path, playing=playing)
    latest = _project_plate(r, tmp_path)                      # a plate saved before X6: no camera facts
    exp, gain = _calibrate(r)
    assert exp and gain is not None, "the fake camera reports an exposure and a gain"
    assert _held(r) and CleanPlate.load(latest).created == OLD_CREATED        # before Save
    before = _row(r)
    assert before.status == "ok", f"right after Calibrate, before Save: {before.status} {before.detail}"
    assert _taken(exp, gain) in before.detail and before.detail.endswith("as the camera now")
    r.flows._save_calibration()                               # the result dialog's « Save to project »
    assert r.flows._pending_plate is None
    saved = CleanPlate.load(latest)
    assert saved.created != OLD_CREATED and saved.camera == _facts(exp, gain) == r.proc._fg_detector.plate.camera
    after = _row(r)
    assert (after.status, after.detail) == (before.status, before.detail)
    print(f"[X6b] {r.flows.recorder.is_playing and 'take' or 'camera'}: before Save: {before.status} {before.detail}")
    print(f"[X6b] {r.flows.recorder.is_playing and 'take' or 'camera'}: after Save:  {after.status} {after.detail}")


@pytest.mark.parametrize("playing", [True, False], ids=["take", "camera"])
def test_a_plate_taken_at_another_exposure_or_gain_than_the_cameras_is_refused_with_both_values(tmp_path, playing):
    from core.foreground import CleanPlate
    r = _setup(tmp_path, playing=playing)
    latest = _project_plate(r, tmp_path)
    exp, gain = _calibrate(r)
    cams = r.app.cameras
    r.proc._belt_gray, r.proc._belt_gray_offset = np.full((H, W), 70, np.uint8), (0, 0)

    def run_refusal():
        r.proc._fg_cached = False
        return r.proc._fg_frame(W, H) is None, r.proc.plate_refusal

    for save in (False, True):
        assert _held(r) != save
        cams.ids_exposure_us = exp * 0.4                      # the camera moved since (not by hand: no X5)
        row = _row(r)
        assert row.status == "fail", f"{row.status} {row.detail}"
        both = f"plate taken at {exp / 1000:.1f} ms, the camera is at {exp * 0.4 / 1000:.1f} ms"
        assert row.detail.startswith(both) and "RUN refuses it" in row.detail
        refused, why = run_refusal()
        assert refused and why and why.startswith(both)
        print(f"[X6b] {'take' if playing else 'camera'}: {'after' if save else 'before'} Save, camera moved: "
              f"{row.status} {row.detail} | RUN: {why}")
        cams.ids_exposure_us, cams.ids_gain_db = exp, gain + 3.0
        row = _row(r)
        assert row.status == "fail" and row.detail.startswith(
            f"plate taken at {gain:.1f} dB, the camera is at {gain + 3.0:.1f} dB"), row.detail
        cams.ids_gain_db = gain
        assert _row(r).status == "ok" and run_refusal() == (False, None)
        r.app.roi.state.source_size = (W + 20, H)            # another crop: refused by its size, as before X6b
        row = _row(r)
        assert row.status == "fail" and row.detail.startswith(f"taken on a {W}x{H} picture, the camera gives "
                                                              f"{W + 20}x{H}"), row.detail
        r.app.roi.state.source_size = (W, H)
        if not save:                                          # Save pressed while the camera is elsewhere:
            cams.ids_exposure_us = exp * 0.4                  # the file keeps the camera the plate was taken at
            r.flows._save_calibration()
            cams.ids_exposure_us = exp
            assert CleanPlate.load(latest).camera == _facts(exp, gain)


# ------------------------------------------------------------------ a cancelled Calibrate, a pre-X6 plate

@pytest.mark.parametrize("facts", [True, False], ids=["with-facts", "pre-X6"])
@pytest.mark.parametrize("playing", [True, False], ids=["take", "camera"])
def test_a_cancelled_calibrate_restores_the_previous_plate_with_its_previous_facts(tmp_path, playing, facts):
    r = _setup(tmp_path, playing=playing, wall_check=True)
    e0, g0 = r.app.cameras.exposure_gain_now()                # the camera as the project left it
    previous = dict(_facts(e0, g0), roi=[0, 0, W, H]) if facts else {}   # taken with another zone drawn
    _project_plate(r, tmp_path, **previous)
    first = _row(r)
    if facts:
        assert first.status == "ok" and _taken(e0, g0) in first.detail, first.detail
    else:                                                     # an honest warning, as before X6b
        assert first.status == "warn" and first.detail.endswith(
            "the snapshot does not say at which exposure and gain it was taken - Calibrate on the empty wall")
        assert "taken at exposure ? / gain ?" in first.detail
    exp, gain = _calibrate(r, cancel_when_held=True)          # cancelled with its plate taken and in use
    assert r.held_camera == _facts(exp, gain)                 # ... which knew its camera
    assert r.flows._pending_plate is None and r.app.cameras.exposure_gain_now() == (e0, g0)   # A6 ②
    assert r.proc._ensure_plate() is not None                 # the next frame: the project's plate again
    plate = r.proc._fg_detector.plate
    assert plate.created == OLD_CREATED and plate.camera == previous
    assert (_row(r).status, _row(r).detail) == (first.status, first.detail)
    print(f"[X6b] {'take' if playing else 'camera'}, previous plate {'with facts' if facts else 'pre-X6'}: "
          f"held {r.held_camera} -> cancelled: {first.status} {first.detail}")


# ------------------------------------------------------------------ A2's restart path

def test_the_minute_save_of_a_calibrate_plate_carries_its_facts_and_resumes_with_them(tmp_path):
    """The minute-save writes the plate in use beside the project's latest.npz; after Save it is the one a
    relaunch resumes (core/foreground.resumable_live_plate)."""
    import app as app_mod
    from core.foreground import LIVE_PLATE_FILE, resumable_live_plate
    r = _setup(tmp_path, playing=False)
    latest = _project_plate(r, tmp_path)
    exp, gain = _calibrate(r)
    assert _held(r)
    live = app_mod.WallDanceApp._save_live_plate(r.app, wait=True)
    assert live == str(latest.parent / LIVE_PLATE_FILE)
    r.flows._save_calibration()
    assert app_mod.WallDanceApp._save_live_plate(r.app, wait=True) is None   # the live plate has not moved
    resumed = resumable_live_plate(live, latest)
    assert resumed is not None and resumed.camera == _facts(exp, gain)
