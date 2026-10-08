#!/usr/bin/env python3
"""HTML report of the 30 m confirmation (results.json, light.json, figs.json, flow_v2/v2_calibrate.json,
verdict.json) -> report.html next to img/ (relative image paths; light/dark tokens as flowcheck/report.py)."""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/data/WallDance/tmp_analysis/flowcheck")
from report import CSS  # noqa: E402  (same tokens as the flow-check report)

SHORT = {"slot_2_20261007_200742": "slot 2", "slot_3_20261007_201246": "slot 3",
         "slot_4_20261007_201949": "slot 4", "slot_5_20261007_202412": "slot 5"}
VNAME = {"V0": "V0 as shot", "V1": "V1 D27 settings", "V2": "V2 operator flow (D27 + Calibrate)",
         "V1b": "Control: V1 at x@800", "V1r": "Control: V1 + ROI 1300 x 900"}
VDESC = {
    "V0": "The project config as recorded: x@800, confidence 0.25, intermittent confirm OFF, gamma 0.8 / CLAHE 1.0, "
          "MOG2 8 @ 0.5, person height 150 (slot 2) / 253 (slots 3-5), ROI off, the live snapshot "
          "(plates/latest.npz, 20:06, no belt on the stand yet).",
    "V1": "V0 + D27: yolo_first, confidence 0.15, intermittent confirm ON, x@1280. Same snapshot.",
    "V2": "V1, then Calibrate on the empty end of slot 2 (from frame 1700, belt lit on the stand): it keeps "
          "gamma 0.80 / CLAHE 1.0 and MOG2 8 @ 0.5, and takes a new snapshot (with the belt on the stand).",
    "V1b": "V1 at x@800 instead of x@1280 (isolates the input size).",
    "V1r": "V1 with a ROI of 1300 x 900 px at (238, 250) on the wall band (the 25 m project's ROI width): the dancer "
           "~128 px in YOLO's input instead of ~94; the walk-out close to the lens leaves the ROI.",
}


def esc(x):
    return html.escape(str(x))


def f3(v):
    return "-" if v is None else f"{v:.3f}"


def holes_txt(m):
    if not m["holes"]:
        return "none"
    return "; ".join(f"{h['t']:.1f} s +{h['dur_s']:.1f} s ({h['cause']})" for h in m["holes"])


def eps_txt(eps, k=3):
    if not eps:
        return ""
    s = ", ".join(f"{t:.1f} s ({d:.1f} s)" for t, d in sorted(eps, key=lambda e: -e[1])[:k])
    return s + (" ..." if len(eps) > k else "")


def ph_txt(m):
    ch = "".join(f" &rarr; {new} @{t:.0f} s" for t, _old, new in m["ph_changes"])
    return f"{m['ph_start']}{ch}"


def variant_table(res, v):
    rows = res["variants"].get(v) or {}
    h = ["<div class='wrap'><table><tr><th>take</th><th>in view (s)</th><th>coverage in view</th>"
         "<th>on the walker</th><th>far wall: coverage / on walker</th><th>holes &ge; 1 s (cause)</th>"
         "<th>2-point frames</th><th>1 point, off the walker (frames)</th><th>on stand / door (frames)</th>"
         "<th>points with nobody in view</th><th>jumps</th><th>id switches</th><th>lag (ms)</th>"
         "<th>person height (px) over the take</th><th>own size at the wall (px)</th></tr>"]
    for stem, m in rows.items():
        cls_g = " class='bad'" if m["ghost_frames"] > 20 else ""
        cls_w = " class='bad'" if m["wrong_frames"] > 20 else ""
        cls_s = " class='bad'" if m["stand_frames"] + m["door_frames"] > 20 else ""
        own = "-" if m["own_wall_med"] is None else f"{m['own_wall_med']:.0f} (gates on own size {m['own_gates_share']:.0%})"
        h.append(f"<tr><td>{SHORT[stem]}</td><td>{m['view_s'][0]:.1f}-{m['view_s'][1]:.1f}</td>"
                 f"<td>{f3(m['cov_view'])}</td><td>{f3(m['on_view'])}</td>"
                 f"<td>{f3(m['cov_wall'])} / {f3(m['on_wall'])}</td><td style='white-space:normal;min-width:220px'>"
                 f"{esc(holes_txt(m))}</td><td{cls_g}>{m['ghost_frames']}</td><td{cls_w}>{m['wrong_frames']}</td>"
                 f"<td{cls_s}>{m['stand_frames']} / {m['door_frames']}</td><td>{m['points_out_of_view']}</td>"
                 f"<td>{len(m['jumps'])}</td><td>{m['id_switches']}</td><td>{'-' if m['lag_ms'] is None else round(m['lag_ms'])}</td>"
                 f"<td style='white-space:normal;min-width:160px'>{ph_txt(m)}</td><td>{own}</td></tr>")
    h.append("</table></div>")
    return "".join(h)


