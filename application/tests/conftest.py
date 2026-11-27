"""Make the app's ``src/`` importable (its modules import each other by bare name)."""
import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# t-060 guard: no test may write the real projects folder.
#
# On the laptop the DEV slot's projects folder is a junction to the live one, and
# projects/last_project.txt is the pointer the live app and its launcher read to
# preselect a project: a test's store once rewrote it with a test-only project name.
# The guard CHECKS the real files around every test instead of redirecting the path:
# a redirect patches one name in one process, and a default argument bound at import,
# a module that copied the path, a store or app a test builds its own way, or a
# subprocess (the app, replay.py) would all still reach the real file. A check looks
# at the file itself, whoever wrote it. A test that changed the pointer FAILS by name
# and the pointer's old content is put back (its mtime stays moved, as evidence); a
# test that added, removed or replaced anything in the projects tree (two folder
# levels down: project folders, config versions, recordings, plates) FAILS by name.
# ---------------------------------------------------------------------------
import os

import pytest

from core import config_store as _config_store

_POINTER = _config_store.LAST_PROJECT_FILE
_PROJECTS = _config_store.PROJECTS_DIR


def _pointer_state():
    try:
        with open(_POINTER, "rb") as f:
            data = f.read()
        return data, os.stat(_POINTER).st_mtime_ns
    except OSError:
        return None, None


def _tree_state(depth=2):
    """{folder: (mtime_ns, entry names, {file: (inode, size, mtime_ns)})} for the projects folder and
    its folders ``depth`` levels down (links followed: on the laptop and in the snapshots the projects
    are links). The files' stats (not the pointer's: its own check watches it) show a file replaced or
    rewritten whatever its folder's mtime says; they come with the listing (``DirEntry.stat``): no call
    per file on Windows (st_ino is 0 there), one stat per file on Linux. One pass per folder: cost."""
    state, level = {}, [_PROJECTS]
    for k in range(depth + 1):
        nxt = []
        for d in level:
            names, files, dirs = [], {}, []
            try:
                mtime = os.stat(d).st_mtime_ns
                with os.scandir(d) as it:
                    for e in it:
                        names.append(e.name)
                        if e.is_dir():
                            dirs.append(e.path)
                        elif e.path != _POINTER:
                            try:
                                st = e.stat()
                                files[e.name] = (st.st_ino, st.st_size, st.st_mtime_ns)
                            except OSError:
                                files[e.name] = None
            except OSError:
                state[d] = (None, frozenset(), {})
                continue
            state[d] = (mtime, frozenset(names), files)
            if k < depth:
                nxt += dirs
        level = nxt
    return state


def _tree_changes(before, after, pointer_written):
    out, none = [], (None, frozenset(), {})
    for d in sorted(set(before) | set(after)):
        (m0, n0, f0), (m1, n1, f1) = before.get(d, none), after.get(d, none)
        if n0 != n1:   # relpath only for a change: it cost more than the stats, at every check
            out.append(f"{os.path.relpath(d, _PROJECTS)}: added {sorted(n1 - n0)} removed {sorted(n0 - n1)}")
        elif f0 != f1 or (m0 is not None and m1 is not None and m0 != m1
                          and not (d == _PROJECTS and pointer_written)):
            out.append(f"{os.path.relpath(d, _PROJECTS)}: a file in it was replaced (same entries)")
    return out


