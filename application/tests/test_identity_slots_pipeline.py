"""Identity slots wired into the pipeline output stage (CONT-6): FrameProcessor
glue, the belt hook (fake module), the OSC state message, the FRAME_SUMMARY
annotation.  No model / GPU: the output stage is driven directly."""
import socket
import sys
import types

import numpy as np
import pytest
from pythonosc.osc_message import OscMessage

pytest.importorskip("torch")  # pipeline pulls in torch/ultralytics
from core.osc_output import OSCSender  # noqa: E402
from core.pipeline import FrameProcessor, ProcessingSettings, ScaledTrack  # noqa: E402
from core.tracking_logger import TrackingLogger  # noqa: E402

H = 200.0


def _settings(**kw):
    s = ProcessingSettings(confidence=0.3, imgsz=640, use_fp16=False,
                           enhance_enabled=False, enhance_lite=False,
                           enhance_force=False, person_height_px=200,
                           use_gpu_path=False)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _st(tid, x, y, fss=0, hits=50):
    kp = np.tile([x, y], (17, 1)).astype(np.float64)
    return ScaledTrack(track_id=tid, keypoints=kp, confidence=np.full(17, 0.9),
                       bbox=np.array([x - 40, y - H / 2, 80, H]), history=[],
                       velocity=np.zeros(2), smoothed_centroid=np.array([x, y], float),
                       frames_since_skeleton=fss, centroid_raw=np.array([x, y], float),
                       hits=hits, age=hits, time_since_update=0, feed_src="yolo")


@pytest.fixture
def proc():
    p = FrameProcessor(model=None, settings=_settings())
    clock = {"t": 0.0}
    p.set_output_clock(lambda: clock["t"])
    p._clock = clock
    yield p


def _step(p, tracks, finalize=lambda t: t):
    p._clock["t"] += 0.05
    return p._run_identity_slots(tracks, finalize, 1920, 1080)


