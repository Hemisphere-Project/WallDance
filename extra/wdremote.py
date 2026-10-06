#!/usr/bin/env python3
"""wdremote — work on the WallDance prod laptop from a dev box, over SSH (tailnet).

The show laptop is reached over the Hemisphere tailnet, sometimes through a fast link
and sometimes through slow 4G tethering. This tool supports both ways of working:

* **Fast link: mirror, then run locally.** `plan` sizes what is on prod and prints
  transfer-time estimates; `pull` mirrors it into this repo's `projects/`. Pulls are
  resumable, prioritised, compressed when the content is text, and can be
  bandwidth-limited.
* **Slow link: run on prod, read results.** `replay`, `py` (upload and run a local
  script), `run` and `pytest` execute on the laptop with its own venv. Only the
  small outputs (JSON, logs, contact sheets) come back.

Pure stdlib. Needs OpenSSH (`ssh`, `sftp`) on this side, and the Windows OpenSSH
Server on the laptop (tailnet-only, key auth; see docs/REMOTE_OPS.md). It never
pushes or changes git state on the laptop. `bundle` only *reads* the repo and
writes into the scratch dir.

Quick start::

    python extra/wdremote.py setup --host wd-prod --root C:/WallDance/WallDance
    python extra/wdremote.py doctor
    python extra/wdremote.py inventory            # git state, stack, engines, projects
    python extra/wdremote.py probe                # measure the link (Mbit/s)
    python extra/wdremote.py plan --probe         # what to pull, sizes, ETA per tier
    python extra/wdremote.py pull --tier P0       # configs + sessions + issues + logs
    python extra/wdremote.py pull projects/markers-0a --since 2026-10-01
    python extra/wdremote.py replay hangar-aerial --trt --score
    python extra/wdremote.py py tmp_analysis/audit-2026-10/continuity/drive.py -- build ...
    python extra/wdremote.py bundle               # git bundle --all (or a .git zip) -> local

Live app (the app's loopback remote API, reached through an SSH tunnel per call;
the token is read over SSH once and cached)::

    python extra/wdremote.py status               # state, project, fps, tracks, engine, recorder
    python extra/wdremote.py events -f            # toasts, alerts, readiness, calibration...
    python extra/wdremote.py record start --slot 3 ; ... record stop
    python extra/wdremote.py cmd SetRigSheet field=f_number value=2.8
    python extra/wdremote.py cmd CheckReadiness ; python extra/wdremote.py commands
    python extra/wdremote.py logs -f --tail 50 ; python extra/wdremote.py snapshot
    python extra/wdremote.py clip projects/p/recordings/slot_3_x.avi --start 900 --frames 200 --pull

Control commands are refused while the app is in RUN unless the operator ticked
"Allow remote control during RUN" (phase 6 Live); heavy jobs are STANDBY-only.

Remote layout: `<root>` is the launcher's checkout (`<launcher dir>\\WallDance`).
Scratch work goes to `<root>/tmp_analysis/remote/<stamp>/`, which is gitignored, so
the launcher's dirty-tree check never sees it. Local results land in
`tmp_analysis/remote-runs/<stamp>/`.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(os.environ.get(
    "WD_REMOTE_CONFIG", "~/.config/walldance/remote.json")).expanduser()
RUNS_DIR = REPO / "tmp_analysis" / "remote-runs"
SCENARIOS_DIR = REPO / "application" / "tests" / "scenarios"
JSON_MARK = "@@WDREMOTE-JSON@@"

# Text-like files compress ~10x over SSH; recordings (FFV1/MJPG) do not.
TEXT_SUFFIXES = (".json", ".jsonl", ".log", ".txt", ".csv", ".meta", ".md", ".py")
VIDEO_SUFFIXES = (".avi", ".mp4", ".mov", ".mkv")


# ---------------------------------------------------------------------------
# Remote description
# ---------------------------------------------------------------------------

@dataclass
class Remote:
    host: str
    root: str                       # forward slashes, e.g. C:/WallDance/WallDance
    os: str = "windows"             # windows | posix
    python: str = ""                # override; default = the app venv's python
    bwlimit_kbit: int = 0           # sftp -l (0 = unlimited)
    api_port: int = 8765            # the app's loopback remote API (REMOTE_API_PORT)
    dev_root: str = ""              # dev slot (default: <root>-dev), see wdslot.py
    ssh_opts: List[str] = field(default_factory=list)

    @property
    def win(self) -> bool:
        return self.os == "windows"

    def path(self, rel: str = "") -> str:
        """Native absolute path on the remote (backslashes on Windows)."""
        root = self.root.rstrip("/\\")
        full = f"{root}/{rel.lstrip('/')}" if rel else root
        return full.replace("/", "\\") if self.win else full

    def sftp_path(self, rel: str = "") -> str:
        """Path as Win32-OpenSSH's sftp-server wants it (``/C:/x/y``)."""
        root = self.root.replace("\\", "/").rstrip("/")
        full = f"{root}/{rel.lstrip('/')}" if rel else root
        if self.win and not full.startswith("/"):
            full = "/" + full
        return full

    def python_exe(self) -> str:
        if self.python:
            return self.python
        if self.win:
            return self.path("application/.venv/Scripts/python.exe")
        return self.path("application/.venv/bin/python")

    def shell(self, argv: Sequence[str], cwd: Optional[str] = None,
              env: Optional[Dict[str, str]] = None) -> str:
        """One command line for the remote login shell (cmd.exe on Windows)."""
        env = dict(env or {})
        if self.win:
            parts = [f'set "{k}={v}"' for k, v in env.items()]
            if cwd is not None:
                parts.append(f'cd /d {_q_cmd(self.path(cwd))}')
            parts.append(" ".join(_q_cmd(a) for a in argv))
            return " && ".join(parts)
        pre = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
        cd = f"cd {shlex.quote(self.path(cwd))} && " if cwd is not None else ""
        return cd + (pre + " " if pre else "") + shlex.join(list(argv))


def _q_cmd(arg: str) -> str:
    """Quote one argument for cmd.exe. No arg we build contains a double quote."""
    if arg and not any(c in arg for c in ' \t&|<>^()%!"'):
        return arg
    if '"' in arg:
        raise ValueError(f"cannot quote a double quote for cmd.exe: {arg!r}")
    return f'"{arg}"'


def load_remote(path: Path = DEFAULT_CONFIG, **overrides) -> Remote:
    data: Dict = {}
    if path.exists():
        data = json.loads(path.read_text())
    for k, v in overrides.items():
        if v not in (None, ""):
            data[k] = v
    for env_key, key in (("WD_REMOTE_HOST", "host"), ("WD_REMOTE_ROOT", "root")):
        if os.environ.get(env_key) and key not in overrides:
            data.setdefault(key, os.environ[env_key])
    if not data.get("host") or not data.get("root"):
        raise SystemExit("wdremote: no remote configured. Run "
                         "`wdremote setup --host <tailnet-host> --root <checkout path>`.")
    known = {f for f in Remote.__dataclass_fields__}
    return Remote(**{k: v for k, v in data.items() if k in known})


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

