"""Synthetic ankle/wrist-marker study on REAL tracked skeletons (scratch).

Question: if a dancer wears 4 retro markers at the wrists (COCO 9,10) and
ankles (15,16) and YOLO drops for k frames, how well can the track centroid be
estimated from the visible marker constellation (1..4 markers)?

Ground truth = the tracker's own measurement definition on YOLO frames
(confidence-weighted mean of keypoints with conf > 0.3 — DancerTrack.
_compute_centroid) — so a marker-fed estimate that matches it causes no
measurement-definition step when sources switch.  Markers are simulated AT the
YOLO wrist/ankle keypoints (so YOLO keypoint noise is included → conservative).

Estimators (all use info frozen at the anchor frame t0 + markers at t0+k):
  hold      last YOLO centroid (no markers)                 [today, coast-ish]
  cvdecay   last centroid + velocity * sum(0.9^j)             [today's KF coast]
  mean      plain mean of visible markers (no model)
  offset    per-marker frozen offsets o_i = C(t0) - m_i(t0); mean_i(m_i + o_i)
  offset_ul same, but markers UNLABELLED (identical dots): slots re-associated
            frame-by-frame across the gap by Hungarian NN (realistic)
  simil     similarity (scale+rot+trans) fit of the frozen constellation onto
            the visible markers (>=2), translation-only for 1; applied to C(t0)
  blend     offset_ul for n>=2, but n==1 -> 0.5*(offset_ul) + 0.5*(cvdecay)

Errors normalised by the track's median YOLO bbox height H.
"""
from __future__ import annotations

import argparse
import itertools
import json
import pickle
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

EXT = [9, 10, 15, 16]          # L wrist, R wrist, L ankle, R ankle
NAMES = {9: "LW", 10: "RW", 15: "LA", 16: "RA"}
KC = 0.3
GAPS = [1, 2, 5, 10, 20, 40]


def centroid(k, c):
    m = c > KC
    if not m.any():
        return None
    return np.average(k[m], axis=0, weights=c[m])


def load_tracks(path):
    d = pickle.load(open(path, "rb"))
    by = defaultdict(dict)
    for r in d["rows"]:
        if r["fss"] != 0:
            continue
        C = centroid(r["kpts"].astype(np.float64), r["conf"].astype(np.float64))
        if C is None:
            continue
        vis = r["conf"][EXT] > KC
        hip_ok = bool((r["conf"][[11, 12]] > KC).all())
        hip = r["kpts"][[11, 12]].astype(np.float64).mean(0)
        by[r["id"]][r["f"]] = {"C": C, "M": r["kpts"][EXT].astype(np.float64),
                               "vis": vis, "h": float(r["bbox"][3]),
                               "hip": hip, "hip_ok": hip_ok}
    return d, by


