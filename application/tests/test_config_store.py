"""Tests for the project-management config store (rename / delete / ordering).

Backs the startup project picker (ROADMAP §7B) and the audit's "config tests"
deliverable. Uses a temp projects dir so nothing touches the real ``projects/``.
"""

import json
import os
import time

import pytest

from core.config_store import ConfigStore


@pytest.fixture
def store(tmp_path):
    return ConfigStore(config_dir=str(tmp_path),
                       last_project_file=str(tmp_path / "last_project.txt"))


def test_list_projects_by_date_orders_newest_first(store):
    store.save("Alpha", {"x": 1})
    time.sleep(0.02)
    store.save("Beta", {"x": 2})
    time.sleep(0.02)
    store.save("Alpha", {"x": 3})           # Alpha touched most recently

    order = [i.name for i in store.list_projects_by_date()]
    assert order[0] == "Alpha"
    assert set(order) == {"Alpha", "Beta"}


def test_save_count_and_display(store):
    store.save("Gamma", {"x": 1})
    info = store.list_projects_by_date()[0]
    assert info.name == "Gamma"
    assert info.save_count >= 1
    assert info.latest_config and info.latest_config.endswith(".json")
    assert info.last_saved > 0
    assert info.last_saved_display != "never"


def test_rename_moves_dir_and_rewrites_meta(store):
    store.save("OldName", {"x": 1})
    assert store.read_last_project() == "OldName"

    new = store.rename_project("OldName", "Shiny New")
    assert new == "Shiny_New"                       # sanitized
    names = store.list_projects()
    assert "Shiny_New" in names and "OldName" not in names
    # _meta.project rewritten so a reload infers the new name
    meta = json.load(open(store.latest_for_project("Shiny_New")))["_meta"]["project"]
    assert meta == "Shiny_New"
    # last-project pointer followed the rename
    assert store.read_last_project() == "Shiny_New"


def test_rename_rejects_collision(store):
    store.save("A", {"x": 1})
    store.save("B", {"x": 2})
    assert store.rename_project("A", "B") is None    # B already exists
    assert store.list_projects() == ["A", "B"]       # unchanged


def test_rename_same_name_is_noop(store):
    store.save("Keep", {"x": 1})
    assert store.rename_project("Keep", "Keep") == "Keep"


def test_delete_removes_dir_and_clears_last_pointer(store):
    store.save("Doomed", {"x": 1})
    assert store.read_last_project() == "Doomed"

    assert store.delete_project("Doomed") is True
    assert "Doomed" not in store.list_projects()
    assert store.read_last_project() is None          # pointer cleared

    assert store.delete_project("Doomed") is False     # already gone


def test_delete_keeps_unrelated_last_pointer(store):
    store.save("First", {"x": 1})
    store.save("Second", {"x": 2})                     # last = Second
    assert store.delete_project("First") is True
    assert store.read_last_project() == "Second"       # untouched


# ----------------------------------------------------------- bug #7 (latest)

def test_safe_defaults_never_wins_latest(store):
    # '_safe_defaults.json' reverse-name-sorts above capitalized project names
    # ('A' < '_'), so it used to hijack "latest" → startup silently loaded
    # safe defaults instead of the last save.
    store.save("Alpha", {"x": 1})
    store.save_safe_defaults("Alpha", {"x": "safe"})   # newer mtime, too

    latest = store.latest_for_project("Alpha")
    assert latest and not os.path.basename(latest).startswith("_")

    history = store.project_history("Alpha")
    assert len(history.configs) == 1
    assert all(not os.path.basename(path).startswith("_")
               for _disp, path in history.configs)

    info = next(i for i in store.list_projects_by_date() if i.name == "Alpha")
    assert info.save_count == 1
    assert not os.path.basename(info.latest_config).startswith("_")


def test_latest_picked_by_mtime_not_name(store, tmp_path):
    # A renamed project keeps old-prefix filenames, so a name sort can
    # disagree with save order — mtime decides.
    pdir = tmp_path / "Renamed"
    pdir.mkdir()
    older = pdir / "zzz_20250101_000000.json"          # name-sorts first
    newer = pdir / "aaa_20260101_000000.json"
    older.write_text("{}")
    newer.write_text("{}")
    past = time.time() - 1000
    os.utime(older, (past, past))

    latest = store.latest_for_project("Renamed")
    assert latest.endswith("aaa_20260101_000000.json")
    assert store.project_history("Renamed").configs[0][1] == latest


def test_project_with_only_safe_defaults_not_listed(store):
    store.save_safe_defaults("GhostProj", {"x": 1})
    assert "GhostProj" not in store.list_projects()
    assert all(i.name != "GhostProj" for i in store.list_projects_by_date())
    assert store.latest_for_project("GhostProj") is None
    # the safe defaults themselves stay loadable (separate explicit action)
    assert store.has_safe_defaults("GhostProj")
