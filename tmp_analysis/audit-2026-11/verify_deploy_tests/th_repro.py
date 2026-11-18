"""tests-harness#3 / #7 / #12 reproductions (CPU only, fixtures + fakes)."""
import dataclasses
import inspect
import json
import sys
import tempfile
from pathlib import Path

T = Path("/data/WallDance/application/tests")
sys.path.insert(0, str(T))
sys.path.insert(0, str(T.parent / "src"))
sys.path.insert(0, str(T.parent))

import pytest  # noqa: E402
import emitted_golden as eg  # noqa: E402
import test_emitted_golden as teg  # noqa: E402

WORK = Path(sys.argv[1])

# ---------------------------------------------------------------- #7 (a) the exact compare elsewhere
print("=== tests-harness#7 (a): the exact compare under the laptop's / CI's numpy ===")
real_env = eg.env_versions
for label, env in (("dev37 (here)", real_env()), ("laptop pin (requirements-prod.txt)", {"numpy": "2.4.4", "scipy": "1.17.1"}),
                   ("CI (numpy<2.0)", {"numpy": "1.26.4", "scipy": "1.15.3"})):
    eg.env_versions = lambda env=env: env
    ran = skipped = 0
    for name in teg.FIXTURES:
        try:
            teg.test_emitted_stream_matches_golden(name)
            ran += 1
        except pytest.skip.Exception:
            skipped += 1
    print(f"  {label:36s} exact per-frame compare: {ran} run, {skipped} skipped")
eg.env_versions = real_env

# ---------------------------------------------------------------- #7 (b) how loose the floors are


def floors_ok(name, kpi):
    cov, ond, coast = teg.FLOORS[name]
    return (kpi["continuity.coverage"] >= cov and kpi["continuity.on_dancer"] >= ond
            and kpi["quality.coasting_share"] <= coast and kpi["quality.over_n_frames"] == 0)


print("\n=== tests-harness#7 (b): slot-layer changes that keep every KPI floor green ===")
base = eg.shipped_params()
fixtures = {n: eg.load_fixture(n) for n in teg.FIXTURES}
base_kpi = {n: eg.simulate(fx)[1] for n, fx in fixtures.items()}
variants = {
    "shipped": {},
    "coast_s 2.0 -> 1.5": {"coast_s": 1.5},
    "coast_s 2.0 -> 3.0": {"coast_s": 3.0},
    "stability -> 0 (no smoothing)": {"stability": 0.0},
    "stability -> 3 (max)": {"stability": 3.0},
    "smart_hold off": {"smart_hold": False},
    "static guard + yield off": {"static_guard": False, "static_yield": False},
    "filter_input raw": {"filter_input": "raw"},
}
print("  shipped coast_s =", base.coast_s, "| stability =", base.stability)
for label, ch in variants.items():
    p = dataclasses.replace(base, **ch)
    green, rows = 0, []
    for n, fx in fixtures.items():
        _, k = eg.simulate(fx, p)
        ok = floors_ok(n, k)
        green += ok
        b = base_kpi[n]
        rows.append(f"{n}: floors {'OK ' if ok else 'RED'} sw {b['quality.id_switches']}->{k['quality.id_switches']} "
                    f"gap {b['continuity.gap_max_s']}->{k['continuity.gap_max_s']}s "
                    f"jit {b['quality.jitter_rest_pct']}->{k['quality.jitter_rest_pct']}")
    print(f"\n  [{label}] KPI-floor tests green on {green}/6 fixtures")
    for r in rows:
        print("     " + r)

# what the fixture path feeds the slot layer vs the pipeline
from core import identity_slots as isl  # noqa: E402
import slot_replay  # noqa: E402
sig = inspect.signature(isl.IdentitySlots.update)
print("\n  IdentitySlots.update parameters:", list(sig.parameters)[1:])
print("  emitted_golden.simulate passes : candidates, t, hidden   (emitted_golden.py:115)")
src = inspect.getsource(slot_replay.candidates_from_row)
cand_fields = [f.name for f in dataclasses.fields(isl.SlotCandidate)]
print("  SlotCandidate fields:", cand_fields)
print("  fields the fixture path never sets:", [f for f in cand_fields if f + "=" not in src.replace(" ", "")])
print("  SlotParams fields shipped_params() leaves at the dataclass default although the pipeline sets them:",
      [f for f in ("belt_backing", "entry_min_travel_h") if f + "=" not in inspect.getsource(eg.shipped_params)])

# ---------------------------------------------------------------- #3
print("\n=== tests-harness#3: the seven golden numbers come from tracker log events only ===")
import analyze_session as an  # noqa: E402
import test_regression_replay as trr  # noqa: E402
print("  compared keys:", list(trr.TOLERANCE) + ["avg_detections"], "+ frames_processed, model")


def summary(lines):
    d = Path(tempfile.mkdtemp(dir=WORK))
    (d / "tracking_events.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    st = an.collect_stats(d / "tracking_events.jsonl")
    tr = an.classify_tracks(st)
    dets = [x.get("n_detections", 0) for x in st["frame_summaries"].values()]
    return {"real_tracks": len(tr["real"]), "marginal_tracks": len(tr["marginal"]), "ghost_tracks": len(tr["ghost"]),
            "total_tracks": len(tr["all"]), "swap_count": len(st["swap_events"]),
            "zero_detection_frames": dets.count(0), "avg_detections": round(sum(dets) / len(dets), 3)}


sample = None
for cand in sorted((T.parent / "src").glob("tracking_events.jsonl")) + sorted(T.parent.glob("tracking_events.jsonl")):
    sample = cand
print("  event-line shape taken from:", sample)
first = [json.loads(x) for x in sample.read_text().splitlines()[:400]] if sample else []
keys = sorted({k for e in first for k in e})
print("  event keys:", keys)
