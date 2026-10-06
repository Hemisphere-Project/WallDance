"""
Helpers for persisting WallDance configurations.
- Manages per-project JSON configs stored under `projects/<project>/`.
- Keeps track of the last project loaded.

Durability (audit 2026-10 ARCH-8): every write goes to a temp file in the same
directory, is fsynced, then ``os.replace``d over the target, so a crash or
power cut mid-save leaves either the old file or the new one, never a
truncated one. "Latest config" skips unreadable files and falls back to the
newest *valid* save, and two saves within the same second never overwrite
each other (the second takes the next free second; ``_meta.saved_at`` keeps
the true time). The append-only history format is unchanged.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# Projects directory is at workspace root (three levels up from src/core/)
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORKSPACE_ROOT = os.path.dirname(_APP_ROOT)
PROJECTS_DIR = os.path.join(_WORKSPACE_ROOT, "projects")
LAST_PROJECT_FILE = os.path.join(PROJECTS_DIR, "last_project.txt")


def sanitize_project_name(name: str) -> str:
    """Sanitize project name for use as folder name."""
    cleaned = re.sub(r"[^\w\s-]", "", name).strip()
    cleaned = re.sub(r"[\s]+", "_", cleaned)
    return cleaned if cleaned else "default"


# Serialises "pick a free timestamped name + publish it" across threads (saves
# come from the main loop, but tuning applies run on worker threads).
_SAVE_LOCK = threading.Lock()
_TIMESTAMP_FMT = "%Y%m%d_%H%M%S"
# Windows: an antivirus / indexer can hold the target for a few ms, so
# os.replace raises PermissionError; retry briefly before giving up.
_REPLACE_ATTEMPTS = 6


def _replace(src: str, dst: str) -> None:
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


def atomic_write_text(path: str, text: str) -> None:
    """Write ``text`` to ``path`` atomically: temp file in the same directory,
    flush + fsync, then ``os.replace``. Readers (and a crash at any point)
    see the old content or the new content, never a truncated file. The temp
    name starts with ``.`` and ends with ``.tmp``, so the config listing
    never picks it up even if a crash leaves it behind."""
    directory = os.path.dirname(os.path.abspath(path))
    tmp = os.path.join(
        directory,
        f".{os.path.basename(path)}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, payload) -> None:
    """``json.dump(payload, f, indent=2)``, atomically. Serialises first, so
    an unserialisable value raises before anything touches the disk."""
    atomic_write_text(path, json.dumps(payload, indent=2))


def read_config_file(filepath: str) -> Dict:
    """Load one config file; a config must be a JSON object."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{os.path.basename(filepath)}: top level is "
                         f"{type(data).__name__}, expected a JSON object")
    return data


def is_valid_config_file(filepath: str) -> bool:
    """True if the file parses as a JSON object (a truncated save does not)."""
    try:
        read_config_file(filepath)
        return True
    except (OSError, ValueError):   # JSONDecodeError / UnicodeDecodeError are ValueErrors
        return False


def format_config_display(filename: str) -> str:
    """Convert config filename to a human-readable timestamp label."""
    display_name = filename.replace(".json", "")
    parts = display_name.rsplit("_", 2)
    if len(parts) >= 3:
        date_str = parts[-2]
        time_str = parts[-1]
        try:
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:]}"
        except Exception:
            return display_name
    return display_name


def list_config_files(project_dir: str) -> List[str]:
    """Timestamped config filenames in a project dir, newest first (by mtime).

    Skips `_`-prefixed specials (`_safe_defaults.json`): they are not project
    saves and must never be picked as "latest" — uppercase project names would
    otherwise sort them first (bug #7).
    """
    if not os.path.exists(project_dir):
        return []
    configs = [f for f in os.listdir(project_dir)
               if f.endswith(".json") and not f.startswith("_")]

    def mtime(name: str) -> float:
        try:
            return os.path.getmtime(os.path.join(project_dir, name))
        except OSError:
            return 0.0

    configs.sort(key=lambda f: (mtime(f), f), reverse=True)
    return configs