class SshTransport:
    """ssh/sftp subprocesses. BatchMode: never prompt (key auth only)."""

    def __init__(self, remote: Remote):
        self.remote = remote

    def _base(self, prog: str, compress: bool) -> List[str]:
        args = [prog, "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15"]
        if compress:
            args.append("-C")
        return args + list(self.remote.ssh_opts)

    def run(self, command: str, stdin: Optional[bytes] = None, stream: bool = False,
            timeout: Optional[float] = None, compress: bool = False,
            stdout_binary: bool = False) -> subprocess.CompletedProcess:
        argv = self._base("ssh", compress) + [self.remote.host, command]
        if stream:
            return subprocess.run(argv, input=stdin, timeout=timeout)
        return subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)

    def sftp_batch(self, lines: Sequence[str], compress: bool = False,
                   limit_kbit: int = 0) -> int:
        argv = self._base("sftp", compress) + ["-b", "-"]
        if limit_kbit:
            argv += ["-l", str(int(limit_kbit))]
        argv.append(self.remote.host)
        proc = subprocess.run(argv, input="\n".join(lines).encode() + b"\n")
        return proc.returncode


class LocalTransport:
    """Runs the 'remote' on this machine (posix). Used by the tests and `--local`
    dry runs. It emulates the sftp subset wdremote emits (get -ap / put / -mkdir)."""

    def __init__(self, remote: Remote):
        self.remote = remote

    def run(self, command: str, stdin: Optional[bytes] = None, stream: bool = False,
            timeout: Optional[float] = None, compress: bool = False,
            stdout_binary: bool = False) -> subprocess.CompletedProcess:
        if stream:
            return subprocess.run(["bash", "-c", command], input=stdin, timeout=timeout)
        return subprocess.run(["bash", "-c", command], input=stdin,
                              capture_output=True, timeout=timeout)

    def sftp_batch(self, lines: Sequence[str], compress: bool = False,
                   limit_kbit: int = 0) -> int:
        rc = 0
        for line in lines:
            tolerant = line.startswith("-")
            words = shlex.split(line.lstrip("-"))
            try:
                if words[0] == "get":
                    flags = words[1] if words[1].startswith("-") else ""
                    src, dst = words[-2], words[-1]
                    _copy_resume(Path(src), Path(dst), resume="a" in flags,
                                 preserve="p" in flags)
                elif words[0] == "put":
                    Path(words[-1]).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(words[-2], words[-1])
                elif words[0] == "mkdir":
                    Path(words[1]).mkdir()
                else:
                    raise ValueError(f"unsupported sftp command {words[0]}")
            except Exception:
                if not tolerant:
                    return 1
        return rc


