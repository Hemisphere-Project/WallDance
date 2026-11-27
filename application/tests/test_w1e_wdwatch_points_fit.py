"""W1e (#t-060, 27/11): the watch tool's POINTS line fits the summary's 100 characters with 1, 2 and 3 points.

« POINTS now 2 sent, 2 held: D1 held on the foreground 10.7s, D2 held on the belt 0.3s | 15 min: sent at 4 of 5
polls, held at 3 » went past the cap, and render_summary cut it with x[:width]: the window counts were lost.  Now
the slots go to short words past the width (« D1 fg 10.7s, D2 belt 0.3s »), then without their ages (three held
points: « D1 fg, D2 belt, D3 weak »), then the line keeps the counts alone -- never cut.  One point reads as before
(test_f4h_held_points pins it).
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

COUNTS = re.compile(r" \| (\d+) min: sent at (\d+) of (\d+) polls, held at (\d+)$")


def _wdwatch():
    if "wdwatch" in sys.modules:
        return sys.modules["wdwatch"]
    spec = importlib.util.spec_from_file_location("wdwatch", Path(__file__).resolve().parents[2] / "extra" /
                                                  "wdwatch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wdwatch"] = mod
    spec.loader.exec_module(mod)
    return mod


def _records(slots, polls, held_polls, t0=1000.0, every=10.0):
    """A start and ``polls`` ok polls; the last carries ``slots``; ``held_polls`` of them held a point."""
    recs = [{"kind": "start", "run": "w1e", "host": "laptop", "t": t0 - 1.0}]
    for i in range(polls):
        last = i == polls - 1
        sl = slots if last else [{"id": 1, "state": "fg" if i < held_polls else "live", "age_s": 2.0}]
        held = sum(1 for s in sl if s["state"] not in ("live",))
        recs.append({"kind": "poll", "ok": True, "t": t0 + i * every, "interval_s": every, "status": {},
                     "health": {"points": {"sent": len(sl), "held": held, "slots": sl}}, "hit_rate": None})
    return recs


ONE = [{"id": 1, "state": "fg", "age_s": 10.7}]
TWO = [{"id": 1, "state": "fg", "age_s": 10.7}, {"id": 2, "state": "belt", "age_s": 0.3}]
THREE = [{"id": 1, "state": "fg", "age_s": 10.7}, {"id": 2, "state": "belt", "age_s": 0.3},
         {"id": 3, "state": "weak", "age_s": 4.2}]
THREE_LONG = [{"id": 1, "state": "weak", "age_s": 1234.5}, {"id": 2, "state": "coasting", "age_s": 987.6},
              {"id": 3, "state": "weak", "age_s": 4321.0}]


@pytest.mark.parametrize("slots,polls,held_polls,words", [
    (ONE, 5, 3, "D1 held on the foreground 10.7s"),                        # as before: the long words fit
    (TWO, 5, 3, "D1 fg 10.7s, D2 belt 0.3s"),                              # the brief's example
    (THREE, 5, 3, "D1 fg, D2 belt, D3 weak"),                              # three with ages: 108 chars, so none
    (THREE, 90, 88, "D1 fg, D2 belt, D3 weak"),                            # a 15-minute window at 10 s polls
    (THREE_LONG, 900, 899, ""),                                            # 1 s polls, long holds: counts alone
])
def test_the_points_line_fits_and_keeps_its_counts(slots, polls, held_polls, words):
    wd = _wdwatch()
    recs = _records(slots, polls, held_polls, every=900.0 / polls)
    now = recs[-1]["t"] + 1.0
    screen = wd.render_summary(recs, now, minutes=15.0)
    line = next(x for x in screen.splitlines() if x.startswith("POINTS "))
    # what the line would be uncut: the summary did not have to cut it
    assert line == wd._points_line([r for r in recs if r["kind"] == "poll"], now - 900.0, 15.0)
    assert len(line) <= 100, (len(line), line)
    assert line.startswith(f"POINTS now {len(slots)} sent, {sum(1 for s in slots if s['state'] != 'live')} held")
    m = COUNTS.search(line)
    assert m, line
    assert m.groups() == ("15", str(polls), str(polls), str(held_polls + 1)), line   # sent at N of M, held at K
    if words:
        assert f"held: {words} | " in line, line
    else:
        assert " held | 15 min: " in line, line
    assert all(len(x) <= 100 for x in screen.splitlines())


def test_two_points_in_long_words_would_pass_the_cap():
    """Why: the long words with two points are over 100 characters (the line the summary used to cut)."""
    wd = _wdwatch()
    recs = [r for r in _records(TWO, 5, 3, every=180.0) if r["kind"] == "poll"]
    long = wd._points_line(recs, recs[0]["t"] - 1.0, 15.0, width=10_000)
    assert long.startswith("POINTS now 2 sent, 2 held: D1 held on the foreground 10.7s, D2 held on the belt 0.3s")
    assert len(long) > 100


def test_none_sent_reads_as_before():
    wd = _wdwatch()
    recs = _records(ONE, 5, 3, every=10.0)
    recs[-1]["health"]["points"] = {"sent": 0, "held": 0, "slots": []}
    polls = [r for r in recs if r["kind"] == "poll"]
    assert wd._points_line(polls, 0.0, 15.0) == "POINTS now none sent | 15 min: sent at 4 of 5 polls, held at 3"
