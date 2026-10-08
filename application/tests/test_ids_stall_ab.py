"""extra/ids_stall_ab.py -- the crop A/B for the IDS stalls (no camera: a fake)."""
import importlib.util
import json
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "extra" / "ids_stall_ab.py"
_spec = importlib.util.spec_from_file_location("ids_stall_ab", _PATH)
ab = importlib.util.module_from_spec(_spec)
sys.modules["ids_stall_ab"] = ab
_spec.loader.exec_module(ab)


def test_parse_geom():
    assert ab.parse_geom("1.37", 2334784) == (1.37, 2334784)
    assert ab.parse_geom("1.37@1600000", 2334784) == (1.37, 1600000)


def test_alternates_crops_and_counts_stalls(monkeypatch, tmp_path):
    import camera.ids_camera as ic

    class FakeCam:
        def __init__(self, settings):
            self.settings = settings
            self.state = ic.IDSCameraState()
            self._stalls = 0

        def _track_frame(self, *a, **k):
            return {"gap_ms": 1667, "kind": ic.STALL_CAMERA_SILENT}

        def open(self, serial):
            wide = self.settings.crop_ratio > 1.2
            self.state.width, self.state.height = (1776, 1304) if wide else (1488, 1528)
            return True

        def start_acquisition(self):
            return True

        def get_stream_diagnostics(self):
            return {"counters": {"PipeTotalErrorCount": self._stalls}, "incomplete": 0}

        def read(self):
            self.state.frame_count += 1
            if self.state.width == 1776 and not self._stalls:   # the landscape crop
                self._track_frame(1.0, 2, None, False)           # stalls once per run
                self._stalls += 1
            return True, None

        def stop_acquisition(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(ic, "IDSCamera", FakeCam)
    assert ab.main(["--minutes", "0.0005", "--reps", "2", "--out", str(tmp_path)]) == 0
    res = json.loads((tmp_path / "stall_ab.json").read_text())
    assert [r["size"] for r in res["runs"]] == ["1488x1528", "1776x1304"] * 2     # ABAB
    s = res["summary"]
    assert s["0.974@2334784"]["stalls"] == 0
    land = s["1.37@2334784"]
    assert land["stalls"] == 2 and land["kinds"] == {"camera_silent": 2}
    assert land["gap_ms_min_max"] == [1667, 1667]
    assert res["runs"][1]["stream_counters_delta"] == {"PipeTotalErrorCount": 1}