def _copy_resume(src: Path, dst: Path, resume: bool, preserve: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    offset = dst.stat().st_size if (resume and dst.exists()) else 0
    with open(src, "rb") as fi, open(dst, "ab" if offset else "wb") as fo:
        fi.seek(offset)
        shutil.copyfileobj(fi, fo, 1 << 20)
    if preserve:
        st = src.stat()
        os.utime(dst, (st.st_atime, st.st_mtime))


# ---------------------------------------------------------------------------
# The remote agent: a stdlib script piped to the laptop's python over stdin
# ---------------------------------------------------------------------------

AGENT_PY = r'''
import json, os, sys, time, shutil, platform, subprocess, fnmatch
from pathlib import Path

MARK = "@@WDREMOTE-JSON@@"
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != "-" else None

def _run(argv, cwd=None, timeout=20):
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return None, "", str(e)

def _stat(p):
    st = p.stat()
    return {"size": st.st_size, "mtime": round(st.st_mtime, 3)}

def _git(root):
    out = {}
    rc, _, _ = _run(["git", "--version"])
    out["git_cli"] = rc == 0
    if rc == 0:
        for key, argv in {
            "status": ["git", "status", "--porcelain=v1", "-b"],
            "log": ["git", "log", "--oneline", "-15"],
            "branches": ["git", "branch", "-vv", "--all"],
            "stash": ["git", "stash", "list"],
            "ahead_behind": ["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"],
            "head": ["git", "rev-parse", "HEAD"],
        }.items():
            rc2, so, se = _run(argv, cwd=str(root))
            out[key] = so if rc2 == 0 else ("ERROR: " + se)
        return out
    gd = root / ".git"
    try:
        head = (gd / "HEAD").read_text().strip()
        out["HEAD"] = head
        refs = {}
        if (gd / "packed-refs").exists():
            for line in (gd / "packed-refs").read_text().splitlines():
                if line and line[0] not in "#^":
                    sha, ref = line.split(" ", 1)
                    refs[ref] = sha
        for sub in ("refs/heads", "refs/remotes"):
            base = gd / sub
            if base.exists():
                for f in base.rglob("*"):
                    if f.is_file():
                        refs[str(f.relative_to(gd)).replace("\\", "/")] = f.read_text().strip()
        out["refs"] = refs
        out["note"] = "no git CLI: refs only (no dirty/ahead info). Use `wdremote bundle`."
    except Exception as e:
        out["error"] = str(e)
    return out

def _packages():
    try:
        from importlib import metadata
        pk = {}
        for d in metadata.distributions():
            name = (d.metadata["Name"] or "").lower()
            if name:
                pk[name] = d.version
        return pk
    except Exception as e:
        return {"error": str(e)}

def _cpu_name():
    if os.name == "nt":
        rc, so, _ = _run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_Processor).Name"])
        if rc == 0 and so:
            return so.splitlines()[0].strip()
        return os.environ.get("PROCESSOR_IDENTIFIER", "")
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()

def _engine_meta(p):
    """Ultralytics engines start with int32 len + JSON metadata."""
    try:
        with open(p, "rb") as f:
            n = int.from_bytes(f.read(4), "little", signed=True)
            if 0 < n < 100000:
                return json.loads(f.read(n).decode("utf-8", "replace"))
    except Exception:
        pass
    return None

def inventory(args):
    root = ROOT
    inv = {"root": str(root), "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "platform": platform.platform(), "python": sys.version.split()[0],
           "executable": sys.executable,
           "cpu": _cpu_name(),
           "cpu_count": os.cpu_count()}
    rc, so, se = _run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,power.limit,temperature.gpu",
                       "--format=csv,noheader"])
    inv["gpu"] = so if rc == 0 else ("ERROR: " + se)
    du = shutil.disk_usage(str(root))
    inv["disk_free_gb"] = round(du.free / 1e9, 1)
    inv["git"] = _git(root)
    pk = _packages()
    keys = ("torch", "torchvision", "tensorrt", "tensorrt-cu12", "tensorrt-cu13", "tensorrt_cu12_libs",
            "tensorrt-cu13-libs", "ultralytics", "numpy", "opencv-python", "kornia", "onnx", "onnxslim",
            "onnxruntime-gpu", "ids-peak", "ids-peak-ipl", "dearpygui", "nvidia-modelopt")
    inv["stack"] = {k: pk.get(k) for k in keys if k in pk}
    inv["packages"] = pk if args.get("freeze") else len(pk)
    eng = []
    md = root / "models"
    if md.exists():
        for p in sorted(md.glob("*.engine")):
            e = {"name": p.name, **_stat(p)}
            m = _engine_meta(p)
            if m:
                e["ultralytics"] = m.get("version"); e["built"] = m.get("date")
                e["half"] = m.get("args", {}).get("half"); e["imgsz"] = m.get("imgsz")
            eng.append(e)
    inv["engines"] = eng
    projects = []
    pdir = root / "projects"
    if pdir.exists():
        for p in sorted(x for x in pdir.iterdir() if x.is_dir()):
            info = {"name": p.name, "configs": 0, "latest_config": None,
                    "recordings": [], "sessions": 0, "sessions_bytes": 0,
                    "issues": 0, "calib2_bytes": 0, "total_bytes": 0}
            latest = None
            for f in p.glob("*.json"):
                info["configs"] += 1
                if latest is None or f.stat().st_mtime > latest.stat().st_mtime:
                    latest = f
            if latest:
                info["latest_config"] = latest.name
            rec = p / "recordings"
            if rec.exists():
                for f in sorted(rec.iterdir()):
                    if f.is_file() and not f.name.endswith((".meta", ".jsonl")):
                        r = {"file": f.name, **_stat(f)}
                        mf = rec / (f.name + ".meta")
                        if mf.exists():
                            try:
                                r["meta"] = json.loads(mf.read_text())
                            except Exception:
                                r["meta"] = "unreadable"
                        info["recordings"].append(r)
            for f in p.rglob("*"):
                if f.is_file():
                    try:
                        sz = f.stat().st_size
                    except OSError:
                        continue
                    info["total_bytes"] += sz
                    rel = f.relative_to(p).parts
                    if rel and rel[0] == "sessions":
                        info["sessions_bytes"] += sz
                        if f.name == "session.json":
                            info["sessions"] += 1
                    if "issues" in rel:
                        info["issues"] += 1
                    if rel and rel[0] == "calib2":
                        info["calib2_bytes"] += sz
            projects.append(info)
    inv["projects"] = projects
    extra = {}
    for rel in ("application/tracking_events.jsonl", "logs", "TODO-me"):
        q = root / rel
        if q.is_file():
            extra[rel] = _stat(q)
        elif q.is_dir():
            extra[rel] = {"files": sum(1 for _ in q.rglob("*")),
                          "size": sum(f.stat().st_size for f in q.rglob("*") if f.is_file())}
    inv["other"] = extra
    return inv

def manifest(args):
    root = ROOT
    inc = args.get("include") or []
    exc = args.get("exclude") or []
    since = args.get("since") or 0
    files = []
    for rel in args.get("paths") or ["projects"]:
        base = root / rel
        cands = [base] if base.is_file() else (list(base.rglob("*")) if base.exists() else [])
        for f in cands:
            if not f.is_file() or f.is_symlink():
                continue
            r = str(f.relative_to(root)).replace("\\", "/")
            if inc and not any(fnmatch.fnmatch(r, g) for g in inc):
                continue
            if exc and any(fnmatch.fnmatch(r, g) for g in exc):
                continue
            st = f.stat()
            if st.st_mtime < since:
                continue
            files.append({"rel": r, "size": st.st_size, "mtime": round(st.st_mtime, 3)})
    return {"files": files}

def throughput(args):
    n = int(args.get("mb", 16)) << 20
    chunk = os.urandom(1 << 20)
    out = sys.stdout.buffer
    sent = 0
    while sent < n:
        out.write(chunk); sent += len(chunk)
    out.flush()
    return None

def main(op, args):
    res = {"inventory": inventory, "manifest": manifest, "throughput": throughput}[op](args)
    if res is not None:
        sys.stdout.write("\n" + MARK + "\n" + json.dumps(res) + "\n" + MARK + "\n")
        sys.stdout.flush()
'''


def run_agent(tr, remote: Remote, op: str, args: Optional[dict] = None,
              timeout: float = 600, script_src: str = "") -> dict:
    """Pipe an agent script to the laptop's python and parse its JSON answer.
    ``script_src`` lets other modules (wdslot) ship their own agent; it must
    define ``main(op, args)`` printing the JSON between JSON_MARK lines."""
    body = script_src or AGENT_PY
    script = body + f"\nmain({op!r}, json.loads({json.dumps(json.dumps(args or {}))}))\n"
    cmd = remote.shell([remote.python_exe(), "-", remote.root],
                       env={"PYTHONIOENCODING": "utf-8"})
    proc = tr.run(cmd, stdin=script.encode(), timeout=timeout, compress=True)
    out = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
    if JSON_MARK not in out:
        err = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
        raise SystemExit(f"wdremote: agent '{op}' failed (rc={proc.returncode}).\n"
                         f"--- stdout ---\n{out[-2000:]}\n--- stderr ---\n{err[-2000:]}")
    payload = out.split(JSON_MARK)[1]
    return json.loads(payload)


# ---------------------------------------------------------------------------
# Pull planning
# ---------------------------------------------------------------------------

def scenario_files() -> Dict[str, Tuple[str, int]]:
    """{filename: (project, bytes)} for every scenario manifest in the repo."""
    out: Dict[str, Tuple[str, int]] = {}
    for p in sorted(SCENARIOS_DIR.glob("*.json")):
        try:
            m = json.loads(p.read_text())
            fp = m.get("recording_fingerprint") or {}
            if fp.get("file"):
                out[fp["file"]] = (m.get("project", ""), int(fp.get("bytes") or 0))
        except Exception:
            continue
    return out


def classify(rel: str, size: int, mtime: float, since: float,
             scen: Dict[str, Tuple[str, int]]) -> str:
    """Tier of one remote file:

    P0 state + text (configs, sessions, issues, calib2 json, logs, meta);
    P1 recordings newer than ``since`` (fresh field / marker takes);
    P2 recordings the scenario corpus pins;
    P3 everything else.
    """
    name = rel.rsplit("/", 1)[-1]
    if rel.endswith(TEXT_SUFFIXES) and not rel.endswith(VIDEO_SUFFIXES):
        return "P0"
    if name.endswith(VIDEO_SUFFIXES):
        if mtime >= since:
            return "P1"
        if name in scen:
            return "P2"
        return "P3"
    if "/calib2/" in rel:
        return "P0" if size < 5_000_000 else "P3"
    return "P3"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n}"


def eta(nbytes: int, mbps: Optional[float]) -> str:
    if not mbps:
        return "?"
    s = nbytes * 8 / (mbps * 1e6)
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------

def _local_has_symlink(dest: Path, rel: str) -> Optional[Path]:
    p = dest
    for part in PurePosixPath(rel).parts[:-1]:
        p = p / part
        if p.is_symlink():
            return p
    return None


