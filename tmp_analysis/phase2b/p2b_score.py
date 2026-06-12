#!/usr/bin/env python3
"""Phase 2b score phase (CPU, multiprocess): tau sweep + scoring per cell.

Per cell: replay the tau grid (+ the cell's calib2-(5)a seed tau, derived from
the floor replay's reported-track box confs), dedup identical tau memberships,
score each replay with scoring.score_timeline + evaluate_pass.

Results: results/<scenario>.jsonl, one record per (cell, tau); a final
{"cell_done": ...} sentinel per cell makes the run resumable at cell
granularity (an interrupted cell is redone; analysis dedups by last record).

Usage:
  python p2b_score.py [--workers 6] [--only NAME ...]
"""
from __future__ import annotations

import argparse
import json
import time
from multiprocessing import get_context

import p2b_common as C


def _score_one_scenario(name: str, models=None, out_suffix: str = "") -> str:
    import scoring
    manifests = C.load_scenarios(only=[name])
    m = manifests[0]
    config = m["_config"]
    hb = f"score_{name}{out_suffix}"

    index = json.loads(C.GRAYSTORE_INDEX.read_text())
    from pathlib import Path
    grays = C.decode_grays(Path(index[name]))

    C.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    res_path = C.RESULTS_DIR / f"{name}{out_suffix}.jsonl"
    # Resume: a cell finished by ANY shard/result-file of this scenario counts.
    done_cells = set()
    for rf in C.RESULTS_DIR.glob(f"{name}*.jsonl"):
        for line in rf.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("cell_done"):
                done_cells.add((rec["model"], rec["imgsz"]))

    out = open(res_path, "a", encoding="utf-8")

    def emit(rec):
        out.write(json.dumps(rec) + "\n")
        out.flush()

    long_side = C.probe_long_side(config, m["_video"])
    use_models = tuple(models) if models else C.MODELS
    cells = [(model, imgsz) for model in use_models
             for imgsz in C.cell_imgsz_list(config, long_side)]
    n_done = 0
    C.heartbeat(hb, status="start", n_cells=len(cells),
                already_done=len(done_cells))
    for model, imgsz in cells:
        C.arm_stall_dump(900)
        if (model, imgsz) in done_cells:
            n_done += 1
            continue
        path = C.cell_path(name, model, imgsz)
        if not path.exists():
            C.heartbeat(hb, model=model, imgsz=imgsz, status="missing_cell")
            continue
        cell = C.load_cell(path)
        t_cell = time.time()

        taus = list(C.TAU_GRID)
        seed_tau = None
        memo = {}  # membership hash -> (tau, score, components)
        i = 0
        while i < len(taus):
            tau = round(taus[i], 3)
            i += 1
            mh = C.tau_membership_hash(cell, tau)
            base = {
                "scenario": name, "model": model, "imgsz": imgsz,
                "tau": tau, "net_height": cell["net_height"],
                "is_seed": bool(seed_tau is not None and tau == seed_tau),
            }
            if mh in memo:
                first_tau, score, components, passed = memo[mh]
                emit({**base, "dup_of": first_tau, "score": score,
                      "components": components, "passed": passed})
            else:
                t0 = time.time()
                collect = (tau == C.CONF_FLOOR)
                summary, track_confs = C.replay_cell(
                    cell, grays, config, m, tau, collect_track_confs=collect)
                per_frame = summary.pop("per_frame", [])
                sc = scoring.score_timeline(per_frame, m)
                ev = scoring.evaluate_pass(sc, m)
                components = sc.get("components", {})
                raw = sc.get("raw", {})
                emit({**base, "dup_of": None,
                      "score": sc.get("score"),
                      "components": components,
                      "raw": {k: raw.get(k) for k in (
                          "longest_drop_seconds", "missed_dancer_frames",
                          "ghost_dancer_frames", "drop_episodes",
                          "ghost_episodes", "distinct_ids", "id_switches")},
                      "passed": ev.get("passed"),
                      "pass_checks": {k: v.get("ok")
                                      for k, v in ev.get("checks", {}).items()},
                      "summary": {k: summary.get(k) for k in (
                          "real_tracks", "marginal_tracks", "ghost_tracks",
                          "swap_count", "zero_detection_frames",
                          "avg_detections")},
                      "replay_s": round(time.time() - t0, 2)})
                memo[mh] = (tau, sc.get("score"), components,
                            ev.get("passed"))
                if collect:
                    seed_tau = C.seed_tau_from_track_confs(track_confs)
                    if seed_tau is not None and \
                            all(abs(seed_tau - t) > 1e-9 for t in taus):
                        taus.append(seed_tau)
        emit({"cell_done": True, "scenario": name, "model": model,
              "imgsz": imgsz, "seed_tau": seed_tau,
              "yolo_ms_median": cell.get("yolo_ms_median"),
              "n_dets": cell.get("n_dets"),
              "secs": round(time.time() - t_cell, 1)})
        n_done += 1
        C.heartbeat(hb, model=model, imgsz=imgsz, seed_tau=seed_tau,
                    secs=round(time.time() - t_cell, 1),
                    done=n_done, total=len(cells))
    out.close()
    C.heartbeat(hb, status="done")
    return name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--models", nargs="*", default=None,
                    help="restrict to these models (cell-level sharding)")
    ap.add_argument("--out-suffix", default="",
                    help="suffix for the results/heartbeat files of a shard")
    args = ap.parse_args()

    manifests = C.load_scenarios(only=args.only)
    names = [m["name"] for m in manifests]
    if args.workers <= 1 or len(names) == 1:
        for n in names:
            _score_one_scenario(n, models=args.models,
                                out_suffix=args.out_suffix)
        return
    ctx = get_context("spawn")
    with ctx.Pool(processes=min(args.workers, len(names)),
                  maxtasksperchild=1) as pool:
        for n in pool.imap_unordered(_score_one_scenario, names):
            print(f"[score] scenario complete: {n}", flush=True)


if __name__ == "__main__":
    main()
