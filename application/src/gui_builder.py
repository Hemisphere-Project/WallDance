"""
Layout and theme helpers for the DearPyGui interface.
The functions here are called from `gui.WallDanceGUI` to keep that class lean.

Phase 2 reorganization:
- Live Controls: Standby/Live/Pause buttons, camera/OSC status (always visible, prominent)
- Show Settings: Person height, max dancers, confidence, OSC target (per-venue, visible)
- Advanced: Tracker, enhancement, model selection (collapsed by default)
"""

import os
from typing import Any, Tuple

import dearpygui.dearpygui as dpg
import numpy as np

from core.config import (RECORDING_SLOTS, IDENTITY_SLOTS_ENABLED, IDENTITY_SLOTS_MAX_DANCERS,
                         IDENTITY_SLOTS_STABILITY, IDENTITY_SLOTS_COAST_S,
                         IDENTITY_SLOTS_STATIC_GUARD, IDENTITY_SLOTS_SMART_HOLD, FOREGROUND_ENABLED,
                         IDENTITY_SLOTS_USE_IR_BELT, OSC_SEND_STATE)
from gui_icons import Icons
from gui_constants import (
    TEXT_NORMAL, TEXT_MUTED, TEXT_DIM, TEXT_HINT, TEXT_FAINT,
    HEADING_GREEN, BRIGHT_GREEN, PALE_GREEN, WARN_ORANGE, ERROR_SOFT,
    CONTROL_PANEL_WIDTH,
)
# SystemState moved to runtime/api.py (DECOMPOSITION_PLAN Phase 3): the
# runtime owns the authoritative state; re-exported here for the existing
# `from gui_builder import SystemState` sites.
from runtime.api import SystemState


# Input transform (REQ-5) rotation combo: clockwise quarter turns.
INPUT_ROTATION_LABELS = ("0\u00b0", "90\u00b0", "180\u00b0", "270\u00b0")


def rotation_label(degrees) -> str:
    """0/90/180/270 -> the combo label ('90°')."""
    try:
        deg = int(float(str(degrees).rstrip("\u00b0").strip())) % 360
    except (TypeError, ValueError):
        deg = 0
    return f"{deg}\u00b0" if deg in (0, 90, 180, 270) else INPUT_ROTATION_LABELS[0]


def rotation_from_label(label):
    """The combo label ('90°') -> 90; None when it is not a quarter turn."""
    try:
        deg = int(float(str(label).rstrip("\u00b0").strip()))
    except (TypeError, ValueError):
        return None
    return deg if deg in (0, 90, 180, 270) else None


# State badge colors: (text_color, bg_color)
STATE_COLORS = {
    SystemState.STANDBY: ((255, 220, 100, 255), (100, 90, 40, 255)),   # Yellow/Amber
    SystemState.RUN:     ((100, 255, 100, 255), (40, 100, 50, 255)),   # Green
}

STATE_LABELS = {
    SystemState.STANDBY: "STANDBY",
    SystemState.RUN:     "RUN",
}

# Global DPI scale factor - set by setup_theme based on gui._dpi_scale
_dpi_scale = 1.0

# Layout constants live in gui_constants.py (CONTROL_PANEL_WIDTH is
# re-exported above for the existing `from gui_builder import ...` sites).


def scaled(value: int) -> int:
    """Scale a pixel value by the DPI factor."""
    return int(value * _dpi_scale)


def _slider_nudge(slider_tag: str, step: float, min_val: float, max_val: float, callback=None):
    """Return a nudge function that adjusts a slider by +/- step."""
    def _nudge(sender, app_data, direction=1):
        cur = dpg.get_value(slider_tag)
        new = round(cur + step * direction, 6)
        new = max(min_val, min(max_val, new))
        dpg.set_value(slider_tag, new)
        if callback:
            callback(slider_tag, new)
    return _nudge


def _add_slider_row(slider_tag: str, step: float, min_val: float, max_val: float, callback=None):
    """Add - / + buttons next to a slider for fine-tuning.
    Call this inside a horizontal group, right after the slider.
    """
    nudge = _slider_nudge(slider_tag, step, min_val, max_val, callback)
    dpg.add_button(
        label="-", width=scaled(20),
        callback=lambda: nudge(None, None, -1),
    )
    dpg.add_button(
        label="+", width=scaled(20),
        callback=lambda: nudge(None, None, 1),
    )


def get_state_colors(state: SystemState) -> Tuple[Tuple, Tuple]:
    """Get text and background colors for a system state."""
    return STATE_COLORS.get(state, STATE_COLORS[SystemState.STANDBY])


