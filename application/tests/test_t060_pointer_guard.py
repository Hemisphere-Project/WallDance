"""t-060: the test suite never writes the shared last-project pointer.

On the laptop the DEV slot's projects folder is a junction to the live one: a store a test
built on a temp folder still wrote the real projects/last_project.txt, which the live app
and its launcher read to preselect a project. Two ends: a ConfigStore on any folder other
than the default keeps its pointer inside that folder (the default is unchanged), and
tests/conftest.py fails, by name, any test that writes the real pointer or the real
projects tree. The last test here runs the real conftest on a fake workspace and checks
that it catches deliberately bad tests -- never the real pointer.
"""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from core import config_store
from core.config_store import ConfigStore


def _state(path):
    try:
        return Path(path).read_bytes(), os.stat(path).st_mtime_ns
    except OSError:
        return None, None


def test_store_on_another_folder_keeps_its_own_pointer(tmp_path):
    before = _state(config_store.LAST_PROJECT_FILE)
    store = ConfigStore(config_dir=str(tmp_path))
    assert store.last_project_file == os.path.join(str(tmp_path), "last_project.txt")
    path = store.save("w1-probe", {"model": "yolo11l-pose", "confidence": 0.3})
    assert os.path.dirname(os.path.dirname(path)) == str(tmp_path)
    assert (tmp_path / "last_project.txt").read_text(encoding="utf-8") == "w1-probe"
    assert store.read_last_project() == "w1-probe"
    assert _state(config_store.LAST_PROJECT_FILE) == before        # content and mtime


def test_default_store_still_points_at_the_shared_pointer(monkeypatch):
    made = []
    monkeypatch.setattr(config_store.os, "makedirs", lambda d, **kw: made.append(d))  # create nothing
    store = ConfigStore()
    assert store.config_dir == config_store.PROJECTS_DIR
    assert store.last_project_file == config_store.LAST_PROJECT_FILE
    assert made == [config_store.PROJECTS_DIR]
    # the default folder named explicitly is the default; an explicit pointer is kept as given
    assert ConfigStore(config_dir=config_store.PROJECTS_DIR).last_project_file == config_store.LAST_PROJECT_FILE
    assert ConfigStore(config_dir="elsewhere", last_project_file="p.txt").last_project_file == "p.txt"


_BAD_TESTS = '''
import os, subprocess, sys
from core.config_store import ConfigStore, LAST_PROJECT_FILE, PROJECTS_DIR

def test_clean(tmp_path):
    ConfigStore(config_dir=str(tmp_path)).save("rehearsal-cordee", {"a": 1})

def test_store_writes_pointer():
    ConfigStore().remember_last_project("rehearsal-cordee")

def test_path_writes_pointer():
    with open(LAST_PROJECT_FILE, "w") as f:
        f.write("by-path")

def test_subprocess_writes_pointer():
    subprocess.run([sys.executable, "-c", "open(%r, 'w').write('from-a-child')" % LAST_PROJECT_FILE], check=True)

def test_adds_project_folder():
    os.mkdir(os.path.join(PROJECTS_DIR, "new-proj"))

def test_replaces_plate():
    plate = os.path.join(PROJECTS_DIR, "proj-a", "plates", "live.npz")
    with open(plate + ".tmp", "wb") as f:
        f.write(b"new")
    os.replace(plate + ".tmp", plate)

def test_after():
    pass
'''


def test_guard_fails_a_test_that_writes_the_pointer(tmp_path):
    here = Path(__file__).resolve().parent
    ws = tmp_path / "ws"                                     # a workspace of its own: app + projects
    (ws / "application" / "tests").mkdir(parents=True)
    (ws / "application" / "src" / "core").mkdir(parents=True)
    shutil.copy(here / "conftest.py", ws / "application" / "tests" / "conftest.py")
    shutil.copy(config_store.__file__, ws / "application" / "src" / "core" / "config_store.py")
    (ws / "application" / "src" / "core" / "__init__.py").write_text("")
    (ws / "application" / "tests" / "test_bad.py").write_text(textwrap.dedent(_BAD_TESTS))
    (ws / "pytest.ini").write_text("[pytest]\n")
    plates = ws / "projects" / "proj-a" / "plates"
    plates.mkdir(parents=True)
    (plates / "live.npz").write_bytes(b"old")
    pointer = ws / "projects" / "last_project.txt"
    pointer.write_text("sentinel")
    real_before = _state(config_store.LAST_PROJECT_FILE)

    run = subprocess.run([sys.executable, "-m", "pytest", "application/tests", "-q", "-rA",
                          "-p", "no:cacheprovider", "-c", str(ws / "pytest.ini"), "--rootdir", str(ws)],
                         cwd=ws, capture_output=True, text=True, timeout=180)
    out = run.stdout + run.stderr
    assert run.returncode == 1, out
    for name in ("test_store_writes_pointer", "test_path_writes_pointer", "test_subprocess_writes_pointer",
                 "test_adds_project_folder", "test_replaces_plate"):
        assert f"FAILED application/tests/test_bad.py::{name}" in out, out
        assert f"CAUGHT application/tests/test_bad.py::{name} (call)" in out, out
    for name in ("test_clean", "test_after"):               # a later test is not blamed
        assert f"PASSED application/tests/test_bad.py::{name}" in out, out
    assert "t-060 guard" in out and "'rehearsal-cordee'" in out and "new-proj" in out
    assert "plates: a file in it was replaced" in out
    assert pointer.read_text() == "sentinel"                 # its content was put back each time
    assert _state(config_store.LAST_PROJECT_FILE) == real_before


