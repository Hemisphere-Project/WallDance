#!/usr/bin/env python3
"""Score a replay timeline or a live log against a per-frame presence truth (Plan Cordée rev 2, M1).

The truth (``tests/truth0810/<stem>.npz``, built by ``truth_build.py``) says, for every frame of a take, which
dancers are on the wall, where (the centroid of her foreground group), the group's box, and a state:
0 own group · 1 shared group (contact) · 2 no group (coasted, interpolated) · 3 uncertain (a > 100 px jump).
It was made by background subtraction and hand-declared entries and exits, independent of the detector.

A box or point is **on** a dancer when its centre lies in her group's box grown by 0.35 H, or within 0.75 H of
her centroid (H = the take's median body height). One box credits one dancer (Hungarian, highest score first);
in contact, a point on the shared group counts for both (the Cordée KPI rule).

Inputs
  replay   ``<stem>.<tag>.jsonl`` written by a counter-analysis style replay: per frame ``low`` (every YOLO box
           down to conf 0.02, ``[x0, y0, x1, y1, conf, n_kp]`` in portrait px), ``ref`` (boxes kept by the app's
           filters), ``ora`` (a reference detector, optional), ``tracks``, ``emitted``. The shipped confidence
           comes from ``<stem>.<tag>.info.json`` next to it, or ``--conf``.
  live     ``live_<project>_<take>.csv``: the live session's event log aligned to the take's frames.

Outputs: per dancer-frame arrays (``score_replay``, ``score_live``), the per-take summary (``take_summary``),
the counter-analysis tables (``tables``) and a check against a reference summary (``compare``).

  truth_score.py replay <truth.npz> <stem.tag.jsonl> [--conf 0.15] [--out z.npz]
  truth_score.py summary --truth DIR --replays DIR [--tag ship] [--live DIR] [--json out.json] [--check ref.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TRUTH_DIR = HERE / "truth0810"

# the 13 takes of 08/10, in the order of the evening
ORDER = ["jeudi0809_mur__slot_1_20261008_183954", "jeudi0809_mur__slot_2_20261008_200907",
         "jeudi0809_mur__slot_3_20261008_201808", "jeudi0809_mur__slot_4_20261008_205648",
         "jeudi0809_mur__slot_5_20261008_210402", "jeudi0809_mur2__slot_1_20261008_213126",
         "jeudi0809_mur2__slot_2_20261008_213650", "jeudi0809_mur2__slot_3_20261008_214816",
         "jeudi0809_mur2__slot_4_20261008_215322", "jeudi0809_mur2__slot_5_20261008_215608",
         "jeudi0809_mur2__slot_6_20261008_215704", "jeudi0809_mur2__slot_7_20261008_220951",
         "jeudi0809_mur2__slot_8_20261008_221927"]

# wall polygons, full-size portrait px, per frame size (w, h)
WALL = {
    (1200, 1920): [(355, 180), (955, 185), (1090, 1910), (140, 1910), (265, 900)],
    (1296, 1776): [(340, 45), (950, 65), (1065, 1775), (140, 1775), (225, 800)],
    (1260, 1824): [(320, 80), (930, 85), (1055, 1800), (95, 1800), (200, 900)],
}
# roof line y per frame size: a dancer within 100 px of it is "on the roof"
ROOF = {(1200, 1920): 185, (1296, 1776): 60, (1260, 1824): 85}
SLOT_STATES = {None: 0, "live": 1, "belt": 2, "fg": 3, "coasting": 4, "weak": 5}
LIVE_STATES = {"": 0, "lost": 0, "live": 1, "belt": 2, "fg": 3, "coasting": 4, "weak": 5}


def short(stem: str) -> str:
    return stem.replace("jeudi0809_", "").replace("__slot_", " ").split("_2026")[0]


def in_poly(x, y, poly) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


# ── the truth ────────────────────────────────────────────────────────────────

class Truth:
    """A take's presence truth. ``at(f)`` -> [(dancer, (x, y, state), box, bg, clip)] sorted by dancer."""

    def __init__(self, stem, frames, size, h_take, f, d, x, y, st, box, bg=None, clip=None, meta=None, plan=None):
        self.stem = stem
        self.frames = int(frames)
        self.size = (int(size[0]), int(size[1]))
        self.h_take = float(h_take)
        self.meta = meta or {}
        self.plan = plan or {}
        order = np.lexsort((d, f))
        self.f, self.d = np.asarray(f)[order].astype(np.int64), np.asarray(d)[order].astype(np.int64)
        self.x, self.y = np.asarray(x)[order].astype(np.float64), np.asarray(y)[order].astype(np.float64)
        self.st = np.asarray(st)[order].astype(np.int64)
        self.box = np.asarray(box)[order].astype(np.float64).reshape(-1, 4)
        n = len(self.f)
        self.bg = (np.asarray(bg)[order].astype(np.float64) if bg is not None else np.zeros(n))
        self.clip = (np.asarray(clip)[order].astype(np.float64) if clip is not None else np.zeros(n))
        self._start = np.searchsorted(self.f, np.arange(self.frames + 1), side="left")

    @property
    def names(self):
        return [chr(65 + int(k)) for k in np.unique(self.d)]

    def at(self, frame: int):
        if frame < 0 or frame >= self.frames:
            return []
        a, b = self._start[frame], self._start[frame + 1]
        return [(int(self.d[i]), (float(self.x[i]), float(self.y[i]), int(self.st[i])), self.box[i].tolist(),
                 float(self.bg[i]), float(self.clip[i])) for i in range(a, b)]

    @classmethod
    def load(cls, path):
        z = np.load(path, allow_pickle=False)
        # positions are kept as tenths of a pixel, exactly as the truth was rounded
        return cls(str(z["stem"]), int(z["frames"]), z["size"].tolist(), float(z["h_take"]), z["f"], z["d"],
                   z["x10"] / 10.0, z["y10"] / 10.0, z["st"], z["box"], z["bg"], z["clip"],
                   json.loads(str(z["meta"])), json.loads(str(z["plan"])))

    def save(self, path):
        np.savez_compressed(path, stem=np.array(self.stem), frames=np.int64(self.frames), size=np.array(self.size, np.int64),
                            h_take=np.float64(self.h_take), f=self.f.astype(np.int32), d=self.d.astype(np.int8),
                            x10=np.round(self.x * 10).astype(np.int32), y10=np.round(self.y * 10).astype(np.int32),
                            st=self.st.astype(np.int8), box=self.box.astype(np.int16), bg=self.bg, clip=self.clip,
                            meta=np.array(json.dumps(self.meta)), plan=np.array(json.dumps(self.plan)))


