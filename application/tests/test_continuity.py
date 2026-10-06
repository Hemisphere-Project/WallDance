"""Unit tests for the continuity metrics C1-C10 + window pass rate (TEST-1).

Synthetic timelines only -- no GPU, no footage -- in the style of
test_scoring.py: these lock the *definitions* (01-continuity §3.3) so a tracker
change is judged by a stable yardstick.
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import continuity  # noqa: E402
import scoring  # noqa: E402

FPS = 20.0


def _m(n=1, warmup=0, start=0, frames=100, **extra):
    m = {"name": "t", "project": "p", "slot": 0, "start": start,
         "frames": frames, "warmup": warmup, "fps": FPS, "expected_count": n}
    m.update(extra)
    return m


def _row(f, ids, tracks=None, ref=None):
    r = {"frame": f, "reported": len(ids), "ids": list(ids)}
    if tracks is not None:
        r["tracks"] = tracks
    if ref is not None:
        r["ref"] = ref
    return r


def _trk(tid, x, y, h=100.0):
    return {"id": tid, "bbox": [x - 20, y - h / 2, 40, h], "centroid": [x, y]}


def _seq(spec):
    """[(n_frames, ids), ...] -> timeline rows (no spatial info)."""
    rows, f = [], 0
    for n, ids in spec:
        for _ in range(n):
            rows.append(_row(f, ids))
            f += 1
    return rows


# --------------------------------------------------------------------------- #
# C1-C6 (count + id stream)
# --------------------------------------------------------------------------- #
def test_perfect_stream():
    m = continuity.continuity_metrics(_seq([(200, [1])]), _m())
    assert m["coverage"] == 1.0
    assert m["drop_episodes"] == 0 and m["gaps_per_min"] == 0.0
    assert m["gap_max_s"] == 0.0 and m["gaps_ge_1s"] == 0
    assert m["distinct_ids"] == 1 and m["ids_per_dancer"] == 1.0
    assert m["reassoc_rate"] is None          # no gap to re-associate across
    assert m["handovers"] == 0
    assert m["acquisition_frames"] == 0
    assert m["mean_run_s"] == 10.0


def test_gaps_distribution_and_rate():
    # 3 gaps of 2, 10 and 30 frames (0.1 / 0.5 / 1.5 s) in 1200 frames = 1 min
    tl = _seq([(100, [1]), (2, []), (300, [1]), (10, []), (300, [1]),
               (30, []), (458, [1])])
    m = continuity.continuity_metrics(tl, _m())
    assert m["frames"] == 1200
    assert m["drop_episodes"] == 3
    assert m["gaps_per_min"] == 3.0
    assert m["gap_p50_s"] == 0.5
    assert m["gap_max_s"] == 1.5
    assert m["gaps_ge_0_5s"] == 2 and m["gaps_ge_1s"] == 1
    assert m["coverage"] == pytest.approx(1 - 42 / 1200, abs=1e-4)


def test_warmup_frames_not_scored_for_c1_but_c9_sees_them():
    tl = _seq([(15, []), (85, [1])])
    m = continuity.continuity_metrics(tl, _m(warmup=15))
    assert m["coverage"] == 1.0                # cold start hidden from C1 ...
    assert m["acquisition_frames"] == 15       # ... but not from C9
    assert m["acquisition_s"] == 0.75
    assert m["acquisition_basis"] == "expected"


def test_reassociation_same_vs_new_id():
    tl = _seq([(50, [1]), (5, []), (50, [1]), (5, []), (50, [2])])
    m = continuity.continuity_metrics(tl, _m())
    assert (m["reassoc_same"], m["reassoc_new"]) == (1, 1)
    assert m["reassoc_rate"] == 0.5
    assert m["distinct_ids"] == 2 and m["ids_per_dancer"] == 2.0
    assert m["handovers"] == 0                 # the id change happened across a gap


def test_handover_without_gap_vs_duplicate_vanishing():
    # 1 -> 2 on consecutive covered frames is a handover; a duplicate id (7)
    # appearing then vanishing next to a stable id is not.
    tl = _seq([(50, [1]), (50, [2]), (10, [2, 7]), (40, [2])])
    m = continuity.continuity_metrics(tl, _m())
    assert m["handovers"] == 1
    assert m["drop_episodes"] == 0
    assert m["primary_id_switches"] == 1      # contmetrics-style: min id only


def test_n2_coverage_counts_partial_drop():
    tl = _seq([(50, [1, 2]), (50, [1])])
    m = continuity.continuity_metrics(tl, _m(n=2))
    assert m["coverage"] == pytest.approx(150 / 200)
    assert m["drop_episodes"] == 1
    assert m["ids_per_dancer"] == 1.0


def test_per_range_expected_count():
    ec = [{"from": 0, "to": 49, "n": 1}, {"from": 50, "to": 99, "n": 0}]
    tl = _seq([(50, [1]), (50, [])])
    m = continuity.continuity_metrics(tl, _m(n=ec))
    assert m["coverage"] == 1.0 and m["drop_episodes"] == 0


# --------------------------------------------------------------------------- #
# C7 duplicates vs ghosts
# --------------------------------------------------------------------------- #
def test_c7_reference_basis_splits_duplicate_and_ghost():
    ref = [{"c": [500.0, 300.0], "h": 100.0, "conf": 0.9}]
    rows = [
        # two ids on the dancer -> 1 duplicate
        _row(0, [1, 2], [_trk(1, 500, 300), _trk(2, 540, 310)], ref),
        # one on the dancer, one 600 px away -> 1 ghost
        _row(1, [1, 3], [_trk(1, 500, 300), _trk(3, 1100, 300)], ref),
        _row(2, [1], [_trk(1, 500, 300)], ref),
    ]
    m = continuity.continuity_metrics(rows, _m())
    assert m["multi_id_frames"] == 2
    assert (m["dup_frames"], m["ghost_frames"]) == (1, 1)
    assert m["dup_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert m["ghost_extra_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert m["c7_basis"] == {"reference": 2}


def test_c7_proximity_basis_without_reference():
    rows = [
        _row(0, [1, 2], [_trk(1, 500, 300), _trk(2, 530, 300)]),   # same body
        _row(1, [1, 3], [_trk(1, 500, 300), _trk(3, 900, 300)]),   # elsewhere
        _row(2, [1, 4]),                                            # no spatial info
    ]
    m = continuity.continuity_metrics(rows, _m())
    assert (m["dup_frames"], m["ghost_frames"]) == (1, 1)
    assert m["c7_basis"] == {"proximity": 2, "unknown": 1}


# --------------------------------------------------------------------------- #
# C8 spatial validity + reference filtering
# --------------------------------------------------------------------------- #
def test_c8_spatial_validity():
    ref = [{"c": [500.0, 300.0], "h": 100.0, "conf": 0.8}]
    rows = [_row(f, [1], [_trk(1, 520, 310)], ref) for f in range(8)]
    rows += [_row(f, [1], [_trk(1, 800, 300)], ref) for f in range(8, 10)]   # 3 h off
    rows += [_row(10, [], [], ref)]            # a drop is C1's business, not C8's
    m = continuity.continuity_metrics(rows, _m())
    assert m["spatial_checked"] == 10 and m["spatial_misplaced"] == 2
    assert m["spatial_validity"] == 0.8


def test_c8_none_without_reference():
    m = continuity.continuity_metrics(_seq([(20, [1])]), _m())
    assert m["spatial_validity"] is None and m["inputs"]["ref"] is False


def test_reference_filtering_conf_spots_and_top_n():
    cfg = {"min_conf": 0.5, "tol_h": 0.75, "exclude_spots": [[870, 510, 70]]}
    row = {"ref": [
        {"c": [880.0, 500.0], "h": 150.0, "conf": 0.9},   # on a fixed spot
        {"c": [300.0, 300.0], "h": 180.0, "conf": 0.4},   # under the floor
        {"c": [400.0, 300.0], "h": 180.0, "conf": None},  # unknown conf passes
        {"c": [600.0, 300.0], "h": 180.0, "conf": 0.7},
    ]}
    got = continuity.reference_positions(row, 1, cfg)
    assert [r["c"][0] for r in got] == [400.0]           # None ranks as 1.0
    got2 = continuity.reference_positions(row, 2, cfg)
    assert [r["c"][0] for r in got2] == [400.0, 600.0]
    assert continuity.reference_positions({}, 1, cfg) == []


def test_reference_from_dets_maps_letterbox_to_original():
    space = {"scale": 0.5, "pad_x": 10.0, "pad_y": 20.0, "roi_x": 100, "roi_y": 50}
    dets = [(None, None, [110.0, 120.0, 40.0, 90.0])]
    ref = continuity.reference_from_dets(dets, space, [0.8123])
    # centre (130, 165) -> ((130-10)/0.5+100, (165-20)/0.5+50); h 90/0.5
    assert ref == [{"c": [340.0, 340.0], "h": 180.0, "conf": 0.812}]
    assert continuity.reference_from_dets(dets, space)[0]["conf"] is None


def test_c9_reference_basis_uses_first_dancer_evidence():
    ref = [{"c": [500.0, 300.0], "h": 100.0, "conf": 0.9}]
    rows = [_row(f, [], None, []) for f in range(10)]          # dancer not seen yet
    rows += [_row(f, [], None, ref) for f in range(10, 30)]    # seen, not emitted
    rows += [_row(f, [1], [_trk(1, 500, 300)], ref) for f in range(30, 60)]
    m = continuity.continuity_metrics(rows, _m())
    assert m["acquisition_basis"] == "reference"
    assert m["acquisition_frames"] == 20


def test_c9_never_acquired():
    m = continuity.continuity_metrics(_seq([(40, [])]), _m())
    assert m["acquisition_frames"] is None and m["acquisition_s"] is None


# --------------------------------------------------------------------------- #
# C10 freeze share
# --------------------------------------------------------------------------- #
def test_c10_freeze_share():
    rows = [_row(0, [1], [_trk(1, 100, 100)]),
            _row(1, [1], [_trk(1, 100, 100)]),    # held point
            _row(2, [1], [_trk(1, 105, 100)]),
            _row(3, [1], [_trk(1, 110, 100)]),
            _row(4, [1], [_trk(1, 110, 100)])]    # held point
    m = continuity.continuity_metrics(rows, _m())
    assert m["freeze_share"] == 0.5               # 2 frozen / 4 comparable


def test_c10_needs_consecutive_frames_and_centroids():
    rows = [_row(0, [1], [_trk(1, 100, 100)]), _row(2, [1], [_trk(1, 100, 100)])]
    assert continuity.continuity_metrics(rows, _m())["freeze_share"] is None
    assert continuity.continuity_metrics(_seq([(5, [1])]), _m())["freeze_share"] is None


# --------------------------------------------------------------------------- #
# Per-window pass rate
# --------------------------------------------------------------------------- #
def test_window_pass_rate_counts_full_windows_only():
    # 5 full 300-f windows + a 100-f tail; window 2 has a 2 s hole.
    spec = [(600, [1]), (40, []), (260, [1]), (700, [1])]
    m = _m(warmup=15, start=1000, frames=1600)
    w = continuity.window_pass_rate(_seq(spec), m)
    assert w["window"] == 300 and w["n_windows"] == 5
    assert w["n_pass"] == 4 and w["pass_rate"] == 0.8
    assert w["worst"]["start_abs"] == 1600
    assert w["worst"]["longest_drop_s"] == 2.0
    assert [x["passed"] for x in w["windows"]] == [True, True, False, True, True]


def test_window_pass_rate_warmup_only_on_first_window():
    # 10 empty frames opening EVERY window: excused by the warm-up in window 0
    # only -- later windows start with a warm tracker.
    spec = []
    for _ in range(3):
        spec += [(10, []), (290, [1])]
    w = continuity.window_pass_rate(_seq(spec), _m(warmup=15, frames=900),
                                    window=300)
    assert [x["drop_rate"] for x in w["windows"]][0] == 0.0
    assert all(x["drop_rate"] > 0 for x in w["windows"][1:])


def test_window_pass_rate_shifts_per_range_expected_count():
    ec = [{"from": 0, "to": 299, "n": 1}, {"from": 300, "to": 599, "n": 0}]
    w = continuity.window_pass_rate(_seq([(300, [1]), (300, [])]), _m(n=ec, frames=600))
    assert w["n_pass"] == 2       # window 2 expects nobody and sees nobody


def test_window_pass_line_from_manifest():
    m = _m(frames=300, window_pass={"window": 100, "class": "A", "drop_rate": 0.0})
    w = continuity.window_pass_rate(_seq([(100, [1]), (1, []), (199, [1])]), m)
    assert w["window"] == 100 and w["n_windows"] == 3 and w["n_pass"] == 2
    assert "window" not in w["line"]


# --------------------------------------------------------------------------- #
# Pass lines (scoring.evaluate_pass) + scoring entry point
# --------------------------------------------------------------------------- #
def test_long_span_pass_line():
    m = _m(**{"pass": dict(continuity.LONG_SPAN_PASS_A)})
    good = _seq([(2000, [1])])
    for r in good:
        r["tracks"] = [_trk(1, 500, 300)]
        r["ref"] = [{"c": [500.0, 300.0], "h": 100.0, "conf": 0.9}]
    cont = continuity.continuity_metrics(good, m)
    v = scoring.evaluate_pass(scoring.score_timeline(good, m), m, continuity=cont)
    assert v["passed"] and set(v["checks"]) == {
        "coverage_min", "gaps_ge_1s_max", "gaps_per_min_max",
        "ids_per_dancer_max", "spatial_validity_min"}

    bad = _seq([(1000, [1]), (30, []), (970, [2])])    # 1.5 s hole + new id
    cont = continuity.continuity_metrics(bad, m)
    v = scoring.evaluate_pass(scoring.score_timeline(bad, m), m, continuity=cont)
    assert not v["passed"]
    assert not v["checks"]["gaps_ge_1s_max"]["ok"]
    assert not v["checks"]["ids_per_dancer_max"]["ok"]
    # C8 not measurable without refs -> the check FAILS, it is not skipped
    assert v["checks"]["spatial_validity_min"] == {
        "value": None, "limit": 0.97, "ok": False, "note": "not computed"}


def test_legacy_pass_line_unchanged_by_continuity_arg():
    m = _m(**{"pass": {"class": "A", "drop_rate": 0.05, "ghost_rate": 0.05,
                       "longest_drop_s": 1.0}})
    tl = _seq([(100, [1])])
    r = scoring.score_timeline(tl, m)
    assert scoring.evaluate_pass(r, m) == scoring.evaluate_pass(
        r, m, continuity=continuity.continuity_metrics(tl, m))


def test_score_continuity_entry_point():
    rep = scoring.score_continuity(_seq([(600, [1])]), _m(frames=600))
    assert rep["metrics"]["coverage"] == 1.0
    assert rep["windows"]["n_windows"] == 2
    text = continuity.format_report(rep)
    assert text.startswith("C1") and "C10" in text and "windows (300 f)" in text


def test_pass_keys_name_real_metrics():
    """Every *_min/*_max key of the long-span line must name a metric the
    module actually produces (a typo would silently never pass)."""
    keys = set(continuity.continuity_metrics(_seq([(10, [1])]), _m()))
    for k in continuity.LONG_SPAN_PASS_A:
        if k.endswith(("_min", "_max")):
            assert k[:-4] in keys, k
    assert set(continuity.C_KEYS.values()) <= keys


# --------------------------------------------------------------------------- #
# Committed long-span manifests
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["hangar-aerial", "hangar-floor"])
def test_long_span_manifests_mirror_their_window(name):
    full = scoring.load_scenario(HERE / "scenarios" / f"{name}-full.json")
    win = scoring.load_scenario(HERE / "scenarios" / f"{name}.json")
    assert scoring.is_long_span(full) and not scoring.is_long_span(win)
    assert full["start"] == 0
    assert full["frames"] / full["fps"] >= 240          # >= 4 min (long-span line)
    # pinned config + fingerprint copied verbatim (CFG-1/2 deliberately NOT fixed)
    assert full["config"] == win["config"]
    assert full["recording_fingerprint"] == win["recording_fingerprint"]
    assert full["project"] == win["project"] and full["slot"] == win["slot"]
    assert full["expected_count"] == win["expected_count"]
    assert full["pass"] == continuity.LONG_SPAN_PASS_A
    assert full["window_pass"]["window"] == 300
    assert any("CFG-2" in s for s in full["known_issues"])


def test_known_n_discovery_skips_long_span():
    import known_n
    found = [Path(p).stem for p in known_n.scenarios_for_project("3_TANGO_HANGAR-whitebg2")]
    assert "hangar-aerial" in found and "hangar-floor" in found
    assert not any(s.endswith("-full") for s in found)


def test_continuity_cli(tmp_path, capsys, monkeypatch):
    tl = tmp_path / "tl.json"
    tl.write_text(json.dumps(_seq([(400, [1])])))
    sc = tmp_path / "sc.json"
    sc.write_text(json.dumps(_m(frames=400, recording_fingerprint={"bytes": 1},
                                config={"a": 1})))
    monkeypatch.setattr(sys, "argv", ["continuity.py", "--scenario", str(sc),
                                      "--timeline", str(tl)])
    continuity.main()
    out = capsys.readouterr().out
    assert "C1  coverage          1.0000" in out
