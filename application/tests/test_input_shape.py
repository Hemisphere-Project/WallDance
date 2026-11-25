"""P1 (Plan Cordée rev 2): a rectangular network input, its engine naming, its fallback, its readiness row."""
import pytest

from core.input_shape import (engine_suffix, net_height, padding_share, parse_shape, pick_shape, shape_str,
                              target_hw)

FACADE_ROI = (965, 1771)          # the 08/10 façade ROI (w, h)
WALL25_ROI = (1322, 1275)         # the 25 m wall ROI: nearly square


@pytest.mark.parametrize("value,want", [
    ("1760x960", (1760, 960)), ("1920X1056", (1920, 1056)), (" 1760 x 960 ", (1760, 960)),
    ([1760, 960], (1760, 960)), ((1920, 1056), (1920, 1056)), ("", None), (None, None)])
def test_parse_shape(value, want):
    assert parse_shape(value) == want


@pytest.mark.parametrize("bad", ["1760", "1760x", "1000x960", "0x960", "axb", "1760x960x3", [1760]])
def test_parse_shape_rejects(bad):
    with pytest.raises(ValueError):
        parse_shape(bad)


def test_names():
    assert shape_str((1760, 960)) == "1760x960" and shape_str(None) == ""
    assert engine_suffix(1280) == "1280" and engine_suffix(1280, (1760, 960)) == "1760x960"
    assert target_hw(1280) == (1280, 1280) and target_hw(1280, (1760, 960)) == (1760, 960)


def test_facade_numbers_of_the_roadmap():
    # a 1280 square: 45 % padding, a 110 px dancer at ~80 px; the rectangle keeps her near full size
    assert padding_share(*FACADE_ROI, 1280) == pytest.approx(0.455, abs=0.01)
    assert net_height(110, *FACADE_ROI, 1280) == pytest.approx(79.5, abs=1)
    assert net_height(110, *FACADE_ROI, 1280, (1760, 960)) == pytest.approx(109.3, abs=1)
    assert padding_share(*FACADE_ROI, 1280, (1760, 960)) < 0.02


def test_pick_shape():
    assert pick_shape(*FACADE_ROI) == (1760, 960)
    assert pick_shape(*WALL25_ROI) is None          # nearly square: the square stays


def test_letterbox_rectangle_and_square_unchanged():
    torch = pytest.importorskip("torch")
    from core.gpu_pipeline import GpuFrame, GpuPipeline
    t = torch.rand(1, 3, 1771, 965)
    rect, info = GpuPipeline._prepare_yolo_input(None, GpuFrame(t), 1280, (1760, 960))
    assert tuple(rect.shape) == (1, 3, 1760, 960)
    assert info["target_w"] == 960 and info["target_h"] == 1760
    assert info["scale"] == pytest.approx(min(960 / 965, 1760 / 1771))
    assert info["pad_x"] == (960 - info["new_w"]) // 2 and info["pad_y"] == (1760 - info["new_h"]) // 2
    sq, sq_info = GpuPipeline._prepare_yolo_input(None, GpuFrame(t), 1280)
    sq2, _ = GpuPipeline._prepare_yolo_input(None, GpuFrame(t), 1280, None)
    assert tuple(sq.shape) == (1, 3, 1280, 1280) and torch.equal(sq, sq2)
    assert sq_info["target_w"] == 1280 and sq_info["pad_x"] == (1280 - sq_info["new_w"]) // 2


def test_engine_naming_and_fallback(tmp_path):
    pytest.importorskip("ultralytics")
    from core.model_manager import ModelManager
    mm = ModelManager(str(tmp_path), use_tensorrt=True, imgsz=1280)
    assert mm.get_engine_path("yolo11x-pose").endswith("yolo11x-pose_1280.engine")
    mm.set_shape((1760, 960))
    # no rectangle engine on disk: the square's path, so the existing build/prompt flow is unchanged
    assert mm.get_engine_path("yolo11x-pose").endswith("yolo11x-pose_1280.engine")
    assert mm.get_engine_path("yolo11x-pose", shape=(1760, 960)).endswith("yolo11x-pose_1760x960.engine")
    (tmp_path / "yolo11x-pose_1760x960.engine").write_bytes(b"")
    assert mm.get_engine_path("yolo11x-pose").endswith("yolo11x-pose_1760x960.engine")
    assert mm.get_engine_path("yolo11x-pose_1760x960", shape=None).endswith("yolo11x-pose_1280.engine")
    assert mm.effective_shape() is None             # nothing loaded yet


def test_schema_normalises_or_drops():
    from core.config_schema import validate_flat
    out, warn = validate_flat({"yolo_shape": "1760X960"})
    assert out["yolo_shape"] == "1760x960" and not warn
    out, warn = validate_flat({"yolo_shape": "1000x960"})
    assert "yolo_shape" not in out and warn
    out, warn = validate_flat({"yolo_shape": ""})
    assert out["yolo_shape"] == "" and not warn


def test_readiness_row():
    from core.ops_monitor import check_input_shape
    r = check_input_shape(requested=(1760, 960), running=None, imgsz=1280,
                          fallback_reason="no engine yolo11x-pose_1760x960.engine", roi_w=965, roi_h=1771)
    assert r.status == "warn" and "1760x960" in r.detail and "no engine" in r.detail
    r = check_input_shape(requested=(1760, 960), running=(1760, 960), imgsz=1280, roi_w=965, roi_h=1771)
    assert r.status == "ok" and "rectangle" in r.detail
    r = check_input_shape(requested=None, running=None, imgsz=1280, roi_w=965, roi_h=1771)
    assert r.status == "ok" and "suits yolo_shape 1760x960" in r.detail
    r = check_input_shape(requested=None, running=None, imgsz=1280, roi_w=1322, roi_h=1275)
    assert r.status == "ok" and "suits" not in r.detail
