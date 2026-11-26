# Plan Cordée — N dancers on the wall, each accounted for until seen leaving

**Date:** 2026-11-18 (Phase R added the same day) · **Revision 2:** 2026-11-25 · **Status:** rev 1 ratified
by Thomas through the hub on 2026-11-18; rev 2's order decided by him on 2026-11-25 · **Branch:** `remote-ops`
· **Evidence:** [AUDIT_2026-11.md](AUDIT_2026-11.md) and [audit-2026-11/](audit-2026-11/README.md); for rev 2,
the 08/10 façade rehearsal re-read frame by frame (`tmp_analysis/counter-0810/`, 13 takes, per-frame truth).

**What rev 2 changes.** The 08/10 rehearsal showed that the detector and the foreground lose the duo before
the tracker does: the dancer reaches the network at 80 px, the shipped foreground gives three to five false
blobs a frame, and the operator could undo Calibrate without a warning. Rev 2 adds six step families — **M**
measure, **P** pixels, **X** exposure and Calibrate, **F** foreground, **H** guidance, **U** camera link — and
three releases (§1, « Order since rev 2 »). Pixels and foreground go **before** phases C and D (Thomas,
2026-11-25: « yes if useful »), so the regime fixes and the estimator are measured against a detector and a
foreground that will not move under them again.

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
- **Rev 2 steps** are minted with wave 1 (hub, 2026-11-25): most amend a wave-1 row as a new sub-step, the rest
  are new rows (map in §6). Their commits end with their own id (`… [P1]`). A night session may work on a
  branch cut from `remote-ops` (`cordee-r1`, 2026-11-25) that Thomas fast-forwards onto it.

### Order since rev 2

| Release | Steps | Bench | Laptop | Expected on the 08/10 takes |
|---|---|---|---|---|
| 1. Pixels and a foreground that works | M1, M2, P1–P4, F1, F3, X5, X6, U1 | about 7 days | one window, no camera needed | detector from 61 % to 81 % (yolo11x) or 94 % (yolo11l), false boxes ≤ 0.02 per frame |
| 2. Calibrate and holds | X1–X4, F2, F4, H3 | about 6 days | one window with the camera, IR and a person | points on nobody ≤ 2 %; no hold without foreground |
| 3. Guided | H1, H2, U2, U3 | about 4 days | one window at a rehearsal | four setup steps, one health line |

- F1 and F3 are in release 1 because P3 chooses the model on false boxes **after** the foreground veto: the
  08/10 projects had no exclusion zone, and without F3 yolo11l's boxes on the tree (0.094 a frame) would
  reach the tracker. X6 comes with them, since F1 needs the plate to say at which exposure it was taken.
- Phases C and D start after release 2, measured with M1. Phase A's Anchor fixes and phase B's scorer and
  baselines keep their place; A8 (the release of D34) carries U1.
- Each release goes to the DEV slot first, then to `release` by Thomas's push.

### What the operator will do (the target of X and H)

Four steps, then nothing: **1. Frame** — draws the wall on the picture and sets how many dancers; the app keeps
the crop with the ROI and picks the network shape that fits it. **2. Empty wall** — presses « le mur est
vide »; the app checks it is empty and says where it is not, exposes for the brightest part of the zone,
judges the light and says what to move, and takes the plate. **3. A dancer** — one dancer hangs for 10 s; the
app measures her size, chooses the enhancement on her, checks the belt glare and that she is seen. **4. Card**
— four lines at most, green, orange or red, each a fact and an instruction. During the show: one line. Number
of dancers and Hold stay on the panel; exposure, gain, crop, enhancement and the dials move behind « expert ».

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
| Laptop, with TouchDesigner, N = 6 | at least 15 fps sustained — `process_wall` ≤ 55 ms at p95 — GPU < 83 °C (Thomas, 2026-11-25: a floor of about 15 fps with room left for TouchDesigner; rev 1 said 20 fps) |
| Ratchet | no labelled take worse than the last release |

Rev 2 adds two measures, because they are what the operator's setup controls:

| Measure | 08/10, night | Target |
|---|---|---|
| Detector: a box on a dancer, share of her frames | 55 % (61 % on the one-frame-per-second sample) | ≥ 85 % |
| Foreground: a group on a dancer, share of her frames | not usable as shipped | ≥ 98 %, at most 0.5 false groups per frame |
| Operator actions to be ready | exposure, gain, crop, enhancement and two dials within reach; six Calibrate runs that evening | 4 steps, one result card |

On the 08/10 night takes the shipped app scores 68–92 % on-body, 5 to 63 s longest gap and 2–13 % points on
nobody.

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

### Rev 2 — a setup the operator can trust (wave 1, 2026-11-25)

Estimates are bench days on mgr-ran. `hub` names the row that carries the step; a `④` is a sub-step added to
an existing row.

#### M — Measure first

- **M1 · The 08/10 truth and its scorer in the repo.** Per-frame presence and positions for the 13 takes,
  with the hand-declared entries and exits, and a scorer that reads a replay timeline or a live log and prints
  the §2 measures and the KPI. It is B2 ①② with a different pre-label: background subtraction and declared
  entries, not YOLO ≥ 0.5. Gate: reproduces the counter-analysis tables to ±1 point. — @est(1d) — hub #t-031 ④
- **M2 · A live session can be scored.** The event log records the coordinates sent for each slot; app log
  lines carry a time (A2 ③). Gate: a live session scored with no proxy. — @est(0.5d) — hub #t-023 ④
