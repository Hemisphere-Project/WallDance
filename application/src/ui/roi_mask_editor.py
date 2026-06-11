"""ROI rectangle editor + exclusion-mask paint editor peeled from WallDanceApp.

DECOMPOSITION_PLAN §5 Phase 2 (6). Method bodies moved verbatim from
app.py; ``self.<app attribute>`` references renamed to constructor-injected
dependencies. This module lives in ``ui/`` and may import dearpygui — it
owns the mouse/drag/paint interaction state and the preview-compose
drawing. The runtime-side ROI facts (source size, effective rect) live in
``runtime/roi_state.py`` (``self.state`` here) so headless code never
imports this module.

``gui`` is a provider callable (the GUI does not exist yet at
construction); each method resolves it locally, mirroring the original
``self.gui`` guards.
"""
from __future__ import annotations

from typing import Callable, Optional

import cv2
import dearpygui.dearpygui as dpg
import numpy as np

from runtime.roi_state import RoiState


class RoiMaskEditor:
    """Owns ROI drag editing, mask cell painting and their preview overlays."""

    def __init__(
        self,
        state: RoiState,
        settings,
        processor,
        imgsz_presets: tuple,
        gui: Callable[[], object],
        request_reprocess: Callable[[], None],
    ) -> None:
        self.state = state
        self.settings = settings
        self.processor = processor
        self.imgsz_presets = imgsz_presets
        self.gui = gui
        self.request_reprocess = request_reprocess

        # ROI state (stored in full-frame source coordinates)
        self.roi_edit_mode = False
        self._roi_drag_active = False
        self._roi_drag_mode: Optional[str] = None
        self._roi_drag_origin: Optional[tuple] = None
        self._roi_drag_start_rect: Optional[tuple] = None
        self._roi_mouse_was_down = False

        # Exclusion-mask manual editor (ROADMAP §4.2 Phase 2 ④)
        self.mask_edit_mode = False
        self._mask_paint_active = False
        self._mask_paint_value: Optional[bool] = None
        self._mask_painted_cells: set = set()
        self._mask_mouse_was_down = False

    # ------------------------------------------------------------------
    # ROI rect + imgsz advice
    # ------------------------------------------------------------------
    def _sync_roi_ui(self):
        gui = self.gui()
        if not gui:
            return
        gui.sync_checkbox("roi_enable", self.settings.roi_enabled)
        gui.update_roi_rect_text(
            self.settings.roi_x,
            self.settings.roi_y,
            self.settings.roi_w,
            self.settings.roi_h,
            edit_mode=self.roi_edit_mode,
        )
        self._update_imgsz_roi_warning()

    def _get_recommended_imgsz_for_roi(self) -> Optional[tuple]:
        if not self.settings.roi_enabled:
            return None

        frame_w, frame_h = self.state.source_size
        _, _, roi_w, roi_h = self.state.effective_roi(frame_w, frame_h)
        long_edge = max(roi_w, roi_h)
        min_target = long_edge * 1.5
        max_target = long_edge * 2.0

        low = self.imgsz_presets[-1]
        for preset in self.imgsz_presets:
            if preset >= min_target:
                low = preset
                break

        in_range = [preset for preset in self.imgsz_presets if min_target <= preset <= max_target]
        high = in_range[-1] if in_range else low
        return low, high, roi_w, roi_h

    def _get_imgsz_roi_warning(self) -> Optional[str]:
        roi_info = self._get_recommended_imgsz_for_roi()
        if roi_info is None:
            return None

        low, high, roi_w, roi_h = roi_info
        current = int(self.settings.imgsz)
        if current >= low:
            return None

        if low == high:
            target = f"{low}px"
        else:
            target = f"{low}-{high}px"

        return f"ROI {roi_w}x{roi_h}: consider {target} imgsz for better detection."

    def _update_imgsz_roi_warning(self):
        gui = self.gui()
        if not gui:
            return
        gui.update_imgsz_roi_warning(self._get_imgsz_roi_warning())

    def _set_roi_rect(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        *,
        frame_w: Optional[int] = None,
        frame_h: Optional[int] = None,
        sync_ui: bool = True,
        request_reprocess: bool = True,
    ):
        if frame_w is None or frame_h is None:
            frame_w, frame_h = self.state.source_size
        x, y, w, h = self.state.normalize_rect(x, y, w, h, frame_w, frame_h)
        self.settings.roi_x = x
        self.settings.roi_y = y
        self.settings.roi_w = w
        self.settings.roi_h = h
        self.state.source_size = (frame_w, frame_h)
        if sync_ui:
            self._sync_roi_ui()
        if request_reprocess:
            self.request_reprocess()

    def _clamp_roi_to_source(self, frame_w: int, frame_h: int, *, sync_ui: bool = True):
        self._set_roi_rect(
            self.settings.roi_x,
            self.settings.roi_y,
            self.settings.roi_w or frame_w,
            self.settings.roi_h or frame_h,
            frame_w=frame_w,
            frame_h=frame_h,
            sync_ui=sync_ui,
            request_reprocess=False,
        )

    # ------------------------------------------------------------------
    # Preview-space mouse mapping
    # ------------------------------------------------------------------
    def _get_preview_item_rect(self) -> Optional[tuple]:
        if not dpg.does_item_exist("video_image"):
            return None
        try:
            state = dpg.get_item_state("video_image")
        except Exception:
            state = None

        rect_min = None
        rect_size = None
        if isinstance(state, dict):
            rect_min = state.get("rect_min")
            rect_size = state.get("rect_size")

        if rect_min is None or rect_size is None:
            try:
                rect_min = dpg.get_item_rect_min("video_image")
                rect_size = dpg.get_item_rect_size("video_image")
            except Exception:
                return None

        if len(rect_min) < 2 or len(rect_size) < 2:
            return None

        img_x, img_y = int(rect_min[0]), int(rect_min[1])
        img_w, img_h = int(rect_size[0]), int(rect_size[1])
        if img_w <= 0 or img_h <= 0:
            return None
        return img_x, img_y, img_w, img_h

    def _get_preview_mouse_point(self) -> Optional[tuple]:
        if not self.gui():
            return None
        rect = self._get_preview_item_rect()
        if rect is None:
            return None
        img_x, img_y, img_w, img_h = rect
        try:
            mouse_x, mouse_y = dpg.get_mouse_pos(local=False)
        except TypeError:
            mouse_x, mouse_y = dpg.get_mouse_pos()
        if mouse_x < img_x or mouse_y < img_y or mouse_x >= img_x + img_w or mouse_y >= img_y + img_h:
            return None
        frame_w, frame_h = self.state.source_size
        frame_x = int((mouse_x - img_x) * frame_w / img_w)
        frame_y = int((mouse_y - img_y) * frame_h / img_h)
        frame_x = max(0, min(frame_w - 1, frame_x))
        frame_y = max(0, min(frame_h - 1, frame_y))
        return frame_x, frame_y, frame_w, frame_h

    # ------------------------------------------------------------------
    # ROI drag interaction
    # ------------------------------------------------------------------
    def _classify_roi_drag_mode(self, frame_x: int, frame_y: int, frame_w: int, frame_h: int) -> str:
        roi_x, roi_y, roi_w, roi_h = self.state.effective_roi(frame_w, frame_h)
        roi_x2 = roi_x + roi_w
        roi_y2 = roi_y + roi_h
        edge_margin = max(6, int(min(frame_w, frame_h) * 0.01))

        near_left = abs(frame_x - roi_x) <= edge_margin
        near_right = abs(frame_x - roi_x2) <= edge_margin
        near_top = abs(frame_y - roi_y) <= edge_margin
        near_bottom = abs(frame_y - roi_y2) <= edge_margin
        inside = roi_x <= frame_x <= roi_x2 and roi_y <= frame_y <= roi_y2

        if near_left and near_top:
            return "resize_tl"
        if near_right and near_top:
            return "resize_tr"
        if near_left and near_bottom:
            return "resize_bl"
        if near_right and near_bottom:
            return "resize_br"
        if near_left and inside:
            return "resize_l"
        if near_right and inside:
            return "resize_r"
        if near_top and inside:
            return "resize_t"
        if near_bottom and inside:
            return "resize_b"
        if inside:
            return "move"
        return "new"

    def _apply_roi_drag(self, frame_x: int, frame_y: int, frame_w: int, frame_h: int):
        if self._roi_drag_origin is None or self._roi_drag_start_rect is None or self._roi_drag_mode is None:
            return

        start_x, start_y = self._roi_drag_origin
        roi_x, roi_y, roi_w, roi_h = self._roi_drag_start_rect
        roi_x2 = roi_x + roi_w
        roi_y2 = roi_y + roi_h
        dx = frame_x - start_x
        dy = frame_y - start_y
        min_size = 8

        if self._roi_drag_mode == "new":
            left = min(start_x, frame_x)
            top = min(start_y, frame_y)
            right = max(start_x, frame_x)
            bottom = max(start_y, frame_y)
        elif self._roi_drag_mode == "move":
            left = roi_x + dx
            top = roi_y + dy
            left = max(0, min(left, frame_w - roi_w))
            top = max(0, min(top, frame_h - roi_h))
            right = left + roi_w
            bottom = top + roi_h
        else:
            left = roi_x
            top = roi_y
            right = roi_x2
            bottom = roi_y2
            resize_mode = self._roi_drag_mode.replace("resize_", "")
            if "l" in resize_mode:
                left = min(frame_x, right - min_size)
            if "r" in resize_mode:
                right = max(frame_x, left + min_size)
            if "t" in resize_mode:
                top = min(frame_y, bottom - min_size)
            if "b" in resize_mode:
                bottom = max(frame_y, top + min_size)

        left = max(0, min(left, frame_w - 1))
        top = max(0, min(top, frame_h - 1))
        right = max(left + 1, min(right, frame_w))
        bottom = max(top + 1, min(bottom, frame_h))

        self._set_roi_rect(
            left,
            top,
            right - left,
            bottom - top,
            frame_w=frame_w,
            frame_h=frame_h,
            sync_ui=True,
            request_reprocess=False,
        )

    def _update_roi_drag_from_mouse(self):
        if not self._roi_drag_active:
            return
        point = self._get_preview_mouse_point()
        if point is None:
            return
        frame_x, frame_y, frame_w, frame_h = point
        self._apply_roi_drag(frame_x, frame_y, frame_w, frame_h)

    def _poll_roi_mouse_interaction(self):
        if not self.roi_edit_mode or not self.settings.roi_enabled:
            self._roi_mouse_was_down = False
            return

        try:
            is_down = dpg.is_mouse_button_down(dpg.mvMouseButton_Left)
        except Exception:
            return

        if is_down and not self._roi_mouse_was_down:
            self._handle_roi_mouse_down(app_data=dpg.mvMouseButton_Left)
        elif is_down and self._roi_mouse_was_down:
            self._update_roi_drag_from_mouse()
        elif (not is_down) and self._roi_mouse_was_down:
            self._handle_roi_mouse_up(app_data=dpg.mvMouseButton_Left)

        self._roi_mouse_was_down = is_down

    # ------------------------------------------------------------------
    # Preview compose / overlays
    # ------------------------------------------------------------------
    def _draw_roi_mask(self, frame: np.ndarray, source_w: int, source_h: int):
        if not self.settings.roi_enabled:
            return
        frame_h, frame_w = frame.shape[:2]
        x, y, w, h = self.state.effective_roi(source_w, source_h)
        x = int(round(x * frame_w / max(source_w, 1)))
        y = int(round(y * frame_h / max(source_h, 1)))
        w = max(1, int(round(w * frame_w / max(source_w, 1))))
        h = max(1, int(round(h * frame_h / max(source_h, 1))))
        border_color = (80, 220, 120) if self.roi_edit_mode else (100, 180, 240)
        cv2.rectangle(frame, (x, y), (x + w, y + h), border_color, 2)
        if self.roi_edit_mode:
            handle = max(4, min(10, int(min(frame_w, frame_h) * 0.01)))
            for hx, hy in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
                cv2.rectangle(frame, (hx - handle, hy - handle), (hx + handle, hy + handle), border_color, -1)

    def _compose_roi_preview(self, preview_frame: Optional[np.ndarray], source_w: int, source_h: int) -> Optional[np.ndarray]:
        if preview_frame is None or source_w <= 0 or source_h <= 0:
            return None

        x, y, w, h = self.state.effective_roi(source_w, source_h)
        roi_frame = preview_frame
        if preview_frame.shape[1] == source_w and preview_frame.shape[0] == source_h:
            roi_frame = preview_frame[y:y + h, x:x + w]

        if roi_frame.size == 0:
            return None

        if roi_frame.shape[1] != w or roi_frame.shape[0] != h:
            roi_frame = cv2.resize(roi_frame, (w, h))

        canvas = np.zeros((source_h, source_w, 3), dtype=roi_frame.dtype)
        canvas[y:y + h, x:x + w] = roi_frame
        return canvas

    def _draw_roi_note(self, frame: np.ndarray, source_w: int, source_h: int):
        if not self.settings.roi_enabled:
            return

        _, _, roi_w_src, roi_h_src = self.state.effective_roi(source_w, source_h)
        note_lines = [f"ROI {roi_w_src}x{roi_h_src} | imgsz {self.settings.imgsz}"]
        roi_info = self._get_recommended_imgsz_for_roi()
        if roi_info is not None:
            low, high, _, _ = roi_info
            if low == high:
                note_lines.append(f"Suggested: {low}")
            else:
                note_lines.append(f"Suggested: {low}-{high}")

        frame_h, frame_w = frame.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = max(0.45, min(frame_w, frame_h) / 1400.0)
        thickness = 1
        line_height = max(18, int(24 * font_scale))
        box_width = 0
        for line in note_lines:
            (text_width, _), _ = cv2.getTextSize(line, font, font_scale, thickness)
            box_width = max(box_width, text_width)
        box_height = 12 + line_height * len(note_lines)
        cv2.rectangle(frame, (8, 8), (20 + box_width, 8 + box_height), (0, 0, 0), -1)
        text_y = 8 + line_height
        for idx, line in enumerate(note_lines):
            color = (0, 140, 255) if idx > 0 else (220, 220, 220)
            cv2.putText(frame, line, (12, text_y), font, font_scale, color, thickness, cv2.LINE_AA)
            text_y += line_height

    # ------------------------------------------------------------------
    # ROI mouse handlers (dpg handler_registry callbacks)
    # ------------------------------------------------------------------
    def _handle_roi_mouse_down(self, sender=None, app_data=None):
        if not self.roi_edit_mode or not self.settings.roi_enabled:
            return
        if app_data != dpg.mvMouseButton_Left:
            return
        point = self._get_preview_mouse_point()
        if point is None:
            return
        frame_x, frame_y, frame_w, frame_h = point
        self._roi_drag_active = True
        self._roi_drag_origin = (frame_x, frame_y)
        self._roi_drag_start_rect = self.state.effective_roi(frame_w, frame_h)
        self._roi_drag_mode = self._classify_roi_drag_mode(frame_x, frame_y, frame_w, frame_h)
        self._clamp_roi_to_source(frame_w, frame_h, sync_ui=False)

    def _handle_roi_mouse_move(self, sender=None, app_data=None):
        if not self._roi_drag_active:
            return
        point = self._get_preview_mouse_point()
        if point is None:
            return
        frame_x, frame_y, frame_w, frame_h = point
        self._apply_roi_drag(frame_x, frame_y, frame_w, frame_h)

    def _handle_roi_mouse_up(self, sender=None, app_data=None):
        if app_data != dpg.mvMouseButton_Left:
            return
        if self._roi_drag_active:
            self._roi_drag_active = False
            self._roi_drag_mode = None
            self._roi_drag_origin = None
            self._roi_drag_start_rect = None
            self.request_reprocess()

    # ------------------------------------------------------------------
    # Exclusion-mask manual editor (ROADMAP §4.2 Phase 2 ④)
    # ------------------------------------------------------------------
    def _mask_space_rect(self, frame_w: int, frame_h: int) -> tuple:
        """The source-frame rect the exclusion grid is normalized over.

        The mask lives in the motion model's input space: the ROI crop when
        ROI is enabled, else the full frame (mirrors the pipeline's
        ``_exclusion_norm_xy`` ROI-local normalization).
        """
        if self.settings.roi_enabled:
            return self.state.effective_roi(frame_w, frame_h)
        return 0, 0, frame_w, frame_h

    def _mask_norm_point(self, frame_x: int, frame_y: int,
                         frame_w: int, frame_h: int) -> Optional[tuple]:
        """Map a source-frame point into the mask's normalized [0,1) space."""
        rx, ry, rw, rh = self._mask_space_rect(frame_w, frame_h)
        if rw <= 0 or rh <= 0:
            return None
        nx = (frame_x - rx) / rw
        ny = (frame_y - ry) / rh
        if not (0.0 <= nx < 1.0 and 0.0 <= ny < 1.0):
            return None
        return nx, ny

    def _handle_mask_mouse_down(self, sender=None, app_data=None):
        if not self.mask_edit_mode or self._mask_paint_active:
            return
        if app_data != dpg.mvMouseButton_Left:
            return
        point = self._get_preview_mouse_point()
        if point is None:
            return
        nxy = self._mask_norm_point(*point)
        if nxy is None:
            return
        # The pressed cell's flip decides the paint value for the whole drag
        # (classic paint semantics: press on a clear cell → masking drag).
        result = self.processor.toggle_exclusion_cell(*nxy)
        if result is None:
            return
        col, row, state = result
        self._mask_paint_active = True
        self._mask_paint_value = state
        self._mask_painted_cells = {(col, row)}
        self._sync_mask_ui()

    def _handle_mask_mouse_move(self, sender=None, app_data=None):
        if not self._mask_paint_active:
            return
        point = self._get_preview_mouse_point()
        if point is None:
            return
        nxy = self._mask_norm_point(*point)
        if nxy is None:
            return
        cell = self.processor.paint_exclusion_cell(*nxy, self._mask_paint_value)
        if cell is not None and cell not in self._mask_painted_cells:
            self._mask_painted_cells.add(cell)
            self._sync_mask_ui()

    def _handle_mask_mouse_up(self, sender=None, app_data=None):
        if app_data != dpg.mvMouseButton_Left:
            return
        if self._mask_paint_active:
            self._mask_paint_active = False
            verb = "masked" if self._mask_paint_value else "unmasked"
            print(f"[Mask] {verb} {len(self._mask_painted_cells)} cell(s) "
                  f"manually")
            self._mask_paint_value = None
            self._mask_painted_cells = set()
            self.request_reprocess()

    def _poll_mask_mouse_interaction(self):
        """Mirror of _poll_roi_mouse_interaction for the mask editor."""
        if not self.mask_edit_mode:
            self._mask_mouse_was_down = False
            return
        try:
            is_down = dpg.is_mouse_button_down(dpg.mvMouseButton_Left)
        except Exception:
            return
        if is_down and not self._mask_mouse_was_down:
            self._handle_mask_mouse_down(app_data=dpg.mvMouseButton_Left)
        elif is_down and self._mask_mouse_was_down:
            self._handle_mask_mouse_move(app_data=dpg.mvMouseButton_Left)
        elif (not is_down) and self._mask_mouse_was_down:
            self._handle_mask_mouse_up(app_data=dpg.mvMouseButton_Left)
        self._mask_mouse_was_down = is_down

    def _draw_exclusion_overlay(self, frame: np.ndarray, source_w: int, source_h: int):
        """Grid + cell overlay on the preview while the mask editor is active."""
        if not self.mask_edit_mode:
            return
        grid, auto, manual_add, manual_remove = self.processor.get_exclusion_state()
        gx, gy = grid
        if gx <= 0 or gy <= 0:
            return
        frame_h, frame_w = frame.shape[:2]
        rx, ry, rw, rh = self._mask_space_rect(source_w, source_h)
        # Scale the mask-space rect into preview-frame coordinates.
        sx = frame_w / max(source_w, 1)
        sy = frame_h / max(source_h, 1)
        rx, ry = rx * sx, ry * sy
        rw, rh = rw * sx, rh * sy

        def cell_rect(col: int, row: int) -> tuple:
            x0 = int(round(rx + col / gx * rw))
            y0 = int(round(ry + row / gy * rh))
            x1 = int(round(rx + (col + 1) / gx * rw))
            y1 = int(round(ry + (row + 1) / gy * rh))
            return x0, y0, x1, y1

        effective = (set(map(tuple, auto)) | set(map(tuple, manual_add))) \
            - set(map(tuple, manual_remove))
        overlay = frame.copy()
        for col, row in effective:
            x0, y0, x1, y1 = cell_rect(col, row)
            color = (60, 60, 230) if (col, row) in set(map(tuple, manual_add)) \
                else (40, 40, 180)
            cv2.rectangle(overlay, (x0, y0), (x1, y1), color, -1)
        cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, dst=frame)
        # Manually unmasked auto cells: outline only (auto wanted them, the
        # operator vetoed) so the veto stays visible and re-clickable.
        for col, row in set(map(tuple, manual_remove)) & set(map(tuple, auto)):
            x0, y0, x1, y1 = cell_rect(col, row)
            cv2.rectangle(frame, (x0, y0), (x1, y1), (140, 140, 140), 1)
        # Grid lines (thin) + status note.
        grid_color = (90, 90, 90)
        for col in range(gx + 1):
            x = int(round(rx + col / gx * rw))
            cv2.line(frame, (x, int(ry)), (x, int(ry + rh)), grid_color, 1)
        for row in range(gy + 1):
            y = int(round(ry + row / gy * rh))
            cv2.line(frame, (int(rx), y), (int(rx + rw), y), grid_color, 1)
        cv2.putText(frame, "MASK EDIT: click/drag cells to mask (red) / unmask",
                    (int(rx) + 8, int(ry) + 22), cv2.FONT_HERSHEY_SIMPLEX,
                    max(0.45, min(frame_w, frame_h) / 1400.0),
                    (80, 220, 120), 1, cv2.LINE_AA)

    def _cb_mask_edit_toggle(self):
        """GUI button: toggle the exclusion-mask manual editor."""
        self.mask_edit_mode = not self.mask_edit_mode
        if self.mask_edit_mode and self.roi_edit_mode:
            self._cb_roi_edit_toggle(False)  # one paint mode at a time
        self._mask_paint_active = False
        self._mask_paint_value = None
        self._mask_painted_cells = set()
        gui = self.gui()
        if gui:
            gui.set_mask_edit_state(self.mask_edit_mode)
            message = ("Mask edit: click/drag preview cells"
                       if self.mask_edit_mode else "Mask edit: off")
            gui.show_toast(message, duration=2.5, color=(160, 200, 255))
        self._sync_mask_ui()

    def _cb_mask_clear(self):
        """GUI button: drop the whole mask (auto cells + manual overlays)."""
        self.processor.clear_exclusion()
        self._sync_mask_ui()
        self.request_reprocess()
        gui = self.gui()
        if gui:
            gui.show_toast("Exclusion mask cleared (auto + manual)",
                           duration=2.5, color=(255, 180, 80))
        print("[Mask] cleared (auto + manual)")

    def _sync_mask_ui(self):
        """Push the current mask cell counts to the GUI label."""
        gui = self.gui()
        if not gui:
            return
        _grid, auto, manual_add, manual_remove = self.processor.get_exclusion_state()
        effective = (set(map(tuple, auto)) | set(map(tuple, manual_add))) \
            - set(map(tuple, manual_remove))
        gui.update_exclusion_mask_text(
            len(effective), len(auto), len(manual_add), len(manual_remove))

    # ------------------------------------------------------------------
    # ROI GUI callbacks
    # ------------------------------------------------------------------
    def _cb_roi_toggle(self, enabled: bool):
        self.settings.roi_enabled = bool(enabled)
        if self.settings.roi_enabled and (self.settings.roi_w <= 0 or self.settings.roi_h <= 0):
            frame_w, frame_h = self.state.source_size
            self._set_roi_rect(0, 0, frame_w, frame_h, sync_ui=False, request_reprocess=False)
        if not self.settings.roi_enabled:
            self.roi_edit_mode = False
        self._sync_roi_ui()
        print(f"ROI: {'ON' if self.settings.roi_enabled else 'OFF'}")
        self.request_reprocess()

    def _cb_roi_edit_toggle(self, enabled: bool):
        if enabled and not self.settings.roi_enabled:
            self.settings.roi_enabled = True
        if enabled and self.mask_edit_mode:
            self._cb_mask_edit_toggle()  # one paint mode at a time
        self.roi_edit_mode = bool(enabled) and self.settings.roi_enabled
        if not self.roi_edit_mode:
            self._roi_drag_active = False
            self._roi_drag_mode = None
            self._roi_drag_origin = None
            self._roi_drag_start_rect = None
        self._sync_roi_ui()
        gui = self.gui()
        if gui:
            message = "ROI edit mode: drag on preview" if self.roi_edit_mode else "ROI edit mode: off"
            gui.show_toast(message, duration=2.0, color=(120, 200, 255))

    def _handle_preview_double_click(self, sender=None, app_data=None):
        """Double-click on the preview toggles ROI edit mode."""
        if app_data != dpg.mvMouseButton_Left:
            return
        if self.mask_edit_mode:
            return  # mask editor owns preview clicks
        gui = self.gui()
        if gui and gui.project_picker_visible():
            return
        if self._get_preview_mouse_point() is None:
            return
        self._cb_roi_edit_toggle(not self.roi_edit_mode)

    def _cb_roi_reset(self):
        frame_w, frame_h = self.state.source_size
        self._set_roi_rect(0, 0, frame_w, frame_h)
        print("ROI reset to full frame")
