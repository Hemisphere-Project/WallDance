"""Input transform: mirror / rotate the camera image at the source (REQ-5).

One ``InputTransform`` is applied wherever a frame *enters* the app -- the IDS
camera (mono8, before the GPU upload and the cached CPU preview frame), the
OpenCV camera, and slot playback -- so everything downstream (ROI, exclusion
mask, enhancement, YOLO, calibration, OSC coordinates) lives in the
transformed space.

Convention: rotate **clockwise** by ``rotation`` degrees first, then (if
``mirror``) flip left-right **in the rotated image**. "Mirror" therefore
always means "swap left and right of what the operator sees", whatever the
rotation.

The 8 combinations form the dihedral group D4; ``inverse`` and ``then`` give
the algebra needed to carry the ROI rect / exclusion mask across a change of
transform (``delta = new.after(old.inverse())``).

Identity is free: ``apply`` / ``apply_tensor`` return the *same object* (no
copy), so a disabled transform leaves the hot path byte-identical.

Recordings store RAW sensor frames (the camera callback is upstream of the
transform); the transform active at REC is written into the take's ``.meta``
(``input_transform``) for provenance. Playback applies the project's
*current* transform, exactly like the live camera, so ROI / mask /
calibration always match what is on screen. Offline tools that decode slot
files themselves should do the same: ``InputTransform.from_config(cfg)``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np

ROTATIONS = (0, 90, 180, 270)          # clockwise degrees
CONFIG_KEYS = ("input_mirror", "input_rotation")

# Single-call cv2 ops per (mirror, rotation). (True, 270) needs two.
_ROTATE_CODE = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def normalize_rotation(value) -> Optional[int]:
    """Map any multiple of 90 (incl. negatives / strings) onto ROTATIONS;
    None when the value is not a whole quarter turn."""
    try:
        deg = float(value)
    except (TypeError, ValueError):
        return None
    if not deg.is_integer() or int(deg) % 90 != 0:
        return None
    return int(deg) % 360


@dataclass(frozen=True)
class InputTransform:
    mirror: bool = False
    rotation: int = 0          # one of ROTATIONS (clockwise)

    def __post_init__(self):
        rot = normalize_rotation(self.rotation)
        if rot is None:
            raise ValueError(f"rotation must be a multiple of 90, got {self.rotation!r}")
        object.__setattr__(self, "rotation", rot)
        object.__setattr__(self, "mirror", bool(self.mirror))

    # ------------------------------------------------------------------
    # Identity / config / labels
    # ------------------------------------------------------------------
    @property
    def is_identity(self) -> bool:
        return not self.mirror and self.rotation == 0

    @property
    def swaps_axes(self) -> bool:
        return self.rotation in (90, 270)

    @classmethod
    def from_config(cls, config: Optional[Dict]) -> "InputTransform":
        """Read the shared config keys (absent / invalid = identity)."""
        config = config or {}
        rot = normalize_rotation(config.get("input_rotation", 0))
        mirror = config.get("input_mirror", False)
        if isinstance(mirror, str):
            mirror = mirror.strip().lower() in ("1", "true", "yes", "on")
        return cls(bool(mirror), rot or 0)

    def to_config(self) -> Dict:
        return {"input_mirror": self.mirror, "input_rotation": self.rotation}

    def to_meta(self) -> Dict:
        """``.meta`` provenance block (frames on disk are raw)."""
        return {"mirror": self.mirror, "rotation": self.rotation,
                "applied_to_frames": False}

    def label(self) -> str:
        parts = []
        if self.rotation:
            parts.append(f"rotate {self.rotation}°")
        if self.mirror:
            parts.append("mirror")
        return " + ".join(parts) if parts else "none"

    # ------------------------------------------------------------------
    # Group algebra
    # ------------------------------------------------------------------
    def inverse(self) -> "InputTransform":
        # Reflections are involutions; a pure rotation inverts its angle.
        if self.mirror:
            return self
        return InputTransform(False, (-self.rotation) % 360)

    def then(self, other: "InputTransform") -> "InputTransform":
        """Composition: apply ``self`` first, then ``other``.

        With R(a)M = M R(-a):  M^mb R(b) M^ma R(a) =
        M^(ma xor mb) R(a + b)   if not ma,   M^(ma xor mb) R(a - b)   if ma.
        """
        rot = (self.rotation - other.rotation) if self.mirror else \
              (self.rotation + other.rotation)
        return InputTransform(self.mirror != other.mirror, rot % 360)

    def after(self, other: "InputTransform") -> "InputTransform":
        """Composition: apply ``other`` first, then ``self``."""
        return other.then(self)

    # ------------------------------------------------------------------
    # Sizes
    # ------------------------------------------------------------------
    def output_size(self, width: int, height: int) -> Tuple[int, int]:
        """(w, h) of the transformed image for a (w, h) input."""
        return (height, width) if self.swaps_axes else (width, height)

    def input_size(self, width: int, height: int) -> Tuple[int, int]:
        """(w, h) of the raw input that produces a (w, h) output."""
        return (height, width) if self.swaps_axes else (width, height)

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------
    def apply(self, frame):
        """Transform an (H, W) or (H, W, C) image. Identity returns ``frame``
        itself (no copy); otherwise a new contiguous array."""
        if frame is None or self.is_identity:
            return frame
        rot, mir = self.rotation, self.mirror
        if not mir:
            return cv2.rotate(frame, _ROTATE_CODE[rot])
        if rot == 0:
            return cv2.flip(frame, 1)
        if rot == 90:
            return cv2.transpose(frame)            # rot90 CW + h-flip
        if rot == 180:
            return cv2.flip(frame, 0)              # rot180 + h-flip = v-flip
        return cv2.flip(cv2.rotate(frame, _ROTATE_CODE[270]), 1)

    def apply_copy(self, frame):
        """Like ``apply`` but always returns an array the caller owns (the
        identity path copies, the others already produce a new array)."""
        if frame is None:
            return None
        return frame.copy() if self.is_identity else self.apply(frame)

    def apply_tensor(self, tensor):
        """Transform a torch (N, C, H, W) tensor on its device. Identity
        returns ``tensor`` itself."""
        if tensor is None or self.is_identity:
            return tensor
        import torch
        out = tensor
        if self.rotation:
            # torch.rot90 with k>0 turns from dim 2 (rows) towards dim 3 (cols),
            # i.e. counter-clockwise on screen; clockwise is k = -rotation/90.
            out = torch.rot90(out, k=-(self.rotation // 90), dims=(2, 3))
        if self.mirror:
            out = torch.flip(out, dims=(3,))
        return out.contiguous()

    # ------------------------------------------------------------------
    # Coordinates (continuous, pixel-edge convention: x in [0, W])
    # ------------------------------------------------------------------
    def map_point(self, x: float, y: float, width: int, height: int) -> Tuple[float, float]:
        """Input-space point -> output-space point, for a (width, height) input."""
        rot = self.rotation
        if rot == 90:
            x, y = height - y, x
        elif rot == 180:
            x, y = width - x, height - y
        elif rot == 270:
            x, y = y, width - x
        if self.mirror:
            out_w, _ = self.output_size(width, height)
            x = out_w - x
        return x, y

    def map_rect(self, x: float, y: float, w: float, h: float,
                 width: int, height: int) -> Tuple[int, int, int, int]:
        """Axis-aligned input rect -> output rect (x, y, w, h), integers."""
        x0, y0 = self.map_point(x, y, width, height)
        x1, y1 = self.map_point(x + w, y + h, width, height)
        nx, ny = min(x0, x1), min(y0, y1)
        return (int(round(nx)), int(round(ny)),
                int(round(abs(x1 - x0))), int(round(abs(y1 - y0))))

    def map_grid(self, cols: int, rows: int) -> Tuple[int, int]:
        """(cols, rows) of a cell grid laid over the transformed image."""
        return self.output_size(cols, rows)

    def map_cell(self, col: int, row: int, cols: int, rows: int) -> Tuple[int, int]:
        """Grid cell (col, row) of a cols x rows grid -> its cell in the
        transformed grid (same as cv2 moving that pixel)."""
        cx, cy = self.map_point(col + 0.5, row + 0.5, cols, rows)
        return int(cx), int(cy)

    def map_cells(self, cells: Iterable, cols: int, rows: int) -> List[List[int]]:
        return sorted([list(self.map_cell(int(c[0]), int(c[1]), cols, rows))
                       for c in (cells or ())])


IDENTITY = InputTransform()


def remap_exclusion_bundle(bundle: Dict, delta: InputTransform) -> Dict:
    """Carry a persisted exclusion mask (``exclusion_grid`` + cell lists)
    across a transform change. The mask is normalized over the ROI-local
    MOG2 frame; the ROI rect moves with the same axis-aligned transform, so
    the cells follow the grid-level mapping. Returns a new dict."""
    out = dict(bundle or {})
    grid = out.get("exclusion_grid")
    if delta.is_identity or not grid:
        return out
    cols, rows = int(grid[0]), int(grid[1])
    out["exclusion_grid"] = list(delta.map_grid(cols, rows))
    for key in ("exclusion_cells", "exclusion_manual_add", "exclusion_manual_remove"):
        if out.get(key) is not None:
            out[key] = delta.map_cells(out[key], cols, rows)
    return out
