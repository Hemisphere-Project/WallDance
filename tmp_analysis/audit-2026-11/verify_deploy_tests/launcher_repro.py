"""Throwaway reproduction of the launcher claims (remote-deploy#2/#3/#4/#6).
Real launcher/git_manager.py + launcher/gui.py sequence logic, dulwich 1.2.17, fake Tk + fake .bat runs.
Writes only under the scratch work dir given as argv[1]."""
import os
import socket
import stat
import sys
import threading
import time
import types

WORK = sys.argv[1]
ROOT = "/data/WallDance"
sys.path.insert(0, ROOT + "/launcher")

# --- fake Tk so gui.py imports without a display --------------------------------
ctk = types.ModuleType("customtkinter")


class _CTk:
    def __init__(self, *a, **k):
        pass


ctk.CTk = _CTk
sys.modules["customtkinter"] = ctk
tk = types.ModuleType("tkinter")
tk.messagebox = types.SimpleNamespace()
tk.filedialog = types.SimpleNamespace()
sys.modules["tkinter"] = tk
sys.modules["tkinter.messagebox"] = tk.messagebox

import dulwich  # noqa: E402
from dulwich import porcelain  # noqa: E402
from dulwich.repo import Repo  # noqa: E402
from git_manager import GitManager, UpdateStatus, DirtyWorkingTreeError  # noqa: E402
import gui  # noqa: E402

print("dulwich", ".".join(map(str, dulwich.__version__)))
IDENT = b"Test <test@example.com>"


