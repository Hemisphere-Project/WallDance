"""DPG adapter: the only translation point between the runtime seam and dpg.

DECOMPOSITION_PLAN §4/§5 Phase 3. Two directions:

events -> dpg      ``DpgUiAdapter`` subscribes to the ``EventBus`` and maps
                   every runtime event to the corresponding ``WallDanceGUI``
                   call (1:1 with the former direct ``app.gui.*`` pushes).
dpg -> commands    The ``callbacks`` dict handed to ``WallDanceGUI`` turns
                   every GUI callback into a ``RuntimeAPI.submit`` of a typed
                   command; the keyboard handler (moved here from app.py)
                   does the same for shortcuts.

The adapter also absorbs app.py's direct dpg usage: viewport setup, input
handler registration, the render pumps, ``is_running`` and context teardown.
``render_frame()`` (the GUI wrapper: toast expiry + section exclusion) vs
``render_frame_raw()`` (bare dpg render) preserves the main loop's existing
split -- the loop tail and the model-load pump always used the bare render.

Synchronous exceptions to the event flow (documented in runtime/api.py):
``show_tensorrt_prompt`` (blocking modal the model controller pumps on) and
``consume_layout_change`` (per-tick layout query).
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

import dearpygui.dearpygui as dpg

from gui import WallDanceGUI, get_display_scale
from runtime import api
from ui.calibrate_all_wizard import CalibrateAllWizard


class DpgUiAdapter:
    """Owns the WallDanceGUI instance and both seam directions."""

    def __init__(self, runtime_api: api.RuntimeAPI, bus: api.EventBus) -> None:
        self.api = runtime_api
        self.bus = bus
        self.gui: Optional[WallDanceGUI] = None
        self.wizard: Optional[CalibrateAllWizard] = None
        self._warned_events = set()
        self._event_handlers = self._build_event_handlers()
        bus.subscribe(self._on_event)

    # ------------------------------------------------------------------
    # Lifecycle (the dpg calls that used to live in app.run())
    # ------------------------------------------------------------------
    def create_gui(self, config: Dict) -> None:
        """Build the GUI (creates the dpg context) and mark the bus ready."""
        self.gui = WallDanceGUI(config=config, callbacks=self._build_callbacks())
        # Calibrate All wizard: a second, GUI-local client of the calibration
        # command/event vocabulary (desktop precursor of the Phase 5 tablet
        # wizard). Created with the GUI; opening it has no runtime effect.
        self.wizard = CalibrateAllWizard(self.gui, self.api.submit)
        self.bus.ui_ready = True

    def setup_viewport(self, roi) -> None:
        """Viewport sizing + input handler registration + show.

        ``roi`` is the ui-side RoiMaskEditor whose mouse handlers feed the
        drag/paint state polled by the main loop.
        """
        dpi_scale = get_display_scale()
        # Fixed viewport size - layout engine fits the preview to whatever
        # space is available
        window_width = int(1340 * dpi_scale)
        window_height = int(900 * dpi_scale)
        self.gui.setup(width=window_width, height=window_height)
        with dpg.handler_registry():
            dpg.add_key_press_handler(callback=self._handle_key)
            dpg.add_mouse_down_handler(callback=roi._handle_roi_mouse_down)
            dpg.add_mouse_move_handler(callback=roi._handle_roi_mouse_move)
            dpg.add_mouse_release_handler(callback=roi._handle_roi_mouse_up)
            dpg.add_mouse_down_handler(callback=roi._handle_mask_mouse_down)
            dpg.add_mouse_move_handler(callback=roi._handle_mask_mouse_move)
            dpg.add_mouse_release_handler(callback=roi._handle_mask_mouse_up)
            dpg.add_mouse_double_click_handler(callback=roi._handle_preview_double_click)
        # Let the phase rail drop ROI/mask edit modes when leaving the Rig phase.
        self.gui._exit_edit_modes = roi.exit_edit_modes
        dpg.show_viewport()

    def is_running(self) -> bool:
        return dpg.is_dearpygui_running()

    def render_frame(self) -> None:
        """GUI-wrapped render: toast expiry + section exclusion (wait paths)."""
        if self.gui is not None:
            self.gui.render_frame()

    def render_frame_raw(self) -> None:
        """Bare dpg render (main-loop tail + model-load pump), as before."""
        dpg.render_dearpygui_frame()

    def destroy(self) -> None:
        dpg.destroy_context()

    # ------------------------------------------------------------------
    # Synchronous port surface (documented exceptions to the event flow)
    # ------------------------------------------------------------------
    def show_tensorrt_prompt(self, model_name: str, on_choice) -> None:
        """Blocking-modal prompt; the model controller pumps render_frame_raw
        until ``on_choice`` fires from the dpg callback thread."""
        self.gui.show_tensorrt_prompt(model_name, on_choice)

    def consume_layout_change(self) -> Optional[tuple]:
        """(render_scale, camera_w, camera_h) after a viewport/camera-dims
        layout recompute, else None. Clears the dirty flag."""
        gui = self.gui
        if gui is None or not gui._layout_dirty:
            return None
        gui._layout_dirty = False
        return (gui._fitted_render_scale, gui._camera_width, gui._camera_height)

    # ------------------------------------------------------------------
    # dpg-callbacks -> commands
    # ------------------------------------------------------------------
    def _on_report_issue_request(self):
        """Synchronous-return callback in gui: returning None makes the GUI
        skip opening the dialog itself; it opens via IssueReportContext."""
        self.api.submit(api.RequestIssueReport())
        return None

    def _build_callbacks(self) -> Dict[str, Callable]:
        submit = self.api.submit
        return {
            "show_qr": lambda: submit(api.ShowQr()),
            "on_system_state_change": lambda state, old_state: submit(
                api.SetState(state.name.lower())),
            "on_enhance_toggle": lambda v: submit(api.ToggleEnhance(bool(v))),
            "on_enhance_lite_toggle": lambda v: submit(api.ToggleEnhanceLite(bool(v))),
            "on_enhance_force_toggle": lambda v: submit(api.ToggleEnhanceForce(bool(v))),
            "on_greyscale_toggle": lambda v: submit(api.ToggleGreyscale(bool(v))),
            "on_brightness_threshold_change": lambda v: submit(
                api.SetEnhanceParam("brightness_threshold", v)),
            "on_clahe_change": lambda v: submit(api.SetEnhanceParam("clahe", v)),
            "on_gamma_change": lambda v: submit(api.SetEnhanceParam("gamma", v)),
            "on_denoise_change": lambda v: submit(api.SetEnhanceParam("denoise", v)),
            "on_bg_capture": lambda: submit(api.BgCapture()),
            "on_bg_enable_toggle": lambda v: submit(api.ToggleBgSubtract(bool(v))),
            "on_bg_clear": lambda: submit(api.BgClear()),
            "on_bg_sensitivity_change": lambda v: submit(api.SetBgSensitivity(v)),
            "on_confidence_change": lambda v: submit(api.SetConfidence(v)),
            "on_sensitivity_change": lambda v: submit(api.SetSensitivity(v)),
            "on_motion_sensitivity_change": lambda v: submit(api.SetMotionSensitivity(v)),
            "on_output_smoothing_change": lambda v: submit(api.SetOutputSmoothing(int(v))),
            "on_box_clamp_toggle": lambda v: submit(api.ToggleBoxClamp(bool(v))),
            "on_model_change": lambda name: submit(api.LoadModel(name)),
            "on_trt_toggle": lambda v: submit(api.ToggleTrt(bool(v))),
            "on_trt_rebuild": lambda: submit(api.RebuildTrt()),
            "on_ids_ratio_change": lambda v: submit(api.SetIdsParam("ratio", v)),
            "on_ids_gain_change": lambda v: submit(api.SetIdsParam("gain_db", v)),
            "on_ids_exposure_change": lambda v: submit(api.SetIdsParam("exposure_us", v)),
            "on_camera_change": lambda src: submit(api.SelectSource(src)),
            "on_camera_refresh": lambda: submit(api.RefreshCameras()),
            "on_imgsz_change": lambda v: submit(api.SetImgsz(int(v))),
            "on_person_height_change": lambda v: submit(api.SetPersonHeight(int(v))),
            "on_calibrate": lambda: submit(api.StartCalibration()),
            "on_calibrate_all": lambda: self.wizard and self.wizard.open(),
            "on_calib2": lambda: submit(api.StartDancersRun()),
            "on_calib2_apply": lambda sel: submit(api.ApplyCalib2(list(sel))),
            "on_calib2_clear": lambda: submit(api.ClearCalib2Pool()),
            "on_view_calib2_pool": lambda: submit(api.ViewCalib2Pool()),
            "on_visualization_toggle": lambda name, v: submit(
                api.ToggleOverlay(name, bool(v))),
            "on_tracker_age_change": lambda v: submit(api.SetTrackerMaxAge(int(v))),
            "on_mog2_scale_change": lambda v: submit(api.SetMog2Scale(v)),
            "on_tracker_reset": lambda: submit(api.ResetTracker()),
            "on_osc_toggle": lambda v: submit(api.ToggleOsc(bool(v))),
            "on_osc_config": lambda ip, port: submit(api.SetOscTarget(ip, port)),
            "on_preview_toggle": lambda v: submit(api.TogglePreview(bool(v))),
            "on_input_fps_cap_toggle": lambda v: submit(api.ToggleInputFpsCap(bool(v))),
            "on_preview_cap_toggle": lambda v: submit(api.TogglePreviewCap(bool(v))),
            "on_preview_scale_change": lambda v: submit(api.SetPreviewScale(v)),
            "on_roi_toggle": lambda v: submit(api.SetRoi(bool(v))),
            "on_roi_reset": lambda: submit(api.ResetRoi()),
            "on_mask_edit_toggle": lambda: submit(api.EditMask()),
            "on_mask_clear": lambda: submit(api.ClearMask()),
            "on_save_config": lambda: submit(api.SaveConfig()),
            "on_save_as_config": lambda: submit(api.SaveConfigAs()),
            "on_save_safe_defaults": lambda: submit(api.SaveSafeDefaults()),
            "on_load_safe_defaults": lambda: submit(api.LoadSafeDefaults()),
            "on_load_config": lambda: submit(api.RequestLoadConfigDialog()),
            "on_do_save_config": lambda name: submit(api.SaveConfig(name)),
            "on_do_load_config": lambda fp: submit(api.LoadConfig(fp)),
            "on_profile_switch": lambda name: submit(api.SwitchProfile(name)),
            "on_project_select": lambda name: submit(api.SelectProject(name)),
            "on_config_select": lambda proj, disp: submit(
                api.SelectConfigVersion(proj, disp)),
            "on_project_launch": lambda name: submit(api.LaunchProject(name)),
            "on_project_rename": lambda old, new: submit(api.RenameProject(old, new)),
            "on_project_delete": lambda name: submit(api.DeleteProject(name)),
            "on_project_blank": lambda: submit(api.StartBlankProject()),
            "on_rec_live": lambda: submit(api.PlaybackControl("live")),
            "on_rec_toggle": lambda: submit(api.PlaybackControl("record_toggle")),
            "on_rec_slot_click": lambda slot, ctrl: submit(
                api.SelectSlot(int(slot), bool(ctrl))),
            "on_playback_speed_change": lambda speed: submit(
                api.PlaybackControl("speed", float(speed))),
            "on_playback_pause": lambda: submit(api.PlaybackControl("pause_toggle")),
            "on_playback_force_pause": lambda: submit(api.PlaybackControl("force_pause")),
            "on_playback_next_frame": lambda: submit(api.PlaybackControl("next_frame")),
            "on_playback_prev_frame": lambda: submit(api.PlaybackControl("prev_frame")),
            "on_report_issue_request": self._on_report_issue_request,
            "on_issue_submit": lambda ctx, issue_type, note: submit(
                api.SubmitIssue(ctx, issue_type, note)),
            "on_issue_dialog_closed": lambda: submit(api.IssueDialogClosed()),
            "on_quit": lambda: submit(api.Quit()),
        }

    def _handle_key(self, sender, app_data):
        """Keyboard shortcuts (moved from app.py; runtime effects -> commands)."""
        if dpg.does_item_exist("issue_report_dialog"):
            return
        if self.wizard is not None and self.wizard.active:
            return  # modal wizard open: suppress shortcuts (like the picker)

        key = app_data
        gui = self.gui
        submit = self.api.submit
        # Project picker: suppress all shortcuts while it is open (so typing a
        # project name into the inline rename field doesn't trigger them). Enter
        # launches the highlighted project, except while an inline rename/delete
        # prompt is active.
        if gui and gui.project_picker_visible():
            if (key == dpg.mvKey_Return
                    and not gui.project_picker_inline_active()):
                sel = gui.project_picker_selection()
                if sel:
                    gui.hide_project_picker()
                    submit(api.LaunchProject(sel))
            return
        ctrl_down = dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)
        shift_down = dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift)
        if key == dpg.mvKey_E and ctrl_down and shift_down:
            if gui:
                gui.set_expert_mode(not gui.expert_mode)
                print(f"Expert mode: {'ON' if gui.expert_mode else 'OFF'}")
        elif key == dpg.mvKey_E and not ctrl_down:
            submit(api.ToggleEnhance(enabled=None, quiet=True))
        elif key == dpg.mvKey_T:
            submit(api.ToggleOverlay("trails"))
        elif key == dpg.mvKey_S and not ctrl_down:
            submit(api.ToggleOverlay("skeleton"))
        elif key == dpg.mvKey_K:
            submit(api.ToggleOverlay("keypoints"))
        elif key == dpg.mvKey_B:
            submit(api.ToggleOverlay("bbox"))
        elif key == dpg.mvKey_I:
            submit(api.ToggleOverlay("ids"))
        elif key == dpg.mvKey_P:
            submit(api.TogglePreview(enabled=None, quiet=True))
        elif key == dpg.mvKey_F8:
            submit(api.RequestIssueReport())
        if key == dpg.mvKey_S and ctrl_down:
            submit(api.SaveConfig())

    # ------------------------------------------------------------------
    # events -> dpg
    # ------------------------------------------------------------------
    def _on_event(self, event: api.Event) -> None:
        if self.gui is None:
            return  # pre-GUI events are dropped (old `if self.gui` guards)
        handler = self._event_handlers.get(type(event))
        if handler is None:
            name = type(event).__name__
            if name not in self._warned_events:
                self._warned_events.add(name)
                print(f"[DpgUiAdapter] no handler for event {name} -- ignored")
            return
        handler(event)

    def _sync_control(self, e: api.ControlSync) -> None:
        gui = self.gui
        if e.kind == "slider":
            gui.sync_slider(e.name, e.value)
        elif e.kind == "checkbox":
            gui.sync_checkbox(e.name, e.value)
        elif e.kind == "combo":
            gui.sync_combo(e.name, e.value)
        elif e.kind == "input":
            gui.sync_input(e.name, e.value)

    def _build_event_handlers(self) -> Dict[type, Callable]:
        submit = self.api.submit
        return {
            api.ControlSync: self._sync_control,
            api.Toast: lambda e: self.gui.show_toast(
                e.message, duration=e.duration, color=tuple(e.color)),
            api.Alert: lambda e: self.gui.show_toast(
                f"/!\\ {e.message}", duration=8.0, color=(255, 80, 80)),
            # Calibration events route through the wizard first; when it is
            # closed (or mid-intro) they fall back to the classic dialogs, so
            # the plain CALIBRATE / DANCERS buttons behave exactly as before.
            api.CalibProgress: lambda e: (
                self.wizard.on_progress(e.text),
                self.gui.set_calibrate_status(e.text)),
            api.CalibReportCard: lambda e: (
                None if self.wizard.on_report_card(e.summary)
                else self.gui.show_calibration_result_dialog(
                    e.summary, on_save=lambda: submit(api.SaveConfig()))),
            api.Calib2PoolChanged: lambda e: (
                None if self.wizard.on_pool_changed(e.rows, e.proposal)
                else self.gui.show_calib2_dialog(e.rows, e.proposal)),
            api.ConfigSaved: lambda e: self.gui.show_save_indicator(e.message),
            api.ConfigList: lambda e: self.gui.update_config_list(
                e.configs, e.current_display),
            api.CurrentConfig: lambda e: self.gui.set_current_config(e.display),
            api.ProjectList: lambda e: self.gui.update_project_list(
                e.projects, e.current),
            api.ProjectPicker: lambda e: self.gui.show_project_picker(
                e.rows, last_project=e.last_project),
            api.SaveConfigDialog: lambda e: self.gui.show_save_config_dialog(e.project),
            api.LoadConfigDialog: lambda e: self.gui.show_load_config_dialog(
                e.config_dir, e.project),
            api.ActiveProfile: lambda e: self.gui.set_active_profile(e.name),
            api.CameraStatus: lambda e: self.gui.update_camera_status(
                e.is_open, e.source, reconnecting=e.reconnecting),
            api.CameraSources: lambda e: self.gui.update_camera_sources(
                e.sources, e.current, e.unavailable),
            api.CameraType: lambda e: self.gui.config.__setitem__(
                "camera_type", e.camera_type),
            api.CameraDimensions: lambda e: self.gui.set_camera_dimensions(
                e.width, e.height),
            api.EngineBadge: lambda e: self.gui.update_engine_type_badge(e.is_trt),
            api.TrtBanner: lambda e: self.gui.update_trt_banner(
                e.text, exporting=e.exporting),
            api.TrtCheckbox: lambda e: self.gui.set_trt_checkbox(e.enabled),
            api.ComputeModeBadge: lambda e: self.gui.update_compute_mode_badge(e.reason),
            api.ModelDropdown: lambda e: self.gui.update_model_dropdown(e.name),
            api.ModelLoadModal: lambda e: self.gui.show_model_loading_modal(e.message),
            api.ModelLoadProgress: lambda e: self.gui.update_model_loading_progress(
                e.message, e.progress, e.detail, animate=e.animate),
            api.ModelLoadModalHide: lambda e: self.gui.hide_model_loading_modal(),
            api.RecordingUi: lambda e: self.gui.update_recording_ui(**e.payload),
            api.SlotHistory: lambda e: self.gui.show_slot_history_menu(
                e.slot, e.recordings,
                lambda fp, slot=e.slot: submit(api.PlaySlotRecording(slot, fp))),
            api.BgStatus: lambda e: self.gui.update_bg_status(
                e.has_reference, e.enabled, e.fg_ratio, e.mismatched),
            api.StatsTick: lambda e: self.gui.update_stats(**e.payload),
            api.GpuStats: lambda e: self.gui.update_gpu_stats(),
            api.IssueReportContext: lambda e: self.gui.show_issue_report_dialog(
                e.context),
            api.QrDialog: lambda e: self.gui.show_qr_dialog(e.url, e.matrix),
            api.PreviewFrame: lambda e: self.gui.update_frame(e.frame),
            api.PreviewResize: lambda e: self.gui.resize_preview(e.width, e.height),
            # The GUI flips its own STANDBY/RUN visuals on click; the runtime
            # mirror publishes StateChanged for *other* clients (tablet later).
            api.StateChanged: lambda e: None,
        }