def load_truth(stem_or_path) -> Truth:
    p = Path(stem_or_path)
    if not p.suffix:
        p = TRUTH_DIR / f"{stem_or_path}.npz"
    return Truth.load(p)


# ── matching ─────────────────────────────────────────────────────────────────

def on(px, py, pos, box, H, grow=0.35, rad=0.75) -> bool:
    """Is the point (px, py) on the dancer at pos whose group box is box?"""
    if box[0] - grow * H <= px <= box[2] + grow * H and box[1] - grow * H <= py <= box[3] + grow * H:
        return True
    return math.hypot(px - pos[0], py - pos[1]) <= rad * H


def assign(points, dancers, H) -> dict:
    """points [(x, y, score)], dancers [(pos, box)] -> {dancer index: point index}, one point per dancer,
    the highest score first when several fit."""
    if not points or not dancers:
        return {}
    from scipy.optimize import linear_sum_assignment
    cost = np.full((len(dancers), len(points)), 1e6)
    for i, (pos, box) in enumerate(dancers):
        for j, (x, y, s) in enumerate(points):
            if on(x, y, pos, box, H):
                cost[i, j] = math.hypot(x - pos[0], y - pos[1]) - 1000.0 * s
    ri, ci = linear_sum_assignment(cost)
    return {int(i): int(j) for i, j in zip(ri, ci) if cost[i, j] < 1e5}


# ── replay ───────────────────────────────────────────────────────────────────

def replay_conf(jsonl_path, default=0.15) -> float:
    p = Path(jsonl_path)
    info = p.with_name(p.name[: -len(".jsonl")] + ".info.json")
    if info.exists():
        try:
            return float(json.loads(info.read_text())["config"]["confidence"])
        except (KeyError, ValueError, TypeError):
            pass
    return default


