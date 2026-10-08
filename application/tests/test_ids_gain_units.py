"""IDS gain units: the app speaks dB, the IDS uEye+ ``Gain`` node is a linear factor.

The U3-34E0XCP (IMX664) node spans x1.0 .. x31.6 (= 0 .. 30 dB; datasheet
"Amplification 31.6x"). Until 2026-10-08 the dB value went straight into the node:
every take's .meta reads config ``ids_gain_db`` 36.0 with ``camera.nodes.Gain``
31.62277603149414 (= 10**1.5, the clamp). No SDK or hardware: a fake node map.
"""
import math
from types import SimpleNamespace

import pytest

from camera.ids_camera import (IDSCamera, IDSCameraSettings, IDSCameraState,
                               gain_db_to_factor, gain_db_to_node_value,
                               gain_factor_to_db, gain_node_is_db,
                               gain_node_value_to_db)

IMX664_MAX = 31.62277603149414          # what the laptop's camera reads back at the clamp


class _GainNode:
    """A GenICam float node: range, value, optional unit."""

    def __init__(self, lo=1.0, hi=IMX664_MAX, value=1.0, unit=None):
        self.lo, self.hi, self.value, self.unit = lo, hi, value, unit
        self.writes = []

    def Minimum(self):
        return self.lo

    def Maximum(self):
        return self.hi

    def Value(self):
        return self.value

    def SetValue(self, v):
        if not self.lo <= v <= self.hi:
            raise ValueError(f"out of range: {v}")   # the real node refuses, too
        self.writes.append(v)
        self.value = v

    def Unit(self):
        if self.unit is None:
            raise AttributeError("no unit")
        return self.unit


class _NodeMap:
    def __init__(self, **nodes):
        self.nodes = nodes

    def FindNode(self, name):
        if name not in self.nodes:
            raise KeyError(name)
        return self.nodes[name]


def _camera(node):
    cam = object.__new__(IDSCamera)               # no SDK / hardware needed
    cam.close = lambda: None                      # __del__ on a bare object
    cam.settings = IDSCameraSettings()
    cam.state = IDSCameraState()
    cam.state.is_open = True
    cam._node_map = _NodeMap(Gain=node)
    return cam


# --- pure conversions ---------------------------------------------------------

def test_db_factor_conversions():
    assert gain_db_to_factor(0.0) == 1.0
    assert gain_db_to_factor(20.0) == pytest.approx(10.0)
    assert gain_db_to_factor(30.0) == pytest.approx(IMX664_MAX, rel=1e-6)
    assert gain_factor_to_db(IMX664_MAX) == pytest.approx(30.0, abs=1e-5)
    for db in (0.0, 3.0, 6.0, 23.7, 27.8, 30.0):
        assert gain_factor_to_db(gain_db_to_factor(db)) == pytest.approx(db)


def test_node_value_is_a_clamped_factor():
    assert gain_db_to_node_value(36.0, 1.0, IMX664_MAX) == IMX664_MAX        # clamp, = 30 dB
    assert gain_db_to_node_value(27.8, 1.0, IMX664_MAX) == pytest.approx(24.55, abs=0.01)
    assert gain_db_to_node_value(6.0, 1.0, IMX664_MAX) == pytest.approx(1.995, abs=0.001)
    assert gain_db_to_node_value(0.0, 1.0, IMX664_MAX) == 1.0
    assert gain_db_to_node_value(-3.0, 1.0, IMX664_MAX) == 1.0                # below the floor
    # a node that declares dB is written in dB (other camera families)
    assert gain_db_to_node_value(36.0, 0.0, 48.0, node_is_db=True) == 36.0
    assert gain_node_value_to_db(12.0, node_is_db=True) == 12.0


def test_unit_detection():
    assert not gain_node_is_db(_GainNode())                 # IDS uEye+: no unit = factor
    assert not gain_node_is_db(_GainNode(unit=""))
    assert gain_node_is_db(_GainNode(unit="dB"))


# --- IDSCamera.set_gain / range / capture snapshot ------------------------------

