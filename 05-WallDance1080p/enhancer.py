"""
Image enhancement pipeline for low-light conditions.
Includes CLAHE, gamma correction, and adaptive brightness detection.
"""

import cv2
import numpy as np
from config import (
    CLAHE_CLIP_LIMIT, CLAHE_TILE_SIZE,
    GAMMA_CORRECTION, GAMMA_AUTO,
    BRIGHTNESS_THRESHOLD, ENHANCE_AUTO_DETECT
)


class ImageEnhancer:
    """Adaptive image enhancement for low-light conditions."""
    
    def __init__(self):
        self.clahe = cv2.createCLAHE(
            clipLimit=CLAHE_CLIP_LIMIT,
            tileGridSize=(CLAHE_TILE_SIZE, CLAHE_TILE_SIZE)
        )
        self.gamma = GAMMA_CORRECTION
        self.gamma_lut = self._build_gamma_lut(self.gamma)
        self._last_brightness = 128
    
    def _build_gamma_lut(self, gamma):
        """Build lookup table for gamma correction."""
        inv_gamma = 1.0 / gamma
        table = np.array([
            ((i / 255.0) ** inv_gamma) * 255
            for i in np.arange(0, 256)
        ]).astype("uint8")
        return table
    
    def get_brightness(self, frame):
        """Get average brightness of frame."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return np.mean(gray)
    
    def needs_enhancement(self, frame):
        """Check if frame needs enhancement based on brightness."""
        if not ENHANCE_AUTO_DETECT:
            return True
        
        brightness = self.get_brightness(frame)
        self._last_brightness = brightness
        return brightness < BRIGHTNESS_THRESHOLD
    
    def apply_clahe(self, frame):
        """Apply CLAHE enhancement."""
        # Convert to LAB color space
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        
        # Apply CLAHE to L channel
        l = self.clahe.apply(l)
        
        # Merge and convert back
        lab = cv2.merge([l, a, b])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    
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
    
    def enhance(self, frame, force=False):
        """
        Apply full enhancement pipeline.
        
        Args:
            frame: Input BGR frame
            force: Force enhancement regardless of brightness
            
        Returns:
            Enhanced frame, was_enhanced flag
        """
        if not force and not self.needs_enhancement(frame):
            return frame, False
        
        # Apply CLAHE
        enhanced = self.apply_clahe(frame)
        
        # Apply gamma correction
        enhanced = self.apply_gamma(enhanced)
        
        return enhanced, True
    
    def get_status(self):
        """Get current enhancement status for display."""
        return {
            'brightness': self._last_brightness,
            'gamma': self.gamma,
            'enhanced': self._last_brightness < BRIGHTNESS_THRESHOLD
        }
