"""wdslot — fast deploy / test / release on the prod laptop (used by wdremote).

Two code trees ("slots") live side by side on the laptop:

    <launcher dir>\\WallDance        LIVE  - the launcher's git checkout, follows the
                                    `release` branch (decision D1). Never touched here.
    <launcher dir>\\WallDance-dev    DEV   - a plain file tree managed by `wdremote deploy`.
                                    No .git; it shares the live venv, models/ and projects/
                                    through junctions, so deploying a branch moves only the
                                    tracked files that changed (KBs over 4G, not GBs).

The agile loop during a short online window::

    wdremote deploy my-feature              # incremental sync of a git ref into DEV
    wdremote --slot dev pytest              # unit suite with the laptop's stack
    wdremote --slot dev replay hangar-aerial --trt --score
    wdremote slot run --slot dev -- --project <p> --slot 3   # GUI on the laptop's desktop
    wdremote status / events -f / record ...                 # drive it (remote API)
    wdremote slot stop --slot dev
    wdremote deploy <previous ref>          # rollback = deploy the old ref
    wdremote release-check my-feature       # what promoting it would do on the laptop

Nothing here pushes. Promoting to the field means Thomas pushes the validated
commit to `release` (release-check prints the exact command), and the launcher
fast-forwards LIVE at its next start.

Launcher exe (ARCH-2/19): `wdremote launcher build` builds a pinned
PyInstaller exe ON the laptop from launcher/ sources. `wdremote launcher install`
swaps it in with a backup and writes launcher.json {"branch": "release"}.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import wdremote as wr

# Never shipped to a slot: the old committed launcher exe and prototypes.
SLOT_EXCLUDE = ("launcher/release/", "prototypes/")
# Shared with LIVE through junctions (data + heavy env; never copied).
SHARED_DIRS = ("application/.venv", "models")
MANIFEST = ".wdslot.json"


# ---------------------------------------------------------------------------
# Local git side
# ---------------------------------------------------------------------------

def _git(*args: str, input: Optional[bytes] = None) -> bytes:
    return subprocess.run(["git", "-C", str(wr.REPO), *args], input=input,
                          capture_output=True, check=True).stdout


def resolve_ref(ref: str) -> Tuple[str, str]:
    """(full commit sha, one-line description) for a local ref/branch/sha."""
    sha = _git("rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    desc = _git("log", "-1", "--format=%h %ad %s", "--date=short", sha).decode().strip()
    return sha, desc


def tree_files(commit: str) -> Dict[str, str]:
    """{path: blob sha} of every tracked file at ``commit`` (minus SLOT_EXCLUDE)."""
    out: Dict[str, str] = {}
    for rec in _git("ls-tree", "-r", "-z", commit).split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, kind, sha = meta.split()
        p = path.decode()
        if kind != b"blob" or mode == b"120000" or p.startswith(SLOT_EXCLUDE):
            continue
        out[p] = sha.decode()
    return out


def build_tarball(files: Dict[str, str]) -> bytes:
    """tar.gz of the given {path: blob} using one `git cat-file --batch`."""
    paths = sorted(files)
    req = "".join(f"{files[p]}\n" for p in paths).encode()
    raw = _git("cat-file", "--batch", input=req)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        pos = 0
        for p in paths:
            nl = raw.index(b"\n", pos)
            header = raw[pos:nl].split()
            size = int(header[2])
            data = raw[nl + 1: nl + 1 + size]
            pos = nl + 1 + size + 1
            info = tarfile.TarInfo(p)
            info.size = size
            info.mtime = int(time.time())
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def plan_sync(new: Dict[str, str], old: Dict[str, str]) -> Tuple[Dict[str, str], List[str]]:
    """(files to write, paths to delete) to go from manifest ``old`` to ``new``."""
    changed = {p: s for p, s in new.items() if old.get(p) != s}
    removed = sorted(p for p in old if p not in new)
    return changed, removed


# ---------------------------------------------------------------------------
# Laptop side: agent shipped over SSH (stdlib only, runs with the live venv)
# ---------------------------------------------------------------------------

SLOT_AGENT_PY = r'''
import json, os, sys, time, shutil, tarfile, subprocess, py_compile, io
from pathlib import Path, PurePosixPath

MARK = "@@WDREMOTE-JSON@@"

def _emit(res):
    sys.stdout.write("\n" + MARK + "\n" + json.dumps(res) + "\n" + MARK + "\n")
    sys.stdout.flush()

def _link_dir(link, target):
    """Directory junction on Windows (no admin needed), symlink elsewhere."""
    link, target = Path(link), Path(target)
    if link.exists() or link.is_symlink():
        return "exists"
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True, text=True)
        return "junction" if r.returncode == 0 else ("ERROR " + (r.stdout + r.stderr).strip())
    os.symlink(str(target), str(link), target_is_directory=True)
    return "symlink"

def manifest(args):
    root = Path(args["slot_root"])
    m = root / ".wdslot.json"
    return json.loads(m.read_text()) if m.is_file() else {}

def apply(args):
    root = Path(args["slot_root"]); live = Path(args["live_root"])
    tgz = Path(args["tarball"])
    root.mkdir(parents=True, exist_ok=True)
    written, deleted, problems = 0, 0, []
    with tarfile.open(tgz, "r:gz") as tar:
        for m in tar.getmembers():
            p = PurePosixPath(m.name)
            if p.is_absolute() or ".." in p.parts or not m.isfile():
                problems.append("skipped unsafe member " + m.name); continue
            dst = root.joinpath(*p.parts)
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".wdtmp")
            with tar.extractfile(m) as src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out)
            os.replace(tmp, dst)
            written += 1
    for rel in args.get("delete", []):
        p = PurePosixPath(rel)
        if p.is_absolute() or ".." in p.parts:
            continue
        f = root.joinpath(*p.parts)
        if f.is_file():
            f.unlink(); deleted += 1
    links = {}
    for rel in args.get("shared", []):
        links[rel] = _link_dir(root.joinpath(*rel.split("/")), live.joinpath(*rel.split("/")))
    proj = root / "projects"
    if args.get("isolated_projects"):
        proj.mkdir(exist_ok=True); links["projects"] = "isolated"
    else:
        links["projects"] = _link_dir(proj, live / "projects")
    (root / "logs").mkdir(exist_ok=True)
    bad = []
    src = root / "application" / "src"
    for f in sorted(list(src.rglob("*.py")) + list((root / "application" / "tests").glob("*.py"))):
        try:
            compile(f.read_bytes(), str(f), "exec")      # syntax check, writes nothing
        except Exception as e:
            bad.append(f"{f.relative_to(root)}: {e}")
    man = {"ref": args["ref"], "commit": args["commit"], "desc": args.get("desc"),
           "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": args.get("by"),
           "files": args["files"]}
    (root / ".wdslot.json").write_text(json.dumps(man))
    (root / "DEPLOYED.json").write_text(json.dumps(
        {k: man[k] for k in ("ref", "commit", "desc", "deployed_at", "by")}, indent=1))
    try:
        tgz.unlink()
    except OSError:
        pass
    return {"written": written, "deleted": deleted, "links": links,
            "compile_errors": bad[:20], "problems": problems[:20]}

def ps(args):
    """WallDance app / launcher processes with the slot they run from."""
    rows = []
    if os.name == "nt":
        cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
               "($_.CommandLine -like '*main.py*' -or $_.Name -like 'WallDanceLauncher*') } | "
               "Select-Object ProcessId,Name,CommandLine,CreationDate | ConvertTo-Json -Compress")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True)
        try:
            data = json.loads(r.stdout or "[]")
        except ValueError:
            data = []
        for d in (data if isinstance(data, list) else [data]):
            rows.append({"pid": d.get("ProcessId"), "name": d.get("Name"),
                         "cmd": d.get("CommandLine"), "started": str(d.get("CreationDate"))})
    else:
        r = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True)
        for line in r.stdout.splitlines()[1:]:
            if "src/main.py" in line and "bash -c" not in line:
                pid, _, cmd = line.strip().partition(" ")
                rows.append({"pid": int(pid), "name": "python", "cmd": cmd})
    for r_ in rows:
        c = (r_.get("cmd") or "").replace("\\", "/").lower()
        r_["slot"] = ("dev" if args["dev_root"].replace("\\", "/").lower() in c else
                      "live" if args["live_root"].replace("\\", "/").lower() in c else
                      ("launcher" if "walldancelauncher" in c else "?"))
    return {"processes": rows}

def main(op, args):
    _emit({"manifest": manifest, "apply": apply, "ps": ps}[op](args))
'''


def run_slot_agent(tr, remote, op: str, args: dict, timeout: float = 600) -> dict:
    return wr.run_agent(tr, remote, op, args, timeout=timeout, script_src=SLOT_AGENT_PY)


def dev_root(remote) -> str:
    return remote.dev_root or (remote.root.rstrip("/\\") + "-dev")


def launcher_dir(remote) -> str:
    """The launcher exe lives next to the live checkout (<dir>/WallDance)."""
    r = remote.root.replace("\\", "/").rstrip("/")
    return r.rsplit("/", 1)[0] if "/" in r else r


def _slot_remote(remote, slot: str):
    """A Remote whose root is the slot (python = the slot's junctioned venv)."""
    from dataclasses import replace
    return replace(remote, root=dev_root(remote) if slot == "dev" else remote.root)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_deploy(tr, remote, a) -> int:
    """Incrementally sync a git ref into the DEV slot."""
    commit, desc = resolve_ref(a.ref)
    files = tree_files(commit)
    droot = dev_root(remote)
    old = run_slot_agent(tr, remote, "manifest", {"slot_root": droot})
    old_files = {} if a.full else (old.get("files") or {})
    changed, removed = plan_sync(files, old_files)
    print(f"[deploy] {a.ref} = {desc}")
    print(f"[deploy] dev slot {droot}: was {old.get('commit', 'empty')[:12]} "
          f"({old.get('ref', '-')}) -> {len(changed)} file(s) to write, {len(removed)} to delete")
    if a.dry_run:
        for p in sorted(changed)[:40]:
            print(f"   + {p}")
        for p in removed[:40]:
            print(f"   - {p}")
        return 0
    stamp = wr.new_stamp()
    scratch = wr.remote_scratch(stamp)
    blob = build_tarball(changed) if changed else build_tarball({})
    local_tgz = wr.RUNS_DIR / f"deploy-{stamp}.tgz"
    local_tgz.parent.mkdir(parents=True, exist_ok=True)
    local_tgz.write_bytes(blob)
    print(f"[deploy] payload {wr.fmt_bytes(len(blob))}")
    lines = wr.sftp_mkdirs(remote, scratch)
    lines.append(f'put "{local_tgz}" "{remote.sftp_path(scratch + "/deploy.tgz")}"')
    if tr.sftp_batch(lines, compress=False) != 0:
        print("[deploy] upload failed")
        return 2
    res = run_slot_agent(tr, remote, "apply", {
        "slot_root": droot, "live_root": remote.root,
        "tarball": remote.path(scratch + "/deploy.tgz"),
        "delete": removed, "files": files, "ref": a.ref, "commit": commit, "desc": desc,
        "by": os.environ.get("USER", "dev"), "shared": list(SHARED_DIRS),
        "isolated_projects": bool(a.isolated_projects)})
    local_tgz.unlink(missing_ok=True)
    print(f"[deploy] wrote {res['written']}, deleted {res['deleted']}; links {res['links']}")
    for line in res.get("problems", []):
        print(f"   ! {line}")
    if res.get("compile_errors"):
        print("[deploy] !! compile errors:")
        for line in res["compile_errors"]:
            print(f"   {line}")
        return 1
    print(f"[deploy] dev slot now at {commit[:12]} ({a.ref}). "
          f"Next: wdremote --slot dev pytest | wdremote slot run --slot dev")
    return 0


def cmd_slot_status(tr, remote, a) -> int:
    droot = dev_root(remote)
    dev = run_slot_agent(tr, remote, "manifest", {"slot_root": droot})
    inv = wr.run_agent(tr, remote, "inventory", {})
    g = inv.get("git", {})
    head = (g.get("head") or g.get("HEAD") or "?")
    print(f"LIVE {remote.root}: {str(head)[:12]}  {((g.get('status') or '').splitlines() or [''])[0]}")
    if dev:
        print(f"DEV  {droot}: {dev.get('commit', '')[:12]} ({dev.get('ref')}) "
              f"deployed {dev.get('deployed_at')} -- {dev.get('desc')}")
    else:
        print(f"DEV  {droot}: (empty -- `wdremote deploy <ref>`)")
    ps = run_slot_agent(tr, remote, "ps", {"dev_root": droot, "live_root": remote.root})
    for p in ps["processes"]:
        print(f"  running: pid {p['pid']} [{p['slot']}] {(p.get('cmd') or '')[:110]}")
    if not ps["processes"]:
        print("  running: nothing")
    return 0


def _ps_encoded(script: str) -> List[str]:
    """powershell -EncodedCommand: immune to cmd.exe/ssh quoting."""
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    return ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc]


