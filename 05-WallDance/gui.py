"""
DearPyGui-based control panel for WallDance.
Provides real-time parameter adjustment with sliders, checkboxes, and buttons.
"""

import dearpygui.dearpygui as dpg
import numpy as np
from typing import Callable, Dict, Any, Optional

# GPU monitoring (optional - works with NVIDIA GPUs)
try:
    import pynvml
    pynvml.nvmlInit()
    _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    _GPU_AVAILABLE = True
except Exception:
    _GPU_AVAILABLE = False
    _GPU_HANDLE = None


def get_gpu_stats() -> dict:
    """Get GPU utilization, temperature, and VRAM usage."""
    if not _GPU_AVAILABLE:
        return {'util': -1, 'temp': -1, 'vram_pct': -1}
    try:
        util = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
        temp = pynvml.nvmlDeviceGetTemperature(_GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
        mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
        vram_pct = (mem.used / mem.total) * 100
        return {'util': util.gpu, 'temp': temp, 'vram_pct': vram_pct}
    except Exception:
        return {'util': -1, 'temp': -1, 'vram_pct': -1}


class WallDanceGUI:
    """Modern GUI for WallDance using DearPyGui."""
    
    def __init__(self, config: Dict[str, Any], callbacks: Dict[str, Callable]):
        """
        Initialize GUI.
        
        Args:
            config: Initial configuration values
            callbacks: Dict of callback functions for parameter changes
                - on_enhance_toggle(enabled)
                - on_upscale_change(factor)
                - on_clahe_change(clip_limit)
                - on_gamma_change(gamma)
                - on_confidence_change(conf)
                - on_visualization_toggle(name, enabled)
                - on_tracker_reset()
                - on_osc_toggle(enabled)
                - on_osc_config(ip, port)
                - on_quit()
        """
        self.config = config
        self.callbacks = callbacks
        self.frame_texture_id = None
        self.frame_texture_tag = "video_texture"
        self.texture_registry = None
        
        # Display dimensions (on-screen area)
        self.video_width = config.get('video_width', 960)
        self.video_height = config.get('video_height', 540)
        # Texture/render dimensions (actual resolution uploaded)
        self.texture_width = config.get('texture_width', self.video_width)
        self.texture_height = config.get('texture_height', self.video_height)
        
        # Stats
        self.fps = 0
        self.num_dancers = 0
        self.latency_ms = 0
        self.brightness = 0
        
        # Smoothed timing for preview (avoid flickering 0 values)
        self._last_preview_time = 0
        
        # Project/config state for top bar
        self._projects_list = []
        self._config_files_list = []
        self._current_project = ""
        self._current_config_timestamp = ""
        self._save_indicator_time = 0  # For showing save success feedback
        
        # Initialize DearPyGui
        dpg.create_context()
        self._setup_theme()
        self._create_texture()
        self._build_ui()
        
        # Set initial grey state for disabled rows
        self._update_preview_row_state(self.config.get('preview_enabled', True))
        self._update_enhance_row_state(
            self.config.get('enhance_enabled', False),
            self.config.get('enhance_lite', False),
            bypass=False
        )
        
    def _setup_theme(self):
        """Configure dark modern theme."""
        with dpg.theme() as self.global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 6)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 6)
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (30, 30, 35, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (50, 50, 55, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (70, 70, 75, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (90, 90, 95, 255))
                dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (40, 100, 150, 255))
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (100, 200, 255, 255))
                dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (100, 200, 255, 255))
                dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (130, 220, 255, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Button, (60, 120, 180, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (80, 140, 200, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (100, 160, 220, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Header, (60, 120, 180, 255))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (80, 140, 200, 255))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (100, 160, 220, 255))
        dpg.bind_theme(self.global_theme)
        
    def _create_texture(self):
        """Create dynamic texture for video frame."""
        # Pre-allocate frame buffer (RGBA float32, flattened)
        self.frame_buffer = np.zeros(
            self.texture_height * self.texture_width * 4, 
            dtype=np.float32
        )
        
        with dpg.texture_registry(show=False):
                self.frame_texture_id = dpg.add_raw_texture(
                width=self.texture_width,
                height=self.texture_height,
                    default_value=self.frame_buffer,
                    format=dpg.mvFormat_Float_rgba,
                    tag=self.frame_texture_tag
                )
        
        print(f"Texture created: {self.video_width}x{self.video_height}")
    
    def _build_ui(self):
        """Build the main UI layout."""
        # Main window
        with dpg.window(tag="main_window", label="WallDance Control Panel"):
            # Top bar: Project/Config/Save/GPU
            self._build_top_bar()
            
            dpg.add_spacer(height=4)
            
            with dpg.group(horizontal=True):
                # Left side: Video preview
                self._build_video_panel()
                
                # Right side: Controls
                self._build_control_panel()
    
    def _build_top_bar(self):
        """Build the top horizontal bar with project/config selectors, save buttons, and GPU info."""
        with dpg.group(horizontal=True, tag="top_bar"):
            # Left section: Project and Config dropdowns
            dpg.add_text("Project:", color=(120, 200, 255))
            dpg.add_combo(
                items=[],
                tag="topbar_project_combo",
                default_value="",
                width=150,
                callback=self._on_topbar_project_change
            )
            
            dpg.add_spacer(width=15)
            
            dpg.add_text("Config:", color=(120, 200, 255))
            dpg.add_combo(
                items=[],
                tag="topbar_config_combo",
                default_value="",
                width=180,
                callback=self._on_topbar_config_change
            )
            
            dpg.add_spacer(width=15)
            
            # Save buttons
            dpg.add_button(
                label="Save",
                tag="topbar_save_btn",
                width=60,
                callback=self._on_save_config
            )
            dpg.add_button(
                label="Save As...",
                tag="topbar_save_as_btn",
                width=80,
                callback=self._on_save_as_config
            )
            
            # Save indicator (shows briefly after save)
            dpg.add_text("", tag="save_indicator", color=(100, 255, 100))
            
            # Spacer to push GPU info to the right
            dpg.add_spacer(width=-1)  # This won't work as expected, use table for proper right-alignment
            
            # GPU info (right side)
            dpg.add_text("GPU:", color=(180, 180, 180))
            dpg.add_text("--", tag="topbar_gpu_util_text", color=(150, 150, 150))
            dpg.add_spacer(width=10)
            dpg.add_text("VRAM:", color=(180, 180, 180))
            dpg.add_text("--", tag="topbar_gpu_vram_text", color=(150, 150, 150))
    
    def _build_video_panel(self):
        """Build video preview panel."""
        with dpg.child_window(width=self.video_width + 20, height=-1, tag="video_panel"):
            # Video frame
            dpg.add_image("video_texture", width=self.video_width, height=self.video_height, tag="video_image")
            
            dpg.add_separator()

            # Each row is a separate table for better spacing/readability
            
            # INPUT row
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp, 
                           borders_innerV=True, borders_outerH=True, borders_outerV=True,
                           pad_outerX=True):
                dpg.add_table_column(init_width_or_weight=0.8)   # Section name
                dpg.add_table_column(init_width_or_weight=1.8)   # Col 1 - Camera
                dpg.add_table_column(init_width_or_weight=1.2)   # Col 2 - Resolution
                dpg.add_table_column(init_width_or_weight=1.5)   # Col 3 - Frame Skip

                with dpg.table_row():
                    dpg.add_text("INPUT", color=(120, 200, 255))
                    with dpg.group(horizontal=True):
                        dpg.add_text("Cam:")
                        dpg.add_combo(
                            items=self.config.get('camera_sources', ['0']),
                            tag="tbl_camera_combo",
                            default_value=self.config.get('camera_source', '0'),
                            width=120,
                            callback=self._on_camera_change
                        )
                    with dpg.group(horizontal=True):
                        dpg.add_text("--", tag="input_res_text")
                    with dpg.group(horizontal=True):
                        dpg.add_text("Skip:")
                        dpg.add_slider_int(
                            tag="tbl_frame_skip_slider",
                            default_value=self.config.get('frame_skip', 0),
                            min_value=0, max_value=4, width=-1,
                            callback=self._on_frame_skip_change
                        )

            dpg.add_spacer(height=3)

            # PREVIEW row
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp, 
                           borders_innerV=True, borders_outerH=True, borders_outerV=True,
                           pad_outerX=True):
                dpg.add_table_column(init_width_or_weight=0.8)
                dpg.add_table_column(init_width_or_weight=1.5)
                dpg.add_table_column(init_width_or_weight=1.5)
                dpg.add_table_column(init_width_or_weight=1.5)

                with dpg.table_row():
                    with dpg.group(horizontal=True):
                        dpg.add_text("PREVIEW", color=(120, 200, 255))
                        dpg.add_checkbox(
                            tag="tbl_preview_checkbox",
                            default_value=self.config.get('preview_enabled', True),
                            callback=self._on_preview_toggle
                        )
                    with dpg.group(horizontal=True, tag="preview_tex_group"):
                        dpg.add_text("--", tag="preview_tex_text")
                    with dpg.group(horizontal=True, tag="preview_scale_group"):
                        dpg.add_text("Scale:", tag="preview_scale_label")
                        dpg.add_slider_float(
                            tag="tbl_preview_scale_slider",
                            default_value=self.config.get('preview_scale', 0.5),
                            min_value=0.25, max_value=1.0, format="%.2f", width=-1,
                            callback=self._on_preview_scale_change
                        )

            dpg.add_spacer(height=3)

            # ENHANCE row
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp, 
                           borders_innerV=True, borders_outerH=True, borders_outerV=True,
                           pad_outerX=True):
                dpg.add_table_column(init_width_or_weight=0.8)
                dpg.add_table_column(init_width_or_weight=1.5)
                dpg.add_table_column(init_width_or_weight=1.5)
                dpg.add_table_column(init_width_or_weight=1.5)

                with dpg.table_row():
                    with dpg.group(horizontal=True):
                        dpg.add_text("ENHANCE", color=(120, 200, 255))
                        dpg.add_checkbox(
                            tag="tbl_enhance_checkbox",
                            default_value=self.config.get('enhance_enabled', False),
                            callback=self._on_enhance_toggle
                        )
                    with dpg.group(horizontal=True, tag="enhance_lite_group"):
                        dpg.add_text("Lite Mode:", tag="enhance_lite_label")
                        dpg.add_checkbox(
                            tag="tbl_enhance_lite_checkbox",
                            default_value=self.config.get('enhance_lite', False),
                            callback=self._on_enhance_lite_toggle
                        )
                    with dpg.group(horizontal=True, tag="enhance_clahe_group"):
                        dpg.add_text("Clahe:", tag="enhance_clahe_label")
                        dpg.add_slider_float(
                            tag="tbl_clahe_slider",
                            default_value=self.config.get('clahe_clip', 3.0),
                            min_value=1.0, max_value=6.0, format="%.1f", width=-1,
                            callback=self._on_clahe_change
                        )
                    with dpg.group(horizontal=True, tag="enhance_gamma_group"):
                        dpg.add_text("Gamma:", tag="enhance_gamma_label")
                        dpg.add_slider_float(
                            tag="tbl_gamma_slider",
                            default_value=self.config.get('gamma', 1.2),
                            min_value=0.5, max_value=2.5, format="%.2f", width=-1,
                            callback=self._on_gamma_change
                        )

            dpg.add_spacer(height=3)

            # MODEL row
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp, 
                           borders_innerV=True, borders_outerH=True, borders_outerV=True,
                           pad_outerX=True):
                dpg.add_table_column(init_width_or_weight=0.8)
                dpg.add_table_column(init_width_or_weight=1.5)
                dpg.add_table_column(init_width_or_weight=1.5)
                dpg.add_table_column(init_width_or_weight=1.5)

                with dpg.table_row():
                    dpg.add_text("MODEL", color=(120, 200, 255))
                    with dpg.group(horizontal=True):
                        dpg.add_combo(
                            items=["yolo11n-pose", "yolo11s-pose", "yolo11m-pose", "yolo11l-pose", "yolo11x-pose",
                                "yolov8n-pose", "yolov8s-pose", "yolov8m-pose", "yolov8l-pose", "yolov8x-pose"],
                            tag="tbl_model_combo",
                            default_value=self.config.get('model', 'yolo11m-pose'),
                            width=-80, callback=self._on_model_change
                        )
                        dpg.add_text(" FP16:")
                        dpg.add_checkbox(
                            tag="tbl_fp16_checkbox",
                            default_value=self.config.get('fp16', False),
                            callback=self._on_fp16_toggle
                        )
                    with dpg.group(horizontal=True):
                        dpg.add_text("ImgSz:")
                        dpg.add_combo(
                            items=["640", "800", "960", "1280", "1920"],
                            tag="tbl_imgsz_combo",
                            default_value=str(self.config.get('yolo_imgsz', 640)),
                            width=-1, callback=self._on_imgsz_change
                        )
                    with dpg.group(horizontal=True):
                        dpg.add_text("Confidence:")
                        dpg.add_slider_float(
                            tag="tbl_conf_slider",
                            default_value=self.config.get('confidence', 0.25),
                            min_value=0.1, max_value=0.9, format="%.2f", width=-1,
                            callback=self._on_confidence_change
                        )

            dpg.add_spacer(height=3)

            # PROCESS row (read-only stats)
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp, 
                           borders_innerV=True, borders_outerH=True, borders_outerV=True,
                           pad_outerX=True):
                dpg.add_table_column(init_width_or_weight=0.8)
                dpg.add_table_column(init_width_or_weight=1.5)
                dpg.add_table_column(init_width_or_weight=1.5)
                dpg.add_table_column(init_width_or_weight=1.5)
                
                with dpg.table_row():
                    dpg.add_text("PROCESS", color=(120, 200, 255))
                    with dpg.group(horizontal=True):
                        dpg.add_text("FPS:")
                        dpg.add_text("0.0", tag="fps_text", color=(0, 255, 100))
                    with dpg.group(horizontal=True):
                        dpg.add_text("Dancers:")
                        dpg.add_text("0", tag="dancers_text", color=(0, 255, 100))
                    with dpg.group(horizontal=True):
                        dpg.add_text("Bright:")
                        dpg.add_text("0", tag="brightness_text", color=(150, 150, 150))

            dpg.add_spacer(height=3)

            # TIMINGS row
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                           borders_innerV=True, borders_outerH=True, borders_outerV=True,
                           pad_outerX=True):
                dpg.add_table_column(init_width_or_weight=0.8)
                dpg.add_table_column(init_width_or_weight=1.0)
                dpg.add_table_column(init_width_or_weight=1.0)
                dpg.add_table_column(init_width_or_weight=1.0)
                dpg.add_table_column(init_width_or_weight=1.0)
                dpg.add_table_column(init_width_or_weight=1.0)

                with dpg.table_row():
                    dpg.add_text("TIMINGS", color=(120, 200, 255))
                    with dpg.group(horizontal=True):
                        dpg.add_text("Enh:")
                        dpg.add_text("--", tag="time_enhance", color=(180, 180, 180))
                    with dpg.group(horizontal=True):
                        dpg.add_text("YOLO:")
                        dpg.add_text("--", tag="time_yolo", color=(180, 180, 180))
                    with dpg.group(horizontal=True):
                        dpg.add_text("Track:")
                        dpg.add_text("--", tag="time_track", color=(180, 180, 180))
                    with dpg.group(horizontal=True):
                        dpg.add_text("Prev:")
                        dpg.add_text("--", tag="time_preview", color=(180, 180, 180))
                    with dpg.group(horizontal=True):
                        dpg.add_text("Total:")
                        dpg.add_text("--", tag="time_total", color=(180, 180, 180))

            # Keyboard shortcuts hint
            # dpg.add_spacer(height=6)
            # dpg.add_text("Keys: Q=quit P=preview R=reset E=enhance S=skeleton K=keypts B=bbox T=trails", color=(100, 100, 100))
    
    def _build_control_panel(self):
        """Build control panel with all settings."""
        with dpg.child_window(width=320, height=-1, tag="control_panel"):
            
            # === Detection Section ===
            with dpg.collapsing_header(label="Detection", default_open=True):
                # Max persons
                dpg.add_text("Max Persons")
                dpg.add_slider_int(
                    tag="max_persons_slider",
                    default_value=self.config.get('max_persons', 6),
                    min_value=1,
                    max_value=12,
                    callback=self._on_max_persons_change
                )
                
                dpg.add_spacer(height=10)
                
                # Person height calibration
                dpg.add_text("Person Height (pixels)")
                dpg.add_slider_int(
                    tag="person_height_slider",
                    default_value=self.config.get('person_height_px', 200),
                    min_value=50,
                    max_value=800,
                    format="%d px",
                    callback=self._on_person_height_change
                )
                dpg.add_text("(Adjust to match expected person size)", color=(150, 150, 150))
            
            dpg.add_spacer(height=10)
            
            
            # === Visualization Section ===
            with dpg.collapsing_header(label="Visualization", default_open=True):
                dpg.add_checkbox(
                    label="Skeleton [S]",
                    tag="skeleton_checkbox",
                    default_value=self.config.get('show_skeleton', True),
                    callback=lambda s, d: self._on_vis_toggle('skeleton', d)
                )
                dpg.add_checkbox(
                    label="Keypoints [K]",
                    tag="keypoints_checkbox",
                    default_value=self.config.get('show_keypoints', True),
                    callback=lambda s, d: self._on_vis_toggle('keypoints', d)
                )
                dpg.add_checkbox(
                    label="Bounding Box [B]",
                    tag="bbox_checkbox",
                    default_value=self.config.get('show_bbox', True),
                    callback=lambda s, d: self._on_vis_toggle('bbox', d)
                )
                dpg.add_checkbox(
                    label="Motion Trails [T]",
                    tag="trails_checkbox",
                    default_value=self.config.get('show_trails', True),
                    callback=lambda s, d: self._on_vis_toggle('trails', d)
                )
                dpg.add_checkbox(
                    label="Dancer IDs [I]",
                    tag="ids_checkbox",
                    default_value=self.config.get('show_ids', True),
                    callback=lambda s, d: self._on_vis_toggle('ids', d)
                )
            
            dpg.add_spacer(height=10)
            
            # === Tracker Section ===
            with dpg.collapsing_header(label="Tracker", default_open=True):
                dpg.add_text("Distance Threshold")
                dpg.add_slider_int(
                    tag="tracker_dist_slider",
                    default_value=self.config.get('tracker_distance', 300),
                    min_value=100,
                    max_value=500,
                    format="%d px",
                    callback=self._on_tracker_distance_change
                )
                
                dpg.add_text("Max Age (frames)")
                dpg.add_slider_int(
                    tag="tracker_age_slider",
                    default_value=self.config.get('tracker_max_age', 20),
                    min_value=5,
                    max_value=60,
                    callback=self._on_tracker_age_change
                )
                
                dpg.add_spacer(height=10)
                dpg.add_button(
                    label="Reset Tracker [R]",
                    width=-1,
                    callback=self._on_tracker_reset
                )
            
            dpg.add_spacer(height=10)
            
            # === OSC Section ===
            with dpg.collapsing_header(label="OSC Output", default_open=True):
                dpg.add_checkbox(
                    label="Enable OSC",
                    tag="osc_checkbox",
                    default_value=self.config.get('osc_enabled', True),
                    callback=self._on_osc_toggle
                )
                
                dpg.add_spacer(height=5)
                
                dpg.add_text("Target IP")
                dpg.add_input_text(
                    tag="osc_ip_input",
                    default_value=self.config.get('osc_ip', '127.0.0.1'),
                    width=-1,
                    callback=self._on_osc_config_change
                )
                
                dpg.add_text("Target Port")
                dpg.add_input_int(
                    tag="osc_port_input",
                    default_value=self.config.get('osc_port', 9000),
                    min_value=1024,
                    max_value=65535,
                    width=-1,
                    callback=self._on_osc_config_change
                )
            
            dpg.add_spacer(height=20)
            
            dpg.add_button(
                label="Quit [Q]",
                width=-1,
                callback=self._on_quit
            )
    
    # === Callbacks ===
    
    def _on_enhance_toggle(self, sender, value):
        if 'on_enhance_toggle' in self.callbacks:
            self.callbacks['on_enhance_toggle'](value)
        self._update_enhance_row_state(value, dpg.get_value('tbl_enhance_lite_checkbox'), bypass=False)
    
    def _on_enhance_lite_toggle(self, sender, value):
        if 'on_enhance_lite_toggle' in self.callbacks:
            self.callbacks['on_enhance_lite_toggle'](value)
        self._update_enhance_row_state(dpg.get_value('tbl_enhance_checkbox'), value, bypass=False)
    
    def _on_preview_toggle(self, sender, value):
        if 'on_preview_toggle' in self.callbacks:
            self.callbacks['on_preview_toggle'](value)
        self._update_preview_row_state(value)
    
    def _update_preview_row_state(self, enabled: bool):
        """Grey out PREVIEW row controls when disabled."""
        color = (200, 200, 200) if enabled else (80, 80, 80)
        dpg.configure_item("preview_tex_text", color=color)
        dpg.configure_item("preview_scale_label", color=color)
        dpg.configure_item("tbl_preview_scale_slider", enabled=enabled)
    
    def _update_enhance_row_state(self, enabled: bool, lite_mode: bool, bypass: bool = False):
        """Grey out ENHANCE row controls when disabled, lite mode, or bypassed due to high brightness."""
        color = (200, 200, 200) if enabled else (80, 80, 80)
        # Clahe and Gamma are greyed when: disabled, lite mode, or bypass (bright enough)
        clahe_color = (80, 80, 80) if (not enabled or lite_mode or bypass) else (200, 200, 200)
        gamma_color = (80, 80, 80) if (not enabled or bypass) else (200, 200, 200)
        
        dpg.configure_item("enhance_lite_label", color=color)
        dpg.configure_item("tbl_enhance_lite_checkbox", enabled=enabled)
        dpg.configure_item("enhance_gamma_label", color=gamma_color)
        dpg.configure_item("tbl_gamma_slider", enabled=(enabled and not bypass))
        
        dpg.configure_item("enhance_clahe_label", color=clahe_color)
        dpg.configure_item("tbl_clahe_slider", enabled=(enabled and not lite_mode and not bypass))

    def _on_preview_scale_change(self, sender, value):
        if 'on_preview_scale_change' in self.callbacks:
            self.callbacks['on_preview_scale_change'](value)
    
    def _on_upscale_change(self, sender, value):
        if 'on_upscale_change' in self.callbacks:
            self.callbacks['on_upscale_change'](value)
    
    def _set_upscale(self, value):
        dpg.set_value("upscale_slider", value)
        self._on_upscale_change(None, value)
    
    def _on_clahe_change(self, sender, value):
        if 'on_clahe_change' in self.callbacks:
            self.callbacks['on_clahe_change'](value)
    
    def _on_gamma_change(self, sender, value):
        if 'on_gamma_change' in self.callbacks:
            self.callbacks['on_gamma_change'](value)
    
    def _on_confidence_change(self, sender, value):
        if 'on_confidence_change' in self.callbacks:
            self.callbacks['on_confidence_change'](value)
    
    def _on_max_persons_change(self, sender, value):
        if 'on_max_persons_change' in self.callbacks:
            self.callbacks['on_max_persons_change'](value)
    
    def _on_model_change(self, sender, value):
        if 'on_model_change' in self.callbacks:
            self.callbacks['on_model_change'](value)
    
    def _on_fp16_toggle(self, sender, value):
        if 'on_fp16_toggle' in self.callbacks:
            self.callbacks['on_fp16_toggle'](value)
    
    def _on_frame_skip_change(self, sender, value):
        if 'on_frame_skip_change' in self.callbacks:
            self.callbacks['on_frame_skip_change'](value)
    
    def _on_camera_change(self, sender, value):
        if 'on_camera_change' in self.callbacks:
            self.callbacks['on_camera_change'](value)
    
    def _on_imgsz_change(self, sender, value):
        if 'on_imgsz_change' in self.callbacks:
            self.callbacks['on_imgsz_change'](int(value))
    
    def _on_person_height_change(self, sender, value):
        if 'on_person_height_change' in self.callbacks:
            self.callbacks['on_person_height_change'](int(value))
    
    def _on_vis_toggle(self, name, value):
        if 'on_visualization_toggle' in self.callbacks:
            self.callbacks['on_visualization_toggle'](name, value)
    
    def _on_tracker_distance_change(self, sender, value):
        if 'on_tracker_distance_change' in self.callbacks:
            self.callbacks['on_tracker_distance_change'](value)
    
    def _on_tracker_age_change(self, sender, value):
        if 'on_tracker_age_change' in self.callbacks:
            self.callbacks['on_tracker_age_change'](value)
    
    def _on_tracker_reset(self):
        if 'on_tracker_reset' in self.callbacks:
            self.callbacks['on_tracker_reset']()
    
    def _on_osc_toggle(self, sender, value):
        if 'on_osc_toggle' in self.callbacks:
            self.callbacks['on_osc_toggle'](value)
    
    def _on_osc_config_change(self, sender=None, value=None):
        if 'on_osc_config' in self.callbacks:
            ip = dpg.get_value("osc_ip_input")
            port = dpg.get_value("osc_port_input")
            self.callbacks['on_osc_config'](ip, port)
    
    def _on_save_config(self):
        if 'on_save_config' in self.callbacks:
            self.callbacks['on_save_config']()
    
    def _on_save_as_config(self):
        if 'on_save_as_config' in self.callbacks:
            self.callbacks['on_save_as_config']()
    
    def _on_load_config(self):
        if 'on_load_config' in self.callbacks:
            self.callbacks['on_load_config']()
    
    def _on_topbar_project_change(self, sender, value):
        """Handle project selection from top bar dropdown - load latest config for that project."""
        if value and value != self._current_project:
            if 'on_project_select' in self.callbacks:
                self.callbacks['on_project_select'](value)
    
    def _on_topbar_config_change(self, sender, value):
        """Handle config selection from top bar dropdown - load that config version."""
        if value and value != self._current_config_timestamp:
            if 'on_config_select' in self.callbacks:
                self.callbacks['on_config_select'](self._current_project, value)
    
    def _on_quit(self):
        if 'on_quit' in self.callbacks:
            self.callbacks['on_quit']()
        self.stop()
    
    # === Public Methods ===

    def resize_preview(self, width: int, height: int):
        """Resize preview texture and image when preview scale changes."""
        if width == self.video_width and height == self.video_height:
            return
        # Delete old texture to avoid alias collisions
        if dpg.does_item_exist(self.frame_texture_tag):
            dpg.delete_item(self.frame_texture_tag)

        # Create new unique texture tag
        import time
        self.frame_texture_tag = f"video_texture_{int(time.time()*1000)}"

        self.texture_width = width
        self.texture_height = height
        self.frame_buffer = np.zeros(
            self.texture_height * self.texture_width * 4,
            dtype=np.float32
        )

        # Recreate texture inside registry
        with dpg.texture_registry(show=False):
            self.frame_texture_id = dpg.add_raw_texture(
                width=self.texture_width,
                height=self.texture_height,
                default_value=self.frame_buffer,
                format=dpg.mvFormat_Float_rgba,
                tag=self.frame_texture_tag
            )

        # Update image widget and panel width to match new size
        if dpg.does_item_exist("video_image"):
            dpg.configure_item(
                "video_image",
                texture_tag=self.frame_texture_tag,
                width=self.video_width,
                height=self.video_height,
            )
        # Panel width stays based on display size
    
    def update_frame(self, frame: np.ndarray):
        """
        Update video texture with new frame.
        
        Args:
            frame: BGR image from OpenCV (will be converted to RGBA float)
        """
        if frame is None:
            return
        
        import cv2
        
        # Resize to display size if needed
        h, w = frame.shape[:2]
        if w != self.texture_width or h != self.texture_height:
            frame = cv2.resize(frame, (self.texture_width, self.texture_height))
        
        # Convert BGR to RGBA
        rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
        
        # Convert to float32 normalized, flatten, and ensure contiguous
        # Reuse pre-allocated buffer for speed
        if self.frame_buffer.size != rgba.size:
            # Texture likely re-created; allocate matching buffer
            self.frame_buffer = np.zeros(rgba.size, dtype=np.float32)
        np.copyto(self.frame_buffer, rgba.astype(np.float32).ravel() / 255.0)

        # Update texture - pass the buffer directly
        if dpg.does_item_exist(self.frame_texture_tag):
            dpg.set_value(self.frame_texture_tag, self.frame_buffer)
    
    def update_stats(
        self,
        fps: float,
        num_dancers: int,
        latency_ms: float = 0,
        brightness: float = 0,
        timing: dict = None,
        input_res: tuple = None,
        preview_tex: tuple = None,
        model_name: str = "",
        yolo_imgsz: int = 0,
        preview_enabled: bool = True,
        preview_render_scale: float = 1.0,
        osc_enabled: bool = False,
        osc_ip: str = "",
        osc_port: int = 0,
        enhance_bypassed: bool = False,
    ):
        """Update stats display."""
        self.fps = fps
        self.num_dancers = num_dancers
        self.latency_ms = latency_ms
        self.brightness = brightness

        if input_res:
            dpg.set_value("input_res_text", f"{input_res[0]}x{input_res[1]}")
        if preview_tex:
            dpg.set_value("preview_tex_text", f"{preview_tex[0]}x{preview_tex[1]}")

        dpg.set_value("fps_text", f"{fps:.1f}")
        dpg.set_value("dancers_text", str(num_dancers))
        dpg.set_value("brightness_text", f"{brightness:.0f}")

        # Update GPU stats (in top bar)
        gpu = get_gpu_stats()
        if gpu['util'] >= 0:
            # GPU util/temp - colored by temperature
            dpg.set_value("topbar_gpu_util_text", f"{gpu['util']}%/{gpu['temp']}°C")
            if gpu['temp'] < 70:
                dpg.configure_item("topbar_gpu_util_text", color=(100, 255, 100))
            elif gpu['temp'] < 85:
                dpg.configure_item("topbar_gpu_util_text", color=(255, 200, 0))
            else:
                dpg.configure_item("topbar_gpu_util_text", color=(255, 80, 80))
            # VRAM % - colored by usage
            vram_pct = gpu['vram_pct']
            dpg.set_value("topbar_gpu_vram_text", f"{vram_pct:.0f}%")
            if vram_pct < 50:
                dpg.configure_item("topbar_gpu_vram_text", color=(100, 255, 100))
            elif vram_pct < 80:
                dpg.configure_item("topbar_gpu_vram_text", color=(255, 200, 0))
            else:
                dpg.configure_item("topbar_gpu_vram_text", color=(255, 80, 80))
        else:
            dpg.set_value("topbar_gpu_util_text", "N/A")
            dpg.set_value("topbar_gpu_vram_text", "N/A")
        
        # Update save indicator (fade out after 2 seconds)
        import time
        if self._save_indicator_time > 0:
            elapsed = time.time() - self._save_indicator_time
            if elapsed > 2.0:
                dpg.set_value("save_indicator", "")
                self._save_indicator_time = 0

        # Update enhance row grey state based on bypass
        enhance_enabled = dpg.get_value('tbl_enhance_checkbox')
        lite_mode = dpg.get_value('tbl_enhance_lite_checkbox')
        self._update_enhance_row_state(enhance_enabled, lite_mode, bypass=enhance_bypassed)

        # Update timing breakdown
        if timing:
            enh = timing.get('enhance', 0)
            up = timing.get('upscale', 0)
            yolo = timing.get('yolo', 0)
            trk = timing.get('track', 0)
            pdraw = timing.get('preview_draw', 0)
            pup = timing.get('preview_upload', 0)
            total = timing.get('total', 0)
            
            # Preview timing: only update when non-zero to avoid flickering
            preview_time = pdraw + pup
            if preview_time > 0:
                self._last_preview_time = preview_time

            dpg.set_value("time_enhance", f"{enh:.0f}")
            dpg.set_value("time_yolo", f"{yolo:.0f}")
            dpg.set_value("time_track", f"{trk:.0f}")
            dpg.set_value("time_preview", f"{self._last_preview_time:.0f}")
            dpg.set_value("time_total", f"{total:.0f}")

            # Color code key timings
            def _colorize(tag, val, g=40, y=80):
                if val < g:
                    dpg.configure_item(tag, color=(100, 255, 100))
                elif val < y:
                    dpg.configure_item(tag, color=(255, 200, 0))
                else:
                    dpg.configure_item(tag, color=(255, 80, 80))

            _colorize("time_yolo", yolo)
            _colorize("time_enhance", enh, g=10, y=30)
            _colorize("time_preview", self._last_preview_time, g=5, y=15)

        # Update FPS color based on performance
        if fps >= 25:
            dpg.configure_item("fps_text", color=(0, 255, 100))
        elif fps >= 15:
            dpg.configure_item("fps_text", color=(255, 200, 0))
        else:
            dpg.configure_item("fps_text", color=(255, 80, 80))
    
    def sync_checkbox(self, name: str, value: bool):
        """Sync checkbox state (when changed via keyboard)."""
        tag_map = {
            'enhance': 'tbl_enhance_checkbox',
            'enhance_lite': 'tbl_enhance_lite_checkbox',
            'preview': 'tbl_preview_checkbox',
            'fp16': 'tbl_fp16_checkbox',
            'skeleton': 'skeleton_checkbox',
            'keypoints': 'keypoints_checkbox',
            'bbox': 'bbox_checkbox',
            'trails': 'trails_checkbox',
            'ids': 'ids_checkbox',
            'osc': 'osc_checkbox',
        }
        if name in tag_map:
            dpg.set_value(tag_map[name], value)
        # Update row grey state when toggling via keyboard
        if name == 'preview':
            self._update_preview_row_state(value)
        elif name == 'enhance':
            self._update_enhance_row_state(value, dpg.get_value('tbl_enhance_lite_checkbox'), bypass=False)
    
    def sync_slider(self, name: str, value: float):
        """Sync slider state (when changed via keyboard)."""
        tag_map = {
            'confidence': 'tbl_conf_slider',
            'clahe': 'tbl_clahe_slider',
            'gamma': 'tbl_gamma_slider',
            'preview_scale': 'tbl_preview_scale_slider',
            'frame_skip': 'tbl_frame_skip_slider',
            'max_persons': 'max_persons_slider',
            'person_height': 'person_height_slider',
            'tracker_distance': 'tracker_dist_slider',
            'tracker_max_age': 'tracker_age_slider',
        }
        if name in tag_map:
            dpg.set_value(tag_map[name], value)
    
    def sync_combo(self, name: str, value: str):
        """Sync combo box state."""
        tag_map = {
            'model': 'tbl_model_combo',
            'imgsz': 'tbl_imgsz_combo',
            'camera': 'tbl_camera_combo',
        }
        if name in tag_map:
            dpg.set_value(tag_map[name], value)
    
    def update_camera_sources(self, sources: list, current: str = "", unavailable: list = None):
        """Update camera source dropdown with available/unavailable cameras.
        
        Args:
            sources: List of camera source strings (e.g., ['0', '1', '/dev/video0'])
            current: Currently selected camera source
            unavailable: List of sources that are unavailable (shown greyed)
        """
        if unavailable is None:
            unavailable = []
        
        # Create display items with unavailable markers
        display_items = []
        for src in sources:
            if src in unavailable:
                display_items.append(f"{src} (unavailable)")
            else:
                display_items.append(src)
        
        if dpg.does_item_exist("tbl_camera_combo"):
            dpg.configure_item("tbl_camera_combo", items=display_items)
            # Set current value
            if current in unavailable:
                dpg.set_value("tbl_camera_combo", f"{current} (unavailable)")
            elif current:
                dpg.set_value("tbl_camera_combo", current)
    
    def sync_input(self, name: str, value):
        """Sync input field state."""
        tag_map = {
            'osc_ip': 'osc_ip_input',
            'osc_port': 'osc_port_input',
        }
        if name in tag_map:
            dpg.set_value(tag_map[name], value)
    
    # === Top Bar Methods ===
    
    def update_project_list(self, projects: list, current_project: str = ""):
        """Update the project dropdown in the top bar.
        
        Args:
            projects: List of project names (folder names)
            current_project: Currently active project name
        """
        self._projects_list = projects
        self._current_project = current_project
        if dpg.does_item_exist("topbar_project_combo"):
            dpg.configure_item("topbar_project_combo", items=projects)
            if current_project:
                dpg.set_value("topbar_project_combo", current_project)
    
    def update_config_list(self, config_files: list, current_config: str = ""):
        """Update the config dropdown in the top bar.
        
        Args:
            config_files: List of tuples (display_name, filename) for config history
            current_config: Currently active config display name
        """
        self._config_files_list = config_files
        self._current_config_timestamp = current_config
        
        # Extract display names for the dropdown
        display_names = [item[0] if isinstance(item, tuple) else item for item in config_files]
        
        if dpg.does_item_exist("topbar_config_combo"):
            dpg.configure_item("topbar_config_combo", items=display_names)
            if current_config:
                dpg.set_value("topbar_config_combo", current_config)
    
    def show_save_indicator(self, message: str = "Saved!"):
        """Show a brief save success indicator in the top bar."""
        import time
        self._save_indicator_time = time.time()
        if dpg.does_item_exist("save_indicator"):
            dpg.set_value("save_indicator", f"  ✓ {message}")
            dpg.configure_item("save_indicator", color=(100, 255, 100))
    
    def set_current_project(self, project_name: str):
        """Set the current project in the top bar dropdown."""
        self._current_project = project_name
        if dpg.does_item_exist("topbar_project_combo"):
            dpg.set_value("topbar_project_combo", project_name)
    
    def set_current_config(self, config_display: str):
        """Set the current config in the top bar dropdown."""
        self._current_config_timestamp = config_display
        if dpg.does_item_exist("topbar_config_combo"):
            dpg.set_value("topbar_config_combo", config_display)
    
    # === Config Save/Load Dialogs ===
    
    def show_save_config_dialog(self, default_name: str = "default"):
        """Show modal dialog for saving config with a project name."""
        # Delete existing dialog if present
        if dpg.does_item_exist("save_config_dialog"):
            dpg.delete_item("save_config_dialog")
        
        with dpg.window(
            label="Save Configuration",
            modal=True,
            tag="save_config_dialog",
            width=400,
            height=200,
            pos=[dpg.get_viewport_width() // 2 - 200, dpg.get_viewport_height() // 2 - 80],
            no_resize=True,
            no_move=False,
        ):
            dpg.add_text("Enter project name:")
            dpg.add_text("(Config will be saved with timestamp in project folder)", color=(150, 150, 150))
            dpg.add_spacer(height=5)
            dpg.add_input_text(
                tag="save_config_name_input",
                default_value=default_name,
                width=-1,
                hint="project name"
            )
            dpg.add_spacer(height=10)
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Save",
                    width=180,
                    callback=self._do_save_config
                )
                dpg.add_button(
                    label="Cancel",
                    width=180,
                    callback=lambda: dpg.delete_item("save_config_dialog")
                )
    
    def _do_save_config(self):
        """Execute save config from dialog."""
        import re
        name = dpg.get_value("save_config_name_input")
        if name:
            # Clean name: remove special chars, replace spaces with underscores
            name = re.sub(r'[^\w\s-]', '', name).strip()
            name = re.sub(r'[\s]+', '_', name)
            if name:
                if 'on_do_save_config' in self.callbacks:
                    self.callbacks['on_do_save_config'](name)
        dpg.delete_item("save_config_dialog")
    
    def show_load_config_dialog(self, config_dir: str, current_project: str = ""):
        """Show modal dialog for loading config - first shows projects, then history."""
        import os
        
        # Delete existing dialog if present
        if dpg.does_item_exist("load_config_dialog"):
            dpg.delete_item("load_config_dialog")
        
        # Store for callbacks
        self._load_config_dir = config_dir
        self._load_current_project = current_project
        
        # Get list of project folders
        projects = []
        if os.path.exists(config_dir):
            for item in sorted(os.listdir(config_dir)):
                item_path = os.path.join(config_dir, item)
                if os.path.isdir(item_path):
                    # Check if it has any json files
                    json_files = [f for f in os.listdir(item_path) if f.endswith('.json')]
                    if json_files:
                        projects.append((item, len(json_files)))
        
        with dpg.window(
            label="Load Configuration",
            modal=True,
            tag="load_config_dialog",
            width=500,
            height=480,
            pos=[dpg.get_viewport_width() // 2 - 250, dpg.get_viewport_height() // 2 - 240],
            no_resize=True,
            no_move=False,
        ):
            if not projects:
                dpg.add_text("No saved projects found.", color=(255, 200, 100))
                dpg.add_text("Save a config first to create a project.", color=(150, 150, 150))
            else:
                dpg.add_text("Select a project:", color=(120, 200, 255))
                dpg.add_spacer(height=5)
                
                # Store project list for deselection logic
                self._project_selectables = []
                
                with dpg.child_window(height=140, border=True, tag="project_list_window"):
                    for project_name, file_count in projects:
                        is_current = (project_name == current_project)
                        label = f"{project_name} ({file_count} saves)" + (" [current]" if is_current else "")
                        sel = dpg.add_selectable(
                            label=label,
                            default_value=is_current,
                            callback=self._on_project_select,
                            user_data=project_name
                        )
                        self._project_selectables.append((sel, project_name))
                
                dpg.add_spacer(height=10)
                dpg.add_text("Config history:", tag="history_label", color=(120, 200, 255))
                dpg.add_spacer(height=5)
                
                with dpg.child_window(height=150, border=True, tag="config_history_window"):
                    dpg.add_text("Select a project above...", tag="history_placeholder", color=(100, 100, 100))
                
                dpg.add_spacer(height=10)
                dpg.add_button(
                    label="Load Selected",
                    width=-1,
                    tag="load_selected_btn",
                    callback=self._do_load_selected_config,
                    enabled=False
                )
                
                # Auto-select current project if available
                if current_project and any(p[0] == current_project for p in projects):
                    self._populate_config_history(current_project)
            
            dpg.add_spacer(height=5)
            dpg.add_button(
                label="Cancel",
                width=-1,
                callback=lambda: dpg.delete_item("load_config_dialog")
            )
    
    def _on_project_select(self, sender, value, user_data):
        """Handle project selection - populate config history and enforce single selection."""
        project_name = user_data
        
        # Enforce single selection: deselect all others, select this one
        if hasattr(self, '_project_selectables'):
            for sel_id, proj_name in self._project_selectables:
                if dpg.does_item_exist(sel_id):
                    dpg.set_value(sel_id, proj_name == project_name)
        
        self._populate_config_history(project_name)
    
    def _populate_config_history(self, project_name: str):
        """Populate the config history list for a project."""
        import os
        
        self._selected_project = project_name
        project_dir = os.path.join(self._load_config_dir, project_name)
        
        # Get config files sorted by name (newest first due to timestamp in name)
        config_files = []
        if os.path.exists(project_dir):
            for f in sorted(os.listdir(project_dir), reverse=True):
                if f.endswith('.json'):
                    config_files.append(f)
        
        # Clear and repopulate history window
        if dpg.does_item_exist("config_history_window"):
            dpg.delete_item("config_history_window", children_only=True)
            
            # Reset selectable tracking
            self._config_selectables = []
            
            if config_files:
                self._selected_config_file = config_files[0]  # Pre-select latest
                
                for i, f in enumerate(config_files):
                    # Parse filename: project_YYYYMMDD_HHMMSS.json -> readable date
                    display_name = f.replace('.json', '')
                    parts = display_name.rsplit('_', 2)
                    if len(parts) >= 3:
                        date_str = parts[-2]
                        time_str = parts[-1]
                        try:
                            # Format: 20251208_143022 -> 2025-12-08 14:30:22
                            formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:]}"
                            display_name = formatted
                        except:
                            pass
                    
                    is_latest = (i == 0)
                    label = display_name + (" [latest]" if is_latest else "")
                    
                    sel = dpg.add_selectable(
                        label=label,
                        default_value=is_latest,
                        callback=self._on_config_history_select,
                        user_data=f,
                        parent="config_history_window"
                    )
                    self._config_selectables.append((sel, f))
                
                # Enable load button
                if dpg.does_item_exist("load_selected_btn"):
                    dpg.configure_item("load_selected_btn", enabled=True)
            else:
                dpg.add_text("No configs in this project", color=(100, 100, 100), parent="config_history_window")
    
    def _on_config_history_select(self, sender, value, user_data):
        """Handle config file selection in history - enforce single selection."""
        self._selected_config_file = user_data
        
        # Enforce single selection: deselect all others, select this one
        if hasattr(self, '_config_selectables'):
            for sel_id, filename in self._config_selectables:
                if dpg.does_item_exist(sel_id):
                    dpg.set_value(sel_id, filename == user_data)
    
    def _do_load_selected_config(self):
        """Load the selected config file."""
        import os
        if hasattr(self, '_selected_project') and hasattr(self, '_selected_config_file'):
            filepath = os.path.join(self._load_config_dir, self._selected_project, self._selected_config_file)
            if 'on_do_load_config' in self.callbacks:
                self.callbacks['on_do_load_config'](filepath)
            dpg.delete_item("load_config_dialog")
    
    def _on_config_file_select(self, sender, value, user_data):
        """Handle config file selection (legacy - kept for compatibility)."""
        import os
        filename = user_data
        filepath = os.path.join(self._load_config_dir, filename)
        if 'on_do_load_config' in self.callbacks:
            self.callbacks['on_do_load_config'](filepath)
        dpg.delete_item("load_config_dialog")
    
    def setup(self, width: int = 1340, height: int = 900):
        """Setup viewport and prepare for rendering."""
        dpg.create_viewport(
            title="WallDance Control Panel",
            width=width,
            height=height,
            min_width=900,
            min_height=900
        )
        dpg.setup_dearpygui()
        dpg.set_primary_window("main_window", True)
    
    def start(self):
        """Start the GUI (blocking)."""
        dpg.show_viewport()
        dpg.start_dearpygui()
        dpg.destroy_context()
    
    def render_frame(self) -> bool:
        """
        Render single frame (non-blocking).
        
        Returns:
            True if window is still open, False if closed
        """
        if not dpg.is_dearpygui_running():
            return False
        dpg.render_dearpygui_frame()
        return True
    
    def stop(self):
        """Stop the GUI."""
        dpg.stop_dearpygui()
    
    def is_running(self) -> bool:
        """Check if GUI is still running."""
        return dpg.is_dearpygui_running()
    
    def get_key_press(self) -> Optional[str]:
        """
        Check for keyboard input (basic support).
        Note: DearPyGui keyboard handling is different, we'll handle this in main loop.
        """
        return None
