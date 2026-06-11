"""
Helpers for persisting WallDance configurations.
- Manages per-project JSON configs stored under `projects/<project>/`.
- Keeps track of the last project loaded.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
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
    """Get the most recent config file in a project directory."""
    configs = list_config_files(project_dir)
    if not configs:
        return None
    return os.path.join(project_dir, configs[0])


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

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_name}_{timestamp}.json"
        filepath = os.path.join(project_dir, filename)

        payload = dict(config)
        payload["_meta"] = {
            "project": safe_name,
            "saved_at": datetime.now().isoformat(),
            "filename": filename,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        self.remember_last_project(safe_name)
        return filepath

    def load(self, filepath: str) -> Dict:
        """Load a configuration from disk."""
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

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
                    with open(path, "w", encoding="utf-8") as fh:
                        json.dump(data, fh, indent=2)
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
        os.makedirs(self.config_dir, exist_ok=True)
        with open(self.last_project_file, "w", encoding="utf-8") as f:
            f.write(project)

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

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return filepath

    def load_safe_defaults(self, project_name: str) -> Optional[Dict]:
        """Load safe defaults for the project if they exist."""
        safe_name = sanitize_project_name(project_name)
        filepath = os.path.join(self.config_dir, safe_name, "_safe_defaults.json")
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def has_safe_defaults(self, project_name: str) -> bool:
        """Check if safe defaults exist for the project."""
        safe_name = sanitize_project_name(project_name)
        filepath = os.path.join(self.config_dir, safe_name, "_safe_defaults.json")
        return os.path.exists(filepath)