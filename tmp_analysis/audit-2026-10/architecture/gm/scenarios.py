import os, sys, shutil, tempfile, subprocess
sys.path.insert(0, os.path.dirname(__file__))
from dulwich import porcelain
from dulwich.repo import Repo
import old_git_manager as OLD
import new_git_manager as NEW

ID = b"T <t@e.com>"
def commit(path, rel, content, msg):
    p = os.path.join(path, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(content); porcelain.add(path, paths=[p])
    return porcelain.commit(path, message=msg.encode(), author=ID, committer=ID)
def write(path, rel, content):
    p = os.path.join(path, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(content)
def read(path, rel):
    p = os.path.join(path, rel); return open(p).read() if os.path.exists(p) else None
def mkremote(root):
    r = os.path.join(root, "remote"); os.makedirs(r)
    repo = porcelain.init(r); repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main"); repo.close()
    commit(r, "README.md", "v1\n", "init")
    commit(r, "application/src/core/config.py", "X=1\n", "cfg")
    commit(r, "application/src/old_module.py", "old\n", "old module")
    return r
def head(p):
    with Repo(p) as r: return r.head()

def scen(name):
    print("\n=== " + name)
    return tempfile.mkdtemp()

# 1. OLD launcher: prod AHEAD with unpushed commit + uncommitted tracked edit, remote unchanged
root = scen("OLD exe: local AHEAD (unpushed commit) + uncommitted edit, remote unchanged")
r = mkremote(root); gm = OLD.GitManager(r, os.path.join(root, "local")); gm.clone()
L = gm.target_dir
c_local = commit(L, "application/src/core/tracker.py", "local work\n", "prod-only commit")
write(L, "application/src/core/config.py", "X=42  # tuned on prod, uncommitted\n")
print("check_updates ->", gm.check_updates(), "(old: True means 'update available' prompt)")
gm.update()
print("tracker.py (added by local commit):", repr(read(L, "application/src/core/tracker.py")))
print("config.py (uncommitted edit):", repr(read(L, "application/src/core/config.py")))
print("HEAD == remote:", head(L) == head(r), "| local commit reachable from main:", head(L) == c_local)
print("reflog exists:", os.path.exists(os.path.join(L, ".git/logs/refs/heads/main")))
if os.path.exists(os.path.join(L, ".git/logs/refs/heads/main")):
    print(open(os.path.join(L, ".git/logs/refs/heads/main")).read()[-400:])
print("porcelain.status after:", porcelain.status(L))

# 2. OLD: diverged (Thomas pushes while prod has local commits + edits)
root = scen("OLD exe: DIVERGED + uncommitted edit")
r = mkremote(root); gm = OLD.GitManager(r, os.path.join(root, "local")); gm.clone(); L = gm.target_dir
commit(L, "application/src/core/config.py", "X=7 committed on prod\n", "prod commit")
write(L, "README.md", "prod uncommitted edit\n")
commit(r, "application/src/new.py", "new\n", "dev push")
print("check_updates ->", gm.check_updates()); gm.update()
print("config.py:", repr(read(L, "application/src/core/config.py")), "README:", repr(read(L, "README.md")))

# 3. NEW: origin/HEAD form after dulwich clone
root = scen("NEW: origin/HEAD after dulwich clone")
r = mkremote(root); gm = NEW.GitManager(r, os.path.join(root, "local")); gm.clone(); L = gm.target_dir
with Repo(L) as repo:
    print("origin/HEAD symref chain:", repo.refs.follow(b"refs/remotes/origin/HEAD"))
    print("raw:", repo.refs.read_ref(b"refs/remotes/origin/HEAD"))

# 4. NEW: files deleted upstream linger; untracked collision overwritten
root = scen("NEW: upstream delete lingers / untracked collision overwritten")
r = mkremote(root); gm = NEW.GitManager(r, os.path.join(root, "local")); gm.clone(); L = gm.target_dir
write(L, "application/src/notes_operator.py", "operator untracked file\n")
porcelain.remove(r, paths=[os.path.join(r, "application/src/old_module.py")])
porcelain.commit(r, message=b"delete old_module", author=ID, committer=ID)
commit(r, "application/src/notes_operator.py", "upstream version\n", "add same path upstream")
print("check:", gm.check_updates(), "dirty:", gm.dirty_files()); gm.update()
print("old_module.py still on disk:", read(L, "application/src/old_module.py") is not None)
print("untracked operator file now:", repr(read(L, "application/src/notes_operator.py")))
print("status untracked:", porcelain.status(L).untracked)

# 5. NEW: local on a feature branch
root = scen("NEW: prod checked out on a non-main branch with commits")
r = mkremote(root); gm = NEW.GitManager(r, os.path.join(root, "local")); gm.clone(); L = gm.target_dir
with Repo(L) as repo:
    repo.refs[b"refs/heads/feature"] = repo.head()
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")
fc = commit(L, "feat.txt", "feature work\n", "feature commit")
commit(r, "remote.txt", "r\n", "remote push")
st = gm.check_updates(); print("status:", st)
gm.update()
with Repo(L) as repo:
    print("HEAD symref ->", repo.refs.follow(b"HEAD")[0], "feature ref == remote:", repo.refs[b"refs/heads/feature"] == head(r), "feature commit lost from branch:", repo.refs[b"refs/heads/feature"] != fc)

# 6. NEW: remote moved on but origin/HEAD stale? (simulate git-cloned checkout where origin/HEAD is a symref) -> fine; and when origin/HEAD is a *direct* sha:
root = scen("NEW: origin/HEAD as a direct (stale) sha")
r = mkremote(root); gm = NEW.GitManager(r, os.path.join(root, "local")); gm.clone(); L = gm.target_dir
with Repo(L) as repo:
    repo.refs.remove_if_equals(b"refs/remotes/origin/HEAD", None)
    repo.refs[b"refs/remotes/origin/HEAD"] = repo.head()
commit(r, "remote.txt", "r\n", "remote push")
print("status:", gm.check_updates()); gm.update()
print("after update HEAD==remote:", head(L) == head(r), "remote.txt present:", read(L, "remote.txt") is not None)
