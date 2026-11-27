"""H2c (#t-060, W1 2, seen on the show laptop 27/11): the health band keeps the NAME of the cause when it must shorten.

27/11, in the state every arrival day starts in (the project's empty-wall picture is of another day), the red band
read « FAIL - empty wall: the...: nothing in it lines up with the camera - the wall is empty now: STANDBY, ... »:
the fitter (ui/line_fit.py) cut the cause's name -- the text before the line's first separator -- and kept its
explanation.  Now a line that names its cause gives way: the tail's names, the explanation's brackets, the
explanation, a lead-in of the instruction that only states (« the wall is empty now: »), the name's brackets --
each whole -- and then wraps: the name and the instruction are never cut.

Clauses, for every message of that shape that reaches the band from core/reference_watch.py and core/ops_monitor.py
(``family``, built from the code itself), at the widths the app is used at -- windows of 1280, 1600 and 1920 px at
UI scales 1.0, 1.25 and 1.5, and the laptop's real window (1988 px at 1.5) -- with the fitter's own stand-in for the
GUI's metrics (a glyph of 7, 9, 11 px: what ImGui draws at those scales, test_h2_line_fit.mono):
  * the cause's name is whole, the instruction is whole, no fitted line is wider than its width, at most two lines
    unless the name and the instruction alone need more;
  * the 27/11 line at the laptop's 1600 px gives « ... not this wall - STANDBY, then CALIBRATE ... (+3: ...) »;
  * a line that fits is returned untouched;
  * drawn for real (tests/h2c_family_draw.py: the GUI's own painter and metrics), the same at scale 1.5.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core import ops_monitor as OM
from core import reference_watch as RW
from ui.line_fit import ELLIPSIS, candidates, fit, fit_text, split_alerts, split_health_line, wrap

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"

# 27/11 on the show laptop (gates/w1b/runs/c-app1/d_run_1.jpg): the line, and what the band drew.
LINE_2711 = ("FAIL - empty wall: the empty-wall picture is not this wall: nothing in it lines up with the camera - the "
             "wall is empty now: STANDBY, then CALIBRATE (2 Calibrate) on it, then Save to project   (+3: points, "
             "enhancement, light)")
DRAWN_2711 = ("FAIL - empty wall: the...: nothing in it lines up with the camera - the wall is empty now: STANDBY, then "
              "CALIBRATE (2 Calibrate) on it, then Save to project   (+3: ...)")
# Wanted: the name whole, the explanation gone first, the instruction whole.
WANTED_2711 = ("FAIL - empty wall: the empty-wall picture is not this wall - STANDBY, then CALIBRATE (2 Calibrate) on "
               "it, then Save to project   (+3: ...)")
# The laptop's real window (1988 px): there is room for the instruction's lead-in, so it stays.
WIDE_2711 = ("FAIL - empty wall: the empty-wall picture is not this wall - the wall is empty now: STANDBY, then "
             "CALIBRATE (2 Calibrate) on it, then Save to project   (+3: ...)")

GLYPH = {1.0: 7.0, 1.25: 9.0, 1.5: 11.0}       # px a glyph as ImGui draws it (h2b: probes/textsize4.py)
WINDOWS = (1280, 1600, 1920)
LAPTOP = 1988                                  # the laptop's window at UI scale 1.5 (gates/shot/laptop-1126b)


def mono(px=11.0):
    return lambda s: len(s) * px


def band_width(window: int, scale: float) -> float:
    """The px the health line may take in a window that wide: the band is the window less its padding (16 px,
    gates/h2b draw: band_rect), less the fitter's margin scaled(4) (gui._line_widths)."""
    return window - 16 - 4 * scale


def _norm(s: str) -> str:
    return " ".join(s.split())


def _name_whole(name: str, shown: str) -> bool:
    """The cause's name is on screen whole: every word of it; a bracket of it (the crop, where the wall went, the
    referee's reason, the numbers) whole or given way as « (...) », the last thing before the line wraps."""
    import re
    return _norm(name) in shown or _norm(re.sub(r"\([^()]*\)", f"({ELLIPSIS})", name)) in shown


def _after_lead(prompt: str) -> str:
    """A prompt's instruction without its stating lead-in « the wall is empty now: »."""
    return prompt.split(": ", 1)[1]


# ------------------------------------------------------------------ the family, from the code

