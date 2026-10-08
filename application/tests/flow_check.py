#!/usr/bin/env python3
"""Operator-flow check (2026-10-07): the whole flow as the operator lives it, headless and on recorded takes.

1. CALIBRATE on an empty take with the REAL ``runtime.calibration_flows.CalibrationFlows`` (scene window,
   empty-wall YOLO check, snapshot capture) driven by a real ``FrameProcessor`` (tests/replay.py) and a fake
   recorder that plays the take (looping, like the app) and reports its .meta exposure / gain.
2. APPLY: replay each take WHOLE with the settings Calibrate left (gamma, CLAHE, MOG2 var + scale) and the
   snapshot it captured, the project config otherwise (ROI, confidence, tracker, slots); and the BASELINE: the
   same takes with the config as is and no snapshot.
3. EVENTS per take (N = 1): holes >= 1 s, jumps (the emitted point moves > 1 body height within <= 3 frames,
   or > 2 within <= 10), ghost frames (> 1 point), id switches; an x(t) / y(t) plot per take; image strips
   around the worst events and a thumbnail sheet; an HTML report next to the images.

  python tests/flow_check.py --project mur25m-night-common --empty slot_1_20261006_211624.avi:100 \\
      --takes slot_2_20261006_211831.avi,slot_2_20261006_214531.avi,... --label s1 \\
      --out /data/WallDance/tmp_analysis/flowcheck/<run> [--engine-dir models/dev37] [--imgsz 1280]

Several --label runs can share one --out: the baseline replays are cached there, the report lists them all.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay as R  # noqa: E402  (bootstraps the CUDA libs; src/ on the path)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

FPS = 20.0
STATE_BGR = {"live": (80, 200, 80), "belt": (230, 200, 40), "fg": (235, 235, 235), "coasting": (40, 150, 255),
             "weak": (60, 220, 230)}
STATE_HEX = {"live": "#3fae4f", "belt": "#22b8d8", "fg": "#9aa0a6", "coasting": "#ff8c1a", "weak": "#e0c63a"}


# --------------------------------------------------------------------------------------------------------- #
# 1. Calibrate with the real CalibrationFlows
# --------------------------------------------------------------------------------------------------------- #
def playback_camera(meta: dict):
    cfg, cam = meta.get("config"), meta.get("camera") or {}
    if not isinstance(cfg, dict) or (cam.get("source") or cfg.get("camera_source")) != "ids":
        return None
    try:
        return {"exposure_us": float(cfg["ids_exposure_us"]), "gain_db": float(cfg["ids_gain_db"])}
    except (KeyError, TypeError, ValueError):
        return None


def calibrate(project: str, take: str, start: int, config: dict, model: str, imgsz: int, out: Path,
              max_frames: int = 6000) -> dict:
    from core.config import PLATE_CAPTURE_FRAMES
    from runtime.calibration_flows import CalibrationFlows

    rec_dir = R.PROJECTS_DIR / project / "recordings"
    video = rec_dir / take
    meta = json.loads(Path(str(video) + ".meta").read_text()) if Path(str(video) + ".meta").exists() else {}
    proc = R._build_processor(config, model, imgsz, use_gpu_path=True, use_trt=True)
    clock = R._attach_frame_clock(proc, FPS)
    xf = R.input_transform_for(config)
    applied = {"var": proc.get_motion_var_threshold(), "scale": float(config.get("mog2_scale", 0.0) or 0.0)}
    set_var, set_scale = proc.set_motion_var_threshold, proc.set_motion_scale

    def rec_var(v):
        applied["var"] = float(v)
        set_var(v)

    def rec_scale(s):
        applied["scale"] = float(s)
        set_scale(s)
    proc.set_motion_var_threshold, proc.set_motion_scale = rec_var, rec_scale

    raw = {"frame": None}
    plate_box = {}

    def capture_plate(source):
        proc.start_plate_capture(int(PLATE_CAPTURE_FRAMES),
                                 lambda plate, err: plate_box.update(plate=plate, err=err, source=source))

    cap0 = cv2.VideoCapture(str(video))
    W, H = int(cap0.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap0.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_take = int(cap0.get(cv2.CAP_PROP_FRAME_COUNT))
    cap0.release()
    rx, ry, rw, rh = (int(config.get("roi_x", 0)), int(config.get("roi_y", 0)),
                      int(config.get("roi_w", W)), int(config.get("roi_h", H)))
    flows = CalibrationFlows(
        processor=proc, enhancer=proc.enhancer, tracker=proc.tracker, settings=proc.settings,
        recorder=SimpleNamespace(is_playing=True, playback_camera=playback_camera(meta)),
        camera=SimpleNamespace(state=SimpleNamespace(is_open=False)), unified_camera=None, use_unified=False,
        models=SimpleNamespace(_model_loaded=True, model=proc.model),
        cameras=MagicMock(ids_exposure_us=0.0, ids_gain_db=0.0), configs=MagicMock(),
        ui=SimpleNamespace(available=False),
        last_raw_frame=lambda: raw["frame"],
        roi_source_size=lambda: (W, H),
        get_effective_roi=lambda w, h: (rx, ry, rw, rh),
        reset_sensitivity_anchor=lambda **k: None,
        sync_mask_ui=lambda: None, request_reprocess=lambda: None, imgsz_change=lambda v: None,
        capture_plate=capture_plate)
    flows.calibration_state = json.loads(json.dumps(config.get("calibration_state") or {}))
    before = {"gamma": float(proc.enhancer.gamma), "clahe": float(proc.enhancer.clahe_clip),
              "var": applied["var"], "scale": applied["scale"], "confidence": float(proc.settings.confidence)}

    def frames():
        cap = cv2.VideoCapture(str(video))
        while True:                                   # the app's playback loops the take
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            for _ in range(max(1, n_take - start)):
                ok, fr = cap.read()
                if not ok:
                    break
                yield xf.apply(fr)

    gen = frames()
    t0 = time.time()
    fr = next(gen)                   # the take is already playing when the operator presses CALIBRATE
    raw["frame"] = fr
    proc.process(fr, need_preview=False, frame_number=0)
    flows._cb_calibrate()
    i = 1
    stages = []
    while (flows._calibrating or proc._plate_capture is not None) and i < max_frames:
        fr = next(gen)
        raw["frame"] = fr
        clock["frame"] = i
        tracks, _enh, _timing, _lat = proc.process(fr, need_preview=False, frame_number=i)
        if flows._calibrating:
            stage = "wall" if flows._wall_check is not None else ("servo" if flows._servo else "scene")
            if not stages or stages[-1][0] != stage:
                stages.append((stage, i))
            flows._step_calibration(tracks, 50.0)
        i += 1
    wall = flows._wall_result
    res = {"take": take, "start": start, "frames_used": i, "wall_s": round(time.time() - t0),
           "stages": stages, "before": before,
           "after": {"gamma": round(float(proc.enhancer.gamma), 4), "clahe": float(proc.enhancer.clahe_clip),
                     "var": applied["var"], "scale": applied["scale"],
                     "confidence": float(proc.settings.confidence)},
           "exposure_gain": playback_camera(meta),
           "wall_check": {"clean": getattr(wall, "clean", None), "summary": wall.summary() if wall else None,
                          "log": wall.log_line() if wall else None},
           "calibration_state": flows.calibration_state}
    plate = plate_box.get("plate")
    if plate is not None:
        pdir = R.PROJECTS_DIR / project / "plates"
        pdir.mkdir(parents=True, exist_ok=True)
        ppath = pdir / f"flowcheck_{out.name}_{Path(take).stem}_{start}.npz"
        plate.save(ppath)
        res["plate"] = {"path": str(ppath), "size": list(plate.frame_size), "sigma": round(float(plate.sigma), 3)}
    else:
        res["plate"] = {"error": plate_box.get("err", "no snapshot captured")}
    return res


# --------------------------------------------------------------------------------------------------------- #
# 2. Replays
# --------------------------------------------------------------------------------------------------------- #
def replay(project: str, take: str, sets: list, timeline: Path, engine_dir: str, imgsz: int) -> dict:
    if timeline.exists():
        return {"cached": True}
    slot = int(take.split("_")[1])
    cmd = [sys.executable, str(Path(__file__).resolve().parent / "replay.py"), "--project", project,
           "--slot", str(slot), "--video", str(R.PROJECTS_DIR / project / "recordings" / take),
           "--trt", "--engine-dir", engine_dir, "--imgsz", str(imgsz), "--quality", "--internal",
           "--timeline", str(timeline)]
    for kv in sets:
        cmd += ["--set", kv]
    t0 = time.time()
    pr = subprocess.run(cmd, capture_output=True, text=True)
    if pr.returncode != 0:
        raise RuntimeError(f"replay {take} failed: {(pr.stderr or pr.stdout)[-800:]}")
    logs = [l for l in pr.stdout.splitlines() if l.startswith(("[HeightGuard]", "[Foreground]"))][:6]
    return {"wall_s": round(time.time() - t0), "log": logs}


# --------------------------------------------------------------------------------------------------------- #
# 3. Events
# --------------------------------------------------------------------------------------------------------- #
def load_rows(timeline: Path):
    return sorted(json.loads(timeline.read_text()), key=lambda r: r["frame"])


def emitted(r):
    out = []
    for e in (r.get("emitted") or {}).get("tracks") or []:
        c = e.get("centroid") or [e["bbox"][0] + e["bbox"][2] / 2, e["bbox"][1] + e["bbox"][3] / 2]
        out.append({"id": e["id"], "x": float(c[0]), "y": float(c[1]), "h": float(e["bbox"][3]),
                    "state": e.get("state") or "?", "bbox": e["bbox"]})
    return out


def analyse(rows) -> dict:
    n = len(rows)
    pts = [emitted(r) for r in rows]
    refs = [[x for x in (r.get("ref") or []) if isinstance(x, dict)] for r in rows]
    # holes
    holes, start = [], None
    for i, p in enumerate(pts + [[{"id": -1}]]):
        if not p and start is None:
            start = i
        elif p and start is not None:
            if i - start >= FPS:
                seen = sum(1 for j in range(start, min(i, n)) if refs[j])
                # nobody in view at the hole's start, and the size of what YOLO saw during it: a take's
                # start hole is mostly an empty frame, then the operator walking out from the camera
                # (600-750 px boxes for a ~125 px wall dancer) -- not a dancer entry (PLAN_25M §C.14)
                empty = next((j for j in range(start, min(i, n)) if refs[j]), min(i, n)) - start
                hs = sorted(x["h"] for j in range(start, min(i, n)) for x in refs[j] if x.get("h"))
                holes.append({"frame": start, "t": round(start / FPS, 2), "dur_s": round((i - start) / FPS, 2),
                              "yolo_saw_someone": round(seen / max(1, i - start), 2),
                              "startup": start < 2 * FPS, "empty_s": round(empty / FPS, 2),
                              "h_med": round(hs[len(hs) // 2]) if hs else None})
            start = None
    # jumps per id
    track = {}
    jumps = []
    for i, p in enumerate(pts):
        for e in p:
            hist = track.setdefault(e["id"], [])
            hist.append((i, e["x"], e["y"], e["h"], e["state"]))
            h = max(20.0, float(np.median([q[3] for q in hist[-20:]])))
            for lag, lim in ((3, 1.0), (10, 2.0)):
                prev = [q for q in hist[:-1] if i - q[0] <= lag]
                if prev:
                    d = max(math.hypot(e["x"] - q[1], e["y"] - q[2]) for q in prev) / h
                    if d > lim:
                        if not jumps or i - jumps[-1]["frame"] > 20:
                            jumps.append({"frame": i, "t": round(i / FPS, 2), "id": e["id"], "dist_h": round(d, 2),
                                          "state": e["state"], "x": round(e["x"]), "y": round(e["y"])})
                        elif d > jumps[-1]["dist_h"] and jumps[-1]["id"] == e["id"]:
                            jumps[-1].update(dist_h=round(d, 2))
                        break
    ghost_frames = [i for i, p in enumerate(pts) if len(p) > 1]
    ghosts = []
    for i in ghost_frames:
        if not ghosts or i - ghosts[-1]["end"] > 10:
            ghosts.append({"frame": i, "t": round(i / FPS, 2), "end": i, "n": 1})
        else:
            ghosts[-1]["end"] = i
            ghosts[-1]["n"] += 1
    switches, last = 0, None
    for p in pts:
        if len(p) == 1:
            if last is not None and p[0]["id"] != last:
                switches += 1
            last = p[0]["id"]
    states = {}
    for p in pts:
        for e in p:
            states[e["state"]] = states.get(e["state"], 0) + 1
    return {"frames": n, "coverage": round(sum(1 for p in pts if p) / max(1, n), 4),
            "holes": holes, "longest_hole_s": max([h["dur_s"] for h in holes], default=0.0),
            "jumps": jumps, "ghost_frames": len(ghost_frames), "ghost_episodes": ghosts,
            "id_switches": switches, "ids": sorted(track), "states": states}


def quality(rows) -> dict:
    import output_quality
    man = {"name": "flow", "start": 0, "frames": 10 ** 9, "warmup": 15, "fps": FPS, "expected_count": 1,
           "reference": {"min_conf": 0.5, "tol_h": 0.75, "exclude_spots": []}}
    q = output_quality.compare_streams(rows, man, fps=FPS).get("emitted", {})
    cont, qual = q.get("continuity", {}), q.get("quality", {})
    return {"on_dancer": cont.get("on_dancer"), "coasting_share": qual.get("coasting_share"),
            "jitter_rest_pct": qual.get("jitter_rest_pct"), "lag_ms": qual.get("lag_ms")}


# --------------------------------------------------------------------------------------------------------- #
# 4. Images
# --------------------------------------------------------------------------------------------------------- #
def read_frames(video: Path, idxs):
    out = {}
    cap = cv2.VideoCapture(str(video))
    for i in sorted(set(idxs)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if ok:
            out[i] = fr
    return out


_DISPLAY_CLAHE = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))


def overlay(fr, row, roi, label=""):
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY) if fr.ndim == 3 else fr
    img = cv2.cvtColor(_DISPLAY_CLAHE.apply(g), cv2.COLOR_GRAY2BGR)     # display only: dark IR made visible
    if roi:
        x, y, w, h = roi
        cv2.rectangle(img, (x, y), (x + w, y + h), (180, 180, 180), 2)
    fgr = row.get("fg") or {}
    for b in fgr.get("b") or []:                       # snapshot foreground blobs
        cv2.circle(img, (int(b[0]), int(b[1])), max(6, int(math.sqrt(max(1, b[2])) / 2)), (200, 60, 200), 2)
    for rf in row.get("ref") or []:                    # YOLO detections (conf >= 0.5)
        if isinstance(rf, dict) and rf.get("c"):
            cx, cy, h = rf["c"][0], rf["c"][1], rf.get("h") or 100
            cv2.rectangle(img, (int(cx - 0.2 * h), int(cy - 0.5 * h)), (int(cx + 0.2 * h), int(cy + 0.5 * h)),
                          (0, 220, 255), 2)
    for e in emitted(row):
        col = STATE_BGR.get(e["state"], (255, 0, 255))
        r = max(8, int(0.12 * e["h"]))
        cv2.circle(img, (int(e["x"]), int(e["y"])), r, col, -1)
        cv2.circle(img, (int(e["x"]), int(e["y"])), r + 3, (0, 0, 0), 2)
        cv2.putText(img, f"D{e['id']} {e['state']}", (int(e["x"]) + r + 4, int(e["y"])),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, col, 3)
    cv2.putText(img, label, (12, 48), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 4)
    return img


def save_jpeg(img, path: Path, width: int, max_kb: int = 150):
    h = int(img.shape[0] * width / img.shape[1])
    small = cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)
    for q in (80, 70, 60, 50, 40):
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, q])
        if len(buf) <= max_kb * 1024:
            break
    path.write_bytes(buf.tobytes())


def strip(video, rows, center, roi, path: Path, title: str):
    idxs = [max(0, min(len(rows) - 1, center + d)) for d in (-10, -5, 0, 5, 10)]
    frs = read_frames(video, idxs)
    tiles = []
    for d, i in zip((-10, -5, 0, 5, 10), idxs):
        if i in frs:
            tiles.append(cv2.resize(overlay(frs[i], rows[i], roi, f"{i / FPS:.2f}s ({d:+d})"),
                                    (480, int(480 * frs[i].shape[0] / frs[i].shape[1]))))
    if tiles:
        save_jpeg(np.hstack(tiles), path, 1500)
        return path.name
    return None


def sheet(video, rows, roi, path: Path, every_s: float = 10.0):
    idxs = list(range(0, len(rows), int(every_s * FPS)))[:24]
    frs = read_frames(video, idxs)
    tiles = [cv2.resize(overlay(frs[i], rows[i], roi, f"{i / FPS:.0f}s"), (320, int(320 * frs[i].shape[0] /
                                                                                 frs[i].shape[1])))
             for i in idxs if i in frs]
    if not tiles:
        return None
    cols = 6
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    grid = np.vstack([np.hstack(tiles[k:k + cols]) for k in range(0, len(tiles), cols)])
    save_jpeg(grid, path, 1500)
    return path.name


def plot(rows_by_variant: dict, events_by_variant: dict, path: Path, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    nv = len(rows_by_variant)
    fig, axes = plt.subplots(nv, 1, figsize=(11, 2.3 * nv), sharex=True, squeeze=False)
    for ax, (name, rows) in zip(axes[:, 0], rows_by_variant.items()):
        t = np.arange(len(rows)) / FPS
        rx = [(i / FPS, x["c"][0]) for i, r in enumerate(rows) for x in (r.get("ref") or [])
              if isinstance(x, dict) and x.get("c")]
        if rx:
            ax.scatter([a for a, _ in rx], [b for _, b in rx], s=2, c="#c8c8c8", label="YOLO (conf >= 0.5)")
        for st, col in STATE_HEX.items():
            pts = [(i / FPS, e["x"]) for i, r in enumerate(rows) for e in emitted(r) if e["state"] == st]
            if pts:
                ax.scatter([a for a, _ in pts], [b for _, b in pts], s=3, c=col, label=st)
        ev = events_by_variant.get(name) or {}
        for h in ev.get("holes", []):
            ax.axvspan(h["t"], h["t"] + h["dur_s"], color="#ff5050", alpha=0.15)
        for j in ev.get("jumps", []):
            ax.axvline(j["t"], color="#d03030", lw=0.8)
        ax.set_ylabel(f"{name}\nx (px)")
        ax.set_xlim(0, max(1.0, len(t) / FPS))
    axes[0, 0].legend(loc="upper right", fontsize=7, ncol=6, markerscale=3)
    axes[-1, 0].set_xlabel("time (s)  -- red bands: holes >= 1 s, red lines: jumps")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=72)
    plt.close(fig)
    return path.name


# --------------------------------------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--empty", required=True, help="empty take file name[:start frame]")
    ap.add_argument("--takes", required=True, help="comma-separated take file names")
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--engine-dir", default=os.environ.get("WD_ENGINE_DIR", str(R.REPO / "models" / "dev37")))
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-images", type=int, default=40)
    ap.add_argument("--calibrate-only", action="store_true", help="run Calibrate, print the result, stop")
    a = ap.parse_args()
    os.environ["WD_ENGINE_DIR"] = a.engine_dir
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    config = R._latest_config(a.project)
    if config is None:
        raise SystemExit(f"no config for {a.project}")
    model = a.model or config.get("model", "yolo11x-pose")
    imgsz = a.imgsz or int(config.get("yolo_imgsz", 1280))
    empty, _, st = a.empty.partition(":")
    print(f"[flow] {a.label}: Calibrate on {empty} (from frame {st or 0}) ...", flush=True)
    cal = calibrate(a.project, empty, int(st or 0), config, model, imgsz, out)
    print(f"[flow] Calibrate -> {cal['after']}  wall check: {cal['wall_check']['log']}  plate: {cal['plate']}",
          flush=True)
    if a.calibrate_only:
        print(json.dumps(cal, indent=1, default=str))
        return
    roi = ((int(config["roi_x"]), int(config["roi_y"]), int(config["roi_w"]), int(config["roi_h"]))
           if config.get("roi_enabled") else None)
    cal_sets = [f"gamma={cal['after']['gamma']}", f"clahe_clip={cal['after']['clahe']}",
                f"mog2_var_threshold={cal['after']['var']}", f"mog2_scale={cal['after']['scale']}",
                "fg_enabled=true"]
    if cal["plate"].get("path"):
        cal_sets.append(f"fg_plate={cal['plate']['path']}")
    takes_out = []
    n_img = 0
    for take in [t for t in a.takes.split(",") if t]:
        video = R.PROJECTS_DIR / a.project / "recordings" / take
        stem = Path(take).stem
        tl_base = out / f"base_{stem}.json"
        tl_cal = out / f"{a.label}_{stem}.json"
        print(f"[flow] {take}: baseline ...", flush=True)
        rb = replay(a.project, take, [], tl_base, a.engine_dir, imgsz)
        print(f"[flow] {take}: after Calibrate ({a.label}) ...", flush=True)
        rc = replay(a.project, take, cal_sets, tl_cal, a.engine_dir, imgsz)
        rows = {"baseline": load_rows(tl_base), f"after {a.label}": load_rows(tl_cal)}
        ev = {k: analyse(v) for k, v in rows.items()}
        qa = {k: quality(v) for k, v in rows.items()}
        img_plot = plot(rows, ev, out / f"{a.label}_{stem}_xt.png", f"{take}: x(t), baseline vs after Calibrate")
        img_sheet = sheet(video, rows[f"after {a.label}"], roi, out / f"{a.label}_{stem}_sheet.jpg")
        strips = []
        worst = sorted([("jump", j["dist_h"], j) for j in ev[f"after {a.label}"]["jumps"]] +
                       [("hole", h["dur_s"], h) for h in ev[f"after {a.label}"]["holes"]] +
                       [("ghost", g["n"] / FPS, g) for g in ev[f"after {a.label}"]["ghost_episodes"]],
                       key=lambda x: -x[1])
        for kind, mag, e in worst:
            if n_img >= a.max_images or len(strips) >= 6:
                break
            name = strip(video, rows[f"after {a.label}"], e["frame"], roi,
                         out / f"{a.label}_{stem}_{kind}_{e['frame']}.jpg", f"{kind} at {e['t']} s")
            if name:
                strips.append({"kind": kind, "t": e["t"], "mag": round(mag, 2), "img": name, "event": e})
                n_img += 1
        takes_out.append({"take": take, "events": ev, "quality": qa, "plot": img_plot, "sheet": img_sheet,
                          "strips": strips, "replay": {"baseline": rb, "calibrated": rc}})
        print(f"[flow] {take}: cov base {ev['baseline']['coverage']} -> {ev[f'after {a.label}']['coverage']}, "
              f"jumps {len(ev['baseline']['jumps'])} -> {len(ev[f'after {a.label}']['jumps'])}, ghost frames "
              f"{ev['baseline']['ghost_frames']} -> {ev[f'after {a.label}']['ghost_frames']}", flush=True)
    res = {"label": a.label, "project": a.project, "empty": a.empty, "calibrate": cal, "takes": takes_out,
           "config_roi": roi, "imgsz": imgsz}
    (out / f"{a.label}.json").write_text(json.dumps(res, indent=1, default=str))
    print(f"[flow] wrote {out / (a.label + '.json')}")


if __name__ == "__main__":
    main()