def score_replay(T: Truth, rows, conf: float) -> dict:
    """rows: iterable of per-frame replay records. Returns the per dancer-frame columns, the per-frame
    ``f_*`` columns, ``fp`` (boxes >= conf on the wall on no dancer: frame, x, y, h, conf), H and conf."""
    H = T.h_take
    poly = WALL.get(T.size)
    keys = ("frame", "dancer", "x", "y", "state", "low_conf", "low_nk", "det", "ref", "ora_conf", "trk", "trk_yolo",
            "emit", "emit_state", "bg", "clip", "speed", "dist", "pair_boxes", "n_present")
    cols = {k: [] for k in keys}
    frames = {k: [] for k in ("frame", "n_true", "n_det", "n_ref", "n_emit", "n_emit_on", "n_emit_off", "n_trk")}
    fp_rows = []
    prev = {}
    for r in rows:
        f = int(r["frame"])
        present = T.at(f)
        dz = [((p[0], p[1]), b) for _, p, b, _, _ in present]
        low = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, b[4], b[5], b[3] - b[1]) for b in r.get("low") or []]
        low_pts = [(x, y, c) for x, y, c, _, _ in low]
        m_low = assign(low_pts, dz, H)
        m_ref = assign([(q["c"][0], q["c"][1], q.get("conf") or 0.0) for q in r.get("ref") or []], dz, H)
        ora = r.get("ora")
        ora_pts = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, b[4]) for b in ora] if ora is not None else None
        m_ora = assign(ora_pts, dz, H) if ora_pts is not None else {}
        tr = r.get("tracks") or []
        m_trk = assign([(t["centroid"][0], t["centroid"][1], 1.0) for t in tr], dz, H)
        m_ty = assign([(t["centroid"][0], t["centroid"][1], 1.0) for t in tr if t.get("src") == "yolo"], dz, H)
        em = (r.get("emitted") or {}).get("tracks", [])
        em_pts = [(t["centroid"][0], t["centroid"][1], 1.0) for t in em]
        m_em = assign(em_pts, dz, H)
        n_det_on = 0
        for i, (d, p, b, bgv, clipv) in enumerate(present):
            others = [q for j, (_, q, _, _, _) in enumerate(present) if j != i]
            dist = min((math.hypot(p[0] - q[0], p[1] - q[1]) for q in others), default=-1.0)
            pv = prev.get(d)
            sp = math.hypot(p[0] - pv[0], p[1] - pv[1]) if pv is not None else 0.0
            prev[d] = p
            lc = low[m_low[i]][2] if i in m_low else 0.0
            emit_on = i in m_em
            if not emit_on and p[2] == 1:
                emit_on = any(on(x, y, (p[0], p[1]), b, H) for x, y, _ in em_pts)
            pair_boxes = sum(1 for x, y, c in low_pts if c >= conf and on(x, y, (p[0], p[1]), b, H))
            es = SLOT_STATES.get(em[m_em[i]].get("state"), 6) if i in m_em else 0
            vals = (f, d, p[0], p[1], p[2], lc, low[m_low[i]][3] if i in m_low else 0, lc >= conf, i in m_ref,
                    (ora_pts[m_ora[i]][2] if i in m_ora else 0.0) if ora_pts is not None else -1.0,
                    i in m_trk, i in m_ty, emit_on, es, bgv, clipv, sp, dist, pair_boxes, len(present))
            for k, v in zip(keys, vals):
                cols[k].append(v)
            n_det_on += lc >= conf
        used = set(m_low.values())
        for j, (x, y, c, nk, h) in enumerate(low):
            if c >= conf and j not in used and (poly is None or in_poly(x, y, poly)) \
                    and not any(on(x, y, pp, bb, H) for pp, bb in dz):
                fp_rows.append((f, x, y, h, c))
        frames["frame"].append(f)
        frames["n_true"].append(len(present))
        frames["n_det"].append(n_det_on)
        frames["n_ref"].append(len(m_ref))
        frames["n_emit"].append(len(em))
        frames["n_emit_on"].append(len(set(m_em.values())))
        frames["n_emit_off"].append(sum(1 for x, y, _ in em_pts if not any(on(x, y, pp, bb, H) for pp, bb in dz)))
        frames["n_trk"].append(len(m_trk))
    Z = {k: np.array(v) for k, v in cols.items()}
    Z.update({"f_" + k: np.array(v) for k, v in frames.items()})
    Z["fp"] = np.array(fp_rows, np.float32).reshape(-1, 5)
    Z["H"] = H
    Z["conf"] = conf
    return Z


def read_jsonl(path):
    with open(path) as fh:
        for line in fh:
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError:
                    break


# ── live ─────────────────────────────────────────────────────────────────────

