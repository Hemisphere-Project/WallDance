# Audit 2026-11 — evidence

Reference material behind [AUDIT_2026-11.md](../AUDIT_2026-11.md). **Do not plan from these files**: the plan is
[PLAN_CORDEE_2026-11.md](../PLAN_CORDEE_2026-11.md). They exist so a step of the plan can open the finding it
cites (file:line, reproduction, fix sketch) without redoing the audit.

Audited: branch `remote-ops` @ `d2ecfde` (2026-10-08), on mgr-ran, 2026-11-18. Read-only: no code changed.

| File | What it is |
|---|---|
| [maps/](maps/) | One page per subsystem: what the code does today (purpose, architecture, data flow, threads, failure handling, tunables, strengths). Written from the code alone. The closest thing the repo has to an architecture description. |
| [findings-blind.md](findings-blind.md) | The 119 findings of the first, code-only pass, by subsystem. Ids `<subsystem>#<n>`. Each carries its re-check verdict when it had one. |
| [defects-verified.md](defects-verified.md) | The 61 findings that were re-checked: verdict, evidence, reproduction, field impact, fix sketch. 53 confirmed, 7 partly, 1 by design, 0 refuted. |
| [estimator-panel.md](estimator-panel.md) | The tracking-core question argued both ways (keep and evolve / re-found), and the judge's ruling with the inventory it verified. |
| `dim-*.md` | Seven context-aware reports (runtime architecture, performance, field robustness, operator UX, docs, validation, config surface), each followed by an adversarial review. |
| `regime-*.md` | The five production regimes the owner confirmed on 2026-11-18, traced through the real chain with synthetic inputs. |

Reproduction scripts: `tmp_analysis/audit-2026-11/` (only `.py` is tracked there). They were written to run from
a session scratch folder; some carry absolute paths that need adjusting.

## How far to trust it

- **Subsystem maps and blind findings** come from reading code only. 61 of 119 findings were re-checked and none
  was refuted; the other 58 are leads.
- **Dimension reports** read docs and code. Where the adversarial review weakened a claim, the review is the
  safer reading. Several auditor conclusions ran ahead of their evidence; the reviews say which.
- **Regime results** drive the real `FrameProcessor` with `model=None` on synthetic detections. They prove what
  the logic does and its time constants. They say nothing about how often YOLO produces those inputs under IR.
- **Laptop numbers at x@1280 are projections.** Every laptop timing in the repo was taken at imgsz 800, none with
  TouchDesigner running.
- One premise in the auditors' brief was wrong and the reviews caught it: the October projects store 25 ms
  exposure; the 20 fps comes from the `IDS_MAX_FPS` cap, not from a 50 ms exposure.
- Machine names: the box with the code, the takes and the RTX 3090 is **mgr-ran**. Older docs call it "dev37";
  dev37 is the always-on VM that runs the hub's loop and has neither GPU nor takes.
