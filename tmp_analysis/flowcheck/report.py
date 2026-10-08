#!/usr/bin/env python3
"""HTML report of a flow_check run (+ the older-shots regression table): one self-contained page with relative
image paths, readable on a phone, in light and dark.

  python report.py --run /data/WallDance/tmp_analysis/flowcheck/<run> [--older /data/.../flowcheck/older]
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "application" / "tests"))
sys.path.insert(0, str(WT / "application" / "src"))

OLD = {   # stored timelines of earlier code (the main session's scratchpad)
    "e1": "/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp/e1/{s}_k1.json",
    "v5": "/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/older/{s}_v5.json",
}
TAKE_NAMES = {"slot_2_20261006_211831": "s2a (bright, still positions)",
              "slot_2_20261006_214531": "s2c (dark, 2 min still at the back)",
              "slot_3_20261006_212855": "s3 (bright, moving)", "slot_4_20261006_213310": "s4 (dark, moving)",
              "slot_6_20261006_214306": "s6 (dark, entry/exit)", "slot_7_20261006_220640": "s7 (dark, moving)"}

CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e1dc;--card:#fff;--good:#2f8f46;--bad:#c2410c;--warn:#a16207}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#151514;--fg:#ecebe6;--mut:#a3a29c;
--line:#33322f;--card:#1e1e1c;--good:#5cc177;--bad:#f0814f;--warn:#e0b341}}
:root[data-theme="dark"]{--bg:#151514;--fg:#ecebe6;--mut:#a3a29c;--line:#33322f;--card:#1e1e1c;--good:#5cc177;
--bad:#f0814f;--warn:#e0b341}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,
"Segoe UI",sans-serif}main{max-width:1100px;margin:0 auto;padding:20px 16px 60px}h1{font-size:1.5rem;margin:.2em 0}
h2{font-size:1.2rem;margin:1.8em 0 .4em;border-top:1px solid var(--line);padding-top:1em}h3{font-size:1.02rem;
margin:1.4em 0 .3em}.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:12px 14px;margin:10px 0}table{border-collapse:collapse;width:100%;font-size:.88rem;
font-variant-numeric:tabular-nums}th,td{border-bottom:1px solid var(--line);padding:5px 6px;text-align:right;
white-space:nowrap}th:first-child,td:first-child{text-align:left}.wrap{overflow-x:auto}.good{color:var(--good)}
.bad{color:var(--bad);font-weight:600}.warn{color:var(--warn)}img{max-width:100%;height:auto;border-radius:6px;
border:1px solid var(--line);display:block;margin:6px 0}figure{margin:10px 0}figcaption{font-size:.85rem;
color:var(--mut)}ul{padding-left:1.2em}code{font-size:.9em}
"""


def esc(x):
    return html.escape(str(x))


def fmt(v, nd=3):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def delta_cls(new, old, higher_better=True, tol=0.0):
    if new is None or old is None:
        return ""
    if abs(new - old) <= tol:
        return ""
    better = (new > old) if higher_better else (new < old)
    return "good" if better else "bad"


def take_table(t, label):
    ev, qa = t["events"], t["quality"]
    b, c = ev["baseline"], ev[f"after {label}"]
    qb, qc = qa["baseline"], qa[f"after {label}"]
    rows = [
        ("coverage (a point is sent)", b["coverage"], c["coverage"], True, 0.002),
        ("on the dancer (vs YOLO >= 0.5)", qb.get("on_dancer"), qc.get("on_dancer"), True, 0.005),
        ("holes >= 1 s", len(b["holes"]), len(c["holes"]), False, 0),
        ("longest hole (s)", b["longest_hole_s"], c["longest_hole_s"], False, 0.2),
        ("jumps", len(b["jumps"]), len(c["jumps"]), False, 0),
        ("ghost frames (> 1 point)", b["ghost_frames"], c["ghost_frames"], False, 0),
        ("id switches", b["id_switches"], c["id_switches"], False, 0),
        ("coasting share", qb.get("coasting_share"), qc.get("coasting_share"), False, 0.01),
        ("lag (ms)", qb.get("lag_ms"), qc.get("lag_ms"), False, 15),
    ]
    h = ["<div class='wrap'><table><tr><th></th><th>baseline</th><th>after Calibrate</th></tr>"]
    for name, vb, vc, hb, tol in rows:
        cls = delta_cls(vc, vb, hb, tol) if isinstance(vb, (int, float)) and isinstance(vc, (int, float)) else ""
        h.append(f"<tr><td>{esc(name)}</td><td>{fmt(vb)}</td><td class='{cls}'>{fmt(vc)}</td></tr>")
    h.append("</table></div>")
    return "".join(h)


