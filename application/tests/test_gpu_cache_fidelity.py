"""Track P Stage 1: the GPU/TRT detect-cache replay must equal a direct --trt run.

This is the load-bearing invariant for moving the tuning/golden harness onto the
show path — a search over a GPU cache only counts if cache-replay == live TRT.
Gated on WD_RUN_REPLAY=1 (needs GPU + TRT engines + footage), like the parity
and golden-replay tests.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

RUN = os.environ.get("WD_RUN_REPLAY") == "1"
pytestmark = pytest.mark.skipif(
    not RUN, reason="set WD_RUN_REPLAY=1 (needs GPU + TRT engines + footage)")


@pytest.mark.parametrize("name", ["hangar-floor", "hangar-aerial"])
def test_gpu_cache_matches_direct_trt(name):
    import scoring
    import replay
    import detect_cache

    scen = scoring.load_scenario(
        os.path.join(os.path.dirname(__file__), "scenarios", f"{name}.json"))
    config = replay.scenario_config(scen)
    video = str(replay._find_recording(scen["project"], scen["slot"]))
    model = config.get("model", "yolo11x-pose")
    imgsz = int(config.get("yolo_imgsz", 1280))
    start, frames = scen["start"], scen["frames"]

    # The show path: a direct GPU+TRT replay (proven byte-stable run-to-run).
    direct = replay.replay_recording(
        video, config, model_name=model, imgsz=imgsz,
        start_frame=start, max_frames=frames, use_trt=True)
    dpf = direct.pop("per_frame", [])

    # The harness path: build a GPU/TRT cache, replay it through _post_yolo_chain.
    cpath = detect_cache.build_cache_gpu(
        video, config, model_name=model, imgsz=imgsz,
        start_frame=start, max_frames=frames, use_trt=True)
    cached = detect_cache.replay_from_cache_gpu(
        detect_cache.load_cache(cpath), config)
    cpf = cached.pop("per_frame", [])

    def reported(pf):
        return [(f["reported"], tuple(f["ids"])) for f in pf]

    assert len(dpf) == len(cpf) == frames
    assert reported(dpf) == reported(cpf), \
        f"{name}: GPU cache replay diverged from a direct --trt run"
