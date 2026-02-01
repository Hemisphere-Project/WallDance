"""
Layout and theme helpers for the DearPyGui interface.
The functions here are called from `gui.WallDanceGUI` to keep that class lean.

Phase 2 reorganization:
- Live Controls: Standby/Live/Pause buttons, camera/OSC status (always visible, prominent)
- Show Settings: Person height, max dancers, confidence, OSC target (per-venue, visible)
- Advanced: Tracker, enhancement, model selection (collapsed by default)
"""

import os
from enum import Enum, auto
from typing import Any, Tuple

import dearpygui.dearpygui as dpg
import numpy as np

from gui_icons import Icons


class SystemState(Enum):
    """System operational states for show control.
    
    Simplified 2-state system:
    - STANDBY: Preview + enhancement, no YOLO, no OSC
    - RUN: Full YOLO inference + OSC output
    """
    STANDBY = auto()  # Preview only, no YOLO processing, no OSC
    RUN = auto()      # Full pipeline: YOLO + tracking + OSC


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


def scaled(value: int) -> int:
    """Scale a pixel value by the DPI factor."""
    return int(value * _dpi_scale)


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
    
    # State badge themes (for SETUP/STANDBY/LIVE/PAUSED/ERROR)
    with dpg.theme() as gui._state_setup_theme:
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (200, 200, 200, 255))
    
    with dpg.theme() as gui._state_standby_theme:
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 220, 100, 255))
    
    with dpg.theme() as gui._state_live_theme:
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (100, 255, 100, 255))
    
    with dpg.theme() as gui._state_paused_theme:
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (100, 180, 255, 255))
    
    with dpg.theme() as gui._state_error_theme:
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 100, 100, 255))

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
    """Build the main UI layout."""
    with dpg.window(tag="main_window", label="WallDance Control Panel"):
        build_top_bar(gui)
        dpg.add_spacer(height=1)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=scaled(6))  # Left padding
            build_video_panel(gui)
            build_control_panel(gui)
            dpg.add_spacer(width=scaled(6))  # Right padding to match left


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
                dpg.add_text("Project:", color=(120, 200, 140))
                dpg.add_combo(
                    items=["+ New..."],
                    tag="topbar_project_combo",
                    default_value="",
                    width=scaled(150),
                    callback=gui._on_topbar_project_change,
                )
                dpg.add_spacer(width=scaled(15))
                dpg.add_text("Version:", color=(120, 200, 140))
                dpg.add_combo(
                    items=[],
                    tag="topbar_config_combo",
                    default_value="",
                    width=scaled(180),
                    callback=gui._on_topbar_config_change,
                )
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
                
                safe_btn = dpg.add_button(
                    label=Icons.ROTATE,
                    tag="topbar_safe_btn",
                    width=scaled(20),
                    height=scaled(20),
                    callback=gui._on_safe_defaults,
                )
                if gui._icon_font:
                    dpg.bind_item_font(safe_btn, gui._icon_font)
                with dpg.tooltip(safe_btn):
                    dpg.add_text("Click: Load safe defaults\nCtrl+click: Save as safe defaults")
                
                save_ind = dpg.add_text(Icons.CHECK, tag="save_indicator", color=(100, 255, 100), show=False)
                if gui._icon_font:
                    dpg.bind_item_font(save_ind, gui._icon_font)
            with dpg.group(horizontal=True):
                # System state badge (prominent)
                state_badge = dpg.add_text("RUN", tag="state_badge", color=(100, 255, 100, 255))
                dpg.bind_item_theme(state_badge, gui._state_live_theme)
                with dpg.tooltip(state_badge):
                    dpg.add_text("System state:\n• STANDBY: Preview only, no YOLO\n• RUN: Full YOLO + OSC output")
                dpg.add_spacer(width=scaled(12))
                dpg.add_text("|", color=(80, 80, 80))
                dpg.add_spacer(width=scaled(8))
                dpg.add_text("CAM:", color=(180, 180, 180))
                cam_badge = dpg.add_text("OFF", tag="badge_cam", color=(255, 120, 120))
                with dpg.tooltip(cam_badge):
                    dpg.add_text("Camera status: ON (green) or OFF (red)")
                dpg.add_spacer(width=scaled(6))
                dpg.add_text("OSC:", color=(180, 180, 180))
                osc_badge = dpg.add_text("OFF", tag="badge_osc", color=(255, 120, 120))
                with dpg.tooltip(osc_badge):
                    dpg.add_text("OSC output status: ON (green) or OFF (red)")
                dpg.add_spacer(width=scaled(6))
                dpg.add_text("Model:", color=(180, 180, 180))
                dpg.add_text("--", tag="badge_model", color=(150, 200, 255))
                dpg.add_spacer(width=scaled(3))
                engine_badge = dpg.add_text("[PT]", tag="badge_engine_type", color=(255, 220, 100))  # Yellow for PyTorch
                with dpg.tooltip(engine_badge):
                    dpg.add_text("[TRT] = TensorRT (fast, GPU-optimized)\n[PT] = PyTorch (slower, more compatible)")
                dpg.add_spacer(width=scaled(6))
                dpg.add_text("FPS:", color=(180, 180, 180))
                dpg.add_text("--", tag="badge_fps", color=(150, 200, 255))
                dpg.add_spacer(width=scaled(6))
                dpg.add_text("GPU:", color=(180, 180, 180))
                dpg.add_text("--", tag="topbar_gpu_util_text", color=(150, 150, 150))
                dpg.add_spacer(width=scaled(8))
                dpg.add_text("VRAM:", color=(180, 180, 180))
                dpg.add_text("--", tag="topbar_gpu_vram_text", color=(150, 150, 150))


