#!/usr/bin/env python3
"""Continuity-audit driver (throwaway; read-only w.r.t. the repo).

build : run the real GPU (PyTorch) show path once over a long span at a LOW
        YOLO confidence floor, capturing per frame the RAW (pre-duplicate-filter)
        detections + their box confs + the tracker space + gray shape/md5.
        Small cache (no grays) -> stored in the scratch dir.
replay: re-decode the video (FFV1, lossless) to rebuild the motion gray exactly
        as _run_yolo_and_track does, filter cached dets by tau, run the
        duplicate filter, then FrameProcessor.replay_gpu_cached (the same
        _post_yolo_chain the live path runs).  Records a rich per-frame log:
        reported tracks (OSC-faithful), every internal track, dormant ids, raw
        det stats (incl. below-tau), gate/exclusion counters, cold blobs.
"""
from __future__ import annotations
import argparse, hashlib, json, pickle, sys, time, copy
from pathlib import Path

APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "tests"))
import replay  # noqa: E402  (re-execs once for LD_LIBRARY_PATH)
sys.path.insert(0, str(APP / "src"))
import cv2  # noqa: E402
import numpy as np  # noqa: E402


def load_cfg(args):
    if args.scenario:
        import scoring
        sc = scoring.load_scenario(args.scenario)
        cfg = replay.scenario_config(sc)
        project, slot = sc["project"], sc["slot"]
    else:
        cfg = replay._latest_config(args.project)
        project, slot = args.project, args.slot
    if args.config_json:
        cfg = json.loads(Path(args.config_json).read_text())
    replay.apply_overrides(cfg, args.sets)
    video = args.video or str(replay._find_recording(project, slot))
    return cfg, video


def build(args):
    cfg, video = load_cfg(args)
    cfg = dict(cfg)
    tau_live = cfg.get("confidence")
    cfg["confidence"] = args.floor
    model = cfg.get("model", "yolo11x-pose")
    imgsz = int(cfg.get("yolo_imgsz", 1280))
    proc = replay._build_processor(cfg, model, imgsz, load_model=True,
                                   use_gpu_path=True, use_trt=False)
    proc.tracker.reset()
    proc.tracker.logger.start_session(str(Path(args.out).parent / "buildlog"))
    frames = []
    stash = {}
    orig_dup = proc._filter_duplicate_detections

    def dup_wrap(dets, effective_person_height=None):
        bc = dict(proc._last_box_confs)
        raw = []
        for k, c, b in dets:
            raw.append((k.copy(), c.copy(), np.asarray(b, dtype=np.float64).copy(),
                        float(bc.get(proc._bbox_conf_key(b), -1.0))))
        stash["raw"] = raw
        return orig_dup(dets, effective_person_height=effective_person_height)
    proc._filter_duplicate_detections = dup_wrap

    def cap_hook(dets, space, gray, ow, oh):
        frames.append({
            "raw": stash.pop("raw", []),
            "space": {"person_height": int(space.person_height), "scale": float(space.scale),
                      "pad_x": float(space.pad_x), "pad_y": float(space.pad_y),
                      "roi_x": int(space.roi_x), "roi_y": int(space.roi_y),
                      "frame_width": int(space.frame_width)},
            "gshape": None if gray is None else tuple(gray.shape),
            "gmd5": None if gray is None else hashlib.md5(gray.tobytes()).hexdigest(),
            "ow": int(ow), "oh": int(oh),
        })
    proc._cache_capture_gpu = cap_hook
    cap = cv2.VideoCapture(video)
    if args.start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, args.start)
    t0 = time.time(); n = 0
    while args.frames is None or n < args.frames:
        ok, fr = cap.read()
        if not ok:
            break
        proc.process(fr, need_preview=False, frame_number=n)
        n += 1
        if n % 250 == 0:
            print(f"  built {n} frames  {(time.time()-t0)/n*1000:.0f} ms/f", flush=True)
            if n % 1000 == 0:
                _dump(args.out, video, cfg, tau_live, args, frames)
    cap.release(); proc.tracker.logger.close()
    _dump(args.out, video, cfg, tau_live, args, frames)
    print(f"built {n} frames in {time.time()-t0:.0f}s -> {args.out}")


def _dump(out, video, cfg, tau_live, args, frames):
    with open(out, "wb") as f:
        pickle.dump({"video": video, "start": args.start, "floor": args.floor,
                     "tau_live": tau_live, "build_cfg": cfg, "frames": frames}, f,
                    protocol=pickle.HIGHEST_PROTOCOL)


