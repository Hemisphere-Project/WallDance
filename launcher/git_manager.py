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

    def has_local_changes(self):
        if not self.is_cloned():
            return False
        repo = Repo(self.target_dir)
        st = porcelain.status(repo)
        # st is a namedtuple: Status(staged, unstaged, untracked)
        # We consider staged or unstaged as local changes that might conflict.
        # Untracked files usually don't conflict unless they have the same name as incoming files.
        has_staged = bool(st.staged['add'] or st.staged['delete'] or st.staged['modify'])
        has_unstaged = bool(st.unstaged)
        return has_staged or has_unstaged

    def check_updates(self):
        if not self.is_cloned():
            return False
        try:
            repo = Repo(self.target_dir)
            # Fetch latest from remote
            porcelain.fetch(repo, self.repo_url)
            
            local_head = repo.head()
            
            # Get remote HEAD
            remote_refs = porcelain.ls_remote(self.repo_url)
            remote_head = remote_refs.get(b'HEAD')
            
            if remote_head is None:
                return False
                
            return local_head != remote_head
        except Exception as e:
            print(f"Error checking updates: {e}")
            return False

    def pull(self):
        if not self.is_cloned():
            return False
        repo = Repo(self.target_dir)
        local_head_before = repo.head()
        
        porcelain.pull(self.target_dir, self.repo_url)
        
        local_head_after = repo.head()
        
        # Check if install.bat or application/pyproject.toml changed
        changed_files = self._get_changed_files(repo, local_head_before, local_head_after)
        
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