def events_list(t, label):
    c = t["events"][f"after {label}"]
    items = []
    for hh in c["holes"]:
        seen = hh.get("yolo_saw_someone")
        tag = " <span class='bad'>YOLO saw someone during it</span>" if seen and seen > 0.3 else ""
        if hh.get("startup"):
            # PLAN_25M §C.14: an empty frame, then the operator walking out from the camera (boxes far
            # bigger than a wall dancer), then ~0.7 s of warm-up -- not a dancer entry
            parts = []
            if hh.get("empty_s") is not None:
                parts.append(f"nobody in view for {hh['empty_s']:.1f} s")
            if hh.get("h_med"):
                parts.append(f"then YOLO boxes ~{hh['h_med']} px tall (a walk-out close to the camera when far "
                             f"bigger than the dancer at the wall)")
            tag = (" <span class='mut'>(start of the take: " + (", ".join(parts) or "warm-up") +
                   "; not a dancer entry, PLAN_25M §C.14)</span>")
        items.append(f"<li>hole at {hh['t']:.1f} s for {hh['dur_s']:.1f} s{tag}</li>")
    for j in c["jumps"]:
        items.append(f"<li>jump at {j['t']:.1f} s: D{j['id']} moved {j['dist_h']:.1f} body heights "
                     f"({j['state']}, to x {j['x']})</li>")
    for g in c["ghost_episodes"]:
        items.append(f"<li>ghost at {g['t']:.1f} s: {g['n']} frames with 2+ points</li>")
    return "<ul>" + "".join(items) + "</ul>" if items else "<p class='mut'>No hole, jump or ghost.</p>"


def summary_line(res):
    label = res["label"]
    tk = res["takes"]
    cb = sum(t["events"]["baseline"]["coverage"] for t in tk) / max(1, len(tk))
    cc = sum(t["events"][f"after {label}"]["coverage"] for t in tk) / max(1, len(tk))
    jb = sum(len(t["events"]["baseline"]["jumps"]) for t in tk)
    jc = sum(len(t["events"][f"after {label}"]["jumps"]) for t in tk)
    gb = sum(t["events"]["baseline"]["ghost_frames"] for t in tk)
    gc = sum(t["events"][f"after {label}"]["ghost_frames"] for t in tk)
    return cb, cc, jb, jc, gb, gc