def setup_theme(gui: Any):
    """Configure the global and topbar themes."""
    global _dpi_scale
    _dpi_scale = getattr(gui, '_dpi_scale', 1.0)
    
    # Scaled style values
    frame_pad_x = scaled(6)
    frame_pad_y = scaled(5)
    item_space_x = scaled(8)
    item_space_y = scaled(6)
    cell_pad = scaled(6)
    
    with dpg.theme() as gui.global_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, frame_pad_x, frame_pad_y)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, item_space_x, item_space_y)
            dpg.add_theme_style(dpg.mvStyleVar_CellPadding, cell_pad, cell_pad)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, scaled(14))
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (30, 30, 32, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (48, 48, 52, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (62, 62, 68, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (78, 78, 85, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (50, 85, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (120, 200, 130, 255))
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (100, 180, 110, 255))
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (130, 210, 140, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Button, (65, 107, 71, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (85, 130, 92, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (105, 150, 112, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Header, (65, 107, 71, 255))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (85, 130, 92, 255))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (105, 150, 112, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBg, (35, 35, 38, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt, (42, 42, 46, 255))
    dpg.bind_theme(gui.global_theme)

    with dpg.theme() as gui._topbar_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_CellPadding, scaled(4), scaled(1))
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, scaled(4), scaled(3))
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, scaled(6), scaled(1))

    # Recording button themes
    with dpg.theme() as gui._rec_live_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (50, 120, 50, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (70, 150, 70, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (90, 180, 90, 255))

    with dpg.theme() as gui._rec_live_active_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (80, 180, 80, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (100, 200, 100, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (120, 220, 120, 255))

    # LIVE button when playing (greyed greenish)
    with dpg.theme() as gui._rec_live_playing_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (60, 90, 70, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (70, 110, 80, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (80, 130, 90, 255))

    with dpg.theme() as gui._rec_btn_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (120, 50, 50, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (150, 70, 70, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (180, 90, 90, 255))

    # REC button disabled (grey) - need to style both enabled and disabled states
    with dpg.theme() as gui._rec_btn_disabled_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (55, 55, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (55, 55, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (55, 55, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (100, 100, 100, 255))
        with dpg.theme_component(dpg.mvButton, enabled_state=False):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (55, 55, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (55, 55, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (55, 55, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (100, 100, 100, 255))

    with dpg.theme() as gui._rec_btn_recording_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (200, 50, 50, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (220, 70, 70, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (240, 90, 90, 255))

    with dpg.theme() as gui._slot_empty_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (50, 50, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (70, 70, 75, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (90, 90, 95, 255))

    with dpg.theme() as gui._slot_has_recording_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (70, 90, 120, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (90, 110, 140, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (110, 130, 160, 255))

    with dpg.theme() as gui._slot_playing_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (50, 150, 200, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (70, 170, 220, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (90, 190, 240, 255))

    with dpg.theme() as gui._slot_recording_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (200, 80, 80, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (220, 100, 100, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (240, 120, 120, 255))

    # === PHASE 2: Live Control State Themes ===
    
    # State badge theme for LIVE state
    with dpg.theme() as gui._state_live_theme:
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (100, 255, 100, 255))

    # Live control button themes - 2-state system with clear active/inactive styling
    # STANDBY button: Yellow/Amber when active, greyed when inactive
    with dpg.theme() as gui._btn_standby_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (55, 55, 60, 255))       # Greyed out
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (70, 70, 75, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (85, 85, 90, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (140, 140, 140, 255))      # Dim text
    
    with dpg.theme() as gui._btn_standby_active_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (180, 160, 60, 255))     # Bright yellow
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (200, 180, 80, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (220, 200, 100, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 255, 255, 255))      # White text

    # RUN button: Green when active, greyed when inactive
    with dpg.theme() as gui._btn_run_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (55, 55, 60, 255))       # Greyed out
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (70, 70, 75, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (85, 85, 90, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (140, 140, 140, 255))      # Dim text
    
    with dpg.theme() as gui._btn_run_active_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (60, 180, 70, 255))      # Bright green
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (80, 200, 90, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (100, 220, 110, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 255, 255, 255))      # White text

    # Visualization toolbar button theme (compact)
    with dpg.theme() as gui._vis_btn_on_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (65, 107, 71, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (85, 130, 92, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (105, 150, 112, 255))
    
    with dpg.theme() as gui._vis_btn_off_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (50, 50, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (65, 65, 70, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (80, 80, 85, 255))

    # Get DPI scale from gui instance if available
    dpi_scale = getattr(gui, '_dpi_scale', 1.0)
    load_icon_font(gui, scale=dpi_scale)


def load_icon_font(gui: Any, scale: float = 1.0):
    """Load Font Awesome icons used by the GUI.
    
    Args:
        gui: The WallDanceGUI instance
        scale: DPI scale factor for font size adjustment
    """
    src_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(src_dir)
    font_path = os.path.join(project_dir, "assets", "fa-solid.otf")

    if not os.path.exists(font_path):
        print(f"Warning: Icon font not found at {font_path}")
        gui._icon_font = None
        return

    # Icons stay at fixed size - don't scale with DPI
    # The global font scale already affects icon rendering size
    font_size = 14  # Fixed base size
    with dpg.font_registry():
        gui._icon_font = dpg.add_font(font_path, font_size)
        dpg.add_font_range_hint(dpg.mvFontRangeHint_Default, parent=gui._icon_font)
        dpg.add_font_range(0xf000, 0xf8ff, parent=gui._icon_font)


def create_texture(gui: Any):
    """Create a dynamic texture for the video frame."""
    gui.frame_buffer = np.zeros(gui.texture_height * gui.texture_width * 4, dtype=np.float32)
    with dpg.texture_registry(show=False):
        gui.frame_texture_id = dpg.add_raw_texture(
            width=gui.texture_width,
            height=gui.texture_height,
            default_value=gui.frame_buffer,
            format=dpg.mvFormat_Float_rgba,
            tag=gui.frame_texture_tag,
        )
    print(f"Texture created: {gui.video_width}x{gui.video_height}")


def build_ui(gui: Any):
    """Build the main UI layout with fixed regions.

    Layout:
    - Top bar: project/config, system badges (full width, fixed height)
    - Middle: video preview (flexible) + control panel (fixed width)
    - Bottom bar: SOURCE/playback, STANDBY/RUN, perf stats (full width)

    The middle section height is computed to fill the space between top
    and bottom.  The preview image is scaled to fit the available area.
    """
    with dpg.window(tag="main_window", label="WallDance Control Panel"):
        with dpg.group(tag="top_bar_wrapper"):
            build_top_bar(gui)
        dpg.add_spacer(height=scaled(2))
        # Linear phase rail (OPERATOR_V2 Track O) — the primary control surface.
        with dpg.group(tag="phase_rail_wrapper"):
            build_phase_rail(gui)
        dpg.add_spacer(height=scaled(2))
        with dpg.group(horizontal=True, tag="middle_group"):
            dpg.add_spacer(width=scaled(6))  # Left padding
            build_video_panel(gui)
            build_phase_panel(gui)           # right column = selected phase only
            dpg.add_spacer(width=scaled(6))  # Right padding
        dpg.add_spacer(height=scaled(2))
        with dpg.group(tag="alerts_strip_wrapper"):
            build_alerts_strip(gui)
        with dpg.group(tag="drawer_bar_wrapper"):
            build_drawer_bar(gui)   # Recordings controls inline, on one line
        with dpg.group(tag="bottom_bar_wrapper"):
            build_bottom_bar(gui)
    # Floating Advanced drawer (toggled by the phase-rail Advanced button) —
    # holds today's numeric sections verbatim.
    build_advanced_drawer(gui)


def build_top_bar(gui: Any):
    """Top bar with project/config selectors and GPU stats."""
    with dpg.table(
        header_row=False,
        policy=dpg.mvTable_SizingStretchProp,
        borders_outerH=False,
        borders_outerV=False,
        pad_outerX=False,
        tag="top_bar_table",
    ):
        dpg.bind_item_theme("top_bar_table", gui._topbar_theme)
        dpg.add_table_column(init_width_or_weight=1.0, width_stretch=True)
        dpg.add_table_column(init_width_or_weight=0.0, width_stretch=False, width_fixed=True)
        with dpg.table_row():
            with dpg.group(horizontal=True):
                dpg.add_text("Project:", color=HEADING_GREEN)
                dpg.add_combo(
                    items=["+ New..."],
                    tag="topbar_project_combo",
                    default_value="",
                    width=scaled(150),
                    callback=gui._on_topbar_project_change,
                )
                dpg.add_spacer(width=scaled(8))
                save_btn = dpg.add_button(
                    label=Icons.FLOPPY_DISK,
                    tag="topbar_save_btn",
                    width=scaled(20),
                    height=scaled(20),
                    callback=gui._on_save_config,
                )
                if gui._icon_font:
                    dpg.bind_item_font(save_btn, gui._icon_font)
                with dpg.tooltip(save_btn):
                    dpg.add_text("Save config (Ctrl+S)")

                dpg.add_spacer(width=scaled(12))
                # Show/Rehearsal (Lighting) toggle moved to the phase 2 Profile panel.

                save_ind = dpg.add_text(Icons.CHECK, tag="save_indicator", color=BRIGHT_GREEN, show=False)
                if gui._icon_font:
                    dpg.bind_item_font(save_ind, gui._icon_font)
            with dpg.group(horizontal=True, tag="status_chip_group"):
                # Unified status chip group (OPERATOR_V2 §2.3a): one row, plain
                # meanings, fallback states explicit (Cam / OSC / FPS / Engine /
                # state). Every badge keeps its tag so the stats updaters are
                # unaffected.
                # System state badge (prominent)
                state_badge = dpg.add_text("RUN", tag="state_badge", color=(100, 255, 100, 255))
                dpg.bind_item_theme(state_badge, gui._state_live_theme)
                with dpg.tooltip(state_badge):
                    dpg.add_text("System state:\n• STANDBY: Preview only, no YOLO\n• RUN: Full YOLO + OSC output")
                dpg.add_spacer(width=scaled(10))
                dpg.add_text("CAM:", color=TEXT_NORMAL)
                cam_badge = dpg.add_text("OFF", tag="badge_cam", color=ERROR_SOFT)
                with dpg.tooltip(cam_badge):
                    dpg.add_text("Camera status: ON (green) or OFF (red)")
                dpg.add_spacer(width=scaled(3))
                cam_type_badge = dpg.add_text("[--]", tag="badge_cam_type", color=TEXT_MUTED)
                with dpg.tooltip(cam_type_badge):
                    dpg.add_text("Camera Source Type:\n[IDS] = IDS Peak SDK\n[CV] = OpenCV Fallback")
                dpg.add_spacer(width=scaled(4))
                dpg.add_text(
                    "RECONNECTING",
                    tag="camera_reconnect_label",
                    color=(255, 200, 80),
                    show=gui.config.get("camera_reconnecting", False),
                )
                dpg.add_spacer(width=scaled(6))
                dpg.add_text("OSC:", color=TEXT_NORMAL)
                osc_badge = dpg.add_text("OFF", tag="badge_osc", color=ERROR_SOFT)
                with dpg.tooltip(osc_badge):
                    dpg.add_text("OSC output status: ON (green) or OFF (red)")
                remote_badge = dpg.add_text("REMOTE", tag="badge_remote",
                                            color=WARN_ORANGE, show=False)
                with dpg.tooltip(remote_badge):
                    dpg.add_text("A remote session (SSH tunnel) is talking to the app.\n"
                                 "Control during RUN only if allowed in 6 Live.",
                                 tag="badge_remote_tip")
                dpg.add_spacer(width=scaled(6))
                dpg.add_text("--", tag="badge_model", color=(150, 200, 255))
                dpg.add_spacer(width=scaled(3))
                engine_badge = dpg.add_text("[PT]", tag="badge_engine_type", color=(255, 220, 100))  # Yellow for PyTorch
                with dpg.tooltip(engine_badge):
                    dpg.add_text("[TRT] = TensorRT (fast, GPU-optimized)\n[PT] = PyTorch (slower, more compatible)")
                dpg.add_spacer(width=scaled(6))
                compute_badge = dpg.add_text("[CPU FALLBACK]", tag="badge_compute_mode", color=ERROR_SOFT, show=False)
                with dpg.tooltip(compute_badge):
                    dpg.add_text("Running on CPU fallback mode", tag="badge_compute_reason_text", color=(255, 180, 120))
                    dpg.add_text("Action: install a GPU-compatible PyTorch/CUDA build or keep CPU mode.", tag="badge_compute_action_text", color=TEXT_NORMAL)
                dpg.add_spacer(width=scaled(6))
                dpg.add_text("FPS:", color=TEXT_NORMAL)
                dpg.add_text("--", tag="badge_fps", color=(150, 200, 255))
                dpg.add_spacer(width=scaled(6))
                dpg.add_text("GPU:", color=TEXT_NORMAL)
                dpg.add_text("--", tag="topbar_gpu_util_text", color=TEXT_MUTED)
                dpg.add_spacer(width=scaled(8))
                dpg.add_text("VRAM:", color=TEXT_NORMAL)
                dpg.add_text("--", tag="topbar_gpu_vram_text", color=TEXT_MUTED)


def build_video_panel(gui: Any):
    """Video preview area - alert banner (hidden) + image, dynamically sized."""
    with dpg.child_window(
        width=gui.video_width + scaled(8),
        height=gui._middle_height,
        border=False,
        no_scrollbar=True,
        tag="video_panel",
    ):
        with dpg.child_window(
            tag="trt_banner_window",
            width=gui.video_width,
            height=scaled(34),
            border=False,
            no_scrollbar=True,
            show=False,
        ):
            with dpg.group(horizontal=True):
                dpg.add_text("", tag="trt_banner_text", color=(255, 230, 230))
                dpg.add_button(
                    label="Rebuild TRT",
                    tag="trt_banner_btn",
                    callback=gui._on_trt_rebuild,
                )
        with dpg.theme() as _trt_banner_theme:
            with dpg.theme_component(dpg.mvChildWindow):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (130, 25, 25))
        dpg.bind_item_theme("trt_banner_window", _trt_banner_theme)
        dpg.add_image(
            gui.frame_texture_tag,
            width=gui.video_width,
            height=gui.video_height,
            tag="video_image",
        )


def build_bottom_bar(gui: Any):
    """Bottom bar: performance stats only.

    SOURCE/recording controls moved to the Recordings drawer; CALIBRATE/DANCERS/
    POOL/ALL to phases 3-4; STANDBY/RUN to phase 6 (OPERATOR_V2 Track O). Always
    at the bottom of the window, fixed height.
    """
    dpg.add_separator()

    # Performance stats row
    with dpg.group(horizontal=True, tag="bottom_stats_group"):
        dpg.add_text("Dancers:", color=TEXT_HINT)
        dpg.add_text("0", tag="dancers_text", color=PALE_GREEN)
        dpg.add_spacer(width=scaled(6))
        dpg.add_text("In:", color=TEXT_HINT)
        dpg.add_text("--", tag="input_res_text", color=PALE_GREEN)
        dpg.add_spacer(width=scaled(4))
        dpg.add_text("Prev:", color=TEXT_FAINT)
        dpg.add_text("--", tag="preview_tex_text", color=(90, 90, 90))
        dpg.add_spacer(width=scaled(6))
        dpg.add_text("Bright:", color=TEXT_HINT)
        dpg.add_text("--", tag="brightness_text", color=TEXT_DIM)
        dpg.add_spacer(width=scaled(8))
        dpg.add_text("|", color=(60, 60, 60))
        dpg.add_spacer(width=scaled(8))
        dpg.add_text("FPS:", color=TEXT_HINT)
        dpg.add_text("--", tag="fps_text", color=PALE_GREEN)
        dpg.add_spacer(width=scaled(6))
        dpg.add_text("Enh:", color=TEXT_FAINT)
        dpg.add_text("--", tag="time_enhance", color=TEXT_HINT)
        dpg.add_spacer(width=scaled(3))
        dpg.add_text("YOLO:", color=TEXT_FAINT)
        dpg.add_text("--", tag="time_yolo", color=TEXT_HINT)
        dpg.add_spacer(width=scaled(3))
        dpg.add_text("Trk:", color=TEXT_FAINT)
        dpg.add_text("--", tag="time_track", color=TEXT_HINT)
        dpg.add_spacer(width=scaled(3))
        dpg.add_text("Prev:", color=TEXT_FAINT)
        dpg.add_text("--", tag="time_preview", color=TEXT_HINT)
        dpg.add_spacer(width=scaled(6))
        dpg.add_text("Tot:", color=TEXT_HINT)
        dpg.add_text("--", tag="time_total", color=PALE_GREEN)

    # Hidden tags (for code compatibility)
    with dpg.group(show=False):
        dpg.add_text("", tag="path_enhance")
        dpg.add_text("", tag="path_yolo")
        dpg.add_text("", tag="path_track")


def build_control_panel(gui: Any):
    """Advanced-drawer control stack — today's numeric sections, verbatim, behind
    one disclosure (OPERATOR_V2 Track O §2.1). Mutually-exclusive accordion.

    Input (camera) is promoted to phase ① Rig, ROI + Exclusion to ① Rig, the
    Show/Rehearsal toggle to ② Profile, and the View toolbar to ⑥ Live; what
    remains here is the developer/power-user set:
    1. Background (expert)
    2. Enhancement
    3. Model
    4. Detection (person height, sensitivity, + expert tracker params)
    5. Preview
    6. OSC
    """
    with dpg.child_window(width=scaled(CONTROL_PANEL_WIDTH), height=gui._middle_height, border=False, tag="control_panel"):
        build_background_section(gui)
        dpg.add_spacer(height=scaled(8))
        build_enhancement_section(gui)
        dpg.add_spacer(height=scaled(8))
        build_model_section(gui)
        dpg.add_spacer(height=scaled(8))
        build_detection_section(gui)
        dpg.add_spacer(height=scaled(8))
        build_preview_section(gui)
        dpg.add_spacer(height=scaled(8))
        build_osc_section(gui)              # moved back from phase 2 Profile


# --------------------------------------------------------------------------- #
# Phase rail + per-phase right panel (OPERATOR_V2 Track O §2.1)
# --------------------------------------------------------------------------- #
# (id, rail label) — the canonical operator spine.
PHASES = [
    ("rig", "1 Rig"),
    ("profile", "2 Profile"),
    ("aim", "3 Aim"),
    ("calibrate", "4 Calib"),
    ("verify", "5 Verify"),
    ("live", "6 Live"),
]


def build_phase_rail(gui: Any):
    """Horizontal phase rail — clicking a phase opens its panel on the right.

    Drives the existing commands via gui._on_phase_select (pure UI nav). State
    chips (done/pending/count) are refreshed by gui._update_phase_rail().  A
    2-column stretch table (the proven top-bar pattern) right-aligns the
    Advanced drawer toggle on the rail."""
    with dpg.table(
        header_row=False,
        policy=dpg.mvTable_SizingStretchProp,
        borders_outerH=False,
        borders_outerV=False,
        pad_outerX=False,
        tag="phase_rail_table",
    ):
        dpg.add_table_column(init_width_or_weight=1.0, width_stretch=True)
        dpg.add_table_column(init_width_or_weight=0.0, width_stretch=False, width_fixed=True)
        with dpg.table_row():
            with dpg.group(horizontal=True):
                dpg.add_text("PHASE", color=HEADING_GREEN)
                dpg.add_spacer(width=scaled(6))
                for pid, label in PHASES:
                    btn = dpg.add_button(
                        label=label,
                        tag=f"phase_btn_{pid}",
                        width=scaled(92),
                        height=scaled(26),
                        callback=lambda s, a, u: gui._on_phase_select(u),
                        user_data=pid,
                    )
                    dpg.bind_item_theme(btn, gui._btn_standby_theme)
                    # Per-phase status chip (done/pending/count) — set by _update_phase_rail.
                    dpg.add_text("", tag=f"phase_chip_{pid}", color=TEXT_HINT)
                    dpg.add_spacer(width=scaled(6))
            # Right-aligned: Advanced drawer toggle (OPERATOR_V2 — promoted onto
            # the phase bar; the panel itself stays a floating drawer).
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Advanced",
                    tag="advanced_drawer_btn",
                    width=scaled(110),
                    height=scaled(26),
                    callback=lambda: gui._toggle_advanced_drawer(),
                )
                with dpg.tooltip("advanced_drawer_btn"):
                    dpg.add_text("Today's numeric sections (Input, Background, "
                                 "Enhancement,\nModel, Detection, Preview) — "
                                 "developer / power-user knobs.")


def build_phase_panel(gui: Any):
    """Right-of-video column — only the selected phase's sub-panel is shown."""
    with dpg.child_window(width=scaled(CONTROL_PANEL_WIDTH), height=gui._middle_height,
                          border=False, tag="phase_panel"):
        build_phase_rig(gui)
        build_phase_profile(gui)
        build_phase_aim(gui)
        build_phase_calibrate(gui)
        build_phase_verify(gui)
        build_phase_live(gui)


# Leave room for the phase panel's vertical scrollbar so wrapped text isn't
# clipped on the right.
_PHASE_WRAP = CONTROL_PANEL_WIDTH - 34


def build_phase_rig(gui: Any):
    """① Rig & Frame — camera source + stage ROI + manual exclusion paint."""
    with dpg.group(tag="phase_panel_rig", show=True):
        dpg.add_text("1 - Rig & Frame", color=HEADING_GREEN)
        dpg.add_text("Pick the camera source, mount + manual focus, draw the stage "
                     "ROI, paint known dead zones. Masked cells stay dimmed on the "
                     "preview at all times.",
                     color=TEXT_MUTED, wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(8))
        build_input_section(gui)            # camera selector + input (rig setup)
        dpg.add_spacer(height=scaled(8))
        build_roi_section(gui)              # promoted from the control stack
        dpg.add_spacer(height=scaled(8))
        build_exclusion_mask_section(gui)   # promoted (manual paint, decision 5)
        dpg.add_spacer(height=scaled(8))
        build_rig_sheet_section(gui)        # MRK-0: shooting setup -> every take's .meta


# (field, label, kind) -- fields/bounds are owned by core.config_schema.RIG_FIELDS.
RIG_SHEET_ROWS = (
    ("lens", "Lens", "text"),
    ("focal_mm", "Focal (mm)", "float"),
    ("f_number", "Aperture (f/)", "float"),
    ("focus_m", "Focus ring (m)", "float"),
    ("filter", "Filter", "text"),
    ("illuminator", "IR light", "text"),
    ("illuminator_offset_cm", "IR offset (cm)", "float"),
    ("camera_distance_m", "Cam->stage (m)", "float"),
    ("camera_height_m", "Cam height (m)", "float"),
    ("markers", "IR markers", "text"),
    ("notes", "Notes", "text"),
)


def build_rig_sheet_section(gui: Any):
    """Rig sheet (MRK-0): what the camera cannot report -- lens, iris, focus,
    IR light position, distances. Saved with the project (Save) and copied
    into every recording's .meta for later analysis. 0 / empty = unknown."""
    rig = dict(gui.config.get("rig") or {})
    with dpg.collapsing_header(label="Rig sheet", default_open=False,
                               tag="section_rig_sheet", closable=False):
        dpg.add_text("Saved with the project and with every recording. "
                     "0 / empty = unknown.", color=TEXT_MUTED,
                     wrap=scaled(_PHASE_WRAP))
        for field, label, kind in RIG_SHEET_ROWS:
            with dpg.group(horizontal=True):
                dpg.add_text(label, color=TEXT_NORMAL)
                tag = f"rig_{field}_input"
                if kind == "text":
                    dpg.add_input_text(tag=tag, default_value=str(rig.get(field) or ""),
                                       width=-1, user_data=field,
                                       callback=gui._on_rig_field)
                else:
                    dpg.add_input_float(tag=tag, default_value=float(rig.get(field) or 0.0),
                                        width=-1, step=0, format="%.2f", user_data=field,
                                        callback=gui._on_rig_field)
        with dpg.tooltip("section_rig_sheet"):
            dpg.add_text("Aperture, focus and the IR light's offset from the lens\n"
                         "decide how bright retro markers and dancers come out;\n"
                         "the camera cannot report them. Fill once per venue.")


def build_phase_profile(gui: Any):
    """② Profile — Show/Rehearsal lighting toggle + project/config management."""
    with dpg.group(tag="phase_panel_profile", show=False):
        dpg.add_text("2 - Profile", color=HEADING_GREEN)
        dpg.add_text("Pick the lighting profile (Show = night / Rehearsal = day) and "
                     "manage the project config. Each profile keeps its own "
                     "calibrated settings.",
                     color=TEXT_MUTED, wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(8))
        dpg.add_text("Lighting profile", color=TEXT_NORMAL)
        profile_radio = dpg.add_radio_button(
            items=["Show", "Rehearsal"],
            tag="profile_switch_radio",
            default_value=str(gui.config.get("active_profile", "show")).capitalize(),
            horizontal=True,
            callback=gui._on_profile_switch,
        )
        with dpg.tooltip(profile_radio):
            dpg.add_text("Lighting profile (day vs night): separate calibrated\nsettings (exposure/gain, gamma/CLAHE, MOG2, exclusion\nmask, sensitivity) per lighting condition.\nShow = live-performance / night lighting;\nRehearsal = day / setup lighting.\nCalibrate once per profile, then switch freely.")
        dpg.add_spacer(height=scaled(10))
        # Project / config management (relocated off the cluttered top bar).
        dpg.add_text("Config version", color=TEXT_NORMAL)
        with dpg.group(horizontal=True):
            dpg.add_combo(
                items=[],
                tag="topbar_config_combo",
                default_value="",
                width=scaled(200),
                callback=gui._on_topbar_config_change,
            )
            safe_btn = dpg.add_button(
                label=Icons.ROTATE,
                tag="topbar_safe_btn",
                width=scaled(26),
                height=scaled(26),
                callback=gui._on_safe_defaults,
            )
            if gui._icon_font:
                dpg.bind_item_font(safe_btn, gui._icon_font)
            with dpg.tooltip(safe_btn):
                dpg.add_text("Click: Load safe defaults\nCtrl+click: Save as safe defaults")
            qr_btn = dpg.add_button(
                label=Icons.QRCODE,
                tag="topbar_qr_btn",
                width=scaled(26),
                height=scaled(26),
                callback=gui._on_show_qr,
            )
            if gui._icon_font:
                dpg.bind_item_font(qr_btn, gui._icon_font)
            with dpg.tooltip(qr_btn):
                dpg.add_text("Phone monitor: show a QR code to open the web UI")


def build_phase_aim(gui: Any):
    """③ Aim & empty scene — scene calibration (Calib1)."""
    with dpg.group(tag="phase_panel_aim", show=False):
        dpg.add_text("3 - Aim & Empty Scene", color=HEADING_GREEN)
        dpg.add_text("Clear stage. Drives IDS exposure/gain to the blur budget, "
                     "seeds gamma/CLAHE, sweeps MOG2, captures the clean plate. "
                     "Re-run after each focus/IR change.",
                     color=TEXT_MUTED, wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(10))
        calib_btn = dpg.add_button(
            label="CALIBRATE",
            tag="calibrate_btn",
            width=scaled(120),
            height=scaled(30),
            callback=gui._on_calibrate,
        )
        dpg.bind_item_theme(calib_btn, gui._btn_standby_theme)
        with dpg.tooltip(calib_btn):
            dpg.add_text("Calib 1 - SCENE (empty stage, during rigging):\n"
                         "drives IDS exposure/gain to the blur budget, seeds\n"
                         "gamma/CLAHE, sweeps MOG2 var+scale, captures the\n"
                         "clean plate. Re-click after each focus/IR change.\n"
                         "(Exclusion masks are a manual paint step in phase ①.)")
        dpg.add_spacer(height=scaled(10))
        # Last-calibration line: a real per-run timestamp + the exact applied
        # values need the gated calibration_state metadata (Track S); until then
        # this shows the deterministic influence + a placeholder for the time.
        dpg.add_text("Last calibrated: --", tag="aim_last_calib_text", color=TEXT_HINT)
        dpg.add_text("Aim sets: exposure / gain -> gamma -> MOG2 var+scale -> "
                     "clean-plate.", color=TEXT_DIM, wrap=scaled(_PHASE_WRAP))


def build_phase_calibrate(gui: Any):
    """④ Calibrate dancers — run a pass, then review/apply the evidence pool
    inline (the pool used to be a separate POOL modal; the rail panel has room
    so it lives here, populated on entry + after each run)."""
    with dpg.group(tag="phase_panel_calibrate", show=False):
        dpg.add_text("4 - Calibrate Dancers", color=HEADING_GREEN)
        dpg.add_text("Run a pass on 1-4 dancers (live or playback) to add evidence, "
                     "then review the pool below and Apply. Add more runs (costumes "
                     "/ positions) for a more robust result.",
                     color=TEXT_MUTED, wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(10))
        dancers_btn = dpg.add_button(
            label="Calibrate with Dancers",
            tag="calib2_btn",
            width=scaled(200),
            height=scaled(30),
            callback=gui._on_calib2,
        )
        dpg.bind_item_theme(dancers_btn, gui._btn_standby_theme)
        with dpg.tooltip(dancers_btn):
            dpg.add_text("Calib 2 - DANCERS (1-4 people, live or playback):\n"
                         "collects one evidence run (sizes, confidences, speeds)\n"
                         "into the project pool below; review + Apply the pooled\n"
                         "result: person height, image size, sensitivity seed.")
        dpg.add_spacer(height=scaled(8))
        # Shared calibration status line (Calib1 + Calib2 messages).
        dpg.add_text("", tag="calibrate_status", color=(160, 200, 255), show=False)
        dpg.add_spacer(height=scaled(6))
        # Evidence pool, rendered inline by gui.show_calib2_dialog (populated on
        # entering this phase and after each run via Calib2PoolChanged).
        dpg.add_group(tag="calib2_pool_inline")
        # --- Auto-tune (CLAHE + confidence) — the segment/slot pass-line sweep.
        dpg.add_spacer(height=scaled(12))
        dpg.add_separator()
        dpg.add_spacer(height=scaled(6))
        dpg.add_text("Auto-tune (CLAHE + confidence)", color=TEXT_NORMAL)
        dpg.add_text("Sweep CLAHE x confidence over the newest recording, scored "
                     "vs the dancer count, to auto-pick the detection enhancement "
                     "(CLAHE has no formula). STANDBY only; ~2-4 min.",
                     color=TEXT_MUTED, wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(6))
        with dpg.group(horizontal=True):
            dpg.add_text("Dancers in clip (N):", color=TEXT_DIM)
            dpg.add_input_int(tag="calib_sweep_n", default_value=1, min_value=1,
                              max_value=8, width=scaled(90), step=1)
            dpg.add_text("Slot:", color=TEXT_DIM)
            dpg.add_input_int(tag="calib_sweep_slot", default_value=-1, min_value=-1,
                              max_value=9, width=scaled(90), step=1)
        dpg.add_text("Record a clip in the Recordings panel first, or use -1 for "
                     "the newest. Keep N dancers present throughout, moving "
                     "fast/varied (no entrances/exits).",
                     color=TEXT_HINT, wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(4))
        sweep_btn = dpg.add_button(
            label="Calculate (auto-tune)", tag="calib_sweep_btn",
            width=scaled(200), height=scaled(30), callback=gui._on_calib_sweep)
        dpg.bind_item_theme(sweep_btn, gui._btn_standby_theme)
        with dpg.tooltip(sweep_btn):
            dpg.add_text("Offline CLAHE x confidence sweep over the newest recording\n"
                         "(separate process), scored vs N dancers. Picks the best\n"
                         "detection enhancement; review the curve, then Apply the\n"
                         "seed. STANDBY only, ~2-4 min.")
        dpg.add_spacer(height=scaled(6))
        dpg.add_text("", tag="calib_sweep_result_text", color=TEXT_MUTED,
                     wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(4))
        apply_btn = dpg.add_button(
            label="Apply seed", tag="calib_sweep_apply_btn", show=False,
            width=scaled(140), height=scaled(28), callback=gui._on_calib_sweep_apply)
        dpg.bind_item_theme(apply_btn, gui._btn_standby_theme)
        with dpg.tooltip(apply_btn):
            dpg.add_text("Save the auto-tuned values into the project (profile-aware).\n"
                         "Reload the project to run with them.")

        # Known-N tune (K1) — heavier offline search over the per-scene detection
        # knobs vs the project's labelled scenarios.
        dpg.add_spacer(height=scaled(10))
        dpg.add_separator()
        dpg.add_spacer(height=scaled(6))
        dpg.add_text("Known-N tune (per-scene detection knobs)", color=TEXT_NORMAL)
        dpg.add_text("Joint search over confidence / detection gates / track-age "
                     "against this project's labelled scenarios (known dancer "
                     "counts), on the show path. Needs verified scenarios for the "
                     "project. STANDBY only; several minutes.",
                     color=TEXT_MUTED, wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(4))
        kn_btn = dpg.add_button(
            label="Tune (known-N)", tag="known_n_btn",
            width=scaled(200), height=scaled(30), callback=gui._on_known_n)
        dpg.bind_item_theme(kn_btn, gui._btn_standby_theme)
        with dpg.tooltip(kn_btn):
            dpg.add_text("Offline joint coord-descent (separate process) over the\n"
                         "per-scene known-N knobs vs the project's labelled scenarios.\n"
                         "Reports before/after; review, then Apply. STANDBY, several min.")
        dpg.add_spacer(height=scaled(6))
        dpg.add_text("", tag="known_n_result_text", color=TEXT_MUTED,
                     wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(4))
        kn_apply = dpg.add_button(
            label="Apply tune", tag="known_n_apply_btn", show=False,
            width=scaled(140), height=scaled(28), callback=gui._on_known_n_apply)
        dpg.bind_item_theme(kn_apply, gui._btn_standby_theme)
        with dpg.tooltip(kn_apply):
            dpg.add_text("Save the tuned knobs into the project (profile-aware) and\n"
                         "push them onto the running session.")


def build_phase_verify(gui: Any):
    """⑤ Verify — Go-Live readiness glance (+ dry-run on the last recording)."""
    with dpg.group(tag="phase_panel_verify", show=False):
        dpg.add_text("5 - Verify", color=HEADING_GREEN)
        dpg.add_text("Glance at readiness (camera / FPS / TensorRT / OSC / "
                     "calib-age / disk / GPU) before the room fills. Nothing here "
                     "blocks Go-Live - it's a pre-flight glance.",
                     color=TEXT_MUTED, wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(8))
        check_btn = dpg.add_button(
            label="Check readiness",
            tag="check_readiness_btn",
            width=scaled(160),
            height=scaled(30),
            callback=gui._on_check_readiness,
        )
        dpg.bind_item_theme(check_btn, gui._btn_standby_theme)
        with dpg.tooltip(check_btn):
            dpg.add_text("Run the Go-Live checks now (camera/FPS, TensorRT, OSC,\n"
                         "calibration age, disk, GPU temp). ~0.3 s; never blocks RUN.\n"
                         "Also runs automatically when you open this phase.")
        dpg.add_spacer(height=scaled(8))
        # Readiness rows render here on demand (gui.show_readiness_rows).
        with dpg.group(tag="readiness_rows_container"):
            dpg.add_text("Press 'Check readiness' (or open this phase) to run "
                         "the checks.", color=TEXT_HINT, wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(14))
        dpg.add_separator()
        dpg.add_spacer(height=scaled(6))
        dpg.add_text("Dry-run (optional)", color=TEXT_NORMAL)
        dpg.add_text("Replay the last recording through the current settings "
                     "for a quick track/drop sanity check. STANDBY only.",
                     color=TEXT_MUTED, wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(6))
        dryrun_btn = dpg.add_button(
            label="Dry-run last recording",
            tag="dryrun_btn",
            width=scaled(200),
            height=scaled(30),
            callback=gui._on_dryrun,
        )
        dpg.bind_item_theme(dryrun_btn, gui._btn_standby_theme)
        with dpg.tooltip(dryrun_btn):
            dpg.add_text("Offline replay of the newest recording with the saved\n"
                         "config (separate process - no effect on the live\n"
                         "pipeline/OSC). Shows tracks / swaps / drops. STANDBY only.")
        dpg.add_spacer(height=scaled(6))
        dpg.add_text("", tag="dryrun_result_text", color=TEXT_MUTED,
                     wrap=scaled(_PHASE_WRAP))


def build_phase_live(gui: Any):
    """⑥ Go Live — STANDBY/RUN + the live view toggles."""
    with dpg.group(tag="phase_panel_live", show=False):
        dpg.add_text("6 - Go Live", color=HEADING_GREEN)
        dpg.add_text("STANDBY = preview + enhancement only. RUN turns on full "
                     "YOLO inference + OSC output. Live, nudge a dial or two - "
                     "nothing more.",
                     color=TEXT_MUTED, wrap=scaled(_PHASE_WRAP))
        dpg.add_spacer(height=scaled(10))
        with dpg.group(horizontal=True):
            standby_btn = dpg.add_button(
                label="STANDBY",
                tag="state_standby_btn",
                width=scaled(110),
                height=scaled(30),
                callback=gui._on_state_standby,
            )
            dpg.bind_item_theme(standby_btn, gui._btn_standby_theme)
            with dpg.tooltip(standby_btn):
                dpg.add_text("STANDBY: Preview + enhancement, no YOLO, no OSC")
            dpg.add_spacer(width=scaled(6))
            run_btn = dpg.add_button(
                label="RUN",
                tag="state_run_btn",
                width=scaled(110),
                height=scaled(30),
                callback=gui._on_state_run,
            )
            dpg.bind_item_theme(run_btn, gui._btn_run_active_theme)
            with dpg.tooltip(run_btn):
                dpg.add_text("RUN: Full YOLO inference + OSC output")
        dpg.add_spacer(height=scaled(12))
        # --- Detection dials (OPERATOR_V2 §2.2) — the live levers the operator
        # nudges.  Both: 50 = calibrated seed, right = "catch more dancer".
        dpg.add_text("Detection", color=TEXT_NORMAL)
        dpg.add_text("Drops <-> Ghosts", color=TEXT_MUTED)
        with dpg.group(horizontal=True):
            sens_slider = dpg.add_slider_float(
                tag="sensitivity_slider",
                default_value=gui.config.get("sensitivity", 50.0),
                min_value=0.0,
                max_value=100.0,
                format="%.0f",
                width=scaled(-90),
                callback=gui._on_sensitivity_change,
            )
            _add_slider_row("sensitivity_slider", 5.0, 0.0, 100.0, gui._on_sensitivity_change)
        with dpg.tooltip(sens_slider):
            dpg.add_text("Dial A (confidence-led). 50 = calibrated.\nLosing the dancer? Raise it (catches more,\nmay add ghosts). Too many ghosts? Lower it\n(stricter). Calibration re-centers it at 50.")
        # Dial B lives in a hideable group: calibration hides it on the live
        # surface when the scene's drop-rate says gap-bridging is inert (it
        # stays reachable as the raw motion_sensitivity slider in Advanced, per
        # OPERATOR_V2 P3). gui.set_dial_b_visible() toggles this on project load.
        with dpg.group(tag="dial_b_group"):
            dpg.add_text("Gap bridging", color=TEXT_MUTED)
            with dpg.group(horizontal=True):
                bridge_slider = dpg.add_slider_float(
                    tag="gap_bridging_slider",
                    default_value=gui.config.get("gap_bridging", 50.0),
                    min_value=0.0,
                    max_value=100.0,
                    format="%.0f",
                    width=scaled(-90),
                    callback=gui._on_gap_bridging_change,
                )
                _add_slider_row("gap_bridging_slider", 5.0, 0.0, 100.0, gui._on_gap_bridging_change)
            with dpg.tooltip(bridge_slider):
                dpg.add_text("Dial B (gap bridging). 50 = calibrated.\nDancer dropping out during fast / aerial moves?\nRaise it to bridge YOLO gaps (monotonic\n'fewer drops'). Modest fine-tune; inert on\nclean scenes. Calibration re-centers it at 50.\nHidden when calibration finds it inert (raw\nslider stays in Advanced).")
        dpg.add_spacer(height=scaled(12))
        # --- Output controls (Track X) — OUTPUT-domain, distinct from the
        # detection dial above.  These shape what OSC/preview reports; they do
        # NOT change detection.  See docs/OSC_CONTRACT.md.
        dpg.add_text("Output", color=TEXT_NORMAL)
        with dpg.group(horizontal=True):
            smooth_slider = dpg.add_slider_int(
                tag="output_smoothing_slider",
                label="smooth L",
                default_value=int(gui.config.get("output_smoothing_l", 1)),
                min_value=1,
                max_value=6,
                width=scaled(-120),
                callback=gui._on_output_smoothing_change,
            )
        with dpg.tooltip(smooth_slider):
            dpg.add_text("Output smoothness vs latency — selects the single\n"
                         "/walldance/dancer/* stream.\n"
                         "L=1 = causal / live: zero look-ahead (default).\n"
                         "L>1 = fixed-lag RTS-smoothed stream, released L frames\n"
                         "late (smoother + retroactively gap-corrected), with\n"
                         "/walldance/meta/latency_ms published. Output-only.")
        dpg.add_text("output: live (L=1, 0 ms)", tag="lagged_latency_text", color=TEXT_DIM)
        dpg.add_spacer(height=scaled(6))
        build_identity_slot_controls(gui)
        dpg.add_spacer(height=scaled(12))
        # Remote ops API (REMOTE_OPS): operator-owned gate for control during RUN.
        dpg.add_text("Remote", color=TEXT_NORMAL)
        rc_chk = dpg.add_checkbox(label="Allow remote control during RUN",
                                  tag="remote_control_checkbox", default_value=False,
                                  callback=gui._on_remote_control_toggle)
        with dpg.tooltip(rc_chk):
            dpg.add_text("Off: a remote session can watch, pull files and act in\n"
                         "STANDBY, but not change anything while in RUN.\n"
                         "On: it may also record / switch state / nudge dials in RUN.\n"
                         "Heavy jobs (tunes, model loads) are never allowed in RUN.")
        dpg.add_text("remote: idle", tag="remote_status_text", color=TEXT_DIM)
        dpg.add_spacer(height=scaled(12))
        build_visualization_toolbar(gui)    # promoted: View S/K/B/T/I


def build_identity_slot_controls(gui: Any):
    """Phase-6 'Dancer IDs' block (CONT-6 identity slots): the on-site knobs.
    Output-only: they shape the OSC stream TouchDesigner gets, never detection."""
    cfg = gui.config
    dpg.add_text("Dancer IDs", color=TEXT_NORMAL)
    slots_chk = dpg.add_checkbox(
        label="Stable IDs (D1..Dn, hold through losses)",
        tag="identity_slots_checkbox",
        default_value=bool(cfg.get("identity_slots_enabled", IDENTITY_SLOTS_ENABLED)),
        callback=gui._on_identity_slots_toggle,
    )
    with dpg.tooltip(slots_chk):
        dpg.add_text("ON: OSC sends one stable id per dancer (1..Max dancers)\n"
                     "for the whole show. A dancer the tracker loses is held\n"
                     "(coasting) for 'Hold' seconds instead of vanishing, and\n"
                     "re-found under the SAME id. Extra tracks are not sent.\n"
                     "OFF: the raw tracker ids (they change often).")
    with dpg.group(horizontal=True):
        n_slider = dpg.add_slider_int(
            tag="max_dancers_slider", label="Max dancers",
            default_value=int(cfg.get("max_dancers", IDENTITY_SLOTS_MAX_DANCERS)),
            min_value=1, max_value=8, width=scaled(-150),
            callback=gui._on_max_dancers_change,
        )
    with dpg.tooltip(n_slider):
        dpg.add_text("How many dancers can be on stage at most.\n"
                     "Ids 1..N; never more ids than this.")
    with dpg.group(horizontal=True):
        st_slider = dpg.add_slider_float(
            tag="stability_slider", label="Stability",
            default_value=float(cfg.get("stability", IDENTITY_SLOTS_STABILITY)),
            min_value=0.0, max_value=3.0, format="%.2f", width=scaled(-150),
            callback=gui._on_stability_change,
        )
        _add_slider_row("stability_slider", 0.1, 0.0, 3.0, gui._on_stability_change)
    with dpg.tooltip(st_slider):
        dpg.add_text("Centroid smoothing (adaptive: calm at rest, quick on\n"
                     "fast moves). 0 = most responsive, 0.5 = default, 1 = calm.\n"
                     "Above 1: heavier smoothing, the point lags fast moves\n"
                     "(~70 ms at 1, ~110-170 ms at 2, 160-400 ms at 3).\n"
                     "Shaking point? Raise it. Point trails fast moves? Lower it.")
    with dpg.group(horizontal=True):
        coast_slider = dpg.add_slider_float(
            tag="coast_s_slider", label="Hold (s)",
            default_value=float(cfg.get("coast_s", IDENTITY_SLOTS_COAST_S)),
            min_value=0.0, max_value=10.0, format="%.1f", width=scaled(-150),
            callback=gui._on_coast_s_change,
        )
        _add_slider_row("coast_s_slider", 0.5, 0.0, 10.0, gui._on_coast_s_change)
    with dpg.tooltip(coast_slider):
        dpg.add_text("How long a lost dancer keeps its id (held at its last\n"
                     "position) before it disappears from OSC. Up to 10 s.\n"
                     "Video drops in TD when a dancer vanishes? Raise it.\n"
                     "Ids linger after a dancer leaves? Lower it.\n"
                     "With 'Smart hold' ON (default) only a well-tracked dancer\n"
                     "gets the full Hold; exits across the border are released\n"
                     "after 0.3 s, so a long Hold does not keep ghosts alive.")
    with dpg.group(horizontal=True):
        belt_chk = dpg.add_checkbox(
            label="IR belt", tag="ir_belt_checkbox",
            default_value=bool(cfg.get("use_ir_belt", IDENTITY_SLOTS_USE_IR_BELT)),
            callback=gui._on_ir_belt_toggle,
        )
        with dpg.tooltip(belt_chk):
            dpg.add_text("Use the retroreflective waist belt to keep a dancer\n"
                         "alive when the pose model loses them (needs the belt\n"
                         "detector module; does nothing without it).")
        dpg.add_spacer(width=scaled(8))
        state_chk = dpg.add_checkbox(
            label="Send /dancer/state", tag="osc_state_checkbox",
            default_value=bool(cfg.get("osc_send_state", OSC_SEND_STATE)),
            callback=gui._on_osc_state_toggle,
        )
        with dpg.tooltip(state_chk):
            dpg.add_text("Opt-in OSC /walldance/dancer/state [id, state, age_s]\n"
                         "(live / belt / coasting / lost) for every slot.")
    hold_chk = dpg.add_checkbox(
        label="Smart hold (earned, none at exits)", tag="smart_hold_checkbox",
        default_value=bool(cfg.get("smart_hold", IDENTITY_SLOTS_SMART_HOLD)),
        callback=gui._on_smart_hold_toggle,
    )
    with dpg.tooltip(hold_chk):
        dpg.add_text("ON: a dancer gets the full 'Hold' only after ~1 s of solid\n"
                     "tracking (skeleton seen + some movement); a new, weak or\n"
                     "static point is held 1.5 s at most; a dancer leaving across\n"
                     "the Region of Interest border is released after 0.3 s.\n"
                     "Leave it ON, especially with a long Hold.")
    guard_chk = dpg.add_checkbox(
        label="Ignore static figures", tag="static_guard_checkbox",
        default_value=bool(cfg.get("static_ghost_guard", IDENTITY_SLOTS_STATIC_GUARD)),
        callback=gui._on_static_guard_toggle,
    )
    with dpg.tooltip(guard_chk):
        dpg.add_text("A person-like shape that never moves (a coat, a poster, a\n"
                     "stain) cannot keep a dancer id when a moving dancer needs it,\n"
                     "and cannot take one back. Leave it ON.")
    clamp_chk = dpg.add_checkbox(
        label="Box clamp (stable size during gaps)",
        tag="box_clamp_checkbox",
        default_value=bool(gui.config.get("box_clamp_enabled", True)),
        callback=gui._on_box_clamp_toggle,
    )
    with dpg.tooltip(clamp_chk):
        dpg.add_text("While a dancer is bridged (no fresh skeleton), report a box of\n"
                     "the last YOLO size at the smoothed centroid.  It does not move\n"
                     "the centroid; it keeps the dancer size the ids rely on (search\n"
                     "gates, smoothing speed) steady through detection gaps. Leave ON.")
    with dpg.group(horizontal=True):
        fg_chk = dpg.add_checkbox(
            label="Use empty wall", tag="foreground_checkbox",
            default_value=bool(cfg.get("fg_enabled", FOREGROUND_ENABLED)),
            callback=gui._on_foreground_toggle,
        )
        with dpg.tooltip(fg_chk):
            dpg.add_text("Compares each frame with a snapshot of the EMPTY wall:\n"
                         "a shape that is not on the snapshot is somebody.  Keeps a\n"
                         "dancer's point when YOLO loses them (state 'fg'), stops\n"
                         "fixed figures (coat, stain) from taking an id, and lets the\n"
                         "belt hold a still dancer.  Calibrate records the snapshot;\n"
                         "re-capture after moving the camera or changing the lights.")
        cap_btn = dpg.add_button(label="Capture empty wall", callback=gui._on_capture_plate)
        with dpg.tooltip(cap_btn):
            dpg.add_text("Records 2 s of the wall: NOBODY in the picture.\n"
                         "(Calibrate does it automatically.)")
    dpg.add_text("empty wall: -", tag="fg_status_text", color=TEXT_DIM)
    dpg.add_text("ids: -", tag="identity_slots_status_text", color=TEXT_DIM)


# --------------------------------------------------------------------------- #
# Drawer bar + floating drawers (Advanced / Recordings)
# --------------------------------------------------------------------------- #
def build_alerts_strip(gui: Any):
    """One-line alerts strip (OPERATOR_V2 §2.3c) — warnings surface here:
    GPU temp, TRT fallback, OSC down, config-vs-scene mismatch, height-stale.

    The Track-C feeders that push these are gated (not batch 1); the strip + the
    gui.push_alert/clear_alert API exist now so those fixes have a home."""
    with dpg.group(horizontal=True):
        dpg.add_text("Alerts:", color=TEXT_DIM)
        dpg.add_text("(none)", tag="alerts_text", color=TEXT_HINT)


def build_drawer_bar(gui: Any):
    """Recordings bar — LIVE/REC + 9 slots + IMPORT + status + transport, inline on ONE
    line, always visible (no toggle button).  (Advanced is on the phase rail.)"""
    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_text("Recordings", color=HEADING_GREEN)
        dpg.add_spacer(width=scaled(6))
        build_recordings_content(gui)


def build_advanced_drawer(gui: Any):
    """Floating Advanced panel: today's numeric section stack, verbatim."""
    with dpg.window(label="Advanced  (numeric knobs)", tag="advanced_drawer_window",
                    show=False, no_collapse=True,
                    width=scaled(CONTROL_PANEL_WIDTH + 26), height=scaled(680),
                    pos=(scaled(40), scaled(90))):
        build_control_panel(gui)


def build_recordings_content(gui: Any):
    """Recordings controls laid out inline on the recordings bar's single line:
    LIVE/REC + 9 slots + IMPORT + dynamic status + playback transport.  Emitted directly
    into the caller's horizontal group (no wrapper) so it sits on one line; all
    widget tags are preserved so gui.update_recording_ui drives them unchanged."""
    # LIVE / REC + slot buttons (RECORDING_SLOTS = VideoRecorder.NUM_SLOTS)
    live_btn = dpg.add_button(
        label="LIVE",
        tag="rec_live_btn",
        width=scaled(45),
        callback=gui._on_rec_live,
    )
    dpg.bind_item_theme(live_btn, gui._rec_live_active_theme)
    rec_btn = dpg.add_button(
        label="REC",
        tag="rec_rec_btn",
        width=scaled(45),
        callback=gui._on_rec_toggle,
    )
    dpg.bind_item_theme(rec_btn, gui._rec_btn_theme)
    dpg.add_spacer(width=scaled(4))
    for slot in range(1, RECORDING_SLOTS + 1):
        slot_btn = dpg.add_button(
            label=str(slot),
            tag=f"rec_slot_{slot}_btn",
            width=scaled(23),
            callback=lambda s, a, u: gui._on_rec_slot_click(u),
            user_data=slot,
        )
        dpg.bind_item_theme(slot_btn, gui._slot_empty_theme)

    # REQ-1: load an external video file into a slot (slot picker -> file dialog).
    dpg.add_spacer(width=scaled(4))
    dpg.add_button(
        label="IMPORT",
        tag="rec_import_btn",
        width=scaled(58),
        callback=gui._on_import_video,
    )
    with dpg.tooltip("rec_import_btn"):
        dpg.add_text("Load a video file (.avi .mp4 .mov .mkv ...) into a slot.\n"
                     "It becomes the slot's newest take; older takes stay\n"
                     "in the slot's Ctrl+click history.")

    dpg.add_spacer(width=scaled(10))

    # Dynamic status / playback transport — same line, to the right of the slots.
    with dpg.group(horizontal=True, tag="source_status_group"):
        dpg.add_text("", tag="rec_status_text", color=(80, 200, 80))
        dpg.add_text("", tag="rec_frame_counter", color=(255, 100, 100))
    with dpg.group(horizontal=True, tag="source_playback_group", show=False):
        dpg.add_text("", tag="rec_playback_progress", color=(100, 180, 220))
        dpg.add_combo(
            items=["x0.1", "x0.25", "x0.5", "x0.75", "x1.0", "x1.5", "x2.0", "x4.0"],
            tag="rec_speed_combo",
            default_value="x1.0",
            width=scaled(65),
            callback=gui._on_playback_speed_change,
        )
        pause_btn = dpg.add_button(
            label=Icons.PAUSE,
            tag="rec_pause_btn",
            width=scaled(24),
            callback=gui._on_playback_pause,
        )
        if gui._icon_font:
            dpg.bind_item_font(pause_btn, gui._icon_font)
        prev_btn = dpg.add_button(
            label=Icons.STEP_BACKWARD,
            tag="rec_prev_frame_btn",
            width=scaled(24),
            callback=gui._on_playback_prev_frame,
        )
        if gui._icon_font:
            dpg.bind_item_font(prev_btn, gui._icon_font)
        next_btn = dpg.add_button(
            label=Icons.STEP_FORWARD,
            tag="rec_next_frame_btn",
            width=scaled(24),
            callback=gui._on_playback_next_frame,
        )
        if gui._icon_font:
            dpg.bind_item_font(next_btn, gui._icon_font)
        dpg.add_button(
            label="ISSUE",
            tag="rec_report_issue_btn",
            width=scaled(52),
            callback=gui._on_report_issue,
        )


def build_detection_section(gui: Any):
    """Detection settings - person height, confidence, max dancers."""
    with dpg.collapsing_header(label="Detection", default_open=False, tag="section_detection"):
        # Person Height (manual + calibrated via DANCERS)
        dpg.add_text("Person Height", color=TEXT_NORMAL)
        with dpg.group(horizontal=True):
            height_slider = dpg.add_slider_int(
                tag="person_height_slider",
                default_value=gui.config.get("person_height_px", 200),
                min_value=20,
                max_value=800,
                format="%d px",
                width=scaled(90),
                callback=gui._on_person_height_change,
            )
            _add_slider_row("person_height_slider", 5, 20, 800, gui._on_person_height_change)
        with dpg.tooltip(height_slider):
            dpg.add_text("Expected dancer height in pixels at current\ncamera distance. All tracking thresholds\nscale from this value. Set by the DANCERS\ncalibration; adjust manually if needed.")

        dpg.add_spacer(height=scaled(6))

        # Detection Sensitivity (the one operator live dial) is surfaced on the
        # live surface -- phase 6 LIVE -- not here in the Advanced drawer.

        # Expert-only: the raw knobs behind the macro + tier-3 tracker params.
        with dpg.group(tag="detection_expert_group", show=gui.expert_mode):
            dpg.add_spacer(height=scaled(6))
            dpg.add_text("Detection Confidence (raw)", color=TEXT_NORMAL)
            with dpg.group(horizontal=True):
                conf_slider = dpg.add_slider_float(
                    tag="show_conf_slider",
                    default_value=gui.config.get("confidence", 0.25),
                    min_value=0.1,
                    max_value=0.9,
                    format="%.2f",
                    width=scaled(-90),
                    callback=gui._on_confidence_change,
                )
                _add_slider_row("show_conf_slider", 0.01, 0.1, 0.9, gui._on_confidence_change)
            with dpg.tooltip(conf_slider):
                dpg.add_text("Raw YOLO confidence. Moving this re-anchors\nthe sensitivity macro at 50.")

            dpg.add_spacer(height=scaled(6))
            dpg.add_text("Tracker Max Age (frames)", color=TEXT_NORMAL)
            with dpg.group(horizontal=True):
                age_slider = dpg.add_slider_int(
                    tag="tracker_age_slider",
                    default_value=gui.config.get("tracker_max_age", 20),
                    min_value=5,
                    max_value=60,
                    width=scaled(-90),
                    callback=gui._on_tracker_age_change,
                )
                _add_slider_row("tracker_age_slider", 1, 5, 60, gui._on_tracker_age_change)
            with dpg.tooltip(age_slider):
                dpg.add_text("How long (in frames) to remember a dancer\nwho disappears. Higher = keeps ID longer\nduring occlusions but slower to drop\nstale tracks. 30-45 is a good default.")

            dpg.add_spacer(height=scaled(6))
            dpg.add_text("Motion Bridge Resolution", color=TEXT_NORMAL)
            with dpg.group(horizontal=True):
                mog2_slider = dpg.add_slider_float(
                    tag="mog2_scale_slider",
                    default_value=gui.config.get("mog2_scale", 0.75),
                    min_value=0.25,
                    max_value=1.0,
                    format="%.2f",
                    width=scaled(-90),
                    callback=gui._on_mog2_scale_change,
                )
                _add_slider_row("mog2_scale_slider", 0.05, 0.25, 1.0, gui._on_mog2_scale_change)
            with dpg.tooltip(mog2_slider):
                dpg.add_text("MOG2 background subtraction resolution.\nSet by the scene calibration (joint sweep\nwith varThreshold). 0.50 = fastest,\n1.00 = best blob accuracy.")

            dpg.add_spacer(height=scaled(6))
            dpg.add_text("Motion Sensitivity (raw, Dial B)", color=TEXT_NORMAL)
            with dpg.group(horizontal=True):
                motion_slider = dpg.add_slider_float(
                    tag="motion_sensitivity_slider",
                    default_value=gui.config.get("motion_sensitivity", 0.55),
                    min_value=0.0,
                    max_value=1.0,
                    format="%.2f",
                    width=scaled(-90),
                    callback=gui._on_motion_sensitivity_change,
                )
                _add_slider_row("motion_sensitivity_slider", 0.05, 0.0, 1.0, gui._on_motion_sensitivity_change)
            with dpg.tooltip(motion_slider):
                dpg.add_text("Raw bridge recovery value behind the live\n'Gap bridging' dial (Dial B). Higher = smaller/\nweaker motion keeps a track alive when YOLO\ndrops. Moving this re-anchors the Gap-bridging\ndial at 50 (with a toast).")


def build_visualization_toolbar(gui: Any):
    """Compact icon-based visualization toggles."""
    with dpg.group(horizontal=True):
        dpg.add_text("View:", color=HEADING_GREEN)
        dpg.add_spacer(width=scaled(5))
        
        # Skeleton toggle
        skel_btn = dpg.add_button(
            label="S",
            tag="vis_skeleton_btn",
            width=scaled(28),
            callback=lambda: gui._on_vis_toolbar_toggle("skeleton"),
        )
        dpg.bind_item_theme(skel_btn, gui._vis_btn_on_theme if gui.config.get("show_skeleton", True) else gui._vis_btn_off_theme)
        with dpg.tooltip(skel_btn):
            dpg.add_text("Skeleton [S]")
        
        # Keypoints toggle  
        kp_btn = dpg.add_button(
            label="K",
            tag="vis_keypoints_btn",
            width=scaled(28),
            callback=lambda: gui._on_vis_toolbar_toggle("keypoints"),
        )
        dpg.bind_item_theme(kp_btn, gui._vis_btn_on_theme if gui.config.get("show_keypoints", False) else gui._vis_btn_off_theme)
        with dpg.tooltip(kp_btn):
            dpg.add_text("Keypoints [K]")
        
        # Bounding box toggle
        bbox_btn = dpg.add_button(
            label="B",
            tag="vis_bbox_btn",
            width=scaled(28),
            callback=lambda: gui._on_vis_toolbar_toggle("bbox"),
        )
        dpg.bind_item_theme(bbox_btn, gui._vis_btn_on_theme if gui.config.get("show_bbox", True) else gui._vis_btn_off_theme)
        with dpg.tooltip(bbox_btn):
            dpg.add_text("Bounding Box [B]")
        
        # Trails toggle
        trails_btn = dpg.add_button(
            label="T",
            tag="vis_trails_btn",
            width=scaled(28),
            callback=lambda: gui._on_vis_toolbar_toggle("trails"),
        )
        dpg.bind_item_theme(trails_btn, gui._vis_btn_on_theme if gui.config.get("show_trails", False) else gui._vis_btn_off_theme)
        with dpg.tooltip(trails_btn):
            dpg.add_text("Motion Trails [T]")
        
        # IDs toggle
        ids_btn = dpg.add_button(
            label="I",
            tag="vis_ids_btn",
            width=scaled(28),
            callback=lambda: gui._on_vis_toolbar_toggle("ids"),
        )
        dpg.bind_item_theme(ids_btn, gui._vis_btn_on_theme if gui.config.get("show_ids", True) else gui._vis_btn_off_theme)
        with dpg.tooltip(ids_btn):
            dpg.add_text("Dancer IDs [I]")

        # Output ball toggle (the emitted OSC centroid)
        ball_btn = dpg.add_button(
            label="C",
            tag="vis_ball_btn",
            width=scaled(28),
            callback=lambda: gui._on_vis_toolbar_toggle("ball"),
        )
        dpg.bind_item_theme(ball_btn, gui._vis_btn_on_theme if gui.config.get("show_ball", True) else gui._vis_btn_off_theme)
        with dpg.tooltip(ball_btn):
            dpg.add_text("Circle ball = what OSC sends (/centroid), sized by the dancer's distance [C]")


def build_osc_section(gui: Any):
    """OSC output settings - open by default."""
    with dpg.collapsing_header(label="OSC", default_open=False, tag="section_osc", closable=False):
        with dpg.group(horizontal=True):
            osc_chk = dpg.add_checkbox(
                label="Enable OSC",
                tag="osc_checkbox",
                default_value=gui.config.get("osc_enabled", True),
                callback=gui._on_osc_toggle,
            )
            with dpg.tooltip(osc_chk):
                dpg.add_text("Enable/disable OSC output")
        
        dpg.add_spacer(height=scaled(4))
        dpg.add_text("Target Address", color=TEXT_NORMAL)
        with dpg.group(horizontal=True):
            dpg.add_input_text(
                tag="osc_ip_input",
                default_value=gui.config.get("osc_ip", "127.0.0.1"),
                width=scaled(140),
                callback=gui._on_osc_config_change,
            )
            dpg.add_text(":", color=TEXT_MUTED)
            dpg.add_input_int(
                tag="osc_port_input",
                default_value=gui.config.get("osc_port", 9000),
                min_value=1024,
                max_value=65535,
                step=0,
                width=scaled(70),
                callback=gui._on_osc_config_change,
            )


def build_model_section(gui: Any):
    """Model settings - open by default."""
    with dpg.collapsing_header(label="Model", default_open=False, tag="section_model", closable=False):
        dpg.add_text("YOLO Model", color=TEXT_NORMAL)
        with dpg.group(horizontal=True):
            dpg.add_combo(
                items=[
                    "yolo11n-pose", "yolo11s-pose", "yolo11m-pose", 
                    "yolo11l-pose", "yolo11x-pose",
                ],
                tag="adv_model_combo",
                default_value=gui.config.get("model", "yolo11m-pose"),
                width=scaled(140),
                callback=gui._on_model_change,
            )
        
        dpg.add_spacer(height=scaled(4))
        dpg.add_text("Image Size", color=TEXT_NORMAL)
        with dpg.group(horizontal=True):
            dpg.add_combo(
                items=["640", "800", "960", "1280", "1536", "1920"],
                tag="adv_imgsz_combo",
                default_value=str(gui.config.get("yolo_imgsz", 640)),
                width=scaled(80),
                callback=gui._on_imgsz_change,
            )
            dpg.add_text("TensorRT:")
            dpg.add_checkbox(
                tag="adv_trt_checkbox",
                default_value=gui.config.get("use_tensorrt", False),
                callback=gui._on_trt_toggle,
            )


def build_enhancement_section(gui: Any):
    """Enhancement settings - open by default."""
    with dpg.collapsing_header(label="Enhancement", default_open=False, tag="section_enhancement", closable=False):
        with dpg.group(horizontal=True):
            enable_chk = dpg.add_checkbox(
                label="Enable",
                tag="adv_enhance_checkbox",
                default_value=gui.config.get("enhance_enabled", False),
                callback=gui._on_enhance_toggle,
            )
            grey_chk = dpg.add_checkbox(
                label="Greyscale",
                tag="adv_greyscale_checkbox",
                default_value=gui.config.get("greyscale", False),
                callback=gui._on_greyscale_toggle,
            )
        with dpg.tooltip(enable_chk):
            dpg.add_text("A/B toggle: enhancement (gamma + CLAHE)\nis always applied when enabled.")
        with dpg.tooltip(grey_chk):
            dpg.add_text("Process in greyscale. Matches the IR camera;\nalso useful to simulate it on color footage.")

        # Expert-only: legacy brightness-gated auto-enhance controls.
        with dpg.group(tag="enhance_expert_group", show=gui.expert_mode):
            dpg.add_checkbox(
                label="Force (skip brightness gate)",
                tag="adv_enhance_force_checkbox",
                default_value=gui.config.get("enhance_force", True),
                callback=gui._on_enhance_force_toggle,
            )
            dpg.add_text("Brightness Threshold", color=TEXT_NORMAL)
            with dpg.group(horizontal=True):
                dpg.add_slider_int(
                    tag="adv_brightness_threshold_slider",
                    default_value=gui.config.get("brightness_threshold", 60),
                    min_value=0,
                    max_value=255,
                    width=scaled(-90),
                    callback=gui._on_brightness_threshold_change,
                )
                _add_slider_row("adv_brightness_threshold_slider", 5, 0, 255, gui._on_brightness_threshold_change)

        dpg.add_text("CLAHE Clip", color=TEXT_NORMAL)
        with dpg.group(horizontal=True):
            dpg.add_slider_float(
                tag="adv_clahe_slider",
                default_value=gui.config.get("clahe_clip", 3.0),
                min_value=1.0,
                max_value=6.0,
                format="%.1f",
                width=scaled(-90),
                callback=gui._on_clahe_change,
            )
            _add_slider_row("adv_clahe_slider", 0.1, 1.0, 6.0, gui._on_clahe_change)
        
        dpg.add_text("Gamma", color=TEXT_NORMAL)
        with dpg.group(horizontal=True):
            dpg.add_slider_float(
                tag="adv_gamma_slider",
                default_value=gui.config.get("gamma", 1.2),
                min_value=0.5,
                max_value=2.5,
                format="%.2f",
                width=scaled(-90),
                callback=gui._on_gamma_change,
            )
            _add_slider_row("adv_gamma_slider", 0.05, 0.5, 2.5, gui._on_gamma_change)


def build_background_section(gui: Any):
    """Background subtraction settings (expert-only; superseded by MOG2 + calibration)."""
    with dpg.collapsing_header(label="Background", default_open=False, tag="section_background", closable=False, show=gui.expert_mode):
        # Row 1: Capture / Enable / Clear buttons
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Capture",
                tag="bg_capture_btn",
                width=scaled(80),
                callback=gui._on_bg_capture,
            )
            dpg.add_checkbox(
                label="Enable",
                tag="bg_enable_checkbox",
                default_value=gui.config.get("bg_subtract_enabled", False),
                callback=gui._on_bg_enable_toggle,
            )
            dpg.add_button(
                label="Clear",
                tag="bg_clear_btn",
                width=scaled(50),
                callback=gui._on_bg_clear,
            )
        
        # Status / mismatch warning line
        dpg.add_text("No reference captured", tag="bg_status_text", color=TEXT_DIM)
        
        # Sensitivity slider
        dpg.add_text("Sensitivity", color=TEXT_NORMAL)
        with dpg.group(horizontal=True):
            dpg.add_slider_int(
                tag="bg_sensitivity_slider",
                default_value=gui.config.get("bg_subtract_sensitivity", 30),
                min_value=5,
                max_value=100,
                width=scaled(-90),
                callback=gui._on_bg_sensitivity_change,
            )
            _add_slider_row("bg_sensitivity_slider", 5, 5, 100, gui._on_bg_sensitivity_change)


def build_input_section(gui: Any):
    """Input settings - open by default."""
    with dpg.collapsing_header(label="Input", default_open=True, tag="section_input", closable=False):
        dpg.add_text("Camera", color=TEXT_NORMAL)
        with dpg.group(horizontal=True):
            dpg.add_combo(
                items=gui.config.get("camera_sources", ["0"]),
                tag="adv_camera_combo",
                default_value=gui.config.get("camera_source", "0"),
                width=scaled(160),
                callback=gui._on_camera_change,
            )
            refresh_btn = dpg.add_button(
                label=Icons.ROTATE,
                tag="adv_camera_refresh_btn",
                width=scaled(25),
                callback=gui._on_camera_refresh,
            )
            if gui._icon_font:
                dpg.bind_item_font(refresh_btn, gui._icon_font)
            settings_btn = dpg.add_button(
                label=Icons.GEAR,
                tag="ids_settings_toggle_btn",
                width=scaled(25),
                callback=gui._on_ids_settings_toggle,
                show=(gui.config.get("camera_type", "") == "IDS_PEAK"),
            )
            if gui._icon_font:
                dpg.bind_item_font(settings_btn, gui._icon_font)

        # REQ-5: mirror / rotate the image where it enters the app (camera AND
        # slot playback), so ROI, mask and calibration live in that space.
        dpg.add_spacer(height=scaled(4))
        with dpg.group(horizontal=True, tag="input_transform_group"):
            dpg.add_checkbox(
                label="Mirror",
                tag="input_mirror_checkbox",
                default_value=bool(gui.config.get("input_mirror", False)),
                callback=gui._on_input_mirror_toggle,
            )
            dpg.add_spacer(width=scaled(10))
            dpg.add_text("Rotate", color=TEXT_NORMAL)
            dpg.add_combo(
                items=list(INPUT_ROTATION_LABELS),
                tag="input_rotation_combo",
                default_value=rotation_label(gui.config.get("input_rotation", 0)),
                width=scaled(70),
                callback=gui._on_input_rotation_change,
            )
        with dpg.tooltip("input_transform_group"):
            dpg.add_text("Mirror = swap left/right. Rotate = clockwise.\n"
                         "Applied to the live camera AND to slot playback,\n"
                         "before ROI / mask / detection. Changing it moves the\n"
                         "ROI and mask with the image. Saved with the project\n"
                         "(Save). Recordings stay raw: playback re-applies it.")

        dpg.add_spacer(height=scaled(4))
        dpg.add_checkbox(
            label="Cap Input 20 FPS",
            tag="adv_input_fps_cap_checkbox",
            default_value=gui.config.get("input_fps_cap", False),
            callback=gui._on_input_fps_cap_toggle,
        )

        # --- IDS-specific controls (hidden when using standard webcam) ---
        is_ids = gui.config.get("camera_type", "") == "IDS_PEAK"
        with dpg.group(tag="ids_sliders_group", show=is_ids):
            dpg.add_spacer(height=scaled(4))
            dpg.add_text("IDS Crop Ratio (W/H)", color=TEXT_NORMAL)
            with dpg.group(horizontal=True):
                dpg.add_slider_float(
                    tag="adv_ids_ratio_slider",
                    default_value=gui.config.get("ids_ratio", 1.0),
                    min_value=0.5,
                    max_value=2.0,
                    format="%.2f",
                    width=scaled(-90),
                    callback=gui._on_ids_ratio_change,
                )
                _add_slider_row("adv_ids_ratio_slider", 0.05, 0.5, 2.0, gui._on_ids_ratio_change)

        # --- IDS hardware settings (gain/exposure) — toggled by gear button ---
        with dpg.group(tag="ids_hw_settings_group", show=False):
            dpg.add_spacer(height=scaled(4))
            dpg.add_text("IDS Gain (dB)", color=TEXT_NORMAL)
            with dpg.group(horizontal=True):
                dpg.add_slider_float(
                    tag="adv_ids_gain_slider",
                    default_value=gui.config.get("ids_gain_db", 0.0),
                    min_value=0.0,
                    max_value=48.0,
                    format="%.1f",
                    width=scaled(-90),
                    callback=gui._on_ids_gain_change,
                )
                _add_slider_row("adv_ids_gain_slider", 0.5, 0.0, 48.0, gui._on_ids_gain_change)

            dpg.add_spacer(height=scaled(4))
            dpg.add_text("IDS Exposure (\u00b5s)", color=TEXT_NORMAL)
            ids_exposure_max = float(gui.config.get("ids_exposure_max_us", 100000.0))
            with dpg.group(horizontal=True):
                dpg.add_slider_float(
                    tag="adv_ids_exposure_slider",
                    default_value=gui.config.get("ids_exposure_us", 10000.0),
                    min_value=100.0,
                    max_value=ids_exposure_max,
                    format="%.0f",
                    width=scaled(-90),
                    callback=gui._on_ids_exposure_change,
                )
                _add_slider_row("adv_ids_exposure_slider", 500.0, 100.0, ids_exposure_max, gui._on_ids_exposure_change)

        # Exposure warning — always visible (outside collapsible group)
        dpg.add_text(
            "",
            tag="adv_ids_exposure_warning",
            color=WARN_ORANGE,
            show=False,
        )


def build_preview_section(gui: Any):
    """Preview settings."""
    with dpg.collapsing_header(label="Preview", default_open=False, tag="section_preview", closable=False):
        with dpg.group(horizontal=True):
            dpg.add_checkbox(
                label="Enable Preview",
                tag="adv_preview_checkbox",
                default_value=gui.config.get("preview_enabled", True),
                callback=gui._on_preview_toggle,
            )
            dpg.add_checkbox(
                label="Cap FPS",
                tag="adv_preview_cap_checkbox",
                default_value=gui.config.get("preview_fps_cap", True),
                callback=gui._on_preview_cap_toggle,
            )
        dpg.add_spacer(height=scaled(4))
        with dpg.group(horizontal=True):
            dpg.add_text("Auto-fit scale:", color=TEXT_DIM)
            dpg.add_text("--", tag="preview_autofit_scale_text", color=PALE_GREEN)


def build_exclusion_mask_section(gui: Any):
    """Exclusion mask status + manual editor controls (OPERATOR_V2 decision 5).

    The mask is **manual-only** — Aim/Calibrate no longer auto-build or activate
    one. This section shows the active cell count and opens the preview cell
    editor so the operator can paint known dead zones (bystander strip,
    reflective wall, static ghosts). Masked cells stay dimmed on the preview at
    all times and survive recalibration.
    """
    with dpg.collapsing_header(label="Exclusion Mask", default_open=True,
                               tag="section_exclusion_mask", closable=False):
        with dpg.group(horizontal=True):
            dpg.add_text("Masked:", color=TEXT_DIM)
            dpg.add_text("0 cell(s)", tag="mask_cells_text", color=TEXT_MUTED)
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Edit",
                tag="mask_edit_btn",
                callback=gui._on_mask_edit_toggle,
                width=scaled(60),
            )
            dpg.add_button(
                label="Clear all",
                tag="mask_clear_btn",
                callback=gui._on_mask_clear,
                width=scaled(80),
            )
        dpg.add_text(
            "Manual only — paint what you know is dead. Edit: click/drag preview "
            "cells to mask (red) / unmask. Masked cells stay dimmed at all times "
            "and survive recalibration.",
            color=TEXT_DIM,
            wrap=scaled(300),
        )


def build_roi_section(gui: Any):
    """Region of interest settings."""
    with dpg.collapsing_header(label="Region of Interest", default_open=True, tag="section_roi", closable=False):
        with dpg.group(horizontal=True):
            dpg.add_checkbox(
                label="Enable ROI",
                tag="adv_roi_enable_checkbox",
                default_value=gui.config.get("roi_enabled", False),
                callback=gui._on_roi_toggle,
            )
            dpg.add_button(
                label="Reset",
                tag="adv_roi_reset_btn",
                callback=gui._on_roi_reset,
                width=scaled(60),
            )
        roi_x = gui.config.get("roi_x", 0)
        roi_y = gui.config.get("roi_y", 0)
        roi_w = gui.config.get("roi_w", gui.config.get("camera_width", 1920))
        roi_h = gui.config.get("roi_h", gui.config.get("camera_height", 1080))
        dpg.add_text(
            f"{roi_x},{roi_y}  {roi_w}x{roi_h}",
            tag="roi_rect_text",
            color=TEXT_MUTED,
        )
        dpg.add_text(
            "Double-click the preview to toggle edit mode, then drag to draw, move, or resize.",
            color=TEXT_DIM,
            wrap=scaled(300),
        )