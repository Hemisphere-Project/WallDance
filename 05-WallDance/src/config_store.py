"""
Helpers for persisting WallDance configurations.
- Manages per-project JSON configs stored under `projects/<project>/`.
- Keeps track of the last project loaded.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Projects directory (in project root, not src/)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_DIR = os.path.join(_PROJECT_ROOT, "projects")
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


def get_latest_config_in_project(project_dir: str) -> Optional[str]:
    """Get the most recent config file in a project directory."""
    if not os.path.exists(project_dir):
        return None
    configs = [f for f in os.listdir(project_dir) if f.endswith(".json")]
    if not configs:
        return None
    configs.sort(reverse=True)
    return os.path.join(project_dir, configs[0])


@dataclass
class ProjectHistory:
    project: str
    configs: List[Tuple[str, str]]  # (display_name, filepath)


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
            if os.path.isdir(item_path):
                json_files = [f for f in os.listdir(item_path) if f.endswith(".json")]
                if json_files:
                    projects.append(item)
        return projects

    def project_history(self, project: str) -> ProjectHistory:
        project_dir = os.path.join(self.config_dir, project)
        entries: List[Tuple[str, str]] = []
        current_display = ""
        if os.path.exists(project_dir):
            configs = sorted([f for f in os.listdir(project_dir) if f.endswith(".json")], reverse=True)
            for idx, filename in enumerate(configs):
                display = format_config_display(filename)
                entries.append((display, os.path.join(project_dir, filename)))
                if idx == 0:
                    current_display = display
        return ProjectHistory(project=project, configs=entries)

    def latest_for_project(self, project: str) -> Optional[str]:
        project_dir = os.path.join(self.config_dir, project)
        return get_latest_config_in_project(project_dir)

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