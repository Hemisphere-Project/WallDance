import os
import sys
from enum import Enum
from dulwich import porcelain
from dulwich.repo import Repo
from dulwich.diff_tree import tree_changes
from dulwich.graph import can_fast_forward


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
# past the commit that untracks them (ROADMAP §6 "Untrack committed junk").
_DIRTY_EXEMPT = {"application/merge_dbg.log", "application/src/tracking_events.jsonl"}


def _norm_path(p):
    if isinstance(p, bytes):
        p = p.decode("utf-8", "replace")
    return p.replace("\\", "/")


class GitManager:
    def __init__(self, repo_url, target_dir):
        self.repo_url = repo_url
        self.target_dir = target_dir

    def is_cloned(self):
        return os.path.exists(os.path.join(self.target_dir, '.git'))

    def clone(self, progress_cb=None):
        # dulwich clone doesn't easily support a simple progress callback for the UI
        # without overriding client methods, so we'll just run it synchronously.
        porcelain.clone(self.repo_url, self.target_dir).close()

    def check_updates(self):
        """Fetch from remote and classify local HEAD vs remote HEAD.

        Returns an UpdateStatus. UNKNOWN means the check itself failed
        (offline?) and callers should behave as if no update is available.
        """
        if not self.is_cloned():
            return UpdateStatus.UNKNOWN
        try:
            with Repo(self.target_dir) as repo:
                # Use remote name "origin" (not the raw URL) so dulwich resolves the
                # refspec from .git/config and updates refs/remotes/origin/* properly.
                fetch_result = porcelain.fetch(repo, "origin")

                local_head = repo.head()
                # Use the fetch result's refs (always fresh from the server)
                remote_head = fetch_result.refs.get(b'refs/heads/main') or fetch_result.refs.get(b'HEAD')
                if remote_head is None:
                    return UpdateStatus.UNKNOWN
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
        never touches files git doesn't track.
        """
        # untracked_files="no" skips the filesystem walk over untracked data.
        status = porcelain.status(self.target_dir, untracked_files="no")
        paths = set()
        for bucket in status.staged.values():
            paths.update(_norm_path(p) for p in bucket)
        paths.update(_norm_path(p) for p in status.unstaged)
        return sorted(paths - _DIRTY_EXEMPT)

    def update(self):
        """Force-sync working tree to remote HEAD (fetch already done by check_updates).

        Raises DirtyWorkingTreeError instead of overwriting local
        modifications to tracked files.
        """
        if not self.is_cloned():
            return False
        dirty = self.dirty_files()
        if dirty:
            raise DirtyWorkingTreeError(dirty)
        with Repo(self.target_dir) as repo:
            local_head_before = repo.head()

            # Resolve the remote target commit
            try:
                remote_sha = repo.refs[b'refs/remotes/origin/HEAD']
            except KeyError:
                remote_sha = repo.refs[b'refs/remotes/origin/main']

            # Update HEAD and main branch ref to the remote commit
            repo.refs[b'refs/heads/main'] = remote_sha
            repo.refs[b'HEAD'] = remote_sha

            # Hard-reset: rebuild index and working tree from the new HEAD
            import dulwich.index
            indexfile = repo.index_path()
            tree = repo[repo[remote_sha].tree]

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
                tree_id=tree.id,
                honor_filemode=False,
                symlink_fn=_safe_symlink,
            )

            # Check if install.bat or application/pyproject.toml changed
            changed_files = self._get_changed_files(repo, local_head_before, remote_sha)
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
