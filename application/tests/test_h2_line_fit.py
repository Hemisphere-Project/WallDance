"""H2 follow-up (#t-040): the health line's instruction is never cut -- nor the Alerts line's.

26/11 on the show laptop (gates/shot/laptop-1126b: the window 1988 px wide at UI scale 1.5) the red line read
« FAIL - enhancement: OFF, but Calibrate chose gamma 1.21 / CLAHE 2.5 ... Enable   (+3: camera s » and the
Alerts line stopped in the middle of its instruction, both cut at the window's right edge.

Clauses:
  * ui/line_fit.py: the parts of a line; what gives way and in which order (the tail, then the fact's brackets,
    then the fact's words, each marked « ... »), never the head nor the instruction; one line when it fits,
    else wrapped at words;
  * drawn for real (tests/h2_line_draw.py: the GUI's own painter, the real theme, ImGui's own text metrics) at
    1280, 1600 and 1920 px (and the laptop's 1988) at UI scales 1.0, 1.25 and 1.5, for every health source
    alone, the worst line of 26/11 and the Alerts line: the instruction is whole on screen in every case, what
    was dropped is the tail then the fact's detail, with an ellipsis; the line itself (sent to /status, the
    logs) is untouched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import ops_monitor as OM
from ui.line_fit import (ELLIPSIS, candidates, fit, fit_text, split_alerts, split_health_line, wrap)

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
WORST = ("FAIL - enhancement: OFF, but Calibrate chose gamma 1.21 / CLAHE 2.5 for this project (2026-10-14 "
         "10:01): turn it on - Ctrl+Shift+E, Advanced > Enhancement > Enable   (+3: camera stalls, empty wall, light)")
ENH_HOW = "turn it on - Ctrl+Shift+E, Advanced > Enhancement > Enable"


def mono(px=11.0):
    """A stand-in for the GUI's metrics in the pure tests: px a glyph (the laptop's 11 at 1.5)."""
    return lambda s: len(s) * px


# ------------------------------------------------------------------ the parts

def test_the_parts_of_the_worst_line():
    p = split_health_line(WORST)
    assert p.head == "FAIL - enhancement: "
    assert p.fact == "OFF, but Calibrate chose gamma 1.21 / CLAHE 2.5 for this project (2026-10-14 10:01)"
    assert p.sep == ": " and p.rest == ENH_HOW
    assert p.tail == "   (+3: camera stalls, empty wall, light)" and p.more == 3
    assert p.whole == WORST


def test_the_kept_part_holds_every_sources_instruction():
    """The fact's FIRST separator: what follows always holds the whole instruction (a superset when the fact has
    a « : » of its own, as the stall rate's)."""
    import test_h2_health_line as T
    ok = T._ok_rows()
    full = {"enhancement": ENH_HOW, "plate exposure": "the exposure was changed by hand"}
    for bad, ins in T._bad_rows():
        line = OM.health_line(ok + [bad])
        p = split_health_line(line.text)
        assert p.head == f"FAIL - {bad.name}: " and p.whole == line.text
        assert (full.get(bad.name) or ins) in p.rest, (bad.name, p)


def test_alerts_parts():
    p = split_alerts(["The light is <= 0.25x the empty-wall snapshot (10-14 13:30): between two pieces, ...",
                      "The empty-wall snapshot no longer matches the wall (x): the foreground ... - remedy"])
    assert p.head == "" and p.fact == "The light is <= 0.25x the empty-wall snapshot (10-14 13:30)"
    assert p.rest == "between two pieces, ..." and p.more == 1 and p.tail.startswith("   The empty-wall")
    assert split_alerts([]).whole == ""


# ------------------------------------------------------------------ what gives way, in order

def test_the_order_things_give_way():
    c = candidates(split_health_line(WORST))
    tail = f"   (+3: {ELLIPSIS})"
    assert c[0] == WORST
    assert c[1].endswith(ENH_HOW + tail) and "(2026-10-14 10:01)" in c[1]   # the tail's names first, its count stays
    assert f"project ({ELLIPSIS}): " + ENH_HOW + tail in c[2]                # then the fact's brackets
    for x in c[3:]:                                                          # then the fact, word by word
        assert x.startswith("FAIL - enhancement: ") and x.endswith(ELLIPSIS + ": " + ENH_HOW + tail), x
    assert c[-1] == "FAIL - enhancement: " + ELLIPSIS + ": " + ENH_HOW + tail
    assert len(set(c)) == len(c) and all(len(a) >= len(b) for a, b in zip(c[2:], c[3:]))
    assert all(ELLIPSIS in x for x in c[1:])                                # every give-way is marked


