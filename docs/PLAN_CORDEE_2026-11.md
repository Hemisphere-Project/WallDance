# Plan Cordée — N dancers on the wall, each accounted for until seen leaving

**Date:** 2026-11-18 (Phase R added the same day) · **Status:** proposed to Thomas, to be ratified through the
hub · **Branch:** `remote-ops`
· **Evidence:** [AUDIT_2026-11.md](AUDIT_2026-11.md) and [audit-2026-11/](audit-2026-11/README.md).

*Cordée*: a roped party. A known number of people on a wall, and nobody is lost track of. That is the
requirement: the technician says how many dancers are on the wall, and each keeps a point from the moment she
appears until she is seen leaving, through contact, stillness, a camera stall and a dusk fade.

Step ids are `CRD-<phase><n>` (written `A1`, `B4`, … below). They are new; they do not reuse the roadmap's
labels. This plan orders the work; [ROADMAP.md](ROADMAP.md) §3–§5 keeps the older items it does not mention.

---

## 1. How the plan runs

- **Where.** On **mgr-ran**, in bench sittings: the code, 81 GB of takes, the TensorRT engines and the RTX 3090
  are there (Thomas, 2026-11-18). The hub's loop runs on dev37, which has no GPU and no takes, and WallDance is
  not armed for it.
- **The hub is the ledger.** Each step below is one hub row. Its lane says who moves next:

  | Lane | Hub form | Meaning |
  |---|---|---|
  | bench | `@who(bench)`, host mgr-ran | Code and tests in a session on mgr-ran. |
  | bench ⚙ | same | Needs no GPU and no takes. It would mint to the machine (`@who(claude)`) if Thomas arms WallDance for the loop. |
  | bench GPU | same | Needs the GPU, the engines or the takes. Stays on mgr-ran whatever is armed. |
  | laptop | `@who(bench)`, waiting on the technician | Needs the show laptop powered and on the tailnet. |
  | field | `@who(thomas) @tag(field)` | Dancers and the rig at the hangar. |
  | decide | `[w] @who(thomas)` + an ask | A decision, through the question system. |

- **The rig (Phase R, added 2026-11-18).** mgr-ran becomes a rig host the loop can use: a runner there pulls a
  pushed commit, runs the gates the project declares, and files the verdict to the hub. Once it exists and
  WallDance is armed (R3), the ⚙ rows mint to the machine and the GPU gates run without a sitting. What stays
  on the bench: label spot-checks, the one-time golden re-baseline of A7, the laptop windows. Landings stay
  Thomas's tap: `remote-ops` has two committers, so the hub treats it as a shared base.
- **Branches.** Work lands on `remote-ops`, one commit per sub-step, subject ending with the step id
  (`… [CRD-A2]`) and a `Refs-37: walldance#t-NNN` trailer. `release` moves only by Thomas's push at a release
  decision (A8, G2, G3). `main` is not used.
- **Behaviour changes** follow one discipline: the synthetic scenario that shows today's behaviour is committed
  first (B3); the fix goes behind a project key; the scenario's expectation flips in the same commit; the
  footage gate of the phase (C7, D3) runs before any release.
- **Gates** are tables in §5. A step is done when its gate line is true, and the status board (§4) is updated
  in the same commit.
- **Waves.** The hub mints wave 1 at ratification, wave 2 at G1, wave 3 at G2, so the board never holds rows
  that a later decision could void.

## 2. The Cordée KPI

Measured on the **OSC-emitted stream**, with the **shipped defaults**, in **wall-clock time**, against hand
labels, per take:

| Metric | Target |
|---|---|
| Each labelled dancer has her own point within 0.75 body-height (in contact, the shared position counts for both) | ≥ 95 % of her frames on the wall |
| Longest time a dancer on the wall has no point | ≤ 1 s |
| Points on no dancer | ≤ 2 % of emitted points |
| Coasting (held) points | ≤ 10 % |
| Jitter at rest | ≤ 1.5 % of body height |
| "More dancers than N" alerts with N set right | 0 |
| Laptop, with TouchDesigner, N = 6 | 20 fps sustained, GPU < 83 °C |
| Ratchet | no labelled take worse than the last release |