def commit(repo_path, relpath, content, message):
    path = os.path.join(repo_path, relpath)
    os.makedirs(os.path.dirname(path) or repo_path, exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(content)
    porcelain.add(repo_path, paths=[path])
    return porcelain.commit(repo_path, message=message.encode(), author=IDENT, committer=IDENT)


def commit_on(repo_path, branch, relpath, content, message):
    with Repo(repo_path) as repo:
        prev = repo.refs.read_ref(b"HEAD")
        ref = b"refs/heads/" + branch.encode()
        if ref not in repo.refs:
            repo.refs[ref] = repo.head()
        repo.refs.set_symbolic_ref(b"HEAD", ref)
    sha = commit(repo_path, relpath, content, message)
    with Repo(repo_path) as repo:
        repo.refs.set_symbolic_ref(b"HEAD", prev[len(b"ref: "):])
    return sha


def make_remote(name):
    d = os.path.join(WORK, name, "remote")
    os.makedirs(d)
    repo = porcelain.init(d)
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    repo.close()
    commit(d, "README.md", "hello\n", "init")
    commit(d, "install.bat", "rem install v1\n", "installer")
    commit(d, "application/pyproject.toml", "[project]\nname='wd'\n", "pyproject")
    commit(d, "application/requirements-prod.txt", "numpy==2.4.4\ntensorrt-cu13==11.0.0.114\n", "pin")
    commit(d, "a_first.txt", "v1\n", "a")
    commit(d, "zz/late.txt", "v1\n", "z")
    commit_on(d, "release", "release.txt", "r1\n", "release 1")
    return d


def head(path):
    with Repo(path) as r:
        return r.head().decode()[:10]


def read(path, rel):
    with open(os.path.join(path, rel)) as f:
        return f.read().strip()


class FakeLauncher(gui.LauncherGUI):
    """LauncherGUI._sequence_thread / _run_install_interactive unchanged; Tk and .bat runs faked."""

    def __init__(self, repo_url, target_dir, update_answer=True, install_rcs=(), choices=(), run_rc=0):
        self.repo_url, self.target_dir, self.branch = repo_url, target_dir, "release"
        self.git_manager = GitManager(repo_url, target_dir, branch="release")
        self.needs_install = False            # gui.py:109 -- a fresh launcher process
        self.install_log_lines, self.install_alerts_shown = [], set()
        self.logs_visible = True
        self.trace = []
        self._update_answer, self._install_rcs, self._choices, self._run_rc = (
            update_answer, list(install_rcs), list(choices), run_rc)
        self.progress_bar = types.SimpleNamespace(stop=lambda: None)
        self.action_btn = types.SimpleNamespace(
            configure=lambda **k: self.trace.append(f"   [button] {k.get('text', k)}"))

    # Tk plumbing
    def after(self, _ms, fn=None, *a):
        if fn is not None:
            fn(*a)

    def destroy(self):
        self.trace.append("   [launcher window closes]")

    def deiconify(self):
        pass

    def withdraw(self):
        pass

    def show_logs_force(self):
        pass

    def update_status(self, text):
        self.trace.append(f"   [status] {text}")

    def append_log(self, text):
        self.trace.append("   log: " + text.rstrip())

    def _ensure_desktop_shortcut(self):
        pass

    # dialogs
    def ask_update_sync(self):
        self.trace.append(f"   DIALOG askyesno 'Update Available' -> {'Yes' if self._update_answer else 'No'}")
        return self._update_answer

    def ask_choice_sync(self, title, message, yes_text="Yes", no_text="No"):
        ans = self._choices.pop(0)
        self.trace.append(f"   DIALOG '{title}' [{no_text} | {yes_text}] -> {yes_text if ans else no_text}")
        return ans

    def show_error_sync(self, title, message):
        self.trace.append(f"   DIALOG error '{title}': {message[:90]}")

    def show_warning_sync(self, title, message):
        self.trace.append(f"   DIALOG warning '{title}'")

    # .bat runs
    def run_bat_monitored(self, bat):
        rc = self._install_rcs.pop(0)
        self.trace.append(f"   >>> RUN {os.path.basename(bat)} -> exit {rc}")
        if rc:
            self.install_log_lines.append("ERROR: Dependency installation failed (pinned sync).")
        return rc

    def run_bat_sync(self, bat):
        self.trace.append(f"   >>> RUN {os.path.basename(bat)} -> exit {self._run_rc}")
        return self._run_rc

    def start(self, label):
        print(f"\n -- launcher start: {label}")
        self._sequence_thread()
        print("\n".join(self.trace))
        return self


# =====================================================================================
print("\n=== A1 (remote-deploy#2): a release that changes ONLY application/requirements-prod.txt ===")
remote = make_remote("a1")
local = os.path.join(WORK, "a1", "local")
gm = GitManager(remote, local)
gm.clone()
commit_on(remote, "release", "application/requirements-prod.txt",
          "numpy==2.4.4\ntensorrt-cu13==11.3.0.99\nultralytics==8.4.173\n", "bump the pinned stack")
FakeLauncher(remote, local).start("pin-only release, operator answers Yes")
print("   on disk now:", read(local, "application/requirements-prod.txt").replace("\n", " | "))
sys.path.insert(0, ROOT + "/extra")
import wdslot  # noqa: E402
print("   wdslot.classify_change('application/requirements-prod.txt') ->",
      wdslot.classify_change("application/requirements-prod.txt"))
print("   wdslot.classify_change('install.bat') ->", wdslot.classify_change("install.bat"))

# =====================================================================================
print("\n=== A2 (remote-deploy#2): install fails after an update, the launcher is closed and restarted ===")
remote = make_remote("a2")
local = os.path.join(WORK, "a2", "local")
GitManager(remote, local).clone()
commit_on(remote, "release", "install.bat", "rem install v2 (new deps)\n", "installer change")
FakeLauncher(remote, local, install_rcs=[1], choices=[False]).start(
    "update accepted, install.bat fails (4G), operator picks 'Exit (fix later)'")
FakeLauncher(remote, local).start("the next day, a new launcher process")

# =====================================================================================
print("\n=== A3 (remote-deploy#3): update() fails after HEAD moved ===")
remote = make_remote("a3")
local = os.path.join(WORK, "a3", "local")
GitManager(remote, local).clone()
old_head = head(local)
commit_on(remote, "release", "a_first.txt", "v2\n", "change a_first")
commit_on(remote, "release", "zz/late.txt", "v2\n", "change zz/late")
os.chmod(os.path.join(local, "zz", "late.txt"), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # stands for a locked file
print("   before: HEAD", old_head, "| a_first.txt =", read(local, "a_first.txt"), "| zz/late.txt =", read(local, "zz/late.txt"),
      "| dirty_files() =", GitManager(remote, local).dirty_files())
FakeLauncher(remote, local).start("release available, operator answers Yes, one file cannot be written")
print("   after : HEAD", head(local), "(remote release", head(remote) and Repo(remote).refs[b"refs/heads/release"].decode()[:10] + ")",
      "| a_first.txt =", read(local, "a_first.txt"), "| zz/late.txt =", read(local, "zz/late.txt"))
g2 = GitManager(remote, local)
print("   new GitManager.check_updates() ->", g2.check_updates(), "| dirty_files() ->", g2.dirty_files())
FakeLauncher(remote, local).start("next start (same release on origin)")
os.chmod(os.path.join(local, "zz", "late.txt"), 0o644)   # the lock is gone
commit_on(remote, "release", "fix.txt", "the next release\n", "release 3")
FakeLauncher(remote, local).start("a later release arrives (file no longer locked)")
try:
    GitManager(remote, local).update()
except DirtyWorkingTreeError as e:
    print("   GitManager.update() ->", type(e).__name__, e.files)

# =====================================================================================
print("\n=== A5 (remote-deploy#6): `release` moved BACK to the previous good commit ===")
remote = make_remote("a5")
local = os.path.join(WORK, "a5", "local")
GitManager(remote, local).clone()
good = head(local)
with Repo(remote) as r:
    good_full = r.refs[b"refs/heads/release"]
commit_on(remote, "release", "a_first.txt", "BAD\n", "bad release")
FakeLauncher(remote, local).start("the bad release is taken")
bad = head(local)
with Repo(remote) as r:
    r.refs[b"refs/heads/release"] = good_full          # developer resets release to the good commit
FakeLauncher(remote, local).start("after the developer moved `release` back")
with Repo(local) as r:
    backups = [k.decode() for k in r.refs.keys() if k.startswith(b"refs/walldance/")]
print(f"   good={good} bad={bad} local HEAD now={head(local)} a_first.txt={read(local, 'a_first.txt')}")
print("   backup refs in the checkout:", backups)
print("   GitManager public methods:", [m for m in dir(GitManager) if not m.startswith("_")])

# =====================================================================================
print("\n=== A4 (remote-deploy#4): the update check against a dead / stalled network ===")
remote = make_remote("a4")
local = os.path.join(WORK, "a4", "local")
GitManager(remote, local).clone()


def point_origin(url):
    cfg = os.path.join(local, ".git", "config")
    lines = open(cfg).read().splitlines()
    out = [("\turl = " + url) if ln.strip().startswith("url =") else ln for ln in lines]
    open(cfg, "w").write("\n".join(out) + "\n")


# (a) connection refused (nothing listens)
s = socket.socket(); s.bind(("127.0.0.1", 0)); closed_port = s.getsockname()[1]; s.close()
point_origin(f"http://127.0.0.1:{closed_port}/WallDance.git")
t0 = time.time()
L = FakeLauncher(remote, local)
L.start("origin unreachable (connection refused)")
print(f"   took {time.time() - t0:.1f} s")

# (b) a server that accepts the connection and never answers (captive portal / stalled 4G)
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(8); port = srv.getsockname()[1]
held = []
threading.Thread(target=lambda: [held.append(srv.accept()) for _ in range(8)], daemon=True).start()
point_origin(f"http://127.0.0.1:{port}/WallDance.git")
L = FakeLauncher(remote, local)
th = threading.Thread(target=L._sequence_thread, daemon=True)
t0 = time.time()
th.start()
for wait in (5, 15, 30):
    th.join(timeout=wait - (time.time() - t0))
    print(f"   stalled server: after {time.time() - t0:.0f} s the sequence thread is "
          f"{'STILL BLOCKED' if th.is_alive() else 'done'}; trace so far: {[x.strip() for x in L.trace]}")
    if not th.is_alive():
        break
print("   connections the server holds open:", len(held))
