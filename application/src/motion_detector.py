"""
MOG2-based motion blob detector for bridging YOLO detection gaps.

Designed for fixed-camera IR-bandpass setups with static background.
MOG2 maintains a per-pixel Gaussian mixture model that classifies pixels
as background, foreground, or shadow.  With a low learning rate (0.001),
dancers remain foreground indefinitely while slow BG changes (wind,
vibration) get absorbed.

Phase 3 of the Tracking Robustness Plan.
"""

from dataclasses import dataclass
from typing import List

import cv2
import numpy as np

from config import (
    MOTION_BRIDGE_MOG2_HISTORY,
    MOTION_BRIDGE_MOG2_VAR_THRESHOLD,
    MOTION_BRIDGE_MOG2_LEARN_RATE,
    MOTION_BRIDGE_MIN_AREA,
)


@dataclass
class MotionBlob:
    """A foreground region detected by MOG2."""
    bbox: np.ndarray       # [x, y, w, h]
    centroid: np.ndarray   # [cx, cy]
    area: float


class MotionDetector:
    """Detect foreground blobs using MOG2 background subtraction.

    Filters blobs by area, aspect ratio, and height relative to the
    expected person height.  Shadow detection is ON so projection leak
    (value 127 in the MOG2 mask) is rejected automatically.
    """

    _DOWNSAMPLE = 0.5  # Half-resolution for MOG2 (50px dancers → 25px, still OK for centroids)

    def __init__(self):
        self._mog2 = cv2.createBackgroundSubtractorMOG2(
            history=MOTION_BRIDGE_MOG2_HISTORY,
            varThreshold=MOTION_BRIDGE_MOG2_VAR_THRESHOLD,
            detectShadows=True,
        )
        self._learn_rate = MOTION_BRIDGE_MOG2_LEARN_RATE
        self._min_area = MOTION_BRIDGE_MIN_AREA
        # Morphological kernels
        self._erode_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        self._dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        # Cached foreground mask from last feed()
        self._fg_mask: np.ndarray | None = None
        self._inv_scale = 1.0 / self._DOWNSAMPLE

    def feed(self, gray: np.ndarray) -> None:
        """Update the MOG2 background model on a downscaled frame.

        Call every frame to keep the model current.  The expensive
        contour extraction only happens when detect() is called.
        """
        small = cv2.resize(gray, None, fx=self._DOWNSAMPLE, fy=self._DOWNSAMPLE,
                           interpolation=cv2.INTER_AREA)
        # MOG2 apply — returns 0=bg, 127=shadow, 255=fg
        self._fg_mask = self._mog2.apply(small, learningRate=self._learn_rate)

    def detect(self, person_height: int) -> List[MotionBlob]:
        """Extract filtered blobs from the last feed() mask.

        Only call this when there are actually unmatched tracks to bridge.
        Blob coordinates are scaled back to original resolution.

        Args:
            person_height: Expected person height in *original* pixels.

        Returns:
            List of MotionBlob with bbox, centroid and area in original coords.
        """
        if self._fg_mask is None:
            return []

        # person_height in downscaled space
        scaled_ph = max(1, int(person_height * self._DOWNSAMPLE))

        # Keep only definite foreground (discard shadows at 127)
        fg_mask = (self._fg_mask == 255).astype(np.uint8) * 255

        # Morphology: erode to break projection speckle, dilate to fill gaps
        fg_mask = cv2.erode(fg_mask, self._erode_kernel, iterations=1)
        fg_mask = cv2.dilate(fg_mask, self._dilate_kernel, iterations=1)

        # Find contours
        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Height range for person filtering (in downscaled coords)
        min_h = max(3, int(scaled_ph * 0.3))
        max_h = int(scaled_ph * 2.5)
        frame_area = fg_mask.shape[0] * fg_mask.shape[1]
        max_area = frame_area * 0.25  # no single blob > 25% of frame
        min_area = max(10, int(self._min_area * self._DOWNSAMPLE * self._DOWNSAMPLE))

        inv = self._inv_scale
        blobs: List[MotionBlob] = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue

            x, y, w, h = cv2.boundingRect(cnt)

            # Height filter
            if h < min_h or h > max_h:
                continue

            # Aspect ratio filter (reject wide blobs like projection wash)
            aspect = w / max(1, h)
            if aspect > 3.0 or aspect < 0.15:
                continue

            # Scale back to original resolution
            ox, oy, ow, oh = x * inv, y * inv, w * inv, h * inv
            cx = ox + ow / 2.0
            cy = oy + oh / 2.0
            blobs.append(MotionBlob(
                bbox=np.array([ox, oy, ow, oh], dtype=np.float64),
                centroid=np.array([cx, cy], dtype=np.float64),
                area=float(area * inv * inv),
            ))

        return blobs

    def reset(self):
        """Re-create the MOG2 model (e.g. after scene change)."""
        self._mog2 = cv2.createBackgroundSubtractorMOG2(
            history=MOTION_BRIDGE_MOG2_HISTORY,
            varThreshold=MOTION_BRIDGE_MOG2_VAR_THRESHOLD,
            detectShadows=True,
        )
