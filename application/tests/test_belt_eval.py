"""tmp_analysis/belt_eval.py: the IR-budget arithmetic, the exposure-ladder
fit, the .meta v2 / camlog exposure timeline and an end-to-end CLI run on a tiny
synthetic project (empty slot + exposure ladder take, FFV1). Fast; no GPU."""
import json
import pickle
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO / "tmp_analysis"))      # append: never shadow app/test modules
import belt_eval as be  # noqa: E402

be.import_helpers(REPO)


def _args(**kw):
    a = be.build_parser().parse_args(["--project", "x", "--slot", "1"])
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _take(belts=(), tracks=(), dist=25.0, role=""):
    return {"take": {"role": role}, "camera": {"distance_m": dist},
            "_samples": {"belts": list(belts), "tracks": list(tracks), "meta_gamma": None}}


def _belt(peak=100.0, e=40000.0, g=20.0, w=20.0, body=10.0):
    return {"peak": peak, "mean": 0.8 * peak, "w": w, "h": 5.0, "sat": 0.0, "e": e, "g": g, "body": body}


def test_budget_wall_model_arithmetic():
    """100 DN at 40 ms / 20 dB = 0.25 DN/ms@0dB; at 20 ms / 24 dB / 25 m that is
    79.2 DN -> x1.52 for a 120-DN belt; x(40/25)^2 more at 40 m."""
    a = _args(min_samples=5, distance_model="wall", target_exposure_ms="20", distances="40",
              body_target_dn=5.0, projectors=2)
    bu = be.ir_budget({"t": _take([_belt() for _ in range(30)])}, a)
    assert bu["status"] == "ok" and bu["distance_model"] == "wall"
    assert bu["belt_dn_per_ms_0db_at_wall"]["peak_med"] == pytest.approx(0.25, rel=1e-3)
    r25, r40 = bu["table"]
    assert r25["distance_m"] == 25 and r40["distance_m"] == 40
    assert r25["belt_peak_dn_median"] == pytest.approx(0.25 * 20 * 10 ** (24 / 20), rel=1e-3)
    assert r25["light_x_belt"] == pytest.approx(120 / 79.24, rel=1e-2)
    assert r40["light_x_belt"] == pytest.approx(r25["light_x_belt"] * (40 / 25) ** 2, rel=1e-2)
    assert r40["projectors_needed"] == int(np.ceil(2 * r40["light_x_needed"]))
    # body: 10 DN at the same settings -> 7.92 DN at 20 ms / 24 dB; target 5 -> no extra light for it
    assert r25["body_dn_median"] == pytest.approx(7.92, rel=1e-2) and r25["light_x_body"] < 1


def test_budget_excludes_saturation_and_empty_takes():
    a = _args(min_samples=5, distance_model="wall")
    sat = [_belt(peak=255.0) for _ in range(50)]
    bu = be.ir_budget({"t": _take(sat + [_belt() for _ in range(10)]),
                       "empty": _take([_belt(peak=10.0) for _ in range(50)], role="empty")}, a)
    assert bu["takes_used"] == ["t"]
    assert bu["saturated_share_excluded"] == pytest.approx(50 / 60, rel=1e-3)
    assert bu["belt_dn_per_ms_0db_at_wall"]["peak_med"] == pytest.approx(0.25, rel=1e-3)


def test_budget_size_model_scales_near_samples_back_to_the_wall():
    """Belt-only: a band twice as wide is half as far, so 4x brighter at the same
    light; scaled back to the wall it gives the same normalised signal."""
    a = _args(min_samples=5, distance_model="size", min_distance_frac=0.4)
    far = [_belt(peak=40.0, w=10.0) for _ in range(20)]
    near = [_belt(peak=160.0, w=20.0) for _ in range(20)]
    bu = be.ir_budget({"t": _take(far + near)}, a)
    assert bu["distance_model"] == "size" and bu["wall_size_px"] == pytest.approx(10.0)
    assert bu["belt_dn_per_ms_0db_at_wall"]["peak_med"] == pytest.approx(0.1, rel=1e-3)
    assert bu["belt_dn_per_ms_0db_at_wall"]["peak_p10"] == pytest.approx(0.1, rel=1e-3)