def windows_launch_script(task: str, slot_root: str, app_args: Sequence[str],
                          env: Dict[str, str]) -> str:
    """PowerShell that (re)registers an *interactive* scheduled task and starts it.

    Processes started from an SSH session live in session 0 and cannot show a
    GUI; a task with LogonType Interactive runs in the logged-on user's desktop
    session instead (the SSH user must be that user)."""
    sets = "".join(f'set "{k}={v}"&& ' for k, v in env.items())
    args = " ".join(a.replace('"', "") for a in app_args)
    root = slot_root.replace("/", "\\")
    cmdline = (f'/c {sets}cd /d "{root}" && call run.bat {args} '
               f'> "{root}\\logs\\launch-console.log" 2>&1')
    return (
        "$ErrorActionPreference='Stop';"
        f"$a=New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '{cmdline.replace(chr(39), chr(39) * 2)}';"
        "$p=New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive;"
        "$s=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 3) "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;"
        f"Register-ScheduledTask -TaskName '{task}' -Action $a -Principal $p -Settings $s -Force | Out-Null;"
        f"Start-ScheduledTask -TaskName '{task}';"
        f"Write-Output 'started {task}'")


def cmd_slot_run(tr, remote, a) -> int:
    """Start the app (GUI) of a slot on the laptop's desktop."""
    root = dev_root(remote) if a.slot == "dev" else remote.root
    ps = run_slot_agent(tr, remote, "ps", {"dev_root": dev_root(remote), "live_root": remote.root})
    apps = [p for p in ps["processes"] if p["slot"] in ("dev", "live")]
    if apps and not a.force:
        print(f"[slot] an app is already running ({apps[0]['slot']}, pid {apps[0]['pid']}); "
              "stop it first (camera + ports are exclusive) or pass --force")
        return 1
    app_args = list(a.args)
    if app_args and app_args[0] == "--":
        app_args = app_args[1:]
    env = {"WD_SLOT": a.slot}
    if a.slot == "dev":
        env["WD_REMOTE_ALLOW_QUIT"] = "1"    # lets `slot stop` quit gracefully
        env["WD_REMOTE_CONTROL"] = "1"       # test session: control in RUN allowed
    if remote.win:
        if a.via_launcher and a.slot == "live":
            exe = launcher_dir(remote).replace("/", "\\") + "\\WallDanceLauncher.exe"
            script = ("$ErrorActionPreference='Stop';"
                      f"$a=New-ScheduledTaskAction -Execute '{exe}';"
                      "$p=New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive;"
                      "Register-ScheduledTask -TaskName 'WallDance-launcher' -Action $a "
                      "-Principal $p -Force | Out-Null; Start-ScheduledTask -TaskName "
                      "'WallDance-launcher'; Write-Output 'started launcher'")
        else:
            script = windows_launch_script(f"WallDance-{a.slot}", root, app_args, env)
        p = tr.run(" ".join(wr._q_cmd(x) for x in _ps_encoded(script)), timeout=120)
        out = (p.stdout or b"").decode(errors="replace").strip()
        err = (p.stderr or b"").decode(errors="replace").strip()
        print(f"[slot] {out or err}")
        return p.returncode
    # posix (dev37 --local tests): plain background process
    sets = " ".join(f"{k}={v}" for k, v in env.items())
    cmd = (f"cd {root} && mkdir -p logs && ({sets} setsid nohup bash run.sh "
           f"{' '.join(app_args)} > logs/launch-console.log 2>&1 < /dev/null &)")
    p = tr.run(cmd, timeout=30)
    print(f"[slot] started {a.slot} (posix) rc={p.returncode}")
    return p.returncode