class _ProjectsGuard:
    def __init__(self):
        self.pointer = _pointer_state()
        self.tree = _tree_state()

    def check(self):
        """What changed since the last check (and put the pointer's content back)."""
        problems = []
        pointer = _pointer_state()
        if pointer != self.pointer:
            old, new = self.pointer[0], pointer[0]
            show = lambda b: None if b is None else b.decode("utf-8", "replace")  # noqa: E731
            problems.append(f"the real project pointer {_POINTER} was written: {show(old)!r} -> "
                            f"{show(new)!r}" + ("" if old == new else "; its content is put back"))
            if old != new:
                try:
                    if old is None:
                        os.remove(_POINTER)
                    else:
                        tmp = _POINTER + ".guard-restore"
                        with open(tmp, "wb") as f:
                            f.write(old)
                        os.replace(tmp, _POINTER)
                except OSError as exc:
                    problems.append(f"could not put the pointer back: {exc}")
        # after the put-back, so the next test starts clean. The whole walk at every check: an entry
        # added or removed is seen by the names, a file replaced by its stats, never only through a
        # folder's mtime (NTFS moves it lazily, by coarse ticks: an entry added in the tick of the last
        # check was blamed on the next test that moved a folder); the mtime stays a further hint
        tree = _tree_state()
        changes = _tree_changes(self.tree, tree, pointer != self.pointer)
        if changes:
            problems.append(f"the real projects folder {_PROJECTS} was written: " + "; ".join(changes)
                            + " (if the live app runs on this folder now, it may be its own: re-run alone)")
        self.pointer, self.tree = _pointer_state(), tree
        return problems


# taken when this conftest is imported, before collection: every later write is someone's
_GUARD = _ProjectsGuard()
_CAUGHT = []


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    rep = yield
    # after the call (writes from setup or the test) and after teardown (fixture teardowns,
    # module fixtures ending with the module's last test): the test fails by name
    if call.when in ("call", "teardown"):
        problems = _GUARD.check()
        if problems:
            _CAUGHT.append(f"{item.nodeid} ({call.when}): " + " | ".join(problems))
            text = "t-060 guard: " + "\n".join(problems)
            if rep.failed:
                rep.sections.append(("t-060 guard", text))
            else:
                rep.outcome, rep.longrepr = "failed", text
    return rep


def pytest_sessionfinish(session, exitstatus):
    # a write after the last test (a session fixture, a thread left running) fails the run
    problems = _GUARD.check()
    if problems:
        _CAUGHT.append("after the last test: " + " | ".join(problems))
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter):
    if _CAUGHT:
        terminalreporter.section("t-060 guard: the real projects folder was written")
        for line in _CAUGHT:
            terminalreporter.line("CAUGHT " + line)


# ---------------------------------------------------------------------------
# t-060 (W1g) guard: no test may send a datagram to a real OSC target.
#
# 127.0.0.1:9000 is the app's default OSC target and, on the show laptop, where TouchDesigner
# listens: one run of the suite there sent 15 /walldance/clear to it (apps the tests built with
# the default target, reset), i.e. cleared every dancer in the patch 15 times. The guard sits at
# the two doors a datagram leaves the process by, for the whole run (a client or a socket built
# at import, in a module fixture or in a thread is covered too):
# - every OSC client (pythonosc's UDPClient, which the app's OSCSender wraps) is a RECORDER: what
#   it sends is kept here, whatever the target, and nothing leaves; the test is not failed for it
#   (except a send to a listener the test opened itself: that test wanted the wire, see below);
# - any other UDP send (sendto / send / sendall / sendmsg on a datagram socket: the readiness
#   probe ops_monitor.probe_osc_udp) is refused with an OSError and the test FAILS by name.
# A test that really needs the wire opts out with @pytest.mark.osc_socket or the osc_listener
# fixture, and then sends only to a 127.0.0.1 port that a socket it opened itself got from the
# kernel (bind to port 0, so never 9000): any other target still fails it. A send to the limited
# broadcast address without SO_BROADCAST raises PermissionError as every kernel does (nothing
# leaves either way): the OSC send-failure test keeps its meaning. Subprocesses are not covered
# (none of the suite's children sends OSC: replay.py runs with OSC off).
# ---------------------------------------------------------------------------
import collections
import errno
import socket
import threading

from pythonosc import udp_client as _osc_udp