def get_latest_config_in_project(project_dir: str) -> Optional[str]:
    """Get the most recent *valid* config file in a project directory.

    A save cut short (crash, power loss, full disk) used to be "latest" and
    made auto-load exit with "Failed to load project" although the history
    held a good save. Unreadable files are now skipped, newest first, and
    reported on the console; they are left on disk for post-mortem."""
    for name in list_config_files(project_dir):
        path = os.path.join(project_dir, name)
        if is_valid_config_file(path):
            return path
        print(f"[ConfigStore] Skipping unreadable config {name} "
              "- falling back to the previous save")
    return None


@dataclass
class ProjectHistory:
    project: str
    configs: List[Tuple[str, str]]  # (display_name, filepath)


@dataclass
class ProjectInfo:
    """Summary of a project for the startup picker."""
    name: str
    latest_config: Optional[str]    # path to the most recent config
    last_saved: float               # epoch mtime of the newest config (0.0 if none)
    save_count: int                 # number of saved configs

    @property
    def last_saved_display(self) -> str:
        if not self.last_saved:
            return "never"
        return datetime.fromtimestamp(self.last_saved).strftime("%Y-%m-%d %H:%M")


class ConfigStore:
    """Persist and retrieve WallDance configuration files."""

    def __init__(self, config_dir: str = PROJECTS_DIR, last_project_file: str = LAST_PROJECT_FILE):
        self.config_dir = config_dir
        self.last_project_file = last_project_file
        os.makedirs(self.config_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def save(self, project_name: str, config: Dict) -> str:
        """Persist a configuration and return the saved filepath."""
        safe_name = sanitize_project_name(project_name)
        project_dir = os.path.join(self.config_dir, safe_name)
        os.makedirs(project_dir, exist_ok=True)

        now = datetime.now()
        with _SAVE_LOCK:
            # Same-second saves used to overwrite each other (one history
            # entry lost). Keep the `<project>_<YYYYmmdd_HHMMSS>.json` format
            # and take the next free second instead; `_meta.saved_at` keeps
            # the true time.
            stamp = now
            while True:
                filename = f"{safe_name}_{stamp.strftime(_TIMESTAMP_FMT)}.json"
                filepath = os.path.join(project_dir, filename)
                if not os.path.exists(filepath):
                    break
                stamp += timedelta(seconds=1)

            payload = dict(config)
            payload["_meta"] = {
                "project": safe_name,
                "saved_at": now.isoformat(),
                "filename": filename,
            }
            atomic_write_json(filepath, payload)

        self.remember_last_project(safe_name)
        return filepath

    def load(self, filepath: str) -> Dict:
        """Load a configuration from disk (must be a JSON object)."""
        return read_config_file(filepath)

    # ------------------------------------------------------------------
    # Project discovery
    # ------------------------------------------------------------------
    def list_projects(self) -> List[str]:
        if not os.path.exists(self.config_dir):
            return []
        projects = []
        for item in sorted(os.listdir(self.config_dir)):
            item_path = os.path.join(self.config_dir, item)
            if os.path.isdir(item_path) and list_config_files(item_path):
                projects.append(item)
        return projects

    def project_history(self, project: str) -> ProjectHistory:
        project_dir = os.path.join(self.config_dir, project)
        entries: List[Tuple[str, str]] = []
        for filename in list_config_files(project_dir):
            entries.append((format_config_display(filename),
                            os.path.join(project_dir, filename)))
        return ProjectHistory(project=project, configs=entries)

    def latest_for_project(self, project: str) -> Optional[str]:
        project_dir = os.path.join(self.config_dir, project)
        return get_latest_config_in_project(project_dir)

    def list_projects_by_date(self) -> List[ProjectInfo]:
        """Projects ordered by last-save date, most recent first (for the picker)."""
        infos: List[ProjectInfo] = []
        for name in self.list_projects():
            project_dir = os.path.join(self.config_dir, name)
            configs = list_config_files(project_dir)
            mtime = 0.0
            for f in configs:
                try:
                    mtime = max(mtime, os.path.getmtime(os.path.join(project_dir, f)))
                except OSError:
                    pass
            infos.append(ProjectInfo(
                name=name,
                latest_config=(os.path.join(project_dir, configs[0]) if configs else None),
                last_saved=mtime,
                save_count=len(configs),
            ))
        infos.sort(key=lambda i: i.last_saved, reverse=True)
        return infos

    # ------------------------------------------------------------------
    # Project management (rename / delete) — used by the startup picker
    # ------------------------------------------------------------------
    def rename_project(self, old_name: str, new_name: str) -> Optional[str]:
        """Rename a project directory and return the new safe name, or None on failure.

        Also rewrites each config's ``_meta.project`` so that re-loading a config
        infers the new name (see ``infer_project_from_config``). Updates the
        last-project pointer if it referenced the renamed project.
        """
        new_safe = sanitize_project_name(new_name)
        old_dir = os.path.join(self.config_dir, old_name)
        new_dir = os.path.join(self.config_dir, new_safe)
        if not os.path.isdir(old_dir):
            return None
        if new_safe == old_name:
            return old_name
        if os.path.exists(new_dir):
            return None  # collision — caller surfaces the error
        os.rename(old_dir, new_dir)
        # Rewrite _meta.project inside every config so loads infer the new name.
        for f in os.listdir(new_dir):
            if not f.endswith(".json"):
                continue
            path = os.path.join(new_dir, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                meta = data.get("_meta")
                if isinstance(meta, dict):
                    meta["project"] = new_safe
                    atomic_write_json(path, data)
            except (OSError, ValueError):
                pass  # leave a malformed config untouched
        if self.read_last_project() == old_name:
            self.remember_last_project(new_safe)
        return new_safe

    def delete_project(self, name: str) -> bool:
        """Delete a project directory and all its contents. Clears the last-project
        pointer if it referenced this project. Returns success."""
        project_dir = os.path.join(self.config_dir, name)
        if not os.path.isdir(project_dir):
            return False
        try:
            shutil.rmtree(project_dir)
        except OSError:
            return False
        if self.read_last_project() == name:
            try:
                if os.path.exists(self.last_project_file):
                    os.remove(self.last_project_file)
            except OSError:
                pass
        return True

    # ------------------------------------------------------------------
    # Last project tracking
    # ------------------------------------------------------------------
    def remember_last_project(self, project: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.last_project_file)),
                    exist_ok=True)
        atomic_write_text(self.last_project_file, project)

    def read_last_project(self) -> Optional[str]:
        if not os.path.exists(self.last_project_file):
            return None
        with open(self.last_project_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content or None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def infer_project_from_config(self, config: Dict, fallback_path: str) -> str:
        if "_meta" in config and isinstance(config["_meta"], dict):
            meta_project = config["_meta"].get("project")
            if meta_project:
                return meta_project
        project_dir = os.path.dirname(fallback_path)
        return os.path.basename(project_dir) if project_dir else "default"

    # ------------------------------------------------------------------
    # Safe defaults
    # ------------------------------------------------------------------
    def save_safe_defaults(self, project_name: str, config: Dict) -> str:
        """Save config as safe defaults for the project."""
        safe_name = sanitize_project_name(project_name)
        project_dir = os.path.join(self.config_dir, safe_name)
        os.makedirs(project_dir, exist_ok=True)

        filepath = os.path.join(project_dir, "_safe_defaults.json")

        payload = dict(config)
        payload["_meta"] = {
            "project": safe_name,
            "saved_at": datetime.now().isoformat(),
            "type": "safe_defaults",
        }

        atomic_write_json(filepath, payload)

        return filepath

    def load_safe_defaults(self, project_name: str) -> Optional[Dict]:
        """Load safe defaults for the project if they exist."""
        safe_name = sanitize_project_name(project_name)
        filepath = os.path.join(self.config_dir, safe_name, "_safe_defaults.json")
        if os.path.exists(filepath):
            return read_config_file(filepath)
        return None

    def has_safe_defaults(self, project_name: str) -> bool:
        """Check if safe defaults exist for the project."""
        safe_name = sanitize_project_name(project_name)
        filepath = os.path.join(self.config_dir, safe_name, "_safe_defaults.json")
        return os.path.exists(filepath)