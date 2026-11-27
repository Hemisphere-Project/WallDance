"""X3m review (27/11): the Calibrate stop toast is drawn whole -- the toast wraps to the preview area.

The stop toast is the only place the stop's advice is drawn whole (« If it is a person: clear the wall. If it is
an object: exclude it (1 Rig > Exclusion Mask) or move the Region of Interest off it. Then press Calibrate
again. », 257 characters with the 27/11 place), and the toast was one unwrapped line in an autosized window:
about 175 characters fit across the laptop's window, the rest -- the ROI way out and « Calibrate again » -- was
off it.  The ladder's toast (216) was already past the edge.  Now the toast's text wraps at the preview area's
right edge (the support band's way: DearPyGui's own wrap, ImGui's own metrics at draw time).

Clauses:
  * headless: the toast's text is given a wrap that ends inside the preview area (none before: -1);
  * drawn for real (tests/x3m_toast_draw.py: the GUI's own layout and painter, the real theme) at 1280, 1600 and
    1920 px (and the laptop's 1988) at UI scales 1.0, 1.25 and 1.5: the 27/11 stop toast, the longest place it
    may name and the ladder's toast are drawn wholly inside the preview area and the window, the text unchanged.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")

from core.empty_wall import OBJECT_WAYS_OUT  # noqa: E402

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
ADVICE = (f"If it is a person: clear the wall. If it is an object: {OBJECT_WAYS_OUT}. Then press Calibrate "
          "again.")
# the 27/11 stop toast, as runtime/calibration_flows._stop_for_person writes it (tests/test_x3m_mask_honoured.py
# pins the same text on the flow), the longest place it may name, and the ladder's not-clean toast
STOP_2711 = ("Someone is in view, bottom left of the wall (x 351, y 1668): Calibrate stopped and changed nothing. "
             + ADVICE)
STOP_LONG = ("Someone is in view, middle right of the wall (x 1259, y 1823): Calibrate stopped and changed "
             "nothing. " + ADVICE)
LADDER = ("YOLO now and then sees a person on the EMPTY wall, even at the weakest setting: if an object there "
          f"looks like a person, {OBJECT_WAYS_OUT}, then Calibrate again.")
MESSAGES = [STOP_2711, STOP_LONG, LADDER]


def test_the_toast_wraps_inside_the_preview_area():
    from gui import WallDanceGUI
    from gui_constants import TOAST_POS
    me = SimpleNamespace()
    dpg.create_context()
    try:
        with dpg.window(tag="main_window"):
            dpg.add_child_window(tag="video_panel", width=900, height=500)
        WallDanceGUI.show_toast(me, STOP_2711, duration=10.0, color=(255, 140, 70))
        wrap = dpg.get_item_configuration("toast_text")["wrap"]
        assert 0 < wrap and TOAST_POS[0] + wrap <= 900, wrap      # before: -1, one line off the window
        assert dpg.get_value("toast_text") == STOP_2711            # the text itself is unchanged
        dpg.configure_item("video_panel", width=1500)              # a wider window: the toast follows
        WallDanceGUI.show_toast(me, "Saved", duration=3.0)
        assert TOAST_POS[0] + dpg.get_item_configuration("toast_text")["wrap"] <= 1500
        assert dpg.get_item_configuration("toast_text")["wrap"] > 900 - TOAST_POS[0]
    finally:
        dpg.destroy_context()


def test_no_preview_built_yet_the_toast_is_shown_as_before():
    from gui import WallDanceGUI
    me = SimpleNamespace()
    dpg.create_context()
    try:
        WallDanceGUI.show_toast(me, STOP_2711, duration=10.0)
        assert dpg.get_value("toast_text") == STOP_2711
        assert dpg.get_item_configuration("toast_text")["wrap"] < 0
    finally:
        dpg.destroy_context()


def _draw(scale: float, widths: str, out: Path) -> list:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("no display: the toast's drawn size needs a drawn frame")
    msgs = out.with_suffix(".in.json")
    msgs.write_text(json.dumps(MESSAGES))
    r = subprocess.run([sys.executable, str(HERE / "x3m_toast_draw.py"), "--src", str(SRC), "--scale", str(scale),
                        "--widths", widths, "--messages", str(msgs), "--out", str(out)],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return json.loads(out.read_text())


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_the_stop_toast_is_drawn_whole_inside_the_preview(scale, tmp_path):
    widths = "1280,1600,1920" + (",1988" if scale == 1.5 else "")
    cases = _draw(scale, widths, tmp_path / "draw.json")
    assert len(cases) == len(MESSAGES) * len(widths.split(","))
    for c in cases:
        where = (c["width"], c["message"][:40], c["toast_rect"], c["panel_rect"], c["window"])
        assert c["drawn"] == c["message"], where                      # the whole text, nothing cut from it
        assert c["visible"], where
        assert c["toast_rect"][2] <= c["panel_rect"][2], where       # its right edge inside the preview area
        assert c["text_rect"][2] <= c["panel_rect"][2], where
        assert c["toast_rect"][3] <= c["panel_rect"][3], where       # its bottom too
        assert c["toast_rect"][2] <= c["window"][0], where
    # it does wrap where one line cannot hold it (the laptop's case: 1.5, 1600 / 1988 px)
    assert any(c["lines"] > 1 for c in cases if c["message"] == STOP_2711)
