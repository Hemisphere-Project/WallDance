"""
DearPyGui-based control panel for WallDance.
Provides real-time parameter adjustment with sliders, checkboxes, and buttons.
"""

import os
from typing import Any, Callable, Dict, Optional

import dearpygui.dearpygui as dpg
import numpy as np

from gui_builder import build_ui, create_texture, setup_theme
from gui_icons import Icons

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
        self.camera_running = config.get('camera_running', True)
        
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
        setup_theme(self)

    def _create_texture(self):
        create_texture(self)

    def _build_ui(self):
        build_ui(self)
    
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
    
    def _on_preview_cap_toggle(self, sender, value):
        if 'on_preview_cap_toggle' in self.callbacks:
            self.callbacks['on_preview_cap_toggle'](value)
    
    def _update_preview_row_state(self, enabled: bool):
        """Grey out PREVIEW row controls when disabled."""
        color = (200, 200, 200) if enabled else (80, 80, 80)
        dpg.configure_item("preview_tex_text", color=color)
        dpg.configure_item("preview_scale_label", color=color)
        dpg.configure_item("tbl_preview_scale_slider", enabled=enabled)
        dpg.configure_item("preview_cap_label", color=color)
        dpg.configure_item("tbl_preview_cap_checkbox", enabled=enabled)
    
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
    
    def _on_trt_toggle(self, sender, value):
        if 'on_trt_toggle' in self.callbacks:
            self.callbacks['on_trt_toggle'](value)
    
    def _on_fp16_toggle(self, sender, value):
        if 'on_fp16_toggle' in self.callbacks:
            self.callbacks['on_fp16_toggle'](value)
    
    def _on_frame_skip_change(self, sender, value):
        if 'on_frame_skip_change' in self.callbacks:
            self.callbacks['on_frame_skip_change'](value)
    
    def _on_camera_change(self, sender, value):
        if 'on_camera_change' in self.callbacks:
            self.callbacks['on_camera_change'](value)

    def _on_camera_toggle(self, sender=None, value=None):
        if 'on_camera_toggle' in self.callbacks:
            self.callbacks['on_camera_toggle']()
    
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
        if value.startswith("+ "):
            # Trigger save-as dialog for new project
            if 'on_save_as_config' in self.callbacks:
                self.callbacks['on_save_as_config']()
            # Reset combo to current project
            if self._current_project and dpg.does_item_exist("topbar_project_combo"):
                dpg.set_value("topbar_project_combo", self._current_project)
        elif value and value != self._current_project:
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
    
    # === Recording Callbacks ===
    
    def _on_rec_live(self):
        """Switch to live camera input."""
        if 'on_rec_live' in self.callbacks:
            self.callbacks['on_rec_live']()
    
    def _on_rec_toggle(self):
        """Toggle recording mode."""
        if 'on_rec_toggle' in self.callbacks:
            self.callbacks['on_rec_toggle']()
    
    def _on_rec_slot_click(self, slot: int):
        """Handle slot button click. Ctrl+click for history menu."""
        ctrl_held = dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)
        if 'on_rec_slot_click' in self.callbacks:
            self.callbacks['on_rec_slot_click'](slot, ctrl_held)
    
    def update_recording_ui(self, state: str, current_slot: int, slots_info: list, 
                            recording_frames: int = 0, playback_frame: int = 0, playback_total: int = 0):
        """Update recording UI state.
        
        Args:
            state: 'live', 'armed', 'recording', or 'playing'
            current_slot: Active slot (0 = none)
            slots_info: List of (slot_id, has_recordings) for slots 1-9
            recording_frames: Number of frames recorded (when recording)
            playback_frame: Current playback frame (when playing)
            playback_total: Total playback frames (when playing)
        """
        # Update status text
        if dpg.does_item_exist("rec_status_text"):
            if state == "live":
                dpg.set_value("rec_status_text", "LIVE")
                dpg.configure_item("rec_status_text", color=(80, 200, 80))
            elif state == "armed":
                dpg.set_value("rec_status_text", "REC ARMED - Select slot")
                dpg.configure_item("rec_status_text", color=(255, 180, 80))
            elif state == "recording":
                dpg.set_value("rec_status_text", f"REC >> Slot {current_slot}")
                dpg.configure_item("rec_status_text", color=(255, 80, 80))
            elif state == "playing":
                dpg.set_value("rec_status_text", f"PLAY << Slot {current_slot}")
                dpg.configure_item("rec_status_text", color=(80, 180, 255))
        
        # Update LIVE button theme
        if dpg.does_item_exist("rec_live_btn"):
            if state in ("live", "armed"):
                dpg.bind_item_theme("rec_live_btn", self._rec_live_active_theme)
            elif state == "playing":
                dpg.bind_item_theme("rec_live_btn", self._rec_live_playing_theme)
            else:
                dpg.bind_item_theme("rec_live_btn", self._rec_live_theme)
        
        # Update REC button theme and enabled state
        if dpg.does_item_exist("rec_rec_btn"):
            if state == "recording":
                dpg.bind_item_theme("rec_rec_btn", self._rec_btn_recording_theme)
            elif state == "armed":
                dpg.bind_item_theme("rec_rec_btn", self._rec_btn_recording_theme)  # Show red when armed
            elif state == "playing":
                dpg.bind_item_theme("rec_rec_btn", self._rec_btn_disabled_theme)  # Grey when playing
            else:
                dpg.bind_item_theme("rec_rec_btn", self._rec_btn_theme)
            # REC button enabled in live, armed, or recording states
            dpg.configure_item("rec_rec_btn", enabled=(state in ("live", "armed", "recording")))
        
        # Update frame counter
        if dpg.does_item_exist("rec_frame_counter"):
            if state == "recording":
                dpg.set_value("rec_frame_counter", f"({recording_frames} frames)")
            else:
                dpg.set_value("rec_frame_counter", "")
        
        # Update slot buttons
        for slot_id, has_recordings in slots_info:
            tag = f"rec_slot_{slot_id}_btn"
            if dpg.does_item_exist(tag):
                if state == "recording" and slot_id == current_slot:
                    dpg.bind_item_theme(tag, self._slot_recording_theme)
                elif state == "playing" and slot_id == current_slot:
                    dpg.bind_item_theme(tag, self._slot_playing_theme)
                elif has_recordings:
                    dpg.bind_item_theme(tag, self._slot_has_recording_theme)
                else:
                    dpg.bind_item_theme(tag, self._slot_empty_theme)
        
        # Update playback progress
        if dpg.does_item_exist("rec_playback_group"):
            dpg.configure_item("rec_playback_group", show=(state == "playing"))
        if dpg.does_item_exist("rec_playback_progress"):
            dpg.set_value("rec_playback_progress", f"{playback_frame}/{playback_total}")
    
    def show_slot_history_menu(self, slot: int, recordings: list, callback):
        """Show a popup menu with recording history for a slot.
        
        Args:
            slot: Slot number
            recordings: List of (display_name, filepath) tuples
            callback: Function to call with selected filepath
        """
        menu_tag = f"slot_{slot}_history_menu"
        
        # Delete existing menu
        if dpg.does_item_exist(menu_tag):
            dpg.delete_item(menu_tag)
        
        if not recordings:
            return
        
        with dpg.window(
            label=f"Slot {slot} History",
            tag=menu_tag,
            popup=True,
            no_title_bar=True,
            autosize=True,
        ):
            for display, filepath in recordings:
                dpg.add_button(
                    label=display,
                    width=200,
                    callback=lambda s, a, u: (callback(u), dpg.delete_item(menu_tag)),
                    user_data=filepath,
                )
    
    # === Public Methods ===

    def resize_preview(self, width: int, height: int):
        """Resize preview texture and image when preview scale changes."""
        # Compare against current texture dimensions, not display dimensions
        if width == self.texture_width and height == self.texture_height:
            return
        
        print(f"GUI resize_preview: {self.texture_width}x{self.texture_height} -> {width}x{height}")
        
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

        # Update image widget to use new texture (display size stays the same)
        if dpg.does_item_exist("video_image"):
            dpg.configure_item(
                "video_image",
                texture_tag=self.frame_texture_tag,
                width=self.video_width,
                height=self.video_height,
            )
    
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
        camera_running: bool = True,
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
        self.update_gpu_stats()
        
        # Update save indicator (fade out after 2 seconds)
        import time
        if self._save_indicator_time > 0:
            elapsed = time.time() - self._save_indicator_time
            if elapsed > 2.0:
                if dpg.does_item_exist("save_indicator"):
                    dpg.configure_item("save_indicator", show=False)
                self._save_indicator_time = 0

        # Update enhance row grey state based on bypass
        enhance_enabled = dpg.get_value('tbl_enhance_checkbox')
        lite_mode = dpg.get_value('tbl_enhance_lite_checkbox')
        self._update_enhance_row_state(enhance_enabled, lite_mode, bypass=enhance_bypassed)

        # Status badges
        cam_color = (120, 255, 120) if camera_running else (255, 120, 120)
        if dpg.does_item_exist("badge_cam"):
            dpg.set_value("badge_cam", "ON" if camera_running else "OFF")
            dpg.configure_item("badge_cam", color=cam_color)

        osc_color = (120, 255, 120) if osc_enabled else (255, 120, 120)
        if dpg.does_item_exist("badge_osc"):
            dpg.set_value("badge_osc", "ON" if osc_enabled else "OFF")
            dpg.configure_item("badge_osc", color=osc_color)

        if dpg.does_item_exist("badge_model"):
            dpg.set_value("badge_model", model_name or "--")

        if fps >= 25:
            fps_color = (120, 255, 120)      # green
        elif fps >= 20:
            fps_color = (255, 220, 120)      # yellow
        elif fps >= 15:
            fps_color = (255, 170, 80)       # orange
        else:
            fps_color = (255, 120, 120)      # red
        if dpg.does_item_exist("badge_fps"):
            dpg.set_value("badge_fps", f"{fps:.1f}")
            dpg.configure_item("badge_fps", color=fps_color)

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
            'preview_cap': 'tbl_preview_cap_checkbox',
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
    
    def update_model_dropdown(self, model_name: str):
        """Update model dropdown to show current model."""
        if dpg.does_item_exist("tbl_model_combo"):
            dpg.set_value("tbl_model_combo", model_name)
    
    def update_engine_type_badge(self, is_tensorrt: bool):
        """Update the engine type badge in the top bar.
        
        Args:
            is_tensorrt: True if using TensorRT engine, False for PyTorch
        """
        if dpg.does_item_exist("badge_engine_type"):
            if is_tensorrt:
                dpg.set_value("badge_engine_type", "[TRT]")
                dpg.configure_item("badge_engine_type", color=(100, 255, 150))  # Green for TensorRT
            else:
                dpg.set_value("badge_engine_type", "[PT]")
                dpg.configure_item("badge_engine_type", color=(255, 220, 100))  # Yellow for PyTorch
    
    def set_trt_checkbox(self, enabled: bool):
        """Set the TensorRT checkbox state.
        
        Args:
            enabled: True to check, False to uncheck
        """
        if dpg.does_item_exist("tbl_trt_checkbox"):
            dpg.set_value("tbl_trt_checkbox", enabled)
    
    def update_gpu_stats(self):
        """Update GPU stats in the top bar (util, temp, VRAM).
        
        Can be called independently to update GPU stats during model loading.
        """
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

        # Disable toggle if current source is unavailable
        if dpg.does_item_exist("camera_toggle_btn"):
            disabled = current in unavailable
            dpg.configure_item("camera_toggle_btn", enabled=not disabled)
            if disabled:
                dpg.configure_item("camera_toggle_btn", label="Start")

    def update_camera_status(self, running: bool, source: str = ""):
        """Update camera toggle label and badge color."""
        self.camera_running = running
        if dpg.does_item_exist("camera_toggle_btn"):
            dpg.configure_item("camera_toggle_btn", label="Stop" if running else "Start")
        cam_color = (120, 255, 120) if running else (255, 120, 120)
        if dpg.does_item_exist("badge_cam"):
            dpg.set_value("badge_cam", "ON" if running else "OFF")
            dpg.configure_item("badge_cam", color=cam_color)
    
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
        # Add "+ new project" at the beginning
        items = ["+ new project"] + list(projects)
        if dpg.does_item_exist("topbar_project_combo"):
            dpg.configure_item("topbar_project_combo", items=items)
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
            dpg.configure_item("save_indicator", show=True, color=(100, 255, 100))
    
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
                dpg.add_text("Select a project:", color=(120, 200, 140))
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
                dpg.add_text("Config history:", tag="history_label", color=(120, 200, 140))
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
    
    # === Model Loading Progress Modal ===
    
    def _cleanup_model_modals(self):
        """Clean up any existing model-related modals."""
        for tag in ["model_loading_modal", "tensorrt_prompt_modal"]:
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
    
    def show_model_loading_modal(self, message: str = "Loading model..."):
        """Show blocking modal for model loading/export operations."""
        # Clean up any existing modals first
        self._cleanup_model_modals()
        
        vp_width = dpg.get_viewport_width()
        vp_height = dpg.get_viewport_height()
        modal_width = 500
        modal_height = 200
        
        with dpg.window(
            label="Model Loading",
            modal=True,
            tag="model_loading_modal",
            width=modal_width,
            height=modal_height,
            pos=[vp_width // 2 - modal_width // 2, vp_height // 2 - modal_height // 2],
            no_resize=True,
            no_move=True,
            no_close=True,
            no_collapse=True,
        ):
            dpg.add_spacer(height=10)
            dpg.add_text(message, tag="model_loading_message", wrap=480)
            dpg.add_spacer(height=15)
            dpg.add_progress_bar(
                tag="model_loading_progress",
                default_value=0.0,
                width=-1,
                height=25,
            )
            dpg.add_spacer(height=8)
            dpg.add_text("", tag="model_loading_detail", color=(150, 150, 150), wrap=480)
    
    def update_model_loading_progress(self, message: str, progress: float, detail: str = "", animate: bool = False):
        """Update the model loading modal progress.
        
        Args:
            message: Main status message
            progress: Progress value 0.0 to 1.0 (ignored if animate=True)
            detail: Secondary detail text
            animate: If True, show animated/cycling progress bar
        """
        if dpg.does_item_exist("model_loading_message"):
            dpg.set_value("model_loading_message", message)
        if dpg.does_item_exist("model_loading_progress"):
            if animate:
                # Animated progress - use time to create cycling effect
                import time
                t = time.time() % 2.0  # 2 second cycle
                # Create a ping-pong effect between 0.2 and 0.8
                if t < 1.0:
                    anim_progress = 0.2 + (t * 0.6)
                else:
                    anim_progress = 0.8 - ((t - 1.0) * 0.6)
                dpg.set_value("model_loading_progress", anim_progress)
            else:
                dpg.set_value("model_loading_progress", max(0.0, min(1.0, progress)))
        if dpg.does_item_exist("model_loading_detail"):
            dpg.set_value("model_loading_detail", detail)
    
    def hide_model_loading_modal(self):
        """Hide and destroy the model loading modal."""
        if dpg.does_item_exist("model_loading_modal"):
            dpg.delete_item("model_loading_modal")
    
    def show_tensorrt_prompt(self, model_name: str, callback):
        """Show dialog asking whether to build TensorRT engine or use PyTorch.
        
        Args:
            model_name: Name of the model
            callback: Function to call with result (True=build TRT, False=use PT)
        """
        # Clean up any existing modals first
        self._cleanup_model_modals()
        
        vp_width = dpg.get_viewport_width()
        vp_height = dpg.get_viewport_height()
        modal_width = 450
        modal_height = 180
        
        def on_build_trt():
            if dpg.does_item_exist("tensorrt_prompt_modal"):
                dpg.delete_item("tensorrt_prompt_modal")
            callback(True)
        
        def on_use_pytorch():
            if dpg.does_item_exist("tensorrt_prompt_modal"):
                dpg.delete_item("tensorrt_prompt_modal")
            callback(False)
        
        with dpg.window(
            label="TensorRT Engine",
            modal=True,
            tag="tensorrt_prompt_modal",
            width=modal_width,
            height=modal_height,
            pos=[vp_width // 2 - modal_width // 2, vp_height // 2 - modal_height // 2],
            no_resize=True,
            no_move=True,
            no_close=True,
            no_collapse=True,
        ):
            dpg.add_spacer(height=10)
            dpg.add_text(f"No TensorRT engine found for {model_name}.", wrap=420)
            dpg.add_spacer(height=5)
            dpg.add_text(
                "Build TensorRT engine for faster inference (5-10 min)?\n"
                "Or use PyTorch directly (slower but instant).",
                wrap=420,
                color=(180, 180, 180)
            )
            dpg.add_spacer(height=15)
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Build TensorRT (5-10 min)",
                    callback=on_build_trt,
                    width=180,
                )
                dpg.add_spacer(width=20)
                dpg.add_button(
                    label="Use PyTorch",
                    callback=on_use_pytorch,
                    width=120,
                )
    
    def hide_tensorrt_prompt(self):
        """Hide the TensorRT prompt dialog."""
        if dpg.does_item_exist("tensorrt_prompt_modal"):
            dpg.delete_item("tensorrt_prompt_modal")
    
    def show_toast(self, message: str, duration: float = 3.0, color: tuple = (255, 200, 100)):
        """Show a temporary toast notification at top-left of preview area.
        
        Args:
            message: The message to display
            duration: How long to show the toast (seconds)
            color: Text color (R, G, B)
        """
        import threading
        
        # Remove existing toast if any
        if dpg.does_item_exist("toast_window"):
            dpg.delete_item("toast_window")
        
        # Position at top-left of preview area (below top bar)
        # Top bar is ~30px, so position toast just below it
        toast_x = 15
        toast_y = 38
        
        # Create toast window (compact, no padding)
        with dpg.window(
            label="",
            tag="toast_window",
            no_title_bar=True,
            no_resize=True,
            no_move=True,
            no_collapse=True,
            no_scrollbar=True,
            autosize=True,
            pos=(toast_x, toast_y),
            min_size=(10, 10),
        ):
            dpg.add_text(message, tag="toast_text", color=color)
        
        # Auto-hide after duration
        def hide_toast():
            import time
            time.sleep(duration)
            if dpg.does_item_exist("toast_window"):
                try:
                    dpg.delete_item("toast_window")
                except:
                    pass
        
        threading.Thread(target=hide_toast, daemon=True).start()

    def setup(self, width: int = 1340, height: int = 900):
        """Setup viewport and prepare for rendering."""
        dpg.create_viewport(
            title="WallDance Control Panel",
            width=width,
            height=height,
            min_width=900,
            min_height=900,
            vsync=False  # Disable vsync to prevent throttling when window is in background
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