_OSC_MARK = "osc_socket"
_OSC_FIXTURE = "osc_listener"
# Tests that send to their own loopback listener and pre-date the guard; their files are not
# the guard's to edit, so their opt-out is given here (by file and test name).
_OSC_OPTED_OUT = {
    ("test_osc_output.py", "test_frame_wire_format_on_loopback"),
    ("test_identity_slots_pipeline.py", "test_state_message_wire_format"),
    ("test_ops_monitor.py", "test_probe_osc_loopback_listener_replies_or_silent"),
    ("test_ops_monitor.py", "test_probe_osc_loopback_closed_port_fails"),
}
_OSC_NO_OPT_OUT = (f"no opt-out: a test sends UDP only with @pytest.mark.{_OSC_MARK} or the {_OSC_FIXTURE} "
                   "fixture, and only to a 127.0.0.1 port its own socket got from bind(port 0)")
_OSC_NOT_OWN = "opted out, but only to a 127.0.0.1 port its own socket got from bind(port 0)"


class OscSendRefused(OSError):
    """A UDP send the guard refused: nothing was sent."""


def _osc_dest(dest):
    return f"{dest[0]}:{dest[1]}" if isinstance(dest, tuple) and len(dest) >= 2 else repr(dest)


class _OscGuard:
    def __init__(self):
        self.lock = threading.Lock()
        self.nodeid, self.open, self.ports = None, False, set()
        self.refused = collections.Counter()     # lines since the last check
        self.recorded = {}                       # nodeid -> Counter[(dest, osc address)]

    def start(self, nodeid, opted_out):
        with self.lock:
            self.nodeid, self.open, self.ports = nodeid, opted_out, set()

    def own(self, dest):
        return (isinstance(dest, tuple) and len(dest) >= 2 and dest[0] == "127.0.0.1"
                and dest[1] in self.ports)

    def refuse(self, what, dest, why):
        line = f"t-060 OSC guard: {what} to {_osc_dest(dest)} refused, nothing sent - {why}"
        with self.lock:
            self.refused[line] += 1
        raise OscSendRefused(line)

    def record(self, dest, dgram):
        address = bytes(dgram).split(b"\0", 1)[0].decode("ascii", "replace")[:60]
        with self.lock:
            self.recorded.setdefault(self.nodeid or "(outside a test)", collections.Counter())[
                (_osc_dest(dest), address)] += 1

    def take(self):
        with self.lock:
            lines = [ln + ("" if n == 1 else f" (x{n})") for ln, n in self.refused.items()]
            self.refused.clear()
        return lines


_OSC = _OscGuard()
_OSC_CAUGHT = []

# door 1: every OSC client is a recorder
_osc_send = _osc_udp.UDPClient.send


def _guarded_osc_send(self, content):
    dest = (self._address, self._port)
    if _OSC.open:
        if _OSC.own(dest):
            return _osc_send(self, content)
        _OSC.refuse("an OSC client send", dest, _OSC_NOT_OWN)
    if _OSC.own(dest):
        _OSC.refuse("an OSC client send", dest, _OSC_NO_OPT_OUT)
    if dest[0] == "255.255.255.255" and not self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST):
        raise PermissionError(errno.EACCES, "Permission denied")
    _OSC.record(dest, content.dgram)


_osc_udp.UDPClient.send = _guarded_osc_send

# door 2: any other UDP send; and the ports a test's own sockets get from bind(port 0)
_sock_orig = {n: getattr(socket.socket, n) for n in ("sendto", "send", "sendall", "sendmsg", "bind")
              if hasattr(socket.socket, n)}


def _is_udp(sock):
    return sock.family in (socket.AF_INET, socket.AF_INET6) and sock.type == socket.SOCK_DGRAM


def _udp_check(sock, dest):
    if dest is not None and not (_OSC.open and _OSC.own(dest)):
        _OSC.refuse("a UDP send", dest, _OSC_NOT_OWN if _OSC.open else _OSC_NO_OPT_OUT)


def _peer(sock):
    try:
        return sock.getpeername()
    except OSError:          # not connected: the kernel refuses the send itself
        return None


def _guarded_sendto(self, data, *args):
    if _is_udp(self):
        _udp_check(self, args[-1] if args else None)
    return _sock_orig["sendto"](self, data, *args)


def _guarded_send(self, data, *args):
    if _is_udp(self):
        _udp_check(self, _peer(self))
    return _sock_orig["send"](self, data, *args)


