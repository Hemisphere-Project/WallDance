"""
Visualization helpers for WallDance.
Draws skeleton, keypoints, bounding boxes, and trails on video frames.
"""

import cv2
import numpy as np
from core.config import SKELETON, DANCER_COLORS, KEYPOINT_CONFIDENCE


def scaled_roi_rect(x: int, y: int, w: int, h: int, src_w: int, src_h: int,
                    out_w: int, out_h: int) -> tuple:
    """ROI rect (source px) -> (x0, y0, x1, y1) in a out_w x out_h preview of
    the whole source.  Shared by the GPU preview (sizes the ROI download) and
    the CPU compose (pastes it), so both agree to the pixel."""
    sx = out_w / src_w if src_w > 0 else 1.0
    sy = out_h / src_h if src_h > 0 else 1.0
    x0, y0 = int(round(x * sx)), int(round(y * sy))
    x1, y1 = int(round((x + w) * sx)), int(round((y + h) * sy))
    x0, y0 = min(max(x0, 0), out_w), min(max(y0, 0), out_h)
    x1, y1 = min(max(x1, x0), out_w), min(max(y1, y0), out_h)
    return x0, y0, x1, y1


def get_dancer_color(track_id):
    """Get consistent color for dancer ID."""
    return DANCER_COLORS[(track_id - 1) % len(DANCER_COLORS)]


# Identity-slot state colours (BGR) for the preview (CONT-6).
SLOT_STATE_COLORS = {
    "live": (80, 220, 80),       # green: bound track updated this frame
    "belt": (255, 200, 0),       # cyan: held by the IR belt
    "coasting": (0, 165, 255),   # orange: holding / predicting, no measurement
    "fg": (235, 235, 235),       # white: held by the empty-wall difference (clean-plate foreground)
    "weak": (200, 120, 255),     # pink: coasting slot re-found on weak evidence (option, off by default)
}


# Output ball (2026-10-06): the emitted centroid drawn as a ball so the operator previews
# exactly what TouchDesigner receives (OSC /centroid = smoothed_centroid).  Centre = a solid
# colour picked by the slot id (the dancer palette); only the slim border takes the
# slot-state colour (live / belt / coasting / weak).  A 1 px dark line separates the two,
# so the border stays readable when both colours match (D1 green on a live green border).
OUTPUT_BALL_R_FRAC = 0.0175    # radius as a fraction of the preview height (~13 px at 720 p)
OUTPUT_BALL_R_MIN = 6          # px
OUTPUT_BALL_BORDER_FRAC = 0.15 # border width as a fraction of the radius (2 px at 720 p)


# Ball size follows the dancer's distance (2026-10-07): radius x (body length / reference),
# body length = head-to-ankle distance on frames with a fresh, confident full skeleton
# (orientation-free: aerial and hanging poses too), smoothed, held between such frames.
# Reference = a mid-distance body, 20 % of the frame height (the base radius); clamped to
# 0.5x (a 25 m wall body, ~10 % of the frame) .. 2x (a body near the lens).  Visual only.
BALL_REF_BODY_FRAC = 0.20
BALL_SCALE_MIN, BALL_SCALE_MAX = 0.5, 2.0
BALL_KPT_CONF = 0.5            # head and ankle keypoints this confident = a full skeleton
BALL_SIZE_TAU_S = 0.7          # smoothing time constant of the body length
BALL_FORGET_S = 3.0            # a slot not drawn this long restarts at the base size
_HEAD_KPTS = (0, 1, 2, 3, 4)   # COCO nose, eyes, ears
_ANKLE_KPTS = (15, 16)


