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
    resolve_branch,
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
    # These tests predate the release channel: pin the channel to the
    # remote's main so their ahead/behind/diverged semantics are unchanged.
    gm = GitManager(remote, str(tmp_path / "local"), branch="main")
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


# --- release channel + update safety (audit 2026-10 ARCH-2/ARCH-19, D1) -----

def _commit_on(repo_path, branch, relpath, content, message):
    """Commit on another branch of a (non-bare) repo, then switch back."""
    with Repo(repo_path) as repo:
        prev = repo.refs.read_ref(b"HEAD")
        ref = b"refs/heads/" + branch.encode()
        if ref not in repo.refs:
            repo.refs[ref] = repo.head()
        repo.refs.set_symbolic_ref(b"HEAD", ref)
    sha = _commit(repo_path, relpath, content, message)
    with Repo(repo_path) as repo:
        repo.refs.set_symbolic_ref(b"HEAD", prev[len(b"ref: "):])
    return sha


def test_missing_release_branch_is_unknown(remote, tmp_path):
    gm = GitManager(remote, str(tmp_path / "local"))      # default channel: release
    gm.clone()                                             # falls back to the default branch
    assert gm.branch == "release"
    assert gm.check_updates() is UpdateStatus.UNKNOWN      # never moves until published


def test_release_switch_keeps_laptop_commits_on_main(remote, tmp_path):
    # Today's laptop: old launcher, checkout on main with a laptop-only commit.
    old = GitManager(remote, str(tmp_path / "local"), branch="main")
    old.clone()
    laptop_sha = _commit(old.target_dir, "laptop.txt", "field fix\n", "laptop-only work")
    # Thomas publishes the release branch (main + one commit).
    release_sha = _commit_on(remote, "release", "release.txt", "r1\n", "release 1")

    gm = GitManager(remote, old.target_dir, branch="release")
    assert gm.check_updates() is UpdateStatus.DIVERGED
    gm.update()

    with Repo(gm.target_dir) as repo:
        assert repo.refs.read_ref(b"HEAD") == b"ref: refs/heads/release"
        assert repo.head() == release_sha
        assert repo.refs[b"refs/heads/main"] == laptop_sha          # untouched
        assert repo.refs[gm.last_backup_ref.encode()] == laptop_sha  # + backup ref
    assert os.path.exists(os.path.join(gm.target_dir, "release.txt"))
    assert not os.path.exists(os.path.join(gm.target_dir, "laptop.txt"))  # still on main
    assert gm.current_version() == ("release", release_sha.decode()[:12])


def test_first_release_switch_from_main_fast_forward(remote, tmp_path):
    # The show laptop on 2026-10-08: LIVE on main (the old launcher's clone), no
    # local release ref, never fetched since; GitHub's release = main + commits (a
    # fast-forward), changing install.bat and adding the pinned requirements.
    old = GitManager(remote, str(tmp_path / "local"), branch="main")
    old.clone()
    main_sha = _head(old.target_dir)
    _write(old.target_dir, "projects/show/config.json", "{}\n")      # field data, untracked
    _commit_on(remote, "release", "install.bat", "rem pinned install\n", "pinned installer")
    release_sha = _commit_on(remote, "release", "application/requirements-prod.txt",
                             "numpy==1.26.4\n", "freeze the laptop's stack")
    with Repo(old.target_dir) as repo:
        assert b"refs/heads/release" not in repo.refs
        assert b"refs/remotes/origin/release" not in repo.refs

    gm = GitManager(remote, old.target_dir)          # the new exe: launcher.json -> release
    assert gm.branch == "release"
    assert gm.dirty_files() == []
    assert gm.check_updates() is UpdateStatus.BEHIND    # plain "update available" prompt
    assert gm.update() is True                           # install.bat changed: it runs once

    with Repo(gm.target_dir) as repo:
        assert repo.refs.read_ref(b"HEAD") == b"ref: refs/heads/release"
        assert repo.head() == release_sha
        assert repo.refs[b"refs/heads/main"] == main_sha               # untouched
        assert repo.refs[gm.last_backup_ref.encode()] == main_sha      # + backup ref
        assert repo.refs[b"refs/remotes/origin/release"] == release_sha
    with open(os.path.join(gm.target_dir, "install.bat")) as f:
        assert f.read() == "rem pinned install\n"
    assert os.path.exists(os.path.join(gm.target_dir, "application", "requirements-prod.txt"))
    assert os.path.exists(os.path.join(gm.target_dir, "projects", "show", "config.json"))
    assert gm.last_moved_aside == [] and gm.dirty_files() == []

    # The next start: nothing to update, so no second install.bat run.
    again = GitManager(remote, old.target_dir)
    assert again.check_updates() is UpdateStatus.UP_TO_DATE
    assert again.current_version() == ("release", release_sha.decode()[:12])


def test_update_moves_untracked_collision_aside(manager, remote):
    _write(manager.target_dir, "notes.txt", "precious untracked notes\n")
    _commit(remote, "notes.txt", "upstream notes\n", "upstream starts tracking notes.txt")
    assert manager.check_updates() is UpdateStatus.BEHIND
    manager.update()
    with open(os.path.join(manager.target_dir, "notes.txt")) as f:
        assert f.read() == "upstream notes\n"
    aside = [n for n in os.listdir(manager.target_dir) if n.startswith("notes.txt.wd-local-")]
    assert len(aside) == 1 and manager.last_moved_aside == ["notes.txt"]
    with open(os.path.join(manager.target_dir, aside[0])) as f:
        assert f.read() == "precious untracked notes\n"


def test_update_removes_files_deleted_upstream(manager, remote):
    porcelain.remove(remote, paths=[os.path.join(remote, "README.md")])
    porcelain.commit(remote, message=b"drop readme", author=_IDENT, committer=_IDENT)
    assert manager.check_updates() is UpdateStatus.BEHIND
    manager.update()
    assert not os.path.exists(os.path.join(manager.target_dir, "README.md"))


def test_resolve_branch(tmp_path, monkeypatch):
    monkeypatch.delenv("WALLDANCE_BRANCH", raising=False)
    assert resolve_branch(str(tmp_path)) == "release"
    (tmp_path / "launcher.json").write_text('{"branch": "field-2026"}')
    assert resolve_branch(str(tmp_path)) == "field-2026"
    monkeypatch.setenv("WALLDANCE_BRANCH", "main")
    assert resolve_branch(str(tmp_path)) == "main"