def _guarded_sendall(self, data, *args):
    if _is_udp(self):
        _udp_check(self, _peer(self))
    return _sock_orig["sendall"](self, data, *args)


def _guarded_sendmsg(self, buffers, *args):
    if _is_udp(self):
        _udp_check(self, args[2] if len(args) >= 3 and args[2] is not None else _peer(self))
    return _sock_orig["sendmsg"](self, buffers, *args)


def _guarded_bind(self, address):
    out = _sock_orig["bind"](self, address)
    if _is_udp(self) and isinstance(address, tuple) and address[:2] == ("127.0.0.1", 0):
        with _OSC.lock:
            _OSC.ports.add(self.getsockname()[1])
    return out


for _name, _fn in (("sendto", _guarded_sendto), ("send", _guarded_send), ("sendall", _guarded_sendall),
                   ("sendmsg", _guarded_sendmsg), ("bind", _guarded_bind)):
    if _name in _sock_orig:
        setattr(socket.socket, _name, _fn)


@pytest.fixture
def osc_listener():
    """A UDP listener on an ephemeral 127.0.0.1 port (``.getsockname()[1]``), 2 s timeout. Using it
    opts the test out of the OSC guard, for the ports its own sockets got from bind(port 0)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(2.0)
    yield s
    s.close()


@pytest.fixture
def osc_recorded():
    """What OSC clients sent during this test (recorded, never sent): call it for a Counter of
    ("host:port", OSC address) -> count, so far."""
    def get():
        with _OSC.lock:
            return collections.Counter(_OSC.recorded.get(_OSC.nodeid, {}))
    return get


class _OscGuardPlugin:
    def pytest_collection_modifyitems(self, items):
        for item in items:
            if (item.path.name, getattr(item, "originalname", item.name)) in _OSC_OPTED_OUT:
                item.add_marker(_OSC_MARK)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        _OSC.start(item.nodeid, item.get_closest_marker(_OSC_MARK) is not None
                   or _OSC_FIXTURE in getattr(item, "fixturenames", ()))
        try:
            return (yield)
        finally:
            _OSC.start(None, False)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item, call):
        rep = yield
        if call.when in ("call", "teardown"):
            lines = _OSC.take()
            if lines:
                _OSC_CAUGHT.append(f"{item.nodeid} ({call.when}): " + " | ".join(lines))
                text = "\n".join(lines)
                if rep.failed:
                    rep.sections.append(("t-060 OSC guard", text))
                else:
                    rep.outcome, rep.longrepr = "failed", text
        return rep

    def pytest_sessionfinish(self, session, exitstatus):
        lines = _OSC.take()      # a send after the last test (a thread left running)
        if lines:
            _OSC_CAUGHT.append("after the last test: " + " | ".join(lines))
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter):
        if _OSC_CAUGHT:
            terminalreporter.section("t-060 OSC guard: a test tried a real UDP send")
            for line in _OSC_CAUGHT:
                terminalreporter.line("CAUGHT " + line)
        with _OSC.lock:
            recorded = {k: collections.Counter(v) for k, v in _OSC.recorded.items()}
        if recorded:
            total = collections.Counter()
            for c in recorded.values():
                total.update(c)
            terminalreporter.line(
                f"t-060 OSC guard: {sum(total.values())} OSC datagram(s) from {len(recorded)} test(s) recorded, "
                "none sent: " + ", ".join(f"{a} -> {d} x{n}" for (d, a), n in total.most_common(4)))
            if terminalreporter.verbosity > 0:
                for nodeid, c in sorted(recorded.items()):
                    terminalreporter.line("  RECORDED " + nodeid + ": "
                                          + ", ".join(f"{a} -> {d} x{n}" for (d, a), n in c.most_common()))


def pytest_configure(config):
    config.addinivalue_line("markers", f"{_OSC_MARK}: the test really sends UDP, only to a 127.0.0.1 "
                                       "listener its own socket bound with port 0 (t-060 OSC guard)")
    if not config.pluginmanager.has_plugin("t060-osc-guard"):
        config.pluginmanager.register(_OscGuardPlugin(), "t060-osc-guard")
