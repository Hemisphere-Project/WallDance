"""core/osc_output.py: wire format on loopback + send-failure guard (ARCH-6)."""
import socket
from types import SimpleNamespace

import numpy as np
import pytest
from pythonosc.osc_message import OscMessage

import core.osc_output as osc_mod
from core.osc_output import OSCSender


def _track(tid=3):
    kp = np.arange(34, dtype=float).reshape(17, 2) * 10
    return SimpleNamespace(track_id=tid, bbox=np.array([10., 20., 30., 60.]),
                           velocity=np.array([2., -1.]), keypoints=kp,
                           confidence=np.linspace(0, 1, 17),
                           smoothed_centroid=np.array([25., 50.]))


@pytest.fixture
def receiver():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(2.0)
    yield s
    s.close()


def _recv_all(sock, n):
    msgs = []
    for _ in range(n):
        data, _ = sock.recvfrom(65536)
        m = OscMessage(data)
        msgs.append((m.address, m.params))
    return msgs


def test_frame_wire_format_on_loopback(receiver):
    port = receiver.getsockname()[1]
    sender = OSCSender("127.0.0.1", port)
    sender.enabled = True
    sender.send_frame([_track(3)], 100, 200)
    msgs = _recv_all(receiver, 5)
    assert [a for a, _ in msgs] == [
        "/walldance/count", "/walldance/dancer/centroid", "/walldance/dancer/bbox",
        "/walldance/dancer/velocity", "/walldance/dancer/keypoints"]
    assert msgs[0][1] == [1, 3]
    assert msgs[1][1] == pytest.approx([3, 0.25, 0.25])
    assert msgs[2][1] == pytest.approx([3, 0.1, 0.1, 0.3, 0.3])
    assert msgs[3][1] == pytest.approx([3, 0.02, -0.005])
    assert len(msgs[4][1]) == 1 + 17 * 3
    assert sender.send_errors == 0 and sender.take_alert() is None


def test_broadcast_target_does_not_crash():
    """255.255.255.255 without SO_BROADCAST raises PermissionError on the
    first sendto (audit [S]); it must be dropped, counted, and alerted."""
    sender = OSCSender("255.255.255.255", 9)     # port 9 = discard
    sender.enabled = True
    sender.send_frame([_track()], 100, 100)      # must not raise
    sender.send_latency_ms(0.0)
    sender.send_clear()
    if sender.send_errors:                       # platform refused broadcast
        assert sender.send_errors == 7
        msg = sender.take_alert()
        assert "255.255.255.255:9" in msg and "output continues" in msg


class _FlakyClient:
    def __init__(self):
        self.fail = True
        self.sent = []

    def send_message(self, address, args):
        if self.fail:
            raise BlockingIOError(11, "Resource temporarily unavailable")
        self.sent.append((address, list(args)))


def test_send_errors_rate_limited_and_output_resumes(monkeypatch, capsys):
    clock = [100.0]
    monkeypatch.setattr(osc_mod.time, "monotonic", lambda: clock[0])
    sender = OSCSender("127.0.0.1", 9000)
    sender.enabled = True
    sender.client = _FlakyClient()

    for _ in range(3):
        sender.send_frame([_track()], 100, 100)      # 5 datagrams each
    assert sender.send_errors == 15
    first = sender.take_alert()
    assert "1 message(s) dropped" in first and "BlockingIOError" in first
    assert sender.take_alert() is None
    assert capsys.readouterr().out.count("[OSC]") == 1

    clock[0] += sender.alert_interval_s + 0.01
    sender.send_frame([_track()], 100, 100)
    assert "15 message(s) dropped" in sender.take_alert()   # 14 suppressed + 1

    sender.client.fail = False                           # receiver back
    sender.send_frame([_track(7)], 100, 100)
    assert [a for a, _ in sender.client.sent][0] == "/walldance/count"
    assert sender.client.sent[0][1] == [1, 7]
    assert len(sender.client.sent) == 5


def test_non_socket_errors_still_propagate():
    """Only OSError is a 'send failure'; a programming error must surface
    (the main loop's frame boundary reports it)."""
    class Broken:
        def send_message(self, address, args):
            raise TypeError("bad arg")

    sender = OSCSender("127.0.0.1", 9000)
    sender.enabled = True
    sender.client = Broken()
    with pytest.raises(TypeError):
        sender.send_frame([_track()], 100, 100)