def test_set_gain_writes_the_factor_not_the_db_value():
    node = _GainNode()
    cam = _camera(node)
    assert cam.set_gain(24.0)
    assert node.value == pytest.approx(15.85, abs=0.01)     # x15.85, NOT x24 (= 27.6 dB)
    assert cam.state.gain_db == pytest.approx(24.0)
    assert cam.set_gain(0.0)
    assert node.value == 1.0 and cam.state.gain_db == pytest.approx(0.0)


def test_set_gain_clamps_to_the_camera_max_and_reports_the_real_db(capsys):
    node = _GainNode()
    cam = _camera(node)
    assert cam.set_gain(36.0)                     # the shows' configs since 2026-10-06
    assert node.value == IMX664_MAX
    assert cam.state.gain_db == pytest.approx(30.0, abs=1e-4)
    assert "outside the camera's 0.0..30.0 dB: using 30.0 dB" in capsys.readouterr().out


def test_set_gain_on_a_db_node():
    node = _GainNode(lo=0.0, hi=48.0, value=0.0, unit="dB")
    cam = _camera(node)
    assert cam.set_gain(36.0)
    assert node.value == 36.0 and cam.state.gain_db == 36.0


def test_gain_range_is_in_db():
    cam = _camera(_GainNode())
    lo, hi = cam.get_gain_range()
    assert lo == pytest.approx(0.0) and hi == pytest.approx(30.0, abs=1e-4)


def test_capture_settings_give_the_effective_db():
    cam = _camera(_GainNode(value=IMX664_MAX))
    snap = cam.get_capture_settings(fast=True)
    assert snap["nodes"]["Gain"] == IMX664_MAX              # raw node kept (provenance)
    assert snap["gain_db"] == pytest.approx(30.0)


# --- calibrate-from-take and the servo ceiling ---------------------------------

def _recorder_with_meta(meta):
    import core.video_recorder as vr
    rec = object.__new__(vr.VideoRecorder)
    rec._playback_meta = meta
    return rec


def test_playback_camera_uses_the_gain_the_camera_ran_at():
    cfg = {"camera_source": "ids", "ids_exposure_us": 25000.0, "ids_gain_db": 36.0}
    legacy = {"config": cfg, "camera": {"source": "ids", "nodes": {"Gain": IMX664_MAX}}}
    assert _recorder_with_meta(legacy).playback_camera == {"exposure_us": 25000.0,
                                                           "gain_db": 30.0}
    old_27 = {"config": dict(cfg, ids_gain_db=27.8),
              "camera": {"source": "ids", "nodes": {"Gain": 27.799999237060547}}}
    assert _recorder_with_meta(old_27).playback_camera["gain_db"] == pytest.approx(28.88, abs=0.01)
    fixed = {"config": dict(cfg, ids_gain_db=24.0),
             "camera": {"source": "ids", "gain_db": 24.0, "nodes": {"Gain": 15.85}}}
    assert _recorder_with_meta(fixed).playback_camera["gain_db"] == 24.0
    bare = {"config": dict(cfg, ids_gain_db=12.0), "camera": {"source": "ids"}}
    assert _recorder_with_meta(bare).playback_camera["gain_db"] == 12.0


def test_servo_gain_ceiling_follows_the_camera():
    from core.config import AUTOCAL_SERVO_GAIN_MAX_DB
    from runtime.calibration_flows import CalibrationFlows
    cap = CalibrationFlows._servo_gain_max_db
    imx = SimpleNamespace(get_gain_range_db=lambda: (0.0, 30.0))
    assert cap(SimpleNamespace(unified_camera=imx)) == pytest.approx(30.0)
    none = SimpleNamespace(get_gain_range_db=lambda: None)
    assert cap(SimpleNamespace(unified_camera=none)) == AUTOCAL_SERVO_GAIN_MAX_DB

    def boom():
        raise RuntimeError("no camera")
    assert cap(SimpleNamespace(unified_camera=SimpleNamespace(get_gain_range_db=boom))) \
        == AUTOCAL_SERVO_GAIN_MAX_DB
    assert math.isclose(min(AUTOCAL_SERVO_GAIN_MAX_DB, 48.0),
                        cap(SimpleNamespace(unified_camera=SimpleNamespace(
                            get_gain_range_db=lambda: (0.0, 48.0)))))
