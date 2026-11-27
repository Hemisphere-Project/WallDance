"""X3m review: the toast drawn for real -- what tests/test_gui_x3m_toast_fits.py reads.

Run as a script (never imported by pytest: it opens a DearPyGui viewport, minimized, because ImGui only lays out
a drawn frame -- tests/h2_line_draw.py's way):

    python tests/x3m_toast_draw.py --src <code root>/application/src --scale 1.5 --widths 1280,1600,1920 \\
        --messages in.json --out f.json

For each window width: the GUI's own layout (WallDanceGUI._recompute_layout, the viewport's size given), the GUI's
own painter (WallDanceGUI.show_toast) for each message, a few frames, then the drawn rectangles of the toast, its
text and the preview area (video_panel), in screen px.
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--scale", type=float, default=1.5)
    ap.add_argument("--widths", default="1280,1600,1920")
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--messages", required=True, help="a JSON list of toast texts")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, a.src)
    import dearpygui.dearpygui as dpg
    import gui as gui_mod
    import gui_builder

    messages = json.load(open(a.messages))
    widths = [int(w) for w in a.widths.split(",") if w]

    class _Gui:     # the stand-in of tests/h2_line_draw.py, with the real theme and scale
        def __init__(self):
            self.config, self.callbacks, self.expert_mode = {}, {}, False
            self.video_width, self.video_height, self._middle_height, self._icon_font = 640, 480, 600, None
            self._camera_width, self._camera_height = 1080, 1920        # a portrait façade camera
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
    dpg.create_viewport(title="wd-x3m-toast-draw", width=max(widths), height=a.height, decorated=False,
                        x_pos=0, y_pos=0, vsync=False)
    dpg.setup_dearpygui()
    dpg.show_viewport(minimized=True)
    W_cls = gui_mod.WallDanceGUI
    size = {"w": max(widths), "h": a.height}
    gui_mod.dpg.get_viewport_width = lambda: size["w"]       # the layout reads the window's size from these
    gui_mod.dpg.get_viewport_height = lambda: size["h"]

    def frames(n=3):
        for _ in range(n):
            dpg.render_dearpygui_frame()

    def rect(item):
        st = dpg.get_item_state(item)
        mn, sz = st.get("rect_min") or st.get("pos") or [0, 0], st.get("rect_size", [0, 0])   # a child window: pos
        return [float(mn[0]), float(mn[1]), float(mn[0] + sz[0]), float(mn[1] + sz[1])], bool(st.get("visible"))

    out = []
    try:
        for W in widths:
            size["w"] = W
            dpg.configure_item("main_window", width=W, height=a.height, pos=(0, 0), no_title_bar=True,
                               no_scrollbar=True)
            frames()
            W_cls._recompute_layout(g)
            frames()
            W_cls.show_toast(g, "Saved", duration=60.0)
            frames()
            line_h = rect("toast_text")[0][3] - rect("toast_text")[0][1]
            for msg in messages:
                W_cls.show_toast(g, msg, duration=60.0, color=(255, 140, 70))
                frames()
                tx, ty = dpg.get_item_pos("toast_window")
                tw, th = dpg.get_item_rect_size("toast_window")
                text_rect, visible = rect("toast_text")
                panel_rect, _v = rect("video_panel")
                lines = round((text_rect[3] - text_rect[1]) / line_h) if line_h else 0
                out.append({"scale": a.scale, "width": W, "message": msg, "drawn": dpg.get_value("toast_text"),
                            "visible": visible, "lines": lines, "line_h": line_h,
                            "wrap": dpg.get_item_configuration("toast_text")["wrap"],
                            "toast_rect": [float(tx), float(ty), float(tx + tw), float(ty + th)],
                            "text_rect": text_rect, "panel_rect": panel_rect,
                            "window": list(dpg.get_item_rect_size("main_window"))})
    finally:
        dpg.destroy_context()
    json.dump(out, open(a.out, "w"), indent=1)
    worst = max(out, key=lambda c: c["toast_rect"][2] - c["panel_rect"][2]) if out else None
    print(f"scale {a.scale}: {len(out)} toasts drawn (widths {a.widths}); max lines "
          f"{max((c['lines'] for c in out), default=0)}; worst right margin "
          f"{(worst['panel_rect'][2] - worst['toast_rect'][2]) if worst else 0:.0f} px")
    return 0


if __name__ == "__main__":
    sys.exit(main())