def cmd_slot_stop(tr, remote, a) -> int:
    """Graceful quit through the API when allowed (dev slot), else terminate."""
    if not a.kill:
        try:
            with wr.ApiSession(tr, remote) as s:
                code, res = s.request("/api/v1/command", {"type": "Quit", "args": {}})
                if code == 403 and "RUN" in (res.get("error") or ""):
                    # DEV sessions allow control in RUN: drop to STANDBY, then quit.
                    s.request("/api/v1/command", {"type": "SetState", "args": {"state": "standby"}})
                    time.sleep(1.5)
                    code, res = s.request("/api/v1/command", {"type": "Quit", "args": {}})
            if code == 200:
                print("[slot] quit requested (graceful)")
                return 0
            print(f"[slot] graceful quit refused: {res.get('error')} -> terminating")
        except SystemExit as e:
            print(f"[slot] API unreachable ({e}) -> terminating")
    root = dev_root(remote) if a.slot == "dev" else remote.root
    if remote.win:
        needle = root.replace("/", "\\")
        script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
                  f"'*{needle}*main.py*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId "
                  "-Force; Write-Output \"stopped $($_.ProcessId)\" }")
        p = tr.run(" ".join(wr._q_cmd(x) for x in _ps_encoded(script)), timeout=60)
    else:
        p = tr.run(f"pkill -f '{root}.*src/main.py' || true", timeout=30)
    print(f"[slot] {(p.stdout or b'').decode(errors='replace').strip() or 'nothing to stop'}")
    return 0


