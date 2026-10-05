import os
import sys
from dulwich import porcelain
from dulwich.repo import Repo
from dulwich.diff_tree import tree_changes

class GitManager:
    def __init__(self, repo_url, target_dir):
        self.repo_url = repo_url
        self.target_dir = target_dir

    def is_cloned(self):
        return os.path.exists(os.path.join(self.target_dir, '.git'))

    def clone(self, progress_cb=None):
        # dulwich clone doesn't easily support a simple progress callback for the UI
        # without overriding client methods, so we'll just run it synchronously.
        porcelain.clone(self.repo_url, self.target_dir)

    def check_updates(self):
        """Fetch from remote and return True if local HEAD is behind."""
        if not self.is_cloned():
            return False
        try:
            repo = Repo(self.target_dir)
            # Use remote name "origin" (not the raw URL) so dulwich resolves the
            # refspec from .git/config and updates refs/remotes/origin/* properly.
            fetch_result = porcelain.fetch(repo, "origin")

            local_head = repo.head()
            # Use the fetch result's refs (always fresh from the server)
            remote_head = fetch_result.refs.get(b'refs/heads/main') or fetch_result.refs.get(b'HEAD')
            if remote_head is None:
                return False

            return local_head != remote_head
        except Exception as e:
            print(f"Error checking updates: {e}")
            return False

    def update(self):
        """Force-sync working tree to remote HEAD (fetch already done by check_updates)."""
        if not self.is_cloned():
            return False
        repo = Repo(self.target_dir)
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
