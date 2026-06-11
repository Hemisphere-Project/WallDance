"""Model load/switch + TensorRT build orchestration peeled from WallDanceApp.

DECOMPOSITION_PLAN §5 Phase 2 (2). Method bodies moved verbatim from
app.py; ``self.<app attribute>`` references renamed to constructor-injected
dependencies. The constructor takes narrow ports (Protocols below) and
late-bound callables — never the app instance.

The controller owns the model state that used to live on the app:
``model``, ``model_manager``, ``current_model``, ``current_model_name``,
``_model_loaded``/``_model_loading`` and the pending switch/build requests
drained by the main loop (``_drain_pending_trt_build`` /
``_drain_pending_model_switch``).

Threading model is unchanged: GUI callbacks only queue pending requests;
the main loop drains them; ``_load_model_with_progress`` blocks the main
thread while a worker thread loads, pumping the GUI via the ui port.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Optional, Protocol

from core.config import MODELS_DIR, USE_TENSORRT, YOLO_IMGSZ, YOLO_MODEL
from core.model_manager import ModelManager, ModelProgress, ModelStatus


class ModelUiPort(Protocol):
    """The GUI surface the model cluster needs (no dpg types).

    ``render_frame`` pumps one GUI frame (the modal/prompt wait loops);
    ``available`` is False until the GUI exists.
    """

    @property
    def available(self) -> bool: ...

    def show_model_loading_modal(self, message: str) -> None: ...

    def update_model_loading_progress(self, message: str, progress: float,
                                      detail: str, animate: bool = False) -> None: ...

    def hide_model_loading_modal(self) -> None: ...

    def update_engine_type_badge(self, is_trt: bool) -> None: ...

    def show_toast(self, message: str, duration: float, color) -> None: ...

    def set_trt_checkbox(self, enabled: bool) -> None: ...

    def sync_model_combo(self, name: str) -> None: ...

    def update_model_dropdown(self, name: str) -> None: ...

    def update_trt_banner(self, text: Optional[str], exporting: bool = False) -> None: ...

    def show_tensorrt_prompt(self, model_name: str, on_choice) -> None: ...

    def update_gpu_stats(self) -> None: ...

    def render_frame(self) -> None: ...


class ModelCameraPort(Protocol):
    """Camera pause/resume around a model load (buffer-overflow guard)."""

    def snapshot(self) -> tuple: ...

    def close(self) -> None: ...

    def reopen_and_flush(self, source) -> None: ...


class ModelController:
    """Owns model identity/loading state and the TRT build/switch flows."""

    def __init__(
        self,
        models_dir: str,
        ui: ModelUiPort,
        camera: ModelCameraPort,
        processor: Callable[[], object],
        watchdog: Callable[[], object],
        restore_playback_dims: Callable[[], None],
        update_topbar: Callable[[], None],
    ) -> None:
        self.ui = ui
        self.camera = camera
        self.processor = processor
        self.watchdog = watchdog
        self.restore_playback_dims = restore_playback_dims
        self.update_topbar = update_topbar

        self.model = None
        self.model_manager = ModelManager(models_dir, use_tensorrt=USE_TENSORRT, imgsz=YOLO_IMGSZ)
        self.current_model = YOLO_MODEL
        self.current_model_name = YOLO_MODEL.replace(".pt", "").replace(".engine", "")
        self._model_loaded = False
        self._model_loading = False  # True while model is being loaded/switched
        self._pending_model_switch: Optional[str] = None  # Deferred model switch
        self._pending_trt_switch: Optional[bool] = None  # True=switch to TRT, False=switch to PT
        self._pending_trt_build: Optional[str] = None  # Model name to build TRT engine for
        self._pending_model_for_trt_build: Optional[str] = None  # Model to switch to after TRT build prompt
        # Intent vs reality: requested follows config/operator; the banner fires
        # when requested but the loaded model fell back to PyTorch.
        self._trt_requested: bool = USE_TENSORRT

    @staticmethod
    def _is_trt_input_size_mismatch_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "input size" in msg and "max model size" in msg and "not equal to" in msg

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------
    def _load_default_model_startup(self) -> bool:
        """Load the default model at startup (no project). Returns success."""
        print("No project, loading default model...")
        force_pt_default = not USE_TENSORRT
        if USE_TENSORRT:
            from core.model_manager import is_tensorrt_available
            base_default = YOLO_MODEL.replace('.pt', '').replace('.engine', '')
            if is_tensorrt_available() and not self.model_manager.engine_exists(base_default):
                if self._prompt_trt_build_sync(base_default):
                    print("User accepted TRT build at startup")
                    force_pt_default = False
                else:
                    print("User declined TRT build at startup, using PyTorch")
                    force_pt_default = True
                    self._trt_requested = False
            elif not is_tensorrt_available():
                force_pt_default = True
        if not self._load_model_with_progress(YOLO_MODEL, force_pt=force_pt_default):
            return False
        self.current_model_name = YOLO_MODEL.replace('.pt', '').replace('.engine', '')
        if self.ui.available:
            self.ui.sync_model_combo(self.current_model_name)
            self.ui.set_trt_checkbox(self.model_manager.is_using_tensorrt())
        self._update_trt_banner()
        self.update_topbar()
        return True

    # ------------------------------------------------------------------
    # GUI callbacks (queue pending requests; main loop drains)
    # ------------------------------------------------------------------
    def _cb_model_change(self, model_name: str):
        """Handle model change from GUI dropdown.

        Note: This is called from a DearPyGui callback during render_frame().
        We defer the actual loading to the main loop to avoid race conditions.

        If TRT checkbox is checked:
        - Check if engine exists for new model
        - If not, prompt to build (via _pending_trt_build)
        - If user declines, switch model but disable TRT
        """
        # Check if we're already using this model (either .pt or .engine)
        base_name = model_name.replace('.pt', '').replace('.engine', '')
        current_base = self.current_model_name
        if base_name == current_base:
            print(f"[Model] Already using {base_name}, skipping switch")
            return

        # Check if a model load is already in progress
        if self._model_loading:
            print(f"[Model] WARNING: Model loading already in progress, ignoring switch to {model_name}")
            return

        # Check if TRT checkbox is enabled
        trt_enabled = self.model_manager.use_tensorrt

        if trt_enabled:
            # TRT is enabled - check if engine exists for new model
            from core.model_manager import is_tensorrt_available

            if is_tensorrt_available() and self.model_manager.engine_exists(base_name):
                # Engine exists, switch with TRT
                print(f"Queuing model switch to: {model_name} (TRT engine exists)...")
                self._pending_trt_switch = True
                self._pending_model_switch = model_name
                self._model_loading = True  # Block processing until model is reloaded
            elif is_tensorrt_available():
                # No engine - need to prompt user before building
                # Update model name tracking so TRT build knows which model
                print(f"No TRT engine for {base_name}, prompting to build...")
                self._pending_trt_build = base_name
                # Store that this is a model switch (not just TRT toggle on same model)
                self._pending_model_for_trt_build = base_name
            else:
                # TRT not available, switch with PT and disable checkbox
                print(f"Queuing model switch to: {model_name} (TRT not available)...")
                self._pending_trt_switch = False
                self._pending_model_switch = model_name
                self._model_loading = True  # Block processing until model is reloaded
        else:
            # TRT not enabled, just switch to PT model
            print(f"Queuing model switch to: {model_name}...")
            self._pending_trt_switch = False
            self._pending_model_switch = model_name
            self._model_loading = True  # Block processing until model is reloaded

    def _prompt_trt_build_sync(self, model_name: str) -> bool:
        """Show TRT build prompt and block until user responds.

        Used during startup / project switch before entering the main loop.

        Args:
            model_name: Base model name (e.g. "yolo11m-pose")

        Returns:
            True if user chose to build, False if declined.
        """
        if not self.ui.available:
            return False

        user_choice = {"build_trt": None}

        def on_choice(build_trt: bool):
            user_choice["build_trt"] = build_trt

        self.ui.show_tensorrt_prompt(model_name, on_choice)

        # Spin the GUI event loop until the user clicks a button
        while user_choice["build_trt"] is None:
            self.ui.render_frame()
            time.sleep(0.016)

        # Let modal close cleanly
        for _ in range(5):
            self.ui.render_frame()
            time.sleep(0.02)

        return user_choice["build_trt"]

    def _cb_trt_toggle(self, enabled: bool):
        """Handle TensorRT toggle checkbox.

        If enabling TRT:
        - Check if .engine exists
        - If not, ask to generate
        - If user says no or generation fails, revert checkbox to off

        If disabling TRT:
        - Switch to .pt model
        """
        base_name = self.current_model_name
        self._trt_requested = bool(enabled)

        if enabled:
            # User wants to enable TensorRT
            from core.model_manager import is_tensorrt_available

            if not is_tensorrt_available():
                print("TensorRT not available on this system")
                self._trt_requested = False
                self.ui.set_trt_checkbox(False)
                self.ui.show_toast("TensorRT not available", duration=3.0, color=(255, 100, 100))
                return

            if self.model_manager.engine_exists(base_name):
                # Engine exists, just switch to it
                print(f"Switching to TensorRT engine for {base_name}...")
                self._pending_trt_switch = True
                self._pending_model_switch = base_name
                self._model_loading = True  # Block processing until model is reloaded
            else:
                # Need to build engine - show prompt
                print(f"No TensorRT engine for {base_name}, prompting to build...")
                self._pending_trt_build = base_name
        else:
            # User wants to disable TensorRT, switch to .pt
            print(f"Switching to PyTorch for {base_name}...")
            self._pending_trt_switch = False
            self._pending_model_switch = base_name
            self._model_loading = True  # Block processing until model is reloaded

    def _cb_trt_rebuild(self):
        """Banner button: force a fresh TensorRT engine export for the current model."""
        from core.model_manager import is_tensorrt_available
        base_name = self.current_model_name
        if not is_tensorrt_available():
            if self.ui.available:
                self.ui.show_toast(
                    "TensorRT is not installed on this system", duration=4.0, color=(255, 100, 100))
            return
        engine_path = self.model_manager.get_engine_path(base_name)
        if os.path.exists(engine_path):
            try:
                os.remove(engine_path)
                print(f"[TRT Rebuild] Removed stale engine: {engine_path}")
            except OSError as e:
                print(f"[TRT Rebuild] Could not remove {engine_path}: {e}")
        self._trt_requested = True
        self.model_manager.use_tensorrt = True
        if self.ui.available:
            self.ui.update_trt_banner("Rebuilding TensorRT engine...", exporting=True)
        self._pending_trt_switch = True
        self._pending_model_switch = base_name
        self._model_loading = True

    def _update_trt_banner(self):
        """Show the red preview banner when TRT was requested but isn't running."""
        if not self.ui.available:
            return
        if not self._trt_requested or self.model_manager.is_using_tensorrt():
            self.ui.update_trt_banner(None)
            return
        reason = self.model_manager.get_tensorrt_fallback_reason() or "engine not loaded"
        self.ui.update_trt_banner(f"TensorRT OFF - running PyTorch ({reason})")

    # ------------------------------------------------------------------
    # Main-loop drains (deferred requests)
    # ------------------------------------------------------------------
    def _drain_pending_trt_build(self) -> bool:
        """Handle a pending TRT build request (user clicked TRT checkbox, engine
        doesn't exist). Returns True when a request was handled so the main
        loop restarts its iteration."""
        if self._pending_trt_build is None:
            return False
        model_to_build = self._pending_trt_build
        model_for_switch = self._pending_model_for_trt_build  # May be set if this came from model dropdown
        self._pending_trt_build = None
        self._pending_model_for_trt_build = None

        # Show prompt and wait for user choice
        user_choice = {"build_trt": None}

        def on_choice(build_trt: bool):
            user_choice["build_trt"] = build_trt

        self.ui.show_tensorrt_prompt(model_to_build, on_choice)

        # Wait for user to click a button
        while user_choice["build_trt"] is None:
            self.watchdog().beat()  # modal wait is not a hang
            self.ui.update_gpu_stats()
            self.ui.render_frame()
            time.sleep(0.016)

        # Clean up modal
        for _ in range(5):
            self.ui.render_frame()
            time.sleep(0.02)

        if user_choice["build_trt"]:
            # User wants to build, proceed with TRT loading
            print(f"User chose to build TensorRT engine for {model_to_build}")
            self._pending_trt_switch = True
            self._pending_model_switch = model_to_build
            self._model_loading = True  # Block processing until model is reloaded
        else:
            # User cancelled TRT build
            print(f"User cancelled TensorRT build for {model_to_build}")
            self.ui.set_trt_checkbox(False)

            # If this was a model switch (not just TRT toggle on same model),
            # still switch to the new model but with PyTorch
            if model_for_switch and model_for_switch != self.current_model_name:
                print(f"Switching to {model_for_switch} with PyTorch instead...")
                self._pending_trt_switch = False
                self._pending_model_switch = model_for_switch
                self._model_loading = True  # Block processing until model is reloaded
        return True

    def _drain_pending_model_switch(self) -> bool:
        """Handle a pending model switch (deferred from callback to avoid race
        condition). Returns True when a switch was handled so the main loop
        restarts its iteration."""
        if self._pending_model_switch is None:
            return False
        model_to_load = self._pending_model_switch
        trt_switch = self._pending_trt_switch
        self._pending_model_switch = None
        self._pending_trt_switch = None

        # Determine force_pt based on TRT switch state
        # If trt_switch is False, force PT. If True or None, let model manager decide.
        force_pt = (trt_switch == False)  # noqa: E712 - None must not force PT

        print(f"Switching to model: {model_to_load}... (TRT: {trt_switch}, force_pt: {force_pt})")
        if not self._load_model_with_progress(model_to_load, force_pt=force_pt):
            print(f"Failed to switch to model {model_to_load}")
            self._model_loaded = self.model is not None
            # Revert dropdown to current model
            if self.ui.available:
                self.ui.update_model_dropdown(self.current_model_name)
                # Also revert TRT checkbox if it was a TRT switch attempt
                if trt_switch:
                    self.ui.set_trt_checkbox(False)
        else:
            # Success - update TRT checkbox to match actual state
            if self.ui.available:
                is_trt = self.model_manager.is_using_tensorrt()
                self.ui.set_trt_checkbox(is_trt)
        self._update_trt_banner()
        return True

    # ------------------------------------------------------------------
    # Blocking load with GUI progress
    # ------------------------------------------------------------------
    def _load_model_with_progress(self, model_name: str, force_pt: bool = False) -> bool:
        """Model loads/TRT builds block the loop for minutes - suppress the
        loop watchdog for the duration."""
        self.watchdog().push_busy("model_load")
        try:
            return self._load_model_with_progress_impl(model_name, force_pt)
        finally:
            self.watchdog().pop_busy()

    def _load_model_with_progress_impl(self, model_name: str, force_pt: bool = False) -> bool:
        """
        Load model with GUI progress modal.
        Blocks until complete.

        Args:
            model_name: Model name (e.g., "yolo11m-pose" or "yolo11m-pose.pt")
            force_pt: If True, skip TensorRT and use .pt directly

        Returns:
            True if successful, False otherwise
        """
        if not self.ui.available:
            # No GUI, load directly (shouldn't happen in normal flow)
            print("Warning: Loading model without GUI")
            try:
                self.model = self.model_manager.load_model(model_name, force_pt=force_pt)
                self.processor().model = self.model
                self._model_loaded = True
                # Warmup inference to force TRT engine initialization
                import numpy as np
                dummy_frame = np.zeros((640, 640, 3), dtype=np.uint8)
                _ = self.model(dummy_frame, verbose=False)
                return True
            except Exception as e:
                print(f"Failed to load model: {e}")
                return False

        base_name = model_name.replace('.pt', '').replace('.engine', '')

        print(f"[Model] Starting model load: {model_name} (force_pt={force_pt})...")

        # Pause frame processing while loading model
        self._model_loading = True

        # Close camera during model loading to prevent buffer overflow/stale connection
        camera_was_open, camera_source = self.camera.snapshot()
        if camera_was_open:
            print("[Model] Pausing camera during model load...")
            self.camera.close()

        # Show progress modal
        print("[Model] Showing loading modal...")
        self.ui.show_model_loading_modal(f"Preparing {model_name}...")
        self.ui.render_frame()

        # Thread-safe containers
        import threading
        import queue
        load_result = {"model": None, "error": None, "done": False}
        progress_queue = queue.Queue()  # For thread-safe progress updates
        current_status = {"status": None, "message": "", "detail": ""}  # Track current status for animation

        def progress_callback(progress: ModelProgress):
            # Don't call GUI from background thread - put in queue instead
            status_messages = {
                ModelStatus.CHECKING: "Checking model files...",
                ModelStatus.DOWNLOADING: "Downloading model...",
                ModelStatus.EXPORTING_TENSORRT: "Building TensorRT engine (2-5 min)...",
                ModelStatus.LOADING: "Loading model into GPU...",
                ModelStatus.READY: "Model ready!",
                ModelStatus.ERROR: f"Error: {progress.error}",
            }
            message = status_messages.get(progress.status, progress.message)
            detail = progress.message if progress.status != ModelStatus.ERROR else ""
            # Include status so we know when to animate
            progress_queue.put((progress.status, message, progress.progress, detail))

        self.model_manager.set_progress_callback(progress_callback)

        def load_in_background():
            try:
                load_result["model"] = self.model_manager.load_model(model_name, force_pt=force_pt)
            except Exception as e:
                load_result["error"] = e
            load_result["done"] = True

        # Start loading in background thread
        load_thread = threading.Thread(target=load_in_background, daemon=True)
        load_thread.start()

        # Keep UI responsive while waiting for load to complete
        while not load_result["done"]:
            # Process any pending progress updates from the queue
            while not progress_queue.empty():
                try:
                    status, message, progress_val, detail = progress_queue.get_nowait()
                    current_status["status"] = status
                    current_status["message"] = message
                    current_status["detail"] = detail
                except queue.Empty:
                    break

            # Update UI - animate if exporting TensorRT, otherwise show real progress
            if current_status["status"] == ModelStatus.EXPORTING_TENSORRT:
                self.ui.update_model_loading_progress(
                    current_status["message"], 0.5, current_status["detail"], animate=True
                )
            elif current_status["message"]:
                self.ui.update_model_loading_progress(
                    current_status["message"], 0.5, current_status["detail"], animate=False
                )

            # Keep GPU stats updated during loading
            self.ui.update_gpu_stats()

            self.ui.render_frame()
            time.sleep(0.03)  # ~30 FPS for smoother animation

        # Process any remaining progress updates
        while not progress_queue.empty():
            try:
                status, message, progress_val, detail = progress_queue.get_nowait()
                self.ui.update_model_loading_progress(message, progress_val, detail)
            except queue.Empty:
                break

        # Check result
        if load_result["error"] is not None:
            e = load_result["error"]
            print(f"Failed to load model: {e}")
            self.ui.update_model_loading_progress(f"Error: {e}", 0.0, "Will retry with PyTorch model")
            self.ui.render_frame()
            time.sleep(2)

            # Try fallback to .pt
            if not force_pt:
                self.ui.update_model_loading_progress("Retrying with PyTorch model...", 0.5, "")
                self.ui.render_frame()

                # Run fallback in thread too
                fallback_result = {"model": None, "error": None, "done": False}

                def fallback_load():
                    try:
                        fallback_result["model"] = self.model_manager.load_model(model_name, force_pt=True)
                    except Exception as e2:
                        fallback_result["error"] = e2
                    fallback_result["done"] = True

                fallback_thread = threading.Thread(target=fallback_load, daemon=True)
                fallback_thread.start()

                while not fallback_result["done"]:
                    self.ui.update_gpu_stats()
                    self.ui.render_frame()
                    time.sleep(0.05)

                if fallback_result["model"] is not None:
                    self.model = fallback_result["model"]
                    self.processor().model = self.model
                    self.current_model = f"{model_name.replace('.pt', '').replace('.engine', '')}.pt"
                    self.current_model_name = model_name.replace('.pt', '').replace('.engine', '')
                    self._model_loaded = True
                    self.ui.update_engine_type_badge(False)
                    # Do warmup inference for fallback model too
                    print("[Model] Running warmup inference (fallback)...")
                    try:
                        import numpy as np
                        dummy_frame = np.zeros((640, 640, 3), dtype=np.uint8)
                        _ = self.model(dummy_frame, verbose=False)
                        print("[Model] Warmup complete")
                    except Exception as e:
                        print(f"[Model] Warmup failed (non-critical): {e}")
                    self.ui.show_toast("Using PyTorch (fallback)", duration=4.0, color=(255, 180, 80))
                    time.sleep(0.3)
                    self.ui.hide_model_loading_modal()
                    self._model_loading = False
                    # Reopen camera if it was open before
                    if camera_was_open:
                        print("[Model] Reopening camera after model load...")
                        self.camera.reopen_and_flush(camera_source)
                        self.restore_playback_dims()
                    return True
                else:
                    print(f"Fallback also failed: {fallback_result['error']}")

            self.ui.hide_model_loading_modal()
            self._model_loading = False
            # Reopen camera if it was open before
            if camera_was_open:
                print("[Model] Reopening camera after model load failure...")
                self.camera.reopen_and_flush(camera_source)
                self.restore_playback_dims()
            return False

        # Success path
        self.model = load_result["model"]
        self.processor().model = self.model

        # Update current model tracking
        base_name = model_name.replace('.pt', '').replace('.engine', '')
        if self.model_manager.use_tensorrt and self.model_manager.engine_exists(base_name):
            self.current_model = f"{base_name}.engine"
        else:
            self.current_model = f"{base_name}.pt"
        self.current_model_name = base_name

        self._model_loaded = True

        # Update engine type badge
        self.ui.update_engine_type_badge(self.model_manager.is_using_tensorrt())

        # Show toast if TensorRT was expected but fell back to PyTorch
        fallback_reason = self.model_manager.get_tensorrt_fallback_reason()
        if fallback_reason and self.model_manager.use_tensorrt:
            self.ui.show_toast(fallback_reason, duration=5.0, color=(255, 180, 80))

        # Do a warmup inference to force TRT engine to fully initialize
        # This prevents lazy loading during camera capture which causes buffer overflow
        print("[Model] Running warmup inference...")
        self.ui.update_model_loading_progress("Warming up model...", 0.98, "First inference may take a moment")
        self.ui.render_frame()
        try:
            import numpy as np
            dummy_frame = np.zeros((640, 640, 3), dtype=np.uint8)
            _ = self.model(dummy_frame, verbose=False)
            print("[Model] Warmup complete")
        except Exception as e:
            print(f"[Model] Warmup failed: {e}")
            # If TensorRT warmup fails (e.g. incompatible engine), fall back to PyTorch
            if self.model_manager.is_using_tensorrt() and not force_pt:
                print("[Model] TensorRT warmup failed — falling back to PyTorch model...")
                self.ui.update_model_loading_progress("TRT engine incompatible, loading PyTorch...", 0.5, str(e)[:80])
                self.ui.render_frame()
                try:
                    self.model = self.model_manager.load_model(model_name, force_pt=True)
                    self.processor().model = self.model
                    self.current_model = f"{base_name}.pt"
                    self.ui.update_engine_type_badge(False)
                    self.ui.show_toast("TRT engine incompatible — using PyTorch", duration=5.0, color=(255, 180, 80))
                    # Warmup the fallback model
                    _ = self.model(dummy_frame, verbose=False)
                    print("[Model] PyTorch fallback warmup complete")
                except Exception as e2:
                    print(f"[Model] PyTorch fallback also failed: {e2}")
            else:
                print(f"[Model] Warmup failed (non-critical): {e}")

        # Brief pause to show "ready" message
        time.sleep(0.3)
        self.ui.hide_model_loading_modal()
        self._model_loading = False
        # Reopen camera if it was open before
        if camera_was_open:
            print(f"[Model] Reopening camera {camera_source}...")
            self.camera.reopen_and_flush(camera_source)
            # If playback is active, restore video dimensions (camera reopen overwrites them)
            self.restore_playback_dims()
        print(f"Model loading complete: {self.current_model_name}")
        return True
