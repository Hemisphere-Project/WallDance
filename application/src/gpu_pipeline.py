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
    
    # Denoising
    denoise_enabled: bool = False
    denoise_alpha: float = 0.6      # Weight of new frame (0.0-1.0). Lower = more smoothing.
    
    # Preview - exact dimensions for GPU resize
    preview_width: int = 960        # Target preview width
    preview_height: int = 540       # Target preview height
    preview_fps_cap: Optional[float] = None  # None = no cap, e.g. 10.0 = 10fps
    
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
    
    @property
    def shape(self) -> Tuple[int, int]:
        """Return (H, W) of the frame."""
        return self.tensor.shape[2], self.tensor.shape[3]
    
    def to_numpy_bgr(self) -> np.ndarray:
        """Convert to BGR numpy array (HWC, uint8). Caches result."""
        if self._cpu_cache is not None:
            return self._cpu_cache
        
        # BCHW float [0,1] -> HWC uint8 BGR
        t = self.tensor.squeeze(0)  # (3, H, W)
        if not self._is_bgr:
            # RGB -> BGR
            t = t.flip(0)
        
        # GPU -> CPU, permute, scale
        arr = (t.permute(1, 2, 0).cpu().numpy() * 255).astype(np.uint8)
        self._cpu_cache = arr
        return arr
    
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
            return gpu_frame, False
        
        tensor = gpu_frame.tensor  # (1, 3, H, W) in RGB
        
        # 1. Temporal Denoising (Raw RGB)
        # Apply before brightness check/enhancement to stabilize the signal
        if settings.denoise_enabled:
            tensor = self._apply_temporal_denoise(tensor, settings.denoise_alpha)
            # Update gpu_frame wrapper with denoised tensor
            gpu_frame = GpuFrame(tensor, is_bgr=gpu_frame._is_bgr)
        
        # Compute brightness from Y channel for auto-bypass check
        # Only needed if not in force mode
        blend_factor = 1.0
        if not settings.enhance_force:
            brightness = self._compute_brightness_gpu(tensor)
            self.last_brightness = brightness
            
            # Progressive Enhancement:
            # If brightness < threshold: factor = 1.0 (Full enhance)
            # If brightness > threshold + fade: factor = 0.0 (No enhance)
            # In between: linear blend
            fade_range = 40.0
            if brightness >= settings.brightness_threshold + fade_range:
                # Scene is bright enough - skip enhancement completely
                self.last_used_gpu = False
                return gpu_frame, False
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
        # lerp_(end, weight) -> self + weight*(end - self)
        # We want: (1-alpha)*self + alpha*current
        # This matches lerp exactly.
        self._last_frame_tensor.lerp_(current, alpha)
        
        # Return a clone to ensure downstream operations don't modify our history buffer
        # (unless we are sure downstream is out-of-place, but safety first)
        return self._last_frame_tensor.clone()
    
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
        
        # Apply CLAHE on Y channel only (much faster than full RGB)
        if clip > 1.0:
            y = equalize_clahe(y, clip_limit=clip, grid_size=(grid, grid))
        
        # Apply gamma on Y channel
        if gamma != 1.0:
            inv_gamma = 1.0 / gamma
            y = torch.pow(y.clamp(0, 1), inv_gamma)
        
        # Reassemble YCbCr and convert back to RGB
        ycbcr_new = ycbcr.clone()
        ycbcr_new[:, 0:1, :, :] = y
        
        return ycbcr_to_rgb(ycbcr_new)


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
        
        # 1. Upload to GPU (BGR -> RGB conversion happens here)
        t0 = time.time()
        gpu_frame = self._upload_to_gpu(frame)
        timing['upload'] = (time.time() - t0) * 1000
        
        # Ensure we don't hold onto old CPU cache if we were reusing frames (we aren't, but good practice)
        gpu_frame.invalidate_cache()
        
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
            # Resize to exact preview dimensions on GPU
            preview_gpu = self._resizer.resize(
                enhanced_frame, 
                target_size=(self.settings.preview_width, self.settings.preview_height)
            )
            timing['preview_resize'] = (time.time() - t0) * 1000
            
            # GPU→CPU download
            t0 = time.time()
            preview_frame = preview_gpu.to_numpy_bgr()
            self._cached_preview = preview_frame  # Cache on CPU
            timing['preview_download'] = (time.time() - t0) * 1000
            timing['preview_new'] = True
            
            self._last_preview_time = current_time
        else:
            # Return cached preview (no GPU work for preview)
            preview_frame = self._cached_preview
            timing['preview_resize'] = 0.0
            timing['preview_download'] = 0.0
            timing['preview_new'] = False
        
        # Store original frame dimensions for keypoint scaling
        timing['original_w'] = frame.shape[1]
        timing['original_h'] = frame.shape[0]
        
        self.timing = timing
        return yolo_tensor, preview_frame, timing
    
    def _upload_to_gpu(self, frame: np.ndarray) -> GpuFrame:
        """Upload numpy BGR frame to GPU tensor, convert to RGB."""
        h, w, c = frame.shape
        
        # Convert HWC uint8 BGR -> BCHW float32 RGB [0,1]
        tensor = torch.from_numpy(frame).to(DEVICE)
        tensor = tensor.permute(2, 0, 1).unsqueeze(0).float() / 255.0
        # BGR -> RGB (flip channel dimension)
        tensor = tensor.flip(1)
        
        return GpuFrame(tensor, is_bgr=False)  # Now RGB
    
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
    
    @property
    def enhancer_brightness(self) -> float:
        """Get last computed brightness value."""
        return self._enhancer.last_brightness
    
    @property
    def enhancer_used_gpu(self) -> bool:
        """Check if last enhancement used GPU."""
        return self._enhancer.last_used_gpu


