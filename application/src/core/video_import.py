"""Import external videos into recording slots (REQ-1, TODO-me "+++ Charger des
vidéos dans les slots").

An imported file becomes the slot's **newest take**
(``recordings/slot_N_YYYYMMDD_HHMMSS.<ext>``); the slot's older takes stay
reachable through the Ctrl+click history. A ``.meta`` sidecar (``meta_version``
2) carries ``actual_fps`` / ``frames`` -- what playback and the scenario
fingerprints read -- plus ``imported_from`` provenance.

Containers (the safe choice; operator-facing summary in docs/REMOTE_OPS.md §6):

* ``.avi`` / ``.mp4`` are **byte-copied** (fast, lossless, extension kept).
* Anything else (``.mov``, ``.mkv``, ``.m4v``, ``.webm`` ...) is **transcoded**
  to MJPG ``.avi`` with OpenCV. The slot listing (``VideoRecorder``) and the
  offline tools (``tests/replay.py`` globs ``slot_N_*.avi`` / ``*.mp4``) only
  read .avi/.mp4, and OpenCV's built-in MJPG encoder exists in every build
  (no FFmpeg CLI on the show laptop), so a transcoded take plays everywhere a
  recorded one does. MJPG at high quality keeps dark IR footage clean; the
  cost is disk space (checked before and during the transcode).

Every source is probed first: if OpenCV cannot decode its first frame (exotic
codec), the import is refused with an operator-readable reason instead of
leaving an unplayable slot.

Work happens in a temp file whose name does NOT match the slot pattern, so a
half-copied take is never listed; the ``.meta`` is written before the final
rename, so playback never sees a take without its fps.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

import cv2

from core.config import IMPORT_TRANSCODE_CODEC, IMPORT_TRANSCODE_QUALITY
from core.version import app_version

# What the slot listing accepts (VideoRecorder.get_slot_info, tests/replay.py).
SLOT_VIDEO_EXTS = (".avi", ".mp4")
# What the import accepts (transcoded unless in SLOT_VIDEO_EXTS).
IMPORTABLE_EXTS = SLOT_VIDEO_EXTS + (
    ".mov", ".mkv", ".m4v", ".webm", ".wmv", ".mpg", ".mpeg", ".mts", ".m2ts",
    ".ts", ".flv", ".3gp", ".ogv", ".mxf",
)
IMPORT_MODES = ("auto", "copy", "transcode")
TEMP_PREFIX = "_importing_"          # never matches "slot_N_" -> never listed
COPY_CHUNK = 8 * 1024 * 1024
DISK_MARGIN = 512 * 1024 * 1024      # keep this much free after an import
TRANSCODE_PROBE_FRAMES = 50          # project the output size after N frames

ProgressFn = Callable[[float, str], None]


class VideoImportError(Exception):
    """An import that cannot (or must not) proceed; the message is shown to
    the operator as is."""


@dataclass
class ImportResult:
    dest: str
    mode: str                      # copy | transcode
    frames: int
    fps: float
    width: int
    height: int
    bytes: int
    meta: Dict = field(default_factory=dict)


def _fourcc_str(value: float) -> str:
    code = int(value or 0)
    text = "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))
    return text if text.isprintable() and text.strip() else ""


def _sane_fps(fps) -> Optional[float]:
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        return None
    return fps if 0.5 <= fps <= 1000.0 else None


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB"


def probe_video(path: str, count_frames: bool = False) -> Dict:
    """fps / frames / size / fourcc of a video as OpenCV sees it.

    ``decodable`` is False when the first frame cannot be read (unsupported
    codec). ``frames`` comes from the container header; with
    ``count_frames=True`` a missing/zero header count is replaced by decoding
    the whole file (slow on big files -- worker thread only)."""
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return {"decodable": False, "fps": None, "frames": 0,
                    "width": 0, "height": 0, "fourcc": ""}
        fps = _sane_fps(cap.get(cv2.CAP_PROP_FPS))
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fourcc = _fourcc_str(cap.get(cv2.CAP_PROP_FOURCC))
        ok, frame = cap.read()
        decodable = bool(ok and frame is not None)
        width = int(frame.shape[1]) if decodable else int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(frame.shape[0]) if decodable else int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if decodable and frames <= 0 and count_frames:
            frames = 1
            while cap.grab():
                frames += 1
        return {"decodable": decodable, "fps": fps, "frames": max(0, frames),
                "width": width, "height": height, "fourcc": fourcc}
    finally:
        cap.release()


def validate_source(path: str) -> str:
    """Absolute real path of an importable video file, or VideoImportError."""
    raw = str(path or "").strip().strip('"')
    if not raw:
        raise VideoImportError("no file given")
    real = os.path.realpath(os.path.expanduser(raw))
    if not os.path.exists(real):
        raise VideoImportError(f"file not found: {raw}")
    if not os.path.isfile(real):
        raise VideoImportError(f"not a file: {raw}")
    ext = os.path.splitext(real)[1].lower()
    if ext not in IMPORTABLE_EXTS:
        raise VideoImportError(
            f"unsupported file type '{ext or '(none)'}' -- expected one of "
            f"{', '.join(IMPORTABLE_EXTS)}")
    if os.path.getsize(real) <= 0:
        raise VideoImportError(f"file is empty: {raw}")
    return real


def resolve_mode(src: str, mode: str = "auto") -> str:
    """'copy' or 'transcode' for this source."""
    if mode not in IMPORT_MODES:
        raise VideoImportError(f"mode must be one of {IMPORT_MODES}, got {mode!r}")
    listable = os.path.splitext(src)[1].lower() in SLOT_VIDEO_EXTS
    if mode == "auto":
        return "copy" if listable else "transcode"
    if mode == "copy" and not listable:
        raise VideoImportError(
            f"cannot copy a {os.path.splitext(src)[1]} as is: slots (and the replay "
            "tools) only read .avi/.mp4 -- use mode 'auto' or 'transcode'")
    return mode


def _slot_stamp(name: str) -> Optional[datetime]:
    parts = os.path.splitext(name)[0].split("_")
    if len(parts) < 4:
        return None
    try:
        return datetime.strptime(f"{parts[2]}_{parts[3]}", "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def next_slot_path(recordings_dir: str, slot: int, ext: str,
                   now: Optional[datetime] = None) -> str:
    """``slot_N_<stamp><ext>`` that sorts as the slot's newest take.

    The stamp is ``now``; if a take already carries that second (or a later
    one -- clock skew between machines), it moves just past the newest, so
    the import is always index 0 of the newest-first history."""
    now = (now or datetime.now()).replace(microsecond=0)
    prefix = f"slot_{int(slot)}_"
    newest = None
    taken = set()
    if os.path.isdir(recordings_dir):
        for name in os.listdir(recordings_dir):
            if name.startswith(prefix):
                stamp = _slot_stamp(name)
                if stamp is not None:
                    taken.add(stamp)
                    newest = stamp if newest is None or stamp > newest else newest
    stamp = now
    if newest is not None and newest >= stamp:
        stamp = newest + timedelta(seconds=1)
    while stamp in taken:
        stamp += timedelta(seconds=1)
    return os.path.join(recordings_dir, f"{prefix}{stamp.strftime('%Y%m%d_%H%M%S')}{ext}")


def _read_source_meta(src: str) -> Optional[Dict]:
    """A WallDance take's own sidecar (cross-project import), if any."""
    try:
        with open(src + ".meta", "r", encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else None
    except (OSError, ValueError):
        return None


def cleanup_stale_temps(recordings_dir: str) -> int:
    """Remove leftovers of an import interrupted by a crash."""
    removed = 0
    if not os.path.isdir(recordings_dir):
        return 0
    for name in os.listdir(recordings_dir):
        if name.startswith(TEMP_PREFIX):
            try:
                os.remove(os.path.join(recordings_dir, name))
                removed += 1
            except OSError:
                pass
    return removed


def _check_space(recordings_dir: str, needed: int) -> None:
    free = shutil.disk_usage(recordings_dir).free
    if free < needed + DISK_MARGIN:
        raise VideoImportError(
            f"not enough disk space: needs ~{_gb(needed)}, {_gb(free)} free "
            f"(keeping {_gb(DISK_MARGIN)} spare)")


def _copy(src: str, tmp: str, total: int, progress: ProgressFn) -> int:
    done = 0
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        while True:
            chunk = fi.read(COPY_CHUNK)
            if not chunk:
                break
            fo.write(chunk)
            done += len(chunk)
            progress(done / total if total else 1.0, f"copied {_gb(done)} / {_gb(total)}")
        fo.flush()
        os.fsync(fo.fileno())
    return done


def _transcode(src: str, tmp: str, fps: float, total_frames: int,
               recordings_dir: str, progress: ProgressFn):
    cap = cv2.VideoCapture(src)
    writer = None
    size = None
    n = 0
    try:
        if not cap.isOpened():
            raise VideoImportError("OpenCV could not open the file for reading")
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if writer is None:
                size = (int(frame.shape[1]), int(frame.shape[0]))
                writer = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*IMPORT_TRANSCODE_CODEC),
                                         fps, size)
                if not writer.isOpened():
                    raise VideoImportError(
                        f"could not open the {IMPORT_TRANSCODE_CODEC} writer")
                if IMPORT_TRANSCODE_CODEC == "MJPG":
                    writer.set(cv2.VIDEOWRITER_PROP_QUALITY, IMPORT_TRANSCODE_QUALITY)
            if (frame.shape[1], frame.shape[0]) != size:
                frame = cv2.resize(frame, size)
            writer.write(frame)
            n += 1
            if n == TRANSCODE_PROBE_FRAMES and total_frames > n * 2:
                # Project the output size from what is on disk so far.
                per_frame = os.path.getsize(tmp) / n if os.path.exists(tmp) else 0
                if per_frame > 0:
                    _check_space(recordings_dir, int(per_frame * (total_frames - n)))
            if total_frames > 0:
                progress(min(1.0, n / total_frames), f"transcoded {n} / {total_frames} frames")
            elif n % 100 == 0:
                progress(0.0, f"transcoded {n} frames")
    finally:
        cap.release()
        if writer is not None:
            writer.release()
    if n == 0:
        raise VideoImportError("no frame could be decoded")
    return n, size


