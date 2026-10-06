"""WD_ENGINE_DIR: per-box TRT engines for the app (same override as replay.py)."""
from core.model_manager import ModelManager


def test_engine_dir_override(tmp_path, monkeypatch):
    models = tmp_path / "models"
    (models / "dev37").mkdir(parents=True)
    (models / "dev37" / "yolo11x-pose_1280.engine").write_bytes(b"e")
    mm = ModelManager.__new__(ModelManager)
    mm.models_dir = str(models)
    mm.imgsz = 1280
    monkeypatch.delenv("WD_ENGINE_DIR", raising=False)
    assert mm.get_engine_path("yolo11x-pose") == str(models / "yolo11x-pose_1280.engine")
    monkeypatch.setenv("WD_ENGINE_DIR", "models/dev37")
    assert mm.get_engine_path("yolo11x-pose") == str(models / "dev37" / "yolo11x-pose_1280.engine")
    assert mm.get_engine_path("yolo11x-pose", 800) == str(models / "yolo11x-pose_800.engine")
