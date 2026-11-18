# Dimension — validation

*Context-aware auditor's report, followed by an adversarial reviewer's check of its six most consequential claims. Where the two disagree, the reviewer's reading is the safer one.*

## Verdict

Nobody can show that the shipped stream meets the demo KPI. No two-dancer take has been scored with the shipped defaults, and the only duo evidence, the April proxy, fails it (coverage 0.86, on-dancer 0.76, 8.4 s hole) while the suite is green.

The docs list six gates: unit suite, golden trio, TEST-1, TEST-2, the 12-scenario sweep, the demo KPI. In practice a release was gated on the dev37 unit suite, a laptop unit run with one known failure, before/after replays kept in tmp_analysis, and Thomas's push. The trio is opt-in and compares tracker counters at pre-D33 pins. TEST-1 only prints. The sweep cannot pass. The KPI has no take. CI stops at a collection error on `release` (simulated here).

TEST-2 is the one automated gate on what OSC carries; it cannot see smart hold, foreground, belt or stalls. Replays run on a uniform clock, so the eight 1.6 s stalls inside the 30 m takes reach no metric. A field regression would be caught only by the technician's eye.

The fix is cheap: label the pending duo takes and score distinct on-dancer points on the emitted stream, in wall-clock time, with shipped defaults, behind an exit code.

## Since the October audit

Since the 2026-10-05 audit: TEST-1 (C1-C10, long-span manifests) and TEST-2 (six emitted fixtures with KPI floors) were built. CI was made passable on 10-06 and broken again three hours later by an unguarded import. `flow_check.py`, `empty_wall_check.py` and `replay --stall / --quality` appeared. D33 flipped the code defaults while every manifest stayed pinned to the old recipe. `release` moved to 63f4989 with one failing laptop test; D34 and the stale-plate fix stayed unreleased. The two-dancer takes planned as N-10 turned out solo (N-14); the rehearsal duo takes are still on the laptop. October's validation evidence lives in one-off scripts under tmp_analysis, not in tests. Nothing since 10-09.

## Details

**Evidence map.** U = unit tests, F = TEST-2 fixture, A = April duo proxy (dev37), S = solo takes, L = laptop DEV run.

| Feature | Validated by | Automated gate today |
|---|---|---|
| Identity slots | U 35 + 11 pipeline; F x6; A; S on L | TEST-2 floors; exact compare on dev37 only |
| Smart hold | U 5; one-off `slot_eval` on 14 timelines incl. A | Unit only; TEST-2 blind (flag off changes 0/6 streams) |
| Static-ghost guard, yield | U 4; F: synthetic ghost on A, Bordeaux S | TEST-2 (flag off fails 2/6 floors) |
| Foreground veto, backed hold | U 4; S on L (s2c 88.6 -> 100 %); A with a take-median plate | Unit only; fixtures carry no foreground |
| Selective plate update | U 4; before/after on S (mur30m, night) | Unit only; stale-frame fix unreleased |
| Belt hook | U 23 + 8; S: belt on a floor walker and on a stand | Unit only; fixtures carry no belt |
| Height guard | U 16; A (inactive), Bordeaux S (adopts the close-up) | Dev box and laptop only: 14 tests fail without ultralytics |
| Empty-wall YOLO check | U 8 + flow mocks; one bright empty take; `flow_check` on S | Unit; `flow_check` has no verdict |
| D33 defaults | U 16 (default-vs-stored only); recipe on A with exclusion, S on L, mur30m S | None on footage; a test keeps manifests off D33 |
| D34 stall hold | U 14; 229 synthetic stalls on recorded timelines; 6 injected + 1 real on S | Dev box only (module torch-gated); unreleased; no real stall, no TD check |
| IDS gain fix | U 10 on fake nodes; diagnosed from `.meta` node read-back | Unit; no post-fix camera read-back found |

