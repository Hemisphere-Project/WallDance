"""extra/wdremote.py — dev-side remote tool for the prod laptop (REMOTE_OPS).

Runs the real agent + pull/resume logic against a fake 'remote' on this machine
(``LocalTransport``); the ssh/sftp process boundary is the only part not exercised.
Windows command-line building is checked as strings.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "wdremote", Path(__file__).resolve().parents[2] / "extra" / "wdremote.py")
wdremote = importlib.util.module_from_spec(_SPEC)
sys.modules["wdremote"] = wdremote
_SPEC.loader.exec_module(wdremote)


def _write(p: Path, data: bytes, mtime: float | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


@pytest.fixture
def fake_remote(tmp_path):
    root = tmp_path / "prod"
    old = time.time() - 90 * 86400
    proj = root / "projects" / "1_TANGO_HANGAR-texturedbg"
    _write(proj / "1_TANGO_HANGAR-texturedbg_20260701_120000.json", b'{"confidence": 0.4}')
    _write(proj / "recordings" / "slot_5_20260401_161146.avi", os.urandom(300_000), old)
    _write(proj / "recordings" / "slot_5_20260401_161146.avi.meta",
           b'{"actual_fps": 19.8, "frames": 10}', old)
    _write(proj / "recordings" / "slot_2_20250101_000000.avi", os.urandom(50_000), old)
    _write(proj / "sessions" / "20260710_slot5" / "session.json", b'{"slot": 5}')
    _write(proj / "sessions" / "20260710_slot5" / "events.jsonl", b'{"e":1}\n' * 500)
    mk = root / "projects" / "markers-0a"
    _write(mk / "recordings" / "slot_1_20261006_101010.avi", os.urandom(120_000))
    meta = json.dumps({"version": "8.4.61", "date": "2026-06-08", "imgsz": [1280, 1280],
                       "args": {"half": True}}).encode()
    _write(root / "models" / "yolo11x-pose_1280.engine",
           len(meta).to_bytes(4, "little") + meta + b"\0" * 64)
    remote = wdremote.Remote(host="local", root=str(root), os="posix",
                             python=sys.executable)
    return remote, wdremote.LocalTransport(remote), root


def test_windows_command_lines():
    r = wdremote.Remote(host="wd-prod", root="C:/WallDance/WallDance")
    assert r.path("application/tests") == r"C:\WallDance\WallDance\application\tests"
    assert r.sftp_path("projects/x/a b.avi") == "/C:/WallDance/WallDance/projects/x/a b.avi"
    assert r.python_exe() == r"C:\WallDance\WallDance\application\.venv\Scripts\python.exe"
    line = r.shell([r.python_exe(), "tests/replay.py", "--set", "confidence=0.35"],
                   cwd="application", env={"PYTHONIOENCODING": "utf-8"})
    assert line == ('set "PYTHONIOENCODING=utf-8" && cd /d C:\\WallDance\\WallDance\\application'
                    ' && C:\\WallDance\\WallDance\\application\\.venv\\Scripts\\python.exe'
                    ' tests/replay.py --set confidence=0.35')
    spaced = wdremote.Remote(host="h", root="C:/Users/Tango Nomade/WallDance")
    assert '"C:\\Users\\Tango Nomade\\WallDance\\application"' in spaced.shell(["x"], cwd="application")
    with pytest.raises(ValueError):
        wdremote._q_cmd('a"b')


def test_inventory_reads_projects_engines_and_meta(fake_remote):
    remote, tr, _ = fake_remote
    inv = wdremote.run_agent(tr, remote, "inventory", {})
    names = {p["name"]: p for p in inv["projects"]}
    tex = names["1_TANGO_HANGAR-texturedbg"]
    assert tex["configs"] == 1 and tex["sessions"] == 1 and tex["issues"] == 0
    rec = {r["file"]: r for r in tex["recordings"]}
    assert rec["slot_5_20260401_161146.avi"]["meta"]["actual_fps"] == 19.8
    assert "slot_5_20260401_161146.avi.meta" not in rec
    eng = inv["engines"][0]
    assert eng["name"] == "yolo11x-pose_1280.engine"
    assert eng["ultralytics"] == "8.4.61" and eng["half"] is True
    assert "git" in inv and inv["disk_free_gb"] > 0


def test_plan_tiers(fake_remote, monkeypatch):
    remote, tr, _ = fake_remote
    monkeypatch.setattr(wdremote, "scenario_files",
                        lambda: {"slot_5_20260401_161146.avi": ("1_TANGO_HANGAR-texturedbg", 300_000)})
    tiers = wdremote.build_plan(tr, remote, since=time.time() - 30 * 86400)
    rels = {t: sorted(f["rel"].rsplit("/", 1)[-1] for f in fs) for t, fs in tiers.items()}
    assert "1_TANGO_HANGAR-texturedbg_20260701_120000.json" in rels["P0"]
    assert "events.jsonl" in rels["P0"] and "slot_5_20260401_161146.avi.meta" in rels["P0"]
    assert rels["P1"] == ["slot_1_20261006_101010.avi"]          # fresh marker take
    assert rels["P2"] == ["slot_5_20260401_161146.avi"]          # pinned by a scenario
    assert rels["P3"] == ["slot_2_20250101_000000.avi"]


def test_pull_mirrors_resumes_and_skips(fake_remote, tmp_path):
    remote, tr, root = fake_remote
    dest = tmp_path / "local"
    man = wdremote.run_agent(tr, remote, "manifest", {"paths": ["projects"]})
    s1 = wdremote.pull_files(tr, remote, man["files"], dest, quiet=True)
    assert s1["failed"] == [] and s1["to_transfer"] == len(man["files"])
    for f in man["files"]:
        assert (dest / f["rel"]).read_bytes() == (root / f["rel"]).read_bytes()
        assert abs((dest / f["rel"]).stat().st_mtime - f["mtime"]) < 2.0

    # interrupted transfer: local file truncated -> resumed, not restarted
    big = "projects/1_TANGO_HANGAR-texturedbg/recordings/slot_5_20260401_161146.avi"
    data = (dest / big).read_bytes()
    (dest / big).write_bytes(data[:100_000])
    s2 = wdremote.pull_files(tr, remote, man["files"], dest, quiet=True)
    assert s2["to_transfer"] == 1 and s2["bytes"] == 200_000
    assert (dest / big).read_bytes() == data

    s3 = wdremote.pull_files(tr, remote, man["files"], dest, quiet=True)
    assert s3["to_transfer"] == 0 and s3["up_to_date"] == len(man["files"])


def test_pull_refuses_to_write_through_local_symlinks(fake_remote, tmp_path):
    remote, tr, _ = fake_remote
    dest = tmp_path / "local"
    (dest / "projects").mkdir(parents=True)
    (dest / "other").mkdir()
    (dest / "projects" / "markers-0a").symlink_to(dest / "other")
    man = wdremote.run_agent(tr, remote, "manifest", {"paths": ["projects/markers-0a"]})
    s = wdremote.pull_files(tr, remote, man["files"], dest, quiet=True, dry_run=True)
    assert s["to_transfer"] == 0 and len(s["blocked_by_symlink"]) == 1


def test_py_uploads_runs_and_fetches_outputs(fake_remote, tmp_path, monkeypatch):
    remote, tr, root = fake_remote
    (root / "application").mkdir()
    monkeypatch.setattr(wdremote, "RUNS_DIR", tmp_path / "runs")
    script = _write(tmp_path / "probe_script.py", (
        "import os, sys, json, pathlib\n"
        "out = pathlib.Path(os.environ['WD_REMOTE_OUT'])\n"
        "(out / 'result.json').write_text(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd()}))\n"
    ).encode())
    argv, extra = wdremote._split_passthrough(["py", str(script), "--", "--alpha", "1"])
    args = wdremote.build_parser().parse_args(argv)
    args.args = extra
    assert wdremote.cmd_py(tr, remote, args) == 0
    results = list((tmp_path / "runs").glob("*/out/result.json"))
    assert len(results) == 1
    res = json.loads(results[0].read_text())
    assert res["args"] == ["--alpha", "1"]
    assert res["cwd"].endswith("application")
    # scratch lives under the gitignored tmp_analysis/remote/
    assert list((root / "tmp_analysis" / "remote").glob("*/probe_script.py"))


def test_pull_tier_honours_include_exclude(fake_remote, monkeypatch, tmp_path):
    remote, tr, _ = fake_remote
    monkeypatch.setattr(wdremote, "scenario_files", lambda: {})
    argv, _ = wdremote._split_passthrough(
        ["pull", "--tier", "P0", "--exclude", "*events.jsonl", "--dest", str(tmp_path / "d"),
         "--dry-run"])
    a = wdremote.build_parser().parse_args(argv)
    seen = {}
    monkeypatch.setattr(wdremote, "pull_files",
                        lambda tr, remote, files, dest, **kw: seen.setdefault("f", files))
    assert wdremote.cmd_pull(tr, remote, a) == 0
    rels = [f["rel"] for f in seen["f"]]
    assert rels and not any(r.endswith("events.jsonl") for r in rels)
    assert any(r.endswith("session.json") for r in rels)
