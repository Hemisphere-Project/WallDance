"""
GPU-accelerated frame processing pipeline.

Implements zero-copy GPU path for maximum performance:

Pipeline Flow:
```
Camera (CPU numpy BGR)
    │
    ▼ [torch.from_numpy + .cuda() + BGR→RGB]
┌─────────────────────────────────────────────┐
│                    GPU                       │
│                                              │
│  gpu_frame (torch.Tensor RGB, BCHW)          │
│       │                                      │
│       ▼                                      │
│  Enhancement (kornia Y-channel CLAHE+Gamma)  │
│       │         or bypass if bright          │
│       ▼                                      │
│  enhanced_gpu (torch.Tensor RGB)             │
│       │                                      │
│       ├──────────────────┐                   │
│       ▼                  ▼                   │
│   GPU Resize         GPU Resize              │
│   (to yolo_imgsz)    (to preview_scale)      │
│       │                  │                   │
│       ▼                  ▼ [rate-limited]    │
│   YOLO Input         Preview Download        │
│   (zero-copy!)       (GPU→CPU only when      │
│                       needed for display)    │
└───────┼──────────────────┼───────────────────┘
        ▼                  ▼
    Detections         Preview (DearPyGui)
```

Performance (1920x1080 → YOLO 960x960):
- CPU Pipeline: ~44ms (enhancement + YOLO with numpy input)
- GPU Pipeline: ~32ms with preview, ~28ms without (27-36% faster)

Enhancement modes:
- Normal: Auto-bypass if brightness > threshold, CLAHE + Gamma on Y
- Lite: Auto-bypass, Gamma only (direct on RGB, ~0.1ms)
- Force: Ignore brightness threshold, CLAHE + Gamma on Y
- Force + Lite: Ignore threshold, Gamma only

Preview rate capping:
- When preview_fps_cap is set, GPU→CPU download only happens at that rate
- YOLO still runs at full camera rate (saves ~5ms per skipped preview)
"""

import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import cv2
import numpy as np
import torch
import torch.nn.functional as F

# Check CUDA availability
CUDA_AVAILABLE = torch.cuda.is_available()
if CUDA_AVAILABLE:
    DEVICE = torch.device('cuda')
else:
    DEVICE = torch.device('cpu')

# kornia transitively imports `kornia_rs` (a Rust image-I/O extension) via
# kornia.io.  Its prebuilt wheel is compiled with AVX2 and crashes the whole
# process with SIGILL ("Illegal instruction") on CPUs without AVX2 — e.g. the
# Ivy-Bridge i7-3770K dev box.  That native crash cannot be caught by the
# try/except below.  WallDance only uses kornia.enhance (CLAHE) and
# kornia.color (both pure-torch, GPU), never kornia's file I/O, so we stub
# kornia_rs in sys.modules *before* importing kornia.  A bare module is used
# (not one with a raising __getattr__) so torch's import-time introspection,
# which probes `hasattr(mod, '__file__')` across sys.modules, still works.
# See docs/ROBUSTNESS_PLAN.md (env / install findings).
import sys as _sys
import types as _types
if "kornia_rs" not in _sys.modules:
    _sys.modules["kornia_rs"] = _types.ModuleType("kornia_rs")

# Import kornia for GPU CLAHE
try:
    from kornia.enhance import equalize_clahe
    from kornia.color import rgb_to_ycbcr, ycbcr_to_rgb
    KORNIA_AVAILABLE = True
except ImportError:
    KORNIA_AVAILABLE = False
    equalize_clahe = None
    rgb_to_ycbcr = None
    ycbcr_to_rgb = None


