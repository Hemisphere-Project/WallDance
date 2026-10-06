"""extra/wdslot.py — DEV-slot deploys, release-check, Windows launch scripts.

Deploys run end to end against a throwaway git repo and a fake laptop on this
machine (LocalTransport): incremental sync, deletions, shared-dir links,
manifest/DEPLOYED.json, syntax check. Windows-only pieces (scheduled task,
junctions) are checked as generated scripts.
"""
from __future__ import annotations

import importlib.util
import json
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


wr = _load("wdremote")
ws = _load("wdslot")


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    (r / "application" / "src" / "core").mkdir(parents=True)
    (r / "application" / "tests").mkdir()
    (r / "launcher" / "release").mkdir(parents=True)
    _git(tmp_path, "init", "-q", str(r))
    for k, v in (("user.email", "t@e"), ("user.name", "t")):
        _git(r, "config", k, v)
    (r / "application" / "src" / "app.py").write_text("print('v1')\n")
    (r / "application" / "src" / "core" / "old.py").write_text("x = 1\n")
    (r / "application" / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    (r / "run.sh").write_text("echo run\n")
    (r / "install.bat").write_text("rem v1\n")
    (r / "launcher" / "release" / "WallDanceLauncher.exe").write_bytes(b"MZ" * 1000)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "v1")
    v1 = _git(r, "rev-parse", "HEAD")
    (r / "application" / "src" / "app.py").write_text("print('v2')\n")
    (r / "application" / "src" / "core" / "old.py").unlink()
    (r / "application" / "src" / "core" / "new.py").write_text("y = 2\n")
    (r / "install.bat").write_text("rem v2\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "v2")
    v2 = _git(r, "rev-parse", "HEAD")
    monkeypatch.setattr(wr, "REPO", r)
    monkeypatch.setattr(wr, "RUNS_DIR", tmp_path / "runs")
    return r, v1, v2


@pytest.fixture
def laptop(tmp_path):
    live = tmp_path / "laptop" / "WallDance"
    (live / "application" / ".venv").mkdir(parents=True)
    (live / "models").mkdir()
    (live / "projects" / "show1").mkdir(parents=True)
    remote = wr.Remote(host="local", root=str(live), os="posix", python=sys.executable)
    return remote, wr.LocalTransport(remote), live


def _deploy(tr, remote, ref, **kw):
    args = wr.build_parser().parse_args(["deploy", ref] + [f"--{k.replace('_', '-')}"
                                                           for k, v in kw.items() if v])
    return ws.cmd_deploy(tr, remote, args)


def test_tree_files_excludes_old_exe(repo):
    _, v1, _ = repo
    files = ws.tree_files(v1)
    assert "application/src/app.py" in files
    assert not any(p.startswith("launcher/release/") for p in files)


def test_plan_sync():
    changed, removed = ws.plan_sync({"a": "1", "b": "2", "c": "3"}, {"a": "1", "b": "0", "d": "9"})
    assert changed == {"b": "2", "c": "3"} and removed == ["d"]


def test_deploy_incremental_with_links(repo, laptop, capsys):
    r, v1, v2 = repo
    remote, tr, live = laptop
    dev = Path(ws.dev_root(remote))
    assert _deploy(tr, remote, v1) == 0
    assert (dev / "application" / "src" / "core" / "old.py").read_text() == "x = 1\n"
    assert (dev / "application" / ".venv").resolve() == (live / "application" / ".venv").resolve()
    assert (dev / "models").is_symlink() and (dev / "projects" / "show1").is_dir()
    dep = json.loads((dev / "DEPLOYED.json").read_text())
    assert dep["commit"] == v1 and dep["ref"] == v1

    capsys.readouterr()
    assert _deploy(tr, remote, v2) == 0
    out = capsys.readouterr().out
    assert "3 file(s) to write, 1 to delete" in out          # app.py, new.py, install.bat
    assert not (dev / "application" / "src" / "core" / "old.py").exists()
    assert (dev / "application" / "src" / "app.py").read_text() == "print('v2')\n"
    # same ref again: nothing to send
    capsys.readouterr()
    assert _deploy(tr, remote, v2) == 0
    assert "0 file(s) to write, 0 to delete" in capsys.readouterr().out
    # the version stamp of a plain-tree slot comes from DEPLOYED.json
    from core.version import app_version
    v = app_version.__wrapped__(str(dev))
    assert v["commit"] == v2[:12] and v["slot"] == "dev"


def test_deploy_reports_syntax_errors(repo, laptop):
    r, _, _ = repo
    remote, tr, _ = laptop
    (r / "application" / "src" / "broken.py").write_text("def x(:\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "broken")
    assert _deploy(tr, remote, "HEAD") == 1


def test_release_check_flags_reinstall(repo, capsys):
    r, v1, v2 = repo
    args = wr.build_parser().parse_args(["release-check", v2, "--base", v1])
    assert ws.cmd_release_check(None, None, args) == 0
    out = capsys.readouterr().out
    assert "1 commit(s) ahead, 0 behind (FAST-FORWARD" in out
    assert "REINSTALL" in out and "install.bat" in out
    assert f"git push origin {v2}:refs/heads/release" in out


def test_windows_launch_script_is_interactive_task():
    script = ws.windows_launch_script("WallDance-dev", "C:/WallDance/WallDance-dev",
                                      ["--project", "show1", "--slot", "3"],
                                      {"WD_SLOT": "dev", "WD_REMOTE_ALLOW_QUIT": "1"})
    assert "-LogonType Interactive" in script and "Start-ScheduledTask -TaskName 'WallDance-dev'" in script
    assert 'cd /d "C:\\WallDance\\WallDance-dev"' in script
    assert "call run.bat --project show1 --slot 3" in script
    assert 'set "WD_REMOTE_ALLOW_QUIT=1"&&' in script
    enc = ws._ps_encoded(script)
    assert enc[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand"]


def test_launcher_dir_and_dev_root():
    remote = wr.Remote(host="h", root="C:/WallDance/WallDance")
    assert ws.launcher_dir(remote) == "C:/WallDance"
    assert ws.dev_root(remote) == "C:/WallDance/WallDance-dev"


def test_remote_quit_only_for_dev_slot_in_standby():
    from services.remote_api import check_policy
    assert check_policy("Quit", {}, "standby", False) is not None          # live app
    assert check_policy("Quit", {}, "standby", False, allow_quit=True) is None
    assert check_policy("Quit", {}, "run", True, allow_quit=True) is not None


def test_dash_args_pass_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(wr, "load_remote", lambda **kw: wr.Remote(host="h", root="/r", os="posix"))
    monkeypatch.setattr(wr, "cmd_pytest", lambda tr, remote, a: seen.setdefault("pytest", a.args) and 0)
    monkeypatch.setattr(ws, "cmd_slot_run",
                        lambda tr, remote, a: seen.setdefault("slot", a.args) and 0)
    wr.main(["pytest", "-x", "-k", "rig"])
    wr.main(["slot", "run", "--", "--project", "p", "--slot", "3"])
    assert seen["pytest"] == ["-x", "-k", "rig"]
    assert seen["slot"] == ["--project", "p", "--slot", "3"]