def older_table(older: Path):
    import output_quality
    import scoring
    rows = []
    for mode in ("e1", "v5"):
        for new in sorted(older.glob(f"*_rc_{mode}.json")):
            s = new.name[: -len(f"_rc_{mode}.json")]
            oldp = Path(OLD[mode].format(s=s))
            if not oldp.exists():
                continue
            man = json.loads((WT / "application" / "tests" / "scenarios" / f"{s}.json").read_text())
            fps = man.get("fps") or 19.8
            out = {}
            for tag, p in (("old", oldp), ("new", new)):
                q = output_quality.compare_streams(scoring._load_timeline(str(p)), man, fps=fps).get("emitted", {})
                cont, qual = q.get("continuity", {}), q.get("quality", {})
                out[tag] = {"cov": cont.get("coverage"), "hole": cont.get("gap_max_s"),
                            "holes1": cont.get("gaps_ge_1s"), "on": cont.get("on_dancer"),
                            "coast": qual.get("coasting_share"), "lag": qual.get("lag_ms"),
                            "sw": qual.get("id_switches"), "overN": qual.get("over_n_frames")}
            rows.append((s, mode, out))
    h = ["<div class='wrap'><table><tr><th>scene</th><th>as</th><th>coverage</th><th>longest hole (s)</th>"
         "<th>holes >= 1 s</th><th>on dancer</th><th>coasting</th><th>lag (ms)</th><th>switches</th>"
         "<th>extra points</th></tr>"]
    worse = []
    for s, mode, o in rows:
        a, b = o["old"], o["new"]

        def cell(k, hb, tol, nd=3):
            cls = delta_cls(b[k], a[k], hb, tol) if a[k] is not None and b[k] is not None else ""
            if cls == "bad":
                worse.append(f"{s} ({mode}) {k}: {fmt(a[k], nd)} -> {fmt(b[k], nd)}")
            return f"<td class='{cls}'>{fmt(a[k], nd)} &rarr; {fmt(b[k], nd)}</td>"
        h.append(f"<tr><td>{esc(s)}</td><td>{mode}</td>{cell('cov', True, 0.005)}{cell('hole', False, 0.3, 2)}"
                 f"{cell('holes1', False, 0)}{cell('on', True, 0.01)}{cell('coast', False, 0.02)}"
                 f"{cell('lag', False, 20, 0)}{cell('sw', False, 1)}{cell('overN', False, 5)}</tr>")
    h.append("</table></div>")
    return "".join(h), worse, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--older", default=None)
    ap.add_argument("--title", default="Operator flow check: last night's takes")
    a = ap.parse_args()
    run = Path(a.run)
    results = [json.loads(p.read_text()) for p in sorted(run.glob("*.json"))
               if not p.name.startswith(("base_",)) and "_slot_" not in p.name]
    results = [r for r in results if isinstance(r, dict) and "takes" in r]
    results.sort(key=lambda r: (len(r["takes"]) < 6, r["label"]))      # the full slot-1 flow first
    body = [f"<h1>{esc(a.title)}</h1><p class='mut'>Release candidate code; takes recut to one camera frame "
            "(1488x1300, the area both of last night's crops share) so one Calibrate snapshot applies to all. "
            "Baseline = the project config as is, no snapshot. After Calibrate = the settings and the "
            "snapshot the real Calibrate left after running on the empty take. One dancer (N = 1).</p>"]
    summ = ["<div class='card'><b>In short</b><ul>"]
    for r in results:
        cal = r["calibrate"]
        cb, cc, jb, jc, gb, gc = summary_line(r)
        aft, bef = cal["after"], cal["before"]
        kept = (abs(aft["gamma"] - bef["gamma"]) < 1e-3 and abs(aft["clahe"] - bef["clahe"]) < 1e-3)
        change = "" if kept else f" -> {aft['gamma']:.2f}/{aft['clahe']:.1f}"
        summ.append(f"<li><b>Calibrate on {esc(cal['take'])}</b>: "
                    f"{'kept' if kept else 'changed'} gamma {bef['gamma']:.2f}/CLAHE {bef['clahe']:.1f}{change}"
                    f", MOG2 {bef['var']:.0f}@{bef['scale']:.1f} -> {aft['var']:.0f}@{aft['scale']:.1f}; "
                    f"mean coverage {cb:.3f} -> {cc:.3f}, jumps {jb} -> {jc}, ghost frames {gb} -> {gc} "
                    f"over {len(r['takes'])} takes.</li>")
    summ.append("</ul></div>")
    body += summ
    for r in results:
        cal, label = r["calibrate"], r["label"]
        body.append(f"<h2>Calibrate on {esc(cal['take'])} (from frame {cal['start']}) &rarr; "
                    f"{len(r['takes'])} takes</h2>")
        wc = cal["wall_check"]
        plate = cal.get("plate") or {}
        body.append("<div class='card'>"
                    f"<div>Before: gamma {cal['before']['gamma']:.2f}, CLAHE {cal['before']['clahe']:.1f}, "
                    f"MOG2 {cal['before']['var']:.0f} @ {cal['before']['scale']:.1f}, confidence "
                    f"{cal['before']['confidence']:.2f}</div>"
                    f"<div><b>After: gamma {cal['after']['gamma']:.2f}, CLAHE {cal['after']['clahe']:.1f}, "
                    f"MOG2 {cal['after']['var']:.0f} @ {cal['after']['scale']:.1f}</b></div>"
                    f"<div>Exposure / gain from the take: {esc(cal.get('exposure_gain'))}</div>"
                    f"<div>Empty-wall check: <code>{esc(wc.get('log'))}</code></div>"
                    f"<div>Snapshot: {esc(plate.get('size') or plate.get('error'))}"
                    f"{' (noise ' + str(plate.get('sigma')) + ' DN)' if plate.get('sigma') else ''}</div>"
                    f"<div class='mut'>{cal['frames_used']} frames of the empty take (looped), stages "
                    f"{esc(cal['stages'])}</div></div>")
        for t in r["takes"]:
            stem = Path(t["take"]).stem
            body.append(f"<h3>{esc(TAKE_NAMES.get(stem, stem))}</h3>")
            body.append(take_table(t, label))
            body.append(events_list(t, label))
            if t.get("plot"):
                body.append(f"<figure><img src='{esc(t['plot'])}' alt='x position over time'>"
                            "<figcaption>Horizontal position over time: grey = YOLO detections (conf &ge; 0.5), "
                            "colours = the point sent (green live, cyan belt, grey snapshot, orange held); "
                            "red bands = holes, red lines = jumps.</figcaption></figure>")
            for s in t.get("strips") or []:
                body.append(f"<figure><img src='{esc(s['img'])}' alt='{esc(s['kind'])}'><figcaption>"
                            f"{esc(s['kind'])} at {s['t']:.1f} s: frames -10, -5, 0, +5, +10 (yellow boxes: "
                            "YOLO, filled dots: the point sent, magenta circles: snapshot foreground, grey: "
                            "ROI)</figcaption></figure>")
            if t.get("sheet"):
                body.append(f"<figure><img src='{esc(t['sheet'])}' alt='one frame every 10 s'>"
                            "<figcaption>One frame every 10 s, after Calibrate.</figcaption></figure>")
    if a.older:
        tbl, worse, n = older_table(Path(a.older))
        body.append("<h2>Older shots: release candidate vs earlier code</h2>"
                    "<p class='mut'>e1 = the scenario config at x@1280 (earlier run: 2026-10-07 14:00 code); "
                    "v5 = with belt backing + snapshot + entry rule (earlier run: the 3cde9b3 code). "
                    "Cells: earlier &rarr; now; red = worse beyond noise.</p>")
        body.append(tbl)
        body.append("<p>" + (f"{len(worse)} cell(s) worse: " + "; ".join(esc(w) for w in worse)
                             if worse else f"No regression on {n} runs.") + "</p>")
    page = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>Flow check</title><style>" + CSS +
            "</style></head><body><main>" + "".join(body) + "</main></body></html>")
    (run / "report.html").write_text(page)
    print("wrote", run / "report.html")


if __name__ == "__main__":
    main()