@dataclass
class GpuPipelineSettings:
    """Settings for GPU pipeline processing."""
    # Enhancement
    enhance_enabled: bool = True
    enhance_lite: bool = False      # Gamma only, skip CLAHE
    enhance_force: bool = False     # Ignore brightness threshold
    brightness_threshold: float = 60.0  # Auto-bypass threshold (0-255)
    clahe_clip: float = 2.0
    clahe_grid: int = 8
    gamma: float = 1.2
    greyscale: bool = False         # Convert to greyscale (mono camera simulation)
    
    # Denoising
    denoise_enabled: bool = False
    denoise_alpha: float = 0.6      # Weight of new frame (0.0-1.0). Lower = more smoothing.
    
    # Preview - exact dimensions for GPU resize
    preview_width: int = 960        # Target preview width
    preview_height: int = 540       # Target preview height
    preview_fps_cap: Optional[float] = None  # None = no cap, e.g. 10.0 = 10fps
    
    # Background subtraction
    bg_subtract_enabled: bool = False
    bg_subtract_sensitivity: int = 30  # 0-255

    # ROI crop (full-frame coordinates)
    roi_enabled: bool = False
    roi_x: int = 0
    roi_y: int = 0
    roi_w: int = 0
    roi_h: int = 0
    
    # Processing
    yolo_imgsz: int = 960


class GpuFrame:
    """
    GPU-resident frame with lazy CPU conversion.
    
    Holds the frame as a GPU tensor and only converts to CPU/numpy
    when explicitly requested (for preview output).
    """
    
    def __init__(self, tensor: torch.Tensor, is_bgr: bool = False):
        """
        Args:
            tensor: GPU tensor in BCHW format, float32 [0,1] range
            is_bgr: Whether tensor is BGR (False = RGB)
        """
        self.tensor = tensor  # (1, 3, H, W) on GPU
        self._is_bgr = is_bgr
        self._cpu_cache: Optional[np.ndarray] = None
        self._last_download_timing: Dict[str, float] = {}
    
    @property
    def shape(self) -> Tuple[int, int]:
        """Return (H, W) of the frame."""
        return self.tensor.shape[2], self.tensor.shape[3]
    
    def to_numpy_bgr(self) -> np.ndarray:
        """Convert to BGR numpy array (HWC, uint8). Caches result."""
        if self._cpu_cache is not None:
            self._last_download_timing = {
                "preview_download_total": 0.0,
                "preview_download_sync": 0.0,
                "preview_download_numpy": 0.0,
            }
            return self._cpu_cache
        
        # BCHW float [0,1] -> HWC uint8 BGR
        t = self.tensor.squeeze(0)  # (3, H, W)
        if not self._is_bgr:
            # RGB -> BGR
            t = t.flip(0)
        
        # GPU -> CPU sync + numpy conversion (instrumented)
        # Convert float32→uint8 ON GPU before transfer: 4× less PCIe bandwidth
        # (e.g. 960×540×3: 5.93 MB float32 → 1.48 MB uint8)
        t0 = time.perf_counter()
        hwc = t.permute(1, 2, 0).contiguous()
        hwc_u8 = hwc.mul(255).clamp_(0, 255).byte()  # float32→uint8 on GPU
        t1 = time.perf_counter()
        cpu_tensor = hwc_u8.cpu()                     # 1.48 MB instead of 5.93 MB
        t2 = time.perf_counter()
        arr = cpu_tensor.numpy()
        t3 = time.perf_counter()

        self._last_download_timing = {
            "preview_download_total": (t3 - t0) * 1000.0,
            "preview_download_sync": (t2 - t1) * 1000.0,
            "preview_download_numpy": (t3 - t2) * 1000.0,
        }
        self._cpu_cache = arr
        return arr

    @property
    def last_download_timing(self) -> Dict[str, float]:
        """Last GPU->CPU conversion timing breakdown in milliseconds."""
        return self._last_download_timing
    
    def invalidate_cache(self):
        """Call after modifying tensor to clear CPU cache."""
        self._cpu_cache = None