def score_live(T: Truth, csv_path, replay_rows=None) -> dict:
    """The live session's aligned event log against the truth. The log holds the emitted slots and their state;
    the position is the bound track's centroid (exact for a ``live`` slot measured that frame, a proxy otherwise)."""
    H = T.h_take
    rep = {}
    for r in replay_rows or []:
        rep[int(r["frame"])] = (len(r.get("ref") or []), (r.get("emitted") or {}).get("reported", 0))
    cols = {k: [] for k in ("frame", "dancer", "state", "emit", "emit_state", "emit_exact", "processed", "n_present",
                            "dist", "yolo")}
    fr = {k: [] for k in ("frame", "processed", "t", "n_true", "n_det", "n_emit", "n_emit_on", "n_emit_off",
                          "n_emit_off_exact", "rep_det", "rep_emit")}
    with open(csv_path) as fh:
        for row in csv.DictReader(fh):
            i = int(row["take_frame"])
            present = T.at(i)
            dz = [((p[0], p[1]), b) for _, p, b, _, _ in present]
            proc = row["processed"] == "1"
            pts = []
            if proc:
                for k in ("s1", "s2"):
                    if row.get(f"{k}_emitted") == "1" and row.get(f"{k}_x_rot") not in ("", None):
                        pts.append((float(row[f"{k}_x_rot"]), float(row[f"{k}_y_rot"]),
                                    LIVE_STATES.get(row[f"{k}_state"], 6), row[f"{k}_pos_src"] == "key_measured"))
            n_emit = int(row["n_emitted"] or 0) if proc else 0
            ypts = []
            if proc and row.get("tracks"):
                for tk in row["tracks"].split():
                    q = tk.split(":")
                    if len(q) >= 5 and q[3] == "yolo" and q[4] == "0":
                        ypts.append((float(q[1]), float(q[2]), 1.0))
            m_y = assign(ypts, dz, H)
            m = assign([(x, y, 1.0) for x, y, _, _ in pts], dz, H)
            for k, (d, p, b, _, _) in enumerate(present):
                others = [q for j, (_, q, _, _, _) in enumerate(present) if j != k]
                dist = min((math.hypot(p[0] - q[0], p[1] - q[1]) for q in others), default=-1.0)
                hit = k in m
                if not hit and p[2] == 1:
                    hit = any(on(x, y, (p[0], p[1]), b, H) for x, y, _, _ in pts)
                for key, v in (("frame", i), ("dancer", d), ("state", p[2]), ("emit", hit),
                               ("emit_state", pts[m[k]][2] if k in m else 0),
                               ("emit_exact", pts[m[k]][3] if k in m else False), ("processed", proc),
                               ("n_present", len(present)), ("dist", dist), ("yolo", k in m_y)):
                    cols[key].append(v)
            off = [q for q in pts if not any(on(q[0], q[1], pp, bb, H) for pp, bb in dz)]
            for key, v in (("frame", i), ("processed", proc), ("t", float(row["t_rel_s"] or 0)),
                           ("n_true", len(present)), ("n_det", int(row["n_det"] or 0) if proc else -1),
                           ("n_emit", n_emit), ("n_emit_on", len(set(m.values()))), ("n_emit_off", len(off)),
                           ("n_emit_off_exact", sum(1 for q in off if q[3])),
                           ("rep_det", rep.get(i, (-1, -1))[0]), ("rep_emit", rep.get(i, (-1, -1))[1])):
                fr[key].append(v)
    L = {k: np.array(v) for k, v in cols.items()}
    L.update({"f_" + k: np.array(v) for k, v in fr.items()})
    L["H"] = H
    return L


# ── summary ──────────────────────────────────────────────────────────────────

def runs(mask):
    out, s = [], None
    for i, v in enumerate(mask):
        if v and s is None:
            s = i
        if not v and s is not None:
            out.append((s, i - 1))
            s = None
    if s is not None:
        out.append((s, len(mask) - 1))
    return out


def _share(m, sel=None):
    if sel is not None:
        m = m[sel]
    return round(float(m.mean()), 3) if len(m) else None


