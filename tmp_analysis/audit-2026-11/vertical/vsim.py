#!/usr/bin/env python3
"""Vertical-entries regime: drive the REAL post-YOLO chain (FrameProcessor.replay_gpu_cached ->
crossval -> exclusion -> cold blobs -> DancerTracker.update -> finalize -> IdentitySlots.update)
with synthetic detections + a synthetic motion gray, CPU only, 20 fps, shipped defaults.

Throwaway audit script. Writes only under its own folder.  Synthetic = logic path, not field accuracy.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import cv2
import numpy as np

from core import config as C
from core.pipeline import FrameProcessor, ProcessingSettings
from core.tracker import DancerTracker, DancerTrack
from core.foreground import CleanPlate

FPS = 20.0
HERE = os.path.dirname(os.path.abspath(__file__))

# COCO-17 body template: (dx, dy) in body heights, origin = top-centre of the body box.
_T = [(0.0, 0.07), (-0.02, 0.05), (0.02, 0.05), (-0.04, 0.06), (0.04, 0.06),
      (-0.11, 0.19), (0.11, 0.19), (-0.14, 0.37), (0.14, 0.37), (-0.15, 0.52), (0.15, 0.52),
      (-0.07, 0.52), (0.07, 0.52), (-0.07, 0.75), (0.07, 0.75), (-0.07, 0.96), (0.07, 0.96)]
TEMPLATE = np.array(_T, dtype=np.float64)
BOX_W = 0.34          # body box width, x H


@dataclass
class Scene:
    frame_w: int = 1776
    frame_h: int = 1304
    roi: tuple = (173, 401, 1304, 566)      # x, y, w, h  (the "default" project's wall ROI)
    H: float = 127.0                        # dancer height, camera px (night-take wall height)
    imgsz: int = 1280
    wall_dn: int = 45
    body_dn: int = 120
    noise: float = 2.0

    @property
    def lb(self):
        x, y, w, h = self.roi
        scale = min(self.imgsz / w, self.imgsz / h)
        nw, nh = int(w * scale), int(h * scale)
        return scale, (self.imgsz - nw) // 2, (self.imgsz - nh) // 2


@dataclass
class Body:
    """A dancer: pos(t) -> (cx, top) in camera px of an upright body; None = not in the scene."""
    pos: Callable[[float], Optional[tuple]]
    name: str = "A"
    vis_min: float = 0.30      # YOLO finds a truncated body once this share of it is inside the ROI
    p_partial: float = 1.0     # detection probability while truncated
    p_full: float = 1.0
    kconf: float = 0.85
    yolo: bool = True


def body_state(sc: Scene, b: Body, t: float):
    p = b.pos(t)
    if p is None:
        return None
    cx, top = p
    H = sc.H
    rx, ry, rw, rh = sc.roi
    x0, x1 = cx - BOX_W * H / 2, cx + BOX_W * H / 2
    y0, y1 = top, top + H
    ix0, ix1 = max(x0, rx), min(x1, rx + rw)
    iy0, iy1 = max(y0, ry), min(y1, ry + rh)
    if ix1 <= ix0 or iy1 <= iy0:
        return dict(vis=0.0, cx=cx, top=top)
    vis = ((ix1 - ix0) * (iy1 - iy0)) / ((x1 - x0) * (y1 - y0))
    k = np.column_stack([cx + TEMPLATE[:, 0] * H, top + TEMPLATE[:, 1] * H])
    inside = (k[:, 0] >= rx) & (k[:, 0] <= rx + rw) & (k[:, 1] >= ry) & (k[:, 1] <= ry + rh)
    truth = k.mean(axis=0)                       # full-skeleton centroid (what a full body reports)
    return dict(vis=vis, cx=cx, top=top, kpts=k, inside=inside, box=(ix0, iy0, ix1 - ix0, iy1 - iy0),
                truth=truth)


def make_proc(sc: Scene, *, max_dancers=2, plate=False, belt=False, logdir=None, **over):
    x, y, w, h = sc.roi
    s = ProcessingSettings(confidence=C.YOLO_CONFIDENCE, imgsz=sc.imgsz, use_fp16=False,
                           enhance_enabled=False, enhance_lite=False, enhance_force=False,
                           person_height_px=int(sc.H), osc_enabled=False, use_gpu_path=False)
    s.roi_enabled, s.roi_x, s.roi_y, s.roi_w, s.roi_h = True, x, y, w, h
    s.max_dancers = max_dancers
    s.use_ir_belt = belt
    for k, v in over.items():
        setattr(s, k, v)
    trk = DancerTracker()
    trk.set_person_height(s.person_height_px)
    DancerTrack._id_counter = 0
    proc = FrameProcessor(model=None, settings=s, tracker=trk)
    trk.set_tracking_mode(C.TRACKING_MODE)
    proc.set_tracking_mode(C.TRACKING_MODE)
    clock = {"f": 0}
    proc.set_output_clock(lambda: clock["f"] / FPS)
    trk.logger.start_session(logdir or tempfile.mkdtemp(prefix="vsim_", dir=HERE))
    rng = np.random.default_rng(7)
    if plate:
        frames = [np.clip(sc.wall_dn + rng.normal(0, sc.noise, (sc.frame_h, sc.frame_w)), 0, 255).astype(np.uint8)
                  for _ in range(12)]
        proc.set_clean_plate(CleanPlate.from_frames(frames, source="synthetic"))
    return proc, clock


def run(sc: Scene, bodies: List[Body], n_frames: int, *, seed=1, jitter=1.5, warm=80, motion=True, **mk):
    """Returns per-frame rows.  ``warm`` empty-wall frames first (MOG2 settles; cold blobs are
    suppressed for 60 frames anyway)."""
    proc, clock = make_proc(sc, **mk)
    rng = np.random.default_rng(seed)
    scale, pad_x, pad_y = sc.lb
    rx, ry, rw, rh = sc.roi
    space = dict(person_height=max(1, int(proc.settings.person_height_px * scale)), scale=scale,
                 pad_x=pad_x, pad_y=pad_y, roi_x=rx, roi_y=ry, frame_width=sc.imgsz)
    rows = []
    bank = [np.clip(sc.wall_dn + rng.normal(0, sc.noise, (rh, rw)), 0, 255).astype(np.uint8) for _ in range(24)]
    for f in range(-warm, n_frames):
        t = f / FPS
        clock["f"] = f + warm
        gray = bank[int(rng.integers(0, len(bank)))].copy()
        dets, states = [], []
        proc._last_box_confs = {}
        for b in bodies:
            st = body_state(sc, b, t) if f >= 0 else None
            states.append(st)
            if st is None or st["vis"] <= 0:
                continue
            bx, by, bw, bh = st["box"]
            cv2.rectangle(gray, (int(bx - rx), int(by - ry)), (int(bx + bw - rx), int(by + bh - ry)),
                          int(sc.body_dn), -1)
            if not b.yolo or st["vis"] < b.vis_min:
                continue
            p = b.p_full if st["vis"] >= 0.999 else b.p_partial
            if rng.random() > p:
                continue
            k = st["kpts"].copy() + rng.normal(0, jitter, (17, 2))
            k[:, 0] = np.clip(k[:, 0], rx, rx + rw)
            k[:, 1] = np.clip(k[:, 1], ry, ry + rh)
            conf = np.where(st["inside"], b.kconf, 0.05)
            box = np.array([(bx - rx) * scale + pad_x, (by - ry) * scale + pad_y, bw * scale, bh * scale])
            kl = np.column_stack([(k[:, 0] - rx) * scale + pad_x, (k[:, 1] - ry) * scale + pad_y])
            dets.append((kl, conf.astype(np.float64), box))
            proc._last_box_confs[proc._bbox_conf_key(box)] = 0.3 + 0.5 * st["vis"]
        n_raw = len(dets)
        # the two steps _run_yolo_and_track does before the shared chain
        proc._guard_person_height(dets, 1.0 / scale)
        dets = proc._filter_duplicate_detections(dets, effective_person_height=space["person_height"])
        space["person_height"] = max(1, int(proc.settings.person_height_px * scale))
        timing = {}
        proc.replay_gpu_cached(dets, space, gray if motion else None, sc.frame_w, sc.frame_h, f + warm, timing)
        if f < 0:
            continue
        trk = proc.tracker
        rows.append(dict(
            f=f, t=t, n_raw=n_raw, n_det=len(dets),
            xval={k[9:]: v for k, v in timing.items() if k.startswith("crossval_kept") and v}
            | ({"rej": timing["crossval_rejected"]} if timing.get("crossval_rejected") else {}),
            vis=[None if s is None else round(s["vis"], 3) for s in states],
            truth=[None if (s is None or s["vis"] <= 0) else s["truth"] for s in states],
            tracks=[dict(id=x.track_id, hits=x.hits, tsu=x.time_since_update, wu=round(x._warmup_score, 1),
                         fss=x._frames_since_skeleton, emit=trk.last_emit_reasons.get(x.track_id),
                         src=x._feed_src, est=x.is_established) for x in trk.tracks],
            dormant=[(d.track_id, d.exited_from_edge, d.age) for d in trk._dormant],
            out=[dict(id=o.track_id, st=o.slot_state, x=float(o.smoothed_centroid[0]),
                      y=float(o.smoothed_centroid[1]), h=float(o.bbox[3])) for o in proc.last_emitted],
            ev=list(proc._slots.events) if proc._slots is not None else [],
            fg=None if proc.last_fg is None else (proc.last_fg.valid, len(proc.last_fg.blobs)),
            ph=proc.settings.person_height_px,
            slots=[] if proc._slots is None else [dict(
                id=s.sid, st=s.state, hold=s.hold_s, rep=round(s.rep_t, 2),
                travel=round(s.travel / sc.H, 2), vel=[round(float(v) / sc.H, 2) for v in s.vel],
                static=s.static_since is not None, key=s.key) for s in proc._slots.slots],
            spots=[] if proc._slots is None else [(round(a), round(b), k) for a, b, _h, _t, k in proc._slots._spots],
        ))
    proc.tracker.logger.close()
    proc._rows_logdir = proc.tracker.logger
    return rows, proc


# ----------------------------------------------------------------------------- metrics
def first(rows, pred):
    for r in rows:
        if pred(r):
            return r
    return None


def last(rows, pred):
    out = None
    for r in rows:
        if pred(r):
            out = r
    return out


def entry_report(sc, rows, i=0, label=""):
    fp = first(rows, lambda r: (r["vis"][i] or 0) > 0)
    fd = first(rows, lambda r: r["n_raw"] > 0)
    fk = first(rows, lambda r: any(k.startswith("kept") for k in r["xval"]))
    ft = first(rows, lambda r: any(t["emit"] == "ok" for t in r["tracks"]))
    fe = first(rows, lambda r: len(r["out"]) > 0)
    ffull = first(rows, lambda r: (r["vis"][i] or 0) >= 0.999)
    d = lambda a, b: None if (a is None or b is None) else round(a["t"] - b["t"], 2)
    err = None
    if fe is not None and fe["truth"][i] is not None:
        o = fe["out"][0]
        err = round(float(np.hypot(o["x"] - fe["truth"][i][0], o["y"] - fe["truth"][i][1])) / sc.H, 2)
    ids = sorted({o["id"] for r in rows for o in r["out"]})
    tids = sorted({t["id"] for r in rows for t in r["tracks"]})
    print(f"[{label}] first pixel t={None if fp is None else fp['t']:.2f}s | first YOLO det +{d(fd, fp)}s "
          f"(vis {None if fd is None else fd['vis'][i]}) | first det kept by crossval +{d(fk, fp)}s | "
          f"tracker reports +{d(ft, fp)}s | OSC emits +{d(fe, fp)}s (= +{d(fe, fd)}s after first det, "
          f"vis at emit {None if fe is None else fe['vis'][i]}, err {err} h) | fully in +{d(ffull, fp)}s | "
          f"slot ids {ids} tracker ids {tids}")
    return dict(fp=fp, fd=fd, ft=ft, fe=fe)


def exit_report(sc, rows, i=0, label=""):
    ld = last(rows, lambda r: r["n_raw"] > 0)
    lv = last(rows, lambda r: (r["vis"][i] or 0) > 0)
    le = last(rows, lambda r: len(r["out"]) > 0)
    d = lambda a, b: None if (a is None or b is None) else round(a["t"] - b["t"], 2)
    states = []
    for r in rows:
        if ld is not None and r["f"] > ld["f"] and r["out"]:
            s = r["out"][0]["st"]
            if not states or states[-1][0] != s:
                states.append([s, 0])
            states[-1][1] += 1
    # tracker: when did the last track leave the active list, and how was it classified
    lt = last(rows, lambda r: len(r["tracks"]) > 0)
    dorm = None
    for r in rows:
        if r["dormant"]:
            dorm = r["dormant"][0]
            break
    held = None
    if le is not None and le["out"]:
        o = le["out"][0]
        rx, ry, rw, rh = sc.roi
        held = (round(o["x"]), round(o["y"]), "dist to ROI top/bottom px:",
                round(o["y"] - ry), round(ry + rh - o["y"]))
    print(f"[{label}] last YOLO det t={None if ld is None else ld['t']:.2f}s (vis {None if ld is None else ld['vis'][i]}) | "
          f"body fully out +{d(lv, ld)}s | last OSC point +{d(le, ld)}s after last det = "
          f"{None if (le is None or lv is None) else round(le['t'] - lv['t'], 2)}s after fully out | "
          f"hold states {states} | last held point {held} | tracker track alive until +{d(lt, ld)}s, "
          f"dormant (id, edge_exit, age)={dorm}")
    return dict(ld=ld, lv=lv, le=le)


def timeline(rows, every=10, i=0, upto=None):
    for r in rows[::every]:
        if upto is not None and r["t"] > upto:
            break
        tr = " ".join(f"T{t['id']}(h{t['hits']} tsu{t['tsu']} wu{t['wu']} fss{t['fss']} {t['emit']} {t['src']})"
                      for t in r["tracks"])
        out = " ".join(f"S{o['id']}:{o['st']}@({o['x']:.0f},{o['y']:.0f})" for o in r["out"])
        ev = " ".join(f"{e['ev']}:{e.get('why', e.get('key'))}" for e in r["ev"])
        print(f"  t={r['t']:5.2f} vis={r['vis']} dets={r['n_raw']}->{r['n_det']} xv={r['xval']} | {tr} | OUT {out} | {ev}")
