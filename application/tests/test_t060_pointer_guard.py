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
