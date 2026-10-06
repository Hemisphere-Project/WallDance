import json
import os
import sys
import time
from enum import Enum
from dulwich import porcelain
from dulwich.repo import Repo
from dulwich.diff_tree import tree_changes
from dulwich.graph import can_fast_forward

# The field update channel (Thomas, 2026-10-06, decision D1): the launcher
# follows a RELEASE branch that Thomas moves deliberately, never `main`, so
# development pushes cannot reach a show laptop by accident. Override per
# machine with launcher.json {"branch": "..."} next to the exe, or the
# WALLDANCE_BRANCH environment variable.
DEFAULT_BRANCH = "release"


def resolve_branch(base_dir):
    env = os.environ.get("WALLDANCE_BRANCH", "").strip()
    if env:
        return env
    try:
        with open(os.path.join(base_dir, "launcher.json"), encoding="utf-8") as f:
            branch = str(json.load(f).get("branch", "")).strip()
            if branch:
                return branch
    except (OSError, ValueError):
        pass
    return DEFAULT_BRANCH


class UpdateStatus(Enum):
    UP_TO_DATE = "up-to-date"
    BEHIND = "behind"        # remote has new commits; clean fast-forward available
    AHEAD = "ahead"          # local has commits the remote lacks - never offer update
    DIVERGED = "diverged"    # both sides advanced - update discards local commits
    UNKNOWN = "unknown"      # fetch failed (offline?) or remote ref missing


class DirtyWorkingTreeError(Exception):
    """Tracked files have local modifications; a force-sync would destroy them."""
    def __init__(self, files):
        self.files = files
        super().__init__("Local modifications to tracked files: " + ", ".join(files))


# Tracked files the app rewrites at runtime; exempt from the dirty check so field
# machines aren't permanently refused updates. Remove once every field checkout is
# past the commit that untracks them (launcher update safety, 188d2f7).
_DIRTY_EXEMPT = {"application/merge_dbg.log", "application/src/tracking_events.jsonl"}


def _norm_path(p):
    if isinstance(p, bytes):
        p = p.decode("utf-8", "replace")
    return p.replace("\\", "/")


