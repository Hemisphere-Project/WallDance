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


# ---------------------------------------------------------------------------
# ARCH-8 durability: atomic writes, newest-valid fallback, same-second saves
# ---------------------------------------------------------------------------

def test_truncated_newest_file_falls_back_to_previous_valid(store, tmp_path):
    good = store.save("Show", {"x": 1})
    pdir = tmp_path / "Show"
    # A save cut short mid-write (crash / power loss / disk full) - newest.
    bad = pdir / "Show_29991231_235959.json"
    bad.write_text('{"x": 2, "tracker_max_age": 4', encoding="utf-8")
    # An empty file and a non-object are not configs either.
    (pdir / "Show_29991231_235958.json").write_text("", encoding="utf-8")
    (pdir / "Show_29991231_235957.json").write_text("[1, 2]", encoding="utf-8")
    future = time.time() + 60
    for p in pdir.glob("Show_2999*.json"):
        os.utime(p, (future, future))

    assert store.latest_for_project("Show") == good
    assert store.load(good)["x"] == 1
    # The broken files stay on disk (post-mortem) and in the history list.
    assert bad.exists()
    assert len(store.project_history("Show").configs) == 4


def test_all_files_invalid_means_no_latest(store, tmp_path):
    pdir = tmp_path / "Broken"
    pdir.mkdir()
    (pdir / "Broken_20260101_000000.json").write_text("{", encoding="utf-8")
    assert store.latest_for_project("Broken") is None


def test_load_rejects_non_object(store, tmp_path):
    p = tmp_path / "x.json"
    p.write_text("null", encoding="utf-8")
    with pytest.raises(ValueError):
        store.load(str(p))


def test_same_second_saves_are_both_kept(store, monkeypatch):
    import core.config_store as cs
    from datetime import datetime as real_dt

    class FrozenDatetime(real_dt):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 6, 21, 30, 15, 123456)

    monkeypatch.setattr(cs, "datetime", FrozenDatetime)
    p1 = store.save("Twice", {"x": 1})
    p2 = store.save("Twice", {"x": 2})
    p3 = store.save("Twice", {"x": 3})

    assert len({p1, p2, p3}) == 3
    assert [os.path.basename(p) for p in (p1, p2, p3)] == [
        "Twice_20261006_213015.json",
        "Twice_20261006_213016.json",       # next free second, same format
        "Twice_20261006_213017.json",
    ]
    assert [store.load(p)["x"] for p in (p1, p2, p3)] == [1, 2, 3]
    # _meta keeps the true save time and the real filename
    meta = store.load(p2)["_meta"]
    assert meta["saved_at"] == "2026-10-06T21:30:15.123456"
    assert meta["filename"] == "Twice_20261006_213016.json"
    assert len(store.project_history("Twice").configs) == 3
    assert store.latest_for_project("Twice") == p3


def test_save_is_atomic_and_leaves_no_temp_files(store, tmp_path, monkeypatch):
    import core.config_store as cs

    path = store.save("Atomic", {"x": 1})
    before = open(path, encoding="utf-8").read()

    # A crash between the temp write and the publish must leave the
    # published file untouched and no temp file behind.
    def boom(src, dst):
        raise OSError("simulated crash before publish")

    monkeypatch.setattr(cs, "_replace", boom)
    with pytest.raises(OSError):
        cs.atomic_write_json(path, {"x": 999})
    assert open(path, encoding="utf-8").read() == before
    with pytest.raises(OSError):
        store.save("Atomic", {"x": 2})
    monkeypatch.undo()

    leftovers = [f for f in os.listdir(tmp_path / "Atomic") if f.endswith(".tmp")]
    assert leftovers == []
    assert len(store.project_history("Atomic").configs) == 1
    assert store.load(store.latest_for_project("Atomic"))["x"] == 1


def test_unserialisable_config_never_touches_disk(store):
    store.save("Ser", {"x": 1})
    with pytest.raises(TypeError):
        store.save("Ser", {"x": object()})
    assert len(store.project_history("Ser").configs) == 1
    assert store.load(store.latest_for_project("Ser"))["x"] == 1


def test_last_project_pointer_written_atomically(store, tmp_path):
    store.remember_last_project("One")
    store.remember_last_project("Two")
    assert store.read_last_project() == "Two"
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
