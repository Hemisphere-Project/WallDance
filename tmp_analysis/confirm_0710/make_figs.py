#!/usr/bin/env python3
"""Figures for the 30 m confirmation report: one x(t) plot per take (V0 / V1 / V2 + the walker GT), event strips
(tests/flow_check.py strip(): frames -10 -5 0 +5 +10 around an event, the point sent + YOLO boxes drawn), and a
native-resolution crop of the far wall.  -> img/*.png|jpg, figs.json (what was drawn, for report.py)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, "/data/WallDance/application/tests")
import flow_check as F  # noqa: E402
import analyse as A  # noqa: E402

HERE = Path(__file__).resolve().parent
IMG = HERE / "img"
REC = Path("/data/WallDance/projects/mur30m-0710/recordings")
VCOL = {"V0": "as shot (x@800, conf 0.25)", "V1": "D27 (x@1280, conf 0.15, intermittent)",
        "V2": "D27 + Calibrate snapshot", "V1b": "D27 at x@800"}


BAND = (560, 500, 1460, 800)       # far-wall crop: door, stand, a margin (x0, y0, x1, y1)
EVENTS = {
    "slot_2_20261007_200742": [
        ("V0", 70.0, "as shot: the point stays on the stand (belt state, the static belt answers) while the walker "
                     "crosses the door, 66-84 s (x@800 sees no skeleton at the wall)", BAND),
        ("V1", 70.0, "D27: same moment, the point is on the walker (YOLO at x@1280 re-acquires at 66 s)", BAND),
        ("V1", 45.0, "D27: walker standing by the stand among the belts (YOLO misses; the belt holds the point)", BAND)],
    "slot_4_20261007_201949": [
        ("V1", 17.0, "D27: 2 points -- D1 held on the door by the snapshot foreground (fg), D2 on the walker", (560, 500, 1460, 800)),
        ("V1", 33.0, "D27: 2 points -- D1 now held by snapshot foreground near the stand's old place", BAND),
        ("V2", 17.0, "D27 + Calibrate snapshot: the same orphan on the door, gone by ~21 s", (560, 500, 1460, 800)),
        ("V2", 33.0, "D27 + Calibrate snapshot: one point, on the walker", BAND)],
    "slot_5_20261007_202412": [
        ("V1", 25.2, "D27: the 1.2-body-height jump at 25.2 s", BAND),
        ("V2", 30.4, "D27 + Calibrate snapshot: the 2.1-body-height jump at 30.4 s (fg state)", BAND)],
    "slot_3_20261007_201246": [
        ("V1", 33.0, "D27: walker crouching in front of the door (31-35 s)", BAND)],
}


def xt_plot(stem, fps, rows_by_v, res_by_v, scene, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    gt = A.gt_of(scene["rows"])
    stands = [p for p in scene["stands"] if p]
    nv = len(rows_by_v)
    fig, axes = plt.subplots(nv, 1, figsize=(11, 2.25 * nv), sharex=True, squeeze=False)
    tg = [f / fps for f in sorted(gt)]
    xg = [gt[f][0] for f in sorted(gt)]
    for ax, (v, rows) in zip(axes[:, 0], rows_by_v.items()):
        m = res_by_v[v]
        ax.axhspan(A.DOOR_X[0], A.DOOR_X[1], color="#b0b0b0", alpha=0.18, lw=0)
        for sx, sy, _n in stands:
            ax.axhline(sx, color="#7a4fd0", lw=0.8, ls="--")
        a, b = m["view"]
        ax.axvspan(0, a / fps, color="#000000", alpha=0.06, lw=0)
        ax.axvspan(b / fps, len(rows) / fps, color="#000000", alpha=0.06, lw=0)
        ax.scatter(tg, xg, s=1.2, c="#222222", label="walker (GT)")
        for st, col in F.STATE_HEX.items():
            pts = [(i / fps, e["x"]) for i, r in enumerate(rows) for e in F.emitted(r) if e["state"] == st]
            if pts:
                ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=4, c=col, label=f"sent: {st}")
        gh = [(i / fps, e["x"]) for i, r in enumerate(rows) if len(F.emitted(r)) > 1 for e in F.emitted(r)]
        if gh:
            ax.scatter([p[0] for p in gh], [p[1] for p in gh], s=9, facecolors="none", edgecolors="#d02020",
                       lw=0.35, alpha=0.6, label="2 points")
        for h in m["holes"]:
            ax.axvspan(h["t"], h["t"] + h["dur_s"], color="#ff5050", alpha=0.18, lw=0)
        ax.set_ylim(0, 1776)
        ax.set_ylabel(f"{v}\nx (px)", fontsize=8)
        ax.set_title(VCOL.get(v, v), fontsize=8, loc="left")
    axes[0, 0].legend(loc="upper right", fontsize=6.5, ncol=4, markerscale=2.5)
    axes[-1, 0].set_xlabel("time (s, frame / take fps)  -- grey band: door, dashed: belt on the stand, "
                           "shaded ends: nobody in view, red bands: holes >= 1 s", fontsize=8)
    axes[-1, 0].set_xlim(0, len(next(iter(rows_by_v.values()))) / fps)
    fig.suptitle(f"{A.SHORT[stem]} ({stem}): x of the point(s) sent vs the walker", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)


def crop_strip(video, rows, center, path, box, fps, label):
    """Native-resolution far-wall crop strip (5 frames) with the points sent."""
    ds_ = (-10, 0, 10)
    idxs = [max(0, min(len(rows) - 1, center + d)) for d in ds_]
    frs = F.read_frames(video, idxs)
    x0, y0, x1, y1 = box
    tiles = []
    for d, i in zip(ds_, idxs):
        if i not in frs:
            continue
        img = F.overlay(frs[i], rows[i], None, "")[y0:y1, x0:x1].copy()
        cv2.putText(img, f"{i / fps:.1f}s", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        tiles.append(img)
    if tiles:
        F.save_jpeg(np.vstack(tiles), path, x1 - x0, max_kb=220)
        return path.name
    return None


def main():
    IMG.mkdir(exist_ok=True)
    res = json.loads((HERE / "results.json").read_text())
    figs = {"xt": {}, "strips": {}}
    for stem, fps in A.TAKES.items():
        scene = json.loads((HERE / "scene" / f"scene_{stem}.json").read_text())
        rows_by_v, res_by_v = {}, {}
        for v in ("V0", "V1", "V2"):
            p = HERE / "tl" / f"{v}_{stem}.json"
            if p.exists() and stem in res["variants"].get(v, {}):
                rows_by_v[v] = F.load_rows(p)
                res_by_v[v] = res["variants"][v][stem]
        path = IMG / f"xt_{stem}.png"
        xt_plot(stem, fps, rows_by_v, res_by_v, scene, path)
        figs["xt"][stem] = path.name
        video = REC / f"{stem}.avi"
        strips = []
        for v, rows in rows_by_v.items():                 # the start holes (walker close to the lens): full frame
            for h in res_by_v[v]["holes"][:1]:
                f = h["frame"] + int(min(h["dur_s"], 2.0) * fps / 2)
                name = F.strip(video, rows, f, None, IMG / f"{v}_{stem}_hole_{f}.jpg", "")
                if name:
                    strips.append({"v": v, "kind": "hole", "img": name,
                                   "title": f"hole {h['t']:.1f} s for {h['dur_s']:.1f} s ({h['cause']})"})
        for v, t, title, box in EVENTS.get(stem, []):        # far-wall events: native-resolution crops
            p = HERE / "tl" / f"{v}_{stem}.json"
            rows = rows_by_v.get(v) or F.load_rows(p)
            f = int(t * fps)
            name = crop_strip(video, rows, f, IMG / f"{v}_{stem}_wall_{f}.jpg", box, fps, "")
            if name:
                strips.append({"v": v, "kind": "wall", "img": name, "title": title})
        figs["strips"][stem] = strips
        print(stem, "strips", len(strips), flush=True)
    # far-wall crop at native resolution: walker at the door + belt on the stand (slot 2, ~69 s)
    stem = "slot_2_20261007_200742"
    rows = F.load_rows(HERE / "tl" / f"V2_{stem}.json")
    figs["crop"] = crop_strip(REC / f"{stem}.avi", rows, 1290, IMG / "farwall_crop.jpg", (640, 540, 1180, 780),
                              A.TAKES[stem], "")
    (HERE / "figs.json").write_text(json.dumps(figs, indent=1))
    print("wrote figs.json")


if __name__ == "__main__":
    main()