def pull_files(tr, remote: Remote, files: List[dict], dest: Path, *,
               compress_text: bool = True, limit_kbit: int = 0,
               follow_symlinks: bool = False, dry_run: bool = False,
               quiet: bool = False) -> dict:
    """Mirror ``files`` (manifest rows) under ``dest``; resume partial files."""
    todo, skipped, blocked = [], [], []
    for f in files:
        rel, size = f["rel"], f["size"]
        link = _local_has_symlink(dest, rel)
        if link and not follow_symlinks:
            blocked.append((rel, str(link)))
            continue
        local = dest / rel
        if local.exists():
            lsize = local.stat().st_size
            if lsize == size and abs(local.stat().st_mtime - f["mtime"]) < 2.0:
                skipped.append(rel)
                continue
            if lsize > size or (lsize == size):
                f = dict(f, fresh=True)          # changed remotely: re-download
        todo.append(f)
    total = sum(f["size"] - (0 if f.get("fresh") or not (dest / f["rel"]).exists()
                             else (dest / f["rel"]).stat().st_size) for f in todo)
    summary = {"to_transfer": len(todo), "bytes": total, "up_to_date": len(skipped),
               "blocked_by_symlink": blocked}
    if not quiet:
        print(f"[pull] {len(todo)} file(s) to transfer ({fmt_bytes(total)}), "
              f"{len(skipped)} already up to date"
              + (f", {len(blocked)} blocked by local symlinks" if blocked else ""))
        for rel, link in blocked[:5]:
            print(f"   blocked: {rel} (local symlink {link}; pass --follow-symlinks)")
    if dry_run or not todo:
        return summary

    # Two sessions: text compressed, binaries not (FFV1 does not shrink).
    groups = {True: [], False: []}
    for f in todo:
        is_text = compress_text and f["rel"].endswith(TEXT_SUFFIXES)
        groups[is_text].append(f)

    stop = threading.Event()
    watch = [dest / f["rel"] for f in todo]
    start_bytes = sum(p.stat().st_size for p in watch if p.exists())
    t0 = time.time()

    def _progress():
        while not stop.wait(3.0):
            have = sum(p.stat().st_size for p in watch if p.exists()) - start_bytes
            rate = have / max(1e-3, time.time() - t0)
            left = max(0, total - have)
            print(f"[pull] {fmt_bytes(have)} / {fmt_bytes(total)}  "
                  f"{rate * 8 / 1e6:.1f} Mbit/s  ETA {eta(left, rate * 8 / 1e6)}", flush=True)

    th = threading.Thread(target=_progress, daemon=True)
    if not quiet:
        th.start()
    rcs = []
    try:
        for compress, group in groups.items():
            if not group:
                continue
            lines = []
            for f in group:
                local = dest / f["rel"]
                local.parent.mkdir(parents=True, exist_ok=True)
                if f.get("fresh") and local.exists():
                    local.unlink()
                lines.append(f'-get -ap "{remote.sftp_path(f["rel"])}" "{local}"')
            rcs.append(tr.sftp_batch(lines, compress=compress, limit_kbit=limit_kbit))
    finally:
        stop.set()
    bad = []
    for f in todo:
        local = dest / f["rel"]
        if not local.exists() or local.stat().st_size != f["size"]:
            bad.append(f["rel"])
        else:
            try:
                os.utime(local, (f["mtime"], f["mtime"]))
            except OSError:
                pass
    summary["failed"] = bad
    summary["seconds"] = round(time.time() - t0, 1)
    summary["sftp_rc"] = rcs
    log = dest / "tmp_analysis" / "remote-runs" / "pull-log.jsonl" if dest == REPO \
        else dest / ".wdremote-pull.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as fh:
        fh.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                             "host": remote.host, **summary,
                             "files": [f["rel"] for f in todo]}) + "\n")
    if not quiet:
        print(f"[pull] done in {summary['seconds']} s; "
              + (f"{len(bad)} incomplete (re-run to resume): {bad[:5]}" if bad else "all complete"))
    return summary


# ---------------------------------------------------------------------------
# Remote execution helpers
# ---------------------------------------------------------------------------

def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def new_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def remote_scratch(stamp: str) -> str:
    return f"tmp_analysis/remote/{stamp}"


def sftp_mkdirs(remote: Remote, rel_dir: str) -> List[str]:
    lines, acc = [], ""
    for part in PurePosixPath(rel_dir).parts:
        acc = f"{acc}/{part}" if acc else part
        lines.append(f'-mkdir "{remote.sftp_path(acc)}"')
    return lines


def fetch_dir(tr, remote: Remote, rel_dir: str, local_dir: Path) -> dict:
    man = run_agent(tr, remote, "manifest", {"paths": [rel_dir]})
    files = man["files"]
    prefix = rel_dir.rstrip("/") + "/"
    mapped = [dict(f, rel=f["rel"][len(prefix):]) if f["rel"].startswith(prefix) else f
              for f in files]
    # pull_files mirrors by rel under dest, so stage into local_dir directly
    out = {"to_transfer": 0}
    if mapped:
        lines = []
        for f, orig in zip(mapped, files):
            loc = local_dir / f["rel"]
            loc.parent.mkdir(parents=True, exist_ok=True)
            lines.append(f'-get -p "{remote.sftp_path(orig["rel"])}" "{loc}"')
        tr.sftp_batch(lines, compress=True)
        out["to_transfer"] = len(lines)
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_setup(a) -> int:
    cfg = {}
    if DEFAULT_CONFIG.exists():
        cfg = json.loads(DEFAULT_CONFIG.read_text())
    for k in ("host", "root", "os", "python"):
        v = getattr(a, k, None)
        if v:
            cfg[k] = v
    if a.bwlimit is not None:
        cfg["bwlimit_kbit"] = a.bwlimit
    DEFAULT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"[setup] wrote {DEFAULT_CONFIG}: {cfg}")
    return 0


def cmd_doctor(tr, remote: Remote, a) -> int:
    print(f"[doctor] host={remote.host} root={remote.root} os={remote.os}")
    ok = True
    probe = 'echo %COMSPEC%' if remote.win else 'echo $SHELL'
    p = tr.run(probe, timeout=30)
    shell = (p.stdout or b"").decode(errors="replace").strip()
    print(f"  ssh: rc={p.returncode} shell={shell!r}")
    if p.returncode != 0:
        print("  -> ssh failed: check tailnet, sshd, key auth (BatchMode, no prompts).")
        return 2
    if remote.win and "%COMSPEC%" in shell:
        print("  !! the remote default shell is not cmd.exe (PowerShell?). wdremote "
              "builds cmd.exe command lines; set DefaultShell back to cmd.exe.")
        ok = False
    exe = remote.python_exe()
    test = remote.shell([exe, "-c", "import sys; print(sys.version.split()[0])"])
    p = tr.run(test, timeout=60)
    print(f"  python: {exe} -> rc={p.returncode} {(p.stdout or b'').decode().strip()}")
    ok &= p.returncode == 0
    p = tr.run(remote.shell(["git", "--version"]), timeout=30)
    print(f"  git CLI: {'yes ' + (p.stdout or b'').decode().strip() if p.returncode == 0 else 'NO (bundle falls back to a .git zip)'}")
    return 0 if ok else 1


