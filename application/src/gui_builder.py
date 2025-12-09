"""
Layout and theme helpers for the DearPyGui interface.
The functions here are called from `gui.WallDanceGUI` to keep that class lean.
"""

import os
from typing import Any

import dearpygui.dearpygui as dpg
import numpy as np

from gui_icons import Icons


def setup_theme(gui: Any):
    """Configure the global and topbar themes."""
    with dpg.theme() as gui.global_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 6, 6)
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
            dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 4, 1)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 4, 3)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 6, 1)

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

    load_icon_font(gui)


def load_icon_font(gui: Any):
    """Load Font Awesome icons used by the GUI."""
    src_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(src_dir)
    font_path = os.path.join(project_dir, "assets", "fa-solid.otf")

    if not os.path.exists(font_path):
        print(f"Warning: Icon font not found at {font_path}")
        gui._icon_font = None
        return

    with dpg.font_registry():
        gui._icon_font = dpg.add_font(font_path, 14)
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
            build_video_panel(gui)
            build_control_panel(gui)


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
                    width=150,
                    callback=gui._on_topbar_project_change,
                )
                dpg.add_spacer(width=15)
                dpg.add_text("Version:", color=(120, 200, 140))
                dpg.add_combo(
                    items=[],
                    tag="topbar_config_combo",
                    default_value="",
                    width=180,
                    callback=gui._on_topbar_config_change,
                )
                save_btn = dpg.add_button(
                    label=Icons.FLOPPY_DISK,
                    tag="topbar_save_btn",
                    width=20,
                    height=20,
                    callback=gui._on_save_config,
                )
                if gui._icon_font:
                    dpg.bind_item_font(save_btn, gui._icon_font)
                with dpg.tooltip(save_btn):
                    dpg.add_text("Save config (Ctrl+S)")
                
                safe_btn = dpg.add_button(
                    label=Icons.ROTATE,
                    tag="topbar_safe_btn",
                    width=20,
                    height=20,
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
                dpg.add_text("CAM:", color=(180, 180, 180))
                cam_badge = dpg.add_text("OFF", tag="badge_cam", color=(255, 120, 120))
                with dpg.tooltip(cam_badge):
                    dpg.add_text("Camera status: ON (green) or OFF (red)")
                dpg.add_spacer(width=6)
                dpg.add_text("OSC:", color=(180, 180, 180))
                osc_badge = dpg.add_text("OFF", tag="badge_osc", color=(255, 120, 120))
                with dpg.tooltip(osc_badge):
                    dpg.add_text("OSC output status: ON (green) or OFF (red)")
                dpg.add_spacer(width=6)
                dpg.add_text("Model:", color=(180, 180, 180))
                dpg.add_text("--", tag="badge_model", color=(150, 200, 255))
                dpg.add_spacer(width=3)
                engine_badge = dpg.add_text("[PT]", tag="badge_engine_type", color=(255, 220, 100))  # Yellow for PyTorch
                with dpg.tooltip(engine_badge):
                    dpg.add_text("[TRT] = TensorRT (fast, GPU-optimized)\n[PT] = PyTorch (slower, more compatible)")
                dpg.add_spacer(width=6)
                dpg.add_text("FPS:", color=(180, 180, 180))
                dpg.add_text("--", tag="badge_fps", color=(150, 200, 255))
                dpg.add_spacer(width=6)
                dpg.add_text("GPU:", color=(180, 180, 180))
                dpg.add_text("--", tag="topbar_gpu_util_text", color=(150, 150, 150))
                dpg.add_spacer(width=8)
                dpg.add_text("VRAM:", color=(180, 180, 180))
                dpg.add_text("--", tag="topbar_gpu_vram_text", color=(150, 150, 150))


def build_video_panel(gui: Any):
    """Video preview and core controls grouped in the left panel."""
    with dpg.child_window(width=gui.video_width + 20, height=-1, tag="video_panel"):
        dpg.add_image(gui.frame_texture_tag, width=gui.video_width, height=gui.video_height, tag="video_image")
        dpg.add_separator()
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
            dpg.add_table_column(init_width_or_weight=0.8)
            dpg.add_table_column(init_width_or_weight=1.6)
            dpg.add_table_column(init_width_or_weight=1.4)
            dpg.add_table_column(init_width_or_weight=1.5)
            with dpg.table_row():
                dpg.add_text("INPUT", color=(120, 200, 140))
                with dpg.group(horizontal=True):
                    dpg.add_text("Cam:")
                    dpg.add_combo(
                        items=gui.config.get("camera_sources", ["0"]),
                        tag="tbl_camera_combo",
                        default_value=gui.config.get("camera_source", "0"),
                        width=100,
                        callback=gui._on_camera_change,
                    )
                    dpg.add_button(
                        label="Stop" if gui.config.get("camera_running", True) else "Start",
                        tag="camera_toggle_btn",
                        width=60,
                        callback=gui._on_camera_toggle,
                    )
                with dpg.group(horizontal=True):
                    dpg.add_text("Res:")
                    dpg.add_text("--", tag="input_res_text")
                with dpg.group(horizontal=True):
                    dpg.add_text("Skip:")
                    dpg.add_slider_int(
                        tag="tbl_frame_skip_slider",
                        default_value=gui.config.get("frame_skip", 0),
                        min_value=0,
                        max_value=4,
                        width=-1,
                        callback=gui._on_frame_skip_change,
                    )
            with dpg.table_row():
                with dpg.group(horizontal=True):
                    dpg.add_text("PREVIEW", color=(120, 200, 140))
                    dpg.add_checkbox(
                        tag="tbl_preview_checkbox",
                        default_value=gui.config.get("preview_enabled", True),
                        callback=gui._on_preview_toggle,
                    )
                with dpg.group(horizontal=True, tag="preview_tex_group"):
                    dpg.add_text("Tex:")
                    dpg.add_text("--", tag="preview_tex_text")
                with dpg.group(horizontal=True, tag="preview_scale_group"):
                    dpg.add_text("Scale:", tag="preview_scale_label")
                    dpg.add_slider_float(
                        tag="tbl_preview_scale_slider",
                        default_value=gui.config.get("preview_scale", 0.5),
                        min_value=0.25,
                        max_value=1.0,
                        format="%.2f",
                        width=-1,
                        callback=gui._on_preview_scale_change,
                    )
                with dpg.group(horizontal=True, tag="preview_cap_group"):
                    dpg.add_text("Cap:", tag="preview_cap_label")
                    dpg.add_checkbox(
                        tag="tbl_preview_cap_checkbox",
                        default_value=gui.config.get("preview_fps_cap", True),
                        callback=gui._on_preview_cap_toggle,
                    )
            with dpg.table_row():
                with dpg.group(horizontal=True):
                    dpg.add_text("ENHANCE", color=(120, 200, 140))
                    dpg.add_checkbox(
                        tag="tbl_enhance_checkbox",
                        default_value=gui.config.get("enhance_enabled", False),
                        callback=gui._on_enhance_toggle,
                    )
                with dpg.group(horizontal=True, tag="enhance_lite_group"):
                    dpg.add_text("Lite:", tag="enhance_lite_label")
                    dpg.add_checkbox(
                        tag="tbl_enhance_lite_checkbox",
                        default_value=gui.config.get("enhance_lite", False),
                        callback=gui._on_enhance_lite_toggle,
                    )
                    dpg.add_text("  Force:", tag="enhance_force_label")
                    force_cb = dpg.add_checkbox(
                        tag="tbl_enhance_force_checkbox",
                        default_value=gui.config.get("enhance_force", False),
                        callback=gui._on_enhance_force_toggle,
                    )
                    with dpg.tooltip(force_cb):
                        dpg.add_text("Force enhancement even when\nbrightness is above threshold")
                with dpg.group(horizontal=True, tag="enhance_clahe_group"):
                    dpg.add_text("Clahe:", tag="enhance_clahe_label")
                    dpg.add_slider_float(
                        tag="tbl_clahe_slider",
                        default_value=gui.config.get("clahe_clip", 3.0),
                        min_value=1.0,
                        max_value=6.0,
                        format="%.1f",
                        width=-1,
                        callback=gui._on_clahe_change,
                    )
                with dpg.group(horizontal=True, tag="enhance_gamma_group"):
                    dpg.add_text("Gamma:", tag="enhance_gamma_label")
                    dpg.add_slider_float(
                        tag="tbl_gamma_slider",
                        default_value=gui.config.get("gamma", 1.2),
                        min_value=0.5,
                        max_value=2.5,
                        format="%.2f",
                        width=-1,
                        callback=gui._on_gamma_change,
                    )
                with dpg.group(horizontal=True, tag="enhance_threshold_group"):
                    dpg.add_text("Threshold:", tag="enhance_threshold_label")
                    threshold_slider = dpg.add_slider_int(
                        tag="tbl_brightness_threshold_slider",
                        default_value=gui.config.get("brightness_threshold", 60),
                        min_value=0,
                        max_value=255,
                        format="%d",
                        width=-1,
                        callback=gui._on_brightness_threshold_change,
                    )
                    with dpg.tooltip(threshold_slider):
                        dpg.add_text("Brightness threshold for auto-bypass.\nIf scene brightness > threshold,\nenhancement is skipped (unless Forced).")
            with dpg.table_row():
                dpg.add_text("MODEL", color=(120, 200, 140))
                with dpg.group(horizontal=True):
                    dpg.add_combo(
                        items=[
                            "yolo11n-pose",
                            "yolo11s-pose",
                            "yolo11m-pose",
                            "yolo11l-pose",
                            "yolo11x-pose",
                            "yolov8n-pose",
                            "yolov8s-pose",
                            "yolov8m-pose",
                            "yolov8l-pose",
                            "yolov8x-pose",
                        ],
                        tag="tbl_model_combo",
                        default_value=gui.config.get("model", "yolo11m-pose"),
                        width=-80,
                        callback=gui._on_model_change,
                    )
                    dpg.add_text("FP16:")
                    dpg.add_checkbox(
                        tag="tbl_fp16_checkbox",
                        default_value=gui.config.get("fp16", False),
                        callback=gui._on_fp16_toggle,
                    )
                with dpg.group(horizontal=True):
                    dpg.add_text("ImgSz:")
                    dpg.add_combo(
                        items=["640", "800", "960", "1280", "1920"],
                        tag="tbl_imgsz_combo",
                        default_value=str(gui.config.get("yolo_imgsz", 640)),
                        width=-100,
                        callback=gui._on_imgsz_change,
                    )
                    dpg.add_text("TensorRT:")
                    dpg.add_checkbox(
                        tag="tbl_trt_checkbox",
                        default_value=gui.config.get("use_tensorrt", False),
                        callback=gui._on_trt_toggle,
                    )
                with dpg.group(horizontal=True):
                    dpg.add_text("Conf:")
                    conf_slider = dpg.add_slider_float(
                        tag="tbl_conf_slider",
                        default_value=gui.config.get("confidence", 0.25),
                        min_value=0.1,
                        max_value=0.9,
                        format="%.2f",
                        width=-1,
                        callback=gui._on_confidence_change,
                    )
                    with dpg.tooltip(conf_slider):
                        dpg.add_text("Detection confidence threshold.\nLower = more detections (may include false positives).\nHigher = fewer, more certain detections.")
        dpg.add_spacer(height=4)
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
            dpg.add_table_column(init_width_or_weight=0.8)
            dpg.add_table_column(init_width_or_weight=1.0)
            dpg.add_table_column(init_width_or_weight=1.0)
            dpg.add_table_column(init_width_or_weight=1.0)
            dpg.add_table_column(init_width_or_weight=1.0)
            dpg.add_table_column(init_width_or_weight=1.0)
            with dpg.table_row():
                dpg.add_text("PROCESS", color=(120, 200, 140))
                with dpg.group(horizontal=True):
                    dpg.add_text("FPS:")
                    dpg.add_text("0.0", tag="fps_text", color=(0, 255, 100))
                with dpg.group(horizontal=True):
                    dpg.add_text("Dancers:")
                    dpg.add_text("0", tag="dancers_text", color=(0, 255, 100))
                with dpg.group(horizontal=True):
                    dpg.add_text("Bright:")
                    dpg.add_text("0", tag="brightness_text", color=(150, 150, 150))
                dpg.add_text("")
                dpg.add_text("")
        dpg.add_spacer(height=4)
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
            dpg.add_table_column(init_width_or_weight=0.8)
            dpg.add_table_column(init_width_or_weight=1.2)
            dpg.add_table_column(init_width_or_weight=1.2)
            dpg.add_table_column(init_width_or_weight=1.0)
            dpg.add_table_column(init_width_or_weight=1.0)
            dpg.add_table_column(init_width_or_weight=1.0)
            with dpg.table_row():
                dpg.add_text("TIMINGS", color=(120, 200, 140))
                with dpg.group(horizontal=True):
                    dpg.add_text("[CPU]", tag="path_enhance", color=(255, 120, 120))
                    dpg.add_text("Enh:")
                    dpg.add_text("--", tag="time_enhance", color=(180, 180, 180))
                with dpg.group(horizontal=True):
                    dpg.add_text("[GPU]", tag="path_yolo", color=(120, 255, 120))
                    dpg.add_text("YOLO:")
                    dpg.add_text("--", tag="time_yolo", color=(180, 180, 180))
                with dpg.group(horizontal=True):
                    dpg.add_text("[CPU]", tag="path_track", color=(255, 120, 120))
                    dpg.add_text("Trk:")
                    dpg.add_text("--", tag="time_track", color=(180, 180, 180))
                with dpg.group(horizontal=True):
                    dpg.add_text("Prev:")
                    dpg.add_text("--", tag="time_preview", color=(180, 180, 180))
                with dpg.group(horizontal=True):
                    dpg.add_text("Tot:")
                    dpg.add_text("--", tag="time_total", color=(180, 180, 180))


def build_control_panel(gui: Any):
    """Right-side control stack."""
    with dpg.child_window(width=320, height=-1, tag="control_panel"):
        # Recording controls at top
        build_recording_panel(gui)
        dpg.add_spacer(height=10)
        
        with dpg.collapsing_header(label="Detection", default_open=True):
            with dpg.group(horizontal=True):
                with dpg.group():
                    dpg.add_text("Max Persons", color=(180, 180, 180))
                    max_p_slider = dpg.add_slider_int(
                        tag="max_persons_slider",
                        default_value=gui.config.get("max_persons", 6),
                        min_value=1,
                        max_value=12,
                        width=100,
                        callback=gui._on_max_persons_change,
                    )
                    with dpg.tooltip(max_p_slider):
                        dpg.add_text("Maximum dancers to track simultaneously")
                dpg.add_spacer(width=10)
                with dpg.group():
                    dpg.add_text("Height (px)", color=(180, 180, 180))
                    height_slider = dpg.add_slider_int(
                        tag="person_height_slider",
                        default_value=gui.config.get("person_height_px", 200),
                        min_value=50,
                        max_value=800,
                        width=100,
                        callback=gui._on_person_height_change,
                    )
                    with dpg.tooltip(height_slider):
                        dpg.add_text("Expected person height in pixels.\nAdjust to match dancers in frame.")
        dpg.add_spacer(height=10)
        with dpg.collapsing_header(label="Visualization", default_open=True):
            dpg.add_checkbox(
                label="Skeleton [S]",
                tag="skeleton_checkbox",
                default_value=gui.config.get("show_skeleton", True),
                callback=lambda s, d: gui._on_vis_toggle("skeleton", d),
            )
            dpg.add_checkbox(
                label="Keypoints [K]",
                tag="keypoints_checkbox",
                default_value=gui.config.get("show_keypoints", True),
                callback=lambda s, d: gui._on_vis_toggle("keypoints", d),
            )
            dpg.add_checkbox(
                label="Bounding Box [B]",
                tag="bbox_checkbox",
                default_value=gui.config.get("show_bbox", True),
                callback=lambda s, d: gui._on_vis_toggle("bbox", d),
            )
            dpg.add_checkbox(
                label="Motion Trails [T]",
                tag="trails_checkbox",
                default_value=gui.config.get("show_trails", True),
                callback=lambda s, d: gui._on_vis_toggle("trails", d),
            )
            dpg.add_checkbox(
                label="Dancer IDs [I]",
                tag="ids_checkbox",
                default_value=gui.config.get("show_ids", True),
                callback=lambda s, d: gui._on_vis_toggle("ids", d),
            )
        dpg.add_spacer(height=10)
        with dpg.collapsing_header(label="Tracker", default_open=True):
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
                dpg.add_text("Max distance for matching detections to tracks.\nIncrease for fast-moving dancers.")
            
            dpg.add_text("Max Age (frames)")
            age_slider = dpg.add_slider_int(
                tag="tracker_age_slider",
                default_value=gui.config.get("tracker_max_age", 20),
                min_value=5,
                max_value=60,
                callback=gui._on_tracker_age_change,
            )
            with dpg.tooltip(age_slider):
                dpg.add_text("Frames to keep a lost track before deletion.\nIncrease for brief occlusions.")
            
            dpg.add_text("Smoothing (frames)")
            smooth_slider = dpg.add_slider_int(
                tag="tracker_smoothing_slider",
                default_value=gui.config.get("tracker_smoothing", 1),
                min_value=1,
                max_value=10,
                callback=gui._on_tracker_smoothing_change,
            )
            with dpg.tooltip(smooth_slider):
                dpg.add_text("Temporal smoothing depth for confidence values.\nHigher = smoother but more latency.")
            
            dpg.add_spacer(height=10)
            dpg.add_button(label="Reset Tracker [R]", width=-1, callback=gui._on_tracker_reset)
        dpg.add_spacer(height=10)
        with dpg.collapsing_header(label="OSC Output", default_open=True):
            osc_chk = dpg.add_checkbox(
                label="Enable OSC",
                tag="osc_checkbox",
                default_value=gui.config.get("osc_enabled", True),
                callback=gui._on_osc_toggle,
            )
            with dpg.tooltip(osc_chk):
                dpg.add_text("Send pose data via Open Sound Control protocol")
            dpg.add_spacer(height=5)
            dpg.add_text("Target IP")
            osc_ip = dpg.add_input_text(
                tag="osc_ip_input",
                default_value=gui.config.get("osc_ip", "127.0.0.1"),
                width=-1,
                callback=gui._on_osc_config_change,
            )
            with dpg.tooltip(osc_ip):
                dpg.add_text("IP address of OSC receiver\n127.0.0.1 = localhost (same machine)")
            dpg.add_text("Target Port")
            osc_port = dpg.add_input_int(
                tag="osc_port_input",
                default_value=gui.config.get("osc_port", 9000),
                min_value=1024,
                max_value=65535,
                width=-1,
                callback=gui._on_osc_config_change,
            )
            with dpg.tooltip(osc_port):
                dpg.add_text("UDP port for OSC messages (1024-65535)")
        dpg.add_spacer(height=20)
        quit_btn = dpg.add_button(label="Quit [Q]", width=-1, callback=gui._on_quit)
        with dpg.tooltip(quit_btn):
            dpg.add_text("Exit WallDance (Ctrl+Q or Q)")


def build_recording_panel(gui: Any):
    """Recording controls: LIVE, REC, and 9 slot buttons."""
    with dpg.collapsing_header(label="SOURCE", default_open=True):
        # dpg.add_spacer(height=5)
        
        # Status text
        with dpg.group(horizontal=True):
            dpg.add_text("Status:", color=(150, 150, 150))
            dpg.add_text("LIVE", tag="rec_status_text", color=(80, 200, 80))
        
        dpg.add_spacer(height=5)
        
        # LIVE and REC buttons
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="LIVE",
                tag="rec_live_btn",
                width=70,
                callback=gui._on_rec_live,
            )
            dpg.bind_item_theme("rec_live_btn", gui._rec_live_active_theme)
            
            dpg.add_button(
                label="REC",
                tag="rec_rec_btn",
                width=70,
                callback=gui._on_rec_toggle,
            )
            dpg.bind_item_theme("rec_rec_btn", gui._rec_btn_theme)
            
            # Frame counter for recording
            dpg.add_text("", tag="rec_frame_counter", color=(150, 150, 150))
    
    dpg.add_spacer(height=5)
    
    # Slot buttons 1-9 on two rows
    with dpg.group(horizontal=True):
        for slot in range(1, 6):  # Row 1: slots 1-5
            dpg.add_button(
                label=str(slot),
                tag=f"rec_slot_{slot}_btn",
                width=30,
                callback=lambda s, a, u: gui._on_rec_slot_click(u),
                user_data=slot,
            )
            dpg.bind_item_theme(f"rec_slot_{slot}_btn", gui._slot_empty_theme)
    
    with dpg.group(horizontal=True):
        for slot in range(6, 10):  # Row 2: slots 6-9
            dpg.add_button(
                label=str(slot),
                tag=f"rec_slot_{slot}_btn",
                width=30,
                callback=lambda s, a, u: gui._on_rec_slot_click(u),
                user_data=slot,
            )
            dpg.bind_item_theme(f"rec_slot_{slot}_btn", gui._slot_empty_theme)
    
    dpg.add_spacer(height=5)
    
    # Playback controls (hidden by default)
    with dpg.group(horizontal=True, tag="rec_controls_group", show=False):
        dpg.add_text("Speed:", color=(150, 150, 150))
        dpg.add_combo(
            items=["x0.25", "x0.5", "x0.75", "x1.0", "x1.5", "x2.0", "x4.0"],
            tag="rec_speed_combo",
            default_value="x1.0",
            width=80,
            callback=gui._on_playback_speed_change,
        )
        dpg.add_spacer(width=10)
        pause_btn = dpg.add_button(
            label=Icons.PAUSE,
            tag="rec_pause_btn",
            width=35,
            callback=gui._on_playback_pause,
        )
        if gui._icon_font:
            dpg.bind_item_font(pause_btn, gui._icon_font)
        
        prev_btn = dpg.add_button(
            label=Icons.STEP_BACKWARD,
            tag="rec_prev_frame_btn",
            width=35,
            callback=gui._on_playback_prev_frame,
        )
        if gui._icon_font:
            dpg.bind_item_font(prev_btn, gui._icon_font)
        
        next_btn = dpg.add_button(
            label=Icons.STEP_FORWARD,
            tag="rec_next_frame_btn",
            width=35,
            callback=gui._on_playback_next_frame,
        )
        if gui._icon_font:
            dpg.bind_item_font(next_btn, gui._icon_font)
    
    # Playback progress (hidden by default)
    with dpg.group(horizontal=True, tag="rec_playback_group", show=False):
        dpg.add_text("Time:", color=(150, 150, 150))
        dpg.add_text("0/0", tag="rec_playback_progress", color=(100, 180, 220))