# Dimension — docs

*Context-aware auditor's report, followed by an adversarial reviewer's check of its six most consequential claims. Where the two disagree, the reviewer's reading is the safer one.*

## Verdict

Docs are accurate where October's work was written down (OSC_CONTRACT §D, REMOTE_OPS commands, PLAN §C) and wrong at every front door (README, the index, NEW_SHOW). Of the October audit's 22 actions, 1 is done, 6 are partial and 15 are open, while docs/ grew from 173 KB to 449 KB. The oldest request, an operator manual, is unmet, and the one current French note gives a remedy the code cannot honour.

Minimal set (7 pages):
- README, anyone, 80 lines: today's install and licence parts.
- MODE_EMPLOI_FR, operator, 300 lines plus PDF: TERRAIN_DEMO_FR, TOURNAGE notes, ops_monitor alerts.
- OSC_CONTRACT, TD author, one page plus appendix: §A, §D.
- REMOTE_OPS, maintainer, 250 lines with release, rollback and a release log: REVIEW §5.
- DEV, developer, 200 lines: module docstrings, 03-architecture, TUNING §1.
- ROADMAP, 150 lines, forward only.
- DECISIONS, 80 lines.

Keep as reference: TUNING, CORPUS_ANALYSIS, OPTICS, MARKERS_PHASE0A.
Archive (session notes after the manual): NEW_SHOW, CHECK_TEST, TODO, the four French notes, PLAN_25M, REVIEW, AUDIT and its streams, BRAINSTORM, MULTIPASS, AUTOTUNE_DESIGN, GUI_STACK_AUDIT, TRACKING_ROBUSTNESS.
Delete: archives/audit_report.md, archives/tiling_plan.md.

## Since the October audit

05-docs.md (2026-10-05) audited 12 live docs, 173 KB. Since then there were 46 doc commits and 18 files added; nothing left docs/ except the June roadmap and the v2 draft. docs/ now holds 23 files (449 KB), the audit streams 273 KB, archives 20 files (401 KB).

Done: ROADMAP v2 with Appendix A (DOC-5). OSC_CONTRACT gained "When an id disappears" and §D. REMOTE_OPS, MARKERS_PHASE0A and four French session notes were written. The "clean plate" UI text became true because the plate was built (D26/D29).

Not done: the manual, README, ARCHITECTURE, TESTING, CLAUDE.md, ENGINEERING_RECORD §12/§13, every archive move.

New drift: ROADMAP.md:40 says release = 46a0262 while origin/release is 63f4989; REMOTE_OPS.md:3 says "not yet on the laptop"; the roadmap reuses the P and D labels it forbids. No doc changed after 2026-10-08.

## Details

Read-only pass; `git status --short` is empty; no tests or replays run.

**1. DOC-1..22**
- Done: DOC-5.
- Partial: DOC-8 (§D added; O1–O12 not applied), DOC-12, DOC-14, DOC-17 (launcher comment repointed), DOC-21 (REQ-1/4/5/7 only), DOC-22 (REMOTE_OPS §7).
- Open: DOC-1, 2, 3, 4, 6, 7, 9, 10, 11, 13, 15, 16, 18, 19, 20.

**2. Spot-checks (19: 10 true, 1 partial, 8 false or stale)**

| Claim | Verdict | Code |
|---|---|---|
| README:121 keys Q, R, +/- | false | ui/adapter.py:274-297 |
| README:170 FP16 toggle | false | app.py:542 |
| README:100 8 mm lens | stale, 6 mm | core/config.py:817 |
| README:106,111 QR and profiles in top bar | false | gui_builder.py:720, 755 |
| README:63 Python 3.10+ | partial, <3.13 | pyproject.toml:6 |
| README:72 default engine first | true | extra/build_engines.sh:66 |
| README:119 expert mode | true | gui.py:322 |
| README:125 picker, last_project.txt | true | core/config_store.py:32 |
| NEW_SHOW:9 six phases | false, three | gui_builder.py:582 |
| NEW_SHOW:80 smooth L | false, removed | gui_builder.py:1032 |
| NEW_SHOW:60 readiness rows | true, plus two | core/ops_monitor.py:101-312 |
| OSC:33 IP, port | true, cite wrong | core/config.py:523 |
| OSC:91 /clear on engine stop | false | app.py:1983, 2016 |
| OSC:324 stall 3 periods, 5 s, 2.5 s | true | config.py:135; camera_controller.py:77 |
| OSC:344 six states | true | core/identity_slots.py:99 |
| OSC:358 slot defaults | true | core/config.py:320-333 |
| REMOTE:100 127.0.0.1:8765, token | true | core/config.py:582-584 |
| REMOTE:118 policy classes | true | services/remote_api.py:55-93 |
| REMOTE:3 "not yet on the laptop" | stale | ROADMAP.md:71 |

