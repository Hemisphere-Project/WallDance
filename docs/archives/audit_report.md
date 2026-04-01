# WallDance Code Audit Report

## Baseline Elements Review

### 1. Cross-Platform Compatibility (Windows Primary, Linux Secondary)
**Status:** Good, but shell scripts need parity with batch scripts.
*   **Python Code:** The Python codebase is generally well-structured for cross-platform use. OS-specific calls (like Windows DPI awareness in `gui.py` and DirectShow fallback in `ids_camera.py`) are properly guarded with `sys.platform` checks.
*   **IDS Peak SDK:** `ids_camera.py` correctly includes common Linux installation paths for the IDS Peak SDK (`/opt/ids-peak*`, `/usr/lib/...`).
*   **Scripts:** 
    *   `run.bat` and `install.bat` are robust, featuring `uv` detection, Python version checks, and CUDA compatibility verification. `run.bat` also supports a `--cpu` flag.
    *   **Issue:** `run.sh` and `install.sh` are currently very basic. They lack the `--cpu` flag handling (`CUDA_VISIBLE_DEVICES=-1`), dependency checks, and CUDA verification present in the Windows scripts.

### 2. Camera Support (IDS Primary, Webcam Fallback)
**Status:** Excellent.
*   **Unified Interface:** `app.py` utilizes a `UnifiedCamera` class (defined in `ids_camera.py`) that seamlessly attempts to initialize an IDS camera first and falls back to a standard OpenCV `VideoCapture` if no IDS camera is found or if the SDK is missing.
*   **Resource Management:** `ids_camera.py` includes a crucial workaround: it explicitly releases the IDS Peak GenTL transport layer before attempting to open an OpenCV camera. This prevents native crashes caused by USB device locks.
*   **Windows Fallback:** If the default OpenCV backend fails on Windows, it automatically retries with `cv2.CAP_DSHOW`. 

### 3. GPU Optimization & CPU Fallback
**Status:** Excellent.
*   **Zero-Copy Pipeline:** `gpu_pipeline.py` implements a highly optimized zero-copy path using PyTorch and Kornia. Frames are uploaded to the GPU once, enhanced on the GPU, and passed directly to YOLO without returning to the CPU.
*   **Graceful Fallback:** `pipeline.py` checks for `GPU_PIPELINE_AVAILABLE` (which requires both CUDA and Kornia). If unavailable, it defaults to the CPU path.
*   **Runtime Error Handling:** `pipeline.py` includes `_disable_gpu_path_and_fallback`, which catches CUDA kernel compatibility errors at runtime and dynamically switches to CPU processing.
*   **Manual Override:** The `--cpu` flag in `run.bat` allows users to force CPU mode for testing or critical fallbacks.

---

## Proposed Fixes & Improvements

### High Priority
1.  **Upgrade Linux Scripts (`run.sh`, `install.sh`):**
    *   Update `run.sh` to parse the `--cpu` flag and set `export CUDA_VISIBLE_DEVICES=-1`.
    *   Update `install.sh` to include Python detection, `uv` installation/detection, and PyTorch/CUDA compatibility checks, mirroring `install.bat`.
    *   **Status:** Still open.

### Medium Priority
2.  **Linux Camera Fallback (`ids_camera.py`):**
    *   Currently, if the default OpenCV backend fails, Windows retries with `cv2.CAP_DSHOW`. On Linux, we should add a similar retry mechanism using `cv2.CAP_V4L2` (Video4Linux2) to improve fallback reliability.
    *   **Status:** Still open.

### Low Priority / Cleanup
3.  **GUI Camera Status:** Ensure the GUI clearly indicates whether the active camera is using the IDS Peak SDK or the OpenCV fallback, helping users debug hardware connection issues.
    *   **Status:** Addressed — GUI top bar shows [IDS]/[CV] badge.
4.  **Dependency Grouping:** In `pyproject.toml`, `ids-peak` and `ids-peak-ipl` are in the main dependencies but also in an `ids` dependency group. If IDS is optional, they should perhaps only be in the optional group, or the group can be removed if they are mandatory.
    *   **Status:** Addressed — IDS packages are now only in optional `ids` group.

---
*Audit performed on February 26, 2026. Status updated March 26, 2026.*