## 3. Steps

Written in the hub's plan grammar. `after:` names the steps that must be done first.

### Phase A — Anchor: make what ships safe and measurable (wave 1)

- **A1 · A test gate that works anywhere.** ① fix the collection error on the unguarded import that stops CI on
  `release` ② `wdremote release-check --tests` returns non-zero on a failed suite ③ write the CPU-only verify
  command into `docs/REMOTE_OPS.md`. Gate: the suite collects and passes in an environment without torch.
  Source: tests-harness#8, dim-validation F2. — @who(bench) ⚙ @est(3h) repo: walldance — why-not-machine:
  WallDance is not armed for the loop; no GPU needed, so it mints to the machine if armed → task
- **A2 · A restart restores the show.** ① a project load reads the empty-wall plate from the project being
  loaded ② the live plate is saved every minute and reloaded at start ③ app log lines carry a timestamp. Gate:
  unit tests for ① and ②; a restart on project B never opens A's plate. Source: runtime-core#4,
  regime-dusk-light-drift. — @who(bench) ⚙ @est(4h) repo: walldance — why-not-machine: not armed; loop-able
  → task
- **A3 · A hang leaves a diagnosis.** The watchdog's stack dump works under the log tee, is armed once a
  second, and is cancelled before shutdown. Gate: a test that blocks the loop and finds the stacks in the log.
  Source: config-ops#5. — @who(bench) ⚙ @est(2h) repo: walldance — why-not-machine: not armed; loop-able → task
- **A4 · Operator-safety quick fixes.** ① single-letter shortcuts are ignored while a text field has focus
  (ARCH-17) ② RUN/STANDBY has one source of truth and the badge follows a remote state change ③ a red banner
  "LECTURE – pas en direct" whenever RUN plays a recording. Gate: headless GUI tests. Source: gui-operator#1,
  #3, #5. — @who(bench) ⚙ @est(4h) repo: walldance — why-not-machine: not armed; loop-able → task
- **A5 · Config parity and recipe check.** ① save writes the five keys that load applies ② a test compares the
  saved, applied and replay-applied key sets, with today's differences as a shrinking exception list ③
  `recipe_deviations()` at project load, as a log line and a readiness row (project against
  `DETECTION_DEFAULTS`). Gate: the tests; a project at imgsz 800 shows the row. Source: config-ops#3,
  dim-config-surface F1–F2. — @who(bench) ⚙ @est(5h) repo: walldance — why-not-machine: not armed; loop-able
  → task
- **A6 · Calibration correctness.** ① the exposure servo commands the camera when it clamps ② a cancelled or
  failed Calibrate restores the previous enhancement, and the plate is written at Save ③ the alert text and
  the French note stop prescribing "play a take in RUN" and "paint, then Calibrate again". Gate: unit tests for
  ① and ②. Source: motion-calibration#3, #6, #7, gui-operator#3. — @who(bench) ⚙ @est(5h) repo: walldance —
  why-not-machine: not armed; loop-able → task
- **A7 · Gates that cover what ships.** ① scenario manifests gain a shipped-recipe twin (D27: x@1280,
  confidence 0.15, intermittent confirm) ② the six emitted fixtures are re-recorded at D27, with foreground,
  belt and bounds passed in their replay ③ D34 and the stale-plate fix are replayed on the night takes that
  contain real stalls. Gate: goldens regenerated once and reviewed; a table of ③ in §5. Source: dim-validation
  F5–F6, estimator judge. — @who(bench) @est(1d) repo: walldance — why-not-machine: needs the GPU, the engines
  and the takes on mgr-ran → task
