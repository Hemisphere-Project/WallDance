# WallDance

Multi-person pose detection and tracking for wall dancers in low-light outdoor settings.

## Quick Start

### Linux

```bash
chmod +x install.sh run.sh
./install.sh         # installs dependencies via uv and verifies torch + cv2
./run.sh             # launches GUI + camera processing
```

### Windows 11

```bat
install.bat          REM installs dependencies via uv and verifies torch + cv2
run.bat              REM launches GUI + camera processing
```

If `run.sh` or `run.bat` reports that `cv2` or `torch` is missing, the install did not complete successfully. Re-run the installer and fix the reported dependency error before launching the app.

### Windows GPU compatibility note (PyTorch/CUDA)

On some recent NVIDIA GPUs (for example RTX 50-series), the pinned PyTorch/CUDA build may not support your GPU architecture yet. If that happens, WallDance will show **CPU fallback** in the top bar and FPS will be much lower.

`install.bat` checks this and now attempts an automatic fix when detected.

Important: this is usually a **PyTorch wheel compatibility** issue, not a missing standalone CUDA Toolkit issue.

- Installing NVIDIA CUDA Toolkit by itself (for example CUDA 13.1.1 from NVIDIA downloads) is **not usually enough** to fix `sm_120` mismatch.
- The proper fix is to install a PyTorch build that explicitly supports your GPU architecture.

Simple fix steps:

1. Update NVIDIA driver (latest Studio/Game Ready).
2. Run `install.bat` again. It installs `torch` and `torchvision` from the selected PyTorch wheel index and will auto-try fallback CUDA indexes in this order if needed: `cu130`, `cu129`, `cu128`, `cu126`, `cu124`.
3. If auto-fix still fails, reinstall PyTorch using the selector command from https://pytorch.org/get-started/locally/ (latest stable/nightly CUDA option).
4. Re-run:

```bat
install.bat
run.bat
```

Optional example (check the PyTorch site first for the latest recommended command):

```bat
cd application
uv pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cu130
```

If stable wheels still do not include your architecture, use a newer/nightly PyTorch CUDA wheel from the PyTorch selector page and then re-run `install.bat`.

To skip the automatic install attempts (for offline/manual control), run:

```bat
set WALLDANCE_SKIP_TORCH_AUTOFIX=1
install.bat
```

If you intentionally run on CPU, use:

```bat
run.bat --cpu
```

Requirements: Python 3.10+, `uv` installed (`pip install uv` if missing), a webcam or capture card, and optional CUDA GPU for best performance. Model weights live in `models/` (some are included in the repo; others may be downloaded by Ultralytics depending on configuration).

## Extra Scripts

### Build TensorRT engines

- Linux: `./extra/build_engines.sh`
- Windows: `extra\build_engines.bat`

Builds TensorRT engines for all `.pt` models in `models/` across preset image sizes.

### GPU power limiter (NVIDIA)

- Linux (root): `sudo ./extra/gpu_limiter.sh 280`
- Windows (Administrator terminal): `extra\gpu_limiter.bat 280`

Applies a temporary GPU power limit in watts (resets after reboot).

## License

WallDance is free software licensed under the **GNU General Public License v3.0 (GPLv3)**. See [LICENSE](LICENSE).

## YOLO models, downloads, and third-party terms

WallDance uses **Ultralytics YOLO** for pose estimation and relies on pretrained YOLO model files (e.g. `*.pt`, `*.onnx`) and optionally TensorRT engines (`*.engine`).

- Some model files may be present in this repository (for convenience), and the Ultralytics stack may also **download model weights automatically** if a requested model file is missing.
- **Model weights/engines and the Ultralytics package are third-party components** and are **not** covered by WallDance's GPLv3 license.
- Your use of YOLO software and/or model weights may be subject to separate **licenses, terms, and conditions** (including restrictions that may vary by model and by commercial vs non-commercial use).

Please review and comply with the applicable terms for:

- Ultralytics YOLO (code/package): https://github.com/ultralytics/ultralytics
- Ultralytics documentation / licensing pages: https://docs.ultralytics.com/

This note is informational and not legal advice.

**Production Hardware:** IDS U3-34E0XCP-M-GL camera (4MP Starvis 2 mono), Tamron 8mm lens, MidOpt BP850 IR filter, ASUS ROG Strix SCAR 16 (RTX 5080). See [docs/archives/HARDWARE_GUIDE.md](docs/archives/HARDWARE_GUIDE.md) for details.

## Setup & Calibration Workflow

WallDance is built around a **rig → calibrate → monitor** flow so a new venue does not require hand-tuning knobs. See [docs/UX_PLAN.md](docs/UX_PLAN.md) for the full design.

