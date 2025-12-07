"""
DearPyGui-based control panel for WallDance.
Provides real-time parameter adjustment with sliders, checkboxes, and buttons.
"""

import dearpygui.dearpygui as dpg
import numpy as np
from typing import Callable, Dict, Any, Optional


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
        self.texture_registry = None
        
        # Video dimensions
        self.video_width = config.get('video_width', 960)
        self.video_height = config.get('video_height', 540)
        
        # Stats
        self.fps = 0
        self.num_dancers = 0
        self.latency_ms = 0
        self.brightness = 0
        
        # Initialize DearPyGui
        dpg.create_context()
        self._setup_theme()
        self._create_texture()
        self._build_ui()
        
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
            self.video_height * self.video_width * 4, 
            dtype=np.float32
        )
        
        with dpg.texture_registry(show=False):
            self.frame_texture_id = dpg.add_raw_texture(
                width=self.video_width,
                height=self.video_height,
                default_value=self.frame_buffer,
                format=dpg.mvFormat_Float_rgba,
                tag="video_texture"
            )
        
        print(f"Texture created: {self.video_width}x{self.video_height}")
    
    def _build_ui(self):
        """Build the main UI layout."""
        # Main window
        with dpg.window(tag="main_window", label="WallDance Control Panel"):
            with dpg.group(horizontal=True):
                # Left side: Video preview
                self._build_video_panel()
                
                # Right side: Controls
                self._build_control_panel()
    
    def _build_video_panel(self):
        """Build video preview panel."""
        with dpg.child_window(width=self.video_width + 20, height=-1, tag="video_panel"):
            # Video frame
            dpg.add_image("video_texture", width=self.video_width, height=self.video_height)
            
            dpg.add_separator()
            
            # Stats bar
            with dpg.group(horizontal=True):
                dpg.add_text("FPS:", color=(150, 150, 150))
                dpg.add_text("0.0", tag="fps_text", color=(0, 255, 100))
                dpg.add_spacer(width=20)
                dpg.add_text("Dancers:", color=(150, 150, 150))
                dpg.add_text("0", tag="dancers_text", color=(0, 255, 100))
                dpg.add_spacer(width=20)
                dpg.add_text("Latency:", color=(150, 150, 150))
                dpg.add_text("0 ms", tag="latency_text", color=(0, 255, 100))
                dpg.add_spacer(width=20)
                dpg.add_text("Brightness:", color=(150, 150, 150))
                dpg.add_text("0", tag="brightness_text", color=(150, 150, 150))
            
            # Timing breakdown bar
            with dpg.group(horizontal=True):
                dpg.add_text("Timing:", color=(100, 100, 100))
                dpg.add_text("Enh:", color=(120, 120, 120))
                dpg.add_text("--", tag="time_enhance", color=(180, 180, 180))
                dpg.add_text("Up:", color=(120, 120, 120))
                dpg.add_text("--", tag="time_upscale", color=(180, 180, 180))
                dpg.add_text("YOLO:", color=(120, 120, 120))
                dpg.add_text("--", tag="time_yolo", color=(180, 180, 180))
                dpg.add_text("Trk:", color=(120, 120, 120))
                dpg.add_text("--", tag="time_track", color=(180, 180, 180))
            
            # Dancer indicators
            dpg.add_spacer(height=5)
            with dpg.group(horizontal=True, tag="dancer_indicators"):
                pass  # Will be populated dynamically
            
            # Keyboard shortcuts hint
            dpg.add_spacer(height=10)
            dpg.add_text("Keyboard: Q=quit, P=preview, R=reset, E=enhance", color=(100, 100, 100))
    
    def _build_control_panel(self):
        """Build control panel with all settings."""
        with dpg.child_window(width=320, height=-1, tag="control_panel"):
            
            # === Detection Section ===
            with dpg.collapsing_header(label="Detection", default_open=True):
                # Model selection dropdown
                dpg.add_text("YOLO Model")
                dpg.add_combo(
                    items=["yolo11n-pose", "yolo11s-pose", "yolo11m-pose", "yolo11l-pose", "yolo11x-pose",
                           "yolov8n-pose", "yolov8s-pose", "yolov8m-pose", "yolov8l-pose", "yolov8x-pose"],
                    tag="model_combo",
                    default_value=self.config.get('model', 'yolo11m-pose'),
                    width=-1,
                    callback=self._on_model_change
                )
                dpg.add_text("(n=fast, x=accurate, v8=older)", color=(120, 120, 120))
                
                dpg.add_spacer(height=5)
                
                # FP16 half precision
                dpg.add_checkbox(
                    label="FP16 Half Precision (faster)",
                    tag="fp16_checkbox",
                    default_value=self.config.get('fp16', False),
                    callback=self._on_fp16_toggle
                )
                
                dpg.add_spacer(height=5)
                
                # Confidence slider
                dpg.add_text("Confidence Threshold")
                dpg.add_slider_float(
                    tag="conf_slider",
                    default_value=self.config.get('confidence', 0.25),
                    min_value=0.1,
                    max_value=0.9,
                    format="%.2f",
                    callback=self._on_confidence_change
                )
                
                # Max persons
                dpg.add_text("Max Persons")
                dpg.add_slider_int(
                    tag="max_persons_slider",
                    default_value=self.config.get('max_persons', 6),
                    min_value=1,
                    max_value=12,
                    callback=self._on_max_persons_change
                )
                
                dpg.add_spacer(height=5)
                
                # Frame skip
                dpg.add_text("Frame Skip (0=none)")
                dpg.add_slider_int(
                    tag="frame_skip_slider",
                    default_value=self.config.get('frame_skip', 0),
                    min_value=0,
                    max_value=4,
                    callback=self._on_frame_skip_change
                )
                
                dpg.add_spacer(height=5)
                
                # YOLO imgsz
                cam_w = self.config.get('camera_width', 1920)
                cam_h = self.config.get('camera_height', 1080)
                dpg.add_text(f"YOLO Input Size (camera: {cam_w}x{cam_h})")
                dpg.add_combo(
                    items=["640", "800", "960", "1280", "1920", "2560"],
                    tag="imgsz_combo",
                    default_value=str(self.config.get('yolo_imgsz', 640)),
                    width=-1,
                    callback=self._on_imgsz_change
                )
                dpg.add_text("(use ≤ camera size for close-up)", color=(150, 150, 150))
                
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
            
            # === Enhancement Section ===
            with dpg.collapsing_header(label="Enhancement", default_open=True):
                dpg.add_checkbox(
                    label="Enable Enhancement",
                    tag="enhance_checkbox",
                    default_value=self.config.get('enhance_enabled', True),
                    callback=self._on_enhance_toggle
                )
                
                dpg.add_checkbox(
                    label="Lite Mode (gamma only, faster)",
                    tag="enhance_lite_checkbox",
                    default_value=self.config.get('enhance_lite', False),
                    callback=self._on_enhance_lite_toggle
                )
                
                dpg.add_spacer(height=5)
                
                dpg.add_text("CLAHE Clip Limit")
                dpg.add_slider_float(
                    tag="clahe_slider",
                    default_value=self.config.get('clahe_clip', 3.0),
                    min_value=1.0,
                    max_value=6.0,
                    format="%.1f",
                    callback=self._on_clahe_change
                )
                
                dpg.add_text("Gamma Correction")
                dpg.add_slider_float(
                    tag="gamma_slider",
                    default_value=self.config.get('gamma', 1.2),
                    min_value=0.5,
                    max_value=2.5,
                    format="%.2f",
                    callback=self._on_gamma_change
                )
            
            dpg.add_spacer(height=10)
            
            # === Upscaling Section ===
            with dpg.collapsing_header(label="Upscaling", default_open=True):
                dpg.add_text("Upscale Factor")
                dpg.add_slider_float(
                    tag="upscale_slider",
                    default_value=self.config.get('upscale_factor', 2.0),
                    min_value=1.0,
                    max_value=4.0,
                    format="%.1fx",
                    callback=self._on_upscale_change
                )
                
                # Quick buttons
                with dpg.group(horizontal=True):
                    dpg.add_button(label="1x", width=50, callback=lambda: self._set_upscale(1.0))
                    dpg.add_button(label="1.5x", width=50, callback=lambda: self._set_upscale(1.5))
                    dpg.add_button(label="2x", width=50, callback=lambda: self._set_upscale(2.0))
                    dpg.add_button(label="2.5x", width=50, callback=lambda: self._set_upscale(2.5))
                    dpg.add_button(label="3x", width=50, callback=lambda: self._set_upscale(3.0))
            
            dpg.add_spacer(height=10)
            
            # === Visualization Section ===
            with dpg.collapsing_header(label="Visualization", default_open=True):
                dpg.add_checkbox(
                    label="Preview (push video to GUI) [P]",
                    tag="preview_checkbox",
                    default_value=self.config.get('preview_enabled', True),
                    callback=self._on_preview_toggle
                )
                dpg.add_text("(Disable to measure raw FPS)", color=(150, 150, 150))
                
                dpg.add_spacer(height=5)
                
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
            with dpg.collapsing_header(label="Tracker", default_open=False):
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
            with dpg.collapsing_header(label="OSC Output", default_open=False):
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
            
            # === Actions ===
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Save Config",
                    width=145,
                    callback=self._on_save_config
                )
                dpg.add_button(
                    label="Load Config",
                    width=145,
                    callback=self._on_load_config
                )
            
            dpg.add_spacer(height=10)
            
            dpg.add_button(
                label="Quit [Q]",
                width=-1,
                callback=self._on_quit
            )
    
    # === Callbacks ===
    
    def _on_enhance_toggle(self, sender, value):
        if 'on_enhance_toggle' in self.callbacks:
            self.callbacks['on_enhance_toggle'](value)
    
    def _on_enhance_lite_toggle(self, sender, value):
        if 'on_enhance_lite_toggle' in self.callbacks:
            self.callbacks['on_enhance_lite_toggle'](value)
    
    def _on_preview_toggle(self, sender, value):
        if 'on_preview_toggle' in self.callbacks:
            self.callbacks['on_preview_toggle'](value)
    
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
    
    def _on_load_config(self):
        if 'on_load_config' in self.callbacks:
            self.callbacks['on_load_config']()
    
    def _on_quit(self):
        if 'on_quit' in self.callbacks:
            self.callbacks['on_quit']()
        self.stop()
    
    # === Public Methods ===
    
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
        if w != self.video_width or h != self.video_height:
            frame = cv2.resize(frame, (self.video_width, self.video_height))
        
        # Convert BGR to RGBA
        rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
        
        # Convert to float32 normalized, flatten, and ensure contiguous
        # Reuse pre-allocated buffer for speed
        np.copyto(
            self.frame_buffer,
            rgba.astype(np.float32).ravel() / 255.0
        )
        
        # Update texture - pass the buffer directly
        dpg.set_value("video_texture", self.frame_buffer)
    
    def update_stats(self, fps: float, num_dancers: int, latency_ms: float = 0, brightness: float = 0, timing: dict = None):
        """Update stats display."""
        self.fps = fps
        self.num_dancers = num_dancers
        self.latency_ms = latency_ms
        self.brightness = brightness
        
        dpg.set_value("fps_text", f"{fps:.1f}")
        dpg.set_value("dancers_text", str(num_dancers))
        dpg.set_value("latency_text", f"{latency_ms:.0f} ms")
        dpg.set_value("brightness_text", f"{brightness:.0f}")
        
        # Update timing breakdown
        if timing:
            dpg.set_value("time_enhance", f"{timing.get('enhance', 0):.0f}")
            dpg.set_value("time_upscale", f"{timing.get('upscale', 0):.0f}")
            dpg.set_value("time_yolo", f"{timing.get('yolo', 0):.0f}")
            dpg.set_value("time_track", f"{timing.get('track', 0):.0f}")
            
            # Color code YOLO time (biggest bottleneck)
            yolo_time = timing.get('yolo', 0)
            if yolo_time < 40:
                dpg.configure_item("time_yolo", color=(100, 255, 100))
            elif yolo_time < 80:
                dpg.configure_item("time_yolo", color=(255, 200, 0))
            else:
                dpg.configure_item("time_yolo", color=(255, 80, 80))
            
            # Color code enhance time
            enhance_time = timing.get('enhance', 0)
            if enhance_time < 10:
                dpg.configure_item("time_enhance", color=(100, 255, 100))
            elif enhance_time < 30:
                dpg.configure_item("time_enhance", color=(255, 200, 0))
            else:
                dpg.configure_item("time_enhance", color=(255, 80, 80))
        
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
            'enhance': 'enhance_checkbox',
            'preview': 'preview_checkbox',
            'skeleton': 'skeleton_checkbox',
            'keypoints': 'keypoints_checkbox',
            'bbox': 'bbox_checkbox',
            'trails': 'trails_checkbox',
            'ids': 'ids_checkbox',
            'osc': 'osc_checkbox',
        }
        if name in tag_map:
            dpg.set_value(tag_map[name], value)
    
    def sync_slider(self, name: str, value: float):
        """Sync slider state (when changed via keyboard)."""
        tag_map = {
            'upscale': 'upscale_slider',
            'confidence': 'conf_slider',
            'clahe': 'clahe_slider',
            'gamma': 'gamma_slider',
        }
        if name in tag_map:
            dpg.set_value(tag_map[name], value)
    
    def setup(self, width: int = 1340, height: int = 640):
        """Setup viewport and prepare for rendering."""
        dpg.create_viewport(
            title="WallDance Control Panel",
            width=width,
            height=height,
            min_width=800,
            min_height=400
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
