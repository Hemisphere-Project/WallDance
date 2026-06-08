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
    MOTION_BRIDGE_MOG2_SCALE,
    MOTION_BRIDGE_MIN_AREA,
    MOTION_BRIDGE_MIN_AREA_LOWLIGHT_MULT,
    MOTION_FIRST_WARMUP_FRAMES,
    MOTION_FIRST_STATIC_BLOB_FRAMES,
    MOTION_LOWLIGHT_LUMA_THRESHOLD,
    MOTION_LOWLIGHT_MEDIAN_KERNEL,
    MOTION_LOWLIGHT_VAR_THRESHOLD_MULT,
    MOTION_CROSSVAL_MIN_COHERENCE,
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

    def __init__(self):
        # Base varThreshold — overridable at runtime (Go-Live calibration sets it
        # from the measured background-noise sigma).  The adaptive low-light path
        # multiplies this base; see feed().
        self._var_threshold = float(MOTION_BRIDGE_MOG2_VAR_THRESHOLD)
        self._mog2 = cv2.createBackgroundSubtractorMOG2(
            history=MOTION_BRIDGE_MOG2_HISTORY,
            varThreshold=self._var_threshold,
            detectShadows=True,
        )
        self._learn_rate = MOTION_BRIDGE_MOG2_LEARN_RATE
        self._min_area = MOTION_BRIDGE_MIN_AREA
        # Morphological kernels
        self._erode_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        self._dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        # Cached foreground mask from last feed()
        self._fg_mask: np.ndarray | None = None
        # Morphologically-cleaned mask for ratio queries (no speckle noise)
        self._clean_mask: np.ndarray | None = None
        self._scale = MOTION_BRIDGE_MOG2_SCALE
        self._inv_scale = 1.0 / self._scale
        self._last_brightness = 255.0
        # Warmup counter — suppress blobs until MOG2 has settled
        self._frame_count = 0
        # Consecutive-frame buffer for frame-diff fallback.
        # These store a lightly-downscaled (no blur) version of the raw gray
        # so frame differencing preserves motion contrast that Gaussian blur
        # would destroy.
        self._prev_raw: np.ndarray | None = None
        self._curr_raw: np.ndarray | None = None
        self._diff_scale = min(1.0, self._scale * 2.0)  # less aggressive downscale for diff
        self._diff_inv_scale = 1.0 / self._diff_scale
        # Static blob suppression: spatial hash → consecutive frame count
        self._static_cells: dict[tuple[int, int], int] = {}
        self._static_cell_size = 32  # grid cell size in downscaled pixels

    def set_scale(self, scale: float) -> None:
        """Change downscale factor and reset MOG2 model."""
        scale = max(0.25, min(1.0, scale))
        if abs(scale - self._scale) < 0.01:
            return
        self._scale = scale
        self._inv_scale = 1.0 / scale
        self.reset()

    def set_learn_rate(self, rate: float) -> None:
        """Change the MOG2 learning rate (e.g. faster for lighting adaptation)."""
        self._learn_rate = max(0.0, min(1.0, rate))

    def set_var_threshold(self, base: float) -> None:
        """Set the base MOG2 varThreshold (Go-Live calibration → measured noise).

        Stored as the base that the adaptive low-light path in feed() multiplies,
        and pushed to the live model so it takes effect without a reset.
        """
        self._var_threshold = max(1.0, float(base))
        self._mog2.setVarThreshold(self._var_threshold)

    @property
    def has_mask(self) -> bool:
        """Whether a foreground mask is available from a previous feed()."""
        return self._fg_mask is not None

    @property
    def frame_count(self) -> int:
        """Number of frames processed by this detector instance."""
        return self._frame_count

    @property
    def last_brightness(self) -> float:
        """Mean brightness of the latest preprocessed grayscale frame."""
        return self._last_brightness

    def motion_ratio_in_bbox(self, x: float, y: float, w: float, h: float,
                             core_scale: float = 1.0,
                             include_shadows: bool = False,
                             use_clean_mask: bool = True,
                             require_coherence: bool = True) -> float:
        """Return fraction of cleaned foreground pixels inside a bbox.

        Uses the morphologically-cleaned mask (not the raw MOG2 output)
        so scattered noise speckle is stripped before counting.
        Coordinates are in **original** (unscaled) frame space.
        Returns 0.0 if no mask is available or bbox is degenerate.
        """
        if use_clean_mask:
            mask = self._clean_mask
        elif self._fg_mask is not None:
            if include_shadows:
                mask = (self._fg_mask >= 127).astype(np.uint8) * 255
            else:
                mask = (self._fg_mask == 255).astype(np.uint8) * 255
        else:
            mask = None
        if mask is None:
            return 0.0
        core_scale = max(0.1, min(1.0, core_scale))
        if core_scale < 1.0:
            inset_x = w * (1.0 - core_scale) * 0.5
            inset_y = h * (1.0 - core_scale) * 0.5
            x += inset_x
            y += inset_y
            w *= core_scale
            h *= core_scale
        # Map bbox to downscaled mask coordinates
        s = self._scale
        sx = max(0, int(x * s))
        sy = max(0, int(y * s))
        sw = max(1, int(w * s))
        sh = max(1, int(h * s))
        mh, mw = mask.shape[:2]
        x1 = min(sx, mw - 1)
        y1 = min(sy, mh - 1)
        x2 = min(sx + sw, mw)
        y2 = min(sy + sh, mh)
        if x2 <= x1 or y2 <= y1:
            return 0.0
        roi = mask[y1:y2, x1:x2]
        total = roi.size
        if total == 0:
            return 0.0
        fg_count = int(np.count_nonzero(roi))
        if fg_count == 0:
            return 0.0
        ratio = fg_count / total
        # Coherence check: reject if foreground is scattered noise rather
        # than a single coherent blob.  The largest connected component must
        # account for a minimum fraction of total fg pixels in the ROI.
        if require_coherence and MOTION_CROSSVAL_MIN_COHERENCE > 0.0 and fg_count >= 4:
            n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
                roi, connectivity=8)
            if n_labels > 1:
                # Label 0 is background; find largest fg component
                largest_cc = int(stats[1:, cv2.CC_STAT_AREA].max())
                coherence = largest_cc / fg_count
                if coherence < MOTION_CROSSVAL_MIN_COHERENCE:
                    return 0.0
        return ratio

    @staticmethod
    def preprocess(gray: np.ndarray, scale: float) -> tuple[np.ndarray, float]:
        """Blur, denoise and downscale a grayscale frame.

        Returns (small_frame, brightness) so the caller can share one
        preprocessed image across multiple MotionDetector instances.
        """
        if MOTION_LOWLIGHT_MEDIAN_KERNEL >= 3:
            kernel = MOTION_LOWLIGHT_MEDIAN_KERNEL
            if kernel % 2 == 0:
                kernel += 1
        else:
            kernel = 0
        brightness = float(np.mean(gray))
        if kernel >= 3 and brightness < MOTION_LOWLIGHT_LUMA_THRESHOLD:
            gray = cv2.medianBlur(gray, kernel)
            gray = cv2.GaussianBlur(gray, (5, 5), 0)
        else:
            gray = cv2.GaussianBlur(gray, (3, 3), 0)
        if scale < 1.0:
            small = cv2.resize(gray, None, fx=scale, fy=scale,
                               interpolation=cv2.INTER_AREA)
        else:
            small = gray
        return small, brightness

    def feed(self, gray: np.ndarray) -> None:
        """Update the MOG2 background model on a downscaled frame.

        Call every frame to keep the model current.  The expensive
        contour extraction only happens when detect() is called.
        """
        small, brightness = self.preprocess(gray, self._scale)
        self.feed_preprocessed(small, brightness, raw_gray=gray)

    def feed_preprocessed(self, small: np.ndarray, brightness: float,
                          raw_gray: np.ndarray | None = None) -> None:
        """Update MOG2 from an already blurred+resized frame.

        Use this when multiple detectors share the same scale so
        preprocessing runs once instead of per-detector.

        Args:
            small: Blurred and downscaled grayscale frame for MOG2.
            brightness: Mean brightness of the frame.
            raw_gray: Original grayscale frame (before blur/downscale).
                      Used for frame-diff bridge fallback which needs
                      unblurred pixels to detect subtle motion.
        """
        self._frame_count += 1
        self._last_brightness = brightness
        # Keep consecutive raw-gray pair for frame-diff bridge fallback.
        # Uses a light downscale (no blur) so motion contrast is preserved.
        if raw_gray is not None:
            ds = self._diff_scale
            if ds < 1.0:
                raw_small = cv2.resize(raw_gray, None, fx=ds, fy=ds,
                                       interpolation=cv2.INTER_AREA)
            else:
                raw_small = raw_gray
            if self._curr_raw is not None and self._curr_raw.shape == raw_small.shape:
                peak_diff = int(cv2.absdiff(raw_small, self._curr_raw).max())
                if peak_diff > 8:  # real motion threshold on unblurred pixels
                    self._prev_raw = self._curr_raw
                    self._curr_raw = raw_small
            else:
                self._prev_raw = None
                self._curr_raw = raw_small
        # Adaptive varThreshold: raise in low light to reject noise at the model level.
        # Scales the (possibly calibrated) base, not the module constant.
        if brightness < MOTION_LOWLIGHT_LUMA_THRESHOLD:
            target_var = self._var_threshold * MOTION_LOWLIGHT_VAR_THRESHOLD_MULT
        else:
            target_var = self._var_threshold
        self._mog2.setVarThreshold(target_var)
        # MOG2 apply — returns 0=bg, 127=shadow, 255=fg
        self._fg_mask = self._mog2.apply(small, learningRate=self._learn_rate)
        # Build cleaned mask: morphological open strips scattered noise pixels
        # while preserving coherent blobs.  This is used by motion_ratio_in_bbox()
        # so cross-validation never counts noise speckle as real motion.
        clean = (self._fg_mask == 255).astype(np.uint8) * 255
        clean = cv2.morphologyEx(clean, cv2.MORPH_OPEN, self._erode_kernel)
        self._clean_mask = clean

    def detect(
        self,
        person_height: int,
        aspect_range: tuple[float, float] | None = None,
        allow_during_warmup: bool = False,
        suppress_static: bool = True,
        include_shadows: bool = False,
        min_area_scale: float = 1.0,
        min_height_ratio: float = 0.3,
    ) -> List[MotionBlob]:
        """Extract filtered blobs from the last feed() mask.

        Only call this when there are actually unmatched tracks to bridge.
        Blob coordinates are scaled back to original resolution.

        Args:
            person_height: Expected person height in *original* pixels.
            aspect_range: Optional (min, max) aspect ratio override.
                          Defaults to (0.15, 3.0).
            allow_during_warmup: When True, bypass the motion-first warmup
                          gate. This is used for track-conditioned
                          bridging where an existing track already constrains
                          blob selection.
            suppress_static: When True, drop blobs that stay in the same
                          grid cell for too many frames. Synthetic blob
                          spawning keeps this enabled; motion bridging can
                          disable it so a slow dancer still remains bridgeable.
            include_shadows: When True, treat MOG2 shadow pixels (127) as
                          foreground.  In IR setups where the dancer is
                          darker than the background, MOG2 classifies
                          the body as shadow rather than definite
                          foreground — this flag recovers that signal.
            min_area_scale: Multiplier for the minimum blob area. Values
                          below 1.0 make bridge detection more permissive.
            min_height_ratio: Minimum accepted blob height as a fraction of
                          person_height.

        Returns:
            List of MotionBlob with bbox, centroid and area in original coords.
        """
        if self._fg_mask is None:
            return []

        # Suppress all blobs during MOG2 warmup
        if not allow_during_warmup and self._frame_count < MOTION_FIRST_WARMUP_FRAMES:
            return []

        # person_height in downscaled space
        scaled_ph = max(1, int(person_height * self._scale))

        # Keep definite foreground; optionally include shadow pixels (127)
        # which represent motion in IR setups where the dancer body appears
        # darker than the background wall.
        if include_shadows:
            fg_mask = (self._fg_mask >= 127).astype(np.uint8) * 255
        else:
            fg_mask = (self._fg_mask == 255).astype(np.uint8) * 255

        # Morphology: erode to break projection speckle, dilate to fill gaps
        fg_mask = cv2.erode(fg_mask, self._erode_kernel, iterations=1)
        fg_mask = cv2.dilate(fg_mask, self._dilate_kernel, iterations=1)

        # Find contours
        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Height range for person filtering (in downscaled coords)
        min_h = max(3, int(scaled_ph * min_height_ratio))
        max_h = int(scaled_ph * 2.5)
        frame_area = fg_mask.shape[0] * fg_mask.shape[1]
        max_area = frame_area * 0.25  # no single blob > 25% of frame
        min_area_scale = max(0.1, float(min_area_scale))
        min_area = max(6, int(self._min_area * min_area_scale * self._scale * self._scale))
        if self._last_brightness < MOTION_LOWLIGHT_LUMA_THRESHOLD:
            min_area = int(min_area * MOTION_BRIDGE_MIN_AREA_LOWLIGHT_MULT)

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
            ar_min, ar_max = aspect_range if aspect_range else (0.15, 3.0)
            if aspect > ar_max or aspect < ar_min:
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

        # Merge overlapping / nearby blobs that likely belong to one person
        blobs = self._merge_nearby_blobs(blobs, person_height)

        # Motion-first synthetic detections need aggressive static suppression,
        # but bridge mode already has a strong track-position gate.
        if suppress_static:
            blobs = self._suppress_static_blobs(blobs)

        return blobs

    def extract_local_motion_blob(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        target_centroid: np.ndarray | None = None,
        min_motion_ratio: float = 0.02,
        include_shadows: bool = False,
    ) -> tuple[MotionBlob | None, float]:
        """Extract a track-conditioned blob from the raw MOG2 motion mask.

        This is intended as a fallback for already-established tracks when
        global contour extraction finds no full-body bridge blobs. The query
        box is expected in original-frame coordinates.
        """
        if self._fg_mask is None:
            return None, 0.0
        if include_shadows:
            mask = (self._fg_mask >= 127).astype(np.uint8) * 255
        else:
            mask = (self._fg_mask == 255).astype(np.uint8) * 255

        sx = max(0, int(x * self._scale))
        sy = max(0, int(y * self._scale))
        sw = max(1, int(w * self._scale))
        sh = max(1, int(h * self._scale))
        mh, mw = mask.shape[:2]
        x1 = min(sx, mw - 1)
        y1 = min(sy, mh - 1)
        x2 = min(sx + sw, mw)
        y2 = min(sy + sh, mh)
        if x2 <= x1 or y2 <= y1:
            return None, 0.0

        roi = mask[y1:y2, x1:x2]
        total = roi.size
        if total == 0:
            return None, 0.0

        fg_count = int(np.count_nonzero(roi))
        if fg_count <= 0:
            return None, 0.0

        motion_ratio = fg_count / total
        if motion_ratio < min_motion_ratio:
            return None, motion_ratio

        n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            roi, connectivity=8)
        if n_labels <= 1:
            return None, motion_ratio

        target_small = None
        if target_centroid is not None:
            target_small = np.array(target_centroid, dtype=np.float64) * self._scale

        best_label = None
        best_key = None
        for label in range(1, n_labels):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area <= 0:
                continue
            cx = float(centroids[label][0] + x1)
            cy = float(centroids[label][1] + y1)
            if target_small is not None:
                dist = float(np.linalg.norm(np.array([cx, cy]) - target_small))
                key = (dist, -area)
            else:
                key = (-area,)
            if best_key is None or key < best_key:
                best_key = key
                best_label = label

        if best_label is None:
            return None, motion_ratio

        left = int(stats[best_label, cv2.CC_STAT_LEFT]) + x1
        top = int(stats[best_label, cv2.CC_STAT_TOP]) + y1
        width = int(stats[best_label, cv2.CC_STAT_WIDTH])
        height = int(stats[best_label, cv2.CC_STAT_HEIGHT])
        cx = float(centroids[best_label][0] + x1)
        cy = float(centroids[best_label][1] + y1)
        inv = self._inv_scale
        blob = MotionBlob(
            bbox=np.array([left * inv, top * inv, width * inv, height * inv], dtype=np.float64),
            centroid=np.array([cx * inv, cy * inv], dtype=np.float64),
            area=float(stats[best_label, cv2.CC_STAT_AREA] * inv * inv),
        )
        return blob, motion_ratio

    def frame_diff_blob_in_bbox(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        target_centroid: np.ndarray | None = None,
        threshold: int = 15,
        min_ratio: float = 0.03,
    ) -> tuple[MotionBlob | None, float]:
        """Detect motion via absolute frame difference inside a bbox.

        This is the last-resort bridge fallback: when the MOG2 background
        model has fully absorbed the dancer (no foreground *or* shadow
        pixels), comparing consecutive raw frames still detects any
        pixel-intensity change.

        Uses the lightly-downscaled raw gray pair (no Gaussian blur) so
        motion contrast is preserved.

        Coordinates are in **original** (unscaled) frame space.
        """
        if self._prev_raw is None or self._curr_raw is None:
            return None, 0.0

        s = self._diff_scale
        inv = self._diff_inv_scale
        sx = max(0, int(x * s))
        sy = max(0, int(y * s))
        sw = max(1, int(w * s))
        sh = max(1, int(h * s))
        mh, mw = self._curr_raw.shape[:2]
        x1 = min(sx, mw - 1)
        y1 = min(sy, mh - 1)
        x2 = min(sx + sw, mw)
        y2 = min(sy + sh, mh)
        if x2 <= x1 or y2 <= y1:
            return None, 0.0

        roi_curr = self._curr_raw[y1:y2, x1:x2]
        roi_prev = self._prev_raw[y1:y2, x1:x2]
        diff = cv2.absdiff(roi_curr, roi_prev)
        changed = (diff > threshold).astype(np.uint8) * 255

        total = changed.size
        if total == 0:
            return None, 0.0
        fg_count = int(np.count_nonzero(changed))
        if fg_count == 0:
            return None, 0.0
        motion_ratio = fg_count / total
        if motion_ratio < min_ratio:
            return None, motion_ratio

        n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            changed, connectivity=8)
        if n_labels <= 1:
            return None, motion_ratio

        target_small = None
        if target_centroid is not None:
            target_small = np.array(target_centroid, dtype=np.float64) * s

        best_label = None
        best_key = None
        for label in range(1, n_labels):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area <= 0:
                continue
            cx = float(centroids[label][0] + x1)
            cy = float(centroids[label][1] + y1)
            if target_small is not None:
                dist = float(np.linalg.norm(np.array([cx, cy]) - target_small))
                key = (dist, -area)
            else:
                key = (-area,)
            if best_key is None or key < best_key:
                best_key = key
                best_label = label

        if best_label is None:
            return None, motion_ratio

        left = int(stats[best_label, cv2.CC_STAT_LEFT]) + x1
        top = int(stats[best_label, cv2.CC_STAT_TOP]) + y1
        width = int(stats[best_label, cv2.CC_STAT_WIDTH])
        height = int(stats[best_label, cv2.CC_STAT_HEIGHT])
        cx = float(centroids[best_label][0] + x1)
        cy = float(centroids[best_label][1] + y1)
        blob = MotionBlob(
            bbox=np.array([left * inv, top * inv, width * inv, height * inv],
                          dtype=np.float64),
            centroid=np.array([cx * inv, cy * inv], dtype=np.float64),
            area=float(stats[best_label, cv2.CC_STAT_AREA] * inv * inv),
        )
        return blob, motion_ratio

    def bridge_diagnostics(self, x: float, y: float, w: float, h: float,
                           threshold: int = 6) -> dict:
        """Return diagnostic stats for bridge debugging (original-frame coords)."""
        info: dict = {
            "frame_count": self._frame_count,
            "has_fg_mask": self._fg_mask is not None,
            "has_prev": self._prev_raw is not None,
            "has_curr": self._curr_raw is not None,
        }
        s = self._scale
        sx, sy = max(0, int(x * s)), max(0, int(y * s))
        sw, sh = max(1, int(w * s)), max(1, int(h * s))
        if self._fg_mask is not None:
            mh, mw = self._fg_mask.shape[:2]
            x1, y1 = min(sx, mw - 1), min(sy, mh - 1)
            x2, y2 = min(sx + sw, mw), min(sy + sh, mh)
            info["mask_shape"] = [mh, mw]
            info["roi_px"] = [x1, y1, x2, y2]
            if x2 > x1 and y2 > y1:
                roi = self._fg_mask[y1:y2, x1:x2]
                total = roi.size
                info["roi_total_px"] = total
                info["fg255"] = int(np.count_nonzero(roi == 255))
                info["shadow127"] = int(np.count_nonzero(roi == 127))
                info["bg0"] = int(np.count_nonzero(roi == 0))
        if self._prev_raw is not None and self._curr_raw is not None:
            ds = self._diff_scale
            dx, dy = max(0, int(x * ds)), max(0, int(y * ds))
            dw, dh = max(1, int(w * ds)), max(1, int(h * ds))
            dmh, dmw = self._curr_raw.shape[:2]
            dx1, dy1 = min(dx, dmw - 1), min(dy, dmh - 1)
            dx2, dy2 = min(dx + dw, dmw), min(dy + dh, dmh)
            if dx2 > dx1 and dy2 > dy1:
                diff = cv2.absdiff(
                    self._curr_raw[dy1:dy2, dx1:dx2],
                    self._prev_raw[dy1:dy2, dx1:dx2],
                )
                info["diff_mean"] = round(float(diff.mean()), 2)
                info["diff_max"] = int(diff.max())
                info["diff_gt_thr"] = int(np.count_nonzero(diff > threshold))
                info["diff_total"] = diff.size
        return info

    @staticmethod
    def _merge_nearby_blobs(blobs: list, person_height: int) -> list:
        """Merge blobs whose bboxes overlap or are within merge_gap of each other."""
        if len(blobs) <= 1:
            return blobs

        merge_gap = person_height * 0.5
        merged_flags = [False] * len(blobs)
        result: list = []

        for i in range(len(blobs)):
            if merged_flags[i]:
                continue
            # Gather cluster starting from blob i
            cluster = [i]
            merged_flags[i] = True
            queue = [i]
            while queue:
                cur = queue.pop()
                bx, by, bw, bh = blobs[cur].bbox
                for j in range(len(blobs)):
                    if merged_flags[j]:
                        continue
                    jx, jy, jw, jh = blobs[j].bbox
                    # Check if expanded bboxes overlap
                    if (bx - merge_gap <= jx + jw and
                            bx + bw + merge_gap >= jx and
                            by - merge_gap <= jy + jh and
                            by + bh + merge_gap >= jy):
                        merged_flags[j] = True
                        cluster.append(j)
                        queue.append(j)

            if len(cluster) == 1:
                result.append(blobs[cluster[0]])
            else:
                # Union bbox of cluster
                xs = [blobs[k].bbox[0] for k in cluster]
                ys = [blobs[k].bbox[1] for k in cluster]
                x2s = [blobs[k].bbox[0] + blobs[k].bbox[2] for k in cluster]
                y2s = [blobs[k].bbox[1] + blobs[k].bbox[3] for k in cluster]
                ux = min(xs)
                uy = min(ys)
                uw = max(x2s) - ux
                uh = max(y2s) - uy
                total_area = sum(blobs[k].area for k in cluster)
                result.append(MotionBlob(
                    bbox=np.array([ux, uy, uw, uh], dtype=np.float64),
                    centroid=np.array([ux + uw / 2.0, uy + uh / 2.0],
                                      dtype=np.float64),
                    area=total_area,
                ))

        return result

    def _suppress_static_blobs(self, blobs: list) -> list:
        """Remove blobs that occupy the same grid cell for too many frames.

        Detects persistent foreground artifacts (e.g. painted background
        features misclassified as motion) by tracking which grid cells
        have blobs frame-over-frame.  If a cell has had a blob for
        MOTION_FIRST_STATIC_BLOB_FRAMES consecutive frames, suppress it.
        """
        cs = self._static_cell_size
        inv = self._inv_scale

        # Map current blobs to grid cells
        current_cells: set[tuple[int, int]] = set()
        for blob in blobs:
            cx, cy = blob.centroid
            cell = (int(cx * self._scale / cs), int(cy * self._scale / cs))
            current_cells.add(cell)

        # Update counters
        new_static: dict[tuple[int, int], int] = {}
        for cell in current_cells:
            new_static[cell] = self._static_cells.get(cell, 0) + 1
        self._static_cells = new_static

        # Filter out blobs in cells that exceeded the static threshold
        threshold = MOTION_FIRST_STATIC_BLOB_FRAMES
        result = []
        for blob in blobs:
            cx, cy = blob.centroid
            cell = (int(cx * self._scale / cs), int(cy * self._scale / cs))
            if self._static_cells.get(cell, 0) < threshold:
                result.append(blob)
        return result

    def reset(self):
        """Re-create the MOG2 model (e.g. after scene change).

        Preserves any calibrated base varThreshold across the reset.
        """
        self._mog2 = cv2.createBackgroundSubtractorMOG2(
            history=MOTION_BRIDGE_MOG2_HISTORY,
            varThreshold=self._var_threshold,
            detectShadows=True,
        )
        self._fg_mask = None
        self._clean_mask = None
        self._frame_count = 0
        self._static_cells.clear()
