"""Unit coverage for the known-N ritual's pure-Python logic (K1).

The search itself needs GPU + TRT + footage (run known_n.py directly); these
lock the config write-back routing + the oracle lookup, which are where a silent
mistake would corrupt a project save.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import known_n                       # noqa: E402
from core import config_schema       # noqa: E402
from core import config_store        # noqa: E402


def test_save_into_project_routes_profile_vs_shared(tmp_path, monkeypatch):
    proj = "TEST_PROJ"
    pdir = tmp_path / "projects" / proj
    pdir.mkdir(parents=True)
    base = config_schema.structure(
        {"confidence": 0.2, "tracker_max_age": 45}, {}, config_schema.DEFAULT_PROFILE)
    (pdir / f"{proj}_20260101_000000.json").write_text(json.dumps(base))

    monkeypatch.setattr(known_n, "REPO", tmp_path)
    store = config_store.ConfigStore(
        config_dir=str(tmp_path / "projects"),
        last_project_file=str(tmp_path / "last.txt"))

    tuned = {"confidence": 0.45, "crossval_skel_min_kpts": 10,
             "crossval_motion_min_ratio": 0.04, "tracker_max_age": 60}
    path = known_n.save_into_project(proj, tuned, store)
    saved = json.loads(open(path).read())
    active = saved["active_profile"]

    # confidence (Dial A) -> the active lighting profile; not a top-level key
    assert saved["profiles"][active]["confidence"] == 0.45
    assert "confidence" not in saved
    # gate/tracker knobs (G4: per-scene, internal) -> shared top-level
    assert saved["crossval_skel_min_kpts"] == 10
    assert saved["crossval_motion_min_ratio"] == 0.04
    assert saved["tracker_max_age"] == 60


def test_save_into_project_preserves_last_project(tmp_path, monkeypatch):
    """Saving known-N into project B must not hijack the active project A."""
    monkeypatch.setattr(known_n, "REPO", tmp_path)
    for p in ("A", "B"):
        d = tmp_path / "projects" / p
        d.mkdir(parents=True)
        (d / f"{p}_20260101_000000.json").write_text(json.dumps(
            config_schema.structure({}, {}, config_schema.DEFAULT_PROFILE)))
    store = config_store.ConfigStore(
        config_dir=str(tmp_path / "projects"),
        last_project_file=str(tmp_path / "last.txt"))
    store.remember_last_project("A")
    known_n.save_into_project("B", {"tracker_max_age": 60}, store)
    assert store.read_last_project() == "A"


def test_oracle_tau(tmp_path, monkeypatch):
    f = tmp_path / "analysis.json"
    f.write_text(json.dumps(
        {"cells": {"hangar-floor|yolo11x-pose|960": {"best_tau": 0.35}}}))
    monkeypatch.setattr(known_n, "ORACLE", f)
    assert known_n.oracle_tau("hangar-floor", "yolo11x-pose", 960) == 0.35
    assert known_n.oracle_tau("nope", "yolo11x-pose", 960) is None


def test_oracle_tau_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(known_n, "ORACLE", tmp_path / "nope.json")
    assert known_n.oracle_tau("x", "m", 1) is None
