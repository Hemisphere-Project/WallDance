"""App version / provenance, read straight from ``.git`` (no git CLI needed).

The prod laptop's launcher syncs the checkout with dulwich, so ``git`` may not
be installed there. Recordings (MRK-0) and the remote API stamp this so every
take / status answer says which code produced it.
"""
from __future__ import annotations

import os
import platform
import sys
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

_REPO = Path(__file__).resolve().parents[3]


def _read_ref(git_dir: Path, ref: str) -> Optional[str]:
    loose = git_dir / ref
    if loose.is_file():
        return loose.read_text().strip()
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text().splitlines():
            if line and line[0] not in "#^" and line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    return None


@lru_cache(maxsize=1)
def app_version(repo: Optional[str] = None) -> Dict[str, Optional[str]]:
    """``{"commit", "branch", "python", "platform"}`` -- commit/branch None when
    the tree is not a git checkout. Cached: the code does not change while the
    process runs."""
    root = Path(repo) if repo else _REPO
    git_dir = root / ".git"
    commit = branch = None
    try:
        head = (git_dir / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            branch = ref.rsplit("refs/heads/", 1)[-1]
            commit = _read_ref(git_dir, ref)
        else:
            commit = head
    except OSError:
        pass
    slot = None
    if commit is None:
        # A wdremote DEV slot is a plain tree (no .git): DEPLOYED.json says
        # which ref/commit was synced into it (extra/wdslot.py).
        try:
            import json
            dep = json.loads((root / "DEPLOYED.json").read_text())
            commit, branch, slot = dep.get("commit"), dep.get("ref"), "dev"
        except (OSError, ValueError):
            pass
    return {
        "commit": commit[:12] if commit else None,
        "branch": branch,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "host": platform.node() or os.environ.get("COMPUTERNAME"),
        "slot": slot or os.environ.get("WD_SLOT") or ("live" if commit else None),
    }