def similarity_fit(src, dst):
    """Umeyama 2-D similarity: dst ~ s R src + t."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    var_s = (xs ** 2).sum() / len(src)
    cov = xd.T @ xs / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(2)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[1, 1] = -1
    R = U @ S @ Vt
    s = float(np.trace(np.diag(D) @ S) / max(var_s, 1e-9))
    s = float(np.clip(s, 0.6, 1.6))
    t = mu_d - s * R @ mu_s
    return s, R, t


def run(by, min_track_frames=30):
    res = defaultdict(list)       # (est, n, gap) -> [err/H]
    pair_res = defaultdict(list)  # (est, pairtype, gap) -> [err/H]
    static_bias = defaultdict(list)
    kin = {"ext_speed": [], "c_speed": [], "min_pair_dist": [], "label_swaps": [0, 0]}
    for tid, fr in by.items():
        if len(fr) < min_track_frames:
            continue
        H = float(np.median([v["h"] for v in fr.values()]))
        frames = sorted(fr)
        fset = set(frames)
        for f in frames:
            v = fr[f]
            # instantaneous: mean of visible markers vs C
            for n in range(1, 5):
                for S in itertools.combinations(range(4), n):
                    if v["vis"][list(S)].all():
                        static_bias[n].append(float(np.linalg.norm(v["M"][list(S)].mean(0) - v["C"]) / H))
            if v["vis"].all():
                d = [np.linalg.norm(v["M"][a] - v["M"][b]) / H
                     for a, b in itertools.combinations(range(4), 2)]
                kin["min_pair_dist"].append(float(min(d)))
            if f - 1 in fset:
                p = fr[f - 1]
                kin["c_speed"].append(float(np.linalg.norm(v["C"] - p["C"]) / H))
                both = v["vis"] & p["vis"]
                for i in np.nonzero(both)[0]:
                    kin["ext_speed"].append(float(np.linalg.norm(v["M"][i] - p["M"][i]) / H))
        # gap simulation: anchor t0 with all 4 visible; need contiguous YOLO
        # frames t0..t0+k so the (unlabelled) marker tracker sees every frame.
        for t0 in frames[::2]:
            a = fr[t0]
            if not a["vis"].all():
                continue
            vel = (a["C"] - fr[t0 - 1]["C"]) if (t0 - 1) in fset else np.zeros(2)
            for k in GAPS:
                if not all((t0 + j) in fset and fr[t0 + j]["vis"].all() for j in range(1, k + 1)):
                    break
                b = fr[t0 + k]
                truth = b["C"]
                hold = a["C"]
                cvd = a["C"] + vel * sum(0.9 ** j for j in range(1, k + 1))
                # unlabelled slot tracking through the gap (all 4 visible)
                slots = a["M"].copy()
                for j in range(1, k + 1):
                    cur = fr[t0 + j]["M"]
                    perm = np.random.permutation(4)
                    obs = cur[perm]
                    cost = np.linalg.norm(slots[:, None, :] - obs[None, :, :], axis=2)
                    r, c = linear_sum_assignment(cost)
                    new = slots.copy()
                    new[r] = obs[c]
                    slots = new
                # slot i now holds an observed point; check if it is truly marker i
                truth_slots = b["M"]
                swapped = int(np.sum(np.linalg.norm(slots - truth_slots, axis=1) > 1e-6))
                kin["label_swaps"][0] += int(swapped > 0)
                kin["label_swaps"][1] += 1
                offs = a["C"][None, :] - a["M"]
                for n in range(1, 5):
                    for S in itertools.combinations(range(4), n):
                        S = list(S)
                        ests = {}
                        ests["hold"] = hold
                        ests["cvdecay"] = cvd
                        ests["mean"] = b["M"][S].mean(0)
                        ests["offset"] = (b["M"][S] + offs[S]).mean(0)
                        ests["offset_ul"] = (slots[S] + offs[S]).mean(0)
                        if n >= 2:
                            s, R, t = similarity_fit(a["M"][S], b["M"][S])
                            ests["simil"] = s * R @ a["C"] + t
                        else:
                            ests["simil"] = a["C"] + (b["M"][S[0]] - a["M"][S[0]])
                        ests["blend"] = (ests["offset_ul"] if n >= 2
                                         else 0.5 * ests["offset_ul"] + 0.5 * cvd)
                        if n == 4 and a["hip_ok"] and b["hip_ok"]:
                            # 5th marker at the harness (hip midpoint)
                            oh = a["C"] - a["hip"]
                            res[("offset_ul", "hip1", k)].append(float(np.linalg.norm(b["hip"] + oh - truth) / H))
                            res[("hold", "hip1", k)].append(float(np.linalg.norm(hold - truth) / H))
                            five = np.vstack([slots, b["hip"][None]])
                            ofive = np.vstack([offs, oh[None]])
                            res[("offset_ul", "all5", k)].append(float(np.linalg.norm((five + ofive).mean(0) - truth) / H))
                            src5 = np.vstack([a["M"], a["hip"][None]]); dst5 = np.vstack([b["M"], b["hip"][None]])
                            s5, R5, t5 = similarity_fit(src5, dst5)
                            res[("simil", "all5", k)].append(float(np.linalg.norm(s5 * R5 @ a["C"] + t5 - truth) / H))
                            res[("hold", "all5", k)].append(float(np.linalg.norm(hold - truth) / H))
                        if n == 2:
                            names = {NAMES[EXT[i]] for i in S}
                            ptype = ("wrists" if names == {"LW", "RW"} else
                                     "ankles" if names == {"LA", "RA"} else "mixed")
                        for e, est in ests.items():
                            err = float(np.linalg.norm(est - truth) / H)
                            res[(e, n, k)].append(err)
                            if n == 2:
                                pair_res[(e, ptype, k)].append(err)
    return res, pair_res, static_bias, kin


def q(v, p):
    return round(float(np.percentile(v, p)), 3) if v else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pkls", nargs="+")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    np.random.seed(0)
    allres = defaultdict(list)
    allpair = defaultdict(list)
    allbias = defaultdict(list)
    allkin = {"ext_speed": [], "c_speed": [], "min_pair_dist": [], "label_swaps": [0, 0]}
    meta = {}
    for p in args.pkls:
        d, by = load_tracks(p)
        nfr = sum(len(v) for v in by.values())
        res, pair, bias, kin = run(by)
        meta[Path(p).stem] = {"frames": d["frames"], "yolo_track_frames": nfr,
                              "tracks": len(by),
                              "all4_visible_pct": round(100 * np.mean([v["vis"].all() for t in by.values() for v in t.values()]), 1) if nfr else 0}
        for k_, v in res.items():
            allres[k_] += v
        for k_, v in pair.items():
            allpair[k_] += v
        for k_, v in bias.items():
            allbias[k_] += v
        for k_ in ("ext_speed", "c_speed", "min_pair_dist"):
            allkin[k_] += kin[k_]
        allkin["label_swaps"][0] += kin["label_swaps"][0]
        allkin["label_swaps"][1] += kin["label_swaps"][1]
    out = {"meta": meta, "static_bias_mean_of_visible": {
        n: {"p50": q(v, 50), "p90": q(v, 90), "N": len(v)} for n, v in sorted(allbias.items())},
        "kinematics_per_frame_over_H": {
            "extremity_speed_p50_p90_p99": [q(allkin["ext_speed"], 50), q(allkin["ext_speed"], 90), q(allkin["ext_speed"], 99)],
            "centroid_speed_p50_p90_p99": [q(allkin["c_speed"], 50), q(allkin["c_speed"], 90), q(allkin["c_speed"], 99)],
            "min_intra_dancer_marker_dist_p5_p10_p50": [q(allkin["min_pair_dist"], 5), q(allkin["min_pair_dist"], 10), q(allkin["min_pair_dist"], 50)],
            "unlabelled_slot_swap_rate": round(allkin["label_swaps"][0] / max(1, allkin["label_swaps"][1]), 3),
            "N_gap_samples": allkin["label_swaps"][1]},
        "gap_table": {}, "pair_table": {}}
    ests = ["hold", "cvdecay", "mean", "offset", "offset_ul", "simil", "blend"]
    for n in range(1, 5):
        for k in GAPS:
            row = {}
            for e in ests:
                v = allres.get((e, n, k), [])
                if v:
                    row[e] = [q(v, 50), q(v, 90)]
            if row:
                row["N"] = len(allres.get(("hold", n, k), []))
                out["gap_table"][f"n{n}_k{k}"] = row
    for tag in ("hip1", "all5"):
        for k in GAPS:
            row = {e: [q(allres[(e, tag, k)], 50), q(allres[(e, tag, k)], 90)]
                   for e in ests if allres.get((e, tag, k))}
            if row:
                row["N"] = len(allres.get(("hold", tag, k), []))
                out["gap_table"][f"{tag}_k{k}"] = row
    for pt in ("wrists", "ankles", "mixed"):
        for k in GAPS:
            row = {e: [q(allpair[(e, pt, k)], 50), q(allpair[(e, pt, k)], 90)]
                   for e in ests if allpair.get((e, pt, k))}
            if row:
                out["pair_table"][f"{pt}_k{k}"] = row
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(out["meta"], indent=1))
    print(json.dumps(out["static_bias_mean_of_visible"]))
    print(json.dumps(out["kinematics_per_frame_over_H"], indent=1))
    print(f"{'cell':<10}" + "".join(f"{e:>14}" for e in ests) + "      N")
    for key, row in out["gap_table"].items():
        print(f"{key:<10}" + "".join(f"{('%.3f/%.3f' % tuple(row[e])) if e in row else '-':>14}" for e in ests) + f"  {row['N']:>6}")
    print("pairs (n=2) by type:")
    for key, row in out["pair_table"].items():
        print(f"{key:<10}" + "".join(f"{('%.3f/%.3f' % tuple(row[e])) if e in row else '-':>14}" for e in ests))


if __name__ == "__main__":
    main()
