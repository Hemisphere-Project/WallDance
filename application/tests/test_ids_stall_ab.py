"""extra/ids_stall_ab.py -- the crop A/B for the IDS stalls (no camera: a fake)."""
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

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


class _DiagCam:
    """A camera whose frame-id / incomplete counters move, and whose portrait run
    ends inside a hole (the last frame came 23.6 s before the end, none follows)."""

    def __init__(self, settings):
        import camera.ids_camera as ic
        self.settings = settings
        self.state = ic.IDSCameraState()
        self._stall_threshold_s = 0.4
        self._last_acq_frame_time = 0.0
        self.diag = {"frame_id_gaps": 0, "frame_ids_skipped": 0, "incomplete": 0}

    def _track_frame(self, *a, **k):
        return None

    def open(self, serial):
        wide = self.settings.crop_ratio > 1.2
        self.state.width, self.state.height = (1776, 1304) if wide else (1488, 1528)
        return True

    def start_acquisition(self):
        hole = 0.0 if self.state.width == 1776 else 23.6
        self._last_acq_frame_time = time.perf_counter() - hole
        return True

    def get_stream_diagnostics(self):
        return {"counters": {}, **self.diag}

    def read(self):
        if self.state.width == 1776:                     # landscape: frames keep coming
            self.state.frame_count += 1
            self._last_acq_frame_time = time.perf_counter()
            self.diag["frame_id_gaps"] += 1
            self.diag["frame_ids_skipped"] += 3
            self.diag["incomplete"] += 2
        return True, None

    def stop_acquisition(self):
        pass

    def close(self):
        pass


def test_a_hole_open_at_the_end_and_the_frame_id_counters(monkeypatch, tmp_path):
    import camera.ids_camera as ic
    monkeypatch.setattr(ic, "IDSCamera", _DiagCam)
    assert ab.main(["--minutes", "0.0005", "--reps", "1", "--out", str(tmp_path)]) == 0
    res = json.loads((tmp_path / "stall_ab.json").read_text())
    portrait, land = res["runs"]
    # the 23.6 s hole used to read "stalls=0"
    assert len(portrait["stalls"]) == 1
    hole = portrait["stalls"][0]
    assert hole["kind"] == ab.OPEN_AT_END and hole["open_at_end"] is True
    assert 23600 <= hole["gap_ms"] < 30000
    assert res["summary"]["0.974@2334784"]["kinds"] == {"open_at_end": 1}
    assert land["stalls"] == []
    # frame-id gaps / skipped ids / incomplete buffers: per-run deltas + summary sums
    assert land["frame_id_gaps"] > 0 and land["frame_ids_skipped"] == 3 * land["frame_id_gaps"]
    assert land["incomplete"] == 2 * land["frame_id_gaps"]
    assert portrait["frame_id_gaps"] == portrait["frame_ids_skipped"] == portrait["incomplete"] == 0
    s = res["summary"]["1.37@2334784"]
    assert s["frame_id_gaps"] == land["frame_id_gaps"] and s["incomplete"] == land["incomplete"]


def test_no_open_hole_below_the_stall_threshold():
    cam = SimpleNamespace(_stall_threshold_s=0.4, _last_acq_frame_time=time.perf_counter())
    assert ab.open_stall(cam, time.perf_counter() + 0.1) is None
    assert ab.open_stall(cam, time.perf_counter() + 1.0)["gap_ms"] >= 1000
    cam._last_acq_frame_time = 0.0                       # acquisition never started
    assert ab.open_stall(cam, time.perf_counter()) is None


def test_an_uploaded_copy_finds_the_app_code_from_the_cwd(tmp_path, monkeypatch):
    """`wdremote py` runs a copy from tmp_analysis/remote/<stamp>/ with cwd = the
    slot's application/: the copy's parents[1] is not the checkout."""
    repo = Path(__file__).resolve().parents[2]
    scratch = tmp_path / "tmp_analysis" / "remote" / "20261008_120000"
    scratch.mkdir(parents=True)
    copy = scratch / "ids_stall_ab.py"
    copy.write_text(_PATH.read_text())
    spec = importlib.util.spec_from_file_location("ids_stall_ab_copy", copy)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                         # importing needs no app code
    monkeypatch.delenv("WD_REPO_ROOT", raising=False)
    monkeypatch.chdir(repo / "application")
    assert mod.find_repo_root() == repo
    monkeypatch.chdir(tmp_path)                          # nothing above: say so
    with pytest.raises(SystemExit):
        mod.find_repo_root()
    assert mod.find_repo_root(str(repo)) == repo         # --repo
    monkeypatch.setenv("WD_REPO_ROOT", str(repo))
    assert mod.find_repo_root() == repo