**Tool runs** (GPU hidden; the failing branch does not depend on it)
- `soak.py --max-chunks 1`: RuntimeError "GPU pipeline required", exit 1, after printing `[Soak] PASS` and writing a PASS summary for 0 frames.
- `calibrate_project.py`, `calibrate_segment.py` (Auto-tune): same error, exit 1, no output file.
- `sweep_project._run` on today's `--score` stdout: `(None, None)`; the June shape parses.
- `replay_sweep.py`: not run (needs GPU). 13/13 archived summaries say `"path": "cpu"`; replay now writes gpu or trt.
- `test_emitted_golden.py`: 13 passed here.

**Never observed**
- Contact: foreground hold and veto with a real plate, two belts, the size gate and height guard on a duo. Only the April duos (off-axis light, take-median plate; switches 10 -> 16).
- Dark 30-40 m: four solo floor walks at ~30 m; nobody hanging; nothing beyond.
- Light drift: between takes and with mismatched plates only. A stale plate removes veto and hold mid-show (alert only), leaving the recipe that put a second point on 90 % of aerial frames without exclusion.
- Stalls: `release` lacks D34; 8 real stalls sit in the mur30m camlogs, 7 never replayed as stalls.

**Could run in CI today and does not:** the six pure `CameraStallClock` tests (hidden by the module-level importorskip at test_camera_stall.py:107), the exact emitted compare (align numpy/scipy), a parse smoke test for `sweep_project`, stall-injected slot fixtures, the model-less pipeline and GUI smoke tests on CPU torch.

**Side effect to report.** My CI-simulation run of the unit suite created one empty gitignored directory, `tmp_analysis/remote-runs/20261118-102250` (test_wdremote.py:187; every suite run does this). `git status --short` is clean. Left in place.

## Findings

### 1. The shipped defaults have never been scored on two dancers; the duo proxy fails the demo KPI while every gate is green

severity critical · confidence high · status known open · effort M · prior id: N-10 / CONTEXT §6

**Evidence.** docs/PLAN_25M_2026-10.md:25 says no baseline exists; ROADMAP.md:103,107: N-10 pending, the N-14 takes were solo. All 19 manifests pin intermittent confirm off (test_d33_defaults.py:312-317). Re-run today: fixture white-duo-full (April pin, shipped slot defaults) gives coverage 0.865, on-dancer 0.756, longest hole 8.43 s, coasting 0.18, 13 switches. It passes FLOORS 0.85 / 0.74 / 0.20 (test_emitted_golden.py:43) against a KPI of 0.95, 1 s and 0.10 (PLAN_25M:17-20).

**Recommendation.** Run the pending rehearsal duo takes (t-020) on the laptop DEV slot with shipped defaults (`replay --trt --quality --internal --timeline`), pull timelines and 20-frame sheets only, label per-dancer centroids (~1,200 frames, 2 h). If they lack them, shoot: empty wall, dancers apart, crossings with contact, entries/exits, ten continuous minutes with TD.

**Risk.** The first evidence about two dancers at the wall arrives in front of the client.

### 2. CI stops at a collection error on `release`, and release-check returns 0 on a failed suite

severity high · confidence high · status regression · effort S · prior id: ARCH-9

**Evidence.** Unit suite with torch, ultralytics and dearpygui hidden (.github/requirements-ci.txt installs none): `1 error during collection`, 0 tests run. test_engine_dir_override.py:2 imports core/model_manager.py, whose line 14 imports ultralytics; added by f93aac6 three hours after ARCH-9 (d7b576c). Continuing past it: 15 failed (test_auto_height.py:37, test_foreground.py:323), 864 passed, 53 skipped. ci.yml:9-11 never runs on remote-ops. extra/wdslot.py:489-497 prints FAILED, then the push line, and returns 0. GitHub status not checked.

**Recommendation.** Guard the three imports with importorskip, add `remote-ops` to the ci.yml push branches, make `release-check --tests` return non-zero and withhold the push line. Add one CPU-torch job (pyproject already names the CPU index) so the 164 tests that pass on dev37 but not in CI run there, D34 and the height guard included.