def take_summary(T: Truth, Z: dict | None, L: dict | None = None) -> dict:
    """The per-take record of the counter-analysis (content, detector, replay, live)."""
    meta = T.meta
    n = T.frames
    r = {k: meta.get(k) for k in ("start", "fps", "exp_ms", "gain_db", "enhance", "gamma", "clahe")}
    r["frames"] = n
    if Z is not None:
        H = float(Z["H"])
        r["H"] = round(H)
        nt = Z["f_n_true"]
        r["n_true"] = {int(k): round(float((nt == k).mean()), 3) for k in np.unique(nt)}
        fr, dn, st, y = Z["frame"], Z["dancer"], Z["state"], Z["y"]
        r["dancer_frames"] = int(len(fr))
    if Z is not None and len(Z["frame"]):
        roof = y < ROOF[T.size] + 100
        oval = Z["clip"] >= 0.5
        contact = st == 1
        ok = st < 2
        still = np.zeros(len(fr), bool)
        still_runs, cross, pos = [], 0, {}
        for d in np.unique(dn):
            m = np.nonzero(dn == d)[0]
            x_, y_, f_ = Z["x"][m], Z["y"][m], fr[m]
            disp = np.full(len(m), 1e9)
            if len(m) > 20:
                disp[20:] = np.hypot(x_[20:] - x_[:-20], y_[20:] - y_[:-20])
                same = (f_[20:] - f_[:-20]) == 20
                disp[20:][~same] = 1e9
            sm = disp < 0.15 * H
            still[m] = sm
            still_runs += [(int(f_[a]), int(f_[b])) for a, b in runs(sm) if b - a + 1 >= 180]
            pos[int(d)] = dict(zip(f_.tolist(), x_.tolist()))
        if len(pos) >= 2:
            ks = sorted(pos)[:2]
            common = sorted(set(pos[ks[0]]) & set(pos[ks[1]]))
            dx = np.array([pos[ks[0]][f] - pos[ks[1]][f] for f in common])
            sg = np.sign(dx[np.abs(dx) > 0.5 * H])
            cross = int((np.diff(sg) != 0).sum())
        two = Z["n_present"] == 2
        r["roof_share"] = _share(roof)
        r["oval_share"] = _share(oval, ~roof) if (~roof).any() else None
        r["contact_share_of_2"] = _share(contact, two) if two.any() else None
        r["contact_episodes"] = len([1 for a, b in runs((nt == 2) & np.isin(np.arange(n), fr[contact])) if b - a >= 10])
        r["crossings"] = cross
        r["still_10s"] = still_runs
        r["truth_uncertain_share"] = _share(st >= 2)
        r["bg_under_dancer"] = ([round(float(np.percentile(Z["bg"][~roof], q))) for q in (10, 50, 90)]
                                if (~roof).any() else None)
        det = Z["det"].astype(bool)
        low = Z["low_conf"]
        ref = Z["ref"].astype(bool)
        em = Z["emit"].astype(bool)
        base = ok & ~roof

        def f(m):
            return round(float(det[m].mean()), 3) if m.any() else None
        r["det"] = {"all": f(np.ones(len(fr), bool)), "wall": f(base), "roof": f(roof), "oval": f(base & oval),
                    "not_oval": f(base & ~oval), "contact": f(base & contact), "apart": f(base & ~contact & two),
                    "solo": f(base & ~two), "still": f(base & still), "moving": f(base & ~still),
                    "n": {"wall": int(base.sum()), "oval": int((base & oval).sum()), "not_oval": int((base & ~oval).sum()),
                          "contact": int((base & contact).sum()), "apart": int((base & ~contact & two).sum()),
                          "solo": int((base & ~two).sum()), "still": int((base & still).sum()), "roof": int(roof.sum())}}
        lb = low[base]
        r["low"] = {"none": _share(lb == 0), "0.02-0.05": _share((lb > 0) & (lb < 0.05)),
                    "0.05-0.15": _share((lb >= 0.05) & (lb < 0.15)), "0.15-0.4": _share((lb >= 0.15) & (lb < 0.4)),
                    ">=0.4": _share(lb >= 0.4)}
        oc = Z["ora_conf"]
        ran = (oc >= 0) & base
        r["ora"] = ({">=0.15": _share(oc[ran] >= 0.15), "ship_same_frames": _share(det[ran]), "n": int(ran.sum())}
                    if ran.any() else None)
        r["ref_after_filter"] = _share(ref[base])
        m2 = nt == 2
        r["two"] = ({"frames": int(m2.sum()),
                     "det_0_1_2": [round(float((Z["f_n_det"][m2] == k).mean()), 3) for k in (0, 1, 2)],
                     "emit_on_0_1_2": [round(float((Z["f_n_emit_on"][m2] == k).mean()), 3) for k in (0, 1, 2)]}
                    if m2.any() else None)
        r["replay"] = {"emit_on": _share(em), "trk_yolo": _share(Z["trk_yolo"].astype(bool)),
                       "det&emit": _share(det & em), "det&!emit": _share(det & ~em), "!det&emit": _share(~det & em),
                       "!det&!emit": _share(~det & ~em), "points": int(Z["f_n_emit"].sum()),
                       "points_off": int(Z["f_n_emit_off"].sum())}
        r["fp_boxes_per_frame"] = round(len(Z["fp"]) / n, 4)
    if L is not None:
        r["live"] = live_summary(L, n)
    return r


