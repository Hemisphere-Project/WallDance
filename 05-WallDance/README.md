# WallDance

Multi-person pose detection and tracking for wall dancers in low-light outdoor settings.

## Quick Start

```bash
chmod +x install.sh run.sh
./install.sh         # installs dependencies via uv
./run.sh             # launches GUI + camera processing
```

Requirements: Python 3.10+, `uv` installed (`pip install uv` if missing), a webcam or capture card, and optional CUDA GPU for best performance. Model weights live in `models/` (already included in the repo).

## GUI at a Glance

- **Top bar**: pick project, pick saved config version, save current settings, GPU/VRAM readout.
- **Video panel**: live preview with INPUT / PREVIEW / ENHANCE / MODEL tables, plus FPS/dancer counts and timing breakdowns.
- **Control panel**: detection caps, visualization toggles, tracker tuning, OSC target, quit button.

Keyboard shortcuts: `Q` quit, `E` enhance, `S` skeleton, `K` keypoints, `B` bbox, `T` trails, `I` IDs, `R` reset tracker, `+/-` adjust preview scale.

## Projects and Configs

- Configs are stored under `configs/<project>/<project>_YYYYMMDD_HHMMSS.json`.
- The GUI top bar lets you create/select projects, pick a saved version, and save the current settings; the latest project is remembered in `configs/last_project.txt`.
- Settings cover camera source, preview scale, enhancement, YOLO model/imgsz/conf, tracker params, OSC target, and visualization flags.

## Runtime Flow

1. Camera capture (via OpenCV, see `camera_manager.py`).
2. Optional enhancement (CLAHE + gamma) in `enhancer.py`.
3. Pose detection with Ultralytics YOLO in `pipeline.FrameProcessor`.
4. Duplicate filtering + Kalman/Hungarian tracking in `tracker.py`.
5. Rendering/overlay in `visualization.py` and OSC output in `osc_output.py`.
6. DearPyGui front-end in `gui.py` + layout helpers in `gui_builder.py`.

## File Map (src/)

- `main.py` — thin entrypoint delegating to `app.main`.
- `app.py` — orchestrator wiring camera, pipeline, GUI, OSC, and configs.
- `pipeline.py` — frame processing (enhance → YOLO → dedupe → track → OSC).
- `camera_manager.py` — camera discovery/open/close with state.
- `config_store.py` — save/load configs per project and remember last project.
- `gui.py` — GUI logic/callbacks; `gui_builder.py`/`gui_icons.py` hold layout/theme.
- `enhancer.py`, `tracker.py`, `osc_output.py`, `visualization.py`, `config.py` — processing utilities and defaults.

Support files: `install.sh` (uv sync), `run.sh` (launch), `configs/` (saved presets), `models/` (YOLO weights).

## Performance Tips

- Switch to `yolo11n-pose` or `yolo11s-pose` for speed; `yolo11m` is a balanced default.
- Enable FP16 when running on CUDA for ~20-30% speedup.
- Increase **Frame Skip** (1–2) if the scene is stable; tracker interpolates between detections.
- Lower **preview scale** if the UI lags; it only affects display, not detection.

## OSC Messages

All coordinates are normalized (0–1) to the input frame.

| Address | Arguments | Description |
|---------|-----------|-------------|
| `/walldance/count` | `[n]` | Number of tracked dancers |
| `/walldance/dancer/<id>/centroid` | `[x, y]` | Dancer center |
| `/walldance/dancer/<id>/bbox` | `[x, y, w, h]` | Bounding box |
| `/walldance/dancer/<id>/velocity` | `[vx, vy]` | Velocity |
| `/walldance/dancer/<id>/keypoints` | `[x0,y0,c0, ...]` | 17 keypoints (51 floats) |
| `/walldance/clear` | `[1]` | Tracker reset event |

## Troubleshooting

- No detections: verify camera with `ffplay /dev/video0`, lower confidence, or upscale more.
- Flicker/ID swaps: raise tracker distance/age, lower confidence slightly, ensure lighting is stable.
- Slow FPS: use faster model, enable FP16, increase frame skip, or reduce preview scale.
- OSC not received: check IP/port, ensure firewall allows UDP, verify the `Enable OSC` toggle.