- **A8 · Release D34 and the Anchor fixes.** After: A7, L1. Ask: push `remote-ops` to `release`? With the
  command card. — @who(thomas) @est(0.5h) repo: none — why-not-machine: moving `release` is Thomas's push (D1)
  → task

### Phase R — Rig: mgr-ran's hardware, usable by the loop (wave 1)

- **R1 · mgr-ran as a rig host.** Generic, for any project that needs this box: the RTX 3090, the Blackbox on
  its switched USB hub and SD mux, the Fireface. It lands in 37Dev, not here, and is proposed there as its own
  plan (hub inbox, 2026-11-18). In short:
  - a runner on mgr-ran that **pulls**: no inbound access, and since the box is off by default nothing queues
    against it;
  - **resources** declared once on the host (`gpu:rtx3090`, `usb-power:blackbox`, `sdmux:blackbox`,
    `audio:fireface`, a project's data folder), each with a lock and take/release hooks that are the host's
    own, trusted code;
  - **gates** declared by each project in its own repo, run as untrusted code: a container, no network, data
    read-only, only the devices the gate names;
  - a **hold** Thomas can put on a resource or on the whole box, because it is his workstation and his
    instruments;
  - verdicts filed to the hub through the `37` bridge, where the loop stamps them
    (`brain-code-turn rig … --host mgr-ran`).

  — a 37dev row; here it is a dependency only
- **R2 · WallDance's gates on the rig.** After: A1, R1. ① `rig.toml` at the repo root, with the gates below ②
  the container recipe: the pinned stack, the engines and the takes mounted read-only ③ each gate writes a
  verdict file. Gate: every gate green through the runner, from a clean checkout of `remote-ops`. — @who(bench)
  @est(1d) repo: walldance — why-not-machine: proving the gates needs mgr-ran's GPU and takes, and it is the
  rig's first use → task
- **R3 · Arm the loop.** After: R2. Ask: arm `walldance:branch`, with `remote-ops` as its base and A1's command
  as `verify:`? — @who(thomas) @est(0.25h) repo: none — why-not-machine: arming a repo is Thomas's → task

  | Gate | Needs | Takes about | Proves | Used by |
  |---|---|---|---|---|
  | `unit` | – | 2 min | the unit suite, torch included | every step |
  | `scenarios` | – | 2 min | the five regimes' synthetic scenarios (B3) | C1–C6, D2 |
  | `emitted` | – | 1 min | the emitted-stream fixtures | C1–C6, D0 |
  | `goldens` | `gpu:rtx3090`, `takes:walldance` | 10 min | the footage goldens at the shipped recipe (A7) | D0, D6, any tracker change |
  | `stalls` | `gpu:rtx3090`, `takes:walldance` | 10 min | D34 on the takes with real stalls | A7 ③, C6 |
  | `score` | `gpu:rtx3090`, `takes:walldance` | 30–60 min | the §2 KPI on the labelled takes, with the ratchet | B4, B7, C7, D3 |

  Durations are estimates.

### Phase B — Baseline: evidence on what the show needs (wave 1)

- **B1 · Time-faithful takes.** ① the recorder writes a per-frame sidecar (camera timestamp, frame id) ②
  `replay.py` and `flow_check.py` run on the sidecar clock when present ③ a test: a sidecar with a 1.7 s gap
  reproduces a stall offline. Source: camera-recording#2, #6, tests-harness#9. — @who(bench) ⚙ @est(1d) repo:
  walldance — why-not-machine: not armed; loop-able → task
- **B2 · A scorer for the emitted stream.** ① a sparse label format (frame, dancer, x, y, on or off the wall)
  ② `score_emitted`: the §2 metrics, a verdict file keyed by commit, a non-zero exit, the ratchet ③ a pre-label
  helper (YOLO ≥ 0.5 and foreground) with contact sheets for the spot check. Gate: unit tests on synthetic
  timelines. Source: dim-validation rethink. — @who(bench) ⚙ @est(1.5d) repo: walldance — why-not-machine: not
  armed; loop-able → task
- **B3 · The regime scenarios in the suite.** Port the audit's CPU harness (the real `FrameProcessor` with
  `model=None`) and the five regimes' scenarios as tests that state today's behaviour. Source:
  `tmp_analysis/audit-2026-11/{contact,stillness,n_dancers,vertical,dusk}`. — @who(bench) ⚙ @est(1d) repo:
  walldance — why-not-machine: not armed; loop-able → task