def cmd_inventory(tr, remote: Remote, a) -> int:
    inv = run_agent(tr, remote, "inventory", {"freeze": a.freeze})
    stamp = new_stamp()
    out = RUNS_DIR / f"inventory-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(inv, indent=2))
    if a.json:
        print(json.dumps(inv, indent=2))
        return 0
    g = inv.get("git", {})
    print(f"== {remote.host}  {inv['platform']}  py{inv['python']}  cpu: {inv['cpu']} x{inv['cpu_count']}")
    print(f"   gpu: {inv['gpu']}   disk free: {inv['disk_free_gb']} GB")
    print(f"   stack: {inv['stack']}")
    if g.get("git_cli"):
        print(f"   git: {g.get('status', '').splitlines()[0] if g.get('status') else ''}"
              f"   origin/main...HEAD (behind ahead) = {g.get('ahead_behind')}")
        dirty = [l for l in (g.get("status") or "").splitlines()[1:] if l.strip()]
        if dirty:
            print(f"   !! {len(dirty)} uncommitted change(s): {dirty[:8]}")
        if g.get("stash"):
            print(f"   stash: {g['stash'].splitlines()[:5]}")
        print("   log:\n      " + "\n      ".join((g.get("log") or "").splitlines()[:8]))
    else:
        print(f"   git: no CLI — {g.get('HEAD')}  refs: {list((g.get('refs') or {}).items())[:4]}")
    print("   engines:")
    for e in inv.get("engines", []):
        print(f"      {e['name']:<28} {fmt_bytes(e['size']):>9}  "
              f"{time.strftime('%Y-%m-%d', time.localtime(e['mtime']))}  "
              f"ultralytics={e.get('ultralytics')}")
    print("   projects:")
    for p in inv.get("projects", []):
        recs = p["recordings"]
        newest = max((r["mtime"] for r in recs), default=0)
        print(f"      {p['name']:<30} {fmt_bytes(p['total_bytes']):>9}  rec={len(recs):>2} "
              f"(newest {time.strftime('%Y-%m-%d', time.localtime(newest)) if newest else '-'})  "
              f"sessions={p['sessions']} ({fmt_bytes(p['sessions_bytes'])})  issues={p['issues']}  "
              f"configs={p['configs']}")
    print(f"   saved: {_rel(out)}")
    return 0


def measure_mbps(tr, remote: Remote, mb: int = 8) -> float:
    script = AGENT_PY + f"\nmain('throughput', {{'mb': {int(mb)}}})\n"
    cmd = remote.shell([remote.python_exe(), "-", remote.root])
    t0 = time.time()
    proc = tr.run(cmd, stdin=script.encode(), timeout=600)
    dt = time.time() - t0
    n = len(proc.stdout or b"")
    if proc.returncode != 0 or n < (mb << 20) * 0.9:
        raise SystemExit(f"wdremote: probe failed rc={proc.returncode} got {n} bytes")
    # subtract ~one round trip of python start-up (measured separately)
    t1 = time.time()
    tr.run(remote.shell([remote.python_exe(), "-c", "pass"]), timeout=120)
    startup = time.time() - t1
    return n * 8 / 1e6 / max(0.05, dt - startup)


def cmd_probe(tr, remote: Remote, a) -> int:
    mbps = measure_mbps(tr, remote, a.mb)
    mode = ("FAST: mirror and run locally is practical" if mbps >= 50 else
            "MEDIUM: pull P0 + selected takes; run heavy analysis remotely" if mbps >= 8 else
            "SLOW: run remotely; pull only results/small clips")
    print(f"[probe] ~{mbps:.1f} Mbit/s over ssh ({a.mb} MB random)  ->  {mode}")
    return 0


def _since_ts(s: Optional[str], days_default: int = 30) -> float:
    if not s:
        return time.time() - days_default * 86400
    return time.mktime(time.strptime(s, "%Y-%m-%d"))


def build_plan(tr, remote: Remote, since: float) -> Dict[str, List[dict]]:
    man = run_agent(tr, remote, "manifest", {"paths": ["projects", "logs",
                                                        "application/tracking_events.jsonl",
                                                        "TODO-me"]})
    scen = scenario_files()
    tiers: Dict[str, List[dict]] = {"P0": [], "P1": [], "P2": [], "P3": []}
    for f in man["files"]:
        if f["rel"].endswith("tracking_events.jsonl") and f["rel"].startswith("application/"):
            tiers["P3"].append(f)          # live-run JSONL appends forever: opt-in
            continue
        tiers[classify(f["rel"], f["size"], f["mtime"], since, scen)].append(f)
    return tiers


TIER_LABEL = {
    "P0": "state + text: configs, sessions, issues, calib2 json, logs, .meta",
    "P1": "recent recordings (since cutoff): field + marker takes",
    "P2": "scenario-corpus recordings (pinned by tests/scenarios)",
    "P3": "everything else (old recordings, big blobs, live tracking_events.jsonl)",
}


def cmd_plan(tr, remote: Remote, a) -> int:
    mbps = a.mbps
    if a.probe:
        mbps = measure_mbps(tr, remote, 8)
        print(f"[plan] measured ~{mbps:.1f} Mbit/s")
    tiers = build_plan(tr, remote, _since_ts(a.since))
    dest = Path(a.dest) if a.dest else REPO
    print(f"[plan] remote {remote.host}:{remote.root} -> local {dest}")
    for t, files in tiers.items():
        missing = [f for f in files if not ((dest / f["rel"]).exists()
                   and (dest / f["rel"]).stat().st_size == f["size"])]
        nbytes = sum(f["size"] for f in missing)
        print(f"  {t}  {len(missing):>5}/{len(files):<5} files  {fmt_bytes(nbytes):>10}  "
              f"ETA {eta(nbytes, mbps):>8}   {TIER_LABEL[t]}")
        if a.verbose:
            for f in sorted(missing, key=lambda x: -x["size"])[:12]:
                print(f"         {fmt_bytes(f['size']):>9}  {f['rel']}")
    print("  pull a tier:  wdremote pull --tier P0   (tiers are cumulative only if you list them: --tier P0 --tier P1)")
    return 0


def cmd_pull(tr, remote: Remote, a) -> int:
    dest = Path(a.dest) if a.dest else REPO
    if a.tier:
        tiers = build_plan(tr, remote, _since_ts(a.since))
        files = [f for t in a.tier for f in tiers[t]]
        if a.paths:
            files = [f for f in files if any(f["rel"].startswith(p.rstrip("/")) for p in a.paths)]
    else:
        if not a.paths:
            raise SystemExit("wdremote pull: give PATH(s) relative to the remote root, or --tier")
        man = run_agent(tr, remote, "manifest", {
            "paths": a.paths, "include": a.include, "exclude": a.exclude,
            "since": _since_ts(a.since, 0) if a.since else 0})
        files = man["files"]
    if a.max_gb:
        cap, kept, acc = a.max_gb * 1e9, [], 0
        for f in sorted(files, key=lambda x: x["size"]):
            if acc + f["size"] > cap:
                continue
            kept.append(f)
            acc += f["size"]
        files = kept
    limit = a.bwlimit if a.bwlimit is not None else remote.bwlimit_kbit
    pull_files(tr, remote, files, dest, limit_kbit=limit, dry_run=a.dry_run,
               follow_symlinks=a.follow_symlinks)
    return 0


def _stream(tr, remote: Remote, argv: Sequence[str], cwd: str = "application",
            env: Optional[Dict[str, str]] = None) -> int:
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", **(env or {})}
    cmd = remote.shell(argv, cwd=cwd, env=env)
    print(f"[remote] {cmd}", flush=True)
    return tr.run(cmd, stream=True).returncode


