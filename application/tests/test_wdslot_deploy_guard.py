"""extra/wdslot.py deploy — the whole tree wherever the tools sit, and the guard.

27/11 09:21: `wdremote.py deploy 1e53974` run from a plain export sitting untracked
inside a clone listed the commit with `git -C <export> ls-tree` (only that sub-folder
of the commit: nothing), planned « 0 file(s) to write, 779 to delete » and emptied
the DEV slot. Here: the listing is the whole tree from any folder, and a tree that is
not the app, or a plan that would leave the slot without it, is refused before the
slot is touched (dry run too). Throwaway repos and a fake laptop (LocalTransport).
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_EXTRA = Path(__file__).resolve().parents[2] / "extra"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _EXTRA / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Reuse the copies test_wdslot.py already loaded: a second copy would rebind
# sys.modules["wdslot"], and its test_dash_args_pass_through (which patches its own
# copy) would then run the real `slot run` over ssh.
if "wdremote" not in sys.modules:
    _load("wdremote")
if "wdslot" not in sys.modules:
    _load("wdslot")
ws = sys.modules["wdslot"]
wr = ws.wr

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    for k in ("GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(k, raising=False)
    r = tmp_path / "clone"
    (r / "application" / "src" / "core").mkdir(parents=True)
    (r / "application" / "tests").mkdir()
    (r / "docs").mkdir()
    _git(tmp_path, "init", "-q", str(r))
    for k, v in (("user.email", "t@e"), ("user.name", "t")):
        _git(r, "config", k, v)
    (r / "run.sh").write_text("echo run\n")
    (r / "run.bat").write_text("rem run\n")
    (r / "application" / "src" / "main.py").write_text("print('main')\n")
    (r / "application" / "src" / "core" / "a.py").write_text("a = 1\n")
    (r / "application" / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    (r / "docs" / "README.md").write_text("doc\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "app")
    app = _git(r, "rev-parse", "HEAD")
    # a commit of the same repo that is not the app, and one with an empty tree
    blob = _git(r, "hash-object", "-w", "docs/README.md")
    sub = subprocess.run(["git", "-C", str(r), "mktree"], input=f"100644 blob {blob}\tREADME.md\n",
                         check=True, capture_output=True, text=True).stdout.strip()
    docs_tree = subprocess.run(["git", "-C", str(r), "mktree"], input=f"040000 tree {sub}\tdocs\n",
                               check=True, capture_output=True, text=True).stdout.strip()
    no_app = _git(r, "commit-tree", docs_tree, "-m", "docs only")
    empty = _git(r, "commit-tree", EMPTY_TREE, "-m", "empty")
    # the 27/11 shape: a plain export of the commit, untracked, inside the clone
    nested = r / "tmp_analysis" / "r2" / "snap-app"
    (nested / "application" / "src").mkdir(parents=True)
    (nested / "application" / "src" / "main.py").write_text("print('main')\n")
    monkeypatch.setattr(wr, "REPO", r)
    monkeypatch.setattr(wr, "RUNS_DIR", tmp_path / "runs")
    return r, nested, app, no_app, empty


@pytest.fixture
def laptop(tmp_path):
    if os.name == "nt":
        pytest.skip("LocalTransport emulates the laptop with a posix shell (bash)")
    live = tmp_path / "laptop" / "WallDance"
    (live / "application" / ".venv").mkdir(parents=True)
    (live / "models").mkdir()
    (live / "projects" / "show1").mkdir(parents=True)
    remote = wr.Remote(host="local", root=str(live), os="posix", python=sys.executable)
    return remote, wr.LocalTransport(remote), live


def _deploy(tr, remote, ref, *flags):
    args = wr.build_parser().parse_args(["deploy", ref, *flags])
    return ws.cmd_deploy(tr, remote, args)


def _snapshot(root: Path) -> dict:
    """Every entry under ``root``: kind, size, mtime, content hash or link target."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            st = p.lstat()
            if p.is_symlink():
                out[str(p.relative_to(root))] = ("link", os.readlink(p))
            elif p.is_dir():
                out[str(p.relative_to(root))] = ("dir", st.st_mtime_ns)
            else:
                out[str(p.relative_to(root))] = ("file", st.st_size, st.st_mtime_ns,
                                                 hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def test_listing_is_the_whole_tree_from_a_nested_export(repo, monkeypatch):
    r, nested, app, _, _ = repo
    whole = ws.tree_files(app)
    assert {"run.sh", "application/src/main.py", "docs/README.md"} <= set(whole)
    monkeypatch.setattr(wr, "REPO", nested)            # the tools sit in the export
    assert ws.tree_files(app) == whole
    monkeypatch.chdir(nested / "application" / "src")  # and the shell stands anywhere
    assert ws.tree_files(app) == whole
    assert "sub-folder of the clone" in ws.repo_note()
    monkeypatch.setattr(wr, "REPO", r)
    assert ws.repo_note() == ""                        # a normal run says nothing more


def test_deploy_from_nested_export_plans_like_from_the_top(repo, laptop, monkeypatch, capsys):
    r, nested, app, _, _ = repo
    remote, tr, live = laptop
    assert _deploy(tr, remote, app) == 0
    capsys.readouterr()
    monkeypatch.setattr(wr, "REPO", nested)
    assert _deploy(tr, remote, app, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "0 file(s) to write, 0 to delete" in out and "sub-folder of the clone" in out
    assert _deploy(tr, remote, app) == 0
    dev = Path(ws.dev_root(remote))
    assert (dev / "application" / "src" / "main.py").is_file() and (dev / "run.sh").is_file()


@pytest.mark.parametrize("which, words", [("empty", "lists no file"),
                                          ("no_app", "not the WallDance app")])
@pytest.mark.parametrize("dry", [True, False])
def test_tree_that_is_not_the_app_is_refused_slot_untouched(repo, laptop, capsys, which, words, dry):
    r, _, app, no_app, empty = repo
    remote, tr, live = laptop
    assert _deploy(tr, remote, app) == 0
    before = _snapshot(live.parent)
    capsys.readouterr()
    ref = {"empty": empty, "no_app": no_app}[which]
    assert _deploy(tr, remote, ref, *(["--dry-run"] if dry else [])) == ws.DEPLOY_REFUSED
    out = capsys.readouterr().out
    refused = [ln for ln in out.splitlines() if "refused" in ln]
    assert len(refused) == 1 and words in refused[0] and "nothing deleted" in refused[0]
    assert "to delete" not in out                      # refused before any plan
    assert _snapshot(live.parent) == before


@pytest.mark.parametrize("dry", [True, False])
def test_plan_that_would_delete_the_app_is_refused(repo, laptop, monkeypatch, capsys, dry):
    r, _, app, _, _ = repo
    remote, tr, live = laptop
    assert _deploy(tr, remote, app) == 0
    before = _snapshot(live.parent)
    capsys.readouterr()
    # a planner gone wrong: delete everything the slot holds, write nothing
    monkeypatch.setattr(ws, "plan_sync", lambda new, old: ({}, sorted(old)))
    assert _deploy(tr, remote, app, *(["--dry-run"] if dry else [])) == ws.DEPLOY_REFUSED
    out = capsys.readouterr().out
    assert "refused: the plan would leave the slot without the app" in out
    assert _snapshot(live.parent) == before


def test_tools_outside_any_clone_say_so(repo, laptop, tmp_path, monkeypatch, capsys):
    _, _, app, _, _ = repo
    remote, tr, live = laptop
    plain = tmp_path / "plain-export"
    plain.mkdir()
    monkeypatch.setattr(wr, "REPO", plain)
    assert _deploy(tr, remote, app, "--dry-run") == ws.DEPLOY_REFUSED
    out = capsys.readouterr().out
    assert "not inside a git clone" in out and not (live.parent / "WallDance-dev").exists()


def test_unknown_ref_is_one_plain_line(repo, laptop, capsys):
    remote, tr, live = laptop
    assert _deploy(tr, remote, "no-such-ref", "--dry-run") == ws.DEPLOY_REFUSED
    out = capsys.readouterr().out
    assert "no-such-ref is not a commit of the clone" in out and "Traceback" not in out


def test_deploy_refusal_rules():
    app = {"run.bat": "1", "run.sh": "2", "application/src/main.py": "3"}
    assert ws.deploy_refusal(app, win=True) == ""
    assert "lists no file" in ws.deploy_refusal({}, win=True)
    assert "no run.bat" in ws.deploy_refusal({"run.sh": "2", "application/src/main.py": "3"}, win=True)
    assert "no application/src/*.py" in ws.deploy_refusal({"run.bat": "1"}, win=True)
    # the plan: the slot keeps what it has minus the deletions, plus the writes
    assert ws.deploy_refusal(app, True, changed={}, removed=[], old=app) == ""
    assert "leave the slot without the app" in ws.deploy_refusal(
        app, True, changed={}, removed=sorted(app), old=app)