def replay_cache(args):
    cfg, video = load_cfg(args)
    cache = pickle.load(open(args.cache, "rb"))
    video = cache["video"]
    tau = float(cfg.get("confidence", cache["tau_live"]))
    if tau < cache["floor"] - 1e-9:
        sys.exit(f"tau {tau} below cache floor {cache['floor']}")
    model = cfg.get("model", "yolo11x-pose")
    imgsz = int(cfg.get("yolo_imgsz", 1280))
    proc = replay._build_processor(cfg, model, imgsz, load_model=False)
    if args.app_order and "tracker_max_age" in cfg:
        # app.py:1126 applies tracker_max_age AFTER tracking_mode (replay.py does
        # it before, so motion_first clobbers it to 60) -> mimic the live app.
        proc.tracker.max_age = cfg["tracker_max_age"]
    apply_patches(proc, args)
    proc.tracker.reset()
    logdir = Path(args.log_dir); logdir.mkdir(parents=True, exist_ok=True)
    proc.tracker.logger.start_session(str(logdir))
    # record cold blobs after the gate
    cold = {}
    orig_gate = proc._gate_cold_blobs

    def gate_wrap(blobs):
        out = orig_gate(blobs)
        cold["n_in"] = len(blobs or []); cold["n"] = len(out or [])
        cold["blobs"] = [[round(float(b.centroid[0]), 1), round(float(b.centroid[1]), 1),
                          round(float(b.bbox[2]), 1), round(float(b.bbox[3]), 1)] for b in (out or [])][:4]
        return out
    proc._gate_cold_blobs = gate_wrap
    cap = cv2.VideoCapture(video)
    start = cache["start"] + args.offset
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    rows = []
    t0 = time.time()
    frames = cache["frames"][args.offset:]
    if args.frames:
        frames = frames[:args.frames]
    stride = max(1, args.stride)
    md5_bad = 0
    for i, fr in enumerate(frames):
        ok, img = cap.read()
        if not ok:
            break
        if stride > 1 and i % stride:
            continue
        gray = None
        if fr["gshape"] is not None:
            sp = fr["space"]; gh, gw = fr["gshape"]
            sub = img[sp["roi_y"]:sp["roi_y"] + gh, sp["roi_x"]:sp["roi_x"] + gw] if cfg.get("roi_enabled") else img
            gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
            if i < 5 or i % 500 == 0:
                if hashlib.md5(gray.tobytes()).hexdigest() != fr["gmd5"]:
                    md5_bad += 1
        sp = dict(fr["space"])
        sp["person_height"] = max(1, int(int(cfg.get("person_height_px", sp["person_height"])) * sp["scale"])) \
            if "person_height_px" in cfg else sp["person_height"]
        raw = fr["raw"]
        kept = [(k.copy(), c.copy(), b.copy()) for (k, c, b, bc) in raw if bc >= tau]
        proc._last_box_confs = {proc._bbox_conf_key(b): bc for (k, c, b, bc) in raw if bc >= tau}
        n_raw = len(kept)
        dets = proc._filter_duplicate_detections(kept, effective_person_height=sp["person_height"])
        n_dup = len(dets)
        timing = {}
        cold.clear()
        tracks = proc.replay_gpu_cached(dets, sp, gray, fr["ow"], fr["oh"], i // stride, timing)
        trk = proc.tracker
        s = sp["scale"]
        def to_orig(x, y):
            return [round((x - sp["pad_x"]) / s + sp["roi_x"], 1), round((y - sp["pad_y"]) / s + sp["roi_y"], 1)]
        # top raw dets incl. below-tau (what YOLO saw)
        top = sorted(raw, key=lambda r: -r[3])[:3]
        rows.append({
            "f": i // stride, "abs": start + i,
            "rep": [{"id": int(t.track_id),
                     "c": [round(float(t.smoothed_centroid[0]), 1), round(float(t.smoothed_centroid[1]), 1)] if t.smoothed_centroid is not None else None,
                     "h": round(float(t.bbox[3]), 1),
                     "br": bool(t.is_bridged), "fss": t.frames_since_skeleton} for t in tracks],
            "trk": [{"id": int(t.track_id), "tsu": int(t.time_since_update), "hits": int(t.hits),
                     "age": int(t.age), "wu": round(float(t._warmup_score), 2),
                     "conf": bool(t.warmup_confirmed), "br": bool(t.is_bridged),
                     "bf": int(t.bridge_frames), "fss": int(t._frames_since_skeleton),
                     "p": to_orig(float(t.kf.x[0, 0]), float(t.kf.x[1, 0])),
                     "h": round(float(t.bbox[3]) / s, 1),
                     "spd": round(float(np.linalg.norm(t.get_velocity())), 2),
                     "est": bool(t.is_established), "occ": bool(t._occluded)} for t in trk.tracks],
            "ph": int(trk._person_height_px),
            "dorm": [int(d.track_id) for d in trk._dormant],
            "n_raw": n_raw, "n_dup": n_dup,
            "xv_rej": int(timing.get("crossval_rejected", 0)),
            "xv": {k: int(timing.get(k, 0)) for k in ("crossval_kept_skeleton", "crossval_kept_motion", "crossval_kept_track")},
            "excl_rej": int(timing.get("exclusion_rejected", 0)),
            "cold": dict(cold),
            "top": [{"bc": round(r[3], 3), "c": to_orig(float(r[2][0] + r[2][2] / 2), float(r[2][1] + r[2][3] / 2)),
                     "h": round(float(r[2][3]) / s, 1),
                     "nk": int(np.sum(r[1] > 0.5))} for r in top],
        })
        if (i + 1) % 1000 == 0:
            print(f"  replayed {i+1}  {(time.time()-t0)/(i+1)*1000:.0f} ms/f", flush=True)
    cap.release(); proc.tracker.logger.close()
    meta = {"video": video, "start": start, "tau": tau, "cfg": cfg, "md5_bad": md5_bad,
            "n": len(rows), "secs": round(time.time() - t0, 1), "sets": args.sets}
    Path(args.out).write_text(json.dumps({"meta": meta, "rows": rows}))
    print(f"replayed {len(rows)} frames in {meta['secs']}s md5_bad={md5_bad} -> {args.out}")


def apply_patches(proc, args):
    """Scratch-only behaviour experiments (monkeypatches; repo untouched)."""
    import core.tracker as T
    import core.pipeline as P
    for kv in args.const:
        k, v = kv.split("=", 1)
        mod, name = k.split(".", 1)
        m = {"tracker": T, "pipeline": P}[mod]
        assert hasattr(m, name), k
        setattr(m, name, json.loads(v))
    trk = proc.tracker
    if args.resurrect_warm:
        orig_res = trk._try_resurrect
        def res(*a, **kw):
            t = orig_res(*a, **kw)
            if t is not None:
                t._warmup_score = max(t._warmup_score, T.TRACK_WARMUP_THRESHOLD)
            return t
        trk._try_resurrect = res
    if args.fuse_box:
        # Suppress a cold motion blob when its centroid lies inside a YOLO box
        # (expanded by fuse_box) -- instead of the 0.3h centroid-distance gate.
        orig_fuse = trk._fuse_motion_blobs
        exp = args.fuse_box
        def fuse(dets, blobs):
            merged = orig_fuse(dets, blobs)          # persistence sees ALL blobs
            n = len(dets)
            boxes = [[float(v) for v in bb[:4]] for (_k, _c, bb) in dets]
            out = list(merged[:n])
            for k, c, bb in merged[n:]:
                cx, cy = float(k[0][0]), float(k[0][1])
                if any(x - exp * w <= cx <= x + w + exp * w and y - exp * h <= cy <= y + h + exp * h
                       for x, y, w, h in boxes):
                    continue
                out.append((k, c, bb))
            return out
        trk._fuse_motion_blobs = fuse
    if args.reacq:
        K = args.reacq
        orig_gate = trk._get_creation_gate
        def gate(det_centroid):
            g, edge = orig_gate(det_centroid)
            cl = trk._find_closest_track(det_centroid)
            if (cl.track is not None and cl.track.is_established
                    and cl.track._frames_since_skeleton > T.TRACKER_GHOST_SKELETON_AGE):
                g = max(g, int(K * trk._person_height_px))
            return g, edge
        trk._get_creation_gate = gate
    if args.yolo_first_match:
        # Two-tier assignment: YOLO dets first (established, then tentative),
        # motion-synthetic dets only for tracks YOLO left unmatched.
        import types
        def rmp(self, detections, matched_det, matched_trk, matched_pairs_log, frame_ctx):
            ny = frame_ctx.n_yolo_detections
            yolo = list(range(min(ny, len(detections))))
            syn = list(range(ny, len(detections)))
            est = [i for i, t in enumerate(self.tracks) if t.is_established
                   and t.track_id not in self._cascade_suppressed]
            self._run_assignment_pass(detections, yolo, est, matched_det, matched_trk,
                                      matched_pairs_log, frame_ctx=frame_ctx, defer_updates=True)
            tent = [i for i, t in enumerate(self.tracks) if (not t.is_established
                    or t.track_id in self._cascade_suppressed) and i not in matched_trk]
            self._run_assignment_pass(detections, [d for d in yolo if d not in matched_det], tent,
                                      matched_det, matched_trk, matched_pairs_log,
                                      frame_ctx=frame_ctx, defer_updates=True)
            if syn:
                rest = [i for i in range(len(self.tracks)) if i not in matched_trk]
                self._run_assignment_pass(detections, syn, rest, matched_det, matched_trk,
                                          matched_pairs_log, frame_ctx=frame_ctx, defer_updates=True)
            for update in frame_ctx.pending_updates:
                self._apply_track_update(update.trk_idx, update.kpts, update.conf, update.bbox, frame_ctx)
        trk._run_matching_phase = types.MethodType(rmp, trk)
    if args.coast_vel is not None:
        # Net per-miss velocity factor F during a plain miss (code uses 0.9):
        # pre-scale by F/0.9 so predict()'s own *0.9 yields F.  F=0 -> hold.
        F = args.coast_vel
        orig_pred = T.DancerTrack.predict
        def pred(self):
            if self.time_since_update > 0:
                self.kf.x[2:4] *= (F / 0.9)
            return orig_pred(self)
        T.DancerTrack.predict = pred
    if args.keep_confirmed is not None:
        # Continuation != birth: a track that has been emitted stays emitted while
        # it had a real skeleton within the last K frames (integral ignored);
        # the frozen-ghost gate still applies beyond K.
        K = args.keep_confirmed
        orig_col2 = trk._collect_confirmed_tracks
        st2 = {"rep": set()}
        def col2():
            out = orig_col2()
            ids = {t.track_id for t in out}
            extra = [t for t in trk.tracks if t.track_id in st2["rep"] and t.track_id not in ids
                     and t._frames_since_skeleton <= K]
            out = out + extra
            if trk.max_persons > 0 and len(out) > trk.max_persons:
                out = sorted(out, key=lambda t: (-t.hits, t.track_id))[:trk.max_persons]
            st2["rep"] = (st2["rep"] & {t.track_id for t in trk.tracks}) | {t.track_id for t in out}
            return out
        trk._collect_confirmed_tracks = col2
    if args.sticky is not None:
        K = args.sticky
        orig_col = trk._collect_confirmed_tracks
        state = {"rep": set()}
        def col():
            out = orig_col()
            ids = {t.track_id for t in out}
            extra = [t for t in trk.tracks if t.track_id in state["rep"] and t.track_id not in ids
                     and t.time_since_update <= K]
            out = out + extra
            if trk.max_persons > 0 and len(out) > trk.max_persons:
                out = sorted(out, key=lambda t: (-t.hits, t.track_id))[:trk.max_persons]
            state["rep"] = (state["rep"] & {t.track_id for t in trk.tracks}) | {t.track_id for t in out}
            return out
        trk._collect_confirmed_tracks = col


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "replay"])
    ap.add_argument("--scenario"); ap.add_argument("--project"); ap.add_argument("--slot", type=int)
    ap.add_argument("--video"); ap.add_argument("--config-json")
    ap.add_argument("--set", dest="sets", action="append", default=[])
    ap.add_argument("--start", type=int, default=0); ap.add_argument("--frames", type=int)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--floor", type=float, default=0.1)
    ap.add_argument("--cache"); ap.add_argument("--out", required=True)
    ap.add_argument("--const", action="append", default=[], help="tracker.NAME=json or pipeline.NAME=json")
    ap.add_argument("--resurrect-warm", action="store_true")
    ap.add_argument("--app-order", action="store_true")
    ap.add_argument("--yolo-first-match", action="store_true")
    ap.add_argument("--coast-vel", type=float, default=None)
    ap.add_argument("--keep-confirmed", type=int, default=None)
    ap.add_argument("--fuse-box", type=float, default=None, help="suppress blobs inside YOLO boxes expanded by this ratio")
    ap.add_argument("--reacq", type=float, default=None, help="creation gate -> K*h when closest track is established+skeleton-stale")
    ap.add_argument("--sticky", type=int, default=None, help="keep reporting once-reported tracks while tsu<=K")
    ap.add_argument("--log-dir", default=str(__import__("pathlib").Path(__file__).resolve().parent / "logs" / "tmp"))
    args = ap.parse_args()
    (build if args.cmd == "build" else replay_cache)(args)


if __name__ == "__main__":
    main()