def classify_change(path: str) -> Optional[str]:
    if path in ("install.bat", "install.sh", "application/pyproject.toml"):
        return "REINSTALL (the launcher re-runs install.bat: unpinned deps until ARCH-4!)"
    if path.startswith("launcher/"):
        return "launcher source (needs `wdremote launcher build` + install; the exe does not self-update)"
    if path in ("run.bat", "run.sh"):
        return "start script"
    if path == "docs/OSC_CONTRACT.md" or path.startswith("application/src/core/osc_output"):
        return "OSC contract/output (operator-confirmed change? TouchDesigner impact)"
    if path.startswith("application/src/core/tracker") or path.startswith("application/src/core/pipeline"):
        return "tracking core (replay-gated: run the scenarios on the laptop first)"
    return None


def cmd_release_check(tr, remote, a) -> int:
    """Local-only: what promoting ``ref`` to the release branch changes."""
    commit, desc = resolve_ref(a.ref)
    base = a.base
    try:
        base_sha, base_desc = resolve_ref(base)
    except subprocess.CalledProcessError:
        base_sha, base_desc = None, f"{base} (not found locally)"
    print(f"[release-check] candidate {a.ref} = {desc}")
    print(f"[release-check] current release {base} = {base_desc}")
    if base_sha:
        names = _git("diff", "--name-only", f"{base_sha}..{commit}").decode().split()
        ahead = _git("rev-list", "--count", f"{base_sha}..{commit}").decode().strip()
        behind = _git("rev-list", "--count", f"{commit}..{base_sha}").decode().strip()
        print(f"  {ahead} commit(s) ahead, {behind} behind ({'FAST-FORWARD' if behind == '0' else 'NOT a fast-forward: the launcher will treat it as DIVERGED'})")
        flags: Dict[str, List[str]] = {}
        for n in names:
            c = classify_change(n)
            if c:
                flags.setdefault(c, []).append(n)
        print(f"  {len(names)} file(s) changed")
        for c, ns in flags.items():
            print(f"  ! {c}: {', '.join(ns[:6])}{' ...' if len(ns) > 6 else ''}")
        reinstall = [n for n in names if n in ("install.bat", "application/pyproject.toml")]
        if reinstall and "application/requirements-prod.txt" not in tree_files(commit):
            print("  !! DANGER: this release makes the laptop re-run install.bat WITHOUT "
                  "application/requirements-prod.txt -> an UNPINNED resolve (TensorRT 11, "
                  "dead engines). Ship the pin file in the same release (wdremote freeze-lock).")
    if a.tests:
        print("[release-check] running the unit suite at the candidate (temporary worktree)...")
        wt = Path(wr.RUNS_DIR) / f"release-check-{wr.new_stamp()}"
        _git("worktree", "add", "--detach", str(wt), commit)
        try:
            py = wr.REPO / "application" / ".venv" / "bin" / "python"
            r = subprocess.run([str(py), "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
                               cwd=wt / "application", capture_output=True, text=True)
            print("  " + (r.stdout.strip().splitlines() or ["(no output)"])[-1])
            if r.returncode != 0:
                print("  !! unit suite FAILED at the candidate")
        finally:
            _git("worktree", "remove", "--force", str(wt))
    print("\n  To promote (Thomas runs this; dev boxes never push):")
    print(f"    git push origin {commit}:refs/heads/release")
    print("  The laptop's launcher fast-forwards LIVE at its next start "
          "(or now: wdremote slot run --slot live --via-launcher).")
    return 0


# --- launcher exe build (on the laptop) -------------------------------------

LAUNCHER_BUILD_PS = r'''
$ErrorActionPreference = 'Stop'
Set-Location '{build_dir}'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {{ throw 'uv not found on PATH' }}
uv venv .venv --python 3.12 --allow-existing | Out-Null
uv pip install --python .venv\Scripts\python.exe -r requirements-win.lock | Out-Null
Set-Content -Path build_info.json -Value '{build_info}' -Encoding UTF8
.venv\Scripts\pyinstaller.exe --noconfirm --clean WallDanceLauncher.spec | Out-Null
$exe = Join-Path (Get-Location) 'dist\WallDanceLauncher.exe'
$h = (Get-FileHash $exe -Algorithm SHA256).Hash
Write-Output ("BUILT " + $exe + " " + (Get-Item $exe).Length + " " + $h)
'''

LAUNCHER_INSTALL_PS = r'''
$ErrorActionPreference = 'Stop'
$dir = '{launcher_dir}'
$new = '{new_exe}'
if (Get-Process -Name 'WallDanceLauncher' -ErrorAction SilentlyContinue) {{ throw 'launcher is running: close WallDance first' }}
$cur = Join-Path $dir 'WallDanceLauncher.exe'
if (Test-Path $cur) {{ Copy-Item $cur ($cur + '.bak-{stamp}') }}
Copy-Item $new $cur -Force
$cfg = Join-Path $dir 'launcher.json'
if (-not (Test-Path $cfg)) {{ Set-Content -Path $cfg -Value '{{"branch": "{branch}"}}' -Encoding UTF8 }}
Write-Output ("INSTALLED " + $cur + " (backup .bak-{stamp}); channel: " + (Get-Content $cfg -Raw))
'''


def cmd_launcher(tr, remote, a) -> int:
    ldir = launcher_dir(remote)
    build_rel_root = ldir.replace("\\", "/") + "/launcher-build"
    if a.action == "status":
        script = (f"$d='{ldir}'; Get-ChildItem $d -Filter 'WallDanceLauncher*' | "
                  "Select-Object Name,Length,LastWriteTime | Format-Table -AutoSize | Out-String; "
                  "if (Test-Path (Join-Path $d 'launcher.json')) { Get-Content (Join-Path $d 'launcher.json') } "
                  "else { 'launcher.json: absent (channel defaults to release in the new exe; old exe ignores it)' }; "
                  "if (Get-Process -Name 'WallDanceLauncher' -ErrorAction SilentlyContinue) { 'running: yes' } else { 'running: no' }")
        p = tr.run(" ".join(wr._q_cmd(x) for x in _ps_encoded(script)), timeout=60)
        print((p.stdout or b"").decode(errors="replace"))
        return p.returncode
    if a.action == "build":
        commit, desc = resolve_ref(a.ref)
        files = {p: s for p, s in tree_files(commit).items()
                 if p.startswith("launcher/") and not p.startswith("launcher/release/")}
        blob = build_tarball(files)
        stamp = wr.new_stamp()
        local_tgz = wr.RUNS_DIR / f"launcher-{stamp}.tgz"
        local_tgz.parent.mkdir(parents=True, exist_ok=True)
        local_tgz.write_bytes(blob)
        bremote = wr.Remote(**{**wr.asdict(remote), "root": build_rel_root})
        lines = wr.sftp_mkdirs(bremote, "src")
        lines.append(f'put "{local_tgz}" "{bremote.sftp_path("src/launcher.tgz")}"')
        tr.sftp_batch(lines)
        unpack = ("import tarfile,sys,shutil,os; d=sys.argv[1]; "
                  "shutil.rmtree(os.path.join(d,'launcher'), ignore_errors=True); "
                  "tarfile.open(os.path.join(d,'launcher.tgz')).extractall(d)")
        tr.run(remote.shell([remote.python_exe(), "-c", unpack, bremote.path("src")]), timeout=120)
        info = json.dumps({"commit": commit[:12], "ref": a.ref, "built": stamp}).replace("'", "")
        script = LAUNCHER_BUILD_PS.format(build_dir=bremote.path("src/launcher"), build_info=info)
        p = tr.run(" ".join(wr._q_cmd(x) for x in _ps_encoded(script)), timeout=1800)
        out = (p.stdout or b"").decode(errors="replace").strip()
        print(out or (p.stderr or b"").decode(errors="replace")[-2000:])
        local_tgz.unlink(missing_ok=True)
        return p.returncode
    if a.action == "install":
        new = launcher_dir(remote).replace("/", "\\") + "\\launcher-build\\src\\launcher\\dist\\WallDanceLauncher.exe"
        script = LAUNCHER_INSTALL_PS.format(launcher_dir=ldir.replace("/", "\\"), new_exe=new,
                                            stamp=wr.new_stamp(), branch=a.branch)
        p = tr.run(" ".join(wr._q_cmd(x) for x in _ps_encoded(script)), timeout=120)
        print((p.stdout or b"").decode(errors="replace").strip()
              or (p.stderr or b"").decode(errors="replace")[-2000:])
        return p.returncode
    return 2


# ---------------------------------------------------------------------------
# CLI wiring (called by wdremote.build_parser / main)
# ---------------------------------------------------------------------------

def add_commands(sub) -> Dict[str, object]:
    s = sub.add_parser("deploy", help="incrementally sync a git ref into the laptop's DEV slot")
    s.add_argument("ref")
    s.add_argument("--full", action="store_true", help="ignore the slot manifest, resend all")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--isolated-projects", action="store_true",
                   help="DEV gets its own projects/ instead of sharing LIVE's")

    s = sub.add_parser("slot", help="slot status | run | stop")
    s.add_argument("action", choices=["status", "run", "stop"])
    s.add_argument("--slot", dest="which", choices=["dev", "live"], default="dev")
    s.add_argument("--force", action="store_true")
    s.add_argument("--kill", action="store_true", help="stop: terminate without asking the app")
    s.add_argument("--via-launcher", action="store_true",
                   help="run --slot live through WallDanceLauncher.exe (applies release updates)")
    s.set_defaults(args=[])                 # app arguments after --, e.g. -- --project p --slot 3

    s = sub.add_parser("release-check", help="what promoting a ref to `release` does (local)")
    s.add_argument("ref")
    s.add_argument("--base", default="origin/release")
    s.add_argument("--tests", action="store_true", help="run the unit suite at the ref")

    s = sub.add_parser("launcher", help="launcher exe: status | build [--ref] | install")
    s.add_argument("action", choices=["status", "build", "install"])
    s.add_argument("--ref", default="HEAD")
    s.add_argument("--branch", default="release", help="channel written to launcher.json on install")

    def _slot(tr, remote, a):
        a.slot = a.which
        return {"status": cmd_slot_status, "run": cmd_slot_run, "stop": cmd_slot_stop}[a.action](tr, remote, a)

    return {"deploy": cmd_deploy, "slot": _slot, "release-check": cmd_release_check,
            "launcher": cmd_launcher}