- **M3 · Playback on a frame clock** (B1 ②, extended to the app's playback). Gate: two playbacks of one file
  give the same output. — in B1 — hub #t-030 ④

#### P — Pixels

- **P1 · Rectangular input.** The ROI is letterboxed to a rectangle of its own shape (the façade ROI is
  965 × 1771: in a 1280 square, 45 % of the input is padding and a 110 px dancer arrives at 80 px), engines
  are named by height × width, the tracker space follows the tensor; falls back to the square with a readiness
  warning. Where: `core/gpu_pipeline.py` `_prepare_yolo_input`, `core/model_manager.py` engine naming,
  `core/pipeline.py` tracker space. Gate: square goldens unchanged; on the 08/10 truth through the real
  pipeline, ≥ 80 % per dancer-frame with yolo11x. — @est(1.5d) bench GPU — hub #t-047
- **P2 · Engines and timing.** yolo11x and yolo11l at 1760 × 960 and 1920 × 1056 (height × width, FP16),
  built on mgr-ran and on the laptop; timed on the laptop with TouchDesigner running, as the whole loop.
  Where: `extra/build_engines.sh`. Gate: `process_wall` ≤ 55 ms at p95 with TouchDesigner, GPU under 83 °C.
  — laptop window 1 — hub #t-048
- **P3 · Model by the ratchet.** After: P1, F3. yolo11x against yolo11l on the 08/10 truth **and** on the
  hangar corpus (the docs report yolo11l weak on the textured duo). Gate: highest recall with ≤ 0.02 false
  boxes per frame after F3, no guard take worse. — @est(0.5d) bench GPU — hub #t-049
- **P4 · Readiness shows the dancer's height at the network** and warns under 100 px. After: P1. Where:
  `core/ops_monitor.py`. Gate: the 08/10 projects show the warning as shipped, not after P1. — @est(0.5d) —
  hub #t-050
- **P5 · Warmup swipe.** After: P1, F1. Which model suits depends on the scene, so the app measures it instead of
  the operator guessing. With a valid empty-wall plate and dancers on the wall, the pipeline keeps the newest 150
  frames with a dancer group on the wall, 1 s apart, whatever the live setting did on them (two and a half
  minutes of dancing, kept passively in RUN). On « Try other settings » a worker thread runs them through the
  other candidates beside the live loop, one paced inference per live frame, one engine loaded at a time. The
  foreground referees: dancers with a box, minus boxes on nobody (under 64 px² of foreground). A setting is
  named only when a paired test over those frames proves it better than the live one, by 3 points plus 5 per
  doubling of its cost per frame; then the best of those is named. The swipe picks the **model**; the dancer's
  height picks the **size** (her height at the live input ≥ 200 px: the other model at the live input; under
  200: both models at the largest input that has an engine). Readiness shows a row only when the verdict
  recommends a change; Apply works in STANDBY only (it reloads the model); nothing switches by itself. Where:
  `core/swipe.py` (referee, collector, verdict), `core/swipe_runner.py` (planner, candidate runner),
  `runtime/swipe_controller.py`, `core/pipeline.py` `_swipe_tap`, `core/ops_monitor.py` `check_swipe`; gates
  `tests/swipe_gate.py` (records; `tests/test_swipe_records.py` when `WD_SWIPE_RECORDS` is set) and
  `tests/swipe_replay.py` (a take through the real code). Gate: on mur2 6–8 the pick within 1.5 points of the
  best; never a setting worse than x @ 1280 on the corpus takes with a plate; ≤ 30 s on the laptop. What is
  measured, and where the gate as written is not met, in §5. — @est(2d) bench GPU + laptop — hub #t-058
- **P6 · Autonomous swipe.** After: P5. (Thomas, 26/11: the swipe runs by itself.) Project key `swipe_auto` =
  off | suggest | auto, off by default and absent from existing projects. In RUN, once 150 fresh frames with
  dancers are kept and the live setting's own boxes leave room for a gain, the pair {live, the other model at
  the live input} runs in the background at a gentle pace. `suggest`: a line and a toast with « Apply now ».
  `auto`: the switch itself, between two frames at the same network input, only after two verdicts in a row on
  different frames, a large gain, a dwell, never back, never slower, with an alert toast. An empty wall costs
  nothing: no frame with a dancer, no inference. Gate: frame time ≤ 55 ms at p95 with TouchDesigner; no switch
  on mur2 6–8 at l @ 1920 × 1056; from x at the same input one switch with the ids unchanged; zero candidate
  inference on an empty take. — @est(3d) laptop — hub #t-059

Engine-only estimates on the laptop (pixel count against its measured 21.3 ms for x@1280 and 13.5 ms for
l@1280): x at 1760 × 960 about 22 ms, l at 1760 × 960 about 14 ms, l at 1920 × 1056 about 17 ms, x at
1920 × 1056 about 26 ms. Live on 08/10 at x@1280: `process_wall` p50 23.7 / p95 45.7 ms, `yolo` 15.0 / 32.5,
`mog2_feed` 16.9 / 35.9 — the motion model costs as much as YOLO, and is the first place to look if P2's gate
fails. The rule at the 15 fps floor: the highest recall whose loop stays under 55 ms at p95. First choice
yolo11l at 1920 × 1056 (94 % on the twelve night takes); fallback yolo11x at 1920 × 1056 (86 %); two tiles
kept in reserve.

#### X — Exposure and Calibrate

- **X1 · The servo exposes for the highlights of the zone:** the 99.5th percentile of the ROI, belt-sized
  blobs removed, stays under 235 with at most 0.5 % clipped; the median becomes a floor. Where:
  `core/calibration.py`, `core/config.py`. Gate: a synthetic oval does not clip; on `mur2` take 2 frames it
  commands down; the final check needs the camera. — @est(1d) — hub #t-027 ④
- **X2 · A light verdict with one instruction:** the ratio of the brightest to the darkest part of the zone,
  from the plate (08/10: 15–20 times); red above 4, saying what to move — the app may ask for the IR flood to
  be moved (Thomas, 2026-11-25). Gate: the 08/10 plates give red; a flat synthetic wall gives green. —
  @est(0.5d) — hub #t-054
