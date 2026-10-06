"""MRK-1: the IR-marker eval harness (tmp_analysis/marker_eval.py +
marker_evallib.py). Detector blob extraction on synthetic frames, glint map,
persistence, Tier-A association, metric computation on synthetic ground truth,
go/no-go gates, .meta v2 provenance parsing and an end-to-end CLI run on a tiny
synthetic FFV1 take. Fast; no GPU. The pipeline pose source runs only with
WD_RUN_REPLAY=1 (GPU + weights + corpus recordings)."""
import json
import math
import os
import pickle
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from core import marker_model as mm

REPO = Path(__file__).resolve().parents[2]
TMP_ANALYSIS = REPO / "tmp_analysis"
sys.path.append(str(TMP_ANALYSIS))       # append: never shadow app/test modules
import marker_evallib as ev  # noqa: E402
import marker_eval as me  # noqa: E402


# ---------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------

def _frame(h=200, w=240, bg=5):
    return np.full((h, w), bg, np.uint8)


def test_detect_empty_frame_early_out():
    st = {}
    assert ev.detect_blobs(_frame(), 160, stats=st) == []
    assert st["n_px"] == 0


def test_detect_disc_subpixel_centroid_and_features():
    g = _frame()
    mm.render_marker(g, (100.4, 60.6), diameter=6.0, level=3000.0, psf_sigma=0.5)
    (b,) = ev.detect_blobs(g, 160)
    assert math.hypot(b["x"] - 100.4, b["y"] - 60.6) < 0.15
    assert b["peak"] == 255 and b["n_sat"] >= 20
    assert 25 <= b["area"] <= 60
    assert b["elong"] < 1.3 and b["streak"] < 1.5
    x0, y0, x1, y1 = b["bbox"]
    assert x0 <= 100 <= x1 and y0 <= 60 <= y1


def test_detect_streak_elongation_and_orientation():
    g = _frame()
    th = math.radians(30.0)
    v = (40 * math.cos(th), 40 * math.sin(th))
    mm.render_marker(g, (120, 100), diameter=4.0, level=6000.0, velocity=v)
    (b,) = ev.detect_blobs(g, 160)
    assert b["elong"] > 4 and b["streak"] > 25
    assert abs(b["angle"] - 30.0) < 3.0
    assert math.hypot(b["x"] - 120, b["y"] - 100) < 0.5


def test_sparse_labelling_merges_close_and_splits_far():
    g = _frame()
    mm.render_marker(g, (50, 50), diameter=4.0, level=3000.0)
    mm.render_marker(g, (57, 50), diameter=4.0, level=3000.0)     # 3 px gap -> one blob (4 px cells)
    mm.render_marker(g, (150, 120), diameter=4.0, level=3000.0)
    blobs = ev.detect_blobs(g, 160, cell=4)
    assert len(blobs) == 2
    merged = min(blobs, key=lambda b: b["x"])
    assert 52 < merged["x"] < 55 and merged["elong"] > 1.5
    assert len(ev.detect_blobs(g, 160, cell=1)) == 3                # 1 px cells: plain 8-CC


def test_area_gate_offset_and_overflow():
    g = _frame()
    g[10, 10] = 255                                                  # hot pixel
    mm.render_marker(g, (100, 100), diameter=5.0, level=3000.0)
    assert len(ev.detect_blobs(g, 160, min_area=2)) == 1
    assert len(ev.detect_blobs(g, 160, min_area=1)) == 2
    assert ev.detect_blobs(g, 160, max_area=5) == [] or all(b["area"] <= 5 for b in ev.detect_blobs(g, 160, max_area=5))
    (b,) = ev.detect_blobs(g, 160, offset=(1000, 2000))
    assert 1099 < b["x"] < 1101 and 2099 < b["y"] < 2101 and b["bbox"][0] > 1000
    many = _frame()
    many[::10, ::10] = 255
    st = {}
    out = ev.detect_blobs(many, 160, min_area=1, cell=1, max_blobs=20, stats=st)
    assert len(out) == 20 and st["overflow"]


# ---------------------------------------------------------------------------
# glint map + persistence
# ---------------------------------------------------------------------------