def live_summary(L: dict, n: int) -> dict:
    pr = L["f_processed"].astype(bool)
    wall_s = float(L["f_t"][-1]) if len(L["f_t"]) else 0.0
    lv = {"wall_s": round(wall_s, 1), "frame_s": round(n / 20.0, 1), "processed": round(float(pr.mean()), 4),
          "sent_0_1_2": [round(float((L["f_n_emit"][pr] == k).mean()), 3) for k in (0, 1, 2)]}
    if not len(L["frame"]):
        return lv
    cp = L["processed"].astype(bool)
    e = L["emit"].astype(bool)
    yl = L["yolo"].astype(bool)
    lv["emit_on"] = round(float(e[cp].mean()), 3)
    lv["yolo_track"] = round(float(yl[cp].mean()), 3)
    lv["2x2"] = {"yolo&emit": round(float((yl & e)[cp].mean()), 3), "yolo&!emit": round(float((yl & ~e)[cp].mean()), 3),
                 "!yolo&emit": round(float((~yl & e)[cp].mean()), 3), "!yolo&!emit": round(float((~yl & ~e)[cp].mean()), 3)}
    es = L["emit_state"][cp & e]
    lv["emit_states"] = ({k: round(float((es == v).mean()), 3) for k, v in (("live", 1), ("belt", 2), ("fg", 3), ("coasting", 4))}
                         if len(es) else {})
    lv["points"] = int(L["f_n_emit"][pr].sum())
    lv["points_off"] = int(L["f_n_emit_off"][pr].sum())
    lv["points_off_exact"] = int(L["f_n_emit_off_exact"][pr].sum())
    for k in (1, 2):
        sel = pr & (L["f_n_true"] == k)
        if sel.any():
            lv[f"n{k}"] = {"frames": int(sel.sum()),
                           "sent_0_1_2": [round(float((L["f_n_emit"][sel] == q).mean()), 3) for q in (0, 1, 2)],
                           "on_0_1_2": [round(float((L["f_n_emit_on"][sel] == q).mean()), 3) for q in (0, 1, 2)]}
    # longest wall-clock time a dancer on the wall had no point on her (stalls included)
    t = L["f_t"]
    holes = []
    for d in np.unique(L["dancer"]):
        m = L["dancer"] == d
        fd = L["frame"][m]
        ed = e[m] & cp[m]
        for a, b in runs(~ed):
            holes.append(float(t[min(len(t) - 1, fd[b] + 1)] - t[fd[a]]))
    holes.sort(reverse=True)
    lv["hole_max_s"] = round(holes[0], 1) if holes else 0.0
    lv["holes_ge_1s"] = int(sum(1 for h in holes if h >= 1.0))
    lv["holes_ge_5s"] = int(sum(1 for h in holes if h >= 5.0))
    lv["uncovered_s"] = round(float(sum(holes)), 1)
    lv["dancer_s"] = round(float(sum(t[min(len(t) - 1, L["frame"][L["dancer"] == d][-1])] - t[L["frame"][L["dancer"] == d][0]]
                                     for d in np.unique(L["dancer"]))), 1)
    return lv


def kpi(S: dict) -> dict:
    """Pooled over the takes: the detector measure (share of on-wall dancer-frames with a box at the shipped
    confidence), the emitted point on her, false boxes per frame and points on nobody."""
    wall = sum(r["det"]["n"]["wall"] for r in S.values() if r.get("det"))
    det = sum(r["det"]["wall"] * r["det"]["n"]["wall"] for r in S.values() if r.get("det") and r["det"]["wall"] is not None)
    frames = sum(r["frames"] for r in S.values() if r.get("det"))
    fp = sum(r["fp_boxes_per_frame"] * r["frames"] for r in S.values() if r.get("det"))
    df = sum(r["dancer_frames"] for r in S.values() if r.get("replay"))
    em = sum(r["replay"]["emit_on"] * r["dancer_frames"] for r in S.values() if r.get("replay"))
    pts = sum(r["replay"]["points"] for r in S.values() if r.get("replay"))
    off = sum(r["replay"]["points_off"] for r in S.values() if r.get("replay"))
    return {"detector_wall": round(det / wall, 3) if wall else None, "wall_dancer_frames": wall,
            "fp_boxes_per_frame": round(fp / frames, 4) if frames else None,
            "replay_point_on_her": round(em / df, 3) if df else None,
            "replay_points_on_nobody": round(off / pts, 3) if pts else None}