- **B4 · Fair baseline T0.** After: A7, B2. The shipped recipe with the plate on the two April duos and the
  three real-plate solo projects; half-rate and injected-stall replays; about 300 labels per take, pre-labelled
  and spot-checked by Thomas. Gate: table T0 in §5, each loss attributed to a regime. — @who(bench) @est(1d)
  repo: walldance — why-not-machine: needs the GPU and the takes on mgr-ran; labels need Thomas's eye → task
- **B5 · The shoot note.** `docs/TOURNAGE_CORDEE_FR.md`: empty wall per lighting; contact (embraces of 3, 5,
  10 s, two lifts, six crossings); stillness (30 s hangs, partner near and far); 3 to 6 dancers; roof and floor
  entries with a pause at the border; an empty wall across dusk; dusk with dancers. — @who(bench) ⚙ @est(2h)
  repo: walldance — why-not-machine: not armed; loop-able → task
- **B6 · Shoot the Cordée takes.** After: B5, L2. — @who(thomas) @tag(field) @est(4h) repo: none —
  why-not-machine: dancers and the rig at the hangar are Thomas's and the technician's → task
- **B7 · Baseline T1.** After: B4, B6, L1. Score the rehearsal duo takes and the Cordée takes with the shipped
  stream. Gate: table T1 in §5. — @who(bench) @est(1d) repo: walldance — why-not-machine: needs the GPU and
  the takes on mgr-ran → task
- **G1 · Read T0.** After: B3, B4, D1. Ask: T0 and the estimator design are on the table: start Phase C in the
  proposed order, and build the offline estimator (D2)? — @who(thomas) @est(0.5h) repo: none —
  why-not-machine: the order of the tracker work and the estimator are Thomas's to decide → task
- **G2 · Read T1.** After: B7, C7, D3. Wave 2. Ask: release the regime fixes; and which stream has authority:
  shipped with the fixes, or the new estimator in live shadow? — @who(thomas) @est(0.5h) repo: none —
  why-not-machine: a release and the tracker's authority are Thomas's to decide → task

### Phase C — Regimes: fixes inside the current design (wave 2, after G1 and B3)

- **C1 · Contact.** ① `_merge_duplicate_tracks` gets the co-fed veto and skips occluded tracks ② a contact
  hold: a slot with reputation that loses its measurement beside a live slot keeps its id at the shared
  position, exempt from the slot merge and from coast expiry, capped at 30 s ③ no shadow suppression or
  keypoint splice of a detection that sits on an established track. Source: tracker#3, identity-output#2,
  detection-pipeline#3. — @who(bench) ⚙ @est(2d) repo: walldance — why-not-machine: not armed; loop-able → task
- **C2 · Stillness.** ① cross-validation keeps a detection on an emitting or just-lost slot ② warm-up is
  restored when a reported track is re-matched by a real skeleton ③ static yield never evicts a slot with
  reputation ④ the plate update protects every emitting slot with reputation. Source: regime-long-stillness,
  tracker#6, identity-output#3. — @who(bench) ⚙ @est(2d) repo: walldance — why-not-machine: not armed;
  loop-able → task
- **C3 · N declared.** ① a large N selector (1–6) on the Live panel, saved with the project and settable
  remotely; the report cap follows N ② a persistent alert when an established dancer has no slot for 3 s ③ a
  slot whose track is alive is not free for rebind ④ the dedupe pair test is vectorised. Source:
  regime-n-greater-than-2. — @who(bench) ⚙ @est(2d) repo: walldance — why-not-machine: not armed; loop-able
  → task