def certain_body_length(track) -> float | None:
    """Head-to-ankle length (track px) when the track carries a fresh, confident full
    skeleton this frame (a live slot fed by YOLO), else None."""
    if (getattr(track, "slot_state", None) or "live") != "live":
        return None
    if getattr(track, "frames_since_skeleton", None) != 0:
        return None
    kp, kc = getattr(track, "keypoints", None), getattr(track, "confidence", None)
    if kp is None or kc is None or len(kc) < 17:
        return None
    head = [i for i in _HEAD_KPTS if float(kc[i]) >= BALL_KPT_CONF]
    ankles = [i for i in _ANKLE_KPTS if float(kc[i]) >= BALL_KPT_CONF]
    if not head or not ankles:
        return None
    hx, hy = np.mean([kp[i] for i in head], axis=0)
    ax, ay = np.mean([kp[i] for i in ankles], axis=0)
    length = float(np.hypot(ax - hx, ay - hy))
    return length if np.isfinite(length) and length > 1.0 else None


class BallSizer:
    """Per-slot ball scale (0.5..2) from the smoothed certain body length."""

    def __init__(self):
        self._len: dict = {}      # slot id -> (smoothed length as a frame-height fraction, last seen t)

    def scale(self, slot_id: int, track, frame_h: float, t: float) -> float:
        prev = self._len.get(slot_id)
        if prev is not None and t - prev[1] > BALL_FORGET_S:
            prev = None
        length = certain_body_length(track)
        frac = prev[0] if prev is not None else None
        if length is not None and frame_h > 0:
            meas = length / float(frame_h)
            if frac is None:
                frac = meas
            else:
                dt = max(0.0, t - prev[1])
                a = 1.0 - np.exp(-dt / BALL_SIZE_TAU_S) if dt > 0 else 0.0
                frac = frac + a * (meas - frac)
        self._len[slot_id] = (frac, t)
        if frac is None:
            return 1.0
        return float(min(BALL_SCALE_MAX, max(BALL_SCALE_MIN, frac / BALL_REF_BODY_FRAC)))


def draw_output_ball(frame, cx: float, cy: float, border_color, fill_color,
                     scale: float = 1.0) -> None:
    """A solid ball at (cx, cy) in preview px: ``fill_color`` centre, slim border in
    ``border_color``, dark outline outside and between the two.  ``scale`` multiplies
    the base radius (``BallSizer``: the dancer's apparent size)."""
    fh = frame.shape[0]
    r = max(OUTPUT_BALL_R_MIN, int(round(fh * OUTPUT_BALL_R_FRAC * scale)))
    x, y = int(round(cx)), int(round(cy))
    border = max(2, int(round(r * OUTPUT_BALL_BORDER_FRAC)))
    cv2.circle(frame, (x, y), r + 1, (0, 0, 0), -1, cv2.LINE_AA)
    cv2.circle(frame, (x, y), r, border_color, -1, cv2.LINE_AA)
    cv2.circle(frame, (x, y), max(1, r - border), (0, 0, 0), -1, cv2.LINE_AA)
    cv2.circle(frame, (x, y), max(1, r - border - 1), fill_color, -1, cv2.LINE_AA)


def draw_slot(frame, track, sx: float = 1.0, sy: float = 1.0,
              thickness_scale: float = 1.0, show_ball: bool = True,
              ball_scale: float = 1.0):
    """One emitted identity slot (what OSC sends): its smoothed box in the state
    colour when not live, the OUTPUT BALL on the smoothed centroid (the exact OSC
    /centroid; centre = slot-id colour, border = state colour; ``ball_scale`` from
    ``BallSizer``), and ``D<id>`` (+ state when not live).  ``sx``/``sy`` map original
    px to the preview."""
    state = getattr(track, "slot_state", None) or "live"
    color = SLOT_STATE_COLORS.get(state, (200, 200, 200))
    scale = max(0.3, thickness_scale)
    x, y, w, h = (float(v) for v in track.bbox[:4])
    x, y, w, h = x * sx, y * sy, w * sx, h * sy
    c = track.smoothed_centroid
    cx, cy = (float(c[0]) * sx, float(c[1]) * sy) if c is not None else (x + w / 2, y + h / 2)
    t = max(1, int(round(scale)))
    if state != "live":   # the live box is the tracker's (drawn already)
        cv2.rectangle(frame, (int(x), int(y)), (int(x + w), int(y + h)), color, t)
    if show_ball:
        draw_output_ball(frame, cx, cy, color, get_dancer_color(int(track.track_id)),
                         scale=ball_scale)
    label = f"D{track.track_id}" + ("" if state == "live" else f" {state}")
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs = max(0.45, 0.8 * scale)
    ft = max(1, int(2 * scale))
    (tw, th), base = cv2.getTextSize(label, font, fs, ft)
    lx, ly = int(x + w) + 4, int(y) + th + 2
    cv2.rectangle(frame, (lx - 2, ly - th - 3), (lx + tw + 2, ly + base + 1), (0, 0, 0), -1)
    cv2.putText(frame, label, (lx, ly), font, fs, color, ft)


