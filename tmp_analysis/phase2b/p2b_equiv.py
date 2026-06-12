#!/usr/bin/env python3
"""Phase 2b equivalence validation (must pass before trusting the grid).

Two checks on two scenarios (hangar-floor: simple A-class; texture-duo:
multi-dancer + ghost pressure):

  CHECK 1 (floor identity, catches replay-loop bugs):
    replay_from_cache(standard cache built at CONF_FLOOR)   [post-dup dets]
    vs replay_cell(det cell, tau=CONF_FLOOR)                [pre-dup + dup]
    Same input set => the dup-filter is deterministic => timelines must be
    BIT-IDENTICAL.

  CHECK 2 (pinned-tau fidelity, quantifies the conf-floor edge effects:
    YOLO-internal NMS seeing floor-conf candidates + dup-filter running on
    the floor set):
    replay_from_cache(standard cache built at the PINNED confidence)
    vs replay_cell(det cell, tau=pinned confidence)
    Report frame agreement + score delta. Small deltas expected; large ones
    invalidate the tau-from-cache design.

Usage: python p2b_equiv.py  (assumes graystore + the 2 needed cells exist;
builds the pinned-conf standard caches itself if missing)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import p2b_common as C

SCENES = ("hangar-floor", "texture-duo")


def timelines_equal(a, b):
    if len(a) != len(b):
        return False, f"length {len(a)} != {len(b)}"
    diff = sum(1 for x, y in zip(a, b)
               if x["reported"] != y["reported"] or x["ids"] != y["ids"])
    return diff == 0, f"{diff}/{len(a)} frames differ"


def frame_agreement(a, b):
    n = min(len(a), len(b))
    same = sum(1 for x, y in zip(a[:n], b[:n])
               if x["reported"] == y["reported"])
    same_ids = sum(1 for x, y in zip(a[:n], b[:n]) if x["ids"] == y["ids"])
    return same / max(1, n), same_ids / max(1, n)


def main():
    import detect_cache
    import scoring

    index = json.loads(C.GRAYSTORE_INDEX.read_text())
    report = {}
    for name in SCENES:
        m = C.load_scenarios(only=[name])[0]
        config = m["_config"]
        pinned_model = config.get("model", "yolo11x-pose")
        pinned_imgsz = int(config.get("yolo_imgsz", 1280))
        pinned_conf = float(config.get("confidence", 0.25))
        print(f"=== {name}  {pinned_model}@{pinned_imgsz} "
              f"pinned_conf={pinned_conf} ===", flush=True)

        grays = C.decode_grays(Path(index[name]))
        cellp = C.cell_path(name, pinned_model, pinned_imgsz)
        if not cellp.exists():
            from p2b_build import build_det_cell
            print(f"building det cell {cellp.name} ...", flush=True)
            long_side = C.probe_long_side(config, m["_video"])
            C.save_cell(cellp, build_det_cell(m, pinned_model, pinned_imgsz,
                                              long_side))
        cell = C.load_cell(cellp)

        # --- CHECK 1: floor identity ---
        floor_cache = detect_cache.load_cache(Path(index[name]))
        t0 = time.time()
        sum_a = detect_cache.replay_from_cache(
            floor_cache, C.graystore_config(config), reuse_grays=True)
        t_cache_replay = time.time() - t0
        tl_a = sum_a.pop("per_frame")
        t0 = time.time()
        sum_b, _ = C.replay_cell(cell, grays, config, m, C.CONF_FLOOR)
        t_cell_replay = time.time() - t0
        tl_b = sum_b.pop("per_frame")
        ok, detail = timelines_equal(tl_a, tl_b)
        print(f"CHECK1 floor identity: {'PASS' if ok else 'FAIL'} ({detail}) "
              f"[std replay {t_cache_replay:.1f}s, cell replay "
              f"{t_cell_replay:.1f}s]", flush=True)

        # --- CHECK 2: pinned-tau fidelity ---
        key = detect_cache.cache_key(
            config, Path(m["_video"]).name, int(m["start"]),
            int(m["frames"]), pinned_model, pinned_imgsz)
        pinned_cache_path = detect_cache.cache_path_for(key)
        if not pinned_cache_path.exists():
            print(f"building pinned-conf standard cache "
                  f"{pinned_cache_path.name} ...", flush=True)
            detect_cache.build_cache(
                m["_video"], config, model_name=pinned_model,
                imgsz=pinned_imgsz, start_frame=int(m["start"]),
                max_frames=int(m["frames"]), out_path=pinned_cache_path)
        pinned_cache = detect_cache.load_cache(pinned_cache_path)
        sum_c = detect_cache.replay_from_cache(
            pinned_cache, config, reuse_grays=True)
        tl_c = sum_c.pop("per_frame")
        sum_d, _ = C.replay_cell(cell, grays, config, m, pinned_conf)
        tl_d = sum_d.pop("per_frame")
        agree_n, agree_ids = frame_agreement(tl_c, tl_d)
        sc_c = scoring.score_timeline(tl_c, m)
        sc_d = scoring.score_timeline(tl_d, m)
        ok2, detail2 = timelines_equal(tl_c, tl_d)
        print(f"CHECK2 pinned-tau fidelity: "
              f"{'IDENTICAL' if ok2 else detail2}; "
              f"reported-agree {agree_n:.4f}, ids-agree {agree_ids:.4f}; "
              f"score true={sc_c['score']:.4f} cellpath={sc_d['score']:.4f} "
              f"delta={sc_d['score'] - sc_c['score']:+.4f}", flush=True)

        report[name] = {
            "check1_identical": ok, "check1_detail": detail,
            "check2_identical": ok2,
            "check2_reported_agree": round(agree_n, 4),
            "check2_ids_agree": round(agree_ids, 4),
            "check2_score_true": sc_c["score"],
            "check2_score_cellpath": sc_d["score"],
            "std_cache_replay_s": round(t_cache_replay, 1),
            "cell_replay_s": round(t_cell_replay, 1),
        }

    out = C.PHASE_DIR / "equiv_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