- **C4 · Roof and floor entries.** ① tracker edges in two dimensions ② a box cut by the ROI border is exempt
  from the 8-keypoint rule, and its position comes from the inner edge and the known height ③ the slot's
  border hold uses the configured height. Source: regime-vertical-entries. — @who(bench) ⚙ @est(1.5d) repo:
  walldance — why-not-machine: not armed; loop-able → task
- **C5 · Relight.** ① a command and a Live button: the gain-only servo brings the ROI back to its calibrated
  level with dancers on the wall, within ±12 dB of the calibrated anchor, logged, with Revert; the anchor is
  never overwritten ② a stale plate is a persistent alert and its status line updates itself ③ the remedies
  point to Relight. Source: regime-dusk-light-drift; Thomas 18/11 (manual, no automatic follow). — @who(bench)
  ⚙ @est(1.5d) repo: walldance — why-not-machine: not armed; loop-able → task
- **C6 · After a stall.** Track velocity is reset after a credited stall, behind `camera_stall_hold`. Source:
  PLAN_25M §C.15. — @who(bench) ⚙ @est(3h) repo: walldance — why-not-machine: not armed; loop-able → task
- **C7 · Regime gate T2.** After: C1–C6. The T0 takes re-scored with the fixes on; no guard take worse; the
  goldens regenerated deliberately. Gate: table T2 in §5. — @who(bench) @est(0.5d) repo: walldance —
  why-not-machine: needs the GPU and the takes on mgr-ran → task

### Phase D — The estimator: N declared targets, in seconds (waves 1 to 3)

- **D1 · Design note.** `docs/CORDEE_ESTIMATOR.md`: state per target, typed measurements (skeleton, plate
  foreground, belt), one assignment per frame, entries at declared lines, exits observed, the contact group,
  at most 30 parameters in seconds and body-heights. Wave 1. — @who(bench) ⚙ @est(0.5d) repo: walldance —
  why-not-machine: not armed; loop-able → task
- **D0 · Dead code out.** After: A7, G1. The swap correctors and their plumbing (about 550 lines), the L > 1
  live branch, the 22 slot parameters of switched-off mechanisms. Gate: every golden byte-identical. Wave 2. —
  @who(bench) @est(1d) repo: walldance — why-not-machine: the byte-identical check needs the footage goldens
  on mgr-ran → task
- **D2 · The offline estimator.** After: G1, B3. `core/cordee.py`, fed from the detect cache: ① targets and
  assignment on skeletons ② foreground blobs, and one blob for a contact group ③ entries and exits ④ belt ⑤
  the B3 scenarios as its acceptance tests. Wave 2. — @who(bench) ⚙ @est(6d) repo: walldance —
  why-not-machine: not armed; loop-able → task
- **D3 · Shadow against shipped.** After: D2, B4. Both streams on T0 (and T1 when it exists). Gate: on-body
  +3 points or held share halved; coverage not lower; no more ghost frames; half-rate and stall replays within
  1 point. Wave 2. — @who(bench) @est(1d) repo: walldance — why-not-machine: needs the GPU and the takes on
  mgr-ran → task
- **D4 · Live shadow.** After: G2. The estimator runs live behind a key: logged and drawn, never sent; 2 ms
  p95. Wave 3. — @who(bench) ⚙ @est(2d) repo: walldance — why-not-machine: not armed; loop-able → task
- **D5 · A field period.** After: D4. The shadow on the DEV slot through one rehearsal period; its log scored.
  Wave 3. — @who(bench) @est(2h) repo: walldance — why-not-machine: needs the show laptop and a rehearsal → task
