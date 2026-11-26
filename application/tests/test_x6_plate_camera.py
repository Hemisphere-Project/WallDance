"""X6 ② ③ (Plan Cordée rev 2): the plate file stores the camera it was taken at, and a plate from another
exposure or gain is refused with its reason, not scaled."""
import json
from pathlib import Path

import numpy as np
import pytest

from core.foreground import CleanPlate

PROJECTS = Path(__file__).resolve().parents[2] / "projects"


def _plate(**camera):
    return CleanPlate(plate=np.full((100, 50), 120.0, np.float32), sigma=0.5, frame_size=(200, 400), camera=camera)


def test_the_plate_file_keeps_its_camera(tmp_path):
    p = _plate(exposure_us=25000.0, gain_db=30.0, crop=[200, 400], roi=[0, 3, 200, 397])
    q = CleanPlate.load(p.save(tmp_path / "plate.npz"))
    assert q.camera == {"exposure_us": 25000.0, "gain_db": 30.0, "crop": [200, 400], "roi": [0, 3, 200, 397]}
    old = tmp_path / "old.npz"                      # a plate saved before X6
    np.savez_compressed(old, plate=p.plate, sigma=p.sigma, frame_size=np.array(p.frame_size), ds=4, frames=40,
                        created="2026-10-08T20:06:17", source="calibrate:jeudi0809_mur")
    assert CleanPlate.load(old).camera == {}


def test_camera_mismatch_rule():
    p = _plate(exposure_us=25000.0, gain_db=30.0)
    assert "25.0 ms" in p.camera_mismatch(8800.0, 30.0) and "8.8 ms" in p.camera_mismatch(8800.0, 30.0)
    assert p.camera_mismatch(26500.0, 30.0) is None          # within 10 %
    assert "27.5 dB" in p.camera_mismatch(25000.0, 27.5)
    assert p.camera_mismatch(None, None) is None              # auto / unknown: no refusal
    assert _plate().camera_mismatch(8800.0, 27.5) is None    # an old plate: no refusal


def test_the_processor_refuses_a_plate_from_another_exposure():
    pytest.importorskip("torch")
    from core.pipeline import FrameProcessor, ProcessingSettings
    proc = FrameProcessor(model=None, settings=ProcessingSettings(
        confidence=0.3, imgsz=640, use_fp16=False, enhance_enabled=False, enhance_lite=False,
        enhance_force=False, person_height_px=200, use_gpu_path=False))
    proc.set_clean_plate(_plate(exposure_us=25000.0, gain_db=30.0))
    proc._belt_gray = np.full((400, 200), 120, np.uint8)
    proc._belt_gray_offset = (0, 0)
    now = {"v": (8800.0, 30.0)}
    proc.set_camera_state(lambda: now["v"])
    proc._fg_cached = False
    assert proc._fg_frame(200, 400) is None
    assert proc.last_fg.reason == "plate camera" and proc.plate_status == "stale"
    assert "25.0 ms" in proc.plate_refusal
    now["v"] = (25000.0, 30.0)
    proc._fg_cached = False
    assert proc._fg_frame(200, 400) is not None and proc.plate_refusal is None


@pytest.mark.skipif(not (PROJECTS / "jeudi0809_mur" / "plates" / "plate_20261008_200617.npz").exists(),
                    reason="the 08/10 projects are not on this machine")
def test_gate_mur_2_to_4_refuse_the_20_06_plate():
    """The 20:06 plate was taken by Calibrate at 25 ms / 30 dB (devlogs/walldance_20261008_195435.log:2223); the
    plate file predates X6, so it is stamped here as Calibrate took it.  mur 2-4 ran at 8.8-12 ms."""
    plate = CleanPlate.load(PROJECTS / "jeudi0809_mur" / "plates" / "plate_20261008_200617.npz")
    plate.camera = {"exposure_us": 25000.0, "gain_db": 30.0}
    for take in ("slot_2_20261008_200907", "slot_3_20261008_201808", "slot_4_20261008_205648"):
        cam = json.loads((PROJECTS / "jeudi0809_mur" / "recordings" / f"{take}.avi.meta").read_text())["camera"]
        why = plate.camera_mismatch(cam["nodes"]["ExposureTime"], cam.get("gain_db"))
        assert why and "25.0 ms" in why, take