def test_churning_track_ids_become_one_stable_slot(proc):
    out = []
    for i in range(60):
        out.append(_step(proc, [_st(100 + i // 10, 500 + i, 400)]))
    assert all([o.track_id for o in f] == [1] for f in out[5:])
    last = out[-1][0]
    assert last.slot_state == "live" and last.track_id == 1
    # the box is the smoothed slot box, centred on the emitted centroid
    cx = last.bbox[0] + last.bbox[2] / 2
    assert cx == pytest.approx(last.smoothed_centroid[0])
    assert last.frames_since_skeleton == 0


def test_coasting_slot_track_keeps_its_id_and_skeleton(proc):
    for i in range(20):
        _step(proc, [_st(7, 500, 400)])
    out = _step(proc, [])
    assert [o.track_id for o in out] == [1]
    st = out[0]
    assert st.slot_state == "coasting" and st.is_bridged
    assert st.frames_since_skeleton == 999            # RTS input: no skeleton
    np.testing.assert_allclose(st.keypoints[0], st.smoothed_centroid, atol=1e-6)


def test_hidden_bound_track_is_followed(proc):
    for i in range(20):
        _step(proc, [_st(7, 500, 400)])

    class Trk:                       # the tracker still has id 7, unreported
        track_id = 7
    proc.tracker.tracks = [Trk()]
    out = _step(proc, [], finalize=lambda t: _st(7, 505, 400, fss=30))
    assert out[0].slot_state == "live" and out[0].track_id == 1


def test_disabled_slots_reset_and_emit_tracker_ids(proc):
    for i in range(10):
        _step(proc, [_st(7, 500, 400)])
    proc.configure_identity_slots(identity_slots_enabled=False)
    assert proc.last_emitted == [] and proc.slot_states()[0][1] == "lost"
    proc.configure_identity_slots(identity_slots_enabled=True, max_dancers=3,
                                  stability=0.9, coast_s=4.0)
    assert len(proc._slots.slots) == 3 and proc._slots.p.coast_s == 4.0


def test_belt_hook_from_a_fake_module(proc, monkeypatch):
    calls = []

    class Blob:
        def __init__(self, cx, cy):
            self.cx, self.cy, self.w, self.h, self.peak, self.score = cx, cy, 20, 8, 250, 0.9

    class BeltDetector:
        def __init__(self, params=None, static=None):
            pass

        def detect_near(self, gray, predictions):
            calls.append(predictions)
            # ROI-local coords in, the belt 0.3 h under the dancer at (500, 400)
            return {key: Blob(500 - 100, 400 + 0.3 * H - 50) for key, *_ in predictions}

    monkeypatch.setitem(sys.modules, "core.belt_detector",
                        types.SimpleNamespace(BeltDetector=BeltDetector,
                                              BeltParams=lambda: None))
    proc._belt_gray = np.zeros((10, 10), np.uint8)
    proc._belt_gray_offset = (100, 50)                # ROI origin in the frame
    for i in range(20):
        _step(proc, [_st(7, 500, 400)])
    assert proc.belt_status == "ready" and calls
    # live: one batched detect_near per frame, searching at the YOLO hips
    # (ROI-local px), a tight gate and the band-width hint
    _key, x, y, gate, bw = calls[-1][0]
    assert (x, y) == pytest.approx((400.0, 350.0), abs=1.0)
    assert gate == pytest.approx(0.25 * H) and bw == pytest.approx(0.22 * H)
    out = [_step(proc, []) for _ in range(40)]        # no track: the belt holds it
    assert all(f and f[0].slot_state == "belt" for f in out)
    assert out[-1][0].smoothed_centroid == pytest.approx([500, 400], abs=2.0)


def test_real_belt_detector_holds_a_dancer_yolo_lost(proc):
    """The real core.belt_detector (gated detect_near) on a synthetic IR frame:
    a dim dancer with a bright waist band.  While YOLO sees the dancer the
    hips->centroid offset is learned; when the tracker loses it, the slot is
    held by the belt at the dancer's centroid instead of coasting."""
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("core.belt_detector")
    h = 150.0
    cx, cy = 700.0, 560.0                      # emitted centroid (chest-ish)
    hip_y = cy + 0.15 * h                      # belt at the hips
    gray = np.full((1000, 1400), 14, np.uint8)
    cv2.rectangle(gray, (int(cx - 20), int(cy - 0.45 * h)), (int(cx + 20), int(cy + 0.5 * h)), 30, -1)
    cv2.rectangle(gray, (int(cx - 15), int(hip_y - 2)), (int(cx + 15), int(hip_y + 2)), 200, -1)
    proc._belt_gray = gray
    proc._belt_gray_offset = (0, 0)

    def st(fss=0):
        t = _st(9, cx, cy, fss=fss)
        t.bbox = np.array([cx - 30, cy - h / 2, 60, h])
        t.keypoints[11] = (cx - 12, hip_y)
        t.keypoints[12] = (cx + 12, hip_y)
        return t
    for _ in range(20):
        _step(proc, [st()])
    assert proc.belt_status == "ready"
    s = proc._slots.slots[0]
    assert s.belt_seen >= 5 and s.belt_offset[1] == pytest.approx(cy - hip_y, abs=3)
    out = [_step(proc, []) for _ in range(30)]          # YOLO / tracker lost it
    assert all(f and f[0].slot_state == "belt" for f in out)
    assert out[-1][0].smoothed_centroid == pytest.approx([cx, cy], abs=3)
    gray[:] = 14                                         # belt gone too -> plain coasting
    assert _step(proc, [])[0].slot_state == "coasting"


def test_missing_belt_module_is_a_no_op(proc, monkeypatch):
    monkeypatch.setitem(sys.modules, "core.belt_detector", None)   # ImportError
    proc._belt_gray = np.zeros((10, 10), np.uint8)
    out = [_step(proc, [_st(7, 500, 400)]) for _ in range(5)]
    assert out[-1][0].slot_state == "live"
    assert proc.belt_status == "unavailable"
    proc.configure_identity_slots(use_ir_belt=False)
    assert proc.belt_status == "off"


def test_slot_states_land_in_the_frame_summary(proc, tmp_path):
    lg = TrackingLogger(enabled=True, filepath=None)
    lg.start_session(str(tmp_path))
    proc.tracker.logger = lg
    for i in range(8):
        lg.set_frame(i)
        lg.log_frame_summary(1, 1, [], 0, [], emitted=[7])
        _step(proc, [_st(7, 500, 400)])
    lg.close()
    import json
    rows = [json.loads(line) for line in (tmp_path / "tracking_events.jsonl").read_text().splitlines()]
    fs = [r for r in rows if r.get("event") == "FRAME_SUMMARY"]
    assert len(fs) == 8 and all("slots" in r["data"] for r in fs)
    assert fs[-1]["data"]["slots"][0]["st"] == "live"
    assert fs[-1]["data"]["emitted_slots"] == [1]
    assert any(r.get("event") == "SLOT_EVENT" and r["data"]["ev"] == "entry" for r in rows)


def test_state_message_wire_format():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(2.0)
    try:
        sender = OSCSender("127.0.0.1", s.getsockname()[1])
        sender.enabled = True
        sender.send_states([(1, "live", 2.5), (2, "lost", 0.0)])
        msgs = [OscMessage(s.recvfrom(65536)[0]) for _ in range(2)]
    finally:
        s.close()
    assert [m.address for m in msgs] == ["/walldance/dancer/state"] * 2
    assert msgs[0].params[:2] == [1, "live"] and msgs[0].params[2] == pytest.approx(2.5)
    assert msgs[1].params[:2] == [2, "lost"]