def draw_dancer(frame, track, show_skeleton=True, show_keypoints=True,
                show_bbox=True, show_trail=True, show_id=True, thickness_scale: float = 1.0,
                id_prefix: str = "D"):
    """Draw single dancer visualization.  ``id_prefix`` = ``T`` labels the
    tracker's internal ids when identity slots own the ``D`` labels."""
    # Normalize scale to avoid vanishing or oversized strokes when preview is scaled
    scale = max(0.3, thickness_scale)
    color = get_dancer_color(track.track_id)
    keypoints = track.keypoints
    confidence = track.confidence
    
    # Draw trail
    if show_trail and len(track.history) > 1:
        points = list(track.history)
        for i in range(1, len(points)):
            alpha = i / len(points)
            thickness = max(1, int(3 * alpha * scale))
            pt1 = tuple(map(int, points[i-1]))
            pt2 = tuple(map(int, points[i]))
            cv2.line(frame, pt1, pt2, color, thickness)
    
    # Draw bounding box
    # Draw bounding box
    is_bridged = getattr(track, 'is_bridged', False)
    if show_bbox:
        x, y, w, h = track.bbox
        bbox_thickness = max(1, int(2 * scale))
        cv2.rectangle(frame, (int(x), int(y)), (int(x+w), int(y+h)), color, bbox_thickness)
    
    # Draw skeleton (skip for bridged tracks — keypoints are frozen)
    if show_skeleton and not is_bridged:
        for start_idx, end_idx in SKELETON:
            if confidence[start_idx] > KEYPOINT_CONFIDENCE and confidence[end_idx] > KEYPOINT_CONFIDENCE:
                x1, y1 = keypoints[start_idx]
                x2, y2 = keypoints[end_idx]
                cv2.line(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, max(1, int(2 * scale)))
    
    # Draw keypoints (skip for bridged tracks — keypoints are frozen)
    if show_keypoints and not is_bridged:
        for i, (x, y) in enumerate(keypoints):
            if confidence[i] > KEYPOINT_CONFIDENCE:
                radius = max(2, int(4 * scale))
                outline_radius = max(radius + 1, int(5 * scale))
                cv2.circle(frame, (int(x), int(y)), radius, color, -1)
                cv2.circle(frame, (int(x), int(y)), outline_radius, (255, 255, 255), 1)
    
    # Draw ID label
    if show_id:
        bbox = track.bbox
        box_x, box_y = int(bbox[0]), int(bbox[1])

        label = f"{id_prefix}{track.track_id}" + ("[M]" if is_bridged else "")

        # Position label above the top edge of the bounding box
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = max(0.4, 0.7 * scale)
        font_thickness = max(1, int(2 * scale))
        (tw, th), baseline = cv2.getTextSize(label, font, font_scale, font_thickness)
        label_x = box_x
        label_y = box_y - max(4, int(6 * scale))  # just above top edge
        # Dark background for readability
        cv2.rectangle(frame,
                      (label_x - 1, label_y - th - 2),
                      (label_x + tw + 2, label_y + baseline + 1),
                      (0, 0, 0), -1)
        cv2.putText(frame, label, (label_x, label_y), font,
                    font_scale, color, font_thickness)