**Risk.** The push that deploys to the show laptop is checked by nothing that can fail.

### 3. Replay time is synthetic: real stalls inside takes are flattened, and the released build is silent through them

severity high · confidence high · status new · effort M

**Evidence.** tests/replay.py:376-385 clocks the output stage at frame/fps; core/video_recorder.py:186-205 stores frames/elapsed only; stalls enter by hand (`--stall`, replay.py:407-415) and no test or manifest passes one. The 1 Hz camlogs of mur30m-0710 show 8 real stalls of 1.59-1.65 s in 4.7 min (slot 2: four in 96 s); PLAN §C.16 replayed one. origin/release (63f4989) lacks D34 (e3ad7de), so OSC is silent there and no replay metric counts it.

**Recommendation.** Stamp each frame at `RecordingJob.put` (monotonic time, IDS frame id, device timestamp) into a sidecar. Let replay drive `set_output_clock`, `note_camera_frame` and `osc_keepalive` from it, and report holes in wall seconds. Until then, derive `--stall F:S:gap` from the camlog's `t` and `frames`: a 20-line script over those fields located all eight today.

**Risk.** 'Longest hole <= 1 s' passes on replays of takes whose live output was silent for 1.6 s several times a minute.

### 4. Score, pass line and tuners judge the tracker stream, and a count score is blind on the emitted one

severity high · confidence high · status known open · effort M · prior id: REVIEW §1.9 / F7

**Evidence.** core/pipeline.py:1047-1055 emits the slot tracks and returns the tracker's. replay.py:912-923 scores `reported`. tune.py:88-93 replays without track_details, so `emitted` is not recorded (replay.py:495-500); known_n.py:171 uses that Tuner. On committed fixtures today: white-duo-full count score 0.449 (tracker) against 0.137 (emitted) while on-dancer falls 0.898 to 0.756; bdx-s5 emitted count score 0.001 with on-dancer 0.80.

**Recommendation.** Make the objective position-aware on the emitted stream: share of labelled dancer positions with a distinct emitted point within 0.75 h (one-to-one), plus phantom points, longest hole in wall seconds and coasting share. Use it in `evaluate_pass`, tune.py and known_n.py; keep the count score as a diagnostic.

**Risk.** Knobs are tuned and saved into projects against a list TouchDesigner never receives; a point off the dancer scores as perfect.

### 5. Footage goldens compare tracker counters at pre-D33 pins and are part of no release run

severity high · confidence high · status known accepted · effort M · prior id: D33 / CONT-2

**Evidence.** test_regression_replay.py:57-65,116-127 compares track classes, swap count, zero-detection frames and average detections; replay.py:896 drops the timeline. The trio pins confidence 0.5 / 0.49 / 0.35 with intermittent off; goldens date from 2026-06-24 (a3f318d). It needs WD_RUN_REPLAY=1 (:76), which extra/wdremote.py:991-993 cannot set; the laptop run shows 22 skipped (tmp_analysis/laptop_0810/02_dev_pytest.txt:63). No October artefact of a trio run found. scenarios/README.md:93-95 calls the long-span suite a laptop gate; it only prints (replay.py:920-923).

**Recommendation.** Add a shipped-defaults variant of each gated manifest (venue keys only; `fill_detection_defaults` supplies the rest), golden its emitted-stream KPIs and assert the pass line in pytest. Keep the pinned trio as history. Replace the pin test with one requiring gated scenarios to carry no detection keys.

**Risk.** A regression specific to confidence 0.15 with intermittent confirm, the regime every new project runs, is invisible to every golden.

### 6. TEST-2 cannot see smart hold, foreground, belt backing or stalls, and its exact compare runs on dev37 only

severity medium · confidence high · status new · effort M