# A folder's mtime proves nothing about its entries: on NTFS it moves lazily and by coarse ticks
# (the laptop, 27/11: a project folder added in the tick of the previous check PASSED, and the
# guard blamed the next test that moved a folder). This plugin gives the lazy case on any OS: every
# folder under the workspace's projects keeps, for os.stat, the mtime it had when first looked at.
_LAZY_MTIME = '''
import os, stat
_real_stat = os.stat
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects")
_first = {}

def _lazy_stat(path, *args, **kwargs):
    st = _real_stat(path, *args, **kwargs)
    p = None if isinstance(path, int) or kwargs.get("dir_fd") is not None else os.fspath(path)
    if not isinstance(p, str) or not stat.S_ISDIR(st.st_mode):
        return st
    p = os.path.abspath(p)
    if p != _ROOT and not p.startswith(_ROOT + os.sep):
        return st
    ns = _first.setdefault(p, st.st_mtime_ns)
    seq = list(st[:os.stat_result.n_sequence_fields])
    seq[8] = ns // 10**9
    named = {n: getattr(st, n) for n in dir(st) if n.startswith("st_")}
    named.update(st_mtime=ns / 1e9, st_mtime_ns=ns)
    return os.stat_result(seq, named)

os.stat = _lazy_stat
'''

_LAZY_TESTS = '''
import os
from core.config_store import PROJECTS_DIR

def test_clean():
    pass

def test_adds_project_folder():
    before = os.stat(PROJECTS_DIR).st_mtime_ns
    os.mkdir(os.path.join(PROJECTS_DIR, "new-proj"))
    print("lazy mtime held:", os.stat(PROJECTS_DIR).st_mtime_ns == before)

def test_removes_config():
    os.remove(os.path.join(PROJECTS_DIR, "proj-a", "configs", "v1.json"))

def test_replaces_plate():
    plate = os.path.join(PROJECTS_DIR, "proj-a", "plates", "live.npz")
    with open(plate + ".tmp", "wb") as f:
        f.write(b"new")
    os.replace(plate + ".tmp", plate)

def test_after():
    pass
'''


def test_guard_fails_the_test_that_added_an_entry_though_folder_mtimes_lag(tmp_path):
    here = Path(__file__).resolve().parent
    ws = tmp_path / "ws"
    (ws / "application" / "tests").mkdir(parents=True)
    (ws / "application" / "src" / "core").mkdir(parents=True)
    shutil.copy(here / "conftest.py", ws / "application" / "tests" / "conftest.py")
    shutil.copy(config_store.__file__, ws / "application" / "src" / "core" / "config_store.py")
    (ws / "application" / "src" / "core" / "__init__.py").write_text("", encoding="utf-8")
    (ws / "application" / "tests" / "test_lazy.py").write_text(textwrap.dedent(_LAZY_TESTS), encoding="utf-8")
    (ws / "lazy_mtime.py").write_text(textwrap.dedent(_LAZY_MTIME), encoding="utf-8")
    (ws / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    for sub, name, data in (("plates", "live.npz", b"old"), ("configs", "v1.json", b"{}")):
        (ws / "projects" / "proj-a" / sub).mkdir(parents=True)
        (ws / "projects" / "proj-a" / sub / name).write_bytes(data)
    real_before = _state(config_store.LAST_PROJECT_FILE)

    # -p loads the plugin before the conftest: the guard's first look already sees frozen mtimes
    run = subprocess.run([sys.executable, "-m", "pytest", "-p", "lazy_mtime", "application/tests", "-q", "-rA",
                          "-p", "no:cacheprovider", "-c", str(ws / "pytest.ini"), "--rootdir", str(ws)],
                         cwd=ws, capture_output=True, text=True, timeout=180)
    out = run.stdout + run.stderr
    assert "lazy mtime held: True" in out, out              # the folder's mtime did not move
    assert run.returncode == 1, out
    for name in ("test_adds_project_folder", "test_removes_config", "test_replaces_plate"):
        assert f"FAILED application/tests/test_lazy.py::{name}" in out, out
        assert f"CAUGHT application/tests/test_lazy.py::{name} (call)" in out, out
    for name in ("test_clean", "test_after"):               # a later test is not blamed
        assert f"PASSED application/tests/test_lazy.py::{name}" in out, out
    assert ".: added ['new-proj'] removed []" in out
    assert "configs: added [] removed ['v1.json']" in out
    assert "plates: a file in it was replaced" in out
    assert _state(config_store.LAST_PROJECT_FILE) == real_before