- **G3 · Authority.** After: D5. Ask: the estimator takes the OSC stream, with the shipped path as fallback?
  Wave 3. — @who(thomas) @est(0.5h) repo: none — why-not-machine: Thomas's to decide → task
- **D6 · Deletions.** After: G3 and one field period. The dormant pool, the merges, the motion bridge, the
  synthetic blobs, the cascade, the identity cost terms. Wave 3. — @who(bench) @est(2d) repo: walldance —
  why-not-machine: the goldens that prove nothing else moved are on mgr-ran → task

### Phase E — Operator and documentation

- **E1 · Show mode, language, temperature.** Ask, three questions: what SPECTACLE locks and what it leaves (N,
  Hold, Stability, Relight); French labels in the app, or English labels with a French manual (D10); the GPU
  temperature alert, whose 85 °C sits inside the laptop's normal range. Wave 1. — @who(thomas) @est(0.5h)
  repo: none — why-not-machine: Thomas's to decide → task
- **E2 · A health strip.** After: E1. Every alert kind stays in the strip until cleared. Wave 1. — @who(bench)
  ⚙ @est(1d) repo: walldance — why-not-machine: not armed; loop-able → task
- **E3 · Show lock, and the Live panel regrouped.** After: E1, C3, C5. In SPECTACLE: playback, Calibrate,
  project switch and ROI editing are refused; Live shows N, Hold, Stability, Relight and the health strip; the
  rest moves behind expert. Wave 2. — @who(bench) ⚙ @est(1.5d) repo: walldance — why-not-machine: not armed;
  loop-able → task
- **E4 · Mode d'emploi.** After: E3. `docs/MODE_EMPLOI_FR.md` and its PDF: install, calibrate, run, and a table
  "si ça ne marche pas" for every alert. Wave 2. — @who(bench) ⚙ @est(1d) repo: walldance — why-not-machine:
  not armed; loop-able → task
- **E5 · Front doors and registers.** README and the docs index describe today; a banner on `NEW_SHOW`;
  `docs/DECISIONS.md` (D1–D34 and the answers of 2026-11-18); a release and rollback runbook with a release
  log in `REMOTE_OPS`; the journals, this audit's evidence included, move to `archives/`; mgr-ran and dev37
  named correctly. Wave 1. — @who(bench) ⚙ @est(1d) repo: walldance — why-not-machine: not armed; loop-able
  → task
- **E6 · Doc facts under test.** After: E5. Every OSC address and state in `OSC_CONTRACT` exists in the code.
  Wave 1. — @who(bench) ⚙ @est(0.5d) repo: walldance — why-not-machine: not armed; loop-able → task

### Phase L — Laptop windows

- **L1 · First window** (this is hub row #t-020, with sub-steps added). ① pull the rehearsal duo takes ② read
  both slots' code against `release` ③ recipe audit of every project with `apply_d33.py --dry-run` (whether it
  ran is unknown, Thomas 18/11) ④ the laptop's unit suite and launcher build ⑤ a per-frame timing dump at
  x@1280 with TouchDesigner running. — existing row, waiting on the technician
- **L2 · Second window.** After: B1, A7. Deploy the dev base to the DEV slot with the time sidecar; the USB
  stall A/B (N-15). Wave 1. — @who(bench) @est(3h) repo: walldance — why-not-machine: needs the show laptop
  powered and on the tailnet → task
- **L3 · Third window.** After: C3, C5. Relight on the IDS in RUN provokes no stall; the technician tries the
  N selector; timing at N = 4 to 6 with TouchDesigner. Wave 2. — @who(bench) @est(3h) repo: walldance —
  why-not-machine: needs the show laptop powered and on the tailnet → task

## 4. Status board

Updated in the commit that changes a step's state. `todo` · `doing` · `done` · `held` (with the reason).