class GpuEnhancer:
    """
    GPU-accelerated image enhancement using kornia.
    
    Operates on GPU tensors directly - no CPU copies for enhancement.
    Uses Y-channel CLAHE for better quality and performance.
    """
    
    def __init__(self):
        self._gpu_available = CUDA_AVAILABLE and KORNIA_AVAILABLE
        self._device = DEVICE
        
        # Last computed brightness (for status display)
        self.last_brightness: float = 0.0
        self.last_used_gpu: bool = False
        
        # Brightness check decimation — .mean().item() forces a CUDA sync
        # (PCIe round-trip) on every call.  Brightness changes slowly, so we
        # only recompute every _BRIGHTNESS_CHECK_INTERVAL frames.
        self._brightness_frame_counter: int = 0
        _BRIGHTNESS_CHECK_INTERVAL = 10  # recompute every 10th frame
        self._brightness_interval: int = _BRIGHTNESS_CHECK_INTERVAL
        
        # Temporal denoising state
        self._last_frame_tensor: Optional[torch.Tensor] = None
    
    @property
    def gpu_available(self) -> bool:
        return self._gpu_available
    
    def enhance(
        self,
        gpu_frame: GpuFrame,
        settings: GpuPipelineSettings
    ) -> Tuple[GpuFrame, bool]:
        """
        Apply enhancement to GPU frame.
        
        Args:
            gpu_frame: Input frame on GPU (RGB format for kornia)
            settings: Enhancement settings
            
        Returns:
            (enhanced_frame, was_enhanced) - frame stays on GPU
        """
        if not settings.enhance_enabled:
            self.last_used_gpu = False
            self.last_brightness = 0.0
            # Still apply greyscale if enabled (independent of enhancement)
            if settings.greyscale:
                grey_frame = self._apply_greyscale_gpu(gpu_frame.tensor)
                return GpuFrame(grey_frame, is_bgr=gpu_frame._is_bgr), True
            return gpu_frame, False
        
        tensor = gpu_frame.tensor  # (1, 3, H, W) in RGB
        
        # 1. Temporal Denoising (Raw RGB)
        # Apply before brightness check/enhancement to stabilize the signal
        if settings.denoise_enabled:
            tensor = self._apply_temporal_denoise(tensor, settings.denoise_alpha)
            # Update gpu_frame wrapper with denoised tensor
            gpu_frame = GpuFrame(tensor, is_bgr=gpu_frame._is_bgr)
        
        # 2. Greyscale conversion (mono camera simulation)
        # Apply before enhancement so CLAHE/Gamma work on greyscale signal
        if settings.greyscale:
            tensor = self._apply_greyscale_gpu(tensor)
            gpu_frame = GpuFrame(tensor, is_bgr=gpu_frame._is_bgr)
        
        # Compute brightness from Y channel for auto-bypass check
        # Only needed if not in force mode.
        # Decimated: .mean().item() forces a CUDA sync (PCIe round-trip)
        # which competes with USB3 DMA.  Recompute only every N frames.
        blend_factor = 1.0
        if not settings.enhance_force:
            self._brightness_frame_counter += 1
            if self._brightness_frame_counter >= self._brightness_interval:
                self._brightness_frame_counter = 0
                brightness = self._compute_brightness_gpu(tensor)
                self.last_brightness = brightness
            else:
                brightness = self.last_brightness  # reuse cached value
            
            # Progressive Enhancement:
            # If brightness < threshold: factor = 1.0 (Full enhance)
            # If brightness > threshold + fade: factor = 0.0 (No enhance)
            # In between: linear blend
            fade_range = 40.0
            if brightness >= settings.brightness_threshold + fade_range:
                # Scene is bright enough - skip enhancement completely
                # Greyscale already applied earlier if enabled
                self.last_used_gpu = settings.greyscale
                return gpu_frame, settings.greyscale
            elif brightness > settings.brightness_threshold:
                # In transition zone - calculate blend factor
                over = brightness - settings.brightness_threshold
                blend_factor = 1.0 - (over / fade_range)
        else:
            self.last_brightness = 0.0  # Not computed in force mode
        
        # Apply enhancement on GPU
        if settings.enhance_lite:
            # Lite mode: Gamma only (on RGB directly)
            enhanced = self._apply_gamma_gpu(tensor, settings.gamma)
        else:
            # Full mode: CLAHE on Y + Gamma on Y
            enhanced = self._apply_clahe_gamma_gpu(
                tensor, 
                settings.clahe_clip, 
                settings.clahe_grid,
                settings.gamma
            )
        
        # Apply progressive blending if needed
        if blend_factor < 1.0:
            # result = original * (1-factor) + enhanced * factor
            # lerp(end, weight) -> start + weight * (end - start)
            # We want: original + factor * (enhanced - original)
            # So we use tensor.lerp(enhanced, blend_factor)
            enhanced = tensor.lerp(enhanced, blend_factor)
        
        result = GpuFrame(enhanced, is_bgr=gpu_frame._is_bgr)
        self.last_used_gpu = True
        return result, True
    
    def _apply_temporal_denoise(self, current: torch.Tensor, alpha: float) -> torch.Tensor:
        """
        Apply temporal smoothing: out = (1-alpha)*last + alpha*current.
        Uses in-place updates on a persistent buffer to minimize allocations.
        """
        # Reset history if shape changes or first frame
        if self._last_frame_tensor is None or self._last_frame_tensor.shape != current.shape:
            self._last_frame_tensor = current.clone()
            return current
        
        # accum = (1-alpha)*accum + alpha*current
        self._last_frame_tensor.lerp_(current, alpha)
        
        # Downstream enhancement ops (CLAHE, gamma, greyscale) are all out-of-place,
        # so returning the buffer directly is safe — no clone needed.
        return self._last_frame_tensor
    
    def _compute_brightness_gpu(self, tensor: torch.Tensor) -> float:
        """Compute average brightness from GPU tensor (RGB format)."""
        # Y = 0.299*R + 0.587*G + 0.114*B (RGB order)
        weights = torch.tensor([0.299, 0.587, 0.114], device=tensor.device)
        gray = (tensor * weights.view(1, 3, 1, 1)).sum(dim=1, keepdim=True)
        brightness = gray.mean().item() * 255.0
        return brightness
    
    def _apply_gamma_gpu(self, tensor: torch.Tensor, gamma: float) -> torch.Tensor:
        """Apply gamma correction on GPU (RGB directly)."""
        if gamma == 1.0:
            return tensor
        
        inv_gamma = 1.0 / gamma
        return torch.pow(tensor.clamp(0, 1), inv_gamma)
    
    def _apply_clahe_gamma_gpu(
        self, 
        tensor: torch.Tensor,
        clip: float,
        grid: int,
        gamma: float
    ) -> torch.Tensor:
        """
        Apply CLAHE and gamma on Y channel using kornia.
        
        Optimized path: Apply CLAHE only to Y channel (luminance),
        which is faster and produces better color preservation.
        """
        if not self._gpu_available:
            return tensor
        
        # Convert RGB to YCbCr
        ycbcr = rgb_to_ycbcr(tensor)
        
        # Extract Y channel
        y = ycbcr[:, 0:1, :, :]

        _, _, h, w = y.shape
        can_run_clahe = h >= grid and w >= grid
        
        # Apply CLAHE on Y channel only (much faster than full RGB).
        # Very small or extremely skinny ROIs can still fail inside Kornia's
        # tile padding logic even when the nominal grid check passes, so fall
        # back to gamma-only enhancement instead of crashing the app.
        if clip > 1.0 and can_run_clahe:
            try:
                y = equalize_clahe(y, clip_limit=clip, grid_size=(grid, grid))
            except (RuntimeError, ValueError):
                pass
        
        # Apply gamma on Y channel
        if gamma != 1.0:
            inv_gamma = 1.0 / gamma
            y = torch.pow(y.clamp(0, 1), inv_gamma)
        
        # Reassemble YCbCr and convert back to RGB
        ycbcr_new = ycbcr.clone()
        ycbcr_new[:, 0:1, :, :] = y
        
        return ycbcr_to_rgb(ycbcr_new)

    def _apply_greyscale_gpu(self, tensor: torch.Tensor) -> torch.Tensor:
        """
        Convert RGB tensor to greyscale (mono camera simulation).
        
        Uses standard luminance weights: Y = 0.299*R + 0.587*G + 0.114*B
        Returns a 3-channel tensor with identical R, G, B values (for compatibility).
        """
        # Compute luminance using standard BT.601 weights (RGB order)
        weights = torch.tensor([0.299, 0.587, 0.114], device=tensor.device, dtype=tensor.dtype)
        gray = (tensor * weights.view(1, 3, 1, 1)).sum(dim=1, keepdim=True)
        
        # Expand back to 3 channels (R=G=B=Y)
        return gray.expand(-1, 3, -1, -1)