**3. Readers**
- **Operator (FR).** Exists: TERRAIN_DEMO_FR (current flow, 100 lines). Stale: TERRAIN_2026-10-07 (six phases), NEW_SHOW, CHECK_TEST, README. Missing: manual, failure runbook, launcher prompts, crash restart.
- **Developer.** Exists: TUNING, scenarios README, module docstrings. Stale: 03-architecture (June), CHECK_TEST. Missing: architecture, state ownership, test and replay how-to, dev-box rules.
- **Maintainer.** Exists: REMOTE_OPS (commands verified). Stale: header, §8 first window. Missing: release, rollback, pin, deployed state.
- **TD author.** Exists: OSC_CONTRACT. Stale: cites, §B title, /clear. Missing: stream lifecycle, oscreplay, a short page.

**4. Decisions**
Files needed for value plus reason: D1 3, D5 2, D13 3 (status contradictory), D27 2, D34 3. No single place.

**5. Anchors** (application/src / tests, tools, launcher, extra, tmp_analysis)
- Track 65/26, D-n 63/54 (some are dancer labels), CONT 40/14, REQ 36/15, bug # 28/18, ROADMAP 29/22 (with § 10/8), PLAN_25M 19/7, ARCH 15/11, PERF 12/7, OSC_CONTRACT 12/3.
- Sample of 15: 8 resolve in a live doc (PLAN C.13, D33, D34, CONT-6, CONT-1, ARCH-6, PERF-4, OSC §B.2); 6 only through Appendix A into archives (§4.2 Phase 2, §7B, bug #8, bug #14, Track S, Track X §2); 1 names a file only.
- Orphan outside the sample: `ROADMAP §6` in tests/test_launcher_git_manager.py:1.

**6. tmp_analysis**
See finding 8.

**7. In-code comments**
- Asset: core/camera_stall.py:1-27, core/identity_slots.py:1-85, core/config.py:252-261 and 1032-1040 (mechanism, evidence, decision).
- Noise: config.py:118-119 (commented twin line), gui_builder.py:547-558 and :966 ("6 - Go Live" under rail "3 Live"), osc_output.py:154-155 (four of six states).

## Findings

### 1. No operator manual; the only current French note has a one-line failure runbook that points to a fallback the release channel removes

severity high · confidence high · status known open · effort M · prior id: DOC-2

**Evidence.** No docs/MODE_EMPLOI.md exists (ROADMAP.md:74: 'still no operator manual'; TODO-me:4). The current French procedure is docs/TERRAIN_DEMO_FR.md, 100 lines. It tells the operator to launch 'WallDance DEV' (:3, :41), and its whole failure runbook is :98-100, 'Revenir au WallDance habituel'. ROADMAP.md:106 says LIVE takes `release` at its next launcher start, so that fallback becomes the same code (my inference). Nothing in French covers the launcher's update prompt, the restart into STANDBY after a crash (D13), camera loss, the TRT banner or the alerts.

**Recommendation.** Write docs/MODE_EMPLOI_FR.md (300 lines at most, PDF on the laptop desktop) from TERRAIN_DEMO_FR.md plus a 'Si ça ne va pas' table: symptom, what to do, when to call. Quote UI labels verbatim. Decide the real fallback first (question 3). Then archive both TERRAIN notes, NEW_SHOW.md and CHECK_TEST.md.

**Risk.** Low, docs only. Labels drift again unless a test checks them (see rethink).

### 2. The operator note and the in-app toast prescribe 'paint an exclusion, then Calibrate again', which cannot make the empty-wall check pass

severity high · confidence high · status new · effort S

**Evidence.** docs/TERRAIN_DEMO_FR.md:50-52 ('peindre une case d'exclusion dessus… puis refaire CALIBRATE'), the toast at runtime/calibration_flows.py:534-535 and the message at core/empty_wall.py:79. The check feeds `processor.last_raw_dets` (calibration_flows.py:506), assigned at core/pipeline.py:741-745 straight after `_extract_detections`; the exclusion mask is applied later, in `_exclusion_step` (pipeline.py:965). core/empty_wall.py takes no mask (grep: 'exclusion' only in the two message strings). Read, not executed. Confirms blind lead motion-calibration#6.

**Recommendation.** Until the code changes, reword the note and the toast: remove or cover the object, tighten the ROI, else call Thomas. Code option, on the owner's go: drop raw detections inside excluded cells before `EmptyWallCheck.feed`, with a unit test.

**Risk.** Text change: none. The code change touches Calibrate and needs the flow_check replay.

### 3. The remote maintainer has no release or rollback runbook, and no doc records what the laptop runs

severity high · confidence high · status new · effort S

**Evidence.** docs/REMOTE_OPS.md:3 still says 'not yet on the laptop'. It never names requirements-prod.txt or freeze-lock (grep: 0 hits) and says nothing of the installers' pinned mode; the rule that an install.bat change alone triggers an unpinned reinstall exists only as a print in extra/wdremote.py:1155-1157. ROADMAP.md:84-85 says two pushes, :212 (D32) one. ROADMAP.md:40 gives release = 46a0262; origin/release is 63f4989, 23 commits later. LIVE rollback is undocumented: REMOTE_OPS.md:193 covers DEV only, and launcher/git_manager.py:36 marks AHEAD as 'never offer update'.

**Recommendation.** Add REMOTE_OPS §9 'Release and rollback': gates, the pin rule, the push command, how to take LIVE back (a revert pushed forward; backup ref refs/walldance/pre-update-*), and a dated release log (sha, content, LIVE and DEV state, unreleased commits). Source: ROADMAP_v2_REVIEW §5.

**Risk.** Low. The rollback steps must be tried once on the DEV slot before anyone relies on them.

### 4. Every front door describes June: README, the docs index and NEW_SHOW send readers to a six-phase UI that no longer exists

severity medium · confidence high · status known open · effort S · prior id: DOC-1, DOC-3, DOC-4, DOC-14

**Evidence.** docs/README.md:25 and :44 present NEW_SHOW.md as the live operator playbook for a six-phase rail; the rail has three phases (gui_builder.py:582-586). NEW_SHOW.md:36 'press Aim'; :80 'smooth L' (removed, gui_builder.py:1032-1034). README.md:121 keys Q, R, +/- are absent from ui/adapter.py:274-297; :170 'Enable FP16' has no control (app.py:542); :100 '8mm lens' while the 6 mm is mounted (core/config.py:817-818); :140-162 a flat file map (code is in src/core, runtime, ui). CHECK_TEST.md:59 expects '365 passed'.

**Recommendation.** Now, one hour: cut README to install, hardware, licence and a reader map; put a 'June 2026, superseded by TERRAIN_DEMO_FR.md' banner on NEW_SHOW.md and CHECK_TEST.md; fix the index row. Later: DOC-1, DOC-3 and DOC-4 as planned, once the manual exists.

**Risk.** None; the cuts only remove wrong text.

### 5. OSC_CONTRACT is true on the wire but not usable as a patch author's page: 381 lines, internals mixed in, no stream-lifecycle section, stale cites

severity medium · confidence high · status known open · effort S · prior id: DOC-8

**Evidence.** Wire values check out (details §2). Line cites do not: config.py:377-379 is now 522-524, pipeline.py:904-905 is 1063, tracker.py:3126 is 3350. §B is still titled 'Planned… gated' (OSC_CONTRACT.md:130). :91 says /walldance/clear marks 'engine stop / scene reset'; only `_cb_tracker_reset` sends it (app.py:1983-1991, callers :895, :1135, :2016). No section says what TD sees on STANDBY, quit or crash. extra/oscreplay appears in no live doc. The tooltip (gui_builder.py:1127-1128) and the docstring (osc_output.py:154-155) list four of the six states.

**Recommendation.** Put a one-page patch contract first: addresses, ids 1..N, hold and coasting, stall re-send, what silence means, oscreplay. Move tracker internals and replay evidence to an appendix; cite symbols, not lines. Keep the §A/§B/§D numbers, because code cites them.

**Risk.** Low. The wording on what silence means needs the owner's confirmation with the TD patch author.

### 6. Decisions D1–D34 have no register: the value is in one file, the reason in another, some statuses contradict, and the labels collide

severity medium · confidence high · status new · effort S

**Evidence.** ROADMAP.md:205-218 lists answered decisions as one paragraph without reasons; the reasons sit in PLAN_25M §C.12-C.16, AUDIT_2026-10.md:271-297 and REVIEW §7. Sample: D1 3 files, D5 2, D13 3, D27 2, D34 3. D13 and D17 are in the 'Open' table (ROADMAP.md:235-236; REVIEW:509 'D13 (open)') although shipped. ROADMAP.md:14 forbids reusing D and P, yet uses D-numbers and P-1..P-9 (:138-146); code cites the legacy P-2 and P-6 (pipeline.py:455, calib2.py:186) and uses D1/D2 for dancers (gui_builder.py:1059).

**Recommendation.** One docs/DECISIONS.md table, 80 lines at most: id, date, status, value, reason in two lines, evidence link, config key. Move D13, D17 and D10 to answered. Rename the roadmap's NEXT items, for example NX-1..9.

**Risk.** None.

### 7. A new developer has no architecture, testing or dev-box page; October's modules are documented only in their docstrings

severity medium · confidence high · status known open · effort M · prior id: DOC-6, DOC-7, DOC-15, DOC-17, DOC-20

**Evidence.** No ARCHITECTURE.md, TESTING.md or CLAUDE.md. identity_slots, remote_api, camera_stall and crash_marker have 0 hits in audit-2026-10/03-architecture.md (June baseline). `--no-sync` is in no live doc, only run.sh:46 and the audit stream files; the cuDNN LD_LIBRARY_PATH need is only in ROADMAP_v2_REVIEW.md:516-520. ROADMAP Appendix A points to ENGINEERING_RECORD §12/§13 'to be created' (ROADMAP.md:252-254; the record ends at §11). `ROADMAP §6` in tests/test_launcher_git_manager.py:1 resolves nowhere. Stale paths remain at core/config.py:103 and camera/ids_camera.py:348.

**Recommendation.** One docs/DEV.md, 200 lines at most: package map, where state lives, a frame's path, how to run the suite and a replay on dev37 and on the laptop, the never-push and no-sync rules. Source: module docstrings, 03-architecture §2-3, TUNING §1, CHECK_TEST §1.5. Create or drop the three Appendix A rows.

**Risk.** Low.

### 8. tmp_analysis/ mixes six to eight living, unit-tested tools with about a hundred one-off study scripts

severity medium · confidence medium · status new · effort M

**Evidence.** 189 tracked files, 19,464 lines. Its .gitignore says 'commit only the scripts' (`!*.py`), yet 23 .sh, 18 .txt, a .jpg and a .patch are tracked. Living: marker_eval.py and marker_evallib.py (tests/test_marker_eval.py:22; MARKERS_PHASE0A.md), belt_eval.py (tests/test_belt_eval.py:15; ROADMAP N-11), plan25m/apply_d33.py (core/config.py:1039), plan25m/slot_eval.py (OSC_CONTRACT.md:368), audit-2026-10/continuity/drive.py (extra/wdremote.py:30), stall_d34/* (the D34 gates). By basename match, 103 files are cited by no live doc and no code.

**Recommendation.** Move the living tools to `application/tools/` (where ARCH-16 should also send the tools now in tests/), keep their tests, update the cites. Leave each study folder frozen with a three-line README naming the doc section it produced, or drop it after a tag.

**Risk.** Path changes break the `wdremote py` examples and two sys.path inserts in tests; run the unit suite after the move.

## Paradigm-level change proposed

Stop keeping work journals in the live doc tree, and stop restating in prose what the code owns.

Today each burst of work writes a dated narrative (PLAN_25M 59 KB, REVIEW 47 KB, audit streams 273 KB) and marks it live. The reference pages then rot because no reader owns them.

The change:
- Seven reader-owned pages, each with a size cap (see verdict).
- Journals are archived at the end of each burst, or sent to the hub.
- The facts that go stale fastest are checked against code by tests:
  - every `/walldance/*` address, state string and default in OSC_CONTRACT must exist in `core/osc_output.py`, `core/identity_slots.py` and `core/config.py`;
  - every bold UI label in the French manual must exist in the `gui_builder.py` strings;
  - every decision number cited in code must exist in DECISIONS.md;
  - `wdremote release-check` appends to the release log.

Cost: about three days. The manual is one day, the moves plus DEV.md one day, the doc tests and the release log half a day each. Labels built at runtime will need an exemption rule in the label test.

Cheapest intermediate step, half a day:
- fix the index and add the README and NEW_SHOW banners, so nobody is sent to June text;
- correct the exclusion remedy in TERRAIN_DEMO_FR;
- add one test, `test_docs_labels.py`, for the OSC addresses and the French note's labels. tests/test_gui_full_build_smoke.py exists and suggests the suite already builds the GUI headless (I did not read it).

## Questions for the owner

- Who reads the manual on site: does the operator rig and calibrate alone, or only run and monitor? Is a PDF on the laptop desktop the wanted format, and should it be a full manual or printable one-page checklists?
- Is the TouchDesigner patch author a separate person who needs a standalone contract page (in French or English)? Does the patch use /walldance/dancer/state (D22), and what should it do on silence?
- Once LIVE takes `release`, what is the operator's fallback if the new version misbehaves? TERRAIN_DEMO_FR says 'revenir au WallDance habituel'; is a kept known-good previous install wanted?
- May the dated journals (PLAN_25M, ROADMAP_v2_REVIEW, the audit streams, BRAINSTORM) move to archives or to the hub? Is the repo public (D11), which decides whether a CLAUDE.md and a release log naming the laptop can be committed?
- TODO-me: 'Désactiver mise à jour auto Windows' is not mirrored as a REQ in the roadmap; was it done on the laptop? And which menu does 'cleanup menu' (REQ-7, D15) mean?

## Adversarial review

The report is largely sound: four of six claims hold on the code and docs, and the two factual findings I expected to break (exclusion remedy, OSC contract drift) check out line by line. Three things are weakened. The 1/6/15 tally counts DOC-5 as done and DOC-10 as open against the October audit's own definitions. Finding 1 overstates what the French notes lack and infers a fallback state the repo's docs do not support. The rethink's label test and release-log automation do not work as specified, and it ignores that the roadmap already defers the manual to NEXT item P-7. The most important omission is the light-change remedy: the note says the show continues while the code stops the camera, resets the tracker and sends /walldance/clear. Read-only pass: no tests or replays run, one read-only Python script, `git status --short` empty at the end.

### Claims checked

- **weakened** — Verdict: docs are accurate where October's work was written down and wrong at every front door; of the October audit's 22 actions 1 is done, 6 partial, 15 open; docs/ grew from 173 KB to 449 KB.  
  The direction holds and the sizes are exact. By the October audit's own definitions the tally is closer to 0 done, 7 partial, 14 open, 1 obsolete. The index is half-updated, not wholly June.  
  Evidence: Sizes verified: `git ls-tree` at 0a987af~1 gives 12 top-level docs/*.md = 172,707 B (docs/audit-2026-10/05-docs.md:94 says 12 files / 173 KB); HEAD gives 23 files = 448,676 B. Front doors verified: docs/README.md:25 sends 'Run a real show' to NEW_SHOW ('the 1→6 phase rail') while application/src/gui_builder.py:582-586 has three phases; README.md:121 lists Q, R, +/- which application/src/ui/adapter.py:274-297 does not handle. Against the tally: DOC-5 (05-docs.md:367) asked for a forward-only roadmap with done rows moved to ENGINEERING_RECORD, but docs/ROADMAP.md:61-74 keeps a history table, :93-107 keeps completed N-rows, and the record still ends at §11. DOC-10 (05-docs.md:372) is a rewrite for ankle and wrist markers, a premise the field dropped (docs/README.md:41 'the field chose a waist belt'). docs/README.md:37 does list TERRAIN_DEMO_FR as live with 'RIG → EMPTY WALL → LIVE, D29'.
- **holds** — Finding 2 (high, new): the operator note and the in-app toast prescribe 'paint an exclusion, then Calibrate again', which cannot make the empty-wall check pass.  
  Mechanism confirmed by reading, not by running. The painted exclusion still protects the live stream; only the 'calibrate again' step repeats the failure. The branch fires only when every rung down to gamma 0.8 / CLAHE 1.0 fails (empty_wall.py:27-30 measured gamma 1.0 and below clean), so 'high' is generous for a text fix.  
  Evidence: application/src/core/pipeline.py:735-745 fills `last_raw_dets` straight from `_extract_detections`; the exclusion step runs later at :965. application/src/runtime/calibration_flows.py:506 feeds those raw detections to `EmptyWallCheck.feed`, and application/src/core/empty_wall.py:143-158 filters on confidence only. The texts are at docs/TERRAIN_DEMO_FR.md:50-52, calibration_flows.py:534-535 and empty_wall.py:78-79. Nuance: pipeline.py:1953-1976 does drop a detection in a painted cell during live (unless it is near a confirmed track), and manual cells survive a Calibrate (application/src/core/calibration.py:594-597).
- **weakened** — Finding 1 (high, known_open): no operator manual; the only current French note has a one-line failure runbook pointing to a fallback that becomes the same code; nothing in French covers crash restart, camera loss, the launcher prompt, the TRT banner or the alerts.  
  The core (no manual, DOC-2) holds. 'Whole failure runbook is :98-100' and 'nothing in French covers crash restart, camera loss or the alerts' are overstated. The 'same code' inference is not what the repo's own docs say. The report also does not mention that docs/ROADMAP.md:134 and :144 schedule DOC-2 as NEXT item P-7, 'only after §3 is demonstrated'.  
  Evidence: No MODE_EMPLOI file exists; docs/ROADMAP.md:74 says 'still no operator manual'; TODO-me:4 '+++ Mode d'emploi'. But docs/TERRAIN_DEMO_FR.md:74-86 is already a nine-row symptom/action table and covers two of the seven alert kinds in application/src/core/ops_monitor.py (light_changed at :84, height_stale at :86). docs/TOURNAGE_2026-10-06_FR.md:104-107 covers 'La caméra n'est pas détectée' and 'L'appli plante : la relancer'. Not covered anywhere in French: the launcher update prompt, the TRT banner, and the camera_down, fps_drop, gpu_temp, no_detection and over_cap alerts. On the fallback, docs/ROADMAP.md:106 says LIVE is still June's a49d0f2 until the operator starts it and accepts the update.
- **holds** — Finding 3 (high, new): the remote maintainer has no release or rollback runbook, and no doc records what the laptop runs.  
  Holds, and is stronger than stated: ROADMAP gives three different laptop states (:41 DEV at 9659349; :106 DEV at c30b6e9 and LIVE at a49d0f2), none tied to a date log. The documented wdremote subcommands all exist (extra/wdslot.py:581-602).  
  Evidence: docs/REMOTE_OPS.md:3 still says 'not yet on the laptop'. grep of REMOTE_OPS.md for requirements-prod and freeze-lock returns 0 hits; the pin rule exists only as a print at extra/wdremote.py:1155-1157. REMOTE_OPS.md:193 documents rollback for the DEV slot only; launcher/git_manager.py:36 marks AHEAD 'never offer update'. docs/ROADMAP.md:40 gives release = 46a0262 while origin/release is 63f4989 (`git rev-list --count 46a0262..origin/release` = 23). ROADMAP.md:85 says two pushes, :212-213 (D32) one. Promotion is documented in four lines at REMOTE_OPS.md:209-212, and a one-line rollback hint sits in a dated journal at docs/ROADMAP_v2_REVIEW.md:447-449.
- **holds** — Finding 5 (medium, known_open DOC-8): OSC_CONTRACT is true on the wire but not usable as a patch author's page: 381 lines, stale cites, §B still 'Planned', /clear wording wrong, no stream-lifecycle section, oscreplay undocumented, four of six states in the tooltip and docstring.  
  Every sub-claim checked out. One more instance the report missed: application/src/core/config.py:539 describes /clear as 'emitted when no dancers tracked', a third wording that is also false.  
  Evidence: `wc -l` gives 381. docs/OSC_CONTRACT.md:33-35 cites config.py:377-379; the constants are at application/src/core/config.py:522-524. :96 cites tracker.py:3126; the function is at application/src/core/tracker.py:3350. :130 is titled 'B. Planned batch-2 additions (gated on this confirmation)'. :90-92 calls /clear a reset signal 'e.g. engine stop / scene reset', while `send_clear` is called only at application/src/app.py:1991 inside `_cb_tracker_reset` (callers :895, :1135, :2016). grep of OSC_CONTRACT.md for standby, quit and crash gives one unrelated hit (:289); 'oscreplay' has 0 hits in docs/*.md and README.md although extra/oscreplay exists. application/src/gui_builder.py:1127-1128 and application/src/core/osc_output.py:154-155 list four states; application/src/core/identity_slots.py:99-104 defines six.
- **weakened** — Rethink: seven reader-owned pages with size caps, journals archived or sent to the hub, and tests that check doc facts against code (OSC addresses, bold UI labels in the French manual, decision numbers, a release log appended by `wdremote release-check`); about three days, with a half-day first step.  
  The direction and the cheap first step (index row, banners, remedy rewording) stand. Two of the automated checks do not work as specified: bold is emphasis, not labels, so the test needs a markup convention and pattern matching; a log appended at check time records an intent, and the laptop's state needs `slot status` after the push. The three-day figure does not argue against the owner's own P-7 ordering.  
  Evidence: Label test: in docs/TERRAIN_DEMO_FR.md, 71 distinct bold spans; only 19 occur anywhere in application/src, several by accident ('2', 'C', 'vert', '15 s') (my read-only script). Operator-facing strings sit in gui_builder.py, application/src/core/ops_monitor.py:233-234 and :517, application/src/runtime/calibration_flows.py:534 and application/src/core/empty_wall.py:78, and several are f-strings ('Person height 45 -> 127 px', 'The light is …x the empty-wall snapshot'). Release log: docs/REMOTE_OPS.md:209-211 and extra/wdslot.py:597 show release-check is a local preview that prints the push command; Thomas pushes afterwards and dev boxes never push (D1). Sequencing: docs/ROADMAP.md:134 and :144 place DOC-2 and DOC-14 in NEXT item P-7, after the demo. application/tests/test_gui_full_build_smoke.py:31 does build the whole window headless, as the report guessed.

### Missed or out of proportion

- A second remedy in the French note contradicts the code, and it is the prescribed mid-show action. docs/TERRAIN_DEMO_FR.md:84 says to replay the empty-wall take and Calibrate '(le spectacle continue)'; the alert text says the same (application/src/core/ops_monitor.py:233-234). Starting playback stops the IDS camera (application/src/runtime/recording_controller.py:509), resets the tracker and identity slots and sends /walldance/clear (recording_controller.py:189-190, application/src/app.py:2016 and :1983-1991). The live stream is gone for the take plus 20-30 s of Calibrate, and under D5 TouchDesigner drops its video. Read, not run; matches blind lead gui-operator#3.
- The fallback may be worse than 'same code'. Session memory outside the repo (dated 2026-10-08, not verifiable here) records that LIVE updated itself to c30b6e9 and that this build still has the stale-frame Calibrate defect fixed in 35d03a0 (`git log c30b6e9..63f4989` shows the fix). If so, 'Revenir au WallDance habituel' (docs/TERRAIN_DEMO_FR.md:100) sends the operator to a build with a known Calibrate bug unless they accept the launcher prompt. No repo doc records this; docs/ROADMAP.md:106 still says LIVE is June's code.
- The report does not size the failure table it asks for. application/src/core/ops_monitor.py raises seven alert kinds (camera_down, fps_drop, gpu_temp, height_stale, light_changed, no_detection, over_cap), all in English; the French notes cover two. That, plus the launcher prompt and the TRT banner, is the real scope of 'Si ça ne va pas'.
- docs/TERRAIN_DEMO_FR.md:70-71 tells the operator to switch Image Size to 1536. extra/build_engines.sh:168 builds 1536 only in the full grid, and `--default-only` skips it. No doc records which engines the laptop has, so the instruction can end in an engine build or the PyTorch fallback mid-session. Not checked on the laptop.
- Out of proportion: finding 8 (reorganise tmp_analysis/, effort M) is a code-layout change, not a docs fix. The laptop runs those paths through `wdremote py tmp_analysis/...` (docs/REMOTE_OPS.md:228), so moving them needs a release to the laptop. It should not sit beside the half-day text fixes.
