"""
GPU-accelerated image enhancement for low-light conditions.

Phase 2 of GPU Path Implementation (kornia version):
- GPU CLAHE using kornia.enhance.equalize_clahe
- GPU Gamma using torch tensor operations
- Uses PyTorch CUDA (same as YOLO) - no OpenCV CUDA needed

Performance optimizations:
- Pre-allocated GPU buffers to avoid allocation per frame
- Apply CLAHE only to luminance (Y) channel, not all RGB
- Gamma applied via pre-computed LUT on CPU (faster for single channel)
"""

import cv2
import numpy as np
import torch
from dataclasses import dataclass
from typing import Optional, Tuple

# Check PyTorch CUDA availability
TORCH_CUDA_AVAILABLE = torch.cuda.is_available()
if TORCH_CUDA_AVAILABLE:
    print(f"[Enhancer] PyTorch CUDA available: {torch.cuda.get_device_name(0)}")
else:
    print("[Enhancer] PyTorch CUDA not available, using CPU")

# Kornia GPU enhancement removed - use GpuPipeline for GPU path
KORNIA_AVAILABLE = False


@dataclass
class EnhancerSettings:
    """Settings for image enhancement."""
    enabled: bool = False
    clahe_clip: float = 2.0
    clahe_grid: int = 8
    gamma: float = 1.0
    
    def needs_enhancement(self) -> bool:
        """Check if any enhancement is needed."""
        return self.enabled and (self.clahe_clip > 1.0 or self.gamma != 1.0)


class Enhancer:
    """
    GPU-accelerated image enhancer using kornia/PyTorch.
    
    Uses PyTorch CUDA for GPU operations - same backend as YOLO.
    Falls back to CPU (OpenCV) when CUDA unavailable.
    
    Optimization: Only CLAHE runs on GPU (for Y channel only).
    Gamma uses CPU LUT which is faster for single-channel operations.
    """
    
    def __init__(self):
        self._gpu_available = False # GPU path moved to GpuPipeline
        
        # CPU CLAHE object (created lazily, cached by parameters)
        self._cpu_clahe: Optional[cv2.CLAHE] = None
        self._cpu_clahe_params: Tuple[float, int] = (0, 0)
        
        # CPU LUT for gamma (cached by gamma value)
        self._cpu_lut: Optional[np.ndarray] = None
        self._cpu_lut_gamma: float = 0
        
        # Track if we're using GPU path
        self._last_used_gpu = False
        
        # Cached brightness from last enhancement (avoids extra grayscale conversion)
        self._cached_brightness: float = 0.0
    
    @property
    def cuda_available(self) -> bool:
        """Check if CUDA is available for GPU enhancement."""
        return self._gpu_available
    
    @property
    def last_used_gpu(self) -> bool:
        """Check if last enhancement used GPU."""
        return self._last_used_gpu
    
    # =========================================================================
    # PUBLIC API
    # =========================================================================
    
    def enhance(self, frame: np.ndarray, settings: EnhancerSettings) -> np.ndarray:
        """
        Apply enhancement to frame (GPU or CPU based on availability).
        
        Args:
            frame: BGR uint8 numpy array
            settings: Enhancement parameters
            
        Returns:
            Enhanced BGR uint8 numpy array
        """
        if not settings.needs_enhancement():
            self._last_used_gpu = False
            # Still compute brightness for status (but skip extra work if possible)
            self._cached_brightness = self._compute_brightness_fast(frame)
            return frame
        
        # Use CPU path - OpenCV CLAHE is faster than kornia when data is on CPU
        # GPU enhancement only benefits when data is already on GPU (zero-copy pipeline)
        # CPU CLAHE: ~3ms vs GPU path: ~25ms (due to CPU↔GPU transfer overhead)
        self._last_used_gpu = False
        return self._enhance_cpu(frame, settings)
    
    def _compute_brightness_fast(self, frame: np.ndarray) -> float:
        """Fast brightness computation using subsampling."""
        # Subsample for speed (every 4th pixel in both directions)
        gray = cv2.cvtColor(frame[::4, ::4], cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray))
    
    # =========================================================================
    # CPU PATH - OpenCV operations
    # =========================================================================
    
    def _enhance_cpu(self, frame: np.ndarray, settings: EnhancerSettings) -> np.ndarray:
        """
        CPU-based enhancement pipeline.
        
        Uses OpenCV's standard CLAHE and LUT operations.
        Uses YCrCb color space (faster than LAB, good enough for enhancement).
        """
        # Convert BGR -> YCrCb (faster than LAB)
        ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        y, cr, cb = cv2.split(ycrcb)
        
        # Store brightness from Y channel (avoids separate grayscale conversion)
        self._cached_brightness = float(np.mean(y))
        
        # Apply CLAHE to Y channel
        if settings.clahe_clip > 1.0:
            clahe = self._get_cpu_clahe(settings.clahe_clip, settings.clahe_grid)
            y = clahe.apply(y)
        
        # Apply gamma to Y channel
        if settings.gamma != 1.0:
            lut = self._get_cpu_lut(settings.gamma)
            y = cv2.LUT(y, lut)
        
        # Merge and convert back
        ycrcb = cv2.merge([y, cr, cb])
        return cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)
    
    def _get_cpu_clahe(self, clip: float, grid: int) -> cv2.CLAHE:
        """Get or create CPU CLAHE object with caching."""
        params = (clip, grid)
        if self._cpu_clahe is None or self._cpu_clahe_params != params:
            self._cpu_clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid))
            self._cpu_clahe_params = params
        return self._cpu_clahe
    
    def _get_cpu_lut(self, gamma: float) -> np.ndarray:
        """Get or create CPU LUT for gamma correction with caching."""
        if self._cpu_lut is None or self._cpu_lut_gamma != gamma:
            inv_gamma = 1.0 / gamma
            self._cpu_lut = np.array([((i / 255.0) ** inv_gamma) * 255 
                                       for i in np.arange(256)]).astype(np.uint8)
            self._cpu_lut_gamma = gamma
        return self._cpu_lut
    
    # =========================================================================
    # LEGACY API - For backward compatibility with existing code
    # =========================================================================
    
    def apply_clahe(self, frame: np.ndarray, clip_limit: float = 2.0, 
                    tile_grid_size: int = 8) -> np.ndarray:
        """
        Apply CLAHE enhancement (legacy API, CPU only).
        """
        settings = EnhancerSettings(
            enabled=True,
            clahe_clip=clip_limit,
            clahe_grid=tile_grid_size,
            gamma=1.0
        )
        return self._enhance_cpu(frame, settings)
    
    def apply_gamma(self, frame: np.ndarray, gamma: float) -> np.ndarray:
        """
        Apply gamma correction (legacy API, CPU only).
        """
        if gamma == 1.0:
            return frame
        
        lut = self._get_cpu_lut(gamma)
        return cv2.LUT(frame, lut)


