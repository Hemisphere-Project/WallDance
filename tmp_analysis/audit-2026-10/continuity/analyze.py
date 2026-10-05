#!/usr/bin/env python3
"""Analyse a drive.py replay (rich rows) for a known-N=1 scene.

Prints: count-based continuity metrics, drop-frame cause taxonomy, YOLO
evidence during drops, episode table, spatial sanity (reported vs dancer det).
"""
from __future__ import annotations
import json, sys, argparse, collections, statistics
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from contmetrics import cont_metrics  # noqa

WARM = 15.0
_SPOTS = []
GHOST_AGE = 3
FROZEN = 0.03


def load(p):
    d = json.loads(Path(p).read_text())
    return d["meta"], d["rows"]


def ghost_spots(rows, min_frac=0.10, cell=60):
    """Recurring det spots (fixed scene features) from top raw dets bc>=0.2."""
    cnt = collections.Counter()
    for r in rows:
        for d in r["top"]:
            if d["bc"] >= 0.2:
                cnt[(int(d["c"][0] // cell), int(d["c"][1] // cell))] += 1
    n = len(rows)
    return [((k[0] + .5) * cell, (k[1] + .5) * cell, v / n) for k, v in cnt.most_common(12) if v / n >= min_frac]


def dancer_det(r, spots, tau_lo=0.25, spot_r=70):
    """Best raw det not on a fixed ghost spot (pseudo-GT dancer position)."""
    best = None
    for d in r["top"]:
        if d["bc"] < tau_lo:
            continue
        if any(abs(d["c"][0] - sx) < spot_r and abs(d["c"][1] - sy) < spot_r for sx, sy, _ in spots):
            continue
        if best is None or d["bc"] > best["bc"]:
            best = d
    return best


def classify_drop(r, tau):
    """Why is nothing reported on a frame where the dancer is present (N=1)?"""
    trks = r["trk"]
    if not trks:
        return "no_track"
    fed = [t for t in trks if t["tsu"] == 0 and t["fss"] == 0]
    est_stale = [t for t in trks if t["est"] and t["fss"] > 0]
    if fed and est_stale and all(t["wu"] < WARM for t in fed):
        return "split:det_feeds_unconfirmed_track"
    if fed and all(t["wu"] < WARM for t in fed) and not any(t["est"] for t in trks):
        return "tentative_new"
    # candidate = track with most hits (the dancer's track in N=1)
    t = max(trks, key=lambda t: (t["hits"], -t["tsu"]))
    if t["wu"] >= WARM:
        if t["fss"] > GHOST_AGE and t["spd"] < FROZEN * r["ph"]:
            d = dancer_det(r, _SPOTS[0], 0.25) if _SPOTS else None
            if d is None:
                return "frozen_gate(dancer pos unknown)"
            dist = ((t["p"][0] - d["c"][0]) ** 2 + (t["p"][1] - d["c"][1]) ** 2) ** .5
            return "frozen_gate:on_dancer" if dist < 0.75 * d["h"] else "frozen_gate:track_drifted_off"
        return "other_gate"
    if t["hits"] >= 15 or t["est"]:
        return "warmup_decayed"   # established track whose integral fell <15
    return "tentative_new"         # young track still warming up


def yolo_class(r, tau, spots):
    d = dancer_det(r, spots, tau_lo=0.05)
    if d is None:
        return "yolo_blind"
    if d["bc"] >= tau:
        if r["n_raw"] and not r["n_dup"]:
            return "det>=tau:size_gate"
        if r["xv_rej"]:
            return "det>=tau:crossval_rej"
        return "det>=tau(passed gates)"
    if d["bc"] >= 0.25:
        return "sub_tau(0.25..tau)"
    return "weak(<0.25)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run"); ap.add_argument("--fps", type=float, default=19.7)
    ap.add_argument("--warmup", type=int, default=15)
    ap.add_argument("--episodes", type=int, default=25)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    meta, rows = load(a.run)
    tau = meta["tau"]
    man = {"expected_count": 1, "fps": a.fps, "warmup": a.warmup, "start": meta["start"]}
    tl = [{"frame": r["f"], "reported": len(r["rep"]), "ids": [t["id"] for t in r["rep"]]} for r in rows]
    cm = cont_metrics(tl, man)
    spots = ghost_spots(rows)
    _SPOTS.append(spots)
    rows_s = [r for r in rows if r["f"] >= a.warmup]
    drops = [r for r in rows_s if len(r["rep"]) == 0]
    cause = collections.Counter(classify_drop(r, tau) for r in drops)
    ycls = collections.Counter(yolo_class(r, tau, spots) for r in drops)
    cross = collections.Counter((classify_drop(r, tau), yolo_class(r, tau, spots)) for r in drops)
    # all-frame YOLO evidence
    yall = collections.Counter(yolo_class(r, tau, spots) for r in rows_s)
    # spatial sanity: reported but far from the dancer det
    mis = 0; checked = 0; extra = 0
    for r in rows_s:
        d = dancer_det(r, spots, tau_lo=max(0.5, tau))
        if len(r["rep"]) > 1:
            extra += 1
        if d is None or not r["rep"]:
            continue
        checked += 1
        best = min(((t["c"][0] - d["c"][0]) ** 2 + (t["c"][1] - d["c"][1]) ** 2) ** .5 for t in r["rep"] if t["c"])
        if best > 0.75 * d["h"]:
            mis += 1
    # ids
    first_seen = {}
    for r in rows_s:
        for t in r["rep"]:
            first_seen.setdefault(t["id"], r["abs"])
    all_ids = sorted({t["id"] for r in rows for t in r["trk"]})
    # bridged share of reported frames
    br = sum(1 for r in rows_s for t in r["rep"] if t["br"])
    fss_pos = sum(1 for r in rows_s for t in r["rep"] if (t["fss"] or 0) > 0)
    nrep = sum(len(r["rep"]) for r in rows_s)
    out = {
        "meta": {k: meta[k] for k in ("start", "tau", "n", "md5_bad", "sets")},
        "continuity": cm,
        "ghost_spots": [(round(x), round(y), round(f, 3)) for x, y, f in spots],
        "drop_frames": len(drops),
        "drop_cause": dict(cause.most_common()),
        "drop_yolo_evidence": dict(ycls.most_common()),
        "drop_cause_x_yolo": {f"{k[0]} | {k[1]}": v for k, v in cross.most_common(12)},
        "allframe_yolo_evidence": dict(yall.most_common()),
        "reported_frames": nrep, "reported_bridged": br, "reported_no_fresh_skeleton": fss_pos,
        "multi_report_frames": extra, "spatial_checked": checked, "spatial_misplaced": mis,
        "reported_ids_first_seen": first_seen, "internal_ids_total": len(all_ids),
    }
    # episode table
    eps = []
    cur = None
    for r in rows_s:
        if len(r["rep"]) == 0:
            if cur is None:
                cur = [r]
            else:
                cur.append(r)
        elif cur is not None:
            eps.append(cur); cur = None
    if cur:
        eps.append(cur)
    idx = {r["f"]: r for r in rows}
    ep_rows = []
    for e in eps:
        f0, f1 = e[0]["f"], e[-1]["f"]
        before = idx.get(f0 - 1, {}).get("rep", [])
        after = idx.get(f1 + 1, {}).get("rep", [])
        c = collections.Counter(classify_drop(r, tau) for r in e)
        y = collections.Counter(yolo_class(r, tau, spots) for r in e)
        maxbc = max((d["bc"] for r in e for d in [dancer_det(r, spots, 0.05)] if d), default=0)
        ep_rows.append({
            "abs": e[0]["abs"], "len": len(e), "sec": round(len(e) / a.fps, 2),
            "id_before": [t["id"] for t in before], "id_after": [t["id"] for t in after],
            "cause": dict(c.most_common(3)), "yolo": dict(y.most_common(3)), "max_dancer_bc": round(maxbc, 2),
        })
    out["episodes_by_len"] = sorted(ep_rows, key=lambda x: -x["len"])[:a.episodes]
    lens = [x["len"] for x in ep_rows]
    out["episode_len_hist"] = {"1-2": sum(1 for l in lens if l <= 2), "3-7": sum(1 for l in lens if 3 <= l <= 7),
                               "8-19": sum(1 for l in lens if 8 <= l <= 19), "20-59": sum(1 for l in lens if 20 <= l <= 59),
                               ">=60": sum(1 for l in lens if l >= 60)}
    out["episode_frames_by_len"] = {"1-2": sum(l for l in lens if l <= 2), "3-7": sum(l for l in lens if 3 <= l <= 7),
                                    "8-19": sum(l for l in lens if 8 <= l <= 19), "20-59": sum(l for l in lens if 20 <= l <= 59),
                                    ">=60": sum(l for l in lens if l >= 60)}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