def _plate_row(status: str, text: str, prompt=None):
    return OM.health_plate_row(status="ready", reference=SimpleNamespace(status=status, instruction=text),
                               prompt=prompt)


def family():
    """[(label, row, the cause's name, the instruction)] -- every message of the shape « name: explanation -
    instruction » / « name - [lead-in:] instruction » that can reach the band from core/reference_watch.py and
    core/ops_monitor.py, each as its row (the source the line names)."""
    out = []
    other = RW._verdict_text(RW.OTHER_WALL)
    crop = " (another crop: 1920x1080, camera 1936x1096)"
    not_this = "the empty-wall picture is not this wall"
    out += [("not this wall", _plate_row("other_wall", other), not_this, RW.FIX_PLATE),
            ("not this wall, empty now (RUN) -- 27/11", _plate_row("other_wall", other, RW.PROMPT_RUN), not_this,
             _after_lead(RW.PROMPT_RUN)),
            ("not this wall, empty now (STANDBY)", _plate_row("other_wall", other, RW.PROMPT_STANDBY), not_this,
             _after_lead(RW.PROMPT_STANDBY)),
            ("not this wall, another crop", _plate_row("other_wall", RW._verdict_text(RW.OTHER_WALL, crop=crop)),
             not_this + crop, RW.FIX_PLATE)]
    moved = RW._verdict_text(RW.MOVED, shift=12.3, dx=-3.0, dy=1.0)
    moved_name = "the camera moved about 12 px since the empty-wall picture" + RW._moved_where(-3.0, 1.0)
    out += [("moved", _plate_row("moved", moved), moved_name, RW.FIX_MOVED),
            ("moved, empty now", _plate_row("moved", moved, RW.PROMPT_MOVED), moved_name,
             _after_lead(RW.PROMPT_MOVED))]
    for reason in ("the foreground found no group under 2 boxes", "foreground: no group under 2 boxes"):
        w = RW.ReferenceWatch()
        for _ in range(RW.REFUSALS_ALERT):
            w.note_referee_refusal(reason)
        v = w.current()
        assert v is not None and v.status == RW.REFUSED, v
        name = f"the swipe's referee refused the empty-wall picture {RW.REFUSALS_ALERT} times in a row ({reason})"
        out += [(f"refused by the swipe's referee ({reason[:12]})", _plate_row(RW.REFUSED, v.instruction), name,
                 RW.FIX_PLATE),
                (f"refused by the swipe's referee ({reason[:12]}), empty now",
                 _plate_row(RW.REFUSED, v.instruction, RW.PROMPT_RUN), name, _after_lead(RW.PROMPT_RUN))]
    sig = RW.plate_signal(SimpleNamespace(plate=np.full((60, 80), 3.0, np.float32), ds=1, frame_size=(80, 60),
                                          sigma=0.5))
    assert sig is not None and not sig.usable
    dark_name = sig.text.split(" - ")[0]
    assert dark_name.startswith("the empty-wall picture is too dark for the foreground (")
    out += [("plate too dark", _plate_row(RW.DARK_PLATE, sig.text), dark_name, RW.FIX_DARK),
            ("plate too dark, empty now", _plate_row(RW.DARK_PLATE, sig.text, RW.PROMPT_DARK), dark_name,
             _after_lead(RW.PROMPT_DARK))]
    w = RW.ReferenceWatch()
    w.blind_ticks = RW.BLIND_TICKS
    v = w.current()
    assert v is not None and v.status == RW.BLIND, v
    blind_name = f"the foreground found no one on {RW.BLIND_TICKS} checks while the detector saw dancers"
    out += [("blind", _plate_row(RW.BLIND, v.instruction), blind_name, RW.FIX_DARK),
            ("blind, empty now", _plate_row(RW.BLIND, v.instruction, RW.PROMPT_DARK), blind_name,
             _after_lead(RW.PROMPT_DARK))]
    for why, todo in (("the picture only partly looks like the empty-wall picture (correlation 0.31: the light "
                       "changed, or people are in view)", RW.TODO_AGAIN),
                      ("the check failed (ValueError: bad shape)", RW.TODO_AGAIN),
                      ("only 12 % of the zone can be compared (at white or hidden)", RW.TODO_DARK)):
        out.append((f"not checked: {why[:24]}", _plate_row("unknown", RW._unknown_text(why, todo)),
                    "ready; not checked against the camera", todo))
    out.append(("stale plate", OM.health_plate_row(status="stale", reason="plate stale"),
                "no longer matches the wall (plate stale)", OM.EMPTY_WALL_HOW))
    out.append(("empty wall off", OM.health_plate_row(status="off"), "off", "3 Live > Use empty wall"))

    # the plate's exposure is not the camera's (X6): the real CleanPlate rule
    from core.foreground import CleanPlate

    def plate(**cam):
        p = SimpleNamespace(camera=cam, frame_size=(1296, 1776), created="2026-12-12T20:10:00")
        p.camera_mismatch = lambda e, g: CleanPlate.camera_mismatch(p, e, g)
        return p
    run_refuses = "RUN refuses it: Calibrate on the empty wall"
    out += [("plate exposure: not the camera's",
             OM.check_plate_exposure(plate=plate(exposure_us=25000.0, gain_db=30.0), exposure_us=8800.0,
                                     gain_db=30.0, frame_size=(1296, 1776)),
             "plate taken at 25.0 ms, the camera is at 8.8 ms", run_refuses),
            ("plate exposure: crop and exposure",
             OM.check_plate_exposure(plate=plate(exposure_us=25000.0, gain_db=30.0), exposure_us=8800.0,
                                     gain_db=30.0, frame_size=(1296, 1200)),
             "taken on a 1296x1776 picture, the camera gives 1296x1200; plate taken at 25.0 ms, the camera is at "
             "8.8 ms", run_refuses),
            ("plate exposure: not recorded", OM.check_plate_exposure(plate=plate(), exposure_us=8800.0, gain_db=30.0,
                                                                     frame_size=(1296, 1776)),
             "taken at exposure ? / gain ? on 1296x1776 (12-12 20:10)", "Calibrate on the empty wall")]
    # the wall-light family
    m = OM.HealthMonitor(gpu_stats_fn=None)
    m._light_alerted = True
    light = [r for r in OM.health_alert_rows(m, 100.0) if r.name == "light"]
    assert len(light) == 1
    from core.light_verdict import NO_LIGHT_HOW
    out += [("light changed", light[0], "the light is not the empty-wall snapshot's", OM.LIGHT_REMEDY),
            ("no light", OM.CauseResult("light", "fail", OM.no_light_line(3.0)),
             OM.no_light_line(3.0).split(" - ")[0], NO_LIGHT_HOW)]
    # the stall rate, the output, the enhancement faded out
    s = OM.StallRate()
    for i in range(61):
        s.update(float(i), i // 8, True)
    st = s.state(60.0)
    assert st.status == "fail"
    out += [("camera stalls", st, st.detail.split(": the picture")[0], OM.STALL_HOW),
            ("OSC off", OM.health_output_row(enabled=False), "OSC is off", OM.OSC_ON_HOW),
            ("enhancement faded out", OM.check_enhancement(enabled=True, gamma=1.3, clahe=2.5, force=False,
                                                           brightness=70.0, calibrated="2026-12-12 20:10"),
             "on (gamma 1.30 / CLAHE 2.5) but faded out", "tick Force (expert mode, Advanced > Enhancement)")]
    return out


def _ok_without(name: str):
    import test_h2_health_line as T
    return [r for r in T._ok_rows() if r.name != name]


TAIL_ROWS = [OM.CheckResult("points", "skip", "not checked: no new frame processed"),
             OM.CheckResult("camera stalls", "skip", "not checked: the camera is not live"),
             OM.CheckResult("height", "skip", "not checked")]


def family_lines():
    """[(label, line text, name, instruction)]: each row alone, and with three more sources (the tail)."""
    out = []
    for label, row, name, ins in family():
        alone = OM.health_line(_ok_without(row.name) + [row])
        assert alone.source == row.name and alone.state == row.status, (label, alone.text)
        out.append((label, alone.text, name, ins))
        if row.status != "skip":
            more = OM.health_line(_ok_without(row.name) + [row] + [r for r in TAIL_ROWS if r.name != row.name])
            assert more.source == row.name and "   (+" in more.text, (label, more.text)
            out.append((label + " +tail", more.text, name, ins))
    return out


LINES = family_lines()


# ------------------------------------------------------------------ the 27/11 line

def test_the_2711_line_comes_from_the_code_word_for_word():
    """The words are the code's (not rewritten): the 27/11 situation, built from the rows, is the line seen."""
    import test_h2_health_line as T
    v = SimpleNamespace(status=RW.OTHER_WALL, instruction=RW._verdict_text(RW.OTHER_WALL))
    plate = OM.health_plate_row(status="ready", reference=v, prompt=RW.PROMPT_RUN)
    enh = OM.check_enhancement(enabled=False, gamma=1.3, clahe=2.5, calibrated="2026-12-12 20:10")
    w = OM.BareGroupWatch()
    for t in T._frames(0.0, 3.5):
        w.update(t, [(500.0, 900.0, 4000.0)], [], body_h=T.H)
    light = OM.CauseResult("light", "fail", OM.no_light_line(3.0))
    rows = [r for r in T._ok_rows() if r.name != "empty wall"] + [plate, enh, w.state(3.5), light]
    assert OM.health_line(rows).text == LINE_2711


def test_the_2711_line_splits_into_name_explanation_lead_in_and_instruction():
    p = split_health_line(LINE_2711)
    assert p.named and p.head == "FAIL - empty wall: " and p.fact == "the empty-wall picture is not this wall"
    assert p.expl == "nothing in it lines up with the camera" and p.lead == "the wall is empty now"
    assert p.ins == _after_lead(RW.PROMPT_RUN) and p.whole == LINE_2711


def test_the_2711_line_gives_way_explanation_first():
    c = candidates(split_health_line(LINE_2711))
    assert c[0] == LINE_2711
    assert c[1] == LINE_2711.replace("(+3: points, enhancement, light)", f"(+3: {ELLIPSIS})")   # the tail's names
    assert c[2] == WIDE_2711                                                                 # the explanation
    assert c[3] == WANTED_2711                                                               # the stating lead-in
    assert len(c) == 4                                     # the name has no bracket; nothing else may give way


def test_the_2711_line_at_the_laptops_width_keeps_the_name():
    m = mono(GLYPH[1.5])
    assert m(DRAWN_2711) <= band_width(LAPTOP, 1.5)            # the band of 27/11 held what it drew ...
    assert fit(split_health_line(LINE_2711), band_width(1600, 1.5), m) == [WANTED_2711]
    assert fit(split_health_line(LINE_2711), band_width(LAPTOP, 1.5), m) == [WIDE_2711]
    assert DRAWN_2711 not in candidates(split_health_line(LINE_2711))      # ... the cut name is not an option now


def test_the_names_brackets_give_way_after_the_lead_in_and_separators_in_brackets_do_not_count():
    reason = "foreground: no group under 2 boxes"
    line = [t for label, t, _n, _i in LINES if label.startswith("refused by the swipe's referee (foreground: ")
            and label.endswith("empty now")][0]
    p = split_health_line(line)
    assert p.fact.endswith(f"({reason})") and p.lead == "the wall is empty now" and not p.expl
    c = candidates(p)
    assert c[-1] == p.head + p.fact.replace(f"({reason})", f"({ELLIPSIS})") + " - " + p.ins
    assert all(_norm(p.ins) in _norm(x) for x in c)


def test_a_fact_followed_by_its_order_is_fitted_as_before():
    """« OFF, but Calibrate chose ...: turn it on - Ctrl+Shift+E, ... »: « turn it on » says what to do -- the
    instruction's own, not an explanation (h2b's tests hold it whole); the fact still gives way word by word."""
    enh = OM.check_enhancement(enabled=False, gamma=1.3, clahe=2.5, calibrated="2026-12-12 20:10")
    p = split_health_line(OM.health_line(_ok_without("enhancement") + [enh]).text)
    assert not p.named and p.rest == "turn it on - " + OM.ENHANCE_ON_HOW
    for detail, named in (("the zone is empty - draw the dancers' wall (1 Rig > Region of Interest)", True),
                          ("zone 900x1700 at (100, 100) drawn on a 1296x1776 picture, cut to 900x1100 by the camera's "
                           "1296x1200: draw it again (1 Rig > Region of Interest), then Calibrate", False),
                          ("refused: the exposure was changed by hand", False)):
        assert split_health_line(f"FAIL - x: {detail}").named == named, detail


# ------------------------------------------------------------------ every message of the family, every width

def test_the_family_is_every_shape():
    labels = {label for label, *_ in LINES}
    for want in ("not this wall", "moved", "refused by the swipe's referee", "plate too dark", "blind",
                 "not checked", "plate exposure: not the camera's", "light changed", "no light", "camera stalls",
                 "OSC off", "empty wall off", "enhancement faded out"):
        assert any(x.startswith(want) for x in labels), want
    for label, text, name, ins in LINES:
        p = split_health_line(text)
        assert p.named and p.fact == name and p.whole == text, (label, p)
        assert p.ins == ins or (p.lead + ": " + p.ins) == ins, (label, p)


@pytest.mark.parametrize("scale", sorted(GLYPH))
@pytest.mark.parametrize("window", WINDOWS + (LAPTOP,))
def test_the_name_and_the_instruction_stay_whole(scale, window):
    if window == LAPTOP and scale != 1.5:
        pytest.skip("the laptop runs at 1.5")
    m, width = mono(GLYPH[scale]), band_width(window, scale)
    for label, text, name, ins in LINES:
        p = split_health_line(text)
        lines = fit(p, width, m)
        shown = _norm(" ".join(lines))
        assert _name_whole(name, shown), (window, scale, label, lines)
        assert _norm(ins) in shown, (window, scale, label, lines)
        assert all(m(x) <= width for x in lines), (window, scale, label, lines)
        assert _norm(" ".join(lines)) in [_norm(c) for c in candidates(p)], (window, scale, label, lines)
        least = len(wrap(p.head + name + " - " + ins + (f"   (+{p.more}: {ELLIPSIS})" if p.tail else ""), width, m))
        assert len(lines) <= max(2, least), (window, scale, label, lines)
        if len(lines) == 1 and lines[0] != text:
            assert not [c for c in candidates(p) if m(c) <= width and len(c) > len(lines[0])], (label, lines)


@pytest.mark.parametrize("scale", sorted(GLYPH))
def test_a_line_that_fits_is_returned_untouched(scale):
    m = mono(GLYPH[scale])
    for label, text, _name, _ins in LINES + [("27/11", LINE_2711, "", "")]:
        p = split_health_line(text)
        assert fit(p, m(text), m) == [text], label
        assert fit_text(p, m(text) + 50, m) == text and fit_text(p, None, m) == text, label


def test_the_alerts_line_keeps_the_name_too():
    """The Alerts line is fitted by the same split: « The empty-wall snapshot no longer matches the wall (...): the
    foreground is not to be trusted until a new one - <remedy> » keeps its name whole."""
    m3 = OM.HealthMonitor(gpu_stats_fn=None)
    msgs = [a.message for a in m3.plate_tick("stale since 21:43:10")]
    assert msgs
    p = split_alerts(msgs)
    assert p.named and p.fact == "The empty-wall snapshot no longer matches the wall (stale since 21:43:10)"
    assert p.expl == "the foreground is not to be trusted until a new one" and p.ins == OM.LIGHT_REMEDY
    for scale, px in GLYPH.items():
        for window in WINDOWS:
            width = band_width(window, scale) - 8 * px          # « Alerts: » and its gap before the text
            lines = fit(p, width, mono(px))
            shown = _norm(" ".join(lines))
            assert _norm(p.fact) in shown and _norm(p.ins) in shown and len(lines) <= 3, (window, scale, lines)


# ------------------------------------------------------------------ drawn for real

def test_the_family_drawn_for_real_keeps_name_and_instruction(tmp_path):
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("no display: the GUI's text metrics need a drawn frame")
    pytest.importorskip("dearpygui.dearpygui")
    out = tmp_path / "family.json"
    r = subprocess.run([sys.executable, str(HERE / "h2c_family_draw.py"), "--src", str(SRC), "--scale", "1.5",
                        "--widths", f"1280,1600,1920,{LAPTOP}", "--out", str(out)], capture_output=True, text=True,
                       timeout=600)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    cases = json.loads(out.read_text())
    names = {label: (name, ins) for label, _t, name, ins in LINES}
    assert {c["case"] for c in cases} == set(names) | {"27/11"}
    for c in cases:
        drawn = _norm(c["drawn"])
        p = split_health_line(c["whole_text"])
        assert drawn in [_norm(x) for x in candidates(p)], (c["width"], c["case"], c["drawn"])
        assert c["whole"] and c["inside"], (c["width"], c["case"], c["drawn"])        # instruction on screen
        if c["case"] != "27/11":
            assert _name_whole(names[c["case"]][0], drawn), (c["width"], c["case"], c["drawn"])
    on = {c["width"]: _norm(c["drawn"]) for c in cases if c["case"] == "27/11"}
    assert on[1600] == _norm(WANTED_2711) and on[LAPTOP] == _norm(WIDE_2711), on