def build_video_panel(gui: Any):
    """Video preview with SOURCE controls."""
    with dpg.child_window(width=gui.video_width + scaled(20), autosize_y=True, border=False, tag="video_panel"):
        dpg.add_image(gui.frame_texture_tag, width=gui.video_width, height=gui.video_height, tag="video_image")
        dpg.add_separator()
        
        # SOURCE row - unified source control with dynamic right section
        # Layout: SOURCE | LIVE/REC + slots | status/playback controls
        with dpg.table(
            header_row=False,
            policy=dpg.mvTable_SizingStretchProp,
            borders_innerH=True,
            borders_innerV=True,
            borders_outerH=True,
            borders_outerV=True,
            pad_outerX=True,
            row_background=True,
        ):
            dpg.add_table_column(init_width_or_weight=0.6)   # SOURCE label
            dpg.add_table_column(init_width_or_weight=3.0)   # LIVE/REC + slots
            dpg.add_table_column(init_width_or_weight=2.4)   # Status/controls
            with dpg.table_row():
                dpg.add_text("SOURCE", color=(120, 200, 140))
                
                # Center: LIVE/REC buttons + slot buttons
                with dpg.group(horizontal=True):
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
                    for slot in range(1, 11):
                        slot_btn = dpg.add_button(
                            label=str(slot),
                            tag=f"rec_slot_{slot}_btn",
                            width=scaled(23),
                            callback=lambda s, a, u: gui._on_rec_slot_click(u),
                            user_data=slot,
                        )
                        dpg.bind_item_theme(slot_btn, gui._slot_empty_theme)
                
                # Right: Dynamic status area (changes based on mode)
                with dpg.group(horizontal=False):
                    # LIVE/REC status (shown when not playing)
                    with dpg.group(horizontal=True, tag="source_status_group"):
                        dpg.add_text("", tag="rec_status_text", color=(80, 200, 80))
                        dpg.add_text("", tag="rec_frame_counter", color=(255, 100, 100))
                    
                    # Playback controls (shown when playing)
                    with dpg.group(horizontal=True, tag="source_playback_group", show=False):
                        dpg.add_text("", tag="rec_playback_progress", color=(100, 180, 220))
                        dpg.add_combo(
                            items=["x0.25", "x0.5", "x0.75", "x1.0", "x1.5", "x2.0", "x4.0"],
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
        
        # Spacer to push STANDBY/RUN to bottom
        dpg.add_spacer(height=-1)
        
        # Extra space above buttons
        dpg.add_spacer(height=scaled(12))
        
        # STANDBY / RUN buttons at bottom, centered
        # Active state = highlighted color, inactive = greyed out
        with dpg.group(horizontal=True):
            # Calculate centering offset
            btn_width = scaled(100)
            total_btns_width = btn_width * 2 + scaled(10)  # 2 buttons + gap
            offset = (gui.video_width - total_btns_width) // 2
            dpg.add_spacer(width=offset)
            
            standby_btn = dpg.add_button(
                label="STANDBY",
                tag="state_standby_btn",
                width=btn_width,
                height=scaled(32),
                callback=gui._on_state_standby,
            )
            dpg.bind_item_theme(standby_btn, gui._btn_standby_theme)  # Start inactive
            with dpg.tooltip(standby_btn):
                dpg.add_text("STANDBY: Preview + enhancement, no YOLO, no OSC")
            
            dpg.add_spacer(width=scaled(10))
            
            run_btn = dpg.add_button(
                label="RUN",
                tag="state_run_btn",
                width=btn_width,
                height=scaled(32),
                callback=gui._on_state_run,
            )
            dpg.bind_item_theme(run_btn, gui._btn_run_active_theme)  # Start active
            with dpg.tooltip(run_btn):
                dpg.add_text("RUN: Full YOLO inference + OSC output")
        
        # Stats footer - compact line at bottom of video panel
        dpg.add_spacer(height=scaled(170))
        with dpg.group(horizontal=True):
            dpg.add_text("Dancers:", color=(100, 100, 100))
            dpg.add_text("0", tag="dancers_text", color=(140, 180, 140))
            dpg.add_spacer(width=scaled(6))
            dpg.add_text("In:", color=(100, 100, 100))
            dpg.add_text("--", tag="input_res_text", color=(140, 180, 140))
            dpg.add_spacer(width=scaled(4))
            dpg.add_text("Prev:", color=(80, 80, 80))
            dpg.add_text("--", tag="preview_tex_text", color=(90, 90, 90))
            dpg.add_spacer(width=scaled(6))
            dpg.add_text("Bright:", color=(100, 100, 100))
            dpg.add_text("--", tag="brightness_text", color=(120, 120, 120))
            dpg.add_spacer(width=scaled(8))
            dpg.add_text("|", color=(60, 60, 60))
            dpg.add_spacer(width=scaled(8))
            dpg.add_text("FPS:", color=(100, 100, 100))
            dpg.add_text("--", tag="fps_text", color=(140, 180, 140))
            dpg.add_spacer(width=scaled(6))
            dpg.add_text("Enh:", color=(80, 80, 80))
            dpg.add_text("--", tag="time_enhance", color=(100, 100, 100))
            dpg.add_spacer(width=scaled(3))
            dpg.add_text("YOLO:", color=(80, 80, 80))
            dpg.add_text("--", tag="time_yolo", color=(100, 100, 100))
            dpg.add_spacer(width=scaled(3))
            dpg.add_text("Trk:", color=(80, 80, 80))
            dpg.add_text("--", tag="time_track", color=(100, 100, 100))
            dpg.add_spacer(width=scaled(3))
            dpg.add_text("Prev:", color=(80, 80, 80))
            dpg.add_text("--", tag="time_preview", color=(100, 100, 100))
            dpg.add_spacer(width=scaled(6))
            dpg.add_text("Tot:", color=(100, 100, 100))
            dpg.add_text("--", tag="time_total", color=(140, 180, 140))
        
        # Hidden tags (for code compatibility)
        with dpg.group(show=False):
            dpg.add_text("", tag="path_enhance")
            dpg.add_text("", tag="path_yolo")
            dpg.add_text("", tag="path_track")


def build_control_panel(gui: Any):
    """Right-side control stack - mutually exclusive dropdowns.
    
    Structure:
    1. Show Settings - per-venue adjustments (default open)
    2. Visualization - S/K/B/T/I toggles
    3. Input/Enhancement/Model/Preview/Tracker - collapsed, mutually exclusive
    """
    with dpg.child_window(width=scaled(320), autosize_y=True, border=False, tag="control_panel"):
        # === SHOW SETTINGS (Visible, per-venue) ===
        build_show_settings(gui)
        dpg.add_spacer(height=scaled(8))
        
        # === VISUALIZATION TOOLBAR (Compact) ===
        build_visualization_toolbar(gui)
        dpg.add_spacer(height=scaled(8))
        
        # === DETAIL SECTIONS (Open by default with spacing) ===
        build_input_section(gui)
        dpg.add_spacer(height=scaled(8))
        build_enhancement_section(gui)
        dpg.add_spacer(height=scaled(8))
        build_model_section(gui)
        dpg.add_spacer(height=scaled(8))
        build_preview_section(gui)
        dpg.add_spacer(height=scaled(8))
        build_tracker_section(gui)
        dpg.add_spacer(height=scaled(8))
        build_osc_section(gui)
        
        dpg.add_spacer(height=scaled(20))
        quit_btn = dpg.add_button(label="Quit [Q]", width=-1, callback=gui._on_quit)
        with dpg.tooltip(quit_btn):
            dpg.add_text("Exit WallDance (Ctrl+Q or Q)")


def build_show_settings(gui: Any):
    """Show settings - per-venue adjustments that operators adjust frequently."""
    with dpg.collapsing_header(label="SHOW SETTINGS", default_open=True, tag="show_settings_header"):
        # Person Height and Max Dancers on same row
        with dpg.group(horizontal=True):
            with dpg.group():
                dpg.add_text("Person Height", color=(180, 180, 180))
                height_slider = dpg.add_slider_int(
                    tag="person_height_slider",
                    default_value=gui.config.get("person_height_px", 200),
                    min_value=50,
                    max_value=800,
                    format="%d px",
                    width=scaled(130),
                    callback=gui._on_person_height_change,
                )
                with dpg.tooltip(height_slider):
                    dpg.add_text("Expected person height in pixels.\nCalibrate for each venue/distance.")
            
            dpg.add_spacer(width=scaled(10))
            
            with dpg.group():
                dpg.add_text("Max Dancers", color=(180, 180, 180))
                max_p_slider = dpg.add_slider_int(
                    tag="max_persons_slider",
                    default_value=gui.config.get("max_persons", 6),
                    min_value=1,
                    max_value=12,
                    width=scaled(100),
                    callback=gui._on_max_persons_change,
                )
                with dpg.tooltip(max_p_slider):
                    dpg.add_text("Maximum dancers to track")
        
        dpg.add_spacer(height=scaled(6))
        
        # Confidence threshold
        dpg.add_text("Detection Confidence", color=(180, 180, 180))
        conf_slider = dpg.add_slider_float(
            tag="show_conf_slider",
            default_value=gui.config.get("confidence", 0.25),
            min_value=0.1,
            max_value=0.9,
            format="%.2f",
            width=-1,
            callback=gui._on_confidence_change,
        )
        with dpg.tooltip(conf_slider):
            dpg.add_text("Lower = more detections (may include false positives)\nHigher = fewer, more certain detections")


def build_visualization_toolbar(gui: Any):
    """Compact icon-based visualization toggles."""
    with dpg.group(horizontal=True):
        dpg.add_text("View:", color=(120, 200, 140))
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
        dpg.bind_item_theme(kp_btn, gui._vis_btn_on_theme if gui.config.get("show_keypoints", True) else gui._vis_btn_off_theme)
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
        dpg.bind_item_theme(trails_btn, gui._vis_btn_on_theme if gui.config.get("show_trails", True) else gui._vis_btn_off_theme)
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


def build_tracker_section(gui: Any):
    """Tracker settings - open by default."""
    with dpg.collapsing_header(label="Tracker", default_open=True, tag="section_tracker", closable=False):
        dpg.add_text("Distance Threshold")
        dist_slider = dpg.add_slider_int(
            tag="tracker_dist_slider",
            default_value=gui.config.get("tracker_distance", 300),
            min_value=100,
            max_value=500,
            format="%d px",
            callback=gui._on_tracker_distance_change,
        )
        with dpg.tooltip(dist_slider):
            dpg.add_text("Max distance for matching detections to tracks")
        
        dpg.add_text("Max Age (frames)")
        age_slider = dpg.add_slider_int(
            tag="tracker_age_slider",
            default_value=gui.config.get("tracker_max_age", 20),
            min_value=5,
            max_value=60,
            callback=gui._on_tracker_age_change,
        )
        with dpg.tooltip(age_slider):
            dpg.add_text("Frames to keep a lost track")
        
        dpg.add_text("Smoothing")
        smooth_slider = dpg.add_slider_int(
            tag="tracker_smoothing_slider",
            default_value=gui.config.get("tracker_smoothing", 1),
            min_value=1,
            max_value=10,
            callback=gui._on_tracker_smoothing_change,
        )
        with dpg.tooltip(smooth_slider):
            dpg.add_text("Temporal smoothing of keypoint positions.\n1 = No smoothing (raw detections)\nHigher = Smoother but more latency")
        dpg.add_spacer(height=scaled(5))
        dpg.add_button(label="Reset Tracker [R]", width=-1, callback=gui._on_tracker_reset)


def build_osc_section(gui: Any):
    """OSC output settings - open by default."""
    with dpg.collapsing_header(label="OSC", default_open=True, tag="section_osc", closable=False):
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
        dpg.add_text("Target Address", color=(180, 180, 180))
        with dpg.group(horizontal=True):
            dpg.add_input_text(
                tag="osc_ip_input",
                default_value=gui.config.get("osc_ip", "127.0.0.1"),
                width=scaled(140),
                callback=gui._on_osc_config_change,
            )
            dpg.add_text(":", color=(150, 150, 150))
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
    with dpg.collapsing_header(label="Model", default_open=True, tag="section_model", closable=False):
        dpg.add_text("YOLO Model", color=(180, 180, 180))
        with dpg.group(horizontal=True):
            dpg.add_combo(
                items=[
                    "yolo11n-pose", "yolo11s-pose", "yolo11m-pose", 
                    "yolo11l-pose", "yolo11x-pose",
                    "yolov8n-pose", "yolov8s-pose", "yolov8m-pose", 
                    "yolov8l-pose", "yolov8x-pose",
                ],
                tag="adv_model_combo",
                default_value=gui.config.get("model", "yolo11m-pose"),
                width=scaled(140),
                callback=gui._on_model_change,
            )
            dpg.add_text("FP16:")
            dpg.add_checkbox(
                tag="adv_fp16_checkbox",
                default_value=gui.config.get("fp16", False),
                callback=gui._on_fp16_toggle,
            )
        
        dpg.add_spacer(height=scaled(4))
        dpg.add_text("Image Size", color=(180, 180, 180))
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
    with dpg.collapsing_header(label="Enhancement", default_open=True, tag="section_enhancement", closable=False):
        with dpg.group(horizontal=True):
            dpg.add_checkbox(
                label="Enable",
                tag="adv_enhance_checkbox",
                default_value=gui.config.get("enhance_enabled", False),
                callback=gui._on_enhance_toggle,
            )
            dpg.add_checkbox(
                label="Lite",
                tag="adv_enhance_lite_checkbox",
                default_value=gui.config.get("enhance_lite", False),
                callback=gui._on_enhance_lite_toggle,
            )
            dpg.add_checkbox(
                label="Force",
                tag="adv_enhance_force_checkbox",
                default_value=gui.config.get("enhance_force", False),
                callback=gui._on_enhance_force_toggle,
            )
        
        dpg.add_text("Brightness Threshold", color=(180, 180, 180))
        dpg.add_slider_int(
            tag="adv_brightness_threshold_slider",
            default_value=gui.config.get("brightness_threshold", 60),
            min_value=0,
            max_value=255,
            callback=gui._on_brightness_threshold_change,
        )
        
        dpg.add_text("CLAHE Clip", color=(180, 180, 180))
        dpg.add_slider_float(
            tag="adv_clahe_slider",
            default_value=gui.config.get("clahe_clip", 3.0),
            min_value=1.0,
            max_value=6.0,
            format="%.1f",
            callback=gui._on_clahe_change,
        )
        
        dpg.add_text("Gamma", color=(180, 180, 180))
        dpg.add_slider_float(
            tag="adv_gamma_slider",
            default_value=gui.config.get("gamma", 1.2),
            min_value=0.5,
            max_value=2.5,
            format="%.2f",
            callback=gui._on_gamma_change,
        )
        
        dpg.add_text("Temporal Denoise", color=(180, 180, 180))
        dpg.add_slider_float(
            tag="adv_denoise_slider",
            default_value=gui.config.get("denoise_strength", 0.0),
            min_value=0.0,
            max_value=0.9,
            format="%.2f",
            callback=gui._on_denoise_change,
        )


def build_input_section(gui: Any):
    """Input settings - open by default."""
    with dpg.collapsing_header(label="Input", default_open=True, tag="section_input", closable=False):
        dpg.add_text("Camera", color=(180, 180, 180))
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
            dpg.add_button(
                label="Stop" if gui.config.get("camera_running", True) else "Start",
                tag="adv_camera_toggle_btn",
                width=scaled(50),
                callback=gui._on_camera_toggle,
            )
        
        dpg.add_spacer(height=scaled(4))
        dpg.add_text("Frame Skip", color=(180, 180, 180))
        dpg.add_slider_int(
            tag="adv_frame_skip_slider",
            default_value=gui.config.get("frame_skip", 0),
            min_value=0,
            max_value=4,
            callback=gui._on_frame_skip_change,
        )


def build_preview_section(gui: Any):
    """Preview settings - open by default."""
    with dpg.collapsing_header(label="Preview", default_open=True, tag="section_preview", closable=False):
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
        
        dpg.add_text("Preview Scale", color=(180, 180, 180))
        dpg.add_slider_float(
            tag="adv_preview_scale_slider",
            default_value=gui.config.get("preview_scale", 0.5),
            min_value=0.25,
            max_value=1.0,
            format="%.2f",
            callback=gui._on_preview_scale_change,
        )