class GpuResizer:
    """GPU-accelerated frame resizing using torch."""
    
    def resize(
        self, 
        gpu_frame: GpuFrame, 
        scale: float = 0.5,
        target_size: Optional[Tuple[int, int]] = None
    ) -> GpuFrame:
        """
        Resize frame on GPU.
        
        Args:
            gpu_frame: Input frame on GPU
            scale: Scale factor (used if target_size not specified)
            target_size: (width, height) target size
            
        Returns:
            Resized frame on GPU
        """
        tensor = gpu_frame.tensor
        h, w = tensor.shape[2], tensor.shape[3]
        
        if target_size:
            new_w, new_h = target_size
        else:
            new_w = int(w * scale)
            new_h = int(h * scale)
        
        if (new_w, new_h) == (w, h):
            return gpu_frame
        
        # Use bilinear interpolation for quality
        resized = F.interpolate(
            tensor,
            size=(new_h, new_w),
            mode='bilinear',
            align_corners=False
        )
        
        return GpuFrame(resized, is_bgr=gpu_frame._is_bgr)


class GpuPipeline:
    """
    Main GPU pipeline orchestrator.
    
    Manages the GPU frame flow:
    1. Upload CPU frame to GPU
    2. Enhance (CLAHE + Gamma) on GPU
    3. Provide GPU tensor for YOLO (zero-copy)
    4. Resize for preview on GPU
    5. Download preview to CPU (rate-limited with CPU-side cache)
    """
    
    def __init__(self, settings: Optional[GpuPipelineSettings] = None):
        self.settings = settings or GpuPipelineSettings()
        self._enhancer = GpuEnhancer()
        self._resizer = GpuResizer()
        self._bg_subtractor = None  # Set by FrameProcessor (shared instance)
        
        # Preview rate limiting - cache on CPU side to skip GPU→CPU copy
        self._last_preview_time: float = 0.0
        self._preview_interval: float = 0.0  # seconds between previews
        self._cached_preview: Optional[np.ndarray] = None  # CPU-side cache
        
        # Timing stats
        self.timing: Dict[str, float] = {}
    
    def update_settings(self, settings: GpuPipelineSettings):
        """Update pipeline settings."""
        self.settings = settings
        # Clear cached preview as settings (resolution/enhancement) might have changed
        self._cached_preview = None
        
        if settings.preview_fps_cap:
            self._preview_interval = 1.0 / settings.preview_fps_cap
        else:
            self._preview_interval = 0.0

    def _resolve_roi(self, frame_w: int, frame_h: int) -> Dict[str, int | bool]:
        """Return clamped ROI metadata in full-frame pixels."""
        if not self.settings.roi_enabled or frame_w <= 1 or frame_h <= 1:
            return {
                'enabled': False,
                'x': 0,
                'y': 0,
                'w': frame_w,
                'h': frame_h,
            }

        x = max(0, min(int(self.settings.roi_x), frame_w - 1))
        y = max(0, min(int(self.settings.roi_y), frame_h - 1))
        w = max(1, int(self.settings.roi_w))
        h = max(1, int(self.settings.roi_h))
        x2 = max(x + 1, min(frame_w, x + w))
        y2 = max(y + 1, min(frame_h, y + h))
        return {
            'enabled': True,
            'x': x,
            'y': y,
            'w': x2 - x,
            'h': y2 - y,
        }

    def _crop_to_roi(self, gpu_frame: GpuFrame, roi: Dict[str, int | bool]) -> GpuFrame:
        """Crop a GPU frame to the active ROI."""
        if not roi.get('enabled'):
            return gpu_frame

        x = int(roi['x'])
        y = int(roi['y'])
        w = int(roi['w'])
        h = int(roi['h'])
        tensor = gpu_frame.tensor[:, :, y:y + h, x:x + w]
        return GpuFrame(tensor, is_bgr=gpu_frame._is_bgr)
    
    def process(
        self, 
        frame: np.ndarray,
        preview_enabled: bool = True
    ) -> Tuple[torch.Tensor, Optional[np.ndarray], Dict[str, float]]:
        """
        Process a frame through the GPU pipeline.
        
        Rate limiting is handled internally - preview is generated at preview_fps_cap rate.
        Check timing['preview_new'] to know if this is a fresh preview frame.
        
        Args:
            frame: BGR numpy array (HWC, uint8) from camera
            preview_enabled: Whether preview is wanted at all (False = never generate)
            
        Returns:
            (yolo_tensor, preview_frame, timing_dict)
            - yolo_tensor: GPU tensor ready for YOLO (BCHW, float32, RGB, at yolo_imgsz)
            - preview_frame: BGR numpy array for display (may be cached)
            - timing_dict: includes 'preview_new' (bool) indicating fresh frame
        """
        timing: Dict[str, float] = {}
        current_time = time.time()
        frame_h, frame_w = frame.shape[:2]
        roi = self._resolve_roi(frame_w, frame_h)
        
        # 1. Upload to GPU (BGR -> RGB conversion happens here)
        t0 = time.time()
        gpu_frame = self._upload_to_gpu(frame)
        timing['upload'] = (time.time() - t0) * 1000
        
        # Ensure we don't hold onto old CPU cache if we were reusing frames (we aren't, but good practice)
        gpu_frame.invalidate_cache()
        
        # 1.5. Background subtraction (on GPU, before enhancement)
        if (self.settings.bg_subtract_enabled and 
                self._bg_subtractor is not None and 
                self._bg_subtractor.has_reference):
            t0 = time.time()
            self._bg_subtractor.ensure_gpu_ref(gpu_frame.tensor.device)
            fg_tensor = self._bg_subtractor.apply_gpu(
                gpu_frame.tensor, self.settings.bg_subtract_sensitivity
            )
            gpu_frame = GpuFrame(fg_tensor, is_bgr=False)
            timing['bg_subtract'] = (time.time() - t0) * 1000
            timing['bg_fg_ratio'] = self._bg_subtractor.foreground_ratio
            timing['bg_mismatched'] = self._bg_subtractor.is_mismatched
        else:
            timing['bg_subtract'] = 0.0

        gpu_frame = self._crop_to_roi(gpu_frame, roi)
        timing['roi'] = roi
        
        # 2. Enhancement (on GPU, in RGB)
        t0 = time.time()
        enhanced_frame, was_enhanced = self._enhancer.enhance(gpu_frame, self.settings)
        timing['enhance'] = (time.time() - t0) * 1000
        timing['enhance_active'] = was_enhanced
        timing['enhance_gpu'] = self._enhancer.last_used_gpu
        timing['brightness'] = self._enhancer.last_brightness
        
        # 3. Resize for YOLO with letterboxing (preserve aspect ratio)
        t0 = time.time()
        yolo_tensor, letterbox_info = self._prepare_yolo_input(enhanced_frame, self.settings.yolo_imgsz)
        timing['yolo_resize'] = (time.time() - t0) * 1000
        timing['letterbox'] = letterbox_info  # For coordinate unscaling
        
        # 4. Preview path: rate-limited GPU resize + download
        # Single rate limiter here - app just checks preview_new flag
        should_generate = preview_enabled
        if should_generate and self._preview_interval > 0:
            if current_time - self._last_preview_time < self._preview_interval:
                should_generate = False
        
        if should_generate:
            t0 = time.time()
            preview_target = (
                (int(roi['w']), int(roi['h']))
                if roi.get('enabled')
                else (self.settings.preview_width, self.settings.preview_height)
            )
            # Resize to exact preview dimensions on GPU
            preview_gpu = self._resizer.resize(
                enhanced_frame, 
                target_size=preview_target
            )
            timing['preview_resize'] = (time.time() - t0) * 1000
            
            # GPU→CPU download
            t0 = time.time()
            preview_frame = preview_gpu.to_numpy_bgr()
            timing['preview_download'] = (time.time() - t0) * 1000
            timing.update(preview_gpu.last_download_timing)
            self._cached_preview = preview_frame  # Cache on CPU
            timing['preview_new'] = True
            
            self._last_preview_time = current_time
        else:
            # Return cached preview (no GPU work for preview)
            preview_frame = self._cached_preview
            timing['preview_resize'] = 0.0
            timing['preview_download'] = 0.0
            timing['preview_new'] = False
        
        # Store original frame dimensions for keypoint scaling
        timing['original_w'] = frame_w
        timing['original_h'] = frame_h
        
        self.timing = timing
        return yolo_tensor, preview_frame, timing
    
    def process_gpu_tensor(
        self,
        gpu_tensor: torch.Tensor,
        preview_enabled: bool = True
    ) -> Tuple[torch.Tensor, Optional[np.ndarray], Dict[str, float]]:
        """
        Process a pre-uploaded GPU tensor (optimized path for IDS camera).
        
        This bypasses the CPU→GPU upload step for lowest latency when the
        camera driver provides frames directly as GPU tensors.
        
        Args:
            gpu_tensor: GPU tensor (1, 3, H, W) float32 [0,1] RGB format
            preview_enabled: Whether to generate preview
            
        Returns:
            Same as process(): (yolo_tensor, preview_frame, timing_dict)
        """
        timing: Dict[str, float] = {}
        current_time = time.time()
        _, _, frame_h, frame_w = gpu_tensor.shape
        roi = self._resolve_roi(frame_w, frame_h)
        
        # No upload needed - tensor is already on GPU
        timing['upload'] = 0.0
        
        # Wrap in GpuFrame
        gpu_frame = GpuFrame(gpu_tensor, is_bgr=False)  # IDS provides RGB-equivalent
        
        # 1.5. Background subtraction (on GPU, before enhancement)
        if (self.settings.bg_subtract_enabled and 
                self._bg_subtractor is not None and 
                self._bg_subtractor.has_reference):
            t0 = time.time()
            self._bg_subtractor.ensure_gpu_ref(gpu_frame.tensor.device)
            fg_tensor = self._bg_subtractor.apply_gpu(
                gpu_frame.tensor, self.settings.bg_subtract_sensitivity
            )
            gpu_frame = GpuFrame(fg_tensor, is_bgr=False)
            timing['bg_subtract'] = (time.time() - t0) * 1000
            timing['bg_fg_ratio'] = self._bg_subtractor.foreground_ratio
            timing['bg_mismatched'] = self._bg_subtractor.is_mismatched
        else:
            timing['bg_subtract'] = 0.0

        gpu_frame = self._crop_to_roi(gpu_frame, roi)
        timing['roi'] = roi
        
        # 2. Enhancement (on GPU, in RGB)
        t0 = time.time()
        enhanced_frame, was_enhanced = self._enhancer.enhance(gpu_frame, self.settings)
        timing['enhance'] = (time.time() - t0) * 1000
        timing['enhance_active'] = was_enhanced
        timing['enhance_gpu'] = self._enhancer.last_used_gpu
        timing['brightness'] = self._enhancer.last_brightness
        
        # 3. Resize for YOLO with letterboxing
        t0 = time.time()
        yolo_tensor, letterbox_info = self._prepare_yolo_input(enhanced_frame, self.settings.yolo_imgsz)
        timing['yolo_resize'] = (time.time() - t0) * 1000
        timing['letterbox'] = letterbox_info
        
        # 4. Preview path (same rate-limited logic as process())
        should_generate = preview_enabled
        if should_generate and self._preview_interval > 0:
            time_since_last = current_time - self._last_preview_time
            should_generate = time_since_last >= self._preview_interval
        
        if should_generate:
            t0 = time.time()
            preview_target = (
                (int(roi['w']), int(roi['h']))
                if roi.get('enabled')
                else (self.settings.preview_width, self.settings.preview_height)
            )
            preview_tensor = self._resizer.resize(
                enhanced_frame,
                target_size=preview_target
            )
            timing['preview_resize'] = (time.time() - t0) * 1000
            
            # GPU→CPU download (included in to_numpy_bgr)
            t0 = time.time()
            preview_frame = preview_tensor.to_numpy_bgr()
            timing['preview_download'] = (time.time() - t0) * 1000
            timing.update(preview_tensor.last_download_timing)
            timing['preview_new'] = True
            
            # Cache and update timestamp
            self._cached_preview = preview_frame
            self._last_preview_time = current_time
        else:
            preview_frame = self._cached_preview
            timing['preview_resize'] = 0.0
            timing['preview_download'] = 0.0
            timing['preview_new'] = False
        
        # Store dimensions from tensor shape
        timing['original_w'] = frame_w
        timing['original_h'] = frame_h
        
        self.timing = timing
        return yolo_tensor, preview_frame, timing

    def _upload_to_gpu(self, frame: np.ndarray) -> GpuFrame:
        """Upload numpy BGR frame to GPU tensor, convert to RGB.
        
        Uses pinned memory + non_blocking transfer for async DMA.
        """
        # Pin source memory for async H2D transfer
        tensor = torch.from_numpy(frame)  # (H, W, 3) uint8, CPU
        if not hasattr(self, '_upload_pinned') or self._upload_pinned.shape != tensor.shape:
            self._upload_pinned = torch.empty_like(tensor).pin_memory()
        self._upload_pinned.copy_(tensor)
        
        gpu_tensor = self._upload_pinned.to(DEVICE, non_blocking=True)
        gpu_tensor = gpu_tensor.permute(2, 0, 1).unsqueeze(0).float().mul_(1.0 / 255.0)
        # BGR -> RGB (flip channel dimension)
        gpu_tensor = gpu_tensor.flip(1)
        
        return GpuFrame(gpu_tensor, is_bgr=False)  # Now RGB
    
    def _prepare_yolo_input(self, gpu_frame: GpuFrame, imgsz: int) -> Tuple[torch.Tensor, Dict]:
        """
        Prepare GPU tensor for YOLO input with letterboxing.
        
        Uses letterbox (pad to square, preserve aspect ratio) to match
        how YOLO was trained. Returns letterbox info for coordinate unscaling.
        
        Args:
            gpu_frame: Input frame on GPU
            imgsz: Target square size for YOLO
            
        Returns:
            (tensor, letterbox_info) where letterbox_info contains:
            - scale: scale factor applied
            - pad_x: horizontal padding (left)
            - pad_y: vertical padding (top)
        """
        tensor = gpu_frame.tensor  # (1, 3, H, W) RGB
        _, _, h, w = tensor.shape
        
        # Calculate letterbox parameters (same as YOLO's letterbox)
        scale = min(imgsz / w, imgsz / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        
        # Padding to center the image
        pad_x = (imgsz - new_w) // 2
        pad_y = (imgsz - new_h) // 2
        
        letterbox_info = {
            'scale': scale,
            'pad_x': pad_x,
            'pad_y': pad_y,
            'new_w': new_w,
            'new_h': new_h,
        }
        
        # Resize preserving aspect ratio
        if new_w != w or new_h != h:
            tensor = F.interpolate(
                tensor,
                size=(new_h, new_w),
                mode='bilinear',
                align_corners=False
            )
        
        # Pad to square (imgsz x imgsz)
        if pad_x > 0 or pad_y > 0 or new_w != imgsz or new_h != imgsz:
            # Pad: (left, right, top, bottom)
            pad_right = imgsz - new_w - pad_x
            pad_bottom = imgsz - new_h - pad_y
            tensor = F.pad(tensor, (pad_x, pad_right, pad_y, pad_bottom), value=0.5)  # Gray padding
        
        return tensor, letterbox_info
