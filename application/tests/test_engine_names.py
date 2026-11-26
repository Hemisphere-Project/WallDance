"""P2 (Plan Cordée rev 2): extra/measure_engine_fps.py times rectangle engines too, and keeps them where
calib2's fps table loader cannot trip on them."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location("measure_engine_fps", ROOT / "extra" / "measure_engine_fps.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_engine_names():
    m = _mod()
    assert m.parse_engine_name("yolo11x-pose_1280.engine") == ("yolo11x-pose", "1280", (1280, 1280))
    assert m.parse_engine_name("yolo11l-pose_1920x1056.engine") == ("yolo11l-pose", "1920x1056", (1920, 1056))
    assert m.parse_engine_name("yolo11x-pose.engine") is None
    assert m.parse_engine_name("yolo11x-pose_1280.onnx") is None


def test_rectangles_never_cost_calib2_its_squares(tmp_path):
    from core.calib2 import load_fps_table
    p = tmp_path / "fps_table.json"
    p.write_text(json.dumps({"yolo11x-pose": {"1280": 44.6}, "_meta": {"runs": 30},
                             "_rect": {"yolo11x-pose": {"1760x960": 42.8}}}))
    assert load_fps_table(str(p)) == {"yolo11x-pose": {1280: 44.6}}