def tables(S: dict) -> str:
    """The counter-analysis tables, markdown."""
    def P(v):
        return "–" if v is None else (f"{100 * v:.0f} %" if isinstance(v, float) else str(v))
    o = ["### content",
         "| take | start | frames | dancers on the wall (share of frames) | contact (of 2-dancer frames) | crossings | roof | still >= 10 s | in the oval |",
         "|---|---|---|---|---|---|---|---|---|"]
    for s, r in S.items():
        o.append(f"| {short(s)} | {r['start']} | {r['frames']} | {r.get('n_true')} | {P(r.get('contact_share_of_2'))} | "
                 f"{r.get('crossings', '–')} | {P(r.get('roof_share'))} | {len(r.get('still_10s', []))} | {P(r.get('oval_share'))} |")
    o += ["", "### detector, share of dancer-frames with a box on her",
          "| take | exp ms / dB | enh | wall | in oval | elsewhere | contact | apart | solo | still | roof | any box >= 0.02 | reference / same frames |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s, r in S.items():
        d = r.get("det")
        if not d:
            continue
        q = r.get("ora") or {}
        o.append(f"| {short(s)} | {r['exp_ms']} / {r['gain_db']} | {'on' if r['enhance'] else 'off'} g{r['gamma']} c{r['clahe']} | "
                 f"{P(d['wall'])} | {P(d['oval'])} | {P(d['not_oval'])} | {P(d['contact'])} | {P(d['apart'])} | {P(d['solo'])} | "
                 f"{P(d['still'])} | {P(d['roof'])} | {P(1 - r['low']['none'])} | {P(q.get('>=0.15'))} / {P(q.get('ship_same_frames'))} |")
    o += ["", "### two dancers on the wall: how many the detector sees, how many have a point (replay), live points",
          "| take | 2-dancer frames | detector 0 / 1 / 2 | replay points on 0 / 1 / 2 | live sent 0 / 1 / 2 | live on-dancer 0 / 1 / 2 |",
          "|---|---|---|---|---|---|"]
    for s, r in S.items():
        t = r.get("two")
        lv = (r.get("live") or {}).get("n2")
        if t:
            o.append(f"| {short(s)} | {t['frames']} | {' / '.join(P(v) for v in t['det_0_1_2'])} | "
                     f"{' / '.join(P(v) for v in t['emit_on_0_1_2'])} | "
                     f"{' / '.join(P(v) for v in lv['sent_0_1_2']) if lv else '–'} | {' / '.join(P(v) for v in lv['on_0_1_2']) if lv else '–'} |")
    o += ["", "### live output against truth",
          "| take | wall s / frame s | a point on her | points on no dancer | longest time without a point | holes >= 1 s | uncovered / dancer-seconds | YOLO-fed track on her: live / replay |",
          "|---|---|---|---|---|---|---|---|"]
    for s, r in S.items():
        lv = r.get("live")
        if lv and "emit_on" in lv:
            rep = (r.get("replay") or {}).get("trk_yolo")
            o.append(f"| {short(s)} | {lv['wall_s']} / {lv['frame_s']} | {P(lv['emit_on'])} | {lv['points_off']} of {lv['points']} "
                     f"({100 * lv['points_off'] / max(1, lv['points']):.0f} %) | {lv['hole_max_s']} s | {lv['holes_ge_1s']} | "
                     f"{lv['uncovered_s']} / {lv['dancer_s']} s | {P(lv['yolo_track'])} / {P(rep)} |")
    o += ["", "### replay 2x2 (share of dancer-frames)", "| take | detected + point | detected, no point | not detected, point | neither |",
          "|---|---|---|---|---|"]
    for s, r in S.items():
        q = r.get("replay")
        if q:
            o.append(f"| {short(s)} | {P(q['det&emit'])} | {P(q['det&!emit'])} | {P(q['!det&emit'])} | {P(q['!det&!emit'])} |")
    o += ["", "### false boxes", "| take | boxes >= conf on the wall on no dancer, per frame |", "|---|---|"]
    for s, r in S.items():
        if "fp_boxes_per_frame" in r:
            o.append(f"| {short(s)} | {r['fp_boxes_per_frame']:.3f} |")
    return "\n".join(o) + "\n"


# ── comparison with a reference summary ─────────────────────────────────────

def compare(S: dict, ref: dict, tol_share=0.01, tol_rel=0.01):
    """Every number of S against ref: shares (0..1 floats) within tol_share, counts and other numbers within
    tol_rel relative (and 1 absolute). Returns [(take, key path, got, want)] of the differences."""
    out = []

    def walk(a, b, path, take):
        if isinstance(b, dict):
            for k, v in b.items():
                walk(a.get(k) if isinstance(a, dict) else None, v, path + [str(k)], take)
        elif isinstance(b, list):
            if not isinstance(a, list) or len(a) != len(b):
                out.append((take, ".".join(path), a, b))
                return
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, path + [str(i)], take)
        elif isinstance(b, bool) or isinstance(b, str) or b is None:
            if a != b:
                out.append((take, ".".join(path), a, b))
        else:
            if not isinstance(a, (int, float)) or isinstance(a, bool):
                out.append((take, ".".join(path), a, b))
                return
            if isinstance(b, float) and 0.0 <= b <= 1.0 and not path[-1] in ("fps",):
                if abs(a - b) > tol_share + 1e-9:
                    out.append((take, ".".join(path), a, b))
            elif abs(a - b) > max(1.0, tol_rel * abs(b)) + 1e-9:
                out.append((take, ".".join(path), a, b))

    for take, rb in ref.items():
        if take not in S:
            out.append((take, "(take)", None, "present"))
            continue
        walk(json.loads(json.dumps(S[take])), rb, [], take)
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────