def test_budget_without_exposure_says_so():
    a = _args(min_samples=5)
    bu = be.ir_budget({"t": _take([_belt(e=None) for _ in range(30)])}, a)
    assert bu["status"].startswith("no usable belt samples")


def test_camera_timeline_precedence_and_plateaus():
    meta = {"camera": {"exposure_us": 30000.0, "gain_db": 12.0},
            "_camlog_series": [(0, 20000.0, 12.0), (20, 20000.0, 12.0), (40, 20000.0, 12.0),
                               (60, 10000.0, 12.0), (80, 10000.0, 12.0), (100, 10000.0, 12.0),
                               (120, 5000.0, 12.0), (140, 5000.0, 12.0), (160, 5000.0, 12.0)]}
    cam = be.CamTimeline(meta, SimpleNamespace(exposure_us=None, gain_db=None))
    assert cam.e_src == "camlog" and cam.at(70) == (10000.0, 12.0) and cam.at(500) == (5000.0, 12.0)
    pl = cam.plateaus()
    assert [round(p["e"]) for p in pl] == [20000, 10000, 5000] and pl[1]["f0"] == 60
    cli = be.CamTimeline(meta, SimpleNamespace(exposure_us=49327.0, gain_db=None))
    assert cli.e_src == "cli" and cli.at(70) == (49327.0, 12.0) and cli.plateaus() == []
    v1 = be.CamTimeline({}, SimpleNamespace(exposure_us=None, gain_db=None))
    assert v1.e_src == "unknown" and v1.at(3) == (None, None)


# ---------------------------------------------------------------------------
# end to end: synthetic project with an empty slot and an exposure ladder
# ---------------------------------------------------------------------------

def _write_meta(video: Path, frames: int, plateaus, gain=12.0):
    meta = {"actual_fps": 20.0, "frames": frames, "meta_version": 2, "codec": "FFV1",
            "camlog": video.name + ".camlog.jsonl",
            "camera": {"source": "ids", "nodes": {"ExposureTime": plateaus[0][1], "Gain": gain, "Gamma": 1.0,
                                                  "ExposureAuto": "Off", "GainAuto": "Off"}},
            "rig": {"camera_distance_m": 25.0, "illuminator_offset_cm": 5.0, "illuminator": "2x 850 nm",
                    "lens": "8 mm", "f_number": 1.4, "filter": "BP850", "markers": "belt front+back"},
            "config": {"roi_enabled": False}}
    Path(str(video) + ".meta").write_text(json.dumps(meta))
    lines = []
    for f0, e in plateaus:
        for k in range(4):                                  # ~1 Hz samples, 4 per plateau
            lines.append({"t": f0 / 20 + k, "frames": f0 + 5 * k,
                          "nodes": {"ExposureTime": e, "Gain": gain, "ExposureAuto": "Off"}})
    Path(str(video) + ".camlog.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def _project(tmp_path: Path):
    rec = tmp_path / "projects" / "synthbelt" / "recordings"
    rec.mkdir(parents=True)
    (tmp_path / "application" / "src" / "core").mkdir(parents=True)
    rng = np.random.default_rng(0)
    h, w = 240, 320

    def frame(belt_dn, body_dn):
        g = np.full((h, w), 10.0)
        cv2.circle(g, (40, 200), 3, 255, -1)                # static glint (lamp)
        if belt_dn:
            cv2.rectangle(g, (140, 70), (180, 190), body_dn, -1)     # body
            cv2.rectangle(g, (144, 128), (176, 133), belt_dn, -1)   # belt 33 x 6
        g = g + rng.normal(0, 1.2, g.shape)
        return np.clip(np.rint(g), 0, 255).astype(np.uint8)

    def write(name, frames):
        p = rec / name
        wr = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"FFV1"), 20, (w, h), isColor=True)
        for g in frames:
            wr.write(cv2.merge([g, g, g]))
        wr.release()
        return p

    empty = write("slot_1_20261006_200000.avi", [frame(0, 0) for _ in range(30)])
    _write_meta(empty, 30, [(0, 20000.0)])
    plateaus = [(0, 20000.0), (40, 10000.0), (80, 5000.0)]
    fr, rows = [], []
    for f in range(120):
        e = 20000.0 if f < 40 else 10000.0 if f < 80 else 5000.0
        fr.append(frame(200.0 * e / 20000.0, 30.0 * e / 20000.0))
        k = np.zeros((17, 2), np.float32)
        k[:, 0], k[:, 1] = 160.0, 100.0
        k[[5, 6], 1], k[[11, 12], 1] = 95.0, 135.0
        k[[5, 11], 0], k[[6, 12], 0] = 150.0, 170.0
        rows.append({"f": f, "id": 1, "kpts": k, "conf": np.full(17, 0.9, np.float32),
                     "bbox": np.array([138, 65, 45, 130], np.float32), "fss": 0})
    ladder = write("slot_7_20261006_201000.avi", fr)
    _write_meta(ladder, 120, plateaus)
    pd = tmp_path / "poses"
    pd.mkdir()
    with open(pd / f"poses_{ladder.stem}_s0_n0.pkl", "wb") as fh:
        pickle.dump({"video": str(ladder), "start": 0, "rows": rows}, fh)
    return pd


