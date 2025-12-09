# WallDance Phase 5: Simplified Tiling Plan

## 1. Problem Statement
- **Context**: Variable input resolutions (**1080p, 2K, 4K**) with small, low-light dancers.
- **Challenge**:
    - **Low Light Physics**: Downscaling reduces noise but destroys faint details.
    - **Performance**: Processing full 4K at 1:1 is impossible in real-time.
    - **Goal**: Achieve **>20 FPS** using TensorRT while maintaining good pixel density.

## 2. Unified Workflow

### User Control
*   **`imgsz`**: User selects from list (640, 800, 960, 1280, 1440, 1920).
*   **`tiling`**: Toggle ON/OFF.

### A. Tiling OFF (Standard Mode)
1.  Resize input to fit `imgsz × imgsz` (letterbox, preserve aspect).
2.  Single inference.
3.  Unscale results.

### B. Tiling ON (2x1 Grid Mode)
1.  **Rescale**: Resize input so **shorter dimension = `imgsz`** (preserve aspect).
    *   Example: 1080p + 1280 → 2276×1280.
    *   Example: 4K + 1440 → 2560×1440.
2.  **Portrait Fix**: If input is portrait (height > width), rotate 90° to landscape.
3.  **Ultrawide Check**: If `width > 2 × height`, use **3x1 Grid** instead of 2x1.
4.  **Square Check**: If input is nearly square (`|w - h| < 0.1 × w`), skip tiling (single tile).
5.  **Tile**: Cut 2 (or 3) square tiles of size `imgsz × imgsz`.
    *   Tile 1: Left-aligned.
    *   Tile 2: Right-aligned.
    *   (Tile 3 for ultrawide: Center).
6.  **Batch Inference**: Stack tiles `(B, 3, imgsz, imgsz)`.
7.  **Merge**: Transform tile-local coordinates → global coordinates. Apply NMS.

## 3. Overlap Guarantees

| Input | `imgsz` | Rescaled | 2× Tile | Overlap |
|-------|---------|----------|---------|---------|
| 1080p | 1280 | 2276×1280 | 2560 | 284px (12%) |
| 2K | 1440 | 2560×1440 | 2880 | 320px (12%) |
| 4K | 1440 | 2560×1440 | 2880 | 320px (12%) |
| 4K | 1920 | 3413×1920 | 3840 | 427px (12%) |

**Result**: ~12% overlap is guaranteed by the math. Safe for most body sizes.

## 4. Performance Estimates (RTX 3090 + TensorRT FP16, 2x1 Tiling)

| `imgsz` | Single Tile | 2 Tiles | Est. FPS |
|---------|-------------|---------|----------|
| 960 | ~10ms | ~20ms | ~50 FPS |
| 1280 | ~18ms | ~36ms | ~28 FPS |
| 1440 | ~25ms | ~50ms | ~20 FPS |
| 1920 | ~45ms | ~90ms | ~11 FPS |

### Recommended `imgsz` by Resolution & Target FPS

#### 1080p Input (1920×1080)
| Target FPS | `imgsz` | Zoom | Notes |
|------------|---------|------|-------|
| ~28 FPS | **1280** | **1.19×** | Best balance |

#### 2K Input (2560×1440)
| Target FPS | `imgsz` | Zoom | Notes |
|------------|---------|------|-------|
| ~28 FPS | **1280** | **0.89×** | Slight loss |
| ~20 FPS | **1440** | **1.00×** | Native, recommended |

#### 4K Input (3840×2160)
| Target FPS | `imgsz` | Zoom | Notes |
|------------|---------|------|-------|
| ~20 FPS | **1440** | **0.67×** | Important loss (down to 2K) |
| ~11 FPS | **1920** | **0.89×** | Slight loss but slow |

**Legend**: Zoom = `imgsz / original_height`. 
- **1.0×** = Native (1:1 pixel mapping).
- **>1.0×** = Upscaling (more detail than source).
- **<1.0×** = Downscaling (loss of detail).

## 5. Operator Controls (GUI)

### A. `imgsz` (Combo)
*   Options: 640, 800, 960, 1280, 1440, 1920.
*   **Default**: 1280 (Good balance).

### B. Tiling (Toggle)
*   **OFF**: Standard letterbox resize.
*   **ON**: 2x1 (or 3x1) grid with overlap.

## 6. Implementation Notes

### TilingManager Responsibilities
1.  `configure(input_shape, imgsz, enabled)` → Calculate grid.
2.  `get_tiles()` → Return list of crop regions `(x1, y1, x2, y2)`.
3.  `transform_to_global(tile_idx, boxes, keypoints)` → Map back to original coords.

### GpuPipeline Changes
1.  After enhancement, call `TilingManager.get_tiles()`.
2.  GPU-crop tiles (zero-copy slicing).
3.  Stack into batch tensor.
4.  Run batch inference.
5.  Loop results, call `transform_to_global()` for each tile.
6.  Apply global NMS.