**Evidence.** Mutation run on the six fixtures: smart_hold, fg_veto, fg_hold or belt_backing switched off change 0/6 streams; stability 1.5 changes 6/6 streams and fails 0/6 floors (switches 10 to 18). emitted_golden.py:115 calls `slots.update(cands, t, hidden=...)`; the app also passes belt, bounds, fg and t_motion (pipeline.py:1506-1526). Goldens record 65 of today's 83 SlotParams. The exact compare skips unless numpy/scipy are 2.2.6 / 1.15.3 (test_emitted_golden.py:25); requirements-prod.txt:44,79 pins 2.4.4 / 1.17.1, CI numpy < 2.

**Recommendation.** Record bounds, belt blobs, foreground blobs and zone flags in fixture rows and drive them through one slot-driving function shared with the pipeline. Rebuild the goldens under requirements-prod.txt and align CI with it. Add floors for longest hole and id switches, and a stall-injected variant of each fixture.

**Risk.** Four default-on October behaviours can break with the suite green on every machine.

### 7. Four harness tools fail when run; the operator's Auto-tune button cannot succeed

severity medium · confidence high · status new · effort M

**Evidence.** Run today: `soak.py`, `calibrate_project.py` and `calibrate_segment.py` (app.py:2492-2499, Auto-tune) raise 'GPU pipeline required' (pipeline.py:592-595, since 2026-06-24) because replay.py:197 defaults use_gpu_path to False. `sweep_project._run` returns (None, None) on today's stdout: JSONDecodeError on both parts (sweep_project.py:53-59 against replay.py:915-933). replay_sweep.py not run: its archive says path cpu, replay.py:656 writes gpu or trt. The last soak PASS is 2026-06-13 (docs/TODO.md:33).

**Recommendation.** Give replay.py a `--result out.json` and make every tool read it instead of scraping stdout. Default `_build_processor` to the GPU path. Add a no-GPU smoke test per tool against a stub replay. Remove or repair Auto-tune, retire replay_sweep with its archive, and soak the October build once with `--trt`.

**Risk.** The soak, the sweep and the per-project calibration give no signal, and no October feature has been soaked.

### 8. A field regression has no detector: live logs carry no time and no emitted-stream KPI is computed

severity medium · confidence medium · status new · effort S

**Evidence.** core/tracking_logger.py:338-357 writes FRAME_SUMMARY with frame, emitted ids and slot states but no wall time (sample: projects/default/sessions/20261006_204746_live, 24,846 frames, keys frame / event / camera / data). analyze_session.py:175-191 reports tracker-track classes only. On 2026-10-08 13:00 LIVE ran c30b6e9 on engine x@800 (tmp_analysis/laptop_0810/03_status_1300.txt); I found no artefact of 63f4989 running LIVE or of a laptop suite at that sha.

**Recommendation.** Add `t` to FRAME_SUMMARY and an after-show report computed on the laptop from the live log: holes >= 1 s per slot, coasting share, frames above or below the declared N, stall count, GPU temperature. `wdremote` pulls about 20 KB per show; compare it with the previous show and the release baseline.

**Risk.** A worse show is noticed only if the technician sees it and reports it; nothing compares one show with the last.

## Paradigm-level change proposed