def test_glint_map_flags_static_and_roundtrips(tmp_path):
    g = _frame()
    g[40:43, 40:43] = 250                                           # fixed fixture
    gm = ev.GlintMap(g.shape, cell=8)
    for _ in range(5):
        gm.add(ev.detect_blobs(g, 100))
    gm.finalize(1)
    assert gm.n_cells == 9                                          # 1 cell + 1-cell dilation
    blobs = ev.detect_blobs(g, 100) + [{"x": 150.0, "y": 150.0}]
    gm.flag(blobs)
    assert blobs[0]["static"] and not blobs[1]["static"]
    gm.T = 180
    gm.save(tmp_path / "g.npz")
    g2 = ev.GlintMap.load(tmp_path / "g.npz")
    assert (g2.cells == gm.cells).all() and g2.cell == 8 and g2.T == 180
    fs = ev.FloorStats()
    fs.add_natural(g, gm)                                            # the fixture is masked out
    assert fs.max_natural == [5]
    assert ev.recommend_threshold(30) == 120 and ev.recommend_threshold(100) == 200
    assert ev.recommend_threshold(150) == 240


def test_tail_histogram_matches_partition():
    rng = np.random.default_rng(1)
    for _ in range(10):
        g = np.clip(rng.exponential(rng.uniform(2, 30), (300, 277)), 0, 255).astype(np.uint8)
        g[0, :3] = 255
        fs = ev.FloorStats()
        fs.add_tail(g)
        flat = g.ravel()
        n = len(flat)
        q = np.partition(flat, [int(n * 0.999), int(n * 0.9999)])
        assert fs.tail["p999"][0] == q[int(n * 0.999)] and fs.tail["p9999"][0] == q[int(n * 0.9999)]
        assert fs.tail["max"][0] == 255 and fs.tail["n255"][0] == int((flat == 255).sum())
        assert fs.tail["mean"][0] == pytest.approx(flat.mean())


def test_chain_persistence_two_of_three():
    lk = ev.ChainLinker(link_px=10, persist_k=2, persist_n=3)
    flash = [{"x": 10.0, "y": 10.0, "peak": 255, "area": 9}]
    lk.step(0, flash)
    assert not flash[0]["persistent"]                               # a one-frame flash
    lk.step(1, [])
    b2 = [{"x": 12.0, "y": 11.0, "peak": 255, "area": 9}]
    lk.step(2, b2)                                                   # 2 of the last 3 frames
    assert b2[0]["persistent"] and b2[0]["chain"] == flash[0]["chain"] and b2[0]["age"] == 2
    for f in range(3, 20):
        lk.step(f, [{"x": 100.0, "y": 100.0, "peak": 200, "area": 4}])
    fixed, moving, short = ev.classify_chains(lk.finish(), static_px=4, static_min_frames=10)
    assert len(fixed) == 1 and len(short) == 1 and not moving
    raw, persist = ev.count_fp([{"static": True}, {"persistent": True}, {}])
    assert (raw, persist) == (2, 1)
    s = ev.fp_summary([0, 1, 2, 3], [1, 0, 0, 0], [0, 0, 0, 0], time_bin=2)
    assert s["fp_per_frame"] == 0.0 and s["fp_per_frame_raw"] == 0.25 and len(s["fp_per_frame_by_time"]) == 2


# ---------------------------------------------------------------------------
# association + metrics on synthetic ground truth
# ---------------------------------------------------------------------------

def _track(tid, center, H=200.0, fss=0, conf=0.9):
    """Skeleton: wrists/ankles at fixed offsets from ``center``; hips mid."""
    k = np.tile(np.asarray(center, float), (17, 1))
    k[9] += (-50, -60); k[10] += (50, -60); k[15] += (-40, 90); k[16] += (40, 90)
    k[11] += (-15, 10); k[12] += (15, 10)
    c = np.full(17, conf)
    x, y = center
    return {"id": tid, "kpts": k, "conf": c, "bbox": np.array([x - 60, y - 100, 120, H]), "fss": fss, "H": H}


def _blob(x, y, peak=255, n_sat=10, area=20, streak=0.0):
    return {"x": float(x), "y": float(y), "peak": peak, "n_sat": n_sat, "area": area, "streak": streak}