1. **Rig the camera + IR.** Aim and focus from the stage using the **phone monitor**: tap the QR button in the top bar to open a browser view (`web_monitor.py`) with a live focus score (variance-of-Laplacian) and a lighting readout (brightness, clipping, uniformity with the darkest tile marked, so you know where to add IR).
2. **CALIBRATE (scene).** On the empty stage, press **CALIBRATE**. On a live IDS camera it servos exposure (up to a motion-blur budget, never below ~15 fps) then gain to hit a brightness target, seeds gamma/CLAHE, sweeps MOG2 `varThreshold`×`scale` for the lowest background false-positive rate, and builds the auto **exclusion mask**. A report card scores focus, uniformity, and clipping. Re-press after each focus/IR adjustment — it is idempotent.
3. **DANCERS (subject).** With 1–4 dancers moving, press **DANCERS** to collect an evidence run (live or during playback). Runs accumulate into a per-project pool; **Apply** sets person height + ratios, auto-selects `imgsz` from the dancer's on-screen size, and seeds the sensitivity dial. Add runs across costumes/positions/recordings for a more robust pool.
4. **Go live & monitor.** Switch to **RUN**. The only live knob you need is **Detection Sensitivity** (50 = calibrated; raise it if you are losing dancers, lower it if you see ghosts).

**Lighting profiles.** Each project holds two calibrated profiles — **Show** and **Rehearsal** — switched from the top bar. Calibrate each once (exposure/gain, gamma/CLAHE, MOG2, exclusion mask, sensitivity are stored per profile); switching applies the whole bundle, including camera hardware.

## GUI at a Glance

- **Top bar**: project + saved-version pickers, **Show/Rehearsal** profile switch, save, phone-monitor QR, and status badges (state, camera, OSC, model, **TRT/PT engine**, FPS, GPU/VRAM).
- **Video panel**: live preview with skeleton/keypoint/bbox/trail/ID overlays; a red banner appears if TensorRT was requested but fell back to PyTorch (with a **Rebuild** button).
- **Control panel (simple)**: Input (camera), ROI, Enhancement (Enable + Greyscale), Model, Detection (**Person Height** + **Sensitivity**), Preview, OSC, and the S/K/B/T/I view toolbar.
- **Bottom bar**: source/playback controls, **CALIBRATE** + **DANCERS**, **STANDBY/RUN**, performance stats.
- **Expert mode** (`Ctrl+Shift+E`, or set `WD_EXPERT=1`): reveals the developer knobs hidden from the operator surface — the Background section, raw enhancement (Force + brightness gate), and raw detection knobs (confidence, tracker max age, MOG2 scale, motion sensitivity).

Keyboard shortcuts: `Q` quit, `E` enhance, `S` skeleton, `K` keypoints, `B` bbox, `T` trails, `I` IDs, `P` preview, `R` reset tracker, `Ctrl+Shift+E` expert mode, `+/-` adjust preview scale. **Double-click the preview** to toggle ROI edit mode, then drag the corners.

## Projects and Configs

- On launch, a **project picker** opens (no silent auto-load): projects are listed by last-save date with the most recent highlighted (Enter launches it), plus **New**, **Rename**, and **Delete**. The last project is remembered in `projects/last_project.txt`.
- Configs are stored under `projects/<project>/<project>_YYYYMMDD_HHMMSS.json` (timestamped versions; the top bar picks a version). Dancer-calibration evidence lives under `projects/<project>/calib2/`.
- `projects/<project>/_safe_defaults.json` is a separate per-project fallback written only by the explicit **Save safe defaults** action (and loaded only by **Load safe defaults**) — it is not a timestamped version, never appears in the version list, and is not what startup or the picker loads.
- Configs use **schema v2**: a set of shared keys (camera, ROI, model/imgsz, OSC, person height) plus two **lighting-profile** bundles (`show` / `rehearsal`) for the lighting-coupled values (exposure/gain, gamma/CLAHE, MOG2, exclusion mask, sensitivity). Older flat configs migrate automatically (wrapped as the `show` profile) and are range-validated on load.

## Runtime Flow

1. Camera capture (IDS USB3 zero-copy or OpenCV fallback, see `ids_camera.py`, `camera_manager.py`).
2. GPU upload (zero-copy for IDS, single upload for OpenCV).
3. Optional enhancement (Kornia CLAHE + gamma on GPU) in `gpu_pipeline.py` / `enhancer.py`.
4. Pose detection with Ultralytics YOLO (TensorRT or PyTorch) in `pipeline.FrameProcessor`.
5. One `MotionModel` (single MOG2 silhouette + frame-diff) feeds source-weighted measurements into cascaded Kalman/Hungarian tracking in `tracker.py`; frame-diff is the primary ghost rejector and gap bridge.
6. Rendering/overlay in `visualization.py` and OSC output in `osc_output.py`.
7. DearPyGui front-end in `gui.py` + layout helpers in `gui_builder.py`.

## File Map (src/)

