"""Project/profile/config persistence flows peeled from WallDanceApp.

DECOMPOSITION_PLAN §5 Phase 2 (4). Method bodies moved verbatim from
app.py; ``self.<app attribute>`` references renamed to constructor-injected
dependencies. The manager owns the ``ConfigStore``, the current project
name, the lighting-profile bundles and the deferred project-switch request
drained by the main loop.

The two whole-app config translators stay on the app and are injected as
callables: ``saveable_config()`` (reads every subsystem) and
``apply_config(config)`` (writes every subsystem) — they dissolve into the
Phase 3 command/event seam rather than into this manager.

Sibling controllers are injected under their app attribute names
(``models``/``cameras``/``recording``) so moved bodies keep their
references verbatim.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Dict, Optional, Protocol

import core.config_schema as config_schema
from core.config_store import ConfigStore, format_config_display, sanitize_project_name

try:
    from camera.ids_camera import CameraSource
except ImportError:
    CameraSource = None


class ConfigUiPort(Protocol):
    """The GUI surface the config cluster needs (no dpg types)."""

    @property
    def available(self) -> bool: ...

    def update_project_list(self, projects, current) -> None: ...

    def update_config_list(self, configs, current_display) -> None: ...

    def set_current_config(self, display: str) -> None: ...

    def show_save_config_dialog(self, project: str) -> None: ...

    def show_load_config_dialog(self, config_dir: str, project: str) -> None: ...

    def show_save_indicator(self, message: str) -> None: ...

    def show_toast(self, message: str, duration: float, color) -> None: ...

    def set_active_profile(self, name: str) -> None: ...

    def show_project_picker(self, rows, last_project: str) -> None: ...

    def sync_combo(self, name: str, value: str) -> None: ...

    def set_trt_checkbox(self, enabled: bool) -> None: ...

    def update_camera_sources(self, sources, current, unavailable) -> None: ...

    def update_camera_status(self, is_open: bool, source: str, reconnecting: bool) -> None: ...

    def set_camera_type(self, camera_type: str) -> None: ...


class ConfigManager:
    """Owns config persistence, project switching and lighting profiles."""

    def __init__(
        self,
        models,
        cameras,
        recording,
        recorder,
        camera,
        unified_camera,
        use_unified: bool,
        settings,
        ui: ConfigUiPort,
        watchdog: Callable[[], object],
        apply_config: Callable[[Dict], None],
        saveable_config: Callable[[], Dict],
        request_reprocess: Callable[[], None],
    ) -> None:
        self.models = models
        self.cameras = cameras
        self.recording = recording
        self.recorder = recorder
        self.camera = camera
        self.unified_camera = unified_camera
        self._use_unified_camera = use_unified
        self.settings = settings
        self.ui = ui
        self.watchdog = watchdog
        self.apply_config = apply_config
        self.saveable_config = saveable_config
        self.request_reprocess = request_reprocess

        self.config_store = ConfigStore()
        self._current_project = "default"
        # Lighting profiles (UX_PLAN U2): bundles of lighting-coupled values.
        # Empty bundles are seeded from live values on first switch.
        self._profiles: Dict[str, Dict] = {name: {} for name in config_schema.PROFILE_NAMES}
        self._active_profile: str = config_schema.DEFAULT_PROFILE
        self._pending_project_switch: Optional[str] = None  # Config filepath to switch to

    # ------------------------------------------------------------------
    # Top bar / project bookkeeping
    # ------------------------------------------------------------------
    def _update_topbar_state(self, selected_filepath: Optional[str] = None):
        projects = self.config_store.list_projects()
        if self.ui.available:
            self.ui.update_project_list(projects, self._current_project)

        history = self.config_store.project_history(self._current_project)

        current_display = ""
        if selected_filepath:
            selected_abs = os.path.abspath(selected_filepath)
            for display, path in history.configs:
                if os.path.abspath(path) == selected_abs:
                    current_display = display
                    break
            if not current_display:
                current_display = format_config_display(os.path.basename(selected_filepath))

        if not current_display and history.configs:
            current_display = history.configs[0][0]

        if self.ui.available:
            self.ui.update_config_list(history.configs, current_display)
            if current_display:
                self.ui.set_current_config(current_display)

    # ------------------------------------------------------------------
    # Project switch (startup + runtime unified path)
    # ------------------------------------------------------------------
    def _execute_project_switch(self, config_filepath: str):
        """Project switch can block the loop for minutes (model/TRT load) -
        suppress the loop watchdog for the duration."""
        self.watchdog().push_busy("project_switch")
        try:
            return self._execute_project_switch_impl(config_filepath)
        finally:
            self.watchdog().pop_busy()

    def _execute_project_switch_impl(self, config_filepath: str):
        """Execute a full project switch with proper cleanup and initialization.

        This is the unified path for both startup and runtime project switching.
        It ensures everything is properly closed and reinitialized.

        Args:
            config_filepath: Path to the config file to load
        """
        print(f"\n{'='*60}")
        print(f"[Project Switch] Starting switch to: {os.path.basename(config_filepath)}")
        print(f"{'='*60}")

        # 1. Stop any recording/playback (clear callback first)
        print("[Project Switch] Stopping recorder...")
        self.cameras._set_camera_frame_callback(None)
        self.recorder.stop_recording()
        self.recorder.stop_playback()

        # 2. Block processing
        self.models._model_loading = True

        # 3. Close camera
        camera_was_open = self.camera.state.is_open
        if camera_was_open:
            print("[Project Switch] Closing camera...")
            self.camera.close()
            self.camera.state.is_open = False

        # 4. Load the config (migrate to schema v2, keep both profile bundles,
        # then work on the flat view of the active profile)
        try:
            config = self.config_store.load(config_filepath)
        except Exception as e:
            print(f"[Project Switch] ERROR: Failed to load config: {e}")
            self.models._model_loading = False
            if camera_was_open:
                self.cameras._open_camera(self.camera.state.source)
            return False
        structured = config_schema.migrate(config)
        self._profiles = {n: dict(b) for n, b in structured["profiles"].items()}
        self._active_profile = structured.get("active_profile", config_schema.DEFAULT_PROFILE)
        flat, cfg_warnings = config_schema.validate_flat(config_schema.flatten(structured))
        self._report_config_warnings(cfg_warnings)
        config = flat

        # 5. Extract model info before applying config
        model_name = config.get("model", self.models.current_model_name)
        use_trt = config.get("use_tensorrt", False)
        self.models._trt_requested = bool(use_trt)
        new_imgsz = config.get("yolo_imgsz", self.settings.imgsz)
        base_name = model_name.replace('.pt', '').replace('.engine', '')

        print(f"[Project Switch] Target: model={model_name}, TRT={use_trt}, imgsz={new_imgsz}")

        # 6. Update imgsz in model manager BEFORE loading model
        self.settings.imgsz = new_imgsz
        self.models.model_manager.set_imgsz(new_imgsz)

        # 7. Determine if we need to reload the model
        need_model_reload = (
            base_name != self.models.current_model_name or
            new_imgsz != self.settings.imgsz or
            use_trt != self.models.model_manager.is_using_tensorrt()
        )

        # For TRT, we always need to reload if imgsz changed (engines are size-specific)
        if self.models.model_manager.is_using_tensorrt() or use_trt:
            need_model_reload = True

        # 8. Load the model if needed
        if need_model_reload:
            # Check if TRT engine exists
            force_pt = not use_trt
            if use_trt and not self.models.model_manager.engine_exists(base_name):
                from core.model_manager import is_tensorrt_available
                if is_tensorrt_available():
                    # Prompt user before starting long TRT build
                    if self.models._prompt_trt_build_sync(base_name):
                        print(f"[Project Switch] User accepted TRT build for {base_name}@{new_imgsz}")
                        force_pt = False
                    else:
                        print(f"[Project Switch] User declined TRT build, using PyTorch")
                        force_pt = True
                        use_trt = False
                        self.models._trt_requested = False
                else:
                    # Keep _trt_requested True: the banner must flag the fallback
                    print(f"[Project Switch] TRT not available, using PyTorch")
                    force_pt = True
                    use_trt = False

            print(f"[Project Switch] Loading model {model_name}... (TRT={use_trt}, force_pt={force_pt})")
            if not self.models._load_model_with_progress(model_name, force_pt=force_pt):
                print(f"[Project Switch] ERROR: Failed to load model")
                self.models._model_loading = False
                if camera_was_open:
                    self.cameras._open_camera(self.camera.state.source)
                return False

        # 9. Apply the rest of the config (skip model since we just loaded it)
        # Also skip imgsz since we already set it
        print("[Project Switch] Applying config settings...")
        self.apply_config(config)

        # 10. Update project tracking
        self._current_project = self.config_store.infer_project_from_config(config, config_filepath)
        self.config_store.remember_last_project(self._current_project)

        # 11. Update recorder
        self.recorder.set_project(self._current_project)

        # 12. Clear any pending operations that were set during config apply
        self.models._pending_model_switch = None
        self.models._pending_trt_switch = None
        self.models._pending_trt_build = None
        self.models._pending_model_for_trt_build = None

        # 13. Reopen camera if it was open (using new camera source from config if specified)
        camera_source = config.get("camera_source", self.camera.state.source)
        if camera_was_open or camera_source != self.camera.state.source:
            print(f"[Project Switch] Opening camera {camera_source}...")
            if self.cameras._attempt_camera_connect(camera_source):
                time.sleep(0.3)
                if self.camera.cap:
                    for _ in range(5):
                        self.camera.cap.grab()

        # 14. Update UI
        self._update_topbar_state(selected_filepath=config_filepath)
        self.recording._update_recording_ui()
        if self.ui.available:
            self.ui.sync_combo("model", base_name)
            self.ui.set_trt_checkbox(self.models.model_manager.is_using_tensorrt())
            self.models._update_trt_banner()
            self.ui.set_active_profile(self._active_profile)
            self.ui.update_camera_sources(self.cameras._camera_ui_sources(), self.camera.state.source, self.camera.state.unavailable)

            cam_type_str = ""
            if self._use_unified_camera and self.unified_camera is not None and self.unified_camera.is_open:
                if self.unified_camera.source_type == CameraSource.IDS_PEAK:
                    cam_type_str = "IDS_PEAK"
                else:
                    cam_type_str = "OPENCV"
            elif self.camera.state.is_open:
                cam_type_str = "OPENCV"
            self.ui.set_camera_type(cam_type_str)

            self.ui.update_camera_status(
                self.camera.state.is_open,
                self.camera.state.source,
                reconnecting=self.cameras._camera_reconnecting,
            )

        # 15. Resume processing
        self.models._model_loading = False

        print(f"[Project Switch] Complete: {self._current_project}")
        print(f"{'='*60}\n")
        return True

    # ------------------------------------------------------------------
    # Save / load / profile callbacks
    # ------------------------------------------------------------------
    def _cb_save_config(self):
        self._cb_do_save_config(self._current_project)

    def _cb_save_as_config(self):
        if self.ui.available:
            self.ui.show_save_config_dialog(self._current_project)

    def _cb_load_config(self):
        if self.ui.available:
            self.ui.show_load_config_dialog(self.config_store.config_dir, self._current_project)

    def _snapshot_active_profile(self) -> Dict:
        """Capture live lighting-coupled values into the active profile bundle."""
        _, profile = config_schema.split_profile(self.saveable_config())
        self._profiles[self._active_profile] = dict(profile)
        return profile

    def _get_structured_config(self) -> Dict:
        """v2 payload for persistence: current values + the inactive profile bundle."""
        self._snapshot_active_profile()
        return config_schema.structure(
            self.saveable_config(), self._profiles, self._active_profile)

    def _cb_profile_switch(self, profile_name: str):
        """Top-bar switch: apply the other lighting profile atomically
        (pipeline values + IDS hardware via the existing apply path)."""
        name = str(profile_name).lower()
        if name not in config_schema.PROFILE_NAMES or name == self._active_profile:
            return
        current = self._snapshot_active_profile()
        self._active_profile = name
        # First switch to a never-calibrated profile: seed it from the current
        # one so the operator starts from a working state, then calibrates.
        bundle = self._profiles.get(name) or dict(current)
        self._profiles[name] = dict(bundle)
        print(f"[Profile] Switching lighting profile -> {name}")
        self.apply_config(bundle)
        self.request_reprocess()
        if self.ui.available:
            self.ui.set_active_profile(name)
            self.ui.show_toast(f"Lighting profile: {name.upper()}",
                               duration=2.5, color=(150, 200, 255))

    def _cb_do_save_config(self, project_name: str):
        filepath = self.config_store.save(project_name, self._get_structured_config())
        new_project = sanitize_project_name(project_name)
        # Only switch recorder project if the name actually changed
        # (avoids stopping playback when saving to the same project)
        if new_project != self._current_project:
            self._current_project = new_project
            self.recorder.set_project(self._current_project)
            self.recording._update_recording_ui()  # Refresh slots for new project
        self._update_topbar_state()
        if self.ui.available:
            self.ui.show_save_indicator("Saved!")
        print(f"Config saved: {filepath}")

    def _cb_do_load_config(self, filepath: str):
        """Handle config load request - defers to main loop for proper sequencing."""
        # Defer the actual project switch to main loop to avoid race conditions
        # The main loop will call _execute_project_switch() which handles:
        # - Stopping recording/playback
        # - Closing camera
        # - Loading model with correct imgsz
        # - Applying config
        # - Reopening camera
        print(f"Queuing project switch to: {os.path.basename(filepath)}")
        self._pending_project_switch = filepath
        self.models._model_loading = True  # Block processing immediately

    def _cb_project_select(self, project_name: str):
        latest = self.config_store.latest_for_project(project_name)
        if latest:
            self._cb_do_load_config(latest)
            print(f"Loaded latest config for project: {project_name}")
        else:
            print(f"No configs found for project: {project_name}")

    def _cb_config_select(self, project_name: str, config_display: str):
        history = self.config_store.project_history(project_name)
        for display, filepath in history.configs:
            if display == config_display:
                self._cb_do_load_config(filepath)
                return
        print(f"Config not found: {config_display}")

    # ------------------------------------------------------------------
    # Startup project picker (ROADMAP §7B)
    # ------------------------------------------------------------------
    def _show_startup_project_picker(self):
        """Populate and show the launch-time project picker."""
        if not self.ui.available:
            return
        infos = self.config_store.list_projects_by_date()
        rows = [(i.name, i.last_saved_display, i.save_count) for i in infos]
        last = self.config_store.read_last_project() or ""
        self.ui.show_project_picker(rows, last_project=last)

    def _cb_project_launch(self, name: str):
        """Picker 'Launch' / Enter → queue a switch to the project's latest config."""
        print(f"[Picker] Launching project: {name}")
        self._cb_project_select(name)

    def _cb_project_rename(self, old: str, new: str):
        result = self.config_store.rename_project(old, new)
        if result is None:
            if self.ui.available:
                self.ui.show_toast("Rename failed - name already in use?",
                                   duration=3.0, color=(255, 180, 80))
        else:
            print(f"[Picker] Renamed '{old}' -> '{result}'")
            if self._current_project == old:
                self._current_project = result
        self._show_startup_project_picker()  # refresh the list

    def _cb_project_delete(self, name: str):
        if self.config_store.delete_project(name):
            print(f"[Picker] Deleted project: {name}")
        elif self.ui.available:
            self.ui.show_toast(f"Could not delete '{name}'",
                               duration=3.0, color=(255, 180, 80))
        self._show_startup_project_picker()  # refresh (may now be empty)

    def _cb_project_blank(self):
        """Picker 'Start blank' → load the default model, no project."""
        print("[Picker] Starting blank (default model)")
        self.models._load_default_model_startup()

    # ------------------------------------------------------------------
    # Safe defaults / validation warnings
    # ------------------------------------------------------------------
    def _cb_save_safe_defaults(self):
        """Save current settings as safe defaults for this project."""
        filepath = self.config_store.save_safe_defaults(self._current_project, self._get_structured_config())
        if self.ui.available:
            self.ui.show_save_indicator("Safe defaults saved!")
        print(f"Safe defaults saved: {filepath}")

    def _report_config_warnings(self, cfg_warnings):
        """Console detail + one summary toast for config-load validation warnings."""
        for w in cfg_warnings:
            print(f"[Config] {w}")
        if cfg_warnings:
            if self.ui.available:
                self.ui.show_toast(
                    f"Config: {len(cfg_warnings)} value(s) adjusted on load - see console",
                    duration=4.0, color=(255, 200, 100),
                )

    def _cb_load_safe_defaults(self):
        """Load safe defaults for this project."""
        raw = self.config_store.load_safe_defaults(self._current_project)
        if raw:
            structured = config_schema.migrate(raw)
            config, cfg_warnings = config_schema.validate_flat(config_schema.flatten(structured))
            self._report_config_warnings(cfg_warnings)
            # Check if model or imgsz would change
            model_changes = config.get("model", self.models.current_model_name) != self.models.current_model_name
            imgsz_changes = config.get("yolo_imgsz", self.settings.imgsz) != self.settings.imgsz
            trt_changes = config.get("use_tensorrt", self.models.model_manager.is_using_tensorrt()) != self.models.model_manager.is_using_tensorrt()

            if model_changes or imgsz_changes or trt_changes:
                # Need full project switch for model/imgsz/trt changes
                # Save the config temporarily and trigger a project switch
                temp_path = self.config_store.save(self._current_project, structured)
                self._pending_project_switch = temp_path
                self.models._model_loading = True
            else:
                # No model changes, just apply config directly
                self._profiles = {n: dict(b) for n, b in structured["profiles"].items()}
                self._active_profile = structured.get("active_profile", config_schema.DEFAULT_PROFILE)
                self.apply_config(config)
                if self.ui.available:
                    self.ui.set_active_profile(self._active_profile)
            if self.ui.available:
                self.ui.show_save_indicator("Safe defaults loaded!")
            print(f"Safe defaults loaded for project: {self._current_project}")
        else:
            print(f"No safe defaults found for project: {self._current_project}")