def test_associate_hungarian_gate_and_contested():
    t = _track(1, (300, 300))
    ext = t["kpts"][list(mm.EXT_KPTS)]
    blobs = [_blob(*(p + (2, -1))) for p in ext] + [_blob(600, 600)]
    A = ev.associate([t], blobs)
    assert len(A.pairs) == 4 and A.owner.tolist() == [0, 0, 0, 0, -1]
    assert all(d < 3 for _, _, d, _ in A.pairs) and not A.contested.any()
    # gap frames (fss > 0) predict nothing: keypoints are stale
    assert ev.associate([_track(1, (300, 300), fss=3)], blobs).pairs == []
    # two dancers whose wrists nearly touch: the blob between them is contested
    t1, t2 = _track(1, (300, 300)), _track(2, (400, 300))
    mid = (t1["kpts"][10] + t2["kpts"][9]) / 2               # RW of 1 meets LW of 2
    A2 = ev.associate([t1, t2], [_blob(*mid)])
    assert len(A2.pairs) == 1 and A2.contested[0] and A2.within[0] == {0, 1}


def test_blob_zones_separate_markers_costume_gap_and_stage():
    skel, gap = _track(1, (300, 300)), _track(2, (700, 300), fss=4)
    blobs = [_blob(*skel["kpts"][9]),                 # a marker
             _blob(300, 330),                          # a buckle on the skeleton dancer
             _blob(700, 300),                          # on the coasting track: Tier-B candidate
             _blob(1000, 50)]                          # stage
    A = ev.associate([skel, gap], blobs)
    assert ev.blob_zones([skel, gap], blobs, A) == [ev.ZONE_ASSOC, ev.ZONE_ON_SKELETON,
                                                    ev.ZONE_ON_GAP_TRACK, ev.ZONE_FREE]


def _run_metrics(frames, harness=False, **kw):
    pm = ev.PoseMetrics(harness=harness, **kw)
    for f, (tracks, blobs) in enumerate(frames):
        pm.observe_heights(tracks)
        A = ev.associate(tracks, blobs, H_of=pm.H_of, harness=harness)
        pm.add_frame(f, tracks, blobs, A)
    return pm


def test_metrics_on_synthetic_ground_truth():
    frames = []
    for f in range(60):
        t = _track(1, (300 + 2 * f, 300))
        ext = t["kpts"][list(mm.EXT_KPTS)]
        # LW blob always; RW/LA unsaturated half the time; RA missing on even frames;
        # one extra blob on the body (a harness buckle) every frame
        blobs = [_blob(*ext[0]), _blob(*ext[1], peak=200 if f % 2 else 255, n_sat=0 if f % 2 else 10),
                 _blob(*ext[2])]
        if f % 2:
            blobs.append(_blob(*ext[3]))
        blobs.append(_blob(t["kpts"][0][0], t["kpts"][0][1] + 20, area=6))
        frames.append(([t], blobs))
    pm = _run_metrics(frames)
    m1 = pm.m1()
    assert m1["N"] == 240 and m1["recall"] == pytest.approx(210 / 240)
    assert m1["by_slot"] == {"LW": 1.0, "RW": 1.0, "LA": 1.0, "RA": 0.5}
    assert ev.gate_m1(m1)["status"] == "FAIL"
    m3 = pm.m3_m4()
    assert m3["N"] == 210 and m3["saturated_pct"] == pytest.approx(100 * 180 / 210, abs=0.01)
    assert ev.gate_m3(m3, None, 160)["status"] == "FAIL"
    assert m3["residual_H_p50_p90_p95"][0] == 0.0
    m2b = pm.m2b()
    assert m2b["assoc_blobs"] == 210 and m2b["extra_blobs"] == 60
    assert ev.gate_m2(None, m2b)["status"] == "FAIL"            # 28.6 % > 5 %
    m5 = pm.m5()
    assert m5["ge1_pct"] == 100.0 and m5["n_visible_hist"][3] == 30 and m5["n_visible_hist"][4] == 30
    assert ev.gate_m5(m5)["status"] == "PASS"
    assert pm.m7() == {"duo_frames": 0}
    assert ev.gate_m7(pm.m7())["status"] == "N/A"


def test_m5_zero_runs_and_gap_frames():
    frames = []
    for f in range(40):
        fss = 0 if f < 20 else f - 19                            # YOLO lost after frame 19
        t = _track(1, (300, 300), fss=fss)
        blobs = [] if 25 <= f < 37 else [_blob(*t["kpts"][9])]  # 12-frame marker gap
        frames.append(([t], blobs))
    m5 = _run_metrics(frames).m5()
    assert m5["N"] == 40 and m5["zero_runs"] == {"n": 1, "p50": 12.0, "p90": 12.0, "max": 12}
    assert m5["ge1_pct"] == pytest.approx(70.0)
    assert ev.gate_m5(m5)["status"] == "NO-GO"                   # < 75 %