| Id | Step | Lane | After | Est | Status | Evidence |
|---|---|---|---|---|---|---|
| A1 | Test gate that works anywhere | bench ⚙ | – | 3h | todo | |
| A2 | A restart restores the show | bench ⚙ | – | 4h | todo | |
| A3 | A hang leaves a diagnosis | bench ⚙ | – | 2h | todo | |
| A4 | Operator-safety quick fixes | bench ⚙ | – | 4h | todo | |
| A5 | Config parity and recipe check | bench ⚙ | – | 5h | todo | |
| A6 | Calibration correctness | bench ⚙ | – | 5h | todo | |
| A7 | Gates that cover what ships | bench GPU | – | 1d | todo | |
| A8 | Release D34 and the Anchor fixes | decide | A7, L1 | – | todo | |
| R1 | mgr-ran as a rig host (lands in 37Dev) | external | – | – | proposed to 37dev | |
| R2 | WallDance's gates on the rig | bench GPU | A1, R1 | 1d | todo | |
| R3 | Arm the loop | decide | R2 | – | todo | |
| B1 | Time-faithful takes | bench ⚙ | – | 1d | todo | |
| B2 | Scorer for the emitted stream | bench ⚙ | – | 1.5d | todo | |
| B3 | Regime scenarios in the suite | bench ⚙ | – | 1d | todo | |
| B4 | Fair baseline T0 | bench GPU | A7, B2 | 1d | todo | |
| B5 | Shoot note | bench ⚙ | – | 2h | todo | |
| B6 | Shoot the Cordée takes | field | B5, L2 | 4h | todo | |
| B7 | Baseline T1 | bench GPU | B4, B6, L1 | 1d | todo | |
| G1 | Read T0 | decide | B3, B4, D1 | – | todo | |
| G2 | Read T1 | decide | B7, C7, D3 | – | todo | |
| C1 | Contact | bench ⚙ | G1 | 2d | todo | |
| C2 | Stillness | bench ⚙ | G1 | 2d | todo | |
| C3 | N declared | bench ⚙ | G1 | 2d | todo | |
| C4 | Roof and floor entries | bench ⚙ | G1 | 1.5d | todo | |
| C5 | Relight | bench ⚙ | G1 | 1.5d | todo | |
| C6 | After a stall | bench ⚙ | G1 | 3h | todo | |
| C7 | Regime gate T2 | bench GPU | C1–C6 | 0.5d | todo | |
| D1 | Estimator design note | bench ⚙ | – | 0.5d | todo | |
| D0 | Dead code out | bench GPU | A7, G1 | 1d | todo | |
| D2 | Offline estimator | bench ⚙ | G1, B3 | 6d | todo | |
| D3 | Shadow against shipped | bench GPU | D2, B4 | 1d | todo | |
| D4 | Live shadow | bench ⚙ | G2 | 2d | todo | |
| D5 | A field period | laptop | D4 | 2h | todo | |
| G3 | Authority | decide | D5 | – | todo | |
| D6 | Deletions | bench GPU | G3 | 2d | todo | |
| E1 | Show mode, language, temperature | decide | – | – | todo | |
| E2 | Health strip | bench ⚙ | E1 | 1d | todo | |
| E3 | Show lock, Live regrouped | bench ⚙ | E1, C3, C5 | 1.5d | todo | |
| E4 | Mode d'emploi | bench ⚙ | E3 | 1d | todo | |
| E5 | Front doors and registers | bench ⚙ | – | 1d | todo | |
| E6 | Doc facts under test | bench ⚙ | E5 | 0.5d | todo | |
| L1 | First laptop window (#t-020) | laptop | – | 4h | waiting on the technician | |
| L2 | Second laptop window | laptop | B1, A7 | 3h | todo | |
| L3 | Third laptop window | laptop | C3, C5 | 3h | todo | |

## 5. Gate tables

Filled by the step named. Until then the cell says so.

- **A7 ③ — D34 on takes with real stalls:** not measured yet.
- **T0 — shipped stream, takes already on mgr-ran (B4):** not measured yet.
- **T1 — shipped stream, rehearsal duo and Cordée takes (B7):** not measured yet.
- **T2 — shipped stream with the regime fixes (C7):** not measured yet.
- **D3 — estimator against shipped:** not measured yet.

## 6. Existing hub rows

| Row | Becomes |
|---|---|
| #t-019 bench session 18/11 | Produced this audit and plan. Thomas closes it. |
| #t-020 remote session on the laptop | L1, with the recipe audit, the suite and the timing dump added. |
| #t-014 duo number | Folded into B7. |
| #t-018 readiness verdict | Folded into G2. |
| #t-010 clean-plate foreground evidence | Folded into B4 and D3. |
| #t-016 order the front BP850 and floodlights, #t-017 rig upgrade | Unchanged. B6 can ride on #t-017's visit. |

## 7. Risks

- No show-like footage with several dancers exists before L1 and B6. → Phases A, B1–B5, C, D1–D3 and E do
  not wait for it; nothing from Phase C reaches `release` before G2.
- The technician's availability gates L1, L2, L3, B6, B7, G2 and D5. → Each laptop window carries a short,
  fixed list; the plan names what does not depend on it.
- Synthetic scenarios prove what the logic does, not how often YOLO produces those inputs. → T1 decides.
- A7 changes the reference goldens. → Once, deliberately, before any behaviour change, with the diff read.
- Relight writes the camera during RUN, and camera writes may provoke stalls. → L3 checks it on the IDS before
  it is released.
- The estimator may not beat the shipped stream with the regime fixes. → G2 may answer "keep shipped". The
  scenarios, the scorer and the labels are kept either way; D2 is the price of knowing.
- The shadow period runs two estimators on a laptop with 6–10 ms of slack per frame. → 2 ms p95 budget in D4.
- About 300 hours of session work on mgr-ran. → Ordered by gates; each sitting ends on a status-board commit.
- This audit added about 880 KB under `docs/`. → E5 archives it.
- The rig runs code a loop turn wrote, on Thomas's home workstation. → Gates run in a container with no
  network and the takes read-only; only the host's own hooks touch devices; stopping the runner revokes it.
- mgr-ran is off by default and is also his desk. → The runner pulls, so nothing waits on it; a hold takes a
  resource or the whole box back at once; a gate that needs the GPU checks free memory first.
- The rig depends on a plan in another project (R1). → Nothing else in this plan waits on it: until R3, every
  step runs as a bench sitting, as written.

## 8. Spend

- Rows: 42 new and 1 existing extended, plus one row in 37dev (R1). 24 bench ⚙ · 8 bench GPU · 3 laptop ·
  1 field · 6 decide.
- Wave 1 (at ratification): 24 rows. Wave 2 (at G1): 14. Wave 3 (at G2): 4.
- Session work on mgr-ran: about 236 h loop-able, 68 h with the GPU, 8 h with the laptop (plus L1's 4 h).
  After R3 the loop-able share runs on the loop, and the GPU gates run on the rig without a sitting.
- Thomas: about 24 h (five decisions, one shoot, laptop windows, label spot-checks, reading the manual).

## 9. Checking where we are

From a terminal on mgr-ran, in this order:

1. `37 where walldance` — the hub's rows: what is open, waiting, or needs Thomas.
2. §4 of this file on `remote-ops` — one line per step.
3. `git log --oneline --grep='CRD-' remote-ops` — what landed, by step id.
4. §5 — the numbers each gate produced.

In a session, "where are we on Cordée" is answered from those four, in that order, and any difference between
the hub's rows and §4 is the first thing to say.

## 10. Open at ratification

- **Arm the loop?** Now step R3, asked once the rig runs WallDance's gates (R2). Until then the plan runs as
  bench sittings on mgr-ran.
- **The hub's record** names `main` as the repo's branch; the work is on `remote-ops`.
- **A bench session** on mgr-ran for these rows, in the order of §4.
