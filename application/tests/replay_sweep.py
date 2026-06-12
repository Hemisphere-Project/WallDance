"""Replay sweep gate: re-run scenarios and byte-compare against the
decomp-phase0 archive (summary + per-frame timeline each).

Tiered usage (DECOMPOSITION_PLAN §6 — pick the smallest gate the diff can
actually fail):

  ui/ / gui* / docs-only diffs   replay imports none of it -> no sweep; the
                                 unit suite (import lints, callback coverage)
                                 is the gate.
  runtime/ or app.py diffs       --golden  (the 3-scenario trio, ~2 min):
                                 catches import/wiring breakage cheaply.
  core/ or config-default diffs  full sweep (all 12, ~7 min) — the corpus
                                 exists exactly for this; the trio leaves the
                                 relay/cold/yolo_first paths unexercised.
  decomp phase boundary          full sweep once, as the archived record.

Usage (from application/):
    .venv/Scripts/python.exe tests/replay_sweep.py <out_dir> [--golden]
    .venv/Scripts/python.exe tests/replay_sweep.py <out_dir> --scenarios a,b
"""
import argparse
import filecmp
import subprocess
import sys
import time
from pathlib import Path

# The committed golden trio (CORPUS_ANALYSIS §5): one floor, one aerial,
# one heavy-texture scene.
GOLDEN_TRIO = ("hangar-floor", "hangar-aerial", "texture-aerial")

TESTS = Path(__file__).resolve().parent
SCEN_DIR = TESTS / "scenarios"
GOLDEN = TESTS / "golden" / "decomp-phase0"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", help="output directory for produced summaries/timelines")
    ap.add_argument("--golden", action="store_true",
                    help=f"only the golden trio {GOLDEN_TRIO}")
    ap.add_argument("--scenarios", default=None,
                    help="comma-separated scenario names (default: all)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    progress = out / "progress.log"

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with progress.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    all_names = sorted(p.stem for p in SCEN_DIR.glob("*.json"))
    if args.scenarios:
        names = [n.strip() for n in args.scenarios.split(",") if n.strip()]
        unknown = sorted(set(names) - set(all_names))
        if unknown:
            log(f"ERROR: unknown scenarios {unknown} (have: {all_names})")
            return 2
    elif args.golden:
        names = [n for n in GOLDEN_TRIO if n in all_names]
        missing = sorted(set(GOLDEN_TRIO) - set(names))
        if missing:
            log(f"ERROR: golden trio scenario(s) missing: {missing}")
            return 2
    else:
        names = all_names

    log(f"replay sweep: {len(names)} scenario(s) "
        f"({'golden trio' if args.golden else 'full' if not args.scenarios else 'custom'})")
    failures = []
    for name in names:
        log(f"START {name}")
        summary = out / f"{name}.summary.json"
        timeline = out / f"{name}.timeline.json"
        run_log = out / f"{name}.run.log"
        proc = subprocess.run(
            [sys.executable, str(TESTS / "replay.py"),
             "--scenario", str(SCEN_DIR / f"{name}.json"),
             "--out", str(summary), "--timeline", str(timeline)],
            capture_output=True, text=True, timeout=1800,
        )
        run_log.write_text(proc.stdout + ("\n--- STDERR ---\n" + proc.stderr
                                          if proc.returncode else ""),
                           encoding="utf-8")
        if proc.returncode != 0:
            failures.append(f"{name}: replay rc={proc.returncode}")
            log(f"DONE {name} rc={proc.returncode} (FAIL)")
            continue
        for produced, golden in ((summary, GOLDEN / f"{name}.summary.json"),
                                 (timeline, GOLDEN / f"{name}.timeline.json")):
            if not golden.exists():
                failures.append(f"{name}: missing golden {golden.name}")
            elif not filecmp.cmp(produced, golden, shallow=False):
                failures.append(f"{name}: {produced.name} differs from golden (bytes)")
        verdict = "byte-identical" if not any(f.startswith(name) for f in failures) \
            else "MISMATCH"
        log(f"DONE {name} rc=0 {verdict}")

    log("ALL DONE")
    if failures:
        log("FAILURES:")
        for f in failures:
            log("  " + f)
        return 1
    log(f"VERDICT: all {len(names)} scenario(s) byte-identical to decomp-phase0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