def test_m7_wrong_dancer_counts_cross_assignment():
    t1, t2 = _track(1, (300, 300)), _track(2, (420, 300))
    blobs = [_blob(*t1["kpts"][9]), _blob(*t2["kpts"][16])]
    m7 = _run_metrics([([t1, t2], blobs)] * 5).m7()
    assert m7["duo_frames"] == 5 and m7["assoc"] == 10 and m7["wrong_dancer"] == 0
    assert ev.gate_m7(m7)["status"] == "PASS"


def test_gap_study_exact_on_rigid_motion_and_gate_m6():
    rng = np.random.default_rng(0)
    rows = []
    for f in range(80):
        c = np.array([300.0 + 4 * f, 200.0 + 2 * math.sin(f / 5)])
        t = _track(1, c)
        rows.append({"f": f, "id": 1, "kpts": t["kpts"], "conf": t["conf"], "bbox": t["bbox"], "fss": 0})
    by = ev.tracks_from_rows(rows)
    st = ev.summarize_study(ev.gap_study(by, require_all=True))
    for key, row in st["gap_table"].items():
        if "offset_ul" in row:
            assert row["offset_ul"][1] < 1e-9, key                 # rigid motion: exact
    assert st["gap_table"]["n4_k10"]["hold"][0] > 0.1
    assert ev.gate_m6(st)["status"] == "PASS"
    r = ev.study_r_table(st["gap_table"])
    assert set(r) >= {"hold", 1, 2, 3, 4, "hip", "4+hip"}
    bad = {"gap_table": {"n2_k10": {"offset_ul": [0.2, 0.5], "hold": [0.1, 0.2], "N": 100}},
           "kinematics_per_frame_over_H": {"centroid_speed_p50_p90_p99": [0.07, 0, 0]}}
    g = ev.gate_m6(bad)
    assert g["status"] == "NO-GO" and g["value"]["fast_take"]
    assert ev.gate_m6({"gap_table": {}})["status"] == "N/A"


def test_gate_m1_m2_m3_thresholds():
    def m1(rec, far, fast, n=100):
        return {"N": n, "recall": rec, "by_distance": {"far": {"N": n, "recall": far}},
                "fast": {"N": n, "recall": fast}}
    assert ev.gate_m1(m1(0.97, 0.96, 0.92))["status"] == "PASS"
    assert ev.gate_m1(m1(0.97, 0.94, 0.92))["status"] == "FAIL"
    assert ev.gate_m1(m1(0.97, 0.80, 0.92))["status"] == "NO-GO"
    assert ev.gate_m1(m1(0.97, 0.96, 0.85))["status"] == "FAIL"
    assert ev.gate_m1({"N": 0})["status"] == "N/A"
    assert ev.gate_m2({"fp_per_frame": 0.005}, None)["status"] == "PASS"
    assert ev.gate_m2({"fp_per_frame": 0.02}, None)["status"] == "FAIL"
    assert ev.gate_m2({"fp_per_frame": 0.06}, None)["status"] == "NO-GO"
    assert ev.gate_m3(None, {"max_natural": {"max": 90}}, 160)["status"] == "PASS"
    assert ev.gate_m3(None, {"max_natural": {"max": 120}}, 160)["status"] == "FAIL"
    assert "M3" in ev.format_gates({"M3": ev.gate_m3(None, {"max_natural": {"max": 90}}, 160)})


