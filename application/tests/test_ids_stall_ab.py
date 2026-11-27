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


# ---- U2 (2026-11-26): the condition knobs -------------------------------------------

def _args(**kw):
    base = dict(geom=None, cond=None, minutes=0.0005, reps=1, fps=20.0, exposure_us=12000.0,
                gain_db=27.5, gpu=False, roi=None, pixel_format=None, throughput_mbps=None,
                buffers=16, wait_timeout_ms=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_parse_roi_and_cond():
    assert ab.parse_roi("1776x1296") == (1776, 1296, None, None)
    assert ab.parse_roi("1776x1296+456+120") == (1776, 1296, 456, 120)
    with pytest.raises(ValueError):
        ab.parse_roi("1776x1296+456")
    assert ab.parse_cond("A:roi=1776x1296+456+120,fps=15") == {
        "name": "A", "roi": "1776x1296+456+120", "fps": "15"}
    with pytest.raises(ValueError):
        ab.parse_cond("A:width=1776")


def test_a_condition_changes_one_thing_over_the_globals():
    args = _args(roi="1776x1296+456+120", cond=["A:", "B:pf=Mono12g24IDS", "C:tl=200,gpu=1", "D:geom=0.974"])
    a, b, c, d = ab.conditions(args, 2334784)
    assert a["roi"] == [1776, 1296, 456, 120] and a["pixels"] == 1776 * 1296 and a["pf"] is None
    assert b["roi"] == a["roi"] and b["pf"] == "Mono12g24IDS" and b["fps"] == a["fps"] == 20.0
    assert c["tl"] == 200.0 and c["gpu"] is True and c["exp"] == 12000.0
    assert d["roi"] is None and (d["ratio"], d["pixels"]) == (0.974, 2334784)   # geom wins over --roi
    with pytest.raises(SystemExit):
        ab.conditions(_args(cond=["A:", "A:fps=15"]), 2334784)                # names must differ


class _Node:
    def __init__(self, v, lo=0, hi=10 ** 9, inc=1, entries=None):
        self.v, self.lo, self.hi, self.inc, self.entries = v, lo, hi, inc, entries

    def Value(self):
        return self.v

    def SetValue(self, v):
        assert self.lo <= v <= self.hi, (v, self.lo, self.hi)
        self.v = v

    def Minimum(self):
        return self.lo

    def Maximum(self):
        return self.hi

    def Increment(self):
        return self.inc

    def CurrentEntry(self):
        if self.entries is None:
            raise AttributeError("not an enumeration")
        return SimpleNamespace(SymbolicValue=lambda: self.v)

    def FindEntry(self, name):
        assert name in self.entries
        return name

    def SetCurrentEntry(self, e):
        self.v = e


class _NodeMap:
    def __init__(self):
        self.n = {"Width": _Node(1824, 256, 2688, 16), "Height": _Node(1260, 128, 1536, 4),
                  "OffsetX": _Node(432, 0, 880, 2), "OffsetY": _Node(136, 0, 276, 4),
                  "PixelFormat": _Node("Mono10g40IDS", entries={"Mono10g40IDS", "Mono12g24IDS"}),
                  "AcquisitionFrameRate": _Node(20.0, 0.5, 24.4),
                  "DeviceLinkThroughputLimit": _Node(162000000, 10000000, 300000000, 2000000)}

    def FindNode(self, name):
        return self.n[name]


def test_apply_condition_sets_an_exact_crop_and_puts_the_limit_back():
    import camera.ids_camera as ic
    nm = _NodeMap()
    cam = SimpleNamespace(_node_map=nm, state=ic.IDSCameraState(), _sensor_w=2688, _sensor_h=1536,
                          reallocs=0)
    cam._allocate_buffers = lambda: setattr(cam, "reallocs", cam.reallocs + 1)
    c = ab.resolve_cond({"name": "x", "roi": "1488x1536", "pf": "Mono12g24IDS", "fps": "30", "tl": "200"},
                        _args(), 2334784)
    done = ab.apply_condition(cam, c)
    assert (nm.n["Width"].v, nm.n["Height"].v) == (1488, 1536)
    assert (nm.n["OffsetX"].v, nm.n["OffsetY"].v) == (600, 0)                  # centred
    assert nm.n["PixelFormat"].v == "Mono12g24IDS" and cam.state.pixel_format == "Mono12g24IDS"
    assert nm.n["AcquisitionFrameRate"].v == 24.4                              # clamped to the max
    assert cam.reallocs == 1                                                   # payload changed: once
    assert nm.n["DeviceLinkThroughputLimit"].v == 200000000
    assert done["restore"] == {"DeviceLinkThroughputLimit": 162000000}
    ab.restore_nodes(cam, done["restore"])
    assert nm.n["DeviceLinkThroughputLimit"].v == 162000000
    # an explicit offset is kept; no change asked: nothing written, nothing to restore
    nm2 = _NodeMap()
    cam2 = SimpleNamespace(_node_map=nm2, state=ic.IDSCameraState(), _allocate_buffers=lambda: None)
    ab.apply_condition(cam2, ab.resolve_cond({"name": "y", "roi": "1776x1296+456+120"}, _args(), 1))
    assert (nm2.n["OffsetX"].v, nm2.n["OffsetY"].v, nm2.n["Width"].v) == (456, 120, 1776)
    assert ab.apply_condition(cam2, ab.resolve_cond({"name": "z", "geom": "1.37"}, _args(), 1)) == \
        {"set": {}, "restore": {}}


def test_wait_timeout_is_presented_only_while_the_loop_starts():
    import camera.ids_camera as ic
    seen = {}
    cam = SimpleNamespace(state=ic.IDSCameraState())
    cam.state.fps = 20.0
    cam.start_acquisition = lambda: seen.setdefault("fps", cam.state.fps) is not None
    assert ab.start_with_timeout(cam, 150) is True
    assert abs(seen["fps"] - 5000.0 / 150) < 1e-9 and cam.state.fps == 20.0
    seen.clear()
    assert ab.start_with_timeout(cam, None) is True and seen["fps"] == 20.0


def test_dump_nodes_reads_every_node_and_writes_none():
    nm = _NodeMap()
    nm.Nodes = lambda: [SimpleNamespace(Name=lambda k=k: k) for k in nm.n] + [SimpleNamespace(Name=lambda: "Absent")]
    before = {k: n.v for k, n in nm.n.items()}
    ds = SimpleNamespace(NodeMaps=lambda: [nm])
    cam = SimpleNamespace(_node_map=nm, _datastream=ds, _device=None)
    doc = ab.dump_nodes(cam)
    assert doc["remote"]["Width"] == 1824 and doc["remote"]["PixelFormat"] == "Mono10g40IDS"
    assert doc["stream"] == doc["remote"] and "Absent" not in doc["remote"]   # unreadable: left out
    assert "_error" in doc["tl_device"]                                       # no device: said, no raise
    assert {k: n.v for k, n in nm.n.items()} == before                        # nothing written


def test_extra_stream_registers_are_read_under_the_stream_lock():
    import threading
    regs = {"IncompleteFramesTimeoutReg": _Node(3), "IncompleteFramesPacketErrorReg": _Node(1)}
    nm = SimpleNamespace(FindNode=lambda name: regs[name])
    lock = threading.Lock()
    cam = SimpleNamespace(_datastream=SimpleNamespace(NodeMaps=lambda: [nm]), _stream_lock=lock)
    assert ab.read_extra_stream(cam) == {"IncompleteFramesTimeoutReg": 3, "IncompleteFramesPacketErrorReg": 1}
    assert not lock.locked()                                    # released
    assert ab.read_extra_stream(SimpleNamespace()) == {}        # no stream: nothing, no raise


def test_a_throughput_limit_between_steps_is_snapped_to_the_node_increment():
    import camera.ids_camera as ic
    nm = _NodeMap()
    cam = SimpleNamespace(_node_map=nm, state=ic.IDSCameraState(), _sensor_w=2688, _sensor_h=1536)
    c = ab.resolve_cond({"name": "x", "tl": "125"}, _args(), 2334784)   # (125 - 10) / 2 is not whole
    done = ab.apply_condition(cam, c)
    assert nm.n["DeviceLinkThroughputLimit"].v in (124000000, 126000000)
    assert (nm.n["DeviceLinkThroughputLimit"].v - 10000000) % 2000000 == 0
    assert done["restore"] == {"DeviceLinkThroughputLimit": 162000000}


def test_a_raised_limit_lets_the_condition_s_frame_rate_through():
    """The open sets the rate under the default limit's ceiling (33.8 fps at 1296 lines); a raised limit
    lifts the ceiling, so the condition's rate is asked again (window 4 ran "40 fps" at 33.8)."""
    import camera.ids_camera as ic
    nm = _NodeMap()
    nm.n["AcquisitionFrameRate"] = _Node(33.8, 0.5, 56.9)            # the ceiling once the limit is 300
    cam = SimpleNamespace(_node_map=nm, state=ic.IDSCameraState(), _sensor_w=2688, _sensor_h=1536)
    c = ab.resolve_cond({"name": "x", "tl": "300", "fps": "40"}, _args(), 2334784)
    done = ab.apply_condition(cam, c)
    assert nm.n["AcquisitionFrameRate"].v == 40.0 and cam.state.fps == 40.0
    assert done["set"]["AcquisitionFrameRate"] == 40.0


# --------------------------------------------------------------------------- U2c: what the app does beside the camera
def test_u2c_keys_are_off_by_default_and_trt_d2h_read_through_the_gpu():
    a, b, c, d = ab.conditions(_args(cond=["A:", "B:trt=1", "C:xf=90,cpu=2", "D:d2h=1"]), 2334784)
    assert (a["xf"], a["trt"], a["d2h"], a["cpu"], a["gpu"]) == (0, False, False, 0, False)
    assert b["trt"] == 1 and b["gpu"] is True and b["d2h"] is False
    assert (c["xf"], c["cpu"], c["gpu"]) == (90, 2, False)          # one thing at a time: no GPU read
    assert d["d2h"] is True and d["gpu"] is True and d["trt"] == 0
    with pytest.raises(ValueError):
        ab.conditions(_args(cond=["A:xf=45"]), 2334784)
    with pytest.raises(ValueError):
        ab.parse_cond("A:yolo=1")


def test_trt_runs_the_engine_on_each_gpu_frame_at_the_engine_shape():
    torch = pytest.importorskip("torch")
    calls = []

    class FakeModel:
        def __call__(self, x, **kw):
            calls.append((tuple(x.shape), kw))
            return []

    loaded = []
    c = ab.conditions(_args(cond=["B:trt=1,d2h=1"]), 2334784)[0]
    load = ab.SideLoad(c, SimpleNamespace(engine="show.engine", engine_hw="64x32", preview_scale=0.5),
                       loader=lambda p: loaded.append(p) or FakeModel())
    assert loaded == ["show.engine"]                    # loaded once, before the camera opens
    load.start(None)
    load.frame(None)                                    # no new frame: nothing done
    for _ in range(3):
        load.frame(torch.rand(1, 3, 50, 40))
    info = load.stop()
    assert [s for s, _ in calls] == [(1, 3, 64, 32)] * 3        # height x width, as the app's engine names
    assert calls[0][1]["imgsz"] == [64, 32] and calls[0][1]["half"] is True
    assert info["frames"] == 3 and info["trt_calls"] == 3 and info["errors"] == []
    assert info["d2h_calls"] == 3 and info["d2h_bytes"] == 3 * 25 * 20 * 3     # a uint8 preview per frame
    assert info["engine_hw"] == [64, 32] and "trt_ms_mean" in info


def test_trt_without_an_engine_is_refused():
    c = ab.conditions(_args(cond=["B:trt=1"]), 2334784)[0]
    with pytest.raises(SystemExit):
        ab.SideLoad(c, SimpleNamespace(engine=None))


def test_an_inference_error_is_recorded_not_raised():
    torch = pytest.importorskip("torch")

    def broken(x, **kw):
        raise RuntimeError("engine gone")

    c = ab.conditions(_args(cond=["B:trt=1"]), 2334784)[0]
    load = ab.SideLoad(c, SimpleNamespace(engine="e", engine_hw="32x32"), loader=lambda p: broken)
    load.frame(torch.rand(1, 3, 8, 8))
    info = load.stop()
    assert info["trt_calls"] == 0 and info["errors"] == ["RuntimeError: engine gone"]


def test_the_cpu_thread_converts_frames_until_stopped_and_xf_turns_the_camera_input():
    import numpy as np
    c = ab.conditions(_args(cond=["C:xf=90,cpu=2"]), 2334784)[0]
    cam = SimpleNamespace(input_transform=None)
    load = ab.SideLoad(c, SimpleNamespace())
    load.start(cam)
    assert cam.input_transform.rotation == 90           # the app's input_rotation, in the acquisition thread
    load.frame(np.zeros((12, 16, 3), np.uint8))
    time.sleep(0.2)
    info = load.stop()
    assert info["cpu_loops"] >= 2 and info["xf"] == 90
    assert not load._thread.is_alive()
    n = info["cpu_loops"]
    time.sleep(0.12)
    assert load.info["cpu_loops"] == n                   # stopped: no more work


def test_a_run_records_its_load(monkeypatch, tmp_path):
    import camera.ids_camera as ic

    class FakeCam:
        def __init__(self, settings):
            self.settings = settings
            self.state = ic.IDSCameraState()
            self.input_transform = None

        def _track_frame(self, *a, **k):
            return None

        def open(self, serial):
            self.state.width, self.state.height = 1824, 1260
            return True

        def start_acquisition(self):
            return True

        def get_stream_diagnostics(self):
            return {"counters": {}, "incomplete": 0}

        def read(self):
            import numpy as np
            self.state.frame_count += 1
            return True, np.zeros((4, 4, 3), np.uint8)

        def stop_acquisition(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(ic, "IDSCamera", FakeCam)
    assert ab.main(["--minutes", "0.003", "--reps", "1", "--roi", "1824x1260+432+136",
                    "--cond", "P:", "--cond", "X:xf=90,cpu=1", "--out", str(tmp_path)]) == 0
    runs = [json.loads(l) for l in (tmp_path / "stall_ab_runs.jsonl").read_text().splitlines()]
    assert [r["geom"] for r in runs] == ["P", "X"]
    assert runs[0]["load"]["frames"] > 0 and runs[0]["load"]["cpu_loops"] == 0 and "xf" not in runs[0]["load"]
    assert runs[1]["load"]["xf"] == 90 and runs[1]["load"]["cpu_loops"] >= 1
    assert runs[1]["cond"]["cpu"] == 1 and runs[1]["cond"]["gpu"] is False


def test_trt2_runs_the_engine_on_a_constant_gpu_tensor_without_the_frame_upload():
    torch = pytest.importorskip("torch")
    import numpy as np
    c = ab.conditions(_args(cond=["K:trt=2"]), 2334784)[0]
    assert c["trt"] == 2 and c["gpu"] is False           # the frame is read on the CPU path: no upload
    seen = []
    load = ab.SideLoad(c, SimpleNamespace(engine="e", engine_hw="64x32"),
                       loader=lambda p: (lambda x, **kw: seen.append(x)))
    load.frame(np.zeros((12, 16, 3), np.uint8))
    load.frame(np.ones((12, 16, 3), np.uint8))
    info = load.stop()
    assert info["trt_calls"] == 2 and [tuple(x.shape) for x in seen] == [(1, 3, 64, 32)] * 2
    assert torch.equal(seen[0], seen[1])                 # the same constant tensor, whatever the frame
    with pytest.raises(ValueError):
        ab.conditions(_args(cond=["K:trt=3"]), 2334784)


def test_mog_runs_the_app_s_motion_stage_on_each_cpu_frame_in_a_worker():
    pytest.importorskip("cv2")
    import numpy as np
    c = ab.conditions(_args(cond=["M:mog=0.5"]), 2334784)[0]
    assert c["mog"] == 0.5 and c["gpu"] is False and ab.conditions(_args(cond=["P:"]), 2334784)[0]["mog"] == 0.0
    load = ab.SideLoad(c, SimpleNamespace(cv2_threads=2))
    rng = np.random.default_rng(0)
    for _ in range(4):
        load.frame(rng.integers(0, 255, (64, 96, 3), dtype=np.uint8))
    load.frame(None)                                       # no new frame: nothing submitted
    info = load.stop()
    assert info["mog_calls"] == 4 and info["errors"] == [] and info["mog_scale"] == 0.5
    assert info["mog_ms"] >= 0.0 and load._mog_pool._shutdown
    import cv2
    before = cv2.getNumThreads()
    one = ab.SideLoad(ab.conditions(_args(cond=["M1:mog=0.5,cvt=1"]), 2334784)[0], SimpleNamespace(cv2_threads=4))
    assert one.stop()["cv2_threads"] == 1                  # cvt=N overrides --cv2-threads for that condition
    assert cv2.getNumThreads() == before                   # and the process-wide setting is put back