# =============================================================================
# BACKWARD COMPATIBILITY - ImageEnhancer alias
# =============================================================================

class ImageEnhancer(Enhancer):
    """
    Legacy compatibility class for the old ImageEnhancer API.
    
    Provides the same interface as the original ImageEnhancer:
    - enhance(frame) -> (enhanced_frame, status_dict)
    - enhance_simple(frame) -> enhanced_frame
    - get_status() -> {"brightness": value}
    - clahe_clip property
    - gamma property
    - _update_clahe() method
    - _update_gamma_lut() method
    """
    
    def __init__(self):
        super().__init__()
        self._last_brightness = 0.0
        self._clahe_clip = 2.0
        self._gamma = 1.0
        self._default_settings = EnhancerSettings(
            enabled=True,
            clahe_clip=self._clahe_clip,
            clahe_grid=8,
            gamma=self._gamma
        )
        # Cached settings for enhance_simple (gamma only, no CLAHE - "Lite" mode)
        self._simple_settings = EnhancerSettings(
            enabled=True,
            clahe_clip=1.0,  # No CLAHE (clip=1.0 means disabled)
            clahe_grid=8,
            gamma=self._gamma  # Use configured gamma
        )
    
    @property
    def clahe_clip(self) -> float:
        """Get CLAHE clip limit."""
        return self._clahe_clip
    
    @clahe_clip.setter
    def clahe_clip(self, value: float):
        """Set CLAHE clip limit."""
        self._clahe_clip = value
        self._default_settings.clahe_clip = value
    
    @property
    def gamma(self) -> float:
        """Get gamma value."""
        return self._gamma
    
    @gamma.setter
    def gamma(self, value: float):
        """Set gamma value."""
        self._gamma = value
        self._default_settings.gamma = value
    
    def _update_clahe(self):
        """Update CLAHE object (legacy method, now a no-op since caching is automatic)."""
        pass
    
    def _update_gamma_lut(self):
        """Update gamma LUT (legacy method, now a no-op since caching is automatic)."""
        pass
    
    def enhance(self, frame: np.ndarray) -> Tuple[np.ndarray, dict]:
        """
        Legacy enhance API - returns (enhanced_frame, status_dict).
        """
        # Apply enhancement using new API
        # Brightness is computed inside _enhance_cpu from Y channel (no extra conversion)
        enhanced = super().enhance(frame, self._default_settings)
        
        # Use cached brightness from _enhance_cpu
        self._last_brightness = self._cached_brightness
        
        status = {"brightness": self._last_brightness}
        return enhanced, status
    
    def enhance_simple(self, frame: np.ndarray) -> np.ndarray:
        """
        Simple enhancement - gamma only, no CLAHE ("Lite" mode).
        
        Much faster than full enhance. Uses direct LUT on all channels
        (faster than YCrCb conversion, minimal color shift for gamma).
        """
        gamma = self._gamma
        if gamma == 1.0:
            self._last_used_gpu = False
            # Fast brightness using subsampling
            self._last_brightness = self._compute_brightness_fast(frame)
            return frame
        
        # Fast brightness using subsampling
        self._last_brightness = self._compute_brightness_fast(frame)
        
        # Direct LUT on all channels (1.1ms vs 10ms for YCrCb approach)
        lut = self._get_cpu_lut(gamma)
        self._last_used_gpu = False
        return cv2.LUT(frame, lut)
    
    def get_status(self) -> dict:
        """
        Get current enhancement status.
        """
        return {"brightness": self._last_brightness}
    
    def _compute_brightness(self, frame: np.ndarray) -> float:
        """Compute average brightness of frame."""
        if frame is None or frame.size == 0:
            return 0.0
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray))


# Module-level singleton for convenience
_enhancer: Optional[Enhancer] = None


def get_enhancer() -> Enhancer:
    """Get the shared enhancer instance."""
    global _enhancer
    if _enhancer is None:
        _enhancer = Enhancer()
    return _enhancer