def summarise(truth_dir, replays_dir, tag="ship", live_dir=None, stems=None):
    S = {}
    for stem in stems or ORDER:
        tp = Path(truth_dir) / f"{stem}.npz"
        rp = Path(replays_dir) / f"{stem}.{tag}.jsonl" if replays_dir else None
        if not tp.exists():
            continue
        T = Truth.load(tp)
        Z = None
        rows = None
        if rp is not None and rp.exists():
            rows = list(read_jsonl(rp))
            Z = score_replay(T, rows, replay_conf(rp))
        L = None
        if live_dir:
            proj, take = stem.split("__")
            lp = Path(live_dir) / f"live_{proj}_{take}.csv"
            if lp.exists():
                L = score_live(T, lp, rows)
        if Z is None and L is None:
            continue
        S[stem] = take_summary(T, Z, L)
    return S


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("replay")
    a1.add_argument("truth")
    a1.add_argument("jsonl")
    a1.add_argument("--conf", type=float, default=None)
    a1.add_argument("--out", default=None)
    a2 = sub.add_parser("summary")
    a2.add_argument("--truth", default=str(TRUTH_DIR))
    a2.add_argument("--replays", default=None)
    a2.add_argument("--tag", default="ship")
    a2.add_argument("--live", default=None)
    a2.add_argument("--stems", nargs="*", default=None)
    a2.add_argument("--json", default=None)
    a2.add_argument("--check", default=None, help="reference summary.json: exit 1 on a difference over 1 point")
    a2.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "replay":
        T = load_truth(a.truth)
        conf = a.conf if a.conf is not None else replay_conf(a.jsonl)
        Z = score_replay(T, read_jsonl(a.jsonl), conf)
        if a.out:
            np.savez_compressed(a.out, **Z)
        r = take_summary(T, Z)
        print(json.dumps({"stem": T.stem, "conf": conf, "H": r.get("H"), "dancer_frames": r.get("dancer_frames"),
                          "det_wall": (r.get("det") or {}).get("wall"), "emit_on": (r.get("replay") or {}).get("emit_on"),
                          "fp_boxes_per_frame": r.get("fp_boxes_per_frame")}))
        return 0
    S = summarise(a.truth, a.replays, a.tag, a.live, a.stems)
    if a.json:
        Path(a.json).write_text(json.dumps(S, indent=1))
    if not a.quiet:
        print(tables(S))
        print("pooled:", json.dumps(kpi(S)))
    if a.check:
        ref = json.loads(Path(a.check).read_text())
        if a.stems:
            ref = {k: v for k, v in ref.items() if k in a.stems}
        diffs = compare(S, ref)
        for d in diffs[:60]:
            print("DIFF", d)
        print(f"check against {a.check}: {len(diffs)} difference(s) over 1 point")
        return 1 if diffs else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
