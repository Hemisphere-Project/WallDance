"""Runtime-side ROI facts (DECOMPOSITION_PLAN Phase 2 (6)).

The mouse/drag/paint editor lives in ``ui/roi_mask_editor.py``; this tiny
state object holds what headless code (calibration flows, config apply,
the pipeline settings) needs without importing anything ui-side: the
source-frame size ROI coordinates refer to, and the clamped effective
rect derived from the live settings.
"""
from __future__ import annotations


class RoiState:
    """Source-frame size + clamped effective ROI rect over ProcessingSettings."""

    def __init__(self, settings, source_size) -> None:
        self.settings = settings
        self.source_size = tuple(source_size)  # (w, h) the ROI coords refer to

    @staticmethod
    def normalize_rect(x: int, y: int, w: int, h: int,
                       frame_w: int, frame_h: int) -> tuple:
        frame_w = max(1, int(frame_w))
        frame_h = max(1, int(frame_h))
        x = max(0, min(int(x), frame_w - 1))
        y = max(0, min(int(y), frame_h - 1))
        w = max(1, int(w))
        h = max(1, int(h))
        w = min(w, frame_w - x)
        h = min(h, frame_h - y)
        return x, y, w, h

    def effective_roi(self, frame_w: int, frame_h: int) -> tuple:
        x, y, w, h = self.normalize_rect(
            self.settings.roi_x,
            self.settings.roi_y,
            self.settings.roi_w or frame_w,
            self.settings.roi_h or frame_h,
            frame_w,
            frame_h,
        )
        return x, y, w, h
