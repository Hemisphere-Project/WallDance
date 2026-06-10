"""Apply the operator GT pass (2026-06-10) to the scenario manifests.

- verified=true + method/notes per the operator's tile-count results
- per-range expected_count for blur-runner / dark-crowd / white-walkers
  (window-relative ranges; boundaries +/-20 frames = GT-sheet stride)
- promote the two drafts into scenarios/

Run from application/:  .venv/Scripts/python.exe ../tmp_analysis/apply_gt.py
"""
from __future__ import annotations

import json
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "application"
SCEN = APP / "tests" / "scenarios"
DRAFTS = SCEN / "drafts"

METHOD = ("operator tile-count pass 2026-06-10 on the brightened GT sheets "
          "(tmp_analysis/gen_gt_sheets.py, 20-frame stride; range boundaries "
          "accurate to +/-20 frames)")

# name -> (expected_count override or None, extra note)
GT = {
    "hangar-floor": (None, "Operator re-confirmed 2026-06-10: N=1 throughout."),
    "hangar-aerial": (None, "Operator re-confirmed 2026-06-10: N=1 throughout."),
    "texture-aerial": (None, "Operator verified 2026-06-10: N=1 throughout."),
    "texture-duo": (None, "Operator verified 2026-06-10: N=2 throughout."),
    "texture-wallhang": (None, "Operator verified 2026-06-10: N=1 throughout."),
    "white-duo": (None, "Operator verified 2026-06-10: N=2 throughout."),
    "outdoor-night": (None, "Operator verified 2026-06-10: N=1 throughout."),
    "outdoor-sitter": (None, "Operator verified 2026-06-10: N=2 throughout "
                             "(second person static in shadow)."),
    "facade-ghosts": (None, "Operator verified 2026-06-10: N=4 throughout."),
    "blur-runner": (
        [{"from": 0, "to": 179, "n": 1},
         {"from": 180, "to": 399, "n": 2},
         {"default": 2}],
        "Operator verified 2026-06-10: white-clad assistant in frame the whole "
        "window; the running dancer enters at abs frame ~1080 (rel 180). "
        "N: rel [0,179]=1, [180,399]=2."),
}

PROMOTE = {
    "dark-crowd": (
        [{"from": 0, "to": 119, "n": 1},
         {"from": 120, "to": 339, "n": 2},
         {"from": 340, "to": 399, "n": 1},
         {"default": 1}],
        "Operator verified 2026-06-10 (per-range): [0,119]=1, [120,339]=2, "
        "[340,399]=1."),
    "white-walkers": (
        [{"from": 0, "to": 39, "n": 5},
         {"from": 40, "to": 159, "n": 4},
         {"from": 160, "to": 199, "n": 5},
         {"from": 200, "to": 279, "n": 4},
         {"from": 280, "to": 339, "n": 5},
         {"from": 340, "to": 399, "n": 4},
         {"default": 4}],
        "Operator verified 2026-06-10 (per-range): [0,39]=5, [40,159]=4, "
        "[160,199]=5, [200,279]=4, [280,339]=5, [340,399]=4."),
}


def apply(path: Path, ec, note: str) -> None:
    m = json.loads(path.read_text())
    if ec is not None:
        m["expected_count"] = ec
    gt = m.setdefault("ground_truth", {})
    gt["verified"] = True
    gt["method"] = METHOD
    gt["notes"] = note + " " + gt.get("notes", "").replace(
        "PENDING -- operator montage pass on tmp_analysis/gt_sheets/", "").strip()
    # drop the draft tag if present
    if "tags" in m and "draft" in m["tags"]:
        m["tags"].remove("draft")
    path.write_text(json.dumps(m, indent=1))
    print("updated", path.name)


def main():
    for name, (ec, note) in GT.items():
        apply(SCEN / f"{name}.json", ec, note)
    for name, (ec, note) in PROMOTE.items():
        src = DRAFTS / f"{name}.json"
        dst = SCEN / f"{name}.json"
        apply(src, ec, note)
        src.rename(dst)
        print("promoted", dst.name)
    try:
        DRAFTS.rmdir()
        print("removed empty drafts/")
    except OSError:
        pass


if __name__ == "__main__":
    main()
