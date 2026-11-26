"""H2 follow-up (#t-040): draw the REAL health line and the REAL Alerts line in the real GUI and say what is on screen.

Run as a script (never imported by pytest: it opens a DearPyGui viewport, minimized, for the GUI's own text
metrics -- ImGui only measures a drawn font):

    python tests/h2_line_draw.py --src <code root>/application/src --scale 1.5 --widths 1280,1600,1920 --out f.json

For each window width and each case -- every health source alone (tests/test_h2_health_line._bad_rows, the
HealthMonitor's conditions, « no light » when the code has it), the worst line seen on 26/11 (enhancement FAIL and
three more), and the Alerts line (each strip alert alone, then two) -- it builds the GUI (gui_builder.build_ui with
the real theme at that UI scale), gives the line to the GUI's own painter (WallDanceGUI.show_health_line /
_refresh_alerts, twice: the 1 Hz tick), renders, and reads back the drawn text and the rectangles.  A case is
« whole » when its instruction is in the drawn text and the drawn text lies inside the band (health) or the
window's content (alerts).  Writes the cases as JSON; prints one summary line.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

# The full instruction of each health source (what must never be cut): the table's instruction is its end; the
# enhancement's starts at « turn it on »; the plate exposure's refusal has no instruction -- its reason stands.
FULL_INSTRUCTION = {
    "camera": "check the camera's USB3 cable and power",
    "output": "start TouchDesigner and its patch",
    "empty wall": "when the wall is empty: CALIBRATE (2 Calibrate), then Save to project",
    "frames": "restart the app",
    "engine": "restart the app",
    "enhancement": "turn it on - Ctrl+Shift+E, Advanced > Enhancement > Enable",
    "ROI": "draw it again (1 Rig > Region of Interest), then Calibrate",
    "plate exposure": "the exposure was changed by hand",
    "points": "if it is a dancer: check Max dancers (3 Live)",
    "camera stalls": "at the next break: reseat the camera's USB cable at both ends, then try the other cable",
    "fps": "mains plugged in, close everything but WallDance and TouchDesigner",
    "gpu": "raise the laptop's back, clear its vents",
    "no dancer": "if dancers are on the wall: check the IR and the picture",
    "over cap": "check Max dancers (3 Live)",
    "height": "a dancer standing 10 s at the far wall",
    "light": None,      # filled from the code: LIGHT_REMEDY, or the « no light » instruction
}


def _norm(s: str) -> str:
    return " ".join(s.split())


def build_cases(tests_dir: str):
    """[(kind, label, state, text or messages, instruction)] from the code under test."""
    sys.path.insert(0, tests_dir)
    from core import ops_monitor as OM
    import test_h2_health_line as T
    cases = []
    ok = T._ok_rows()
    for bad, _ins in T._bad_rows():
        line = OM.health_line(ok + [bad])
        cases.append(("health", f"alone: {bad.name}" + (" (stale since)" if "stale since" in bad.detail else ""),
                      line.state, line.text, FULL_INSTRUCTION.get(bad.name)))
    now = 1000.0
    mon = OM.HealthMonitor(gpu_stats_fn=None)
    for attr in ("_fps_low_since", "_gpu_hot_since", "_no_det_since", "_over_cap_since", "_height_stale_since"):
        setattr(mon, attr, now - 600.0)
    mon._last_gpu_stats = {"temp": 91, "util": 80}
    mon._light_alerted = True
    remedy = getattr(OM, "LIGHT_REMEDY", "")
    for r in OM.health_alert_rows(mon, now):
        ins = FULL_INSTRUCTION.get(r.name) if r.name != "light" else remedy
        line = OM.health_line(ok + [r])
        cases.append(("health", f"alone: {r.name}", line.state, line.text, ins))
    no_light = getattr(OM, "no_light_line", None)
    if no_light is not None:                                   # H2 follow-up: « no light »
        from core.light_verdict import NO_LIGHT_HOW
        m2 = OM.HealthMonitor(gpu_stats_fn=None)
        m2.tick(now, fps=20.0, n_tracked=0, in_run=False, model_ready=True, camera_open=True,
                camera_reconnecting=False, playback_active=False, light_ratio=0.0, light_ref="10-14 13:30",
                picture_dn=0.0)
        for r in OM.health_alert_rows(m2, now):
            line = OM.health_line(ok + [r])
            cases.append(("health", f"alone: {r.name} (no light)", line.state, line.text, NO_LIGHT_HOW))
    # the worst seen on 26/11 (shot/laptop-1126b): enhancement FAIL and three more sources
    enh = OM.check_enhancement(enabled=False, gamma=1.21, clahe=2.5, calibrated="2026-10-14 10:01")
    s = OM.StallRate()
    for i in range(61):
        s.update(float(i), i // 20, True)                     # 3 stalls in a minute: orange
    light = OM.CheckResult("light", "warn", "the light is not the empty-wall snapshot's - " + remedy)
    rows = [r for r in ok if r.name != "empty wall"] + [OM.health_plate_row(status="none"), enh, s.state(60.0),
                                                        light]
    worst = OM.health_line(rows)
    cases.append(("health", "worst 26/11: enhancement FAIL +3", worst.state, worst.text,
                  FULL_INSTRUCTION["enhancement"]))
    # the Alerts line: each strip alert alone, then the two of them
    m3 = OM.HealthMonitor(gpu_stats_fn=None)
    alerts = {}
    for a in m3.tick(now, fps=20.0, n_tracked=0, in_run=False, model_ready=True, camera_open=True,
                     camera_reconnecting=False, playback_active=False, light_ratio=0.1, light_ref="10-14 13:30"):
        alerts[a.kind] = a.message
    for a in m3.plate_tick("stale since 21:43:10"):
        alerts[a.kind] = a.message
    if "light_changed" in alerts:
        cases.append(("alerts", "alone: light changed (<= 0.25x)", "warn", [alerts["light_changed"]], remedy))
    if "plate_stale" in alerts:
        cases.append(("alerts", "alone: plate stale", "warn", [alerts["plate_stale"]], remedy))
    if len(alerts) == 2:
        cases.append(("alerts", "two: light changed + plate stale", "warn", list(alerts.values()), remedy))
    if no_light is not None:
        from core.light_verdict import NO_LIGHT_HOW
        cases.append(("alerts", "alone: no light", "warn", [OM.no_light_line(0.0, alert=True)], NO_LIGHT_HOW))
    return cases


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--tests", default=None, help="the tests folder holding test_h2_health_line.py")
    ap.add_argument("--scale", type=float, default=1.5)
    ap.add_argument("--widths", default="1280,1600,1920")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, a.src)
    import dearpygui.dearpygui as dpg
    import gui as gui_mod
    import gui_builder

    cases = build_cases(a.tests or a.src.rsplit("/src", 1)[0] + "/tests")

    class _Gui:     # the stand-in the band's own test uses, with the real theme and scale
        def __init__(self):
            self.config, self.callbacks, self.expert_mode = {}, {}, False
            self.video_width, self.video_height, self._middle_height, self._icon_font = 640, 480, 600, None
            self._dpi_scale = a.scale
            self._alerts = {}

        def __getattr__(self, name):
            if name.startswith("_health"):
                raise AttributeError(name)
            if name.endswith("_theme"):
                return self.__dict__.setdefault("_any_theme", dpg.add_theme())
            if name.startswith(("_on_", "_toggle", "on_")):
                return lambda *x, **k: None
            raise AttributeError(name)

    dpg.create_context()
    if a.scale != 1.0:
        dpg.set_global_font_scale(a.scale)
    g = _Gui()
    gui_builder.setup_theme(g)
    with dpg.texture_registry():
        g.frame_texture_tag = dpg.add_dynamic_texture(4, 4, [0.0] * 64)
    gui_builder.build_ui(g)
    widths = [int(w) for w in a.widths.split(",") if w]
    # the viewport holds the whole window: ImGui leaves what lies outside it undrawn (its state stale)
    dpg.create_viewport(title="wd-h2-line-draw", width=max(widths), height=1600, decorated=False,
                        x_pos=0, y_pos=0,
                        vsync=False)          # a minimized window with vsync waits ~1 s a frame
    dpg.setup_dearpygui()
    dpg.show_viewport(minimized=True)
    W_cls = gui_mod.WallDanceGUI

    def frames(n=2):
        for _ in range(n):
            dpg.render_dearpygui_frame()

    out = []
    try:
        for W in widths:
            dpg.configure_item("main_window", width=W, height=1600, pos=(0, 0), no_title_bar=True,
                               no_scrollbar=True)
            frames(3)
            for kind, label, state, payload, instruction in cases:
                if kind == "health":
                    g._alerts = {}
                    W_cls._refresh_alerts(g)
                    for _ in range(2):                 # the 1 Hz tick: the second paints with this frame's metrics
                        W_cls.show_health_line(g, state, payload)
                        frames(2)
                    item, whole = "health_line_text", payload
                else:
                    g._alerts = {f"k{i}": m for i, m in enumerate(payload)}
                    for _ in range(2):
                        W_cls._refresh_alerts(g)
                        frames(2)
                    item, whole = "alerts_text", "   ".join(payload)
                drawn = dpg.get_value(item)
                st = dpg.get_item_state(item)
                if not st.get("visible") or not st.get("rect_size", [0, 0])[1]:
                    raise SystemExit(f"the {kind} text was not drawn ({label}, {W} px): no measure")
                tmin, tsize = st.get("rect_min", [0, 0]), st.get("rect_size", [0, 0])
                bx, by = dpg.get_item_pos("health_line_window")
                bw, bh = dpg.get_item_rect_size("health_line_window")
                win_w, win_h = dpg.get_item_rect_size("main_window")
                right, bottom = tmin[0] + tsize[0], tmin[1] + tsize[1]
                if kind == "health":
                    inside = right <= bx + bw and tmin[0] >= bx and tmin[1] >= by and bottom <= by + bh
                else:
                    inside = right <= bx + bw and bottom <= win_h
                has = instruction is None or _norm(instruction) in _norm(drawn)
                # where the instruction ends on screen: one line -> its end x by the drawn metrics (the item's
                # drawn width over dpg's base-size measure of the same text); several -> every line inside
                on_screen = inside
                lines = drawn.split("\n")
                if has and instruction and len(lines) == 1 and instruction in drawn:
                    k = tsize[0] / max(1.0, float(dpg.get_text_size(drawn)[0]))
                    end_x = tmin[0] + dpg.get_text_size(drawn[:drawn.index(instruction) + len(instruction)])[0] * k
                    top_ok = tmin[1] >= by if kind == "health" else True
                    on_screen = end_x <= bx + bw and top_ok and bottom <= (by + bh if kind == "health" else win_h)
                out.append({"scale": a.scale, "width": W, "kind": kind, "case": label, "state": state,
                            "whole_text": whole, "messages": payload if kind == "alerts" else None,
                            "drawn": drawn, "lines": drawn.count("\n") + 1,
                            "instruction": instruction, "instruction_drawn": has, "inside": bool(inside),
                            "whole": bool(has and on_screen), "text_rect": [tmin[0], tmin[1], right, bottom],
                            "band_rect": [bx, by, bx + bw, by + bh], "window": [win_w, win_h],
                            "cut": _norm(drawn) != _norm(whole)})
    finally:
        dpg.destroy_context()
    json.dump(out, open(a.out, "w"), indent=1)
    n_whole = sum(c["whole"] for c in out)
    n_in = sum(c["inside"] for c in out)
    print(f"scale {a.scale}: {n_whole} of {len(out)} cases with the instruction whole on screen, {n_in} drawn "
          f"wholly inside (widths {a.widths}); max lines {max((c['lines'] for c in out), default=0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
