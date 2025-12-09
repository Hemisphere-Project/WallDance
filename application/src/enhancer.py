"""
Image enhancement pipeline for low-light conditions.
Includes CLAHE, gamma correction, and adaptive brightness detection.
Optimized for performance with optional GPU acceleration.
"""

import cv2
import numpy as np
from config import (
    CLAHE_CLIP_LIMIT, CLAHE_TILE_SIZE,
    GAMMA_CORRECTION, GAMMA_AUTO,
    BRIGHTNESS_THRESHOLD, ENHANCE_AUTO_DETECT
)

# Try to use CUDA-accelerated functions
try:
    _cuda_available = cv2.cuda.getCudaEnabledDeviceCount() > 0
except:
    _cuda_available = False


class ImageEnhancer:
    """Adaptive image enhancement for low-light conditions."""
    
    def __init__(self, use_gpu: bool = True):
        self.clahe_clip = CLAHE_CLIP_LIMIT
        self.clahe_tile = CLAHE_TILE_SIZE
        self.clahe = cv2.createCLAHE(
            clipLimit=self.clahe_clip,
            tileGridSize=(self.clahe_tile, self.clahe_tile)
        )
        self.gamma = GAMMA_CORRECTION
        self.gamma_lut = self._build_gamma_lut(self.gamma)
        self._last_brightness = 128
        
        # GPU acceleration
        self.use_gpu = use_gpu and _cuda_available
        if self.use_gpu:
            try:
                self.gpu_clahe = cv2.cuda.createCLAHE(
                    clipLimit=self.clahe_clip,
                    tileGridSize=(self.clahe_tile, self.clahe_tile)
                )
                print("GPU CLAHE enabled")
            except Exception as e:
                print(f"GPU CLAHE not available: {e}")
                self.use_gpu = False
    
    def _update_clahe(self):
        """Rebuild CLAHE with current parameters."""
        self.clahe = cv2.createCLAHE(
            clipLimit=self.clahe_clip,
            tileGridSize=(self.clahe_tile, self.clahe_tile)
        )
        if self.use_gpu:
            try:
                self.gpu_clahe = cv2.cuda.createCLAHE(
                    clipLimit=self.clahe_clip,
                    tileGridSize=(self.clahe_tile, self.clahe_tile)
                )
            except:
                pass
    
    def _update_gamma_lut(self):
        """Rebuild gamma LUT with current gamma value."""
        self.gamma_lut = self._build_gamma_lut(self.gamma)
    
    def _build_gamma_lut(self, gamma):
        """Build lookup table for gamma correction."""
        inv_gamma = 1.0 / gamma
        table = np.array([
            ((i / 255.0) ** inv_gamma) * 255
            for i in np.arange(0, 256)
        ]).astype("uint8")
        return table
    
    def get_brightness(self, frame):
        """Get average brightness of frame (fast sampling)."""
        # Sample every 4th pixel for speed
        gray = cv2.cvtColor(frame[::4, ::4], cv2.COLOR_BGR2GRAY)
        return np.mean(gray)
    
    def needs_enhancement(self, frame):
        """Check if frame needs enhancement based on brightness."""
        if not ENHANCE_AUTO_DETECT:
            return True
        
        brightness = self.get_brightness(frame)
        self._last_brightness = brightness
        return brightness < BRIGHTNESS_THRESHOLD
    
    def apply_clahe_fast(self, frame):
        """Apply CLAHE enhancement - optimized version.
        
        Works on L channel only (grayscale CLAHE) to avoid costly color conversions.
        """
        # Convert BGR to LAB (this is the expensive part)
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        
        # Apply CLAHE to L channel in-place
        lab[:, :, 0] = self.clahe.apply(lab[:, :, 0])
        
        # Convert back
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    
    def apply_clahe_grayscale(self, frame):
        """Apply CLAHE in YCrCb space - faster than LAB."""
        # YCrCb is faster than LAB for this purpose
        ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        ycrcb[:, :, 0] = self.clahe.apply(ycrcb[:, :, 0])
        return cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)
    
    def apply_clahe(self, frame):
        """Apply CLAHE enhancement."""
        return self.apply_clahe_fast(frame)
    
    def apply_gamma(self, frame, auto=True):
        """Apply gamma correction."""
        if auto and GAMMA_AUTO:
            # Auto-adjust gamma based on brightness
            brightness = self._last_brightness
            if brightness < 40:
                gamma = 1.5
            elif brightness < 60:
                gamma = 1.3
            elif brightness < 80:
                gamma = 1.15
            else:
                gamma = 1.0
            
            if gamma != self.gamma:
                self.gamma = gamma
                self.gamma_lut = self._build_gamma_lut(gamma)
        
        return cv2.LUT(frame, self.gamma_lut)
    
    def enhance(self, frame, force=False, lite=False):
        """
        Apply full enhancement pipeline.
        
        Args:
            frame: Input BGR frame
            force: Force enhancement regardless of brightness
            lite: Use lite mode (gamma only, no CLAHE)
            
        Returns:
            Enhanced frame, was_enhanced flag
        """
        if not force and not self.needs_enhancement(frame):
            return frame, False
        
        if lite:
            # Lite mode: gamma only (very fast)
            enhanced = self.apply_gamma(frame)
        else:
            # Full mode: CLAHE + gamma (slower but better quality)
            enhanced = self.apply_clahe_grayscale(frame)
            enhanced = self.apply_gamma(enhanced)
        
        return enhanced, True
    
    def enhance_simple(self, frame):
        """Simplified enhancement - just gamma, no CLAHE.
        
        Much faster, useful for frame-skip intermediate frames.
        """
        return self.apply_gamma(frame, auto=False)
    
    def get_status(self):
        """Get current enhancement status for display."""
        return {
            'brightness': self._last_brightness,
            'gamma': self.gamma,
            'enhanced': self._last_brightness < BRIGHTNESS_THRESHOLD,
            'gpu': self.use_gpu
        }
