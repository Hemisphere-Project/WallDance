"""The settings swipe's facade gate on recorded takes (Plan Cordee P5): ``tests/swipe_gate.py`` gate 1, per swipe,
as shipped (the planner's set and the background pair, the operator's excluded cells), on the 08/10 takes mur2 6,
7 and 8 for every live setting.  Asked for, never part of a plain suite run (about 15 min, and the per-frame
records are scratch files on mgr-ran): it runs only when WD_SWIPE_RECORDS is set, and skips otherwise.

  WD_SWIPE_RECORDS  the folder holding ``<stem>.x1280m.jsonl`` / ``.l1280.jsonl`` (with masks); unset = skipped
                    (on mgr-ran: /data/WallDance/tmp_analysis/swipe-1126/core/rec)
  WD_SWIPE_P3       the folder holding ``<stem>.{x,l}{1760,1920}.jsonl`` -- default mgr-ran's
  WD_SWIPE_DRAWS    swipes per take x live setting (default 100, about 15 min on mgr-ran; the hand-run gate,
                    tests/swipe_gate.py, uses 2,000)
"""
import os
from dataclasses import replace
from pathlib import Path

import pytest

import swipe_gate as G
from core.swipe import SwipeParams

ASKED = bool(os.environ.get("WD_SWIPE_RECORDS"))
REC = Path(os.environ.get("WD_SWIPE_RECORDS") or "/nonexistent")
P3 = Path(os.environ.get("WD_SWIPE_P3", "/data/WallDance/tmp_analysis/night-1126/p3"))
DRAWS = int(os.environ.get("WD_SWIPE_DRAWS", "100"))
TRUTH = Path(__file__).resolve().parent / "truth0810"
STEMS = {"mur2 6": "jeudi0809_mur2__slot_6_20261008_215704", "mur2 7": "jeudi0809_mur2__slot_7_20261008_220951",
         "mur2 8": "jeudi0809_mur2__slot_8_20261008_221927"}


def _paths(stem):
    return ([REC / f"{stem}.x1280m.jsonl", REC / f"{stem}.l1280.jsonl"]
            + [P3 / f"{stem}.{c}.jsonl" for c in ("x1760", "l1760", "x1920", "l1920")])


_missing = [str(p) for s in STEMS.values() for p in _paths(s) if not p.exists()]
pytestmark = pytest.mark.skipif(
    not ASKED or bool(_missing),
    reason=("set WD_SWIPE_RECORDS to run the swipe's gate on the records" if not ASKED
            else f"swipe records absent ({len(_missing)} files): mgr-ran only"))


@pytest.fixture(scope="module")
def takes():
    return [G.Take(label, [str(p) for p in _paths(stem)]) for label, stem in STEMS.items()]


@pytest.mark.parametrize("pair", [False, True], ids=["planned-set", "background-pair"])
def test_every_live_setting_per_swipe(takes, pair):
    green, rows = G.gate1(takes, TRUTH, SwipeParams(), DRAWS, seed=1, pair=pair)
    bad = [f"{r['take']} live {r['live']}: worse {100 * r['worse']:.1f}% near-of-named "
           f"{'-' if r['near_of_named'] is None else round(100 * r['near_of_named'], 1)}% "
           f"best-named {100 * r['near_of_all']:.1f}% (best {r['best']}, live {r['live_truth']})"
           for r in rows if not r["green"]]
    assert len(rows) == 18
    assert green, "; ".join(bad)


def test_the_plate_is_not_blamed_with_a_ghost_prone_live_model(takes):
    """The P5 review's I4: with l @ 1280 live on the real 08/10 plate the old check blamed the plate on 38 % of
    swipes on mur2 6.  Now the cleanest setting decides."""
    p = SwipeParams()
    for take in takes:
        live = take.label_of("l @ 1280")
        cands = G.plan_set(take, live, take.dancer_px(live))
        offers, scores = G.offers_for(take, live, p), take.scores(p)
        blamed = 0
        for start in range(0, len(offers), max(1, len(offers) // 50)):
            res, _ = G.swipe_once(take, live, cands, p, start, offers, scores)
            blamed += "when the wall is empty" in res.reason
        assert blamed == 0, f"{take.label}: the plate blamed on {blamed} swipes"


def test_the_ranking_holds_at_the_projects_confidence(takes):
    """The referee scores at the caller's confidence: at 0.25 the planned set's verdicts still name no setting
    worse than the live one (clause i) for the live settings whose gap to the best is real."""
    p = replace(SwipeParams(), conf=0.25)
    green, rows = G.gate1(takes[2:], TRUTH, p, max(100, DRAWS // 3), seed=1, pair=False)
    assert all(r["clause_i"] for r in rows), [(r["live"], r["worse"]) for r in rows if not r["clause_i"]]