Change what counts as evidence. Today it is developer-picked windows, replayed with frozen configs and judged by tracker counters. Make it field takes replayed the way the operator lives them (Calibrate on that take's empty wall, shipped defaults, real frame times) and judged on the OSC stream against sparse human labels.

One command on the laptop DEV slot writes a verdict keyed by sha and exits non-zero; the push line prints only on PASS. Gate: share of labelled dancers with their own emitted point within 0.75 h >= 0.95, longest hole <= 1 s of wall time, phantom points <= 2 %, coasting <= 10 %, none worse than the last release.

`flow_check.py` is most of this already (real CalibrationFlows, whole takes, emitted stream). It lacks N > 1, labels, a time base and a verdict.

Cost: 3-4 days (frame sidecar 0.5, label format and click tool 1, metric and gate 1, flow_check 0.5, laptop plumbing 0.5), plus about 2 h of labelling per shoot. Second half: each show reports its own KPI from the live log, so field behaviour is seen without pulling 1 GB/min over 4G.

Cheapest step, one day: fix the CI collection error and the release-check exit code; run the pending duo takes on the laptop with shipped defaults; pull timelines and contact sheets; label every 20th frame; commit them as TEST-2 fixtures with a distinct-point on-dancer floor.

"GPU+TRT is the only evidence path" stands: timelines come from TRT, only scoring is CPU.

## Questions for the owner

- What do the pending rehearsal takes (t-020) contain: how many, how long, two dancers hanging on the wall, an empty-wall take per lighting, and which settings were live (imgsz 800 or the D27 recipe)?
- Does the TouchDesigner patch drop a video on OSC silence (a timeout) or only when an id leaves /count? That decides how much the released build's 1.6 s silences cost, and whether the hole metric must count them.
- Is a swap at contact acceptable when each slot drives one dancer's video? That decides whether labels need dancer identity or positions only.
- Is CI enabled on GitHub, and has it been red on `release` since 2026-10-08? Was the golden trio (WD_RUN_REPLAY=1) or the PoseRunner A/B run on the laptop before the pushes? I found no artefact of either.
- Has the laptop's LIVE slot started since 2026-10-08, and with which project (the imgsz-800 `default` or a D27 project)?
- Who labels, the technician with a click tool or you, and is about 2 h per shoot acceptable?
- Do Auto-tune, Known-N and Dry-run stay in the operator build?

## Adversarial review

Three of the six claims hold and three are weakened; none is refuted. The report's measurements reproduce: the fixture KPIs, the count scores, the eight camlog stalls and the CI collection error all came out as stated when I re-ran them.

Its weak points are framing:
- **Headline numbers:** 0.86 / 0.76 / 8.4 s belong to the April pin, not the shipped recipe. With the D27 recipe the same proxy scored 0.96-0.97 two points and misses only on hole and coasting.
- **Release gating:** the report says it found no artefact of the PoseRunner A/B. One exists from the laptop on 2026-10-07, and its hangar-aerial run matches the June golden.
- **Gate proposal:** it would fail on day one against the best existing evidence, and it covers four of the seven KPI lines.

Also reproduced, outside the six:
- **Finding 6:** switching off smart_hold, fg_veto, fg_hold or belt_backing changes 0 of 6 streams; stability 1.5 changes 6 of 6 and fails no floor; the goldens record 65 of 83 SlotParams.
- **Finding 7:** confirmed by reading the code only (application/src/core/pipeline.py:592-595, application/tests/replay.py:197, application/tests/sweep_project.py:53-59, and 13 archive summaries with path "cpu"). I did not re-run soak or the calibrate tools.

Not checked: whether GitHub Actions is enabled, and the full unit suite (it creates a directory under tmp_analysis/remote-runs).

The repo was left untouched: `git status --short` is clean and nothing under /data/WallDance is newer than the start of this review. My scripts are in tmp_analysis/audit-2026-11/validation.

### Claims checked

- **weakened** — Verdict: no two-dancer take has been scored with the shipped defaults; the only duo evidence, the April proxy, fails the KPI (coverage 0.86, on-dancer 0.76, 8.4 s hole) while the suite is green.  
  The gap is real and already CONTEXT §6, but the headline quotes the wrong configuration: with the shipped recipe the proxy misses on hole (1.3-1.5 s) and coasting (12-14 %), not on presence.  
  Evidence: My re-run of fixture white-duo-full gives 0.8646 / 0.7556 / 8.43 s and passes FLOORS (application/tests/test_emitted_golden.py:43), as reported. But that fixture is the April pin: motion_first, confidence 0.25, intermittent off (`config` in application/tests/scenarios/white-duo-full.json), not the D33 defaults (application/src/core/config.py:1041-1050). The D27 recipe, which D33 made the default, was scored on both April duos on the emitted stream: docs/PLAN_25M_2026-10.md:135 (white duo: two points 0.973, hole 1.26 s, coasting 0.123, on-dancer 0.930), :196 (with exclusion: 0.960 / 1.51 s / 0.135 / 0.973), :137 (textured duo: 0.990 / 1.06 s / 0.264 / 0.887); repeated in docs/ROADMAP.md:116-118. Those runs date from 2026-10-06, before 46a0262 and the foreground and entry changes, and use YOLO as their own reference. The six-gate ladder is as quoted (ROADMAP.md:80-81).
- **holds** — Finding 2: CI stops at a collection error on `release`, and `release-check --tests` returns 0 on a failed suite.  
  Not checked: whether Actions is enabled. CI is not in the ROADMAP gate ladder and the deploy is Thomas's push (D1), so this is a dead advisory signal; the exit code is the part that matters.  
  Evidence: Reproduced with torch, ultralytics and dearpygui masked as absent: `Interrupted: 1 error during collection`, exit 2, at application/tests/test_engine_dir_override.py:2 -> application/src/core/model_manager.py:14. The file is on origin/release (f93aac6, 2026-10-06 13:27, is an ancestor; ARCH-9 d7b576c is 10:11 the same day). .github/requirements-ci.txt installs neither package; .github/workflows/ci.yml:9-11 triggers on main and release only. extra/wdslot.py:489-497 prints FAILED, then the push line, and returns 0. Under the same mask test_auto_height.py and test_foreground.py give 15 failures, and application/tests/test_camera_stall.py:107 skips the six pure clock tests above it.
- **holds** — Finding 3: replay time is synthetic, so the eight real 1.6 s stalls inside the 30 m takes reach no metric, and the released build is silent through them.  
  New only for the replay-clock half; the silence on release is PLAN §C.16 and D34, built and waiting. With D34 those stalls become coasting, about 4.6 % of wall time, half the 10 % budget, which the report does not say.  
  Evidence: application/tests/replay.py:376-385 clocks the output stage at frame/fps; application/src/core/video_recorder.py:173-176 and :186-205 keep only frames and elapsed; stalls enter by hand (replay.py:407-415) and grep finds no test or manifest that passes one. I re-derived the stalls from the four camlogs in projects/mur30m-0710/recordings: 8 gaps of 1.59-1.65 s in 4.7 min (slot 2: four in 95.6 s; slot 3: one; slot 4: one; slot 5: two). e3ad7de is not an ancestor of origin/release, which has no camera_stall code.
- **holds** — Finding 4: score, pass line and tuners judge the tracker stream, and a count score is blind on the emitted one.  
  Reach is narrower than the risk line says: the tuners are Expert tools since D29, Auto-tune cannot run at all, and October's decisions were taken on emitted-stream tables (PLAN §C.3, C.8).  
  Evidence: application/src/core/pipeline.py:1047-1055 emits the slot tracks and returns the tracker's. application/tests/replay.py:907-923 scores `reported` and only prints the pass verdict; replay.py:495-500 records `emitted` only with track details, which application/tests/detect_cache.py:246 defaults to False for application/tests/tune.py:88-106 and application/tests/known_n.py:171. My re-run on the committed fixtures: white-duo-full count score 0.449 (tracker) against 0.137 (emitted) with on-dancer 0.898 against 0.756; bdx-s5 emitted count score 0.001 with on-dancer 0.803.
- **weakened** — Finding 5: footage goldens compare tracker counters at pre-D33 pins and are part of no release run; no October artefact of a trio run or of the PoseRunner A/B exists.  
  The pin is the owner's choice (scenarios/README.md:27; re-pin planned as P-2, ROADMAP.md:139). What stands: no gate at confidence 0.15 with intermittent, and the A/B predates 46a0262 and the three 2026-10-08 release commits.  
  Evidence: The counters, the opt-in and the pins are as stated: application/tests/test_regression_replay.py:57-65, :76, :116-127; trio manifests pin confidence 0.49 / 0.5 / 0.35 with intermittent false; goldens last changed in a3f318d (2026-06-24). But an artefact of the laptop release gate exists: tmp_analysis/remote-runs/20261007-150100/out/ab_poserunner.jsonl shows hangar-aerial and white-duo replayed twice with `--trt --score` on the DEV slot, rc 0, summaries and timelines byte-equal. The laptop summary hangar-aerial_runner_summary.json in the same folder equals application/tests/golden/hangar-aerial.json on all seven compared counters. The env can be set through the `py` route (tmp_analysis/plan25m/ab_poserunner.py:36-42).
- **weakened** — Rethink: one laptop command, built on flow_check.py, scores labelled field takes on the emitted stream in wall time with shipped defaults and withholds the push line unless the KPI passes; 3-4 days.  
  Right direction and consistent with the keep-list (GPU+TRT only). It should start as a ratchet against the last release with the absolute thresholds reported, and the cost omits the wait for a laptop window.  
  Evidence: application/tests/flow_check.py is as described: real CalibrationFlows (:62, :95-107), whole takes through `replay --trt --quality --internal --timeline` (:170-173), emitted stream (:191-197), N = 1 (:10, :241, :249-254, :267), FPS fixed at 20 (:40), no verdict (:462-465). Five limits. (a) It is already ROADMAP N-10 (docs/ROADMAP.md:103), blocked by the laptop being offline, not by tooling. (b) The absolute gate fails on the best evidence there is (docs/PLAN_25M_2026-10.md:135, :196: hole 1.26-1.51 s, coasting 0.123-0.135), so it would withhold the push for D34 and the stale-plate fix, unreleased for six weeks. (c) It covers four of the seven KPI lines (PLAN_25M:17-23): no jitter, lag, or frame rate and GPU temperature with TD. (d) flow_check replays the project's stored config (:408), not the shipped defaults, and fakes the camera (:97-100). (e) No existing take has frame times (application/src/core/video_recorder.py:186-205).

### Missed or out of proportion

- Three KPI lines are absent from the evidence map and the proposed gate. Jitter is known to fail at the shipped Stability 0.5 (application/src/core/config.py:322): 3.16 % of height on the white duo against a 1.5 % target, and 1.25 is the first value that meets jitter and lag together (docs/PLAN_25M_2026-10.md:296-303). FLOORS carry no jitter, lag, hole or switch floor although the docstring promises jitter (application/tests/test_emitted_golden.py:7 against :41-59). Nothing checks 20 fps with TD under 83 C.
- Calibrate on the live camera, the operator's one button, has no harness and failed in its first field use. df0bee8 and 35d03a0 (2026-10-08 14:00 and 14:11) fixed a servo that drove the IDS to 0.2 ms and a black preview; 35d03a0 changes main_loop.py only, with no test, and was pushed about 14:45 with no laptop suite at that sha. flow_check fakes the camera (application/tests/flow_check.py:97-100). The only trace I found is a phone snapshot going from black (tmp_analysis/laptop_0810/snap_3.jpg, 14:07) to lit (snap_4.jpg, 14:13) on a desk scene.
- What is validated is not what existing projects run, and the report leaves this as a question. D33 fills absent keys only (application/src/core/config_schema.py:265-287). On the dev37 mirror the newest configs of default, mur30m-0710 and mur25m-ceinture-0610 store yolo_imgsz 800 with intermittent false, and LIVE ran default at x@800 on 2026-10-08 13:00 (tmp_analysis/laptop_0810/03_status_1300.txt). The only mover is the hand-run tmp_analysis/plan25m/apply_d33.py; I found no readiness row that flags a project off the validated recipe.
- TEST-2 gates the slot layer only, which the verdict's 'the one automated gate on what OSC carries' hides. Its fixture rows are tracker outputs recorded on 2026-10-06 (each fixture's `source` field); since then tracker.py changed twice (46a0262, 12ce95f) and pipeline.py nine times. With the trio opt-in, no automated footage check covers detection or the tracker.
- The proposed after-show report counts frames below the declared N as a fault. N is a cap (D4) and dancers leave the wall, so a live log cannot tell an exit from a drop without the border-exit classification smart hold already computes; as specified it would raise false alarms every show.