def import_video(src: str, recordings_dir: str, slot: int, mode: str = "auto",
                 progress: Optional[ProgressFn] = None,
                 now: Optional[datetime] = None,
                 extra_meta: Optional[Dict] = None) -> ImportResult:
    """Copy/transcode ``src`` into ``recordings_dir`` as slot ``slot``'s newest
    take, with its ``.meta``. Blocking (run it on a worker thread); raises
    VideoImportError with an operator-readable message."""
    progress = progress or (lambda frac, text: None)
    if not 1 <= int(slot) <= 9:
        raise VideoImportError(f"slot must be 1-9, got {slot!r}")
    src = validate_source(src)
    mode = resolve_mode(src, mode)
    os.makedirs(recordings_dir, exist_ok=True)
    cleanup_stale_temps(recordings_dir)

    probe = probe_video(src, count_frames=(mode == "copy"))
    if not probe["decodable"]:
        raise VideoImportError(
            f"OpenCV cannot decode '{os.path.basename(src)}' "
            f"(codec '{probe['fourcc'] or '?'}'). Convert it to H.264 .mp4 first "
            "(e.g. HandBrake or VLC > Convert), then import that.")
    src_meta = _read_source_meta(src)
    src_size = os.path.getsize(src)
    src_mtime = os.path.getmtime(src)
    sidecar_fps = _sane_fps((src_meta or {}).get("actual_fps"))
    fps = sidecar_fps or probe["fps"] or 25.0

    ext = os.path.splitext(src)[1].lower() if mode == "copy" else ".avi"
    dest = next_slot_path(recordings_dir, slot, ext, now)
    tmp = os.path.join(recordings_dir, TEMP_PREFIX + os.path.basename(dest))
    meta_path = dest + ".meta"
    try:
        if mode == "copy":
            _check_space(recordings_dir, src_size)
            _copy(src, tmp, src_size, progress)
            frames, size = probe["frames"], (probe["width"], probe["height"])
            codec, container_fps = probe["fourcc"], probe["fps"]
        else:
            _check_space(recordings_dir, src_size)        # floor; refined in-flight
            frames, size = _transcode(src, tmp, fps, probe["frames"], recordings_dir,
                                      progress)
            codec, container_fps = IMPORT_TRANSCODE_CODEC, fps
        out_bytes = os.path.getsize(tmp)
        meta = {
            # v1 keys first (playback reads actual_fps; fingerprints read frames)
            "actual_fps": round(float(fps), 3),
            "frames": int(frames),
            "meta_version": 2,
            "slot": int(slot),
            "file": os.path.basename(dest),
            "codec": codec,
            "container_fps": container_fps,
            "size": [int(size[0]), int(size[1])],
            "source": "import",
            "imported_at": datetime.now().isoformat(timespec="seconds"),
            "imported_from": {
                "path": src,
                "size": src_size,
                "mtime": datetime.fromtimestamp(src_mtime).isoformat(timespec="seconds"),
                "mode": mode,
                "codec": probe["fourcc"],
                "fps": probe["fps"],
                "frames": probe["frames"],
                "width": probe["width"],
                "height": probe["height"],
            },
            "app": app_version(),
        }
        if mode == "transcode":
            meta["imported_from"]["transcode_quality"] = IMPORT_TRANSCODE_QUALITY
        if src_meta is not None:
            meta["imported_from"]["meta"] = src_meta
            # A WallDance take keeps its provenance-relevant facts.
            if isinstance(src_meta.get("input_transform"), dict):
                meta["input_transform"] = src_meta["input_transform"]
        if extra_meta:
            for key, value in extra_meta.items():
                meta.setdefault(key, value)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1, default=str)
        os.replace(tmp, dest)
    except BaseException:
        for leftover in (tmp, meta_path):
            try:
                if os.path.exists(leftover):
                    os.remove(leftover)
            except OSError:
                pass
        raise
    progress(1.0, "done")
    return ImportResult(dest=dest, mode=mode, frames=int(frames), fps=float(fps),
                        width=int(size[0]), height=int(size[1]), bytes=out_bytes,
                        meta=meta)
