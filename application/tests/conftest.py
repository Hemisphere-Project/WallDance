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
    """{folder: (mtime_ns, entry names)} for the projects folder and its folders ``depth``
    levels down (links followed: on the laptop and in the snapshots the projects are links)."""
    state, level = {}, [_PROJECTS]
    for k in range(depth + 1):
        nxt = []
        for d in level:
            try:
                mtime = os.stat(d).st_mtime_ns
                with os.scandir(d) as it:
                    entries = list(it)
            except OSError:
                state[d] = (None, frozenset())
                continue
            state[d] = (mtime, frozenset(e.name for e in entries))
            if k < depth:
                nxt += [e.path for e in entries if e.is_dir()]
        level = nxt
    return state


def _tree_changes(before, after, pointer_written):
    out = []
    for d in sorted(set(before) | set(after)):
        (m0, n0), (m1, n1) = before.get(d, (None, frozenset())), after.get(d, (None, frozenset()))
        rel = os.path.relpath(d, _PROJECTS)
        if n0 != n1:
            out.append(f"{rel}: added {sorted(n1 - n0)} removed {sorted(n0 - n1)}")
        elif m0 is not None and m1 is not None and m0 != m1 and not (d == _PROJECTS and pointer_written):
            out.append(f"{rel}: a file in it was replaced (same entries)")
    return out


class _ProjectsGuard:
    def __init__(self):
        self.pointer = _pointer_state()
        self.tree = _tree_state()

    def _moved(self):
        for d, (mtime, _) in self.tree.items():
            try:
                now = os.stat(d).st_mtime_ns
            except OSError:
                now = None
            if now != mtime:
                return True
        return False

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
        # after the put-back, so the next test starts clean; a full walk only when a folder moved
        tree = _tree_state() if pointer != self.pointer or self._moved() else self.tree
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
