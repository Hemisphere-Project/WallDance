"""
DearPyGui-based control panel for WallDance.
Provides real-time parameter adjustment with sliders, checkboxes, and buttons.
"""

import os, sys
import subprocess
import time
from typing import Any, Callable, Dict, Optional

import cv2
import dearpygui.dearpygui as dpg
import numpy as np

from gui_builder import build_ui, create_texture, setup_theme, load_icon_font, SystemState, scaled, CONTROL_PANEL_WIDTH
from gui_constants import (
    TEXT_NORMAL, TEXT_MUTED, TEXT_DIM, TEXT_HINT, TEXT_FAINT,
    HEADING_GREEN, OK_GREEN, BRIGHT_GREEN, WARN_AMBER, WARN_ORANGE,
    ERROR_SOFT, ALERT_RED,
    TOAST_POS, VIEWPORT_BASE_W, VIEWPORT_BASE_H, VIEWPORT_MIN,
    LAYOUT_H_PAD, LAYOUT_V_MARGIN, LAYOUT_V_FALLBACK,
)
from config import DANCER_COLORS
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
    """Get GPU utilization, temperature, power draw, and VRAM usage."""
    if not _GPU_AVAILABLE:
        return {'util': -1, 'temp': -1, 'power': -1, 'vram_pct': -1}
    try:
        util = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
        temp = pynvml.nvmlDeviceGetTemperature(_GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
        mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
        vram_pct = (mem.used / mem.total) * 100
        # Get power draw in watts (returned in milliwatts)
        try:
            power_mw = pynvml.nvmlDeviceGetPowerUsage(_GPU_HANDLE)
            power_w = power_mw / 1000
        except Exception:
            power_w = -1
        return {'util': util.gpu, 'temp': temp, 'power': power_w, 'vram_pct': vram_pct}
    except Exception:
        return {'util': -1, 'temp': -1, 'power': -1, 'vram_pct': -1}


def get_display_scale() -> float:
    """
    Detect display scale factor based on screen resolution and system DPI settings.
    Returns a scale factor for DPI-aware rendering.
    
    Can be overridden via WALLDANCE_UI_SCALE environment variable.
    
    Supports: Windows, Linux (GNOME, KDE), macOS (basic)
    """
    if hasattr(get_display_scale, "_cached"):
        return get_display_scale._cached
    # Allow manual override via environment variable
    env_scale = os.environ.get('WALLDANCE_UI_SCALE')
    if env_scale:
        try:
            scale = float(env_scale)
            print(f"[GUI] Using UI scale from environment: {scale}")
            return scale
        except ValueError:
            print(f"[GUI] Invalid WALLDANCE_UI_SCALE value: {env_scale}, using auto-detect")
    
    screen_width = 1920  # default
    system_scale = 1.0
    
    # Platform-specific detection
    if sys.platform == 'win32':
        # Windows DPI detection
        try:
            import ctypes
            # Make process DPI aware
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass
            
            user32 = ctypes.windll.user32
            screen_width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            screen_height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
            print(f"[GUI] Detected display resolution: {screen_width}x{screen_height}")
            
            # Get DPI scale from device context
            try:
                hdc = user32.GetDC(0)
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
                user32.ReleaseDC(0, hdc)
                system_scale = dpi / 96.0
                print(f"[GUI] Windows DPI: {dpi}, system scale: {system_scale}")
            except Exception as e:
                print(f"[GUI] Could not get Windows DPI: {e}")
        except Exception as e:
            print(f"[GUI] Windows display detection failed: {e}")
    
    elif sys.platform == 'darwin':
        # macOS - basic detection (Retina displays typically 2x)
        try:
            result = subprocess.run(
                ['system_profiler', 'SPDisplaysDataType'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                output = result.stdout
                for line in output.split('\n'):
                    if 'Resolution' in line and 'x' in line:
                        parts = line.split(':')[1].strip().split()
                        if len(parts) >= 3 and parts[1] == 'x':
                            screen_width = int(parts[0])
                            print(f"[GUI] Detected macOS display width: {screen_width}")
                        if 'Retina' in line:
                            system_scale = 2.0
                            print(f"[GUI] Detected Retina display")
                        break
        except Exception as e:
            print(f"[GUI] macOS display detection failed: {e}")
    
    else:
        # Linux with xrandr
        try:
            result = subprocess.run(['xrandr'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if '*' in line:  # Current resolution marked with *
                        parts = line.split()
                        resolution = parts[0]  # e.g., "3840x2560"
                        screen_width = int(resolution.split('x')[0])
                        print(f"[GUI] Detected display resolution: {resolution}")
                        break
        except Exception as e:
            print(f"[GUI] Could not detect display resolution: {e}")
        
        # Try to detect system DPI scale from various sources (Linux)
        try:
            # Check GDK_SCALE (GNOME/GTK)
            gdk_scale = os.environ.get('GDK_SCALE')
            if gdk_scale:
                system_scale = float(gdk_scale)
                print(f"[GUI] Detected GDK_SCALE: {system_scale}")
            else:
                # Check QT_SCALE_FACTOR (KDE/Qt)
                qt_scale = os.environ.get('QT_SCALE_FACTOR')
                if qt_scale:
                    system_scale = float(qt_scale)
                    print(f"[GUI] Detected QT_SCALE_FACTOR: {system_scale}")
                else:
                    # Try gsettings for GNOME scaling factor
                    try:
                        result = subprocess.run(
                            ['gsettings', 'get', 'org.gnome.desktop.interface', 'text-scaling-factor'],
                            capture_output=True, text=True, timeout=2
                        )
                        if result.returncode == 0:
                            system_scale = float(result.stdout.strip())
                            print(f"[GUI] Detected GNOME text-scaling-factor: {system_scale}")
                    except Exception:
                        pass
        except Exception as e:
            print(f"[GUI] Could not detect system DPI scale: {e}")
    
    # Calculate final scale based on resolution and system settings.
    #
    # With SetProcessDpiAwareness(2), the app is DPI-aware and renders at
    # physical pixel resolution.  Windows will NOT auto-scale our window.
    # We must therefore apply the system DPI factor ourselves.
    #
    # Strategy:
    #   1. Compute the "logical" resolution to pick a resolution-tier base.
    #   2. Take the max of (resolution-tier base, system DPI scale).
    #      This ensures both high-resolution monitors AND user DPI
    #      preferences (e.g. 125 %) are respected.
    logical_width = screen_width / system_scale if system_scale > 0 else screen_width

    if logical_width >= 3200:       # 4K logical (e.g. 3840 @ 100 %, or 5120 @ 125 %)
        base_scale = 2.0
    elif logical_width >= 2200:     # QHD logical (e.g. 2560 @ 100 %)
        base_scale = 1.25
    else:                            # 1080p or anything ≤ FHD
        base_scale = 1.0

    # Honour the user's OS scaling preference: on FHD @ 125 % the base_scale
    # would be 1.0 but the user expects 25 % larger UI elements.
    final_scale = max(base_scale, system_scale)
    print(f"[GUI] Screen width: {screen_width}, System scale: {system_scale}, "
          f"Logical width: {logical_width:.0f}, Base: {base_scale}, Using UI scale: {final_scale}")
    
    get_display_scale._cached = final_scale
    return final_scale


class WallDanceGUI:
    """Modern GUI for WallDance using DearPyGui."""
    
    def __init__(self, config: Dict[str, Any], callbacks: Dict[str, Callable]):
        """
        Initialize GUI.
        
        Args:
            config: Initial configuration values
            callbacks: Dict of callback functions for parameter changes
                - on_enhance_toggle(enabled)
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
        
        # Camera native dimensions (for aspect ratio computation)
        self._camera_width = config.get('camera_width', 1920) or 1920
        self._camera_height = config.get('camera_height', 1080) or 1080

        # Display dimensions (on-screen area) - will be recomputed by _recompute_layout
        self.video_width = config.get('video_width', 960)
        self.video_height = config.get('video_height', 540)
        # Texture/render dimensions (actual resolution uploaded)
        self.texture_width = config.get('texture_width', self.video_width)
        self.texture_height = config.get('texture_height', self.video_height)

        # Layout state – computed by _recompute_layout()
        self._middle_height = self.video_height + scaled(8)
        self._fitted_render_scale = min(
            self.video_width / max(self._camera_width, 1),
            self.video_height / max(self._camera_height, 1),
            1.0,
        )
        self._layout_dirty = False
        
        # Stats
        self.fps = 0
        self.num_dancers = 0
        self.latency_ms = 0
        self.brightness = 0
        
        # Smoothed timing for preview (avoid flickering 0 values)
        self._last_preview_time = 0

        # Toast expiry deadline; expired by render_frame() on the main
        # thread only — DPG is not thread-safe.
        self._toast_deadline = 0.0

        # Modals registered for live centering: tag -> (width, height)
        self._centered_modals: Dict[str, tuple] = {}
        
        # Project/config state for top bar
        self._projects_list = []
        self._config_files_list = []
        self._current_project = ""
        self._current_config_timestamp = ""
        self._save_indicator_time = 0  # For showing save success feedback
        
        # System state (Phase 3 - simplified 2-state system)
        self._system_state = SystemState.RUN
        
        # DPI scaling for high-resolution displays (4K)
        self._dpi_scale = get_display_scale()

        # Expert mode: reveals developer-grade knob panels (Ctrl+Shift+E or WD_EXPERT=1).
        self.expert_mode = os.environ.get("WD_EXPERT", "0") == "1"
        self._expert_only_tags = ["section_background", "enhance_expert_group",
                                  "detection_expert_group"]

        # Initialize DearPyGui
        dpg.create_context()
        
        # Apply global font scale for DPI awareness
        if self._dpi_scale != 1.0:
            dpg.set_global_font_scale(self._dpi_scale)
        
        self._setup_theme()
        self._create_texture()
        self._build_ui()
        
        # Advanced-drawer section accordion (mutual exclusion). ROI -> phase 1 and
        # OSC -> phase 2 are promoted to the phase panels, so they leave the
        # accordion; the exclusion-mask editor was never in it.
        self._section_headers = [
            "section_background", "section_enhancement", "section_model",
            "section_detection", "section_preview", "section_osc",
        ]
        self._last_open_section = "section_input"  # Input section starts open

        # Phase rail (OPERATOR_V2 Track O): the right panel shows one phase at a
        # time; the rail drives existing commands via the phase panels.
        self._phases = ["rig", "profile", "aim", "calibrate", "verify", "live"]
        self._active_phase = "rig"
        self._exit_edit_modes = None  # wired by the adapter to roi.exit_edit_modes
        self._on_phase_select(self._active_phase)  # initial panel + rail highlight

        # Alerts strip (OPERATOR_V2 §2.3c): named warnings render in one strip.
        self._alerts: Dict[str, str] = {}
        
        # Set initial grey state for disabled rows
        self._update_preview_row_state(self.config.get('preview_enabled', True))
        self._update_enhance_row_state(
            self.config.get('enhance_enabled', False),
            bypass=False
        )
        self.update_ids_exposure_warning(self.config.get('ids_exposure_us', 0.0))
        
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
        self._update_enhance_row_state(value, bypass=False)
    
    def _on_enhance_lite_toggle(self, sender, value):
        if 'on_enhance_lite_toggle' in self.callbacks:
            self.callbacks['on_enhance_lite_toggle'](value)
        en_val = dpg.get_value('adv_enhance_checkbox') if dpg.does_item_exist('adv_enhance_checkbox') else False
        self._update_enhance_row_state(en_val, value, bypass=False)
    
    def _on_enhance_force_toggle(self, sender, value):
        if 'on_enhance_force_toggle' in self.callbacks:
            self.callbacks['on_enhance_force_toggle'](value)
        # Update row state: when Force is enabled, threshold slider should be greyed out
        en_val = dpg.get_value('adv_enhance_checkbox') if dpg.does_item_exist('adv_enhance_checkbox') else False
        self._update_enhance_row_state(en_val, bypass=False)

    def _on_greyscale_toggle(self, sender, value):
        if 'on_greyscale_toggle' in self.callbacks:
            self.callbacks['on_greyscale_toggle'](value)

    def _on_brightness_threshold_change(self, sender, value):
        if 'on_brightness_threshold_change' in self.callbacks:
            self.callbacks['on_brightness_threshold_change'](value)

    def _on_denoise_change(self, sender, value):
        if 'on_denoise_change' in self.callbacks:
            self.callbacks['on_denoise_change'](value)

    # --- Background subtraction callbacks ---
    def _on_bg_capture(self, sender=None, value=None):
        if 'on_bg_capture' in self.callbacks:
            self.callbacks['on_bg_capture']()

    def _on_bg_enable_toggle(self, sender, value):
        if 'on_bg_enable_toggle' in self.callbacks:
            self.callbacks['on_bg_enable_toggle'](value)

    def _on_bg_clear(self, sender=None, value=None):
        if 'on_bg_clear' in self.callbacks:
            self.callbacks['on_bg_clear']()

    def _on_bg_sensitivity_change(self, sender, value):
        if 'on_bg_sensitivity_change' in self.callbacks:
            self.callbacks['on_bg_sensitivity_change'](value)

    def update_bg_status(self, has_reference: bool, enabled: bool, 
                         fg_ratio: float = 0.0, is_mismatched: bool = False):
        """Update the background subtraction status text and color."""
        if not dpg.does_item_exist("bg_status_text"):
            return
        if not has_reference:
            dpg.set_value("bg_status_text", "No reference captured")
            dpg.configure_item("bg_status_text", color=TEXT_DIM)
        elif is_mismatched:
            pct = int(fg_ratio * 100)
            dpg.set_value("bg_status_text", f"!! MISMATCH ({pct}% fg) - Recapture or disable")
            dpg.configure_item("bg_status_text", color=ALERT_RED)
        elif enabled:
            pct = int(fg_ratio * 100)
            dpg.set_value("bg_status_text", f"Active ({pct}% foreground)")
            dpg.configure_item("bg_status_text", color=BRIGHT_GREEN)
        else:
            dpg.set_value("bg_status_text", "Reference ready (disabled)")
            dpg.configure_item("bg_status_text", color=(180, 180, 100))

    def _on_preview_toggle(self, sender, value):
        if 'on_preview_toggle' in self.callbacks:
            self.callbacks['on_preview_toggle'](value)
        self._update_preview_row_state(value)
    
    def _on_input_fps_cap_toggle(self, sender, value):
        if 'on_input_fps_cap_toggle' in self.callbacks:
            self.callbacks['on_input_fps_cap_toggle'](value)

    def _on_preview_cap_toggle(self, sender, value):
        if 'on_preview_cap_toggle' in self.callbacks:
            self.callbacks['on_preview_cap_toggle'](value)

    def _on_roi_toggle(self, sender, value):
        if 'on_roi_toggle' in self.callbacks:
            self.callbacks['on_roi_toggle'](value)

    def _on_roi_reset(self, sender=None, value=None):
        if 'on_roi_reset' in self.callbacks:
            self.callbacks['on_roi_reset']()

    def _on_mask_edit_toggle(self, sender=None, value=None):
        if 'on_mask_edit_toggle' in self.callbacks:
            self.callbacks['on_mask_edit_toggle']()

    def _on_mask_clear(self, sender=None, value=None):
        if 'on_mask_clear' in self.callbacks:
            self.callbacks['on_mask_clear']()

    def set_mask_edit_state(self, editing: bool):
        """Flip the mask Edit button label to reflect the editor state."""
        if dpg.does_item_exist("mask_edit_btn"):
            dpg.configure_item("mask_edit_btn",
                               label="Done" if editing else "Edit")

    def update_exclusion_mask_text(self, effective: int, auto: int,
                                   manual_add: int, manual_remove: int):
        """Update the exclusion-mask cell count line."""
        if not dpg.does_item_exist("mask_cells_text"):
            return
        detail = f"{effective} cell(s)"
        if manual_add or manual_remove:
            detail += f"  (auto {auto}, +{manual_add}, -{manual_remove})"
        dpg.set_value("mask_cells_text", detail)
        dpg.configure_item("mask_cells_text",
                           color=(80, 220, 120) if effective else TEXT_MUTED)

    def update_roi_rect_text(self, x: int, y: int, w: int, h: int, edit_mode: bool = False):
        """Update the read-only ROI rect display (replaces the numeric inputs)."""
        if not dpg.does_item_exist("roi_rect_text"):
            return
        suffix = "  (editing)" if edit_mode else ""
        dpg.set_value("roi_rect_text", f"{x},{y}  {w}x{h}{suffix}")
        dpg.configure_item("roi_rect_text", color=(80, 220, 120) if edit_mode else TEXT_MUTED)

    def set_expert_mode(self, enabled: bool):
        """Show/hide developer-grade knob panels."""
        self.expert_mode = bool(enabled)
        for tag in self._expert_only_tags:
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, show=self.expert_mode)
        self.show_toast(
            "Expert mode ON" if self.expert_mode else "Expert mode OFF",
            duration=2.0,
            color=WARN_AMBER if self.expert_mode else TEXT_MUTED,
        )

    # --- Phase rail + drawers (OPERATOR_V2 Track O) ----------------------------
    def _on_phase_select(self, phase_id: str):
        """Show the selected phase's right-panel; highlight its rail button.

        Pure UI navigation -- the phase panels host the existing action buttons
        (calibrate / dancers / pool / all / standby / run), which still submit
        the same commands. No pipeline or command path changes here.
        """
        prev = getattr(self, "_active_phase", None)
        # Leaving Rig: drop any ROI/mask edit mode so a later stray preview
        # click can't mutate the ROI/mask while another phase is showing.
        if prev == "rig" and phase_id != "rig" and self._exit_edit_modes:
            try:
                self._exit_edit_modes()
            except Exception:
                pass
        self._active_phase = phase_id
        for pid in self._phases:
            panel = f"phase_panel_{pid}"
            if dpg.does_item_exist(panel):
                dpg.configure_item(panel, show=(pid == phase_id))
            btn = f"phase_btn_{pid}"
            if dpg.does_item_exist(btn):
                theme = (self._btn_run_active_theme if pid == phase_id
                         else self._btn_standby_theme)
                dpg.bind_item_theme(btn, theme)
        # Entering Calibrate: populate the inline evidence pool (same read-only
        # fetch the old POOL button did; renders via show_calib2_dialog).
        if phase_id == "calibrate" and 'on_view_calib2_pool' in self.callbacks:
            self.callbacks['on_view_calib2_pool']()
        # Entering Verify: auto-run the readiness glance (cheap, off-thread).
        if phase_id == "verify" and 'on_check_readiness' in self.callbacks:
            self.callbacks['on_check_readiness']()

    def _toggle_window(self, tag: str):
        """Flip a floating drawer window's visibility."""
        if dpg.does_item_exist(tag):
            shown = dpg.get_item_configuration(tag).get("show", False)
            dpg.configure_item(tag, show=not shown)

    def _toggle_advanced_drawer(self):
        """Show/hide the floating Advanced (numeric knobs) panel."""
        self._toggle_window("advanced_drawer_window")

    def _toggle_recordings_drawer(self):
        """Show/hide the floating Recordings panel (off the live surface)."""
        self._toggle_window("recordings_drawer_window")

    # --- Alerts strip (OPERATOR_V2 §2.3c) -------------------------------------
    def push_alert(self, key: str, message: str):
        """Add or replace a named alert; rendered in the alerts strip.

        Keyed so a recurring condition (e.g. 'trt_fallback') updates in place
        rather than stacking. The Track-C feeders (imgsz-fail / TRT / health)
        that call this are gated; the API + strip exist now so they have a home.
        """
        self._alerts[key] = message
        self._refresh_alerts()

    def clear_alert(self, key: str):
        """Remove a named alert (no-op if absent)."""
        if self._alerts.pop(key, None) is not None:
            self._refresh_alerts()

    def _refresh_alerts(self):
        """Render the current alert set into the strip."""
        if not dpg.does_item_exist("alerts_text"):
            return
        if self._alerts:
            dpg.set_value("alerts_text", "   ".join(self._alerts.values()))
            dpg.configure_item("alerts_text", color=WARN_ORANGE)
        else:
            dpg.set_value("alerts_text", "(none)")
            dpg.configure_item("alerts_text", color=TEXT_HINT)

    def _update_preview_row_state(self, enabled: bool):
        """Grey out PREVIEW row controls when disabled."""
        if dpg.does_item_exist("preview_tex_text"):
            color = (200, 200, 200) if enabled else TEXT_FAINT
            dpg.configure_item("preview_tex_text", color=color)
        if dpg.does_item_exist("adv_preview_cap_checkbox"):
            dpg.configure_item("adv_preview_cap_checkbox", enabled=enabled)
    
    def _update_enhance_row_state(self, enabled: bool, lite_mode: bool = False, bypass: bool = False):
        """Grey out ENHANCE row controls when disabled or bypassed due to high brightness."""
        # Check if Force is engaged - if so, ignore bypass
        force_enabled = False
        if dpg.does_item_exist("adv_enhance_force_checkbox"):
            force_enabled = dpg.get_value("adv_enhance_force_checkbox")
        
        # When force is enabled, bypass is ignored
        effective_bypass = bypass and not force_enabled
        
        # User requested to be able to move sliders even when disabled
        if dpg.does_item_exist("adv_gamma_slider"):
            dpg.configure_item("adv_gamma_slider", enabled=True)
        if dpg.does_item_exist("adv_brightness_threshold_slider"):
            dpg.configure_item("adv_brightness_threshold_slider", enabled=(enabled and not force_enabled))
        if dpg.does_item_exist("adv_clahe_slider"):
            dpg.configure_item("adv_clahe_slider", enabled=True)

    def _on_preview_scale_change(self, sender, value):
        if 'on_preview_scale_change' in self.callbacks:
            self.callbacks['on_preview_scale_change'](value)
    
    def _on_clahe_change(self, sender, value):
        if 'on_clahe_change' in self.callbacks:
            self.callbacks['on_clahe_change'](value)
    
    def _on_gamma_change(self, sender, value):
        if 'on_gamma_change' in self.callbacks:
            self.callbacks['on_gamma_change'](value)
    
    def _on_confidence_change(self, sender, value):
        if 'on_confidence_change' in self.callbacks:
            self.callbacks['on_confidence_change'](value)

    def _on_sensitivity_change(self, sender, value):
        if 'on_sensitivity_change' in self.callbacks:
            self.callbacks['on_sensitivity_change'](float(value))

    def _on_motion_sensitivity_change(self, sender, value):
        if 'on_motion_sensitivity_change' in self.callbacks:
            self.callbacks['on_motion_sensitivity_change'](float(value))

    def _on_gap_bridging_change(self, sender, value):
        if 'on_gap_bridging_change' in self.callbacks:
            self.callbacks['on_gap_bridging_change'](float(value))

    def _on_output_smoothing_change(self, sender, value):
        if 'on_output_smoothing_change' in self.callbacks:
            self.callbacks['on_output_smoothing_change'](int(value))

    def _on_box_clamp_toggle(self, sender, value):
        if 'on_box_clamp_toggle' in self.callbacks:
            self.callbacks['on_box_clamp_toggle'](bool(value))

    def _on_lagged_tap_toggle(self, sender, value):
        if 'on_lagged_tap_toggle' in self.callbacks:
            self.callbacks['on_lagged_tap_toggle'](bool(value))

    def _on_check_readiness(self, *args):
        if 'on_check_readiness' in self.callbacks:
            self.callbacks['on_check_readiness']()

    def _on_dryrun(self, *args):
        if 'on_dryrun' in self.callbacks:
            self.callbacks['on_dryrun']()

    def _on_max_persons_change(self, sender, value):
        if 'on_max_persons_change' in self.callbacks:
            self.callbacks['on_max_persons_change'](value)
    
    def _on_model_change(self, sender, value):
        if 'on_model_change' in self.callbacks:
            self.callbacks['on_model_change'](value)
    
    def _on_trt_toggle(self, sender, value):
        if 'on_trt_toggle' in self.callbacks:
            self.callbacks['on_trt_toggle'](value)

    def _on_trt_rebuild(self, sender=None, value=None):
        if 'on_trt_rebuild' in self.callbacks:
            self.callbacks['on_trt_rebuild']()

    def _on_profile_switch(self, sender, value):
        if 'on_profile_switch' in self.callbacks:
            self.callbacks['on_profile_switch'](str(value).lower())

    def set_active_profile(self, name: str):
        """Sync the top-bar lighting-profile radio (no callback fired)."""
        if dpg.does_item_exist("profile_switch_radio"):
            dpg.set_value("profile_switch_radio", str(name).capitalize())

    def update_trt_banner(self, message: Optional[str], exporting: bool = False):
        """Red alert band over the preview when TensorRT was requested but is not in use.

        message=None hides the banner; exporting=True shows a progress notice
        (no rebuild button).
        """
        if not dpg.does_item_exist("trt_banner_window"):
            return
        if not message:
            dpg.configure_item("trt_banner_window", show=False)
            return
        dpg.set_value("trt_banner_text", message)
        dpg.configure_item("trt_banner_text", color=(255, 220, 140) if exporting else (255, 230, 230))
        dpg.configure_item("trt_banner_btn", show=not exporting)
        dpg.configure_item("trt_banner_window", show=True)
    
    def _on_ids_ratio_change(self, sender, value):
        if 'on_ids_ratio_change' in self.callbacks:
            self.callbacks['on_ids_ratio_change'](float(value))

    def _on_ids_gain_change(self, sender, value):
        if 'on_ids_gain_change' in self.callbacks:
            self.callbacks['on_ids_gain_change'](float(value))

    def _on_ids_exposure_change(self, sender, value):
        self.update_ids_exposure_warning(float(value))
        if 'on_ids_exposure_change' in self.callbacks:
            self.callbacks['on_ids_exposure_change'](float(value))

    def _on_camera_change(self, sender, value):
        if 'on_camera_change' in self.callbacks:
            self.callbacks['on_camera_change'](value)
    
    def _on_camera_refresh(self, sender=None, value=None):
        if 'on_camera_refresh' in self.callbacks:
            self.callbacks['on_camera_refresh']()

    def _on_ids_settings_toggle(self, sender=None, value=None):
        """Toggle visibility of IDS hardware settings (gain/exposure)."""
        tag = "ids_hw_settings_group"
        if dpg.does_item_exist(tag):
            visible = dpg.get_item_configuration(tag)["show"]
            dpg.configure_item(tag, show=not visible)
    
    def _on_imgsz_change(self, sender, value):
        if 'on_imgsz_change' in self.callbacks:
            self.callbacks['on_imgsz_change'](int(value))
    
    def _on_person_height_change(self, sender, value):
        if 'on_person_height_change' in self.callbacks:
            self.callbacks['on_person_height_change'](int(value))

    def _on_calibrate(self):
        """Calibrate button → ask the app to run a Go-Live scene calibration."""
        if 'on_calibrate' in self.callbacks:
            self.callbacks['on_calibrate']()

    def _on_calibrate_all(self):
        """ALL button → open the guided Calibrate All wizard (GUI-local)."""
        if 'on_calibrate_all' in self.callbacks:
            self.callbacks['on_calibrate_all']()

    def set_calibrate_status(self, text: Optional[str]):
        """Show inline progress next to the Calibrate button (None = idle).

        The button stays enabled while collecting so a second press can cancel a
        run that stalls (e.g. playback paused before the window fills).
        """
        if dpg.does_item_exist("calibrate_status"):
            if text:
                dpg.set_value("calibrate_status", text)
                dpg.configure_item("calibrate_status", show=True)
            else:
                dpg.configure_item("calibrate_status", show=False)
        if dpg.does_item_exist("calibrate_btn"):
            dpg.configure_item("calibrate_btn",
                               label="CANCEL" if text else "CALIBRATE")

    def _on_vis_toggle(self, name, value):
        if 'on_visualization_toggle' in self.callbacks:
            self.callbacks['on_visualization_toggle'](name, value)
    
    def _on_vis_toolbar_toggle(self, name: str):
        """Handle visualization toolbar button toggle."""
        # Get current value and toggle
        key_map = {
            "skeleton": "show_skeleton",
            "keypoints": "show_keypoints",
            "bbox": "show_bbox",
            "trails": "show_trails",
            "ids": "show_ids",
        }
        config_key = key_map.get(name)
        if not config_key:
            return
        
        current = self.config.get(config_key, True)
        new_value = not current
        self.config[config_key] = new_value
        
        # Update button theme
        btn_tag = f"vis_{name}_btn"
        if dpg.does_item_exist(btn_tag):
            theme = self._vis_btn_on_theme if new_value else self._vis_btn_off_theme
            dpg.bind_item_theme(btn_tag, theme)
        
        # Notify callback
        if 'on_visualization_toggle' in self.callbacks:
            self.callbacks['on_visualization_toggle'](name, new_value)
    
    def _on_state_standby(self):
        """Handle STANDBY button press - preview only, no YOLO, no OSC."""
        self.set_system_state(SystemState.STANDBY)
    
    def _on_state_run(self):
        """Handle RUN button press - start YOLO inference and OSC output."""
        self.set_system_state(SystemState.RUN)
    
    def set_system_state(self, state: SystemState):
        """Change system state and update UI.
        
        2-state system:
        - STANDBY: Preview + enhancement, no YOLO, no OSC (standby btn active, run btn greyed)
        - RUN: Full YOLO + OSC (run btn active, standby btn greyed)
        """
        old_state = self._system_state
        self._system_state = state
        
        # Update state badge in top bar
        if dpg.does_item_exist("state_badge"):
            from gui_builder import STATE_LABELS, STATE_COLORS
            dpg.set_value("state_badge", STATE_LABELS.get(state, "UNKNOWN"))
            text_color, _ = STATE_COLORS.get(state, ((200, 200, 200, 255), (80, 80, 90, 255)))
            dpg.configure_item("state_badge", color=text_color)
        
        # Update button themes - active state highlighted, inactive greyed out
        if dpg.does_item_exist("state_standby_btn"):
            theme = self._btn_standby_active_theme if state == SystemState.STANDBY else self._btn_standby_theme
            dpg.bind_item_theme("state_standby_btn", theme)
        
        if dpg.does_item_exist("state_run_btn"):
            theme = self._btn_run_active_theme if state == SystemState.RUN else self._btn_run_theme
            dpg.bind_item_theme("state_run_btn", theme)
        
        # Notify callback
        if 'on_system_state_change' in self.callbacks:
            self.callbacks['on_system_state_change'](state, old_state)
    
    def get_system_state(self) -> SystemState:
        """Get current system state."""
        return self._system_state

    def _on_tracker_age_change(self, sender, value):
        if 'on_tracker_age_change' in self.callbacks:
            self.callbacks['on_tracker_age_change'](value)
    
    def _on_mog2_scale_change(self, sender, value):
        if 'on_mog2_scale_change' in self.callbacks:
            self.callbacks['on_mog2_scale_change'](float(value))

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
    
    def _on_safe_defaults(self):
        """Handle safe defaults button. Ctrl+click saves, normal click loads."""
        ctrl_held = dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)
        if ctrl_held:
            if 'on_save_safe_defaults' in self.callbacks:
                self.callbacks['on_save_safe_defaults']()
        else:
            if 'on_load_safe_defaults' in self.callbacks:
                self.callbacks['on_load_safe_defaults']()
    
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
    
    def _on_playback_speed_change(self, sender, value):
        """Handle playback speed change (e.g. 'x2.0')."""
        try:
            speed = float(value.replace('x', ''))
            if 'on_playback_speed_change' in self.callbacks:
                self.callbacks['on_playback_speed_change'](speed)
        except ValueError:
            pass
    
    def _on_playback_pause(self):
        """Handle pause/resume button click."""
        if 'on_playback_pause' in self.callbacks:
            self.callbacks['on_playback_pause']()
    
    def _on_playback_next_frame(self):
        """Handle next frame button click."""
        if 'on_playback_next_frame' in self.callbacks:
            self.callbacks['on_playback_next_frame']()
    
    def _on_playback_prev_frame(self):
        """Handle previous frame button click."""
        if 'on_playback_prev_frame' in self.callbacks:
            self.callbacks['on_playback_prev_frame']()

    def _on_report_issue(self):
        """Open the issue report dialog for the current playback frame."""
        if 'on_report_issue_request' not in self.callbacks:
            return
        # Pause playback (only if currently playing, never resume)
        if 'on_playback_force_pause' in self.callbacks:
            self.callbacks['on_playback_force_pause']()
        context = self.callbacks['on_report_issue_request']()
        if context:
            self.show_issue_report_dialog(context)
    
    def update_recording_ui(self, state: str, current_slot: int, slots_info: list, 
                            recording_frames: int = 0, playback_frame: int = 0, playback_total: int = 0,
                            playback_fps: float = 30.0, paused: bool = False, playback_speed: float = 1.0):
        """Update recording UI state.
        
        Args:
            state: 'live', 'armed', 'recording', or 'playing'
            current_slot: Active slot (0 = none)
            slots_info: List of (slot_id, has_recordings) for slots 1-9
            recording_frames: Number of frames recorded (when recording)
            playback_frame: Current playback frame (when playing)
            playback_total: Total playback frames (when playing)
            playback_fps: FPS of the video being played
        """
        # Update status text
        if dpg.does_item_exist("rec_status_text"):
            if state == "live":
                dpg.set_value("rec_status_text", "LIVE")
                dpg.configure_item("rec_status_text", color=(80, 200, 80))
            elif state == "armed":
                dpg.set_value("rec_status_text", "REC ARMED - Select slot")
                dpg.configure_item("rec_status_text", color=WARN_ORANGE)
            elif state == "recording":
                dpg.set_value("rec_status_text", f"SLOT {current_slot}")
                dpg.configure_item("rec_status_text", color=ALERT_RED)
            elif state == "playing":
                dpg.set_value("rec_status_text", f"SLOT {current_slot}")
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
        
        # Update playback controls visibility (toggle between status and playback groups)
        is_playing = (state == "playing")
        if dpg.does_item_exist("source_status_group"):
            dpg.configure_item("source_status_group", show=not is_playing)
        if dpg.does_item_exist("source_playback_group"):
            dpg.configure_item("source_playback_group", show=is_playing)
        
        # Update speed combo to match current playback speed
        if dpg.does_item_exist("rec_speed_combo") and state == "playing":
            speed_str = f"x{playback_speed}"
            dpg.set_value("rec_speed_combo", speed_str)
        
        # Update pause button label
        if dpg.does_item_exist("rec_pause_btn"):
            if paused:
                dpg.set_item_label("rec_pause_btn", Icons.PLAY)
            else:
                dpg.set_item_label("rec_pause_btn", Icons.PAUSE)
            # Ensure icon font is bound
            if self._icon_font:
                dpg.bind_item_font("rec_pause_btn", self._icon_font)
            
        if dpg.does_item_exist("rec_playback_progress"):
            # Format as time: MM:SS / MM:SS
            if playback_fps > 0:
                cur_sec = int(playback_frame / playback_fps)
                tot_sec = int(playback_total / playback_fps)
                cur_str = f"{cur_sec//60:02d}:{cur_sec%60:02d}"
                tot_str = f"{tot_sec//60:02d}:{tot_sec%60:02d}"
                dpg.set_value("rec_playback_progress", f"{cur_str} / {tot_str} ({playback_frame}/{playback_total})")
            else:
                dpg.set_value("rec_playback_progress", f"{playback_frame}/{playback_total}")
        if dpg.does_item_exist("rec_report_issue_btn"):
            dpg.configure_item("rec_report_issue_btn", enabled=is_playing)
    
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
                    width=scaled(200),
                    callback=lambda s, a, u: (callback(u), dpg.delete_item(menu_tag)),
                    user_data=filepath,
                )

    # --- Issue classification labels (order matters for cycling) ---
    _ISSUE_LABELS = ["dancer", "swapped", "ghost", "comment"]
    _ISSUE_LABEL_COLORS = {
        "dancer":  (100, 220, 100),   # green
        "swapped": (255, 180, 60),    # orange
        "ghost":   (180, 80, 80),     # red-ish
        "comment": (160, 160, 255),   # blue-ish
    }

    def show_issue_report_dialog(self, context: Dict[str, Any]):
        """Show a modal dialog to record a playback review issue."""
        if dpg.does_item_exist("issue_report_dialog"):
            dpg.delete_item("issue_report_dialog")

        self._issue_report_context = context
        self._issue_selected_ids: set[int] = set()
        # Per-ID classification: {dancer_id: "dancer"|"swapped"|"ghost"|"comment"}
        self._issue_id_labels: dict[int, str] = {}
        # Per-ID free comment (only used when label == "comment")
        self._issue_id_comments: dict[int, str] = {}
        frame_num = context.get('frame', 0)
        slot_num = context.get('slot', 0)
        active_ids = context.get('active_dancer_ids', [])

        dlg_w = scaled(440)
        dlg_h = scaled(300)
        with dpg.window(
            label="Report Issue",
            modal=True,
            autosize=True,
            tag="issue_report_dialog",
            width=dlg_w,
            height=dlg_h,
            pos=self._center_modal("issue_report_dialog", dlg_w, dlg_h),
            no_resize=True,
            no_move=False,
        ):
            dpg.add_text(
                f"F{frame_num}  Slot {slot_num}",
                color=(120, 200, 255),
            )
            dpg.add_spacer(height=scaled(4))
            dpg.add_text("Click ID to classify (cycle: dancer > swapped > ghost > comment > off):")
            with dpg.group(horizontal=True):
                for did in active_ids:
                    bgr = DANCER_COLORS[(did - 1) % len(DANCER_COLORS)]
                    rgb = (bgr[2], bgr[1], bgr[0])
                    btn_tag = f"issue_id_btn_{did}"
                    with dpg.theme() as btn_theme:
                        with dpg.theme_component(dpg.mvButton):
                            dpg.add_theme_color(dpg.mvThemeCol_Text, rgb)
                    dpg.add_button(
                        label=f"D{did}",
                        tag=btn_tag,
                        width=scaled(44),
                        callback=self._toggle_issue_id,
                        user_data=did,
                    )
                    dpg.bind_item_theme(btn_tag, btn_theme)

            # Container for per-ID detail rows (comment inputs appear here)
            dpg.add_group(tag="issue_id_detail_group")

            dpg.add_spacer(height=scaled(6))
            dpg.add_text("Note:")
            dpg.add_input_text(
                tag="issue_note_input",
                width=-1,
                height=scaled(50),
                multiline=True,
                hint="General comment on the frame",
            )
            dpg.add_spacer(height=scaled(8))
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Save",
                    width=scaled(190),
                    callback=self._submit_issue_report,
                )
                dpg.add_button(
                    label="Cancel",
                    width=scaled(190),
                    callback=self._cancel_issue_report,
                )

    def _toggle_issue_id(self, sender, app_data, user_data):
        """Cycle dancer ID classification: dancer → swapped → ghost → comment → off."""
        did = user_data
        labels = self._ISSUE_LABELS
        current = self._issue_id_labels.get(did)

        if current is None:
            # First click → "dancer"
            next_label = labels[0]
        else:
            idx = labels.index(current)
            if idx + 1 < len(labels):
                next_label = labels[idx + 1]
            else:
                next_label = None  # cycle back to unselected

        # Remove per-ID comment input if leaving "comment" state
        comment_tag = f"issue_id_comment_{did}"
        if current == "comment" and dpg.does_item_exist(comment_tag):
            # Save any typed text before removing
            self._issue_id_comments[did] = dpg.get_value(comment_tag)
        row_tag = f"issue_id_row_{did}"
        if dpg.does_item_exist(row_tag):
            dpg.delete_item(row_tag)

        if next_label is None:
            # Deselect
            self._issue_selected_ids.discard(did)
            self._issue_id_labels.pop(did, None)
            dpg.configure_item(sender, label=f"D{did}")
        else:
            self._issue_selected_ids.add(did)
            self._issue_id_labels[did] = next_label
            color = self._ISSUE_LABEL_COLORS[next_label]
            dpg.configure_item(sender, label=f"D{did}:{next_label[:3]}")
            # Show inline label row
            if dpg.does_item_exist("issue_id_detail_group"):
                with dpg.group(
                    horizontal=True,
                    tag=row_tag,
                    parent="issue_id_detail_group",
                ):
                    dpg.add_text(f"  D{did}:", color=color)
                    dpg.add_text(next_label, color=color)
                    if next_label == "comment":
                        dpg.add_input_text(
                            tag=comment_tag,
                            width=scaled(250),
                            hint="describe...",
                            default_value=self._issue_id_comments.get(did, ""),
                        )

    def _submit_issue_report(self):
        """Submit the current issue report dialog."""
        selected_ids = sorted(getattr(self, '_issue_selected_ids', set()))
        id_labels = getattr(self, '_issue_id_labels', {})
        id_comments = getattr(self, '_issue_id_comments', {})

        # Collect final comment text from any open input fields
        for did in list(id_labels.keys()):
            comment_tag = f"issue_id_comment_{did}"
            if id_labels.get(did) == "comment" and dpg.does_item_exist(comment_tag):
                id_comments[did] = dpg.get_value(comment_tag)

        # Build dancer_labels dict: {dancer_id: {"label": ..., "comment": ...}}
        dancer_labels = {}
        for did in selected_ids:
            entry: dict = {"label": id_labels.get(did, "unspecified")}
            if did in id_comments and id_comments[did].strip():
                entry["comment"] = id_comments[did].strip()
            dancer_labels[did] = entry

        # Legacy issue_type for backward compat
        issue_type = ",".join(f"D{d}" for d in selected_ids) if selected_ids else ""
        note = dpg.get_value("issue_note_input") if dpg.does_item_exist("issue_note_input") else ""

        context = getattr(self, '_issue_report_context', None)
        if context:
            context['selected_dancer_ids'] = selected_ids
            context['dancer_labels'] = dancer_labels
        try:
            if context and 'on_issue_submit' in self.callbacks:
                self.callbacks['on_issue_submit'](context, issue_type, note)
        finally:
            self._close_issue_report_dialog()

    def _cancel_issue_report(self):
        """Cancel issue reporting and close the dialog cleanly."""
        self._close_issue_report_dialog()

    def _close_issue_report_dialog(self):
        """Close the issue dialog and notify the app to refresh playback UI."""
        if dpg.does_item_exist("issue_report_dialog"):
            dpg.delete_item("issue_report_dialog")
        if 'on_issue_dialog_closed' in self.callbacks:
            self.callbacks['on_issue_dialog_closed']()
    
    # === Public Methods ===

    # === Layout recomputation ===

    def set_camera_dimensions(self, width: int, height: int):
        """Update camera native dimensions and recompute layout."""
        if width > 0 and height > 0:
            self._camera_width = width
            self._camera_height = height
            self._recompute_layout()

    def _on_viewport_resize(self, sender=None, app_data=None):
        """Called by DearPyGui when the viewport is resized."""
        self._recompute_layout()
        self._recenter_modals()

    # === Modal centering ===

    def _centered_pos(self, w: int, h: int) -> list:
        """Top-left position centering a w×h window in the viewport.

        Uses the client (drawable) area — pos coordinates are client-relative,
        so centering against the outer viewport size drifts down/right by the
        decoration size. Falls back to the outer size when client metrics are
        not ready yet (first frames).
        """
        vw = dpg.get_viewport_client_width() or dpg.get_viewport_width()
        vh = dpg.get_viewport_client_height() or dpg.get_viewport_height()
        return [max(0, (vw - w) // 2), max(0, (vh - h) // 2)]

    def _center_modal(self, tag: str, w: int, h: int) -> list:
        """Return an initial centered position for a modal and register it
        for re-centering when the viewport is resized."""
        self._centered_modals[tag] = (w, h)
        return self._centered_pos(w, h)

    def _recenter_modals(self):
        for tag, (w, h) in self._centered_modals.items():
            if dpg.does_item_exist(tag):
                dpg.set_item_pos(tag, self._centered_pos(w, h))

    def _recompute_layout(self):
        """Recompute layout dimensions based on viewport size and camera aspect ratio.

        Updates:
        - video_panel / control_panel heights (middle section)
        - video_image display size (fitted to available area)
        - _fitted_render_scale (optimal texture scale, capped at 1.0)
        - _layout_dirty flag (so app.py can sync pipeline)
        """
        try:
            vp_w = dpg.get_viewport_width()
            vp_h = dpg.get_viewport_height()
        except Exception:
            return  # Viewport not ready
        if vp_w <= 0 or vp_h <= 0:
            return

        ctrl_w = scaled(CONTROL_PANEL_WIDTH)
        h_pad = scaled(LAYOUT_H_PAD)

        # Dynamically measure top/bottom bar heights if rendered,
        # otherwise fall back to a safe estimate.
        top_h = 0
        bot_h = 0
        rail_h = 0
        drawer_h = 0
        alerts_h = 0
        try:
            if dpg.does_item_exist("top_bar_wrapper"):
                top_h = dpg.get_item_rect_size("top_bar_wrapper")[1]
            if dpg.does_item_exist("bottom_bar_wrapper"):
                bot_h = dpg.get_item_rect_size("bottom_bar_wrapper")[1]
            if dpg.does_item_exist("phase_rail_wrapper"):
                rail_h = dpg.get_item_rect_size("phase_rail_wrapper")[1]
            if dpg.does_item_exist("drawer_bar_wrapper"):
                drawer_h = dpg.get_item_rect_size("drawer_bar_wrapper")[1]
            if dpg.does_item_exist("alerts_strip_wrapper"):
                alerts_h = dpg.get_item_rect_size("alerts_strip_wrapper")[1]
        except Exception:
            pass

        if top_h > 0 and bot_h > 0:
            # Measured bars + phase rail + alerts strip + drawer bar + DPG window
            # padding (2×8) + spacers + item spacing gaps. Generous margin keeps
            # the bottom bar visible.
            v_overhead = (int(top_h + bot_h + rail_h + drawer_h + alerts_h)
                          + scaled(LAYOUT_V_MARGIN))
        else:
            # First frame fallback (items not rendered yet)
            v_overhead = scaled(LAYOUT_V_FALLBACK)

        mid_h = max(scaled(200), vp_h - v_overhead)
        vid_w = max(scaled(200), vp_w - ctrl_w - h_pad)
        vid_h = mid_h - scaled(4)  # internal margin

        # Fit camera image within available area keeping aspect ratio
        cam_w = self._camera_width or 1920
        cam_h = self._camera_height or 1080
        cam_aspect = cam_w / cam_h

        if vid_w / max(vid_h, 1) > cam_aspect:
            img_h = vid_h
            img_w = int(vid_h * cam_aspect)
        else:
            img_w = vid_w
            img_h = int(vid_w / cam_aspect)

        img_w = max(img_w, 100)
        img_h = max(img_h, 100)

        # Apply to DPG items
        if dpg.does_item_exist("video_panel"):
            dpg.configure_item("video_panel", width=vid_w, height=mid_h)
        if dpg.does_item_exist("control_panel"):
            dpg.configure_item("control_panel", height=mid_h)
        if dpg.does_item_exist("phase_panel"):
            dpg.configure_item("phase_panel", height=mid_h)
        if dpg.does_item_exist("video_image"):
            dpg.configure_item("video_image", width=img_w, height=img_h)

        self.video_width = img_w
        self.video_height = img_h
        self._middle_height = mid_h

        # Compute optimal render scale (capped at 1.0)
        self._fitted_render_scale = min(img_w / cam_w, img_h / cam_h, 1.0)
        self._layout_dirty = True

        # Update the read-only scale indicator in Preview section
        if dpg.does_item_exist("preview_autofit_scale_text"):
            dpg.set_value("preview_autofit_scale_text", f"{self._fitted_render_scale:.2f}x")

    def resize_preview(self, width: int, height: int,
                       display_width: int = 0, display_height: int = 0):
        """Resize preview texture when render resolution changes.

        Display dimensions are managed by _recompute_layout(), so the
        display_width/display_height args are accepted for backward
        compatibility but no longer resize the video panel.

        Args:
            width, height: texture (render) dimensions.
            display_width, display_height: (ignored – kept for compat)
        """
        texture_changed = (width != self.texture_width or height != self.texture_height)

        if not texture_changed:
            return

        print(f"GUI resize_preview: tex {self.texture_width}x{self.texture_height} -> {width}x{height}")

        # --- Recreate texture ---
        if dpg.does_item_exist(self.frame_texture_tag):
            dpg.delete_item(self.frame_texture_tag)

        import time
        self.frame_texture_tag = f"video_texture_{int(time.time()*1000)}"

        self.texture_width = width
        self.texture_height = height
        self.frame_buffer = np.zeros(
            self.texture_height * self.texture_width * 4,
            dtype=np.float32,
        )

        with dpg.texture_registry(show=False):
            self.frame_texture_id = dpg.add_raw_texture(
                width=self.texture_width,
                height=self.texture_height,
                default_value=self.frame_buffer,
                format=dpg.mvFormat_Float_rgba,
                tag=self.frame_texture_tag,
            )

        # Re-bind texture to image widget (display size unchanged)
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

        # Resize to display size if needed
        h, w = frame.shape[:2]
        if w != self.texture_width or h != self.texture_height:
            frame = cv2.resize(frame, (self.texture_width, self.texture_height))

        # Convert BGR to RGBA
        rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)

        if self.frame_buffer.size != rgba.size:
            # Texture likely re-created; allocate matching buffer
            self.frame_buffer = np.zeros(rgba.size, dtype=np.float32)
        # Scale+cast straight into the persistent buffer — this runs per
        # frame, so no temporaries (astype/division copies) allowed here.
        np.multiply(rgba.reshape(-1), np.float32(1.0 / 255.0), out=self.frame_buffer)

        # Update texture - pass the buffer directly
        if dpg.does_item_exist(self.frame_texture_tag):
            dpg.set_value(self.frame_texture_tag, self.frame_buffer)
    
    @staticmethod
    def _fps_color(fps: float):
        """Return an (R, G, B) color for an FPS value.

        >= 19  → OK_GREEN
        <= 10  → ALERT_RED
        Between 10 and 19 → smooth gradient from red to green.
        """
        if fps >= 19:
            return OK_GREEN
        if fps <= 10:
            return ALERT_RED
        t = (fps - 10) / 9.0  # 0..1
        r = int(255 + (120 - 255) * t)
        g = int(80 + (255 - 80) * t)
        b = int(80 + (120 - 80) * t)
        return (r, g, b)

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
        camera_reconnecting: bool = False,
        camera_type: str = "",
        enhance_bypassed: bool = False,
        gpu_fallback_reason: str = "",
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
        enhance_enabled = dpg.get_value('adv_enhance_checkbox') if dpg.does_item_exist('adv_enhance_checkbox') else False
        self._update_enhance_row_state(enhance_enabled, bypass=enhance_bypassed)

        # Status badges
        cam_color = OK_GREEN if camera_running else ERROR_SOFT
        if dpg.does_item_exist("badge_cam"):
            dpg.set_value("badge_cam", "ON" if camera_running else "OFF")
            dpg.configure_item("badge_cam", color=cam_color)
        if dpg.does_item_exist("camera_reconnect_label"):
            dpg.configure_item("camera_reconnect_label", show=camera_reconnecting)
            
        if dpg.does_item_exist("badge_cam_type"):
            if camera_type == "IDS_PEAK":
                dpg.set_value("badge_cam_type", "[IDS]")
                dpg.configure_item("badge_cam_type", color=(100, 200, 255))
            elif camera_type == "OPENCV":
                dpg.set_value("badge_cam_type", "[CV]")
                dpg.configure_item("badge_cam_type", color=WARN_AMBER)
            else:
                dpg.set_value("badge_cam_type", "[--]")
                dpg.configure_item("badge_cam_type", color=TEXT_MUTED)

        # Show/hide IDS-specific sliders based on camera type
        is_ids = (camera_type == "IDS_PEAK")
        if dpg.does_item_exist("ids_sliders_group"):
            dpg.configure_item("ids_sliders_group", show=is_ids)
        if dpg.does_item_exist("ids_settings_toggle_btn"):
            dpg.configure_item("ids_settings_toggle_btn", show=is_ids)
        if not is_ids and dpg.does_item_exist("ids_hw_settings_group"):
            dpg.configure_item("ids_hw_settings_group", show=False)

        osc_color = OK_GREEN if osc_enabled else ERROR_SOFT
        if dpg.does_item_exist("badge_osc"):
            dpg.set_value("badge_osc", "ON" if osc_enabled else "OFF")
            dpg.configure_item("badge_osc", color=osc_color)

        if dpg.does_item_exist("badge_model"):
            dpg.set_value("badge_model", model_name or "--")

        fps_color = self._fps_color(fps)
        if dpg.does_item_exist("badge_fps"):
            dpg.set_value("badge_fps", f"{fps:.1f}")
            dpg.configure_item("badge_fps", color=fps_color)

        self.update_compute_mode_badge(gpu_fallback_reason)

        # Update timing breakdown
        if timing:
            enh = timing.get('enhance', 0)
            up = timing.get('upscale', 0)
            yolo = timing.get('yolo', 0)
            trk = timing.get('track', 0)
            pdraw = timing.get('preview_draw', 0)
            pup = timing.get('preview_upload', 0)
            total = timing.get('total', 0)
            
            # Get path indicators (gpu/cpu)
            path_enhance = timing.get('path_enhance', 'cpu')
            path_yolo = timing.get('path_yolo', 'gpu')
            path_track = timing.get('path_track', 'cpu')
            
            # Preview timing: GPU download + CPU draw + texture upload
            pdown = timing.get('preview_download', 0)
            preview_time = pdown + pdraw + pup
            self._last_preview_time = preview_time

            dpg.set_value("time_enhance", f"{enh:.0f}")
            dpg.set_value("time_yolo", f"{yolo:.0f}")
            dpg.set_value("time_track", f"{trk:.0f}")
            dpg.set_value("time_preview", f"{self._last_preview_time:.0f}")
            dpg.set_value("time_total", f"{total:.0f}")
            
            # Update path indicators with colors
            # GPU = green [GPU], CPU = red [CPU]
            gpu_color = OK_GREEN
            cpu_color = ERROR_SOFT
            
            if dpg.does_item_exist("path_enhance"):
                is_gpu = path_enhance == "gpu"
                dpg.set_value("path_enhance", "[GPU]" if is_gpu else "[CPU]")
                dpg.configure_item("path_enhance", color=gpu_color if is_gpu else cpu_color)
            
            if dpg.does_item_exist("path_yolo"):
                is_gpu = path_yolo == "gpu"
                dpg.set_value("path_yolo", "[GPU]" if is_gpu else "[CPU]")
                dpg.configure_item("path_yolo", color=gpu_color if is_gpu else cpu_color)
            
            if dpg.does_item_exist("path_track"):
                is_gpu = path_track == "gpu"
                dpg.set_value("path_track", "[GPU]" if is_gpu else "[CPU]")
                dpg.configure_item("path_track", color=gpu_color if is_gpu else cpu_color)

            # Color code key timings
            def _colorize(tag, val, g=40, y=80):
                if val < g:
                    dpg.configure_item(tag, color=BRIGHT_GREEN)
                elif val < y:
                    dpg.configure_item(tag, color=(255, 200, 0))
                else:
                    dpg.configure_item(tag, color=ALERT_RED)

            _colorize("time_yolo", yolo)
            _colorize("time_enhance", enh, g=10, y=30)
            _colorize("time_preview", self._last_preview_time, g=5, y=15)

        # Update FPS color based on performance
        dpg.configure_item("fps_text", color=self._fps_color(fps))
    
    def sync_checkbox(self, name: str, value: bool):
        """Sync checkbox state (when changed via keyboard or config load)."""
        tag_map = {
            'enhance': ['adv_enhance_checkbox'],
            'enhance_lite': [],
            'enhance_force': ['adv_enhance_force_checkbox'],
            'greyscale': ['adv_greyscale_checkbox'],
            'preview': ['adv_preview_checkbox'],
            'preview_cap': ['adv_preview_cap_checkbox'],
            'input_fps_cap': ['adv_input_fps_cap_checkbox'],
            'trt': ['adv_trt_checkbox'],
            'osc': ['osc_checkbox'],
            'bg_enable': ['bg_enable_checkbox'],
            'roi_enable': ['adv_roi_enable_checkbox'],
        }
        # Visualization toggles - update toolbar button themes instead of checkboxes
        vis_toggles = ['skeleton', 'keypoints', 'bbox', 'trails', 'ids']
        if name in vis_toggles:
            btn_tag = f"vis_{name}_btn"
            if dpg.does_item_exist(btn_tag):
                theme = self._vis_btn_on_theme if value else self._vis_btn_off_theme
                dpg.bind_item_theme(btn_tag, theme)
            return
        
        if name in tag_map:
            for tag in tag_map[name]:
                if dpg.does_item_exist(tag):
                    dpg.set_value(tag, value)
        # Update row grey state when toggling via keyboard
        if name == 'preview':
            self._update_preview_row_state(value)
        elif name == 'enhance':
            self._update_enhance_row_state(value, bypass=False)
    
    def sync_slider(self, name: str, value: float):
        """Sync slider state (when changed via keyboard or config load)."""
        tag_map = {
            'confidence': ['show_conf_slider'],
            'sensitivity': ['sensitivity_slider'],
            'gap_bridging': ['gap_bridging_slider'],
            'motion_sensitivity': ['motion_sensitivity_slider'],
            'clahe': ['adv_clahe_slider'],
            'gamma': ['adv_gamma_slider'],
            'brightness_threshold': ['adv_brightness_threshold_slider'],
            'denoise_strength': [],
            'person_height': ['person_height_slider'],
            'tracker_max_age': ['tracker_age_slider'],
            'ids_ratio': ['adv_ids_ratio_slider'],
            'ids_gain_db': ['adv_ids_gain_slider'],
            'ids_exposure_us': ['adv_ids_exposure_slider'],
            'bg_sensitivity': ['bg_sensitivity_slider'],
            'mog2_scale': ['mog2_scale_slider'],
        }
        if name in tag_map:
            for tag in tag_map[name]:
                if dpg.does_item_exist(tag):
                    dpg.set_value(tag, value)
        if name == 'ids_exposure_us':
            self.update_ids_exposure_warning(value)

    def update_ids_exposure_warning(self, exposure_us: float):
        """Show an exposure warning only when manual exposure implies 15-20 FPS."""
        tag = "adv_ids_exposure_warning"
        if not dpg.does_item_exist(tag):
            return

        exposure_us = float(exposure_us)
        if exposure_us <= 0:
            dpg.configure_item(tag, show=False)
            return

        min_fps = float(self.config.get('ids_exposure_min_fps', 15.0))
        warning_fps = float(self.config.get('ids_exposure_warning_fps', 20.0))
        implied_fps = 1_000_000.0 / exposure_us

        if min_fps <= implied_fps < warning_fps:
            dpg.set_value(tag, f"Exposure-limited: {implied_fps:.1f} FPS")
            dpg.configure_item(tag, color=WARN_ORANGE, show=True)
        else:
            dpg.configure_item(tag, show=False)

    def sync_combo(self, name: str, value: str):
        """Sync combo box state."""
        tag_map = {
            'model': ['adv_model_combo'],
            'imgsz': ['adv_imgsz_combo'],
            'camera': ['adv_camera_combo'],
        }
        if name in tag_map:
            for tag in tag_map[name]:
                if dpg.does_item_exist(tag):
                    dpg.set_value(tag, value)
    
    def update_model_dropdown(self, model_name: str):
        """Update model dropdown to show current model."""
        for tag in ["adv_model_combo"]:
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, model_name)
    
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
        for tag in ["adv_trt_checkbox"]:
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, enabled)
    
    def update_gpu_stats(self):
        """Update GPU stats in the top bar (util, temp, VRAM).
        
        Can be called independently to update GPU stats during model loading.
        """
        gpu = get_gpu_stats()
        if gpu['util'] >= 0:
            # GPU util/temp/power - colored by temperature
            power_str = f"{gpu['power']:.0f}W" if gpu['power'] >= 0 else "?"
            dpg.set_value("topbar_gpu_util_text", f"{gpu['util']}%/{gpu['temp']}°C/{power_str}")
            if gpu['temp'] < 70:
                dpg.configure_item("topbar_gpu_util_text", color=BRIGHT_GREEN)
            elif gpu['temp'] < 85:
                dpg.configure_item("topbar_gpu_util_text", color=(255, 200, 0))
            else:
                dpg.configure_item("topbar_gpu_util_text", color=ALERT_RED)
            # VRAM % - colored by usage
            vram_pct = gpu['vram_pct']
            dpg.set_value("topbar_gpu_vram_text", f"{vram_pct:.0f}%")
            if vram_pct < 50:
                dpg.configure_item("topbar_gpu_vram_text", color=BRIGHT_GREEN)
            elif vram_pct < 80:
                dpg.configure_item("topbar_gpu_vram_text", color=(255, 200, 0))
            else:
                dpg.configure_item("topbar_gpu_vram_text", color=ALERT_RED)
        else:
            dpg.set_value("topbar_gpu_util_text", "N/A")
            dpg.set_value("topbar_gpu_vram_text", "N/A")

    def update_compute_mode_badge(self, gpu_fallback_reason: str = ""):
        """Show/hide CPU fallback indicator with reason and next-step action."""
        has_fallback = bool(gpu_fallback_reason)

        if dpg.does_item_exist("badge_compute_mode"):
            dpg.configure_item("badge_compute_mode", show=has_fallback)

        if not has_fallback:
            return

        reason_text = gpu_fallback_reason.strip().splitlines()[0]
        reason_text = reason_text[:140]

        action_text = "Action: install a GPU-compatible PyTorch/CUDA build for your GPU, then restart WallDance."
        reason_lc = gpu_fallback_reason.lower()
        if "no kernel image is available" in reason_lc or "sm_" in reason_lc:
            action_text = "Action: current Torch/CUDA build does not support this GPU architecture. Upgrade to a build that supports your GPU (e.g. RTX 50-series / sm_120), then restart."

        if dpg.does_item_exist("badge_compute_reason_text"):
            dpg.set_value("badge_compute_reason_text", f"Reason: {reason_text}")
        if dpg.does_item_exist("badge_compute_action_text"):
            dpg.set_value("badge_compute_action_text", action_text)
    
    def update_camera_sources(self, sources: list, current: str = "", unavailable: list = None):
        """Update camera source dropdown with available/unavailable cameras.
        
        Args:
            sources: List of camera source strings (e.g., ['0', '1', '/dev/video0'])
            current: Currently selected camera source
            unavailable: List of sources that are unavailable (shown greyed)
        """
        if unavailable is None:
            unavailable = []

        if current and current not in sources:
            sources = list(sources) + [current]
        
        # Create display items with unavailable markers
        display_items = []
        for src in sources:
            if src in unavailable:
                display_items.append(f"{src} (unavailable)")
            else:
                display_items.append(src)
        
        # Update camera combo
        for combo_tag in ["adv_camera_combo"]:
            if dpg.does_item_exist(combo_tag):
                dpg.configure_item(combo_tag, items=display_items)
                # Set current value
                if current in unavailable:
                    dpg.set_value(combo_tag, f"{current} (unavailable)")
                elif current:
                    dpg.set_value(combo_tag, current)

    def update_camera_status(self, running: bool, source: str = "", reconnecting: bool = False):
        """Update camera badge color."""
        self.camera_running = running
        cam_color = OK_GREEN if running else ERROR_SOFT
        if dpg.does_item_exist("badge_cam"):
            dpg.set_value("badge_cam", "ON" if running else "OFF")
            dpg.configure_item("badge_cam", color=cam_color)
        if dpg.does_item_exist("camera_reconnect_label"):
            dpg.configure_item("camera_reconnect_label", show=reconnecting)
    
    def sync_input(self, name: str, value):
        """Sync input field state."""
        tag_map = {
            'osc_ip': 'osc_ip_input',
            'osc_port': 'osc_port_input',
        }
        if name in tag_map and dpg.does_item_exist(tag_map[name]):
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
            dpg.configure_item("save_indicator", show=True, color=BRIGHT_GREEN)
    
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
        
        dlg_w = scaled(400)
        dlg_h = scaled(200)
        with dpg.window(
            label="Save Configuration",
            modal=True,
            autosize=True,
            tag="save_config_dialog",
            width=dlg_w,
            height=dlg_h,
            pos=self._center_modal("save_config_dialog", dlg_w, dlg_h),
            no_resize=True,
            no_move=False,
        ):
            dpg.add_text("Enter project name:")
            dpg.add_text("(Config will be saved with timestamp in project folder)", color=TEXT_MUTED)
            dpg.add_spacer(height=scaled(5))
            dpg.add_input_text(
                tag="save_config_name_input",
                default_value=default_name,
                width=-1,
                hint="project name"
            )
            dpg.add_spacer(height=scaled(10))
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Save",
                    width=scaled(180),
                    callback=self._do_save_config
                )
                dpg.add_button(
                    label="Cancel",
                    width=scaled(180),
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
    
    # ------------------------------------------------------------------
    # Startup project picker (ROADMAP §7B)
    # ------------------------------------------------------------------
    # Rename/Delete render INLINE inside this one modal and the list refreshes
    # in place — DearPyGui does not reliably show a modal that is created in the
    # same frame another modal was deleted, so we never stack or recreate modals.
    def show_project_picker(self, projects, last_project: str = ""):
        """Show (or refresh in place) the launch-time project picker.

        projects: list of (name, last_saved_display, save_count), ordered
                  most-recent-first.  last_project is pre-highlighted.
        """
        names = [p[0] for p in projects]
        if last_project in names:
            self._picker_selected = last_project
        elif names:
            self._picker_selected = names[0]
        else:
            self._picker_selected = ""

        # Refresh in place if the window already exists (e.g. after rename/delete).
        if dpg.does_item_exist("project_picker_modal"):
            self._picker_rebuild_list(projects)
            if dpg.does_item_exist("picker_action_area"):
                dpg.delete_item("picker_action_area", children_only=True)
            return

        w, h = scaled(520), scaled(560)
        with dpg.window(
            label="WallDance - Select Project", modal=True, tag="project_picker_modal",
            width=w, height=h, pos=self._center_modal("project_picker_modal", w, h),
            no_resize=True, no_move=True, no_close=True, no_collapse=True,
        ):
            dpg.add_spacer(height=scaled(6))
            dpg.add_text("Select a project to launch  (Enter = launch highlighted)",
                         color=HEADING_GREEN)
            dpg.add_text("Ordered by last save, most recent first.", color=(140, 140, 140))
            dpg.add_spacer(height=scaled(8))
            dpg.add_child_window(height=scaled(270), border=True, tag="picker_list_area")
            dpg.add_spacer(height=scaled(10))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Launch", width=scaled(110), height=scaled(30),
                               callback=lambda: self._picker_action("launch"))
                dpg.add_button(label="Rename", width=scaled(90), height=scaled(30),
                               callback=lambda: self._picker_action("rename"))
                dpg.add_button(label="Delete", width=scaled(90), height=scaled(30),
                               callback=lambda: self._picker_action("delete"))
                dpg.add_spacer(width=scaled(24))
                dpg.add_button(label="Start blank", width=scaled(110), height=scaled(30),
                               callback=lambda: self._picker_action("blank"))
            dpg.add_spacer(height=scaled(6))
            dpg.add_group(tag="picker_action_area")   # inline rename / delete UI
        self._picker_rebuild_list(projects)

    def _picker_rebuild_list(self, projects):
        """(Re)populate the project list rows in place."""
        area = "picker_list_area"
        if not dpg.does_item_exist(area):
            return
        dpg.delete_item(area, children_only=True)
        self._picker_rows = []
        if not projects:
            dpg.add_text("No saved projects yet.", parent=area, color=WARN_AMBER)
            dpg.add_text("Start blank below, then save to create one.",
                         parent=area, color=TEXT_MUTED)
            return
        for name, saved, count in projects:
            tag = f"picker_row_{name}"
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
            dpg.add_selectable(
                label=f"{name}    -  saved {saved}  -  {count} save{'s' if count != 1 else ''}",
                default_value=(name == self._picker_selected), tag=tag, parent=area,
                callback=self._picker_on_select, user_data=name)
            self._picker_rows.append((tag, name))

    def _picker_on_select(self, sender, value, user_data):
        """Single-select: highlight the clicked row, clear the others."""
        self._picker_selected = user_data
        for tag, name in getattr(self, "_picker_rows", []):
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, name == user_data)

    def _picker_action(self, action: str):
        sel = getattr(self, "_picker_selected", "")
        if action == "blank":
            self.hide_project_picker()
            cb = self.callbacks.get("on_project_blank")
            if cb:
                cb()
            return
        if not sel:
            return
        if action == "launch":
            self.hide_project_picker()
            cb = self.callbacks.get("on_project_launch")
            if cb:
                cb(sel)
        elif action == "rename":
            self._picker_inline_rename(sel)
        elif action == "delete":
            self._picker_inline_delete(sel)

    def _picker_inline_rename(self, name: str):
        area = "picker_action_area"
        if not dpg.does_item_exist(area):
            return
        dpg.delete_item(area, children_only=True)

        def do_rename(*_):
            new = (dpg.get_value("picker_rename_input") or "").strip()
            dpg.delete_item(area, children_only=True)
            if new and new != name:
                cb = self.callbacks.get("on_project_rename")
                if cb:
                    cb(name, new)   # app renames + refreshes the list in place

        dpg.add_text(f"Rename '{name}' to:", parent=area)
        with dpg.group(horizontal=True, parent=area):
            dpg.add_input_text(tag="picker_rename_input", default_value=name,
                               width=scaled(240), on_enter=True, callback=do_rename)
            dpg.add_button(label="OK", width=scaled(60), callback=do_rename)
            dpg.add_button(label="Cancel", width=scaled(70),
                           callback=lambda: dpg.delete_item(area, children_only=True))

    def _picker_inline_delete(self, name: str):
        area = "picker_action_area"
        if not dpg.does_item_exist(area):
            return
        dpg.delete_item(area, children_only=True)

        def do_delete(*_):
            dpg.delete_item(area, children_only=True)
            cb = self.callbacks.get("on_project_delete")
            if cb:
                cb(name)            # app deletes + refreshes the list in place

        dpg.add_text(f"Delete '{name}'?  Removes all its configs + recordings.",
                     parent=area, color=WARN_ORANGE)
        with dpg.group(horizontal=True, parent=area):
            dpg.add_button(label="Delete", width=scaled(90), callback=do_delete)
            dpg.add_button(label="Cancel", width=scaled(80),
                           callback=lambda: dpg.delete_item(area, children_only=True))

    def hide_project_picker(self):
        if dpg.does_item_exist("project_picker_modal"):
            dpg.delete_item("project_picker_modal")

    def project_picker_visible(self) -> bool:
        return dpg.does_item_exist("project_picker_modal")

    def project_picker_selection(self) -> str:
        return getattr(self, "_picker_selected", "")

    def project_picker_inline_active(self) -> bool:
        """True while an inline rename/delete prompt is showing (suppresses Enter-launch)."""
        if not dpg.does_item_exist("picker_action_area"):
            return False
        return bool(dpg.get_item_children("picker_action_area", 1))

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
        
        load_w = scaled(500)
        load_h = scaled(480)
        with dpg.window(
            label="Load Configuration",
            modal=True,
            tag="load_config_dialog",
            width=load_w,
            height=load_h,
            pos=self._center_modal("load_config_dialog", load_w, load_h),
            no_resize=True,
            no_move=False,
        ):
            if not projects:
                dpg.add_text("No saved projects found.", color=WARN_AMBER)
                dpg.add_text("Save a config first to create a project.", color=TEXT_MUTED)
            else:
                dpg.add_text("Select a project:", color=HEADING_GREEN)
                dpg.add_spacer(height=scaled(5))
                
                # Store project list for deselection logic
                self._project_selectables = []
                
                with dpg.child_window(height=scaled(140), border=True, tag="project_list_window"):
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
                
                dpg.add_spacer(height=scaled(10))
                dpg.add_text("Config history:", tag="history_label", color=HEADING_GREEN)
                dpg.add_spacer(height=scaled(5))
                
                with dpg.child_window(height=scaled(150), border=True, tag="config_history_window"):
                    dpg.add_text("Select a project above...", tag="history_placeholder", color=TEXT_HINT)
                
                dpg.add_spacer(height=scaled(10))
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
            
            dpg.add_spacer(height=scaled(5))
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
                dpg.add_text("No configs in this project", color=TEXT_HINT, parent="config_history_window")
    
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
        
        modal_width = scaled(500)
        modal_height = scaled(200)

        with dpg.window(
            label="Model Loading",
            modal=True,
            autosize=True,
            tag="model_loading_modal",
            width=modal_width,
            height=modal_height,
            pos=self._center_modal("model_loading_modal", modal_width, modal_height),
            no_resize=True,
            no_move=True,
            no_close=True,
            no_collapse=True,
        ):
            dpg.add_spacer(height=scaled(10))
            dpg.add_text(message, tag="model_loading_message", wrap=scaled(480))
            dpg.add_spacer(height=scaled(15))
            dpg.add_progress_bar(
                tag="model_loading_progress",
                default_value=0.0,
                width=-1,
                height=scaled(25),
            )
            dpg.add_spacer(height=scaled(8))
            dpg.add_text("", tag="model_loading_detail", color=TEXT_MUTED, wrap=scaled(480))
    
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
        
        modal_width = scaled(450)
        modal_height = scaled(180)
        
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
            autosize=True,
            tag="tensorrt_prompt_modal",
            width=modal_width,
            height=modal_height,
            pos=self._center_modal("tensorrt_prompt_modal", modal_width, modal_height),
            no_resize=True,
            no_move=True,
            no_close=True,
            no_collapse=True,
        ):
            dpg.add_spacer(height=scaled(10))
            dpg.add_text(f"No TensorRT engine found for {model_name}.", wrap=scaled(420))
            dpg.add_spacer(height=scaled(5))
            dpg.add_text(
                "Build TensorRT engine for faster inference (5-10 min)?\n"
                "Or use PyTorch directly (slower but instant).",
                wrap=scaled(420),
                color=TEXT_NORMAL
            )
            dpg.add_spacer(height=scaled(15))
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Build TensorRT (5-10 min)",
                    callback=on_build_trt,
                    width=scaled(180),
                )
                dpg.add_spacer(width=scaled(20))
                dpg.add_button(
                    label="Use PyTorch",
                    callback=on_use_pytorch,
                    width=scaled(120),
                )
    
    def hide_tensorrt_prompt(self):
        """Hide the TensorRT prompt dialog."""
        if dpg.does_item_exist("tensorrt_prompt_modal"):
            dpg.delete_item("tensorrt_prompt_modal")

    def _on_calib2(self, sender=None, value=None):
        if 'on_calib2' in self.callbacks:
            self.callbacks['on_calib2']()

    def _on_view_calib2_pool(self, sender=None, value=None):
        if 'on_view_calib2_pool' in self.callbacks:
            self.callbacks['on_view_calib2_pool']()

    def show_calib2_dialog(self, rows, proposal: str):
        """Render the dancer-calibration evidence pool INLINE in phase 4
        (Calibrate): run list with include-checkboxes, the pooled proposal, and
        Apply / Clear. Replaces the old modal -- the phase panel has room
        (OPERATOR_V2 Track O). Driven by Calib2PoolChanged + the on-entry fetch.
        """
        container = "calib2_pool_inline"
        if not dpg.does_item_exist(container):
            return
        dpg.delete_item(container, children_only=True)  # re-render from scratch
        wrap = scaled(CONTROL_PANEL_WIDTH - 50)
        checkbox_tags = []

        def on_apply():
            selected = [path for tag, path in checkbox_tags
                        if dpg.does_item_exist(tag) and dpg.get_value(tag)]
            if 'on_calib2_apply' in self.callbacks:
                self.callbacks['on_calib2_apply'](selected)

        def on_clear():
            if 'on_calib2_clear' in self.callbacks:
                self.callbacks['on_calib2_clear']()
            # Re-fetch so the inline view reflects the now-empty pool (the clear
            # command drains before this view command).
            if 'on_view_calib2_pool' in self.callbacks:
                self.callbacks['on_view_calib2_pool']()

        dpg.add_text("Runs in the pool (uncheck to exclude):",
                     color=TEXT_NORMAL, parent=container)
        with dpg.child_window(height=scaled(140), border=True, parent=container):
            for i, row in enumerate(rows):
                tag = f"calib2_run_chk_{i}"
                with dpg.group(horizontal=True):
                    dpg.add_checkbox(tag=tag, default_value=not row.get("stale", False))
                    label = row["label"]
                    if row.get("stale"):
                        dpg.add_text(label + "  [STALE - framing changed]",
                                     color=WARN_ORANGE)
                    else:
                        dpg.add_text(label)
                checkbox_tags.append((tag, row["path"]))
            if not rows:
                dpg.add_text("(empty - run 'Calibrate with Dancers' to add evidence)",
                             color=TEXT_DIM)
        dpg.add_spacer(height=scaled(6), parent=container)
        # NOTE: this proposal is aggregated over ALL pooled runs, not the current
        # checkbox selection -- a live per-selection recompute + auto-apply needs
        # a calib-flow preview path (flagged for the calib agent).
        dpg.add_text("Pooled proposal (all pooled runs):", color=TEXT_NORMAL, parent=container)
        dpg.add_text(proposal, wrap=wrap, color=TEXT_MUTED, parent=container)
        dpg.add_spacer(height=scaled(8), parent=container)
        with dpg.group(horizontal=True, parent=container):
            dpg.add_button(label="Apply selected", callback=on_apply, width=scaled(130))
            dpg.add_spacer(width=scaled(10))
            dpg.add_button(label="Clear pool", callback=on_clear, width=scaled(100))

    def show_calibration_result_dialog(self, summary: str, on_save):
        """Show the measured Go-Live calibration and offer to save it.

        The values are already applied to the running session; this dialog lets
        the operator persist them to the project (``on_save``) or keep them only
        for this session.  Explicit, never silent.
        """
        if dpg.does_item_exist("calibration_result_modal"):
            dpg.delete_item("calibration_result_modal")

        modal_width = scaled(460)
        modal_height = scaled(250)

        def on_save_project():
            if dpg.does_item_exist("calibration_result_modal"):
                dpg.delete_item("calibration_result_modal")
            if callable(on_save):
                on_save()

        def on_keep_session():
            if dpg.does_item_exist("calibration_result_modal"):
                dpg.delete_item("calibration_result_modal")

        with dpg.window(
            label="Scene Calibration",
            modal=True,
            autosize=True,
            tag="calibration_result_modal",
            width=modal_width,
            height=modal_height,
            pos=self._center_modal("calibration_result_modal", modal_width, modal_height),
            no_resize=True,
            no_move=True,
            no_close=True,
            no_collapse=True,
        ):
            dpg.add_spacer(height=scaled(8))
            dpg.add_text("Measured and applied to this session:",
                         color=TEXT_NORMAL)
            dpg.add_spacer(height=scaled(6))
            dpg.add_text(summary, wrap=scaled(430))
            dpg.add_spacer(height=scaled(14))
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Save to project",
                    callback=on_save_project,
                    width=scaled(150),
                )
                dpg.add_spacer(width=scaled(16))
                dpg.add_button(
                    label="Keep this session only",
                    callback=on_keep_session,
                    width=scaled(180),
                )

    def _on_show_qr(self):
        """Top-bar QR button → ask the app to show the phone-monitor QR."""
        cb = self.callbacks.get('show_qr')
        if cb:
            cb()

    def show_qr_dialog(self, url: str, matrix=None):
        """Modal showing a QR code (and the URL) for the phone web monitor.

        ``matrix`` is a list of rows of bools (QR modules) or None — when None
        the URL is still shown as text so it can be typed manually.
        """
        if dpg.does_item_exist("qr_modal"):
            dpg.delete_item("qr_modal")

        border = 3
        if matrix:
            n = len(matrix)
            ps = max(4, scaled(300) // (n + 2 * border))
            canvas = (n + 2 * border) * ps
        else:
            canvas = scaled(240)

        modal_w = canvas + scaled(40)
        modal_h = canvas + scaled(150)

        def on_close():
            if dpg.does_item_exist("qr_modal"):
                dpg.delete_item("qr_modal")

        with dpg.window(
            label="Phone Monitor",
            modal=True,
            autosize=True,
            tag="qr_modal",
            width=modal_w,
            height=modal_h,
            pos=self._center_modal("qr_modal", modal_w, modal_h),
            no_resize=True,
            no_collapse=True,
        ):
            dpg.add_spacer(height=scaled(6))
            dpg.add_text("Scan with a phone on the same Wi-Fi / hotspot:")
            dpg.add_spacer(height=scaled(6))
            if matrix:
                with dpg.drawlist(width=canvas, height=canvas):
                    dpg.draw_rectangle((0, 0), (canvas, canvas),
                                       fill=(255, 255, 255, 255), color=(255, 255, 255, 255))
                    for r, row in enumerate(matrix):
                        y = (border + r) * ps
                        ncols = len(row)
                        c = 0
                        while c < ncols:  # merge runs of dark modules into one rect
                            if row[c]:
                                c0 = c
                                while c < ncols and row[c]:
                                    c += 1
                                dpg.draw_rectangle(((border + c0) * ps, y),
                                                   ((border + c) * ps, y + ps),
                                                   fill=(0, 0, 0, 255), color=(0, 0, 0, 255))
                            else:
                                c += 1
            else:
                dpg.add_text("(install 'segno' in the venv to show a QR code)",
                             color=(220, 170, 90))
            dpg.add_spacer(height=scaled(8))
            dpg.add_input_text(default_value=url, readonly=True, width=canvas)
            dpg.add_spacer(height=scaled(8))
            dpg.add_button(label="Close", callback=on_close, width=scaled(100))


    def show_toast(self, message: str, duration: float = 3.0, color: tuple = WARN_AMBER):
        """Show a temporary toast notification at top-left of preview area.
        
        Args:
            message: The message to display
            duration: How long to show the toast (seconds)
            color: Text color (R, G, B)
        """
        # Remove existing toast if any
        if dpg.does_item_exist("toast_window"):
            dpg.delete_item("toast_window")
        
        toast_x, toast_y = TOAST_POS
        
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

        # Expired by render_frame() on the main thread; deleting from a
        # background thread races the render loop (DPG is not thread-safe).
        self._toast_deadline = time.monotonic() + duration

    def show_readiness_rows(self, rows):
        """Render Go-Live readiness rows into the phase-⑤ Verify panel.

        Called on the main-loop/command thread (DPG-safe).  ``rows`` is a list
        of {name, status, detail}; each line colored by ok/warn/fail/skip."""
        container = "readiness_rows_container"
        if not dpg.does_item_exist(container):
            return
        dpg.delete_item(container, children_only=True)
        palette = {"ok": OK_GREEN, "warn": WARN_AMBER,
                   "fail": ALERT_RED, "skip": TEXT_HINT}
        if not rows:
            dpg.add_text("No readiness results.", parent=container, color=TEXT_HINT)
            return
        for row in rows:
            status = str(row.get("status", "")).lower()
            color = palette.get(status, TEXT_NORMAL)
            line = (f"[{status.upper()}] {row.get('name', '')}: "
                    f"{row.get('detail', '')}")
            dpg.add_text(line, parent=container, color=color, wrap=scaled(340))

    def show_dryrun_result(self, summary, error=""):
        """Render the phase-⑤ dry-run replay summary into a single text widget.

        Uses ``dpg.set_value`` (not add/delete) because the dry-run posts from a
        background thread; set_value is the safe cross-thread DPG op."""
        tag = "dryrun_result_text"
        if not dpg.does_item_exist(tag):
            return
        if error:
            dpg.set_value(tag, f"Dry-run failed: {error}")
            dpg.configure_item(tag, color=ALERT_RED)
            return
        s = summary or {}
        text = (
            f"{s.get('video', '?')} - {s.get('frames_processed', '?')} frames\n"
            f"tracks: {s.get('real_tracks', '?')} real / "
            f"{s.get('marginal_tracks', '?')} marginal / "
            f"{s.get('ghost_tracks', '?')} ghost\n"
            f"swaps: {s.get('swap_count', '?')}   "
            f"zero-detection frames: {s.get('zero_detection_frames', '?')}\n"
            f"avg detections/frame: {s.get('avg_detections', '?')}"
        )
        dpg.set_value(tag, text)
        dpg.configure_item(tag, color=TEXT_NORMAL)

    def show_output_latency(self, latency_ms, enabled=False):
        """Render the phase-⑥ lagged-tap latency readout (Track X §7).

        Uses ``dpg.set_value`` only (the loop posts this from the runtime tick),
        the batch-2 cross-thread DPG rule (see ``show_dryrun_result``)."""
        tag = "lagged_latency_text"
        if not dpg.does_item_exist(tag):
            return
        if enabled and latency_ms > 0:
            text = f"lagged tap: {latency_ms:.0f} ms ({latency_ms / 1000.0:.2f} s)"
            color = TEXT_NORMAL
        else:
            text = "lagged tap: off"
            color = TEXT_DIM
        dpg.set_value(tag, text)
        dpg.configure_item(tag, color=color)

    def setup(self, width: int = VIEWPORT_BASE_W, height: int = VIEWPORT_BASE_H):
        """Setup viewport and prepare for rendering.

        Args:
            width: Viewport width (should already be DPI-scaled if called from app.py)
            height: Viewport height (should already be DPI-scaled if called from app.py)
        """
        # Min dimensions should be scaled for high DPI
        scaled_min = int(VIEWPORT_MIN * self._dpi_scale)
        
        dpg.create_viewport(
            title="WallDance Control Panel",
            width=width,
            height=height,
            min_width=scaled_min,
            min_height=scaled_min,
            vsync=False  # Disable vsync to prevent throttling when window is in background
        )
        dpg.setup_dearpygui()
        dpg.set_primary_window("main_window", True)
        dpg.set_viewport_resize_callback(self._on_viewport_resize)
        # Compute initial layout now that viewport dimensions are known
        self._recompute_layout()

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

        # Check for section mutual exclusion
        self._check_section_exclusion()

        # Expire the toast (main thread — see show_toast)
        if self._toast_deadline and time.monotonic() >= self._toast_deadline:
            self._toast_deadline = 0.0
            if dpg.does_item_exist("toast_window"):
                dpg.delete_item("toast_window")

        dpg.render_dearpygui_frame()
        return True
    
    def _check_section_exclusion(self):
        """Close other section headers when one is opened (mutual exclusion)."""
        # Find which section is currently open
        current_open = None
        for section in self._section_headers:
            if dpg.does_item_exist(section) and dpg.get_value(section):
                current_open = section
                break
        
        # If a new section was just opened, close the previous one
        if current_open and current_open != self._last_open_section:
            for section in self._section_headers:
                if section != current_open and dpg.does_item_exist(section):
                    dpg.set_value(section, False)
        
        self._last_open_section = current_open
    
    def stop(self):
        """Stop the GUI."""
        dpg.stop_dearpygui()
    
    def is_running(self) -> bool:
        """Check if GUI is still running."""
        return dpg.is_dearpygui_running()
