"""Launcher git-update safety (ROADMAP §6).

The launcher must never destroy local work: an update is refused while
tracked files have local modifications, a local-ahead checkout is never
offered an update, and a diverged checkout only updates behind an explicit
destructive confirmation (the prompt itself lives in launcher/gui.py).

dulwich ships with the launcher, not the application venv — skip cleanly
where it is absent.
"""
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("dulwich")

from dulwich import porcelain  # noqa: E402
from dulwich.repo import Repo  # noqa: E402

_LAUNCHER_DIR = str(Path(__file__).resolve().parents[2] / "launcher")
if _LAUNCHER_DIR not in sys.path:
    sys.path.insert(0, _LAUNCHER_DIR)

from git_manager import (  # noqa: E402
    DirtyWorkingTreeError,
    GitManager,
    UpdateStatus,
)

_IDENT = b"Test <test@example.com>"


def _commit(repo_path, relpath, content, message):
    """Write a file and commit it with a fixed identity (CI has no git config)."""
    path = os.path.join(repo_path, relpath)
    os.makedirs(os.path.dirname(path) or repo_path, exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(content)
    porcelain.add(repo_path, paths=[path])
    return porcelain.commit(
        repo_path, message=message.encode(), author=_IDENT, committer=_IDENT
    )


def _write(repo_path, relpath, content):
    path = os.path.join(repo_path, relpath)
    os.makedirs(os.path.dirname(path) or repo_path, exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(content)
    return path


def _head(repo_path):
    with Repo(repo_path) as repo:
        return repo.head()


@pytest.fixture
def remote(tmp_path):
    """A 'server' repo on the main branch with a few commits."""
    remote_dir = str(tmp_path / "remote")
    os.makedirs(remote_dir)
    repo = porcelain.init(remote_dir)
    # dulwich init has no default-branch parameter; check_updates expects main
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    repo.close()
    _commit(remote_dir, "README.md", "hello\n", "init")
    _commit(remote_dir, "install.bat", "rem install\n", "install script")
    # Tracked runtime-junk file (mirrors the real repo's transition state)
    _commit(remote_dir, "application/merge_dbg.log", "old log\n", "junk")
    return remote_dir


@pytest.fixture
def manager(remote, tmp_path):
    """A field checkout cloned through the production clone path."""
    gm = GitManager(remote, str(tmp_path / "local"))
    gm.clone()
    return gm


# --- check_updates: ahead/behind/diverged classification -----------------

def test_up_to_date(manager):
    assert manager.check_updates() is UpdateStatus.UP_TO_DATE


def test_behind_when_remote_advances(manager, remote):
    _commit(remote, "new.txt", "new\n", "remote change")
    assert manager.check_updates() is UpdateStatus.BEHIND


def test_ahead_when_local_advances(manager):
    # The ROADMAP regression: this used to report "update available",
    # and updating would have discarded the local commit.
    _commit(manager.target_dir, "local.txt", "local\n", "local change")
    assert manager.check_updates() is UpdateStatus.AHEAD


def test_diverged_when_both_advance(manager, remote):
    _commit(remote, "remote.txt", "r\n", "remote change")
    _commit(manager.target_dir, "local.txt", "l\n", "local change")
    assert manager.check_updates() is UpdateStatus.DIVERGED


# --- dirty_files: what counts as dirty ------------------------------------

def test_dirty_files_clean_tree(manager):
    assert manager.dirty_files() == []


def test_dirty_files_sees_unstaged_edit(manager):
    _write(manager.target_dir, "README.md", "edited\n")
    assert manager.dirty_files() == ["README.md"]


def test_dirty_files_sees_staged_edit(manager):
    path = _write(manager.target_dir, "README.md", "edited\n")
    porcelain.add(manager.target_dir, paths=[path])
    assert manager.dirty_files() == ["README.md"]


def test_dirty_files_ignores_untracked(manager):
    # Field machines keep working data inside the tree; it must not block updates.
    _write(manager.target_dir, "models/big.engine", "binary-ish\n")
    _write(manager.target_dir, "projects/show/config.json", "{}\n")
    _write(manager.target_dir, "scratch.txt", "notes\n")
    assert manager.dirty_files() == []


def test_dirty_files_exempts_runtime_junk(manager):
    # Tracked-but-rewritten-at-runtime files would otherwise refuse updates
    # on every field machine (see _DIRTY_EXEMPT in git_manager).
    _write(manager.target_dir, "application/merge_dbg.log", "runtime noise\n")
    assert manager.dirty_files() == []


# --- update: refuse on dirty, apply when clean ----------------------------

def test_update_refuses_on_dirty_tree(manager, remote):
    _commit(remote, "new.txt", "new\n", "remote change")
    _write(manager.target_dir, "README.md", "precious local edit\n")
    head_before = _head(manager.target_dir)

    assert manager.check_updates() is UpdateStatus.BEHIND
    with pytest.raises(DirtyWorkingTreeError) as exc:
        manager.update()

    assert "README.md" in exc.value.files
    # Nothing was applied: HEAD unchanged, edit preserved, no new file.
    assert _head(manager.target_dir) == head_before
    with open(os.path.join(manager.target_dir, "README.md")) as f:
        assert f.read() == "precious local edit\n"
    assert not os.path.exists(os.path.join(manager.target_dir, "new.txt"))


def test_clean_update_fast_forwards(manager, remote):
    _commit(remote, "new.txt", "new\n", "remote change")
    assert manager.check_updates() is UpdateStatus.BEHIND

    needs_install = manager.update()

    assert needs_install is False
    assert os.path.exists(os.path.join(manager.target_dir, "new.txt"))
    assert _head(manager.target_dir) == _head(remote)


def test_update_flags_needs_install(manager, remote):
    _commit(remote, "install.bat", "rem install v2\n", "change installer")
    assert manager.check_updates() is UpdateStatus.BEHIND
    assert manager.update() is True


def test_update_applies_when_diverged_and_clean(manager, remote):
    _commit(remote, "remote.txt", "r\n", "remote change")
    _commit(manager.target_dir, "local.txt", "l\n", "local change")
    assert manager.check_updates() is UpdateStatus.DIVERGED

    manager.update()  # gui.py only reaches this behind the destructive prompt

    assert _head(manager.target_dir) == _head(remote)
    assert os.path.exists(os.path.join(manager.target_dir, "remote.txt"))