def cmd_run(tr, remote: Remote, a) -> int:
    argv = list(a.argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if argv and argv[0] in ("python", "py"):
        argv[0] = remote.python_exe()
    return _stream(tr, remote, argv, cwd=a.cwd)


def cmd_pytest(tr, remote: Remote, a) -> int:
    return _stream(tr, remote, [remote.python_exe(), "-m", "pytest", "tests", "-q",
                                "-p", "no:cacheprovider", *a.args])


def cmd_py(tr, remote: Remote, a) -> int:
    """Upload a local script (+ --with files) to the remote scratch and run it there
    with the app venv; then fetch <scratch>/out back to tmp_analysis/remote-runs/."""
    stamp = new_stamp()
    scratch = remote_scratch(stamp)
    script = Path(a.script).resolve()
    uploads = [script] + [Path(w).resolve() for w in (a.with_ or [])]
    lines = sftp_mkdirs(remote, scratch + "/out")
    for u in uploads:
        lines.append(f'put "{u}" "{remote.sftp_path(scratch + "/" + u.name)}"')
    if tr.sftp_batch(lines, compress=True) != 0:
        print("[py] upload failed")
        return 2
    args = list(a.args)
    if args and args[0] == "--":
        args = args[1:]
    env = {"WD_REMOTE_OUT": remote.path(scratch + "/out"), "WD_REMOTE": "1"}
    rc = _stream(tr, remote, [remote.python_exe(), remote.path(f"{scratch}/{script.name}"),
                              *args], cwd=a.cwd, env=env)
    local = RUNS_DIR / stamp
    fetch_dir(tr, remote, scratch + "/out", local / "out")
    for extra in (a.fetch or []):
        fetch_dir(tr, remote, extra, local / PurePosixPath(extra).name)
    print(f"[py] rc={rc}; outputs -> {_rel(local)} (remote scratch {scratch})")
    return rc


def cmd_replay(tr, remote: Remote, a) -> int:
    stamp = new_stamp()
    scratch = remote_scratch(stamp)
    name = a.scenario[:-5] if a.scenario.endswith(".json") else a.scenario
    lines = sftp_mkdirs(remote, scratch + "/logs")
    tr.sftp_batch(lines)
    argv = [remote.python_exe(), "tests/replay.py",
            "--scenario", f"tests/scenarios/{name}.json",
            "--out", remote.path(f"{scratch}/summary.json"),
            "--log-dir", remote.path(f"{scratch}/logs")]
    if a.trt:
        argv.append("--trt")
    if a.score:
        argv.append("--score")
    if a.cache:
        argv.append("--cache")
    if a.start is not None:
        argv += ["--start", str(a.start)]
    if a.frames is not None:
        argv += ["--frames", str(a.frames)]
    if a.timeline:
        argv += ["--timeline", remote.path(f"{scratch}/timeline.json")]
    for s in a.set or []:
        argv += ["--set", s]
    rc = _stream(tr, remote, argv)
    local = RUNS_DIR / stamp
    keep = ["summary.json"] + (["timeline.json"] if a.timeline else [])
    lines = []
    local.mkdir(parents=True, exist_ok=True)
    for k in keep:
        lines.append(f'-get -p "{remote.sftp_path(scratch + "/" + k)}" "{local / k}"')
    tr.sftp_batch(lines, compress=True)
    if a.logs:
        fetch_dir(tr, remote, scratch + "/logs", local / "logs")
    s = local / "summary.json"
    if s.exists():
        print(s.read_text()[:4000])
    print(f"[replay] rc={rc}; results -> {_rel(local)}")
    return rc


def cmd_bundle(tr, remote: Remote, a) -> int:
    """git bundle --all on the laptop (or a .git zip without the git CLI) -> local."""
    stamp = new_stamp()
    scratch = remote_scratch(stamp)
    tr.sftp_batch(sftp_mkdirs(remote, scratch))
    probe = tr.run(remote.shell(["git", "--version"]), timeout=30)
    local = RUNS_DIR / stamp
    local.mkdir(parents=True, exist_ok=True)
    if probe.returncode == 0:
        b = remote.path(f"{scratch}/prod.bundle")
        st = remote.path(f"{scratch}/status.txt")
        if remote.win:
            cmd = (remote.shell(["git", "bundle", "create", b, "--all"], cwd="")
                   + f" && git status --porcelain=v1 -b > {_q_cmd(st)}"
                   + f" && git diff >> {_q_cmd(st)}")
        else:
            cmd = (remote.shell(["git", "bundle", "create", b, "--all"], cwd="")
                   + f" && git status --porcelain=v1 -b > {shlex.quote(st)}"
                   + f" && git diff >> {shlex.quote(st)}")
        files = ["prod.bundle", "status.txt"]
    else:
        z = remote.path(f"{scratch}/prod-git.zip")
        cmd = remote.shell([remote.python_exe(), "-c",
                            "import shutil,sys; shutil.make_archive(sys.argv[1][:-4], 'zip', '.', '.git')",
                            z], cwd="")
        files = ["prod-git.zip"]
    rc = tr.run(cmd, stream=True).returncode
    lines = [f'-get -p "{remote.sftp_path(scratch + "/" + f)}" "{local / f}"' for f in files]
    tr.sftp_batch(lines)
    print(f"[bundle] rc={rc}; -> {_rel(local)}  "
          "(verify: git bundle verify <file>; inspect: git fetch <file> 'refs/*:refs/prod/*')")
    return rc



# ---------------------------------------------------------------------------
# Live app control: the in-app remote API over an SSH port-forward
# ---------------------------------------------------------------------------

import socket
import urllib.error
import urllib.request
import gzip as _gzip


def _token_cache(remote: Remote) -> Path:
    return DEFAULT_CONFIG.parent / f"remote_token.{remote.host}"


def get_token(tr, remote: Remote, refresh: bool = False) -> str:
    """The app's bearer token, read over SSH once and cached locally (0600)."""
    cache = _token_cache(remote)
    if cache.exists() and not refresh:
        return cache.read_text().strip()
    if isinstance(tr, LocalTransport):
        token = Path("~/.walldance/remote_token").expanduser().read_text().strip()
    else:
        cmd = ('type "%USERPROFILE%\\.walldance\\remote_token"' if remote.win
               else "cat ~/.walldance/remote_token")
        p = tr.run(cmd, timeout=30)
        token = (p.stdout or b"").decode().strip()
        if p.returncode != 0 or len(token) < 16:
            raise SystemExit("wdremote: no API token on the laptop yet -- has the app been "
                             "started once with REMOTE_API_ENABLED? "
                             f"({(p.stderr or b'').decode().strip()})")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(token + "\n")
    try:
        os.chmod(cache, 0o600)
    except OSError:
        pass
    return token


class ApiSession:
    """Context manager: SSH -L tunnel to the laptop's 127.0.0.1:<api_port>
    (no tunnel in --local mode) + bearer-token JSON helpers."""

    def __init__(self, tr, remote: Remote):
        self.tr, self.remote = tr, remote
        self.proc: Optional[subprocess.Popen] = None
        self.base = ""
        self.token = ""

    def __enter__(self) -> "ApiSession":
        self.token = get_token(self.tr, self.remote)
        if isinstance(self.tr, LocalTransport):
            self.base = f"http://127.0.0.1:{self.remote.api_port}"
            return self
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            lport = sk.getsockname()[1]
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                "-N", "-L", f"{lport}:127.0.0.1:{self.remote.api_port}",
                *self.remote.ssh_opts, self.remote.host]
        self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise SystemExit("wdremote: ssh tunnel failed (is sshd up? key auth?)")
            try:
                with socket.create_connection(("127.0.0.1", lport), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        self.base = f"http://127.0.0.1:{lport}"
        return self

    def __exit__(self, *exc) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def request(self, path: str, body: Optional[dict] = None, raw: bool = False,
                timeout: float = 70.0):
        req = urllib.request.Request(
            self.base + path, data=None if body is None else json.dumps(body).encode(),
            method="GET" if body is None else "POST",
            headers={"Authorization": f"Bearer {self.token}", "Accept-Encoding": "gzip",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = _gzip.decompress(data)
                code = r.status
        except urllib.error.HTTPError as e:
            data, code = e.read(), e.code
            if e.headers.get("Content-Encoding") == "gzip":
                data = _gzip.decompress(data)
        except urllib.error.URLError as e:
            raise SystemExit(f"wdremote: API unreachable ({e.reason}) -- is the app running "
                             "on the laptop with REMOTE_API_ENABLED?")
        if raw:
            return code, data
        try:
            return code, json.loads(data or b"{}")
        except json.JSONDecodeError:
            return code, {"raw": data[:500].decode(errors="replace")}


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _parse_kv(items: Sequence[str]) -> Dict:
    out = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"wdremote: expected key=value, got {it!r}")
        k, v = it.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def cmd_token(tr, remote: Remote, a) -> int:
    tok = get_token(tr, remote, refresh=True)
    print(f"[token] cached in {_token_cache(remote)} ({len(tok)} chars)")
    return 0


def cmd_status(tr, remote: Remote, a) -> int:
    with ApiSession(tr, remote) as s:
        code, st = s.request("/api/v1/status")
    if code != 200 or a.json:
        _print_json(st)
        return 0 if code == 200 else 1
    rec = st.get("recorder", {})
    eng = st.get("engine", {})
    print(f"{st.get('state', '?').upper():8} project={st.get('project')} profile={st.get('profile')} "
          f"config={st.get('config_file')}  app={st.get('app', {}).get('commit')}")
    print(f"  fps={st.get('fps')} tracks={st.get('tracks')}  camera={st.get('camera')}")
    print(f"  engine={eng.get('model')}@{eng.get('imgsz')} trt={eng.get('trt_active')} "
          f"(requested {eng.get('trt_requested')})  osc={st.get('osc')}")
    print(f"  recorder={rec.get('state')} slot={rec.get('slot')} rec_frames={rec.get('recording_frames')} "
          f"play={rec.get('playback_frame')}/{rec.get('playback_total')} {rec.get('playback_file') or ''}")
    print(f"  remote control in RUN: {st.get('remote', {}).get('control_enabled')}  "
          f"uptime={st.get('uptime_s')} s")
    return 0


def cmd_events(tr, remote: Remote, a) -> int:
    with ApiSession(tr, remote) as s:
        since = a.since
        if since is None:
            since = s.request("/api/v1/status")[1].get("event_seq", 0) if a.follow else 0
        types = f"&types={a.types}" if a.types else ""
        while True:
            code, ev = s.request(f"/api/v1/events?since={since}&wait={25 if a.follow else 0}{types}")
            if code != 200:
                _print_json(ev)
                return 1
            for e in ev["events"]:
                t = time.strftime("%H:%M:%S", time.localtime(e.pop("t")))
                seq, typ = e.pop("seq"), e.pop("type")
                print(f"{t} #{seq} {typ} {json.dumps(e, default=str)}", flush=True)
            since = ev["next"]
            if not a.follow:
                return 0


def cmd_commands(tr, remote: Remote, a) -> int:
    with ApiSession(tr, remote) as s:
        code, sc = s.request("/api/v1/commands")
    for name, info in sc.items():
        fields = ", ".join(f"{k}{'' if v['required'] else '?'}" for k, v in info["fields"].items())
        print(f"  {info['policy']:7} {name}({fields})  {info.get('doc', '')}")
    return 0


def _submit(s: "ApiSession", typ: str, args: Dict) -> int:
    code, res = s.request("/api/v1/command", {"type": typ, "args": args})
    print(f"[cmd] {typ} {args} -> {code} {res.get('error') or 'queued'}")
    return 0 if code == 200 else 1


def cmd_cmd(tr, remote: Remote, a) -> int:
    with ApiSession(tr, remote) as s:
        return _submit(s, a.type, _parse_kv(a.kv))


def cmd_record(tr, remote: Remote, a) -> int:
    with ApiSession(tr, remote) as s:
        if a.action == "start":
            if not a.slot:
                raise SystemExit("wdremote record start --slot N")
            return _submit(s, "StartRecordingSlot", {"slot": a.slot})
        return _submit(s, "StopRecording", {})


def cmd_state(tr, remote: Remote, a) -> int:
    with ApiSession(tr, remote) as s:
        return _submit(s, "SetState", {"state": a.state})


def cmd_logs(tr, remote: Remote, a) -> int:
    with ApiSession(tr, remote) as s:
        last: Optional[str] = None
        while True:
            code, lg = s.request(f"/api/v1/logs?tail={a.tail}")
            if code != 200:
                _print_json(lg)
                return 1
            lines = lg["lines"]
            new = lines
            if last is not None and last in lines:
                new = lines[len(lines) - lines[::-1].index(last):]
            for line in new:
                print(line, flush=True)
            if lines:
                last = lines[-1]
            if not a.follow:
                return 0
            time.sleep(2.0)


def cmd_snapshot(tr, remote: Remote, a) -> int:
    with ApiSession(tr, remote) as s:
        code, data = s.request("/api/v1/snapshot.jpg", raw=True)
    if code != 200:
        print(data[:300].decode(errors="replace"))
        return 1
    out = Path(a.out or RUNS_DIR / f"snapshot-{new_stamp()}.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"[snapshot] {len(data)} B -> {_rel(out)}")
    return 0


def cmd_ls(tr, remote: Remote, a) -> int:
    with ApiSession(tr, remote) as s:
        code, res = s.request(f"/api/v1/files?path={urllib.request.quote(a.path or '')}")
    if code != 200:
        _print_json(res)
        return 1
    for d in res.get("dirs", []):
        print(f"  {'<dir>':>10}  {d['name']}/")
    for f in res.get("files", []):
        print(f"  {fmt_bytes(f['size']):>10}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(f['mtime']))}  {f['name']}")
    for r in res.get("roots", []):
        print(f"  {r}/")
    return 0


def cmd_clip(tr, remote: Remote, a) -> int:
    """Cut a small excerpt on the laptop (STANDBY), then pull just that."""
    with ApiSession(tr, remote) as s:
        code, job = s.request("/api/v1/clip", {"path": a.path, "start": a.start,
                                               "frames": a.frames, "scale": a.scale,
                                               "codec": a.codec})
        if code != 200:
            _print_json(job)
            return 1
        while job.get("state") == "running":
            time.sleep(1.0)
            _, job = s.request(f"/api/v1/jobs/{job['id']}")
            print(f"[clip] {job.get('state')} {int(100 * job.get('progress', 0))} %", flush=True)
    if job.get("state") != "done":
        _print_json(job)
        return 1
    print(f"[clip] {job['output']} ({fmt_bytes(job.get('size', 0))}, {job.get('frames')} frames)")
    if a.pull:
        man = run_agent(tr, remote, "manifest", {"paths": [job["output"]]})
        pull_files(tr, remote, man["files"], REPO)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_SLOT_HANDLERS: Dict[str, object] = {}


def _wdslot():
    """Lazy import of the sibling deploy/slot module (extra/wdslot.py)."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    sys.modules.setdefault("wdremote", sys.modules[__name__])
    import wdslot
    return wdslot


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wdremote", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host")
    ap.add_argument("--root")
    ap.add_argument("--local", action="store_true",
                    help="treat the 'remote' as this machine (tests / dry runs)")
    ap.add_argument("--slot", choices=["live", "dev"], default="live",
                    help="target code tree for run/pytest/py/replay (dev = wdremote deploy slot)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="save host/root to the config file")
    s.add_argument("--os", choices=["windows", "posix"])
    s.add_argument("--python", help="remote python override")
    s.add_argument("--bwlimit", type=int, help="default sftp limit, Kbit/s (0 = none)")

    sub.add_parser("doctor", help="check ssh, shell, venv python, git")

    s = sub.add_parser("inventory", help="git state, stack, engines, projects (saved as JSON)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--freeze", action="store_true", help="include every package version")

    s = sub.add_parser("probe", help="measure ssh throughput")
    s.add_argument("--mb", type=int, default=8)

    s = sub.add_parser("plan", help="what is on the laptop vs here, per tier, with ETA")
    s.add_argument("--mbps", type=float)
    s.add_argument("--probe", action="store_true")
    s.add_argument("--since", help="YYYY-MM-DD cutoff for 'recent recordings' (default 30 days)")
    s.add_argument("--dest")
    s.add_argument("-v", "--verbose", action="store_true")

    s = sub.add_parser("pull", help="resumable mirror of remote paths/tiers into this repo")
    s.add_argument("paths", nargs="*", help="paths relative to the remote root")
    s.add_argument("--tier", action="append", choices=["P0", "P1", "P2", "P3"])
    s.add_argument("--since", help="YYYY-MM-DD: only files modified after")
    s.add_argument("--include", action="append", help="glob on the relative path")
    s.add_argument("--exclude", action="append")
    s.add_argument("--max-gb", type=float, help="stop adding files beyond this total")
    s.add_argument("--bwlimit", type=int, help="Kbit/s for this pull")
    s.add_argument("--dest", help="local root (default: this repo)")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--follow-symlinks", action="store_true")

    s = sub.add_parser("run", help="run a command on the laptop (cwd application/)")
    s.add_argument("--cwd", default="application")
    s.add_argument("argv", nargs=argparse.REMAINDER)

    s = sub.add_parser("pytest", help="run the unit suite on the laptop")
    s.add_argument("args", nargs=argparse.REMAINDER)

    s = sub.add_parser("py", help="upload + run a local script on the laptop, fetch its out/")
    s.add_argument("script")
    s.add_argument("--with", dest="with_", action="append", help="extra file to upload")
    s.add_argument("--fetch", action="append", help="remote dir to fetch afterwards")
    s.add_argument("--cwd", default="application")
    s.add_argument("args", nargs=argparse.REMAINDER)

    s = sub.add_parser("replay", help="tests/replay.py on the laptop; fetch the summary")
    s.add_argument("scenario")
    s.add_argument("--trt", action="store_true")
    s.add_argument("--score", action="store_true")
    s.add_argument("--cache", action="store_true")
    s.add_argument("--start", type=int)
    s.add_argument("--frames", type=int)
    s.add_argument("--timeline", action="store_true")
    s.add_argument("--logs", action="store_true", help="also fetch the session logs")
    s.add_argument("--set", action="append", metavar="KEY=VALUE")

    sub.add_parser("bundle", help="git bundle --all (or .git zip) of the laptop checkout -> local")
    _SLOT_HANDLERS.update(_wdslot().add_commands(sub))

    # -- live app (in-app remote API over an SSH tunnel) --
    sub.add_parser("token", help="(re)fetch the app's API token over ssh")
    s = sub.add_parser("status", help="live app status")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("events", help="event stream (toasts, alerts, calibration, readiness...)")
    s.add_argument("--follow", "-f", action="store_true")
    s.add_argument("--since", type=int)
    s.add_argument("--types", help="comma list, e.g. Alert,Toast,ReadinessResult")
    sub.add_parser("commands", help="remote command allowlist with policy classes")
    s = sub.add_parser("cmd", help="submit one command: cmd SetSensitivity value=55")
    s.add_argument("type")
    s.add_argument("kv", nargs="*", metavar="key=value")
    s = sub.add_parser("record", help="record start --slot N | record stop")
    s.add_argument("action", choices=["start", "stop"])
    s.add_argument("--slot", type=int)
    s = sub.add_parser("state", help="state run | standby")
    s.add_argument("state", choices=["run", "standby"])
    s = sub.add_parser("logs", help="tail the app log")
    s.add_argument("--tail", type=int, default=100)
    s.add_argument("--follow", "-f", action="store_true")
    s = sub.add_parser("snapshot", help="save the current preview JPEG")
    s.add_argument("-o", "--out")
    s = sub.add_parser("ls", help="list shared files via the API (projects/, logs/)")
    s.add_argument("path", nargs="?", default="")
    s = sub.add_parser("clip", help="cut an excerpt on the laptop, optionally pull it")
    s.add_argument("path", help="projects/<p>/recordings/<file>")
    s.add_argument("--start", type=int, default=0)
    s.add_argument("--frames", type=int, default=300)
    s.add_argument("--scale", type=float, default=0.5)
    s.add_argument("--codec", default="mp4v", choices=["mp4v", "MJPG", "FFV1"])
    s.add_argument("--pull", action="store_true")
    return ap


_PASSTHROUGH = {"run": "argv", "pytest": "args", "py": "args", "slot": "args"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    # REMAINDER does not capture leading dash-args inside a subparser
    # (`pytest -x`, `slot run -- --project p`): collect them explicitly.
    a, extra = build_parser().parse_known_args(argv)
    if extra:
        dest = _PASSTHROUGH.get(a.cmd)
        if dest is None:
            build_parser().error(f"unrecognized arguments: {' '.join(extra)}")
        setattr(a, dest, list(getattr(a, dest) or []) + [x for x in extra if x != "--"])
    if a.cmd == "setup":
        return cmd_setup(a)
    remote = load_remote(host=a.host, root=a.root, **({"os": "posix"} if a.local else {}))
    tr = LocalTransport(remote) if a.local else SshTransport(remote)
    if a.slot == "dev" and a.cmd in ("run", "pytest", "py", "replay"):
        from dataclasses import replace
        remote = replace(remote, root=_wdslot().dev_root(remote))
        tr.remote = remote
    handlers = {
        "doctor": cmd_doctor, "inventory": cmd_inventory, "probe": cmd_probe,
        "plan": cmd_plan, "pull": cmd_pull, "run": cmd_run, "pytest": cmd_pytest,
        "py": cmd_py, "replay": cmd_replay, "bundle": cmd_bundle,
        "token": cmd_token, "status": cmd_status, "events": cmd_events,
        "commands": cmd_commands, "cmd": cmd_cmd, "record": cmd_record, "state": cmd_state,
        "logs": cmd_logs, "snapshot": cmd_snapshot, "ls": cmd_ls, "clip": cmd_clip,
        **_SLOT_HANDLERS,
    }
    return handlers[a.cmd](tr, remote, a)


if __name__ == "__main__":
    sys.exit(main())