@pytest.mark.parametrize("glyphs", [60, 80, 100, 120, 150, 175, 200, 250])
def test_fit_keeps_the_instruction_whole_and_takes_the_fullest_that_fits(glyphs):
    m = mono()
    width = glyphs * 11.0
    lines = fit(split_health_line(WORST), width, m)
    shown = " ".join(lines)
    assert ENH_HOW in shown and shown.startswith("FAIL - enhancement: ")
    assert all(m(x) <= width for x in lines) or len(lines) > 1
    c = candidates(split_health_line(WORST))
    one = [x for x in c if m(x) <= width]
    if one:                                    # one line: the first candidate (the fullest) that fits
        assert lines == [one[0]]
    else:                                      # the instruction cannot fit one line: wrapped, nothing cut first
        assert len(lines) >= 2 and " ".join(lines) in [" ".join(x.split(" ")) for x in c]


def test_wrap_breaks_at_words_only():
    m = mono(1.0)
    assert wrap("aaa bbb ccc", 7, m) == ["aaa bbb", "ccc"]
    assert wrap("aaa   (+3: x)", 6, m) == ["aaa", "(+3:", "x)"]
    assert wrap("toolongword x", 4, m) == ["toolongword", "x"]
    assert wrap("", 10, m) == [""]


def test_nothing_known_yet_shows_the_line_whole():
    p = split_health_line(WORST)
    assert fit_text(p, None, mono()) == WORST and fit_text(p, 800, None) == WORST
    assert fit_text(split_health_line("OK - all 7 checks fine (camera, output)"), 10_000, mono()) == \
        "OK - all 7 checks fine (camera, output)"


# ------------------------------------------------------------------ drawn for real

def _draw(scale: float, widths: str, out: Path) -> list:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("no display: the GUI's text metrics need a drawn frame")
    pytest.importorskip("dearpygui.dearpygui")
    r = subprocess.run([sys.executable, str(HERE / "h2_line_draw.py"), "--src", str(SRC), "--scale", str(scale),
                        "--widths", widths, "--out", str(out)], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return json.loads(out.read_text())


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_the_real_lines_keep_their_instruction_whole_on_screen(scale, tmp_path):
    widths = "1280,1600,1920" + (",1988" if scale == 1.5 else "")
    cases = _draw(scale, widths, tmp_path / "draw.json")
    names = {c["case"] for c in cases}
    import test_h2_health_line as T
    for bad, _ins in T._bad_rows():                              # every source of the table, the stall rate's too
        assert any(n.startswith(f"alone: {bad.name}") for n in names), bad.name
    assert {"alone: light (no light)", "worst 26/11: enhancement FAIL +3", "alone: no light",
            "alone: light changed (<= 0.25x)", "two: light changed + plate stale"} <= names
    assert {c["width"] for c in cases} == {int(w) for w in widths.split(",")}
    bad = [c for c in cases if not c["whole"]]
    assert not bad, [(c["width"], c["case"], c["drawn"]) for c in bad]
    for c in cases:
        parts = split_health_line(c["whole_text"]) if c["kind"] == "health" else split_alerts(c["messages"])
        allowed = [" ".join(x.split()) for x in candidates(parts)]
        drawn = " ".join(c["drawn"].split())
        assert drawn in allowed, (c["width"], c["case"], c["drawn"])            # only what may give way, in order
        if c["cut"]:
            assert ELLIPSIS in drawn, (c["width"], c["case"])                   # marked
        assert c["inside"], (c["width"], c["case"])                             # the whole drawn text on screen
        assert c["lines"] <= (3 if c["kind"] == "alerts" else 2), (c["width"], c["case"], c["drawn"])
    worst = [c for c in cases if c["case"].startswith("worst")]
    assert all(ENH_HOW in " ".join(c["drawn"].split()) for c in worst)