- **X3 · Emptiness first.** With someone in view Calibrate stops and says where; it does not lower the
  enhancement. The ladder records the strongest setting that stays clean and never goes under neutral because
  of a person. Gate: Calibrate replayed on a `mur` 3 segment with a dancer stops, enhancement unchanged. —
  @est(1d) — hub #t-027 ⑤
- **X4 · The dancer step, 10 s.** Size from box heights and the foreground height, no keypoint gate;
  enhancement chosen by her confidence, up to X3's ceiling; belt glare measured against her body box. After:
  A6. Gate: on 10 s of each 08/10 take, height within 100–116 px; the chosen enhancement no worse than none. —
  @est(1.5d) bench GPU — hub #t-055
- **X5 · What Calibrate set is locked.** Exposure, gain, crop and the enhancement box leave the Live panel;
  the `E` key goes (A4 ①); a change of exposure, gain or crop marks the plate invalid at once; enhancement off
  shows red. Gate: headless GUI tests. — @est(1d) — hub #t-025 ④
- **X6 · Crop and plate bookkeeping.** ① a ROI follows a sensor-crop change instead of being clamped ② the
  plate file stores exposure, gain, crop and ROI ③ a plate from another exposure, gain or crop is refused with
  its reason, not scaled (F2's refusal). Gate: unit tests; `mur` 2–4 with the 20:06 plate refused. —
  @est(1d) — hub #t-053

X1 alone is not the win: the unclipped 08/10 takes detect no better than the clipped ones (57 % against 53 %).
It buys about 10 points in the oval and a valid plate; P gives the edges their recall; X2 says when the light
itself is the limit.

#### F — Foreground

- **F1 · A foreground that works.** Brightness ratio from unclipped pixels only; « below white » where the
  plate is clipped; elsewhere a difference threshold set by one frame's noise (about 22 levels at 30 dB).
  Where: `core/foreground.py`. Gate: 08/10 `mur2` with the 21:36 plate — foreground under ≥ 99 % of the boxes a
  network gives at confidence ≥ 0.5, and on a hand-labelled sample; ≤ 0.5 false groups per frame, none on
  take 2. — @est(1d) bench GPU — hub #t-051
- **F2 · The plate is honest.** A plate from another exposure, gain or crop is refused (X6 ③); « plate
  stale » stays on screen (C5 ②); the slow plate update can be slow enough not to absorb a still dancer
  (C2 ④). Gate: `mur` 2–4 with the 20:06 plate refused with the reason. — @est(0.5d) — in X6, C2, C5
- **F3 · The veto.** After: F1. A box with no foreground under it, on a valid plate, is dropped before the
  tracker. Where: `core/pipeline.py`, `core/identity_slots.py`. Gate: ≤ 0.02 false boxes per frame on 08/10
  with yolo11l or at confidence 0.10, recall unchanged. — @est(0.5d) bench GPU — hub #t-052
- **F4 · A held slot sits on a foreground group:** it follows the group and lets go after 1 s without one.
  After: F1. Gate: in `mur2` 7 no 21 s hold on the roof edge and no 43 s point above the pair; points on
  nobody ≤ 2 %. It is the bridge to D1–D2 (foreground is the estimator's first measurement). — @est(2d) bench
  GPU — hub #t-056

F1's evidence so far is scored against a truth that is itself a background subtraction: it shows the plate is
usable, not an independent hit rate. A check that does not use the truth: on the 6,263 boxes the shipped path
or yolo26x gives at confidence ≥ 0.5 in `mur2` takes 3 to 8, the rule finds foreground inside the box every
time. The dancer no network sees needs hand labels, which is why F1's gate has them.

#### H — Guidance

- **H1 · The four setup steps as a wizard** on the existing, tested `WizardState`. After: X4. Gate: headless
  GUI tests; a new project reaches Save in four steps. — @est(2d) — hub #t-057
- **H2 · One health line** (E2), worst state wins, with the new signal « a foreground group has no point ». —
  @est(1.5d) — hub #t-040 ①
- **H3 · Readiness runs at start and after Calibrate**, gains rows for enhancement, ROI crop and plate
  exposure, and never shows « skip » as green. — @est(0.5d) — hub #t-026 ④

#### U — Camera link

- **U1 · Release D34 (A8), and the slot hold on the frame clock** so a stall does not use it up (17 of 97 slot
  losses on 08/10 fall on the first frame after a stall). — in A8 — hub #t-029 ①
- **U2 · Stall rate against the sensor crop shape, cable and port**, with the camera temperature logged (no
  stall in 4.4 minutes of portrait crop on 08/10). This is L2's A/B. — laptop window 2 — hub #t-043 ①
- **U3 · Stall rate on the health line.** With H2. — hub #t-040 ①

#### What rev 2 would not do

Switch to yolo26 (42 % against 59 % for yolo11x on the four long takes); run the full frame without the ROI
(54 %); flat-field with a clipped plate (39 % in the oval); tune tracker parameters on these takes before P
and F land; use the laptop's playbacks as evidence before M3.

### Phase C — Regimes: fixes inside the current design (wave 2, after G1 and B3; since rev 2, after release 2)

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
| L1 | First laptop window (#t-020) | laptop | – | 4h | doing: reached, inventory, footage, recipe audit and suite done 25/11; launcher build and timing open | |
| L2 | Second laptop window | laptop | B1, A7 | 3h | todo | |
| L3 | Third laptop window | laptop | C3, C5 | 3h | todo | |

Rev 2 (release in the last column):

| Id | Step | Lane | After | Est | Hub | Status | Rel. |
|---|---|---|---|---|---|---|---|
| M1 | 08/10 truth and scorer in the repo | bench ⚙ | – | 1d | #t-031 ④ | done 26/11 | 1 |
| M2 | A live session can be scored | bench ⚙ | – | 0.5d | #t-023 ④ | done 26/11 | 1 |
| M3 | Playback on a frame clock | bench ⚙ | – | in B1 | #t-030 ④ | todo | – |
| P1 | Rectangular input | bench GPU | – | 1.5d | #t-047 | landed 26/11 (§5) | 1 |
| P2 | Engines and timing | laptop | P1 | window | #t-048 | done 26/11 (§5) | 1 |
| P3 | Model by the ratchet | bench GPU | P1, F3 | 0.5d | #t-049 | measured 26/11, choice open (§5) | 1 |
| P4 | Dancer height at the network | bench ⚙ | P1 | 0.5d | #t-050 | landed 26/11; the live band open | 1 |
| P5 | Warmup swipe | bench GPU + laptop | P1, F1 | 2d | #t-058 | built 26/11 on `cordee-swipe`; the review's findings answered, per-swipe gate in §5; re-review pending | – |
| P6 | Autonomous swipe | bench GPU + laptop | P5 | 3d | #t-059 | in work 26/11, behind `swipe_auto` (off) | – |
| X1 | Servo on the highlights | bench ⚙ | – | 1d | #t-027 ④ | todo | 2 |
| X2 | Light verdict | bench ⚙ | – | 0.5d | #t-054 | todo | 2 |
| X3 | Emptiness first | bench ⚙ | – | 1d | #t-027 ⑤ | todo | 2 |
| X4 | The dancer step | bench GPU | A6 | 1.5d | #t-055 | todo | 2 |
| X5 | What Calibrate set is locked | bench ⚙ | – | 1d | #t-025 ④ | done 26/11 | 1 |
| X6 | Crop and plate bookkeeping | bench ⚙ | – | 1d | #t-053 | ② ③ done 26/11, ① open | 1 |
| F1 | A foreground that works | bench GPU | – | 1d | #t-051 | done 26/11 but the hand sample | 1 |
| F2 | The plate is honest | bench ⚙ | X6 | 0.5d | X6 ③, C2 ④, C5 ② | todo | 2 |
| F3 | The veto | bench GPU | F1 | 0.5d | #t-052 | built 26/11, gate not met, held (§5) | 1 |
| F4 | A held slot sits on a foreground group | bench GPU | F1 | 2d | #t-056 | todo | 2 |
| H1 | The four steps as a wizard | bench ⚙ | X4 | 2d | #t-057 | todo | 3 |
| H2 | One health line | bench ⚙ | E1 | 1.5d | #t-040 ① | todo | 3 |
| H3 | Readiness rows | bench ⚙ | – | 0.5d | #t-026 ④ | todo | 2 |
| U1 | D34 released, hold on the frame clock | decide | A7, L1 | – | #t-029 ① | todo | 1 |
| U2 | Stall A/B with crop shape | laptop | – | window 2 | #t-043 ① | todo | 3 |
| U3 | Stall rate on the health line | bench ⚙ | H2 | – | #t-040 ① | todo | 3 |

## 5. Gate tables

Filled by the step named. Until then the cell says so.

- **A7 ③ — D34 on takes with real stalls:** not measured yet.
- **T0 — shipped stream, takes already on mgr-ran (B4):** not measured yet.
- **T1 — shipped stream, rehearsal duo and Cordée takes (B7):** not measured yet.
- **T2 — shipped stream with the regime fixes (C7):** not measured yet.
- **D3 — estimator against shipped:** not measured yet.
- **M1 — the repo's scorer against the counter-analysis tables** (2026-11-26): `application/tests/truth_score.py
  summary --check` reproduces `tmp_analysis/counter-0810/score/summary.json` exactly — every take, every number,
  zero tolerance.
- **P1 / P3 — the 08/10 truth through the real pipeline** (2026-11-26; the four long takes mur 5, mur2 6–8 in
  full, 49,960 on-wall dancer-frames, conf 0.15, M1's scorer; the rectangle rows with F1 and F3 on). Square
  goldens: byte-identical to `remote-ops` (three scenarios, same engine).

  | Network input | Detector on her | mur 5 / mur2 6 / 7 / 8 | False boxes per frame (after F3) | Replay: a point on her / points on nobody | Laptop p95 with TD (P2) |
  |---|---|---|---|---|---|
  | x @ 1280 square (shipped, `release` code) | 50.9 % | 43.7 / 53.9 / 51.2 / 57.0 | 0.018 (no veto) | 86.3 % / 19.0 % | 35.9 ms |
  | x @ 1760 × 960 | 73.7 % | 72.5 / 77.0 / 71.6 / 75.6 | 0.0312 | 92.6 % / 8.5 % | 39.6 ms |
  | x @ 1920 × 1056 | 83.3 % | 82.4 / 87.1 / 81.3 / 83.5 | 0.0202 | 92.3 % / 8.4 % | 51.5 ms |
  | l @ 1760 × 960 | 87.2 % | 81.1 / 90.6 / 88.5 / 88.6 | 0.0465 | 94.7 % / 6.3 % | 30.9 ms |
  | l @ 1920 × 1056 | 91.0 % | 89.2 / 93.5 / 90.5 / 91.0 | 0.0222 | 93.9 % / 5.5 % | 35.1 ms |

  P1's gate as the night brief set it (x at 1760 × 960, ≥ 80 %) is not met; the row's (≥ 80 % with yolo11x) is,
  at 1920 × 1056. The 1 fps experiment had predicted 78 / 85 / 91 / 93.5 % for these rows. Thomas landed P1 and
  P4 on `remote-ops` on 26/11, without F3.
- **P2 — the loop on the laptop with TouchDesigner** (2026-11-26, night session; `extra/measure_loop.py` from a
  scratch folder, the laptop's own `jeudi0809_mur2` take 7, frames 1500–2730 with both dancers on the wall, its
  plate, F1 on; TouchDesigner 2025.32820 open on a copy of `ByNight_scenes_NOVEMBRE.314.toe`, 25 % GPU alone):

  | Engine | `process_wall` p50 / p95, with TD | without TD | `yolo` p50 / p95 (TD) | `mog2_feed` p50 / p95 (TD) | engine only p50 | GPU max |
  |---|---|---|---|---|---|---|
  | x @ 1280 (shipped) | 30.6 / 35.9 ms | 32.0 / 36.4 | 18.6 / 22.5 | 20.5 / 24.6 | 22.3 ms | 66 °C |
  | x @ 1760 × 960 | 34.0 / 39.6 | 39.8 / 43.7 | 20.9 / 25.5 | 23.7 / 28.4 | 23.6 | 67 °C |
  | l @ 1760 × 960 | 25.4 / 30.9 | 26.1 / 30.9 | 11.5 / 15.1 | 14.8 / 18.4 | 15.5 | 67 °C |
  | l @ 1920 × 1056 | 28.6 / 35.1 | 28.8 / 34.9 | 14.2 / 17.6 | 17.5 / 21.1 | 18.8 | 67 °C |
  | x @ 1920 × 1056 | 40.7 / 51.5 | 44.1 / 48.6 | 26.6 / 36.6 | 29.6 / 39.9 | 29.1 | 67 °C |

  Gate (`process_wall` ≤ 55 ms at p95 with TouchDesigner, GPU < 83 °C): **met by all five**. F1's foreground
  costs 4.3–4.7 ms a frame there. `mog2_feed` overlaps enhance + YOLO (it is not added to them). The
  TouchDesigner load is the patch cooking with no OSC input in a locked session: the show's may be higher.
- **P3 — yolo11x against yolo11l** (2026-11-26). The 08/10 truth: the table above — no row reaches ≤ 0.02
  false boxes per frame strictly (x @ 1920 × 1056 misses by 0.0002, l @ 1920 × 1056 by 0.002); about 40 % of the
  remaining « false » boxes lie beside a dancer the truth only coasts, so the measure is at its noise there. The
  hangar corpus (each scenario's own square size, `replay.py --score`, known N):

  | Scenario | x: drop / ghost (score) | l: drop / ghost (score) | with the take's median plate, x / l score |
  |---|---|---|---|
  | hangar-floor | 5 / 0 % (0.05) | 0 / 0 % (0.00) | – |
  | hangar-aerial | 4 / 0 % (0.04) | 6 / 0 % (0.06) | – |
  | texture-aerial | 3 / 4 % (0.14) | 14 / 10 % (0.33) | – |
  | texture-duo | 36 / 2 % (0.47) | 10 / 30 % (0.52) | 0.49 / 0.56 |
  | texture-duo-full | 23 / 60 % (0.96) | 5 / 87 % (1.06) | 0.65 / 0.49 |
  | texture-wallhang | 39 / 0 % (0.42) | 42 / 0 % (0.49) | 0.42 / 0.49 |
  | white-duo | 25 / 16 % (0.52) | 29 / 24 % (0.64) | 0.57 / 0.46 |
  | white-duo-full | 20 / 15 % (0.45) | 20 / 16 % (0.47) | 0.37 / 0.48 |
  | blur-runner | 3 / 1 % (0.05) | 3 / 1 % (0.05) | – |
  | outdoor-night | 28 / 0 % (0.28) | 73 / 0 % (0.73) | – |
  | outdoor-sitter | 6 / 0 % (0.07) | 52 / 0 % (0.56) | – |
  | bdx1005-s5-ghost | 3 / 116 % (1.29) | 6 / 108 % (1.26) | 0.63 / 0.69 |
  | bdx1005-s8-shadows | 11 / 10 % (0.29) | 13 / 11 % (0.33) | 0.85 / 0.87 |

  (score = drop + ghost + 0.3 fragmentation + 0.1 identity; lower is better.) yolo11l scores worse than yolo11x on 10 of 13
  scenarios without a plate (`facade-ghosts` did not run: mgr-ran's 1920 engines are the laptop's builds).

  Recommendation, not applied: on the façade (portrait ROI, white wall, a real empty-wall plate) yolo11l @
  1920 × 1056 — 7.7 points of recall over x at the same size for 0.002 false boxes a frame, and 16 ms more room
  for TouchDesigner; elsewhere keep the shipped x @ 1280 until the corpus has real empty-wall plates (B5/B6):
  without a plate yolo11l loses on 10 of 13 corpus scenarios (ghosts on the textured walls, drops outdoors at
  night), and the corpus plates are medians of takes with dancers in them, which make the veto cut real boxes. The model is a per-project choice; the default is unchanged.

  The dancer's height in the network (2026-11-26, the median of the confident boxes): it picks the input size,
  not the model. The 08/10 takes reach it at 70–82 px in the 1280 square, 96–113 px at 1760 × 960 and 105–123 px
  at 1920 × 1056 (x: 50.9 → 73.7 → 83.3 %); the hangar scenarios at ~200 px (hangar, white walls), 146–324 px
  (textured walls); outdoor-night and outdoor-sitter at 114 and 134 px, where yolo11l loses badly while it wins on
  the façade at the same height. The order of evidence (Thomas, 26/11): the `mur` takes stand for the show and
  lead; the hangar corpus stands for indoor rehearsals and must still be covered.
- **P5 — the warmup swipe** (2026-11-26; `tests/swipe_gate.py` on per-frame records, `tests/swipe_replay.py
  --concurrent` on the takes; records, logs and the agents' reports in `tmp_analysis/swipe-1126/` on mgr-ran).
  Truth recall on mur2 6 / 7 / 8: x @ 1280 (live on 08/10) 53.9 / 51.2 / 57.0 %, l @ 1280 91.2 / 86.0 / 87.5,
  x @ 1760 × 960 77.0 / 71.6 / 75.6, l @ 1760 × 960 90.6 / 88.5 / 88.6, x @ 1920 × 1056 87.1 / 81.3 / 83.5,
  l @ 1920 × 1056 93.5 / 90.5 / 91.0 (the best).
  - *Referee.* « On nobody » = under 64 px² of foreground mask under the box: on real dancers it flags 0–0.003
    boxes a frame on mur2 7 and 8, where the group-centre rule flagged 0.23–0.41 (dancer and shadow form one
    group whose centre leaves her box); both catch the truth's ghost boxes alike. A box covers a dancer group
    when the group's centre lies in it, or when it holds 25 % of the group's foreground (two dancers in contact
    make one group whose centre lies between them). The boxes are cut at the project's confidence; one
    confidence serves both models, so the swipe recommends none.
  - *What the first version got wrong* (the blind review of `e770937`, reproduced at 2,000 swipes). It kept the
    frames where the live setting lost a dancer and named the best by one standard error; its gate was run from
    x @ 1280 only, 35–40 points under the rest, where any switch passes. From l @ 1280 on mur2 8 it named a
    setting worse than the live one on 13 % of swipes (7 % with the excluded cells), from l @ 1760 × 960 on 10 %,
    from l @ 1920 × 1056 on 2–10 %, and stayed more than 1.5 points under the best on 49–67 % of swipes from
    l @ 1280; frames picked by one model's failures favour any other model. The plate was blamed on 37 % of
    swipes on mur2 6 from l @ 1280 without the excluded cells (0 % with them: l's extra boxes are the excluded
    figure at the foot of the wall).
  - *The verdict now* (`f694c0b`). The newest 150 frames with a dancer group, 1 s apart, whatever the live setting
    did on them (« lost » is only a flag). The noise is the standard error of the frame-by-frame difference with
    the live setting, by pairs of consecutive frames; a setting is proven at a one-sided 5 % shared over the
    alternatives. Its bar is 3 points, plus 5 per doubling of its cost per frame over the live one's (x @ 1920 ×
    1056 over l @ 1920 × 1056: 6.1 points). Once one setting is proven, the best net among those clearing their
    bar is named — the test alone favoured the setting whose errors resemble the live one's. Under 120 frames
    (the 190 s timeout reached) the swipe gives no verdict.
  - *Gate 1, façade* — per swipe, as shipped (the planner's set, the excluded cells, the app's collection), for
    every live setting; 2,000 swipes a cell. Clauses: a setting worse than the live one named on ≤ 1 % of
    swipes; a named setting within 1.5 points of the best on ≥ 95 %; where the best is 10 points or more above
    the live one, it is named on ≥ 95 %.

    | Live setting | Verdict, share of swipes on mur2 6 / 7 / 8 | Worse than live named |
    |---|---|---|
    | x @ 1280 | l @ 1920 × 1056 on 100 / 100 / 100 % | 0.00 % |
    | x @ 1760 × 960 | l @ 1920 × 1056 on 100 / 99.5 / 99.5 %, else kept | 0.00 % |
    | x @ 1920 × 1056 | l @ 1920 × 1056 on 96.7 / 86.2 / 89.5 %, else kept | 0.00 % |
    | l @ 1280 | kept on 93.5 / 95.8 / 96.8 %, else l @ 1920 × 1056 | 0.00 % |
    | l @ 1760 × 960 | kept on 75.0 / 94.2 / 86.2 %, else l @ 1920 × 1056 | 0.00 % |
    | l @ 1920 × 1056 | kept on 100 / 100 / 100 % | 0.00 % |

    The three clauses hold in all 18 cells, and in the 18 of the background pair (the live setting and the
    other model at the live input: from x, l is named on 98.0–100 % at 1280 and 1760 × 960; from l, kept on
    100 %). **Against the gate as this plan words it** (« the pick within 1.5 points of the best »): NOT met in
    8 of the 18 cells — from l at a smaller input the swipe keeps a setting 2–4.5 points under the best on 75–97 %
    of swipes (6 cells), and from x @ 1920 × 1056 it misses the 6–9 points on 10–14 % of swipes on mur2 7 and 8.
    The behaviour there is the safe one (« keep »): 150 frames do not separate such gaps, and the « dancer
    size » row (P4) is what speaks for the input size. The three clauses above were worded with the results in
    hand, on the same three takes; the blind re-review confirmed them with its own measure (a worse setting
    named on 0.00 % of swipes in every cell) and Thomas accepted them in place of the first wording (26/11).
    From an l setting the swipe is close to a no-op: its value is catching a show left on x @ 1280 or
    x @ 1760 × 960. The window: at 60 frames a worse setting is named on mur2 8 (0.05–0.4 % of swipes in the
    review's measure; none in 2,000 at 90), at 120 the third clause falls to 88 % from x @ 1760 × 960, at 150
    all hold; under 120 frames the swipe gives no verdict. Everything is fitted on these three takes of one
    evening: a window fitted for the two safety clauses on mur2 6 and 7 alone picks 60 frames and fails on
    mur2 8. At confidence 0.10, 0.25 and 0.30 the two safety clauses hold everywhere; the third slips to
    82–96 % in five cells.
  - *Gate 1 on the rehearsal build's foreground* (F2: `core/foreground.py` as on `origin/cordee-r2`; the mask
    records of mur2 6 / 7 / 8 made again with it, the boxes identical on all 21,167 frames; the `5d615d3` referee,
    2,000 swipes a cell; `tmp_analysis/swipe-1126/f2measure/`). The three clauses hold in all 36 cells, as before;
    a setting worse than the live one is named on 0.00 % of swipes in every cell; the truth values and the best
    setting are unchanged. The shares move by two points at most:

    | Live setting | Verdict on F2's foreground, share of swipes on mur2 6 / 7 / 8 |
    |---|---|
    | x @ 1280 | l @ 1920 × 1056 on 100 / 100 / 100 % |
    | x @ 1760 × 960 | l @ 1920 × 1056 on 100 / 99.7 / 99.3 %, else kept |
    | x @ 1920 × 1056 | l @ 1920 × 1056 on 96.2 / 84.2 / 89.5 %, else kept |
    | l @ 1280 | kept on 91.3 / 95.1 / 97.7 %, else l @ 1920 × 1056 |
    | l @ 1760 × 960 | kept on 75.6 / 93.7 / 88.2 %, else l @ 1920 × 1056 |
    | l @ 1920 × 1056 | kept on 100 / 100 / 100 % |

    The background pair: from x, l named on 97.8–100 % at 1280 and 1760 × 960; from l, kept on 100 %. The masks:
    groups on a dancer are unchanged (2.01 → 2.03, 1.44 → 1.44, 1.44 → 1.43 a frame on mur2 6 / 7 / 8); groups on
    nobody fall on mur2 7 and 8 (0.18 → 0.11, 0.42 → 0.34) and rise a little on mur2 6 (0.06 → 0.08). « Plate
    stale » never latches on these takes (the foreground's 90th percentile is about 1 % of the zone, the
    threshold 25 %). Not measured: gate 2 on F2's masks; F2 with the rest of the rehearsal build's pipeline (the
    replay kept the 26/11 night pipeline so that only the foreground differs). The first records had run with
    the F3 box veto on (the façade driver's `fg_box_veto=false` was never read): 9, 1 and 0 boxes vetoed on
    mur2 6 / 7 / 8, and no gate row changes with it off.
  - *Gate 2, corpus* — green by refusal only. The 4 corpus plates (8 scenarios) are medians of takes with dancers
    in them; there even the cleanest setting puts 0.38 boxes a frame and more on nobody (0.004–0.011 on 08/10's
    real plate), and above 0.10 the swipe answers « no verdict: every setting puts boxes where the empty-wall
    picture shows nobody … take a new one when the wall is empty ». No worse setting is ever named (white-walkers:
    no verdict on 334 swipes of 500, kept on 166). The outdoor scenarios, where yolo11l collapses, have no plate.
    So this gate says the swipe knows a plate it cannot trust, not that it chooses well in a hangar: that needs
    a real empty-wall plate there (B5).
  - *The plate, watched* (`b860890`, `44addae`). Every finished swipe tells the rehearsal build's reference watch
    (H4, `core/reference_watch.py`) what the referee made of the empty-wall plate: refused it, or judged on it
    (« keep », a switch); any other end tells nothing. A refusal counts only on frames the last refusal told did
    not use, so the same pool pressed three times is one; the watch raises its plate alert at three in a row.
    Without that module the swipe runs as before. When F2 latches « plate stale », the plate stops being usable
    for the swipe until a new one is taken: no swipe starts, an advice already shown is withdrawn (« the
    empty-wall plate is no longer valid - Calibrate on the empty wall, then run 'Try other settings' again »),
    a swipe in flight is dropped, the kept frames start over. Shown by unit tests, and on a copy of the
    rehearsal build's tree with its real detector driven into the latch; no take latches, and the hook has run
    against the real watch on that copy only until the merge.
  - *Gate 3, the laptop* — the real code from a scratch copy, the take played in real time beside the worker,
    pacing 50 ms. Since `5d615d3` the live setting's row comes from the boxes the live pass stored with each
    kept frame (identical to a re-run of its engine on 150 of 150 frames of mur2 7, from x @ 1280 and from
    l @ 1920 × 1056), so a swipe loads two engines and makes 300 inferences instead of 450:

    | Run (150 frames, `5d615d3`, 26/11) | Swipe: loads / inference / total | Live frame p50 / p95 / max, collecting → during the swipe | Over 50 ms during the swipe |
    |---|---|---|---|
    | mur2 6 from x @ 1280 | 0.6 / 12.2 / **18.9 s** | 33.7 / 38.4 / 49 → 49.4 / 56.9 / 186 ms | 44 % |
    | mur2 7 from x @ 1280 | 0.6 / 12.2 / **18.9 s** | 32.4 / 36.3 / 44 → 48.5 / 54.7 / 68 ms | 36 % |
    | mur2 8 from x @ 1280 | 0.6 / 12.2 / **18.8 s** | 32.7 / 36.3 / 46 → 48.4 / 55.1 / 172 ms | 34 % |
    | mur2 7 from x @ 1280, with TouchDesigner | 0.6 / 12.5 / **19.3 s** | 31.2 / 36.2 / 42 → 49.9 / 60.4 / 133 ms | 49 % |
    | mur2 7 from l @ 1920 × 1056 (one other setting) | 0.3 / 5.5 / **9.9 s** | 29.3 / 32.3 / 38 → 54.3 / 57.4 / 163 ms | 71 % |
    | mur2 7 from l @ 1280 | 0.5 / 8.4 / **19.5 s** | 25.9 / 29.5 / 36 → 31.9 / 53.1 / 69 ms | 15 % |
    | mur2 7 from x @ 1280, the old path (`--rerun-live`, three engines) | 0.9 / 17.1 / 28.5 s | 32.5 / 35.6 / 40 → 48.1 / 56.1 / 66 ms | 41 % |

    Under the 30 s gate by ten seconds, also beside TouchDesigner. The run is set by the pacing, not by the
    GPU: one candidate inference per live frame. For those 19 s a third to a half of the live frames pass 50 ms
    (the loop near 20 fps, one frame over 100 ms in some runs); collecting costs nothing measurable (148 s of
    dancers on the wall, passively, before the button answers). Verdicts: from x @ 1280, l @ 1920 × 1056 (97–99 %
    of the dancers against 54–66 %) on all four runs; from l @ 1920 × 1056 and from l @ 1280, « keep ». The
    process peaks at 2.2–2.3 GB of RAM (the kept frames of a played take weigh about 350 MB, as from the
    camera) and the GPU at 2.6 GB, 5.0 GB with TouchDesigner. Before the stored boxes (`0e7fbe5`, three engines):
    28.9 / 29.2 / 28.9 s on mur2 6 / 7 / 8; at 60 frames (`93e92eb`): 12.0–12.4 s. Engine alone on the laptop:
    a load 0.14–0.20 s, a release 0.06 s with all its GPU memory back; TouchDesigner on the show patch holds
    2.4 GB. ultralytics 8.4.63 there has no CUDA-graph path, so the capture guard is inert; on mgr-ran
    (8.4.173) a load beside another thread's CUDA work broke the capture, hence one engine load at a time in
    the process (`ENGINE_LOAD_LOCK`).
  - *Open.* The real GUI and Apply's full switch (camera reopen, engine load) are not exercised by a test or a
    run. The referee still counts a box on a shadow or a rope as a dancer covered, and the 25 % share may make
    that a little more frequent: unmeasured without a real plate in a hangar. The plate refusal rests on one
    real plate (08/10) against medians. It cannot see one model's own ghosts (by design), a plate that only
    misses a light change, or a still person who was in the plate. With x @ 1280 live the referee has no group
    on a dancer on 8.5 / 1.8 / 0.4 % of her frames on mur2 6 / 7 / 8, and F2's foreground leaves that as it is
    (8.2 / 1.8 / 0.4 %): her foreground is there but in pieces under the referee's 300 px² group floor (the
    opening seconds and the last 40 s of mur2 6), not absorbed by the plate update as this paragraph first said.
    No take has a dancer of 200 px at the live input: that branch of the planner is gated only through the
    background pair. « Largest input » can name x @ 1920 × 1056, which has 3.5 ms of margin on the laptop (P2),
    and now needs 6.1 points to be named.
    Three takes, one evening, one wall: the first take with a real plate on another wall is to be put through
    `tests/swipe_gate.py`.
- **F1 — foreground** (2026-11-26, `tests/fg_eval.py`, mur2 with the 21:36 plate, every 3rd frame):
  foreground under 6,263 of 6,263 boxes a network gives at conf ≥ 0.5; a group on the dancer 94.7–100 % of
  dancer-frames; groups on nobody inside the wall 0.000–0.201 per frame; take 2 (empty) 0.0 and no stale frame (the
  old rule: 5.1, 25 % stale). The hand-labelled sample is open. On the stream alone (2026-11-26: x @ 1280 square,
  veto off, the four long takes, F1 against the old rule): a point on her 89.3 → 90.3 %, points on nobody 10.1 →
  9.5 %, the detector unchanged, no take worse (mur 5's plate is another crop: equal).
- **F3 — the veto** (2026-11-26): with l @ 1920 × 1056, 0.0225 → 0.0222 false boxes per frame (gate ≤ 0.02: not
  met), the detector 91.0 → 90.9 % (recall unchanged); with x @ 1920 × 1056, 0.0206 → 0.0202. The boxes it keeps
  have foreground under them (mur 5's plate is another crop: no veto there). Held on 26/11 (Thomas): it stays on
  the branch until a take shows false boxes on the bare wall that it removes.

## 6. Existing hub rows

| Row | Becomes |
|---|---|
| #t-019 bench session 18/11 | Produced this audit and plan. Thomas closes it. |
| #t-020 remote session on the laptop | L1, with the recipe audit, the suite and the timing dump added. |
| #t-014 duo number | Folded into B7. |
| #t-018 readiness verdict | Folded into G2. |
| #t-010 clean-plate foreground evidence | Folded into B4 and D3. |
| #t-016 order the front BP850 and floodlights, #t-017 rig upgrade | Unchanged. B6 can ride on #t-017's visit. |

Rev 2 (hub, 2026-11-25): new rows #t-046 (this revision), P1–P4 #t-047–#t-050, F1 #t-051, F3 #t-052, X6
#t-053, X2 #t-054, X4 #t-055, F4 #t-056, H1 #t-057. 26/11: P5 #t-058 and P6 #t-059, from Thomas's « settings swipe » (hub note
2026-11-26-settings-swipe). Sub-steps added to wave-1 rows: M1 on B2 (#t-031), M2 on
A2 (#t-023), M3 on B1 (#t-030), X5 on A4 (#t-025), H3 on A5 (#t-026), X1 and X3 on A6 (#t-027), U1 on A8
(#t-029), H2 and U3 on E2 (#t-040), U2 on L2 (#t-043), window 1's timing on L1 (#t-020); T1 already exists
as the counter-analysis (B7, #t-036), the order is partly answered (G1, #t-037), and foreground becomes the
estimator's first measurement (D1, #t-038).

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
- Rev 2's evidence is one evening, one façade, two dancers in light costumes, scored at one frame a second on
  JPEG frames with PyTorch for the sizes that had no engine; yolo11l is untested on the hangar corpus. → P1 and
  P3 re-measure through the real pipeline; P3 includes the corpus.
- The audit judged a rectangular engine useless on the 25 m wall (ROI 1322 × 1275, nearly square), and
  `MULTIPASS_REVIEW_2026-10.md` rejected tiling on scenes with larger dancers. → Rev 2 contradicts neither:
  the façade ROI is 965 × 1771, and P1 keeps one pass.
- The laptop's 15 fps floor leaves TouchDesigner's share unknown. → P2 measures the whole loop with it
  running, before a model is chosen.
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
