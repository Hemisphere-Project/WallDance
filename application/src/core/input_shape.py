"""The network's input shape: a square ``imgsz`` or a rectangle ``height x width`` (Plan Cordée rev 2, P1).

A portrait ROI letterboxed into a square wastes the sides: the 08/10 façade ROI (965 x 1771) in a 1280 square
is 45 % padding, and a 110 px dancer reaches the network at 80 px. A rectangle of the ROI's own shape keeps
her at about 108 px at 1760 x 960 for the same pixel count as 1280².

A project opts in with the config key ``yolo_shape`` = ``"<height>x<width>"`` (multiples of 32, e.g.
``"1760x960"``). Absent or empty: the square ``yolo_imgsz``, unchanged. A rectangle runs only when its engine
``<model>_<height>x<width>.engine`` exists (or on PyTorch); otherwise the square runs and readiness says why.

Pure functions only: no torch, no cv2.
"""
from __future__ import annotations

from typing import Iterable, Optional, Tuple

STRIDE = 32
# the rectangles built by extra/build_engines (height, width): the façade's portrait ROI
RECT_PRESETS: Tuple[Tuple[int, int], ...] = ((1760, 960), (1920, 1056))

Shape = Tuple[int, int]


def parse_shape(value) -> Optional[Shape]:
    """``"1760x960"`` / ``[1760, 960]`` / ``(1760, 960)`` -> (1760, 960); empty or None -> None.
    Raises ValueError for anything else (not two positive multiples of 32)."""
    if value is None or value == "" or value == [] or value == ():
        return None
    if isinstance(value, str):
        parts = value.lower().replace("×", "x").replace(" ", "").split("x")
    else:
        parts = list(value)
    if len(parts) != 2:
        raise ValueError(f"yolo_shape {value!r}: expected '<height>x<width>'")
    try:
        h, w = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        raise ValueError(f"yolo_shape {value!r}: height and width must be integers") from None
    if h <= 0 or w <= 0 or h % STRIDE or w % STRIDE:
        raise ValueError(f"yolo_shape {value!r}: height and width must be positive multiples of {STRIDE}")
    return (h, w)


def shape_str(shape: Optional[Shape]) -> str:
    """(1760, 960) -> "1760x960"; None -> ""."""
    return f"{int(shape[0])}x{int(shape[1])}" if shape else ""


def engine_suffix(imgsz: int, shape: Optional[Shape] = None) -> str:
    """The size part of an engine file name: ``1280`` for the square, ``1760x960`` for a rectangle."""
    return shape_str(shape) if shape else str(int(imgsz))


def target_hw(imgsz: int, shape: Optional[Shape] = None) -> Shape:
    """The tensor's (height, width)."""
    return (int(shape[0]), int(shape[1])) if shape else (int(imgsz), int(imgsz))


def letterbox_scale(src_w: int, src_h: int, imgsz: int, shape: Optional[Shape] = None) -> float:
    th, tw = target_hw(imgsz, shape)
    return min(tw / float(src_w), th / float(src_h))


def net_height(person_h_px: float, roi_w: int, roi_h: int, imgsz: int, shape: Optional[Shape] = None) -> float:
    """A dancer of ``person_h_px`` (ROI pixels) as the network sees her."""
    if roi_w <= 0 or roi_h <= 0:
        return 0.0
    return float(person_h_px) * letterbox_scale(roi_w, roi_h, imgsz, shape)


def padding_share(roi_w: int, roi_h: int, imgsz: int, shape: Optional[Shape] = None) -> float:
    """Share of the network input that is letterbox padding."""
    if roi_w <= 0 or roi_h <= 0:
        return 0.0
    th, tw = target_hw(imgsz, shape)
    s = letterbox_scale(roi_w, roi_h, imgsz, shape)
    return 1.0 - (int(roi_w * s) * int(roi_h * s)) / float(th * tw)


def pick_shape(roi_w: int, roi_h: int, available: Iterable[Shape] = RECT_PRESETS,
               min_gain: float = 1.15) -> Optional[Shape]:
    """The rectangle that shows a dancer of this ROI largest per input pixel, if it beats the square of the
    same pixel count by ``min_gain``; None when the ROI is nearly square (the square stays)."""
    best, best_gain = None, 0.0
    for h, w in available:
        sq = round((h * w) ** 0.5)
        gain = letterbox_scale(roi_w, roi_h, 0, (h, w)) / letterbox_scale(roi_w, roi_h, sq)
        if gain > best_gain:
            best, best_gain = (h, w), gain
    return best if best is not None and best_gain >= min_gain else None