# =============================================================================
# Convenience function for testing
# =============================================================================

def benchmark_gpu_pipeline():
    """Benchmark the GPU pipeline."""
    if not CUDA_AVAILABLE:
        print("CUDA not available, cannot benchmark GPU pipeline")
        return
    
    import time
    
    # Create test frame
    frame = np.random.randint(0, 256, (1080, 1920, 3), dtype=np.uint8)
    
    # Create pipeline
    settings = GpuPipelineSettings(
        enhance_enabled=True,
        enhance_lite=False,
        enhance_force=True,  # Force to always enhance
        clahe_clip=2.0,
        gamma=1.2,
        preview_width=960,
        preview_height=540
    )
    pipeline = GpuPipeline(settings)
    
    # Warmup
    print("Warming up...")
    for _ in range(10):
        pipeline.process(frame, need_preview=True)
    
    # Benchmark with preview
    print("\n=== With Preview ===")
    times = []
    for _ in range(50):
        t0 = time.time()
        yolo_tensor, preview, timing = pipeline.process(frame, need_preview=True)
        torch.cuda.synchronize()
        times.append((time.time() - t0) * 1000)
    
    print(f"Total: {np.mean(times):.2f} ms")
    print(f"Breakdown: {timing}")
    
    # Benchmark without preview
    print("\n=== Without Preview (YOLO only) ===")
    times = []
    for _ in range(50):
        t0 = time.time()
        yolo_tensor, preview, timing = pipeline.process(frame, need_preview=False)
        torch.cuda.synchronize()
        times.append((time.time() - t0) * 1000)
    
    print(f"Total: {np.mean(times):.2f} ms")
    print(f"Breakdown: {timing}")
    
    # Test preview rate limiting
    print("\n=== Preview Rate Limiting (10 fps) ===")
    settings.preview_fps_cap = 10.0
    pipeline.update_settings(settings)
    
    preview_count = 0
    for i in range(100):
        _, preview, _ = pipeline.process(frame, need_preview=True)
        if preview is not None:
            preview_count += 1
        time.sleep(0.01)  # Simulate 100fps camera
    
    print(f"Previews generated: {preview_count}/100 frames")


if __name__ == "__main__":
    benchmark_gpu_pipeline()
