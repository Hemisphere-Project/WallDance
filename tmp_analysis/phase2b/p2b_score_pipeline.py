#!/usr/bin/env python3
"""Pipeline wrapper: score each scenario as soon as its inputs are ready.

Polls for (a) the scenario's gray store in graystore_index.json and (b) all
its expected det cells on disk; dispatches _score_one_scenario to a worker
pool the moment both hold. Lets the CPU scoring phase overlap the GPU build
shards instead of serializing behind them.

Usage: python p2b_score_pipeline.py [--workers 4]
"""
from __future__ import annotations

import argparse
import json
import time
from multiprocessing import get_context
from pathlib import Path

import p2b_common as C
from p2b_score import _score_one_scenario


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--poll-s", type=int, default=30)
    args = ap.parse_args()

    manifests = C.load_scenarios()
    expected = {}
    for m in manifests:
        ls = C.probe_long_side(m["_config"], m["_video"])
        expected[m["name"]] = [
            C.cell_path(m["name"], model, imgsz)
            for model in C.MODELS
            for imgsz in C.cell_imgsz_list(m["_config"], ls)]

    pending = set(expected)
    ctx = get_context("spawn")
    pool = ctx.Pool(processes=args.workers, maxtasksperchild=1)
    async_results = {}
    t0 = time.time()
    while pending or async_results:
        if pending:
            index = {}
            if C.GRAYSTORE_INDEX.exists():
                try:
                    index = json.loads(C.GRAYSTORE_INDEX.read_text())
                except json.JSONDecodeError:
                    index = {}
            for name in sorted(pending):
                gray_ok = name in index and Path(index[name]).exists()
                cells_ok = all(p.exists() for p in expected[name])
                if gray_ok and cells_ok:
                    print(f"[pipeline] dispatch {name} "
                          f"(+{time.time() - t0:.0f}s)", flush=True)
                    async_results[name] = pool.apply_async(
                        _score_one_scenario, (name,))
                    pending.discard(name)
        for name, ar in list(async_results.items()):
            if ar.ready():
                try:
                    ar.get()
                    print(f"[pipeline] scored {name} "
                          f"(+{time.time() - t0:.0f}s)", flush=True)
                except Exception as e:  # surface worker failures loudly
                    print(f"[pipeline] FAILED {name}: {e!r}", flush=True)
                del async_results[name]
        if pending or async_results:
            time.sleep(args.poll_s)
    pool.close()
    pool.join()
    print(f"[pipeline] all scenarios scored in {time.time() - t0:.0f}s",
          flush=True)


if __name__ == "__main__":
    main()
