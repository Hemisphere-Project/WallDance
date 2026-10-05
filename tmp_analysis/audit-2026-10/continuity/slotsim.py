#!/usr/bin/env python3
"""Offline simulation of a known-N 'identity-slot' OUTPUT layer over a drive.py
run (N=1 scenes).  Output-only: consumes the tracker's per-frame reported tracks
(and optionally live-but-unreported internal tracks) and emits N stable slots.

Policy (per slot):
  * bound track reported this frame          -> LIVE, position = its centroid
  * else, if candidates allowed include an internal track with the bound id
    (tsu <= relax_tsu)                        -> LIVE(relaxed), position = its KF pos
  * else coast (hold last position) up to coast_s -> COAST
  * any unbound candidate within rebind_r * h (+grow) of the slot's last pos
    rebinds the slot (same OUTPUT id even if the tracker minted a new id)
  * after coast_s with nothing                -> LOST (slot silent, keeps id)
Metrics: coverage = frames with LIVE or COAST / scored frames; coast error vs a
pseudo-GT dancer det; output-id changes (should be 0 for N=1).
"""
import json, sys, argparse, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from analyze import ghost_spots, dancer_det  # noqa


def run(rows, fps, coast_s, relax_tsu, rebind_r, warm):
    spots = ghost_spots(rows)
    slot = None  # dict(id_out, bound, pos, h, last_seen_f)
    out_id = 0
    stats = {"live": 0, "coast": 0, "lost": 0, "rebinds": 0, "out_ids": 0, "frames": 0,
             "coast_err": [], "live_err": [], "relaxed": 0}
    for r in rows:
        if r["f"] < warm:
            continue
        stats["frames"] += 1
        rep = {t["id"]: t for t in r["rep"] if t.get("c")}
        cands = dict((i, (t["c"], t["h"])) for i, t in rep.items())
        relaxed = {}
        if relax_tsu is not None:
            for t in r["trk"]:
                if t["id"] not in cands and t["tsu"] <= relax_tsu and t["hits"] >= 5:
                    relaxed[t["id"]] = (t["p"], t["h"])
        state = "lost"
        if slot is not None:
            if slot["bound"] in cands:
                slot["pos"], slot["h"] = cands[slot["bound"]]; slot["last"] = r["f"]; state = "live"
            elif slot["bound"] in relaxed:
                slot["pos"], slot["h"] = relaxed[slot["bound"]]; slot["last"] = r["f"]; state = "live"
                stats["relaxed"] += 1
            else:
                # rebind to nearest unbound candidate (reported first, then relaxed)
                best = None
                gap = r["f"] - slot["last"]
                lim = rebind_r * slot["h"] * (1 + 0.1 * gap)
                for pool in (cands, relaxed):
                    for i, (c, h) in pool.items():
                        d = math.dist(c, slot["pos"])
                        if d <= lim and (best is None or d < best[0]):
                            best = (d, i, c, h)
                    if best:
                        break
                if best:
                    slot["bound"] = best[1]; slot["pos"], slot["h"] = best[2], best[3]
                    slot["last"] = r["f"]; state = "live"; stats["rebinds"] += 1
                elif (r["f"] - slot["last"]) / fps <= coast_s:
                    state = "coast"
        else:
            if cands:
                i, (c, h) = next(iter(cands.items()))
                out_id += 1
                slot = {"id": out_id, "bound": i, "pos": c, "h": h, "last": r["f"]}
                state = "live"
        if state == "lost" and slot is not None and cands:
            # re-acquire after a lost period: keep the same output id (known N)
            i, (c, h) = next(iter(cands.items()))
            slot.update(bound=i, pos=c, h=h, last=r["f"]); state = "live"; stats["rebinds"] += 1
        stats[state] += 1
        d = dancer_det(r, spots, tau_lo=0.5)
        if d is not None and state in ("live", "coast"):
            e = math.dist(slot["pos"], d["c"]) / max(1.0, d["h"])
            stats["coast_err" if state == "coast" else "live_err"].append(e)
    stats["out_ids"] = out_id
    n = stats["frames"]

    def q(v, p):
        return round(sorted(v)[int(p * (len(v) - 1))], 3) if v else None
    return {
        "coast_s": coast_s, "relax_tsu": relax_tsu,
        "coverage": round((stats["live"] + stats["coast"]) / n, 4),
        "live": round(stats["live"] / n, 4), "coast": round(stats["coast"] / n, 4),
        "lost": round(stats["lost"] / n, 4), "rebinds": stats["rebinds"], "out_ids": out_id,
        "relaxed_frames": stats["relaxed"],
        "coast_err_med_h": q(stats["coast_err"], .5), "coast_err_p90_h": q(stats["coast_err"], .9),
        "live_err_med_h": q(stats["live_err"], .5), "live_err_p90_h": q(stats["live_err"], .9),
        "n_coast_err": len(stats["coast_err"]),
        "err_gt_075h": round(sum(e > 0.75 for e in stats["live_err"] + stats["coast_err"]) / max(1, len(stats["live_err"]) + len(stats["coast_err"])), 4),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run"); ap.add_argument("--fps", type=float, default=19.7)
    ap.add_argument("--warm", type=int, default=15)
    a = ap.parse_args()
    rows = json.loads(Path(a.run).read_text())["rows"]
    for relax in (None, 3):
        for coast in (0.0, 0.5, 1.0, 2.0, 4.0):
            print(json.dumps(run(rows, a.fps, coast, relax, 1.5, a.warm)))
