"""t-060 (W1g): the test suite never sends OSC to a real target.

127.0.0.1:9000 is the app's default OSC target and, on the show laptop, where TouchDesigner
listens: one run of the suite there sent it 15 /walldance/clear. tests/conftest.py makes every
OSC client a recorder (nothing leaves, whatever the target) and fails, by name, any other UDP
send -- unless the test opts out (@pytest.mark.osc_socket or the osc_listener fixture), and
then only towards a 127.0.0.1 port its own socket got from bind(port 0). The last test runs
the real conftest on a fake workspace against deliberately bad tests, aimed at a listener this
test holds, and checks that they fail with the guard's line and that nothing reached it.
"""

import os
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pythonosc import udp_client

from core import config_store
from core.config import OSC_IP, OSC_PORT
from core.osc_output import OSCSender


def test_the_app_sender_on_the_default_target_is_recorded_not_sent(osc_recorded):
    assert udp_client.SimpleUDPClient.send is udp_client.UDPClient.send   # the one door is guarded
    sender = OSCSender(OSC_IP, OSC_PORT)
    sender.enabled = True
    assert sender.client is not None
    # an address no consumer matches: if the guard were ever gone, the visuals would ignore it
    assert sender._send("/walldance/t060-guard", [1]) is True
    assert sender.send_errors == 0 and sender.take_alert() is None
    assert osc_recorded() == {(f"{OSC_IP}:{OSC_PORT}", "/walldance/t060-guard"): 1}


@pytest.mark.osc_socket
def test_opt_out_marker_reaches_the_tests_own_listener():
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    listener.settimeout(2.0)
    try:
        sender = OSCSender("127.0.0.1", listener.getsockname()[1])
        sender.enabled = True
        sender.send_clear()
        data, _ = listener.recvfrom(65536)
    finally:
        listener.close()
    assert data.startswith(b"/walldance/clear\0") and sender.send_errors == 0


def test_opt_out_fixture_reaches_its_listener_raw_and_osc(osc_listener, osc_recorded):
    dest = ("127.0.0.1", osc_listener.getsockname()[1])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.sendto(b"raw", dest)
    finally:
        s.close()
    udp_client.SimpleUDPClient(*dest).send_message("/walldance/t060", [2])
    assert osc_listener.recvfrom(65536)[0] == b"raw"
    assert osc_listener.recvfrom(65536)[0].startswith(b"/walldance/t060\0")
    assert not osc_recorded()


_BAD_TESTS = '''
import os, socket
import pytest
from pythonosc.udp_client import SimpleUDPClient

PORT = int(os.environ["T060_OUTER_PORT"])     # the outer test's listener: never this test's own


def _raw(connect=False):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if connect:                            # the readiness probe's way: connect, then send
            s.connect(("127.0.0.1", PORT))
            s.send(b"/walldance/ping\\0,\\0\\0\\0")
        else:
            s.sendto(b"/walldance/ping\\0,\\0\\0\\0", ("127.0.0.1", PORT))
    except OSError:
        pass                                   # code that swallows the refusal: still failed
    finally:
        s.close()


def test_raw_sendto():
    _raw()


def test_raw_connected_send():
    _raw(connect=True)


def test_osc_client_is_a_recorder(osc_recorded):
    SimpleUDPClient("127.0.0.1", PORT).send_message("/walldance/clear", [1])
    assert osc_recorded() == {("127.0.0.1:%d" % PORT, "/walldance/clear"): 1}


def test_osc_client_to_own_listener_without_opt_out():
    own = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    own.bind(("127.0.0.1", 0))
    try:
        SimpleUDPClient("127.0.0.1", own.getsockname()[1]).send_message("/walldance/t060", [1])
    except OSError:
        pass
    finally:
        own.close()


@pytest.mark.osc_socket
def test_opted_out_but_not_its_own_listener():
    try:
        SimpleUDPClient("127.0.0.1", PORT).send_message("/walldance/clear", [1])
    except OSError:
        pass


@pytest.mark.osc_socket
def test_opted_out_reaches_its_own_listener():
    own = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    own.bind(("127.0.0.1", 0))
    own.settimeout(2.0)
    try:
        SimpleUDPClient("127.0.0.1", own.getsockname()[1]).send_message("/walldance/t060", [7])
        assert own.recvfrom(65536)[0].startswith(b"/walldance/t060")
    finally:
        own.close()


def test_listener_fixture_opts_out(osc_listener):
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", osc_listener.getsockname())
    assert osc_listener.recvfrom(16)[0] == b"x"


def test_after():
    pass
'''


def test_guard_fails_a_real_send_and_nothing_reaches_the_target(tmp_path, osc_listener):
    here = Path(__file__).resolve().parent
    ws = tmp_path / "ws"                                     # a workspace of its own: app + projects
    (ws / "application" / "tests").mkdir(parents=True)
    (ws / "application" / "src" / "core").mkdir(parents=True)
    (ws / "projects").mkdir()
    shutil.copy(here / "conftest.py", ws / "application" / "tests" / "conftest.py")
    shutil.copy(config_store.__file__, ws / "application" / "src" / "core" / "config_store.py")
    (ws / "application" / "src" / "core" / "__init__.py").write_text("")
    (ws / "application" / "tests" / "test_bad.py").write_text(textwrap.dedent(_BAD_TESTS))
    (ws / "pytest.ini").write_text("[pytest]\n")
    port = osc_listener.getsockname()[1]

    run = subprocess.run([sys.executable, "-m", "pytest", "application/tests", "-q", "-rA",
                          "-p", "no:cacheprovider", "-c", str(ws / "pytest.ini"), "--rootdir", str(ws)],
                         cwd=ws, capture_output=True, text=True, timeout=180,
                         env=dict(os.environ, T060_OUTER_PORT=str(port)))
    out = run.stdout + run.stderr
    print("\n".join(ln for ln in out.splitlines() if ln.startswith(("FAILED", "PASSED", "CAUGHT"))))
    assert run.returncode == 1, out
    bad = "application/tests/test_bad.py::"
    for name in ("test_raw_sendto", "test_raw_connected_send", "test_osc_client_to_own_listener_without_opt_out",
                 "test_opted_out_but_not_its_own_listener"):
        assert f"FAILED {bad}{name}" in out, out
        assert f"CAUGHT {bad}{name} (call)" in out, out
    for name in ("test_osc_client_is_a_recorder", "test_opted_out_reaches_its_own_listener",
                 "test_listener_fixture_opts_out", "test_after"):   # a later test is not blamed
        assert f"PASSED {bad}{name}" in out, out
    assert (f"t-060 OSC guard: a UDP send to 127.0.0.1:{port} refused, nothing sent - no opt-out: a test sends "
            "UDP only with @pytest.mark.osc_socket or the osc_listener fixture") in out, out
    assert f"t-060 OSC guard: an OSC client send to 127.0.0.1:{port} refused, nothing sent - opted out, but" in out
    assert f"/walldance/clear -> 127.0.0.1:{port} x1" in out           # the recorder's summary line
    osc_listener.settimeout(0.3)
    with pytest.raises(socket.timeout):                     # the bad tests' target received nothing
        osc_listener.recvfrom(65536)