def main():
    res = json.loads((HERE / "results.json").read_text())
    light = json.loads((HERE / "light.json").read_text())
    figs = json.loads((HERE / "figs.json").read_text())
    cal = json.loads((HERE / "flow_v2" / "v2_calibrate.json").read_text())
    verdict = json.loads((HERE / "verdict.json").read_text())
    body = ["<h1>30 m confirmation: the release on last night's far-wall takes</h1>",
            "<p class='mut'>mur30m-0710: 4 solo takes (slots 2-5, 2026-10-07 20:07-20:25), one walker out to the far "
            "wall and back, a lit IR belt hanging on a light stand at the wall. Release code 46a0262 (= GitHub release "
            "c30b6e9's app code), TensorRT FP16 on dev37 (x@800 built here for V0, x@1280 = models/dev37). Ground "
            "truth for the walker: a two-background difference (the take's empty start and end), independent of "
            "YOLO and the tracker; it agrees with every YOLO box &ge; 0.5. Times = frame / the take's fps "
            "(18.7-19.5).</p>"]
    body.append("<div class='card'><b>Verdicts</b><ul>" + "".join(f"<li>{x}</li>" for x in verdict["verdicts"]) +
                "</ul></div>")
    body.append("<h2>What changes between V0, V1 and V2</h2>" + verdict["changes"])
    for v in ("V0", "V1", "V2", "V1b", "V1r"):
        if v not in res["variants"] or not res["variants"][v]:
            continue
        body.append(f"<h3>{esc(VNAME[v])}</h3><p class='mut'>{esc(VDESC[v])}</p>")
        body.append(variant_table(res, v))
    body.append("<p class='mut'>Coverage in view: share of the frames between the walker's first and last frame "
                "with a point sent. On the walker: a point within 0.75 body heights (&ge; 60 px) of the walker. "
                "2-point frames: more than one point with one person. Off the walker: exactly one point, not on "
                "the walker. On stand / door: a point on the light stand or the door while the walker is elsewhere "
                "or out of view. Own size: the tracker's own-size estimate of the walker's track at the wall, "
                "and the share of those frames where the gates use it (global height &gt; 2 x own).</p>")
    if verdict.get("guard"):
        body.append("<h2>The height guard</h2>" + verdict["guard"])
    body.append("<h2>Dancer size at the far wall, and YOLO recall</h2>" + verdict["size"])
    body.append(verdict["size_table"])
    if figs.get("crop"):
        body.append(f"<figure><img src='img/{esc(figs['crop'])}' alt='far wall at native resolution'><figcaption>"
                    "Slot 2 around 69 s at native resolution (crop 540 x 240 px of the 1776 x 1304 frame): the walker "
                    "in front of the door, the belt on the stand on the right. Display contrast boosted.</figcaption></figure>")
    body.append("<h2>Calibrate on the empty end of slot 2</h2>" + verdict["calibrate"])
    body.append("<h2>The belt on the stand</h2>" + verdict["belt"])
    body.append("<h2>Light at 30 m (for the IR order)</h2>" + verdict["light"])
    body.append("<h2>30 m vs 25 m (2026-10-06 night)</h2>" + verdict["compare"])
    body.append("<h2>Per take</h2>")
    for stem in SHORT:
        body.append(f"<h3>{SHORT[stem]} <span class='mut'>({stem})</span></h3>")
        if stem in verdict.get("per_take", {}):
            body.append(f"<p>{verdict['per_take'][stem]}</p>")
        if figs["xt"].get(stem):
            body.append(f"<figure><img src='img/{esc(figs['xt'][stem])}' alt='x over time'><figcaption>x of the "
                        "point(s) sent per variant (colour = state: green live, cyan belt, grey snapshot, orange held), "
                        "black = the walker (ground truth), red circles = 2 points, red bands = holes.</figcaption></figure>")
        for s in figs["strips"].get(stem) or []:
            body.append(f"<figure><img src='img/{esc(s['img'])}' alt='{esc(s['kind'])}'><figcaption>{esc(s['v'])}: "
                        f"{esc(s['title'])} -- frames -10, -5, 0, +5, +10 (yellow: YOLO &ge; 0.5, dots: the point "
                        "sent, magenta: snapshot foreground)</figcaption></figure>")
    body.append("<h2>Method and caveats</h2>" + verdict["method"])
    page = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>30 m confirmation</title><style>" + CSS +
            "</style></head><body><main>" + "".join(body) + "</main></body></html>")
    (HERE / "report.html").write_text(page)
    print("wrote", HERE / "report.html")


if __name__ == "__main__":
    main()