def test_phase0a_aggregation_uses_the_right_takes():
    def m1(rec, far, n=200):
        return {"N": n, "recall": rec, "by_distance": {"far": {"N": n // 3, "recall": far}},
                "fast": {"N": n // 4, "recall": rec}}
    res = {
        "floor": {"floor": {"fp_per_frame": 0.004, "max_natural": {"max": 60}, "T_eval": 130}},
        "static": {"assoc": {"M1": m1(0.99, 0.97), "M3_M4": {"N": 100, "saturated_pct": 99.0,
                                                               "m4_area_by_H": [{"H_px": [0, 150]}]}}},
        "aerial": {"assoc": {"M1": m1(0.97, 0.90), "M2_dancer": {"assoc_blobs": 500,
                                                                  "extra_pct_of_marker_blobs": 2.0},
                             "M3_M4": {"N": 300, "saturated_pct": 96.0}},
                   "occlusion": {"M5": {"N": 1000, "ge1_pct": 93.0, "ge2_pct": 80.0,
                                        "zero_runs": {"n": 5, "p50": 4, "p90": 12, "max": 15}}},
                   "centroid": {"gap_table": {"n2_k10": {"offset_ul": [0.08, 0.2], "hold": [0.2, 0.5], "N": 100}},
                                "kinematics_per_frame_over_H": {"centroid_speed_p50_p90_p99": [0.07, 0, 0]}}},
        "fast": {"assoc": {"M1": m1(0.93, 0.95), "M3_M4": {"N": 200, "saturated_pct": 94.0}}},
    }
    g = me.aggregate_phase0a(res)
    assert g["M1"]["status"] == "PASS"                       # far = static take (0.97), fast take 0.93
    assert g["M1"]["value"]["recall"] == pytest.approx((0.99 * 200 + 0.97 * 200 + 0.93 * 200) / 600, abs=1e-4)
    assert g["M2"]["status"] == "PASS" and g["M3"]["status"] == "PASS" and g["M3"]["value"]["T"] == 130
    assert g["M4"]["status"] == "INFO" and g["M5"]["status"] == "PASS" and g["M6"]["status"] == "PASS"
    assert g["M7"]["status"] == "N/A" and g["M9"]["status"] == "MANUAL"
    res["fast"]["assoc"]["M1"] = m1(0.80, 0.95)
    assert me.aggregate_phase0a(res)["M1"]["status"] == "FAIL"
    # auto-T is always a floor-grid value (floor, --glint-slot and phase0a agree)
    assert me.grid_up(120) == 120 and me.grid_up(122) == 130 and me.grid_up(251) == 254


# ---------------------------------------------------------------------------
# .meta v2 provenance
# ---------------------------------------------------------------------------

def _write_v2(path: Path, ae="Off", exposure=24800.0, offset=6.0, camlog=True):
    meta = {"actual_fps": 19.8, "frames": 400, "meta_version": 2, "slot": 4, "file": path.name,
            "codec": "FFV1", "container_fps": 30.0, "size": [1488, 1528], "frames_queued": 400,
            "frames_dropped": 0, "camlog": path.name + ".camlog.jsonl" if camlog else None,
            "camera": {"source": "ids", "open": True, "measured_fps": 19.8,
                       "nodes": {"ExposureTime": exposure, "ExposureAuto": ae, "Gain": 12.5,
                                 "GainAuto": "Off", "PixelFormat": "Mono8", "Gamma": 1.0,
                                 "DeviceTemperature": 41.5},
                       "app_settings": {"user_set": "UserSet1", "crop_ratio": 1.3}},
            "app": {"commit": "abc123", "branch": "release"}, "project": "markers-0a-test",
            "engine": {"model": "yolo11x-pose", "imgsz": 1280, "trt_active": True},
            "rig": {"lens": "Tamron 8mm", "f_number": 1.4, "illuminator_offset_cm": offset,
                    "camera_distance_m": 18.0, "markers": "4 cuffs + harness, 5 cm",
                    "illuminator": "2x 850nm", "filter": "BP850"},
            "config": {"roi_enabled": True, "roi_x": 10, "roi_y": 20, "roi_w": 100, "roi_h": 80,
                       "person_height_px": 200, "ids_exposure_us": 24800.0, "huge": list(range(50))},
            "at_stop": {"camera": {"nodes": {"ExposureTime": exposure, "Gain": 12.5}}}}
    Path(str(path) + ".meta").write_text(json.dumps(meta))
    if camlog:
        lines = [{"t": 1.0, "frames": 0, "nodes": {"ExposureTime": exposure, "Gain": 12.5,
                                                    "ExposureAuto": ae, "DeviceTemperature": 41.0}},
                 {"t": 2.0, "frames": 200, "nodes": {"ExposureTime": exposure / 2, "Gain": 12.5,
                                                      "ExposureAuto": ae, "DeviceTemperature": 41.5}}]
        Path(str(path) + ".camlog.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\n")


def test_read_meta_v2_provenance(tmp_path):
    v = tmp_path / "slot_4_20261010_120000.avi"
    _write_v2(v)
    m = ev.read_take_meta(v)
    assert m["present"] and m["meta_version"] == 2 and m["actual_fps"] == 19.8 and m["codec"] == "FFV1"
    cam = m["camera"]
    assert cam["exposure_us"] == 24800.0 and cam["gain_db"] == 12.5 and cam["exposure_auto"] == "Off"
    assert cam["pixel_format"] == "Mono8" and cam["app_user_set"] == "UserSet1"
    assert m["camera_at_stop"]["exposure_us"] == 24800.0
    assert m["rig"]["illuminator_offset_cm"] == 6.0 and m["rig"]["markers"].startswith("4 cuffs")
    assert m["app"]["commit"] == "abc123" and m["engine"]["trt_active"] is True
    assert m["config"]["roi_x"] == 10 and "huge" not in m["config"]
    assert m["camlog"]["samples"] == 2 and m["camlog"]["exposure_varies"]
    assert m["shoot_brief"]["missing"] == []
    assert any("exposure varied" in w for w in m["shoot_brief"]["warnings"])
    look = ev.exposure_lookup({"series": m["_camlog_series"]})
    assert look(0) == 24800.0 and look(199) == 24800.0 and look(250) == 12400.0
    json.dumps(ev.jsonable(ev.public_meta(m)))                     # summary-safe


def test_read_meta_v2_warnings(tmp_path):
    v = tmp_path / "slot_5_x.avi"
    _write_v2(v, ae="Continuous", exposure=51600.0, offset=30.0, camlog=False)
    w = " | ".join(ev.read_take_meta(v)["shoot_brief"]["warnings"])
    assert "auto-exposure was Continuous" in w and "> 25 ms" in w and "30.0 cm off the lens axis" in w


def test_read_meta_v1_and_missing(tmp_path):
    v = tmp_path / "slot_3_old.avi"
    Path(str(v) + ".meta").write_text('{"actual_fps": 19.819, "frames": 9674}')
    m = ev.read_take_meta(v, rig_overrides={"illuminator_offset_cm": 8.0})
    assert m["meta_version"] == 1 and m["actual_fps"] == 19.819 and m["frames"] == 9674
    assert "camera" not in m and m["rig"] == {"illuminator_offset_cm": 8.0}
    assert "camera.exposure_us" in m["shoot_brief"]["missing"]
    assert "rig.illuminator_offset_cm" not in m["shoot_brief"]["missing"]
    assert any("old .meta" in w for w in m["shoot_brief"]["warnings"])
    none = ev.read_take_meta(tmp_path / "nothing.avi")
    assert not none["present"] and none["meta_version"] is None
    bad = tmp_path / "bad.avi"
    Path(str(bad) + ".meta").write_text("{not json")
    assert "error" in ev.read_take_meta(bad)


# ---------------------------------------------------------------------------
# end to end: tiny synthetic take -> CLI -> summary.json + sheets
# ---------------------------------------------------------------------------

def _synthetic_take(tmp_path: Path, n=45, w=320, h=240, markers=True, glint=True):
    """An FFV1 take of one 'dancer' drifting right, with saturated markers at
    its wrists/ankles, a fixed glint, a v2 .meta, and the matching pose dump."""
    proj = tmp_path / "projects" / "synthproj" / "recordings"
    proj.mkdir(parents=True)
    video = proj / "slot_4_20261010_120000.avi"
    wr = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"FFV1"), 30.0, (w, h))
    if not wr.isOpened():
        pytest.skip("OpenCV build without FFV1")
    rows = []
    for f in range(n):
        g = np.full((h, w), 6, np.uint8)
        if glint:
            g[20:23, 280:283] = 250
        t = _track(1, (100 + 2 * f, 120), H=200.0)
        t["kpts"] = (t["kpts"] - [100 + 2 * f, 120]) * 0.5 + [100 + 2 * f, 120]   # fit the frame
        t["bbox"] = np.array([100 + 2 * f - 30, 70, 60, 100.0])
        t["H"] = 100.0
        if markers:
            for k in mm.EXT_KPTS:
                mm.render_marker(g, t["kpts"][k], diameter=4.0, level=3000.0, velocity=(2.0, 0.0))
        wr.write(cv2.merge([g, g, g]))
        rows.append({"f": f, "id": 1, "kpts": t["kpts"].astype(np.float32), "conf": t["conf"].astype(np.float32),
                     "bbox": t["bbox"].astype(np.float32), "fss": 0})
    wr.release()
    _write_v2(video, camlog=False)
    meta = json.loads(Path(str(video) + ".meta").read_text())
    meta["config"]["roi_enabled"] = False
    Path(str(video) + ".meta").write_text(json.dumps(meta))
    dump = tmp_path / "poses.pkl"
    with open(dump, "wb") as fh:
        pickle.dump({"rows": rows}, fh)
    # a fake repo root: application/src/core must exist for find_repo_root
    (tmp_path / "application" / "src" / "core").mkdir(parents=True)
    return video, dump


def test_cli_all_end_to_end(tmp_path):
    video, dump = _synthetic_take(tmp_path)
    out = tmp_path / "out"
    rc = me.main(["all", "--repo", str(tmp_path), "--project", "synthproj", "--slot", "4",
                  "--poses", f"dump:{dump}", "--roi", "none", "--out", str(out), "--progress", "0",
                  "--jsonl"])
    assert rc == 0
    s = json.loads((out / "summary.json").read_text())
    assert s["provenance"]["meta_version"] == 2 and s["provenance"]["camera"]["exposure_us"] == 24800.0
    assert s["take"]["exposure_source"] == "take .meta camera"
    assert s["assoc"]["M1"]["recall"] == 1.0 and s["gates"]["M1"]["status"] == "PASS"
    assert s["assoc"]["M3_M4"]["saturated_pct"] == 100.0 and s["gates"]["M3"]["status"] == "PASS"
    assert s["occlusion"]["M5"]["ge1_pct"] == 100.0 and s["gates"]["M5"]["status"] == "PASS"
    assert s["gates"]["M6"]["status"] in ("PASS", "N/A")
    assert s["centroid"]["gap_table"]["n4_k1"]["offset_ul"][0] < 0.01
    assert (out / "sheet_hits.jpg").exists() and (out / "frames.jsonl.gz").exists()
    total = sum(p.stat().st_size for p in out.iterdir())
    assert total < 400_000


def test_cli_floor_and_inject_end_to_end(tmp_path):
    video, dump = _synthetic_take(tmp_path, n=40, markers=False)
    out = tmp_path / "floor"
    me.main(["floor", "--repo", str(tmp_path), "--video", str(video), "--roi", "none",
             "--glint-frames", "20", "--out", str(out), "--progress", "0", "--threshold", "auto"])
    s = json.loads((out / "summary.json").read_text())
    fl = s["floor"]
    assert fl["max_natural"]["max"] == 6 and fl["T_recommended"] == 120
    assert fl["fp_per_frame"] == 0.0 and s["gates"]["M2"]["status"] == "PASS"
    assert s["gates"]["M3"]["status"] == "PASS"
    assert s["detector"]["glint"]["cells"] >= 1 and (out / "glint_map.npz").exists()
    # inject synthetic streak markers at the pose keypoints of the marker-less take
    out2 = tmp_path / "inj"
    me.main(["assoc", "--repo", str(tmp_path), "--video", str(video), "--roi", "none",
             "--poses", f"dump:{dump}", "--inject", "streak", "--marker-cm", "3",
             "--glint-map", str(out / "glint_map.npz"), "--out", str(out2), "--progress", "0"])
    s2 = json.loads((out2 / "summary.json").read_text())
    assert s2["inject"]["markers"] == 40 * 4 and s2["inject"]["gt_recall"] == 1.0
    assert s2["inject"]["centroid_err_px_p50_p90"][1] < 0.5
    assert s2["assoc"]["M1"]["recall"] == 1.0


@pytest.mark.skipif(not os.environ.get("WD_RUN_REPLAY"), reason="GPU + weights + corpus: set WD_RUN_REPLAY=1")
def test_pipeline_pose_source_gpu(tmp_path):
    """Live pipeline poses (replay._build_processor) on 40 frames of
    hangar-aerial. Subprocess: importing replay re-execs the interpreter."""
    rec = REPO / "projects" / "3_TANGO_HANGAR-whitebg2" / "recordings"
    if not list(rec.glob("slot_4_*.avi")) or not (REPO / "models" / "yolo11x-pose.pt").exists():
        pytest.skip("hangar-aerial recording or yolo11x-pose.pt missing")
    out = tmp_path / "pipe"
    r = subprocess.run([sys.executable, str(TMP_ANALYSIS / "marker_eval.py"), "assoc", "--scenario",
                        "hangar-aerial", "--frames", "40", "--poses", "pipeline", "--inject", "disc",
                        "--out", str(out), "--progress", "0"], cwd=str(REPO / "application"),
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    s = json.loads((out / "summary.json").read_text())
    assert s["inject"]["markers"] > 0 and s["assoc"]["M1"]["N"] > 0