def test_cli_end_to_end_ladder_and_static(tmp_path):
    pd = _project(tmp_path)
    out = tmp_path / "out"
    rc = be.main(["--repo", str(tmp_path), "--project", "synthbelt", "--slot", "1,7", "--empty-slot", "1",
                  "--poses-dir", str(pd), "--floor", "30", "--min-delta", "20", "--min-samples", "10",
                  "--roi", "none", "--out", str(out), "--threads", "1"])
    assert rc == 0
    s = json.loads((out / "summary.json").read_text())
    jpgs = [p for p in out.iterdir() if p.suffix == ".jpg"]
    assert 1 <= len(jpgs) <= 3
    assert sum(p.stat().st_size for p in out.iterdir()) < 600_000
    lad = s["takes"]["slot7"]
    assert lad["camera"]["exposure_source"] == "camlog" and lad["camera"]["distance_m"] == 25.0
    # the empty slot builds the static map; the lamp never counts as a belt
    assert s["takes"]["slot1"]["take"]["role"] == "empty"
    assert s["takes"]["slot1"]["detection"]["fp_per_frame_empty_stage"] == 0
    assert lad["detector"]["static_empty"]["cells"] > 0
    assert lad["belt"]["n"] >= 100 and lad["detection"]["frames_with_belt_pct"] >= 95
    # ladder: three plateaus, DN linear in exposure (200 DN at 20 ms -> 10 DN/ms)
    pl = lad["ladder"]["plateaus"]
    assert [p["exposure_us"] for p in pl] == [20000, 10000, 5000]
    fit = lad["ladder"]["belt_peak_fit"]
    assert fit["r2"] > 0.98 and fit["dn_per_ms"] == pytest.approx(10.0, rel=0.15)
    # gated mode at the YOLO hips; the static (ladder) dancer is kept
    assert lad["poses"]["dancer_visible_pct_skeleton"] >= 95 and lad["poses"]["static_tracks_excluded"] == []
    bu = s["ir_budget"]
    assert bu["status"] == "ok" and bu["takes_used"] == ["slot7"] and bu["ladder"].startswith("ladder:")
    d = {(r["distance_m"], r["exposure_ms"]): r for r in bu["table"]}
    assert set(d) == {(25.0, 20.0), (25.0, 25.0), (30.0, 20.0), (30.0, 25.0), (40.0, 20.0), (40.0, 25.0)}
    assert d[(30.0, 20.0)]["light_x_belt"] == pytest.approx(d[(25.0, 20.0)]["light_x_belt"] * 1.44, rel=0.02)
    # 10 DN/ms at 12 dB -> at 20 ms / 24 dB / 25 m: 10 * 20 * 10^(12/20) ~ 800 DN -> no extra light
    assert d[(25.0, 20.0)]["light_x_belt"] < 0.5
    # the ladder slope gives the same answer directly: 10 DN/ms / 10^(12/20) = 2.51 DN/ms at 0 dB
    assert bu["belt_dn_per_ms_0db_ladder"] == pytest.approx(10.0 / 10 ** 0.6, rel=0.15)
    assert d[(25.0, 20.0)]["light_x_belt_ladder"] == pytest.approx(120 / (2.512 * 20 * 10 ** 1.2), rel=0.15)
    assert d[(40.0, 20.0)]["light_x_belt_ladder"] == pytest.approx(
        d[(25.0, 20.0)]["light_x_belt_ladder"] * (40 / 25) ** 2, rel=0.02)
