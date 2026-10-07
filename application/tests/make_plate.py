"""Clean plate (empty-wall snapshot, core/foreground.py) from a recording, for replays.

    python tests/make_plate.py --project P --slot 1 [--take NAME.avi] [--start 0] [--frames 40]
        -> projects/P/plates/plate_<take>_<start>.npz ; replay with --set fg_plate=plates/<that>.npz

The frames must show the EMPTY wall (no dancer, no operator): an empty-wall take, or
the first seconds of a take before anybody enters.  The app captures its own plate
live (Calibrate / "Capture empty wall"); this tool is the offline equivalent.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from core.foreground import CleanPlate  # noqa: E402

PROJECTS_DIR = HERE.parent.parent / "projects"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--slot", type=int)
    ap.add_argument("--take", help="recording file name (default: the slot's newest take)")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--out", help="output .npz (default projects/P/plates/plate_<take>_<start>.npz)")
    a = ap.parse_args()
    rec = PROJECTS_DIR / a.project / "recordings"
    if a.take:
        video = rec / a.take
    else:
        takes = sorted(rec.glob(f"slot_{a.slot}_*.avi"))
        if not takes:
            raise SystemExit(f"no take for {a.project} slot {a.slot}")
        video = takes[-1]
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, a.start)
    frames = []
    i = 0
    while len(frames) < a.frames:
        ok, fr = cap.read()
        if not ok:
            break
        if i % a.stride == 0:
            frames.append(fr[:, :, 0] if fr.ndim == 3 else fr)
        i += 1
    if len(frames) < 2:
        raise SystemExit(f"could not read frames from {video}")
    plate = CleanPlate.from_frames(frames, source=f"{video.name}@{a.start}+{len(frames)}x{a.stride}")
    out = Path(a.out) if a.out else (PROJECTS_DIR / a.project / "plates"
                                      / f"plate_{video.stem}_{a.start}.npz")
    plate.save(out)
    W, H = plate.frame_size
    print(f"plate {out}  ({W}x{H}, {plate.frames} frames, noise {plate.sigma:.2f} DN, "
          f"mean {plate.plate.mean():.1f} DN)")
    try:
        print(f"replay with: --set fg_plate={out.relative_to(PROJECTS_DIR / a.project)}")
    except ValueError:
        print(f"replay with: --set fg_plate={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
