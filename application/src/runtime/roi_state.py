"""Runtime-side ROI facts (DECOMPOSITION_PLAN Phase 2 (6)).

The mouse/drag/paint editor lives in ``ui/roi_mask_editor.py``; this tiny
state object holds what headless code (calibration flows, config apply,
the pipeline settings) needs without importing anything ui-side: the
source-frame size ROI coordinates refer to, and the clamped effective
rect derived from the live settings.

Drawn vs effective (2026-10-07): ``settings.roi_*`` is the EFFECTIVE rect for the
frames flowing now (``source_size``); ``drawn`` is the ROI as the operator drew or
loaded it, in its own source frame.  A frame of another size -- a take of another
camera crop played back, a crop that flipped -- gets the drawn rect clamped onto it
in the settings, while ``drawn`` stays: frames of the drawn size get it back exactly,
and a config save persists it.  (The clamp used to be written over the operator's
ROI: a 1488x1528 take in a 1776x1300 project cut the width 1322 -> 1254 for good.)
"""
from __future__ import annotations

from typing import Optional


class RoiState:
    """Source-frame size + clamped effective ROI rect over ProcessingSettings."""

    def __init__(self, settings, source_size) -> None:
        self.settings = settings
        self.source_size = tuple(source_size)  # (w, h) the live ROI coords (settings) refer to
        # (x, y, w, h, src_w, src_h): the ROI as drawn / loaded, in its own source frame
        self.drawn: Optional[tuple] = None

    def set_drawn(self, x: int, y: int, w: int, h: int, frame_w: int, frame_h: int) -> None:
        self.drawn = (int(x), int(y), int(w), int(h), int(frame_w), int(frame_h))

    def stored_roi(self) -> tuple:
        """(x, y, w, h, src_w, src_h) a config save persists: the drawn ROI, else the live one."""
        if self.drawn is not None:
            return self.drawn
        s = self.settings
        return (int(s.roi_x), int(s.roi_y), int(s.roi_w), int(s.roi_h),
                int(self.source_size[0]), int(self.source_size[1]))

    def rect_for_frame(self, frame_w: int, frame_h: int) -> tuple:
        """The effective ROI for frames of this size: the drawn ROI as drawn when the size is its
        own, clamped onto the frame otherwise (the live rect when nothing was drawn yet)."""
        if self.drawn is None:
            return self.effective_roi(frame_w, frame_h)
        x, y, w, h, src_w, src_h = self.drawn
        if (int(frame_w), int(frame_h)) == (src_w, src_h):
            return x, y, w, h
        return self.normalize_rect(x, y, w, h, frame_w, frame_h)

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

    def apply_transform(self, delta) -> tuple:
        """Carry the ROI rect across an input-transform change (REQ-5):
        ``delta`` (core.input_transform.InputTransform) maps the old
        transformed frame onto the new one. The rect is clamped to the old
        source first, so it keeps covering the same physical region.
        Returns the new (x, y, w, h); source_size follows."""
        src_w, src_h = self.source_size
        x, y, w, h = self.effective_roi(src_w, src_h)
        nx, ny, nw, nh = delta.map_rect(x, y, w, h, src_w, src_h)
        new_w, new_h = delta.output_size(src_w, src_h)
        nx, ny, nw, nh = self.normalize_rect(nx, ny, nw, nh, new_w, new_h)
        s = self.settings
        s.roi_x, s.roi_y, s.roi_w, s.roi_h = nx, ny, nw, nh
        self.source_size = (new_w, new_h)
        if self.drawn is not None:            # the drawn ROI follows in its own source frame
            dx, dy, dw, dh, dsw, dsh = self.drawn
            dx, dy, dw, dh = delta.map_rect(*self.normalize_rect(dx, dy, dw, dh, dsw, dsh), dsw, dsh)
            dsw, dsh = delta.output_size(dsw, dsh)
            self.set_drawn(*self.normalize_rect(dx, dy, dw, dh, dsw, dsh), dsw, dsh)
        return nx, ny, nw, nh

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