- `main.py` — thin entrypoint delegating to `app.main`.
- `app.py` — orchestrator wiring camera, pipeline, GUI, OSC, and configs.
- `pipeline.py` — frame processing (enhance → YOLO → dedupe → track → OSC).
- `gpu_pipeline.py` — zero-copy GPU pipeline (Kornia enhancement, resize, tensor pass-through).
- `camera_manager.py` — camera discovery/open/close with state (OpenCV).
- `ids_camera.py` — IDS Peak SDK camera + `UnifiedCamera` (IDS/OpenCV transparent switching).
- `config_store.py` — save/load configs per project, project rename/delete, remember last project.
- `config_schema.py` — schema-v2 lighting profiles: migrate / flatten / structure / validate.
- `gui.py` — GUI logic/callbacks; `gui_builder.py`/`gui_icons.py` hold layout/theme.
- `calibration.py` — CALIBRATE (scene) pass: exposure/gain servo, gamma/CLAHE seed, joint MOG2 var×scale sweep, exclusion mask, report card.
- `calib2.py` — DANCERS (subject) pass: accumulative evidence pool, pooled person height + `imgsz` auto-select.
- `sensitivity_macro.py` — maps the one operator sensitivity dial to confidence (+ varThreshold at the loose end).
- `enhancer.py` — low-light enhancement (CPU fallback).
- `tracker.py` — Kalman + Hungarian tracking with cascaded matching, swap correction, dormant pool.
- `tracking_logger.py` — structured JSONL event logger for tracking diagnostics.
- `motion_model.py` — unified MOG2 silhouette + frame-diff motion model (ghost rejection + gap bridging).
- `motion_detector.py` — MOG2 foreground blob detector used by `motion_model`.
- `web_monitor.py` — phone monitor (MJPEG + focus score + lighting readout) for rigging.
- `background.py` — static background subtraction (dormant; expert-only).
- `model_manager.py` — YOLO model loading/switching/TensorRT management.
- `osc_output.py`, `visualization.py`, `video_recorder.py`, `config.py` — processing utilities and defaults.

Support files: `install.sh`/`install.bat` (uv sync), `run.sh`/`run.bat` (launch), `projects/` (saved presets + recordings), `models/` (YOLO weights + TensorRT engines).

## Performance Tips

- **Enable TensorRT** via the TRT checkbox for ~2× inference speedup (first build takes 2-5 minutes).
- Switch to `yolo11n-pose` or `yolo11s-pose` for speed; `yolo11l`/`yolo11m` are accuracy-first (higher models don't hurt quality, only throughput).
- Enable FP16 when running on CUDA for ~20-30% speedup (applies to PyTorch mode).
- Lower **imgsz** for faster inference at the cost of small-figure detection — but prefer letting **DANCERS** calibration pick it from the on-screen dancer size.
- Lower **preview scale** if the UI lags; it only affects display, not detection.

## TensorRT Acceleration

TensorRT provides significant inference speedup (~2×) by optimizing the model for your specific GPU.

### Enabling TensorRT
1. In the **MODEL** section, check the **TRT** checkbox
2. If no engine exists for the current model + imgsz, you'll be prompted to build one
3. Building takes 2-5 minutes (GPU stats update during build)
4. Once built, the engine is saved and reused automatically

### Engine Files
- Engines are named `{model}_{imgsz}.engine` (e.g., `yolo11m-pose_960.engine`)
- Different imgsz settings require different engines
- Engines are GPU-specific and must be rebuilt on different hardware
- Engine preference is saved with your config

### Fallback Behavior
- If TensorRT is unavailable, the checkbox will be disabled
- If an engine fails to load, the app falls back to PyTorch
- On startup, if saved config had TRT but engine is missing, PyTorch is used

## OSC Messages

All coordinates are normalized (0–1) to the input frame.

| Address | Arguments | Description |
|---------|-----------|-------------|
| `/walldance/count` | `[n, id0, id1, ...]` | Count + active track IDs |
| `/walldance/dancer/centroid` | `[id, x, y]` | Dancer center |
| `/walldance/dancer/bbox` | `[id, x, y, w, h]` | Bounding box |
| `/walldance/dancer/velocity` | `[id, vx, vy]` | Velocity |
| `/walldance/dancer/keypoints` | `[id, x0,y0,c0, ...]` | 17 keypoints (52 values) |
| `/walldance/clear` | `[1]` | Tracker reset event |

## Troubleshooting

- Losing dancers (drops): raise **Detection Sensitivity**, or re-run **CALIBRATE** + **DANCERS** for the venue; add more IR for even coverage.
- Too many ghosts: lower **Detection Sensitivity**; run **CALIBRATE** on the empty stage so the exclusion mask + MOG2 threshold are set for the scene.
- No detections at all: verify the camera feed and that you are in **RUN**; check the model loaded.
- Slow FPS: use a faster model, enable TensorRT, enable FP16, lower imgsz, or reduce preview scale.
- TRT build fails: ensure CUDA drivers are up to date, check GPU memory, try a smaller model first.
- TRT banner over the preview (red): TensorRT was requested but fell back to PyTorch — click **Rebuild**, or install with `pip install tensorrt`.
- OSC not received: check IP/port, ensure firewall allows UDP, verify the `Enable OSC` toggle.
- USB3 stalls (IDS camera): hardware-level PCIe contention; preview FPS cap is auto-enabled. See [docs/archives/IDS_STALL_CONCLUSIONS.md](docs/archives/IDS_STALL_CONCLUSIONS.md).
- CPU fallback: PyTorch/CUDA mismatch with your GPU; re-run `install.bat` for auto-fix.