class GitManager:
    def __init__(self, repo_url, target_dir, branch=DEFAULT_BRANCH):
        self.repo_url = repo_url
        self.target_dir = target_dir
        self.branch = branch
        self.branch_ref = b"refs/heads/" + branch.encode()
        self.last_backup_ref = None      # set by update(): where the old HEAD was kept
        self.last_moved_aside = []       # untracked files renamed out of the way

    def is_cloned(self):
        return os.path.exists(os.path.join(self.target_dir, '.git'))

    def clone(self, progress_cb=None):
        # dulwich clone doesn't easily support a simple progress callback for the UI
        # without overriding client methods, so we'll just run it synchronously.
        try:
            porcelain.clone(self.repo_url, self.target_dir,
                            branch=self.branch.encode()).close()
        except Exception as e:
            # Channel branch not published yet: clone the default branch;
            # check_updates() then reports UNKNOWN until it exists.
            print(f"Clone of branch '{self.branch}' failed ({e}); cloning default branch")
            import shutil
            shutil.rmtree(self.target_dir, ignore_errors=True)
            porcelain.clone(self.repo_url, self.target_dir).close()

    def current_version(self):
        """(checked-out branch or None, short sha) for the launcher title/log."""
        if not self.is_cloned():
            return None, None
        with Repo(self.target_dir) as repo:
            branch = None
            try:
                target = repo.refs.read_ref(b"HEAD")
                if target and target.startswith(b"ref: refs/heads/"):
                    branch = target[len(b"ref: refs/heads/"):].decode()
            except Exception:
                pass
            return branch, repo.head().decode()[:12]

    def check_updates(self):
        """Fetch and classify local HEAD vs the release branch on origin.

        Returns an UpdateStatus. UNKNOWN means the check itself failed
        (offline?) or the release branch does not exist on origin yet; callers
        behave as if no update is available, so a laptop never moves until the
        branch is published.
        """
        if not self.is_cloned():
            return UpdateStatus.UNKNOWN
        try:
            with Repo(self.target_dir) as repo:
                # Use remote name "origin" (not the raw URL) so dulwich resolves the
                # refspec from .git/config and updates refs/remotes/origin/* properly.
                fetch_result = porcelain.fetch(repo, "origin")

                local_head = repo.head()
                remote_head = fetch_result.refs.get(self.branch_ref)
                if remote_head is None:
                    print(f"Update channel '{self.branch}' not found on origin")
                    return UpdateStatus.UNKNOWN
                self._remote_head = remote_head
                if local_head == remote_head:
                    return UpdateStatus.UP_TO_DATE
                if can_fast_forward(repo, local_head, remote_head):
                    return UpdateStatus.BEHIND
                if can_fast_forward(repo, remote_head, local_head):
                    return UpdateStatus.AHEAD
                return UpdateStatus.DIVERGED
        except Exception as e:
            print(f"Error checking updates: {e}")
            return UpdateStatus.UNKNOWN

    def dirty_files(self):
        """Tracked files with staged or unstaged local modifications.

        Untracked files never count: field machines keep working data
        (models/, projects/, recordings) inside the tree, and an update
        never touches files git doesn't track (update() moves an untracked
        file aside if the new version starts tracking that exact path).
        """
        # untracked_files="no" skips the filesystem walk over untracked data.
        status = porcelain.status(self.target_dir, untracked_files="no")
        paths = set()
        for bucket in status.staged.values():
            paths.update(_norm_path(p) for p in bucket)
        paths.update(_norm_path(p) for p in status.unstaged)
        return sorted(paths - _DIRTY_EXEMPT)

    def update(self):
        """Sync the working tree to the release branch (fetched by check_updates).

        Safety (audit 2026-10 ARCH-2): never overwrites local work silently.
        - Refuses (DirtyWorkingTreeError) while tracked files are modified.
        - Keeps the previous HEAD under ``refs/walldance/pre-update-<stamp>``
          (recoverable even after a "discard" on a diverged checkout), and
          never rewrites another branch: only the release branch moves; the
          old branch (e.g. main with laptop-only commits) stays as it was.
        - Moves aside (``<path>.wd-local-<stamp>``) any untracked file the new
          version starts tracking, instead of clobbering it.
        - Deletes files the new version removed (tracked + clean => safe).
        Returns True when install.bat / pyproject.toml changed (reinstall).
        """
        if not self.is_cloned():
            return False
        dirty = self.dirty_files()
        if dirty:
            raise DirtyWorkingTreeError(dirty)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        with Repo(self.target_dir) as repo:
            local_head_before = repo.head()
            remote_sha = getattr(self, "_remote_head", None)
            if remote_sha is None:
                remote_sha = repo.refs[b"refs/remotes/origin/" + self.branch.encode()]

            # 1. Backup ref for the old HEAD (survives any later reset).
            backup = f"refs/walldance/pre-update-{stamp}".encode()
            repo.refs[backup] = local_head_before
            self.last_backup_ref = backup.decode()

            new_tree = repo[repo[remote_sha].tree]
            old_tree = repo[repo[local_head_before].tree]

            # 2. Untracked files that the new tree would overwrite -> move aside.
            index = repo.open_index()
            tracked = {_norm_path(p) for p in index}
            self.last_moved_aside = []
            try:   # module-level in newer dulwich; method on older (exe pins)
                from dulwich.object_store import iter_tree_contents as _iter_tree
                entries = _iter_tree(repo.object_store, new_tree.id)
            except ImportError:
                entries = repo.object_store.iter_tree_contents(new_tree.id)
            for entry in entries:
                rel = _norm_path(entry.path)
                full = os.path.join(self.target_dir, *rel.split("/"))
                if rel not in tracked and os.path.lexists(full) and not os.path.isdir(full):
                    aside = f"{full}.wd-local-{stamp}"
                    os.replace(full, aside)
                    self.last_moved_aside.append(rel)

            # 3. Point the release branch at the remote commit and check it out
            #    (symbolic HEAD), leaving every other branch untouched.
            repo.refs[self.branch_ref] = remote_sha
            repo.refs.set_symbolic_ref(b"HEAD", self.branch_ref)

            # 4. Hard-reset index + working tree to the new commit.
            import dulwich.index
            indexfile = repo.index_path()

            def _safe_symlink(source, link_name):
                """On Windows without developer mode, symlinks require admin privileges.
                Fall back to writing the link target as a plain text file."""
                try:
                    os.symlink(source, link_name)
                except OSError:
                    with open(link_name, 'w') as f:
                        f.write(source if isinstance(source, str) else source.decode())

            dulwich.index.build_index_from_tree(
                root_path=self.target_dir,
                index_path=indexfile,
                object_store=repo.object_store,
                tree_id=new_tree.id,
                honor_filemode=False,
                symlink_fn=_safe_symlink,
            )

            # 5. Remove files the new version deleted (they were clean).
            changed_files = self._get_changed_files(repo, local_head_before, remote_sha)
            for change in tree_changes(repo.object_store, old_tree.id, new_tree.id):
                if change.type == "delete":
                    full = os.path.join(self.target_dir, *_norm_path(change.old.path).split("/"))
                    try:
                        os.remove(full)
                    except OSError:
                        pass

            needs_install = any(
                f in [b'install.bat', b'application/pyproject.toml']
                for f in changed_files
            )
            return needs_install

    def _get_changed_files(self, repo, commit1_sha, commit2_sha):
        if commit1_sha == commit2_sha:
            return []
        commit1 = repo[commit1_sha]
        commit2 = repo[commit2_sha]
        changes = tree_changes(repo.object_store, commit1.tree, commit2.tree)
        changed_files = []
        for change in changes:
            if change.type == 'modify' or change.type == 'add':
                changed_files.append(change.new.path)
            elif change.type == 'delete':
                changed_files.append(change.old.path)
        return changed_files
