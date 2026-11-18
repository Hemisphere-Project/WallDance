# Dimension — operator-ux

*Context-aware auditor's report, followed by an adversarial reviewer's check of its six most consequential claims. Where the two disagree, the reviewer's reading is the safer one.*

## Verdict

The operator surface is a developer console under a three-tab rail, not the north star's "rig, aim, one button". From power-on to a running show, the code and the French note require about 23 steps, 6 modals and a dozen judgement calls; four of those steps are in the north star. The default surface exposes 97 controls (75 in the main window); expert mode hides 10. Everything the operator reads is English with pipeline units, and since D29 the decisions hinge on transient sentences, not labels. Health is the weakest part: every alert but one is an 8-second single-slot toast, the OSC chip shows a configuration flag, camera stalls reach only the console, and no place states "healthy / not healthy and why". Nothing protects a running show from the person at the laptop: the app boots in RUN, one click on a slot digit replaces the live camera by a looped recording while RUN and OSC stay green, and the documented light-change remedy does exactly that while the note says the show continues. The parts for a fix exist: alert-strip API, remote policy table, expert tags, readiness checks, D34 hold. DearPyGui stays; I found no new reason against it.

## Since the October audit

D29 cut the rail from six steps to three and D30 removed the L slider (PLAN §C.13). In the same four days the Live panel went from 12 to 21 controls (AST count at 45b35e4^ vs HEAD: nine Dancer-ID controls and the ball toggle in, the L slider out). Expert gating is still June's three groups. The alerts strip got its first and only resident (light_changed, f279562). Readiness gained the "empty wall" and "dancer size" rows; the picker got "New" (63f4989); the height guard took the dancers pass out of the flow. The note's eight Dancer-ID "starting values" equal the code defaults, and D33 put confidence at Dial A's floor, which made that dial one-sided. The crash marker shipped; D13's restart into STANDBY did not. Nothing changed after 2026-10-08: ARCH-17, the phase chips, the ResetTracker binding and the French manual (DOC-2) are still open.

## Details

**1. Journey today** (code + TERRAIN_DEMO_FR). ✗ = not in the north star, J = judgement, M = modal.

1. ✗ J Pick the icon: "WallDance DEV" or the usual one.
2. ✗ M J Launcher: update check; "Update Available?" or "Switch to release / Keep local" (launcher/gui.py:191-205).
3. ✗ M J Project picker, no_close (main_loop.py:322-328).
4. ✗ M Model loading; J if the engine is missing: "Build TensorRT / Use PyTorch", no default, no timeout (model_controller.py:249-251).
5. The app is already in RUN (app.py:521): OSC flows while rigging. No Go-Live step.
6. ✗ J Rig: "Config version" = newest.
7. ✓ Mount the camera. ✗ J source, Mirror/Rotate, crop, gain/exposure.
8. ✓ Focus: QR (M), phone Focus mode.
9. ✓ Aim the IR.
10. ✗ J ROI: Enable, double-click, drag "wall only".
11. ✗ J Exclusion paint.
12. ✗ Rig sheet, 11 fields.
13. ✗ Empty take per lighting: REC, slot digit, 15 s, REC; write slot/time/lighting on paper.
14. ✗ Click that slot (source = recording).
15. ✓ CALIBRATE, 20-30 s.
16. ✗ M J Read a ~12-line report; "Save to project / Keep this session only".
17. ✗ Press LIVE (nothing prompts it).
18. ✗ J Check readiness: 8 rows.
19. ✗ J Dancer 10 s at the far wall, readiness again; maybe narrower ROI or Image Size 1536.
20. ✗ J Live: verify 8 Dancer-ID values (all code defaults); Max dancers.
21. ✗ Tick "Allow remote control during RUN" (every start).
22. ✗ Ctrl+S (no autosave, no unsaved mark).
23. Show: Stability / Hold / dials by symptom (J); light change = 14-17 again.

North star = 7, 8, 9, 15.

**2. Controls.** Counted by running `build_ui` against a recording stub of DearPyGui ((session scratch, not kept)/opux_count_widgets.py): buttons, checkboxes, sliders, combos, inputs, radio. Excluded: 32 ± nudge buttons, 11 section headers, modal contents.

| Area | Widgets | Shown by default |
|---|---|---|
| Top bar + rail | 6 | 6 |
| Rig | 27 | 14 |
| Calibrate | 3 | 3 |
| Live | 21 | 21 |
| Recordings bar | 17 | 12 |
| Advanced, everyone | 22 | drawer |
| Advanced, expert only | 10 | hidden |

Main window 74 (+1 hidden "Rebuild TRT"); total 107; reachable without expert mode 97. The blind scan's 74 and 10 hold; its 24 is 22. Plus 11 shortcuts.

**3. Target surface** (about 18 controls, rail included)

- Stay: health light with reasons, show lock, RUN/STANDBY; Rotate, Mirror, ROI edit and reset, phone QR; CALIBRER with a one-line verdict; Max danseurs, Hold, Stabilité; REC, LIVE, a takes list with names.
- Expert (Ctrl+Shift+E): config version, safe defaults, camera source, crop/gain/exposure, exclusion paint, rig sheet, both dials, the seven Dancer-ID switches, View S/K/B/T, IMPORT, transport, ISSUE, the ms stats, all of Advanced.
- Remote only: Model / Image Size / TensorRT, OSC target, tunes, dancers pass, profiles.
- Automatic: last project after a countdown, newest config, TensorRT build by default, the empty take (recorded by Calibrate), save after Calibrate, return to LIVE, readiness (continuous, off the frame loop), remote permission remembered.
- Health light aggregates: live source (not playback), camera and stall count, RUN, FPS, TensorRT active, OSC sends, empty-wall snapshot and light ratio, enhancement on, share of held points, dancer size, GPU temperature, frame errors. Rouge = no valid output; orange = degraded.
- Show lock: refuses project, source, playback, calibration and model commands locally, mutes bare keys, confirms close; hold to unlock.
- Phone: focus and IR aiming as today, plus the health light, read-only.

## Findings

### 1. The light-change remedy, and any stray click on a slot digit, replaces the live camera by a looped recording in RUN

severity high · confidence high · status new · effort M

**Evidence.** recording_controller.py:426-428: a click on a slot digit starts playback, with no state check and no confirmation; :509 stops IDS acquisition. main_loop.py:636-640 feeds playback frames; :813-816 processes them in RUN; pipeline.py:1027 gates OSC on osc_enabled only. video_recorder.py:563-567 loops at end of file; nothing returns to LIVE after Calibrate. ops_monitor.py:412-413 mutes the fps and no-detection alerts during playback. TERRAIN_DEMO_FR.md:84 prescribes this mid-show and says "le spectacle continue"; CHECK_TEST.md:41-42 confirms OSC from a recording in RUN. Not run on the rig.

**Recommendation.** Under the show lock, refuse playback; otherwise ask for a second click. When RUN plays a recording, show a persistent red banner (reuse trt_banner_window), alert after 60 s, and return to LIVE when Calibrate ends. Decide whether OSC should hold the last live points (the D34 path) instead of sending the take. Correct the note.

**Risk.** Desk rehearsals feed TouchDesigner from a recording in RUN (CHECK_TEST 1.3): gate on the lock, not on RUN alone.

### 2. No single health indicator: alerts are 8 s single-slot toasts and the chips show configuration

severity high · confidence high · status new · effort M

**Evidence.** adapter.py:345-348 turns every api.Alert into show_toast(duration=8.0); adapter.py:34 keeps only light_changed in the strip. gui.py:2930-2931 deletes the previous toast, so any information toast evicts an alert. config.py:1124: a sustained fault re-rings every 120 s, so it is on screen about 7 % of the time. gui.py:1771-1774: the OSC chip is the osc_enabled flag; adapter.py:413 ignores StateChanged, so a remote SetState leaves the RUN badge stale. ops_monitor.py:95: the Go-Live result says "see console". app.py:1020-1048: /status carries no alerts. gui_builder.py:1181-1186 lists the strip's intended residents as "gated".

**Recommendation.** One runtime health model: the active conditions, each with a level, a French sentence and a remedy, set and cleared by HealthMonitor for every kind. Render it as a top-bar light with its reasons, the strip, /status, the phone page and optionally an OSC message. First step: all kinds in STRIP_ALERTS plus cleared events.

**Risk.** A light that is red without cause gets ignored: 30 s without dancers is normal between scenes, so the thresholds need a field pass.

### 3. No show lock: the app boots in RUN and the local surface refuses almost nothing

severity high · confidence high · status new · effort M · prior id: related: ARCH-17 (keys while typing), D13

**Evidence.** app.py:521 and gui.py:316 start in RUN. ROADMAP.md:235 (D13) says STANDBY after a crash; main_loop.py:490-493 only toasts. remote_api.py:56-96 classifies 93 commands; locally only app.py:993, 2343, 2442, 2572 check the state. One unconfirmed click in RUN: project combo (gui.py:1067-1069, model reload), safe defaults (gui.py:1040-1048), CALIBRATE (calibration_flows.py:227-241), Capture empty wall, STANDBY, window close (no exit callback, gui.py:3107-3117). A bare E switches enhancement off silently (adapter.py:278-279, app.py:1166-1173), also while typing (ARCH-17). Opening tab 2 runs readiness on the frame loop: a 0.25 s OSC probe wait (gui.py:537-541, app.py:2314-2335; read, not measured).

**Recommendation.** Tag RuntimeAPI.submit with its source and apply the POLICY table to local commands while a "Verrou spectacle" toggle is on: refuse heavy, never, source, project and calibration commands; mute bare-key shortcuts; confirm close; hide rails 1 and 2. Arm it only when health is not red. Settle D13 against the code.

**Risk.** A lock that is slow to open during a real fault is worse than none: hold to unlock, no password.

### 4. Calibrate on a take is eight operator acts, a 12-line jargon report and a judgement modal, not one button

severity medium · confidence high · status new · effort M

**Evidence.** gui_builder.py:777-781 and TERRAIN_DEMO_FR.md:45-54: arm REC, pick a free slot, wait 15 s, stop, note slot/time/lighting on paper, play the slot, CALIBRATE, read the result, Save, then LIVE. Slots are bare digits (gui_builder.py:1248-1256). The no_close modal (gui.py:2795-2848) prints calibration.py:432-472 and empty_wall.py:64-79: exposure, gain, clip, gamma, "Person height: NOT measured" on every correct run, MOG2 varThreshold, CLAHE, noise sigma, the rung list; then "Save to project / Keep this session only" with no recommendation.

**Recommendation.** One CALIBRER press: it records its own empty take under a lighting name, calibrates, saves (the append-only history is the undo) and returns to LIVE. Lead the card with one verdict line and one action; details behind "more". A person seen on every rung means "wall not empty": abort and restore.

**Risk.** Saving automatically after a bad calibration needs a visible one-click return to the previous one.

### 5. Dial A's "losing the dancer? raise it" half does nothing at the shipped defaults

severity medium · confidence high · status new · effort S

**Evidence.** sensitivity_macro.py:48-52 interpolates confidence from the seed down to SENS_CONF_MIN = 0.15 (config.py:726); D33 made the seed 0.15 (config.py:54, 1045-1047). Run with the repo's function: dial 50, 75 and 100 all give confidence 0.15; varThreshold moves only above 75 and only when the calibrated value exceeds 8 (the night project's is 8, PLAN §C.12 D28). The tooltip (gui_builder.py:1011) still presents it as the lever against drops, the field's main pain.

**Recommendation.** Take both dials off the default Live panel (expert only). If a ghost lever is wanted, show a one-sided "plus strict" control. Do not let the GUI go below 0.15: the empty-wall check validates 0.8 x the live confidence only.

**Risk.** Operators who learned to raise Dial A lose a placebo: say so in the note.

### 6. The default surface is 97 controls, expert mode hides 10, and Live carries switches the note says to leave alone

severity medium · confidence high · status new · effort S

**Evidence.** Stub-DearPyGui run of build_ui: 107 widgets (75 in the main window, one of them hidden; 32 in Advanced), without 32 nudge buttons and 11 headers. gui.py:323-324: expert mode hides three groups, 10 widgets. Live has 21 (12 on 2026-10-06). TERRAIN_DEMO_FR.md:55-66 asks the operator to verify eight Dancer-ID values that equal config.py:320-333, 349, 398. Tooltips say "Leave it ON" at gui_builder.py:1139, 1148, 1159. Expert tools, Model/TensorRT and OSC target are open to everyone. TODO-me:9: "cleanup menu".

**Recommendation.** Wrap Rig's source, IDS, mask and rig-sheet groups, Live's dials and seven switches, View S/K/B/T, IMPORT/transport/ISSUE and the whole Advanced drawer in tagged groups listed in _expert_only_tags. Target: about 18 default controls (details). Add a build test that fails when the non-expert count exceeds a budget.

**Risk.** Thomas reaches hidden settings by remote command, not by widget: check each hidden control has a command in the remote API.

### 7. Everything is English, and the decisions now ride on transient jargon sentences a French manual cannot cover

severity medium · confidence high · status known accepted · effort M · prior id: D10 (manual in French, English labels quoted); DOC-2

**Evidence.** No French string in gui.py, gui_builder.py, ui/, runtime/, ops_monitor.py, empty_wall.py or web_monitor.py (grep for accents and French words: none). D10 (ROADMAP.md:234) chose a French manual quoting English labels; DOC-2 is unwritten. Since D29 the operator acts on sentences: TERRAIN_DEMO_FR.md:50 and 83-86 translate seven by hand. The three phase panels say YOLO 10 times, OSC 9, MOG2 4, CLAHE 4. ops_monitor.py:471-474 advises "raise max_persons", a setting with no widget; :95 says "see console".

**Recommendation.** Keep English in logs. Move the roughly 60 operator-facing strings (rail, three panels, alerts, readiness rows, verdict) into one table with French text in the operator's words (mur vide, point, fantôme) and no unit the operator cannot act on. Accents are Latin-1 like the µ and ° already displayed; check on the laptop.

**Risk.** This reopens D10. The reason that appeared since: D29-D33 moved the operator's decisions from labels to messages.

### 8. Leftovers of earlier designs on the operator's screen

severity low · confidence high · status new · effort S · prior id: partly known: phase chips and NEW_SHOW (CONTEXT §7, DOC-3)

**Evidence.** Rail "3 Live" opens a panel titled "6 - Go Live" (gui_builder.py:585, 966); the REMOTE tooltip says "allowed in 6 Live" (:436). Rig-sheet tooltip: "A new project (Start blank)" (:715). The Advanced tooltip lists "Input" (:634), now on Rig. Toast "re-Aim" (calibration_flows.py:412). Top-bar "+ New..." opens "Save Configuration" (gui.py:1060-1063), unlike the picker's "New". "Capture empty wall" duplicates Calibrate (:807-810). Also: Greyscale, Cap Input 20 FPS, the Show/Rehearsal radio, empty phase chips (:621), the ms stats bar. NEW_SHOW.md still documents six phases.

**Recommendation.** One pass: derive panel titles and tooltips from the PHASES table; take "Capture empty wall" off the operator panel; rename "+ New..." to "Save as..."; drop the re-Aim, Model panel and see-console wording; retire NEW_SHOW.md when the French manual lands. Add a test that fails on "Aim", "6 Live" and "Start blank".

**Risk.** Low. The smoke test stubs every callback, so it cannot catch a string that points nowhere.

## Paradigm-level change proposed

Today the GUI is a settings panel ordered by pipeline stage, with navigation tabs and a toast channel. I would make two things first-class in the runtime and let DearPyGui render them.

1. A mode: PRÉPARATION or SPECTACLE. The mode, not the widget, decides what a command may do: the remote POLICY table moves to the command seam and applies to every source. SPECTACLE is entered by one action that checks health, saves and locks. In SPECTACLE the screen is the preview, the health light, Max danseurs, Hold and Stabilité.

2. A health model: the set of active conditions, each with a level, a French sentence and a remedy, set and cleared by the monitor. The top-bar light, the strip, the phone page, `/status` and an optional OSC health message are views of it. Readiness stops being a button.

Calibration becomes the only setup verb: one press that records, measures, saves and returns to live, with one verdict line.

Cost: about 9 working days plus a laptop session. Health model and cleared events 2 d; policy at the seam and the lock 2 d; one-press Calibrate 2 d; regrouping behind expert tags 1 d; French string table 1 d; tests 1 d. No toolkit change.

Cheapest intermediate step (about one day, pipeline untouched): every alert kind in the strip with cleared events and a red strip; the Live switches, both dials and Advanced behind `_expert_only_tags`; a red "LECTURE - pas en direct" banner whenever RUN plays a recording.

## Questions for the owner

- During a show, does the technician see the WallDance window (second screen), or is TouchDesigner full-screen on the same laptop? That decides whether health must also reach the phone or TouchDesigner.
- How many lightings does one show have, and does the light change while dancers are on the wall? Is a freeze of the points for about 30 s acceptable for a re-calibration, or must it never interrupt?
- D13 says restart into STANDBY after a crash; the code comes back in RUN after two clicks (launcher Restart, picker). Which do you want, given that presence is the product?
- Would the technician rather have French labels and messages, or English labels with a French manual (D10)?
- On a new venue, does the technician create the project and draw the ROI alone, or do you always prepare the project remotely? That decides autolaunch and what stays on Rig.
- Does anyone use the single-key shortcuts (S K B T I C E P) on site?
- For a show, which icon is the operator meant to start: "WallDance DEV" or the usual one?

## Adversarial review

Four of the six claims hold at the cited lines: the three show-safety findings (playback in RUN, no health indicator, no show lock) and the Dial A finding. The verdict and the rethink are weakened.

- **Verdict:** the counts reproduce (107 / 75 / 32 / 10), but "97 exposed" is "97 reachable"; 21 to 39 controls are on screen at once. Several of the 23 steps are conditional, and only one chip is a configuration flag.
- **Rethink:** the direction is right and keeps DearPyGui and the command seam. Three parts of the mechanism do not survive the code:
  - The remote POLICY table cannot be reused for a local lock: its classes encode remote concerns.
  - The seam does not cover the ROI double-click and drag, the window close, or the GUI's own RUN/STANDBY flip.
  - Events are delivered on the publisher's thread, so continuous health off the frame loop needs a main-thread queue first.
  
  The day estimates are therefore unsupported. The target surface also conflicts with D23 and with the French note (exclusion paint, Image Size, IR belt).
- **Largest gap:** the lock removes the only documented light-change remedy and nothing replaces it; the restart behaviour of the mode is also undefined.

Also confirmed: the Live panel went from 12 to 21 controls (AST count at 45b35e4^ against HEAD). Not checked: behaviour on the rig, the laptop's current project files, and findings 4, 7 and 8. The repository is untouched (`git status` clean).

### Claims checked

- **weakened** — Verdict: a developer console under a three-tab rail; about 23 steps, 6 modals and a dozen judgement calls; the default surface exposes 97 controls (75 in the main window), expert mode hides 10; health is 8 s toasts and configuration chips; nothing protects a running show.  
  The safety facts stand. The headline numbers (97, 23, 6) are upper bounds presented as the norm, and 'chips show configuration' is true of one chip.  
  Evidence: (src/ = application/src/, docs/ = docs/). I re-ran (session scratch, not kept)/opux_count_widgets.py: 107 widgets, 75 in the main window, 32 in Advanced, 10 expert-hidden, so the raw counts reproduce. The same output shows what is on screen at once: top bar 2 + rail 4 + recordings bar 12 + one open tab (Rig 14, Calibrate 3, Live 21) = 21 to 39 controls; 20 of Advanced's 32 sit under collapsed headers behind a drawer button. 97 is 'reachable without expert mode', not 'exposed'. Steps: several are conditional. The launcher prompt appears only when BEHIND or DIVERGED (launcher/gui.py:191-195); the TensorRT prompt only when the engine is missing (src/runtime/model_controller.py:240-251); the rig sheet only 'si quelque chose a changé' (docs/TERRAIN_DEMO_FR.md:43); exclusion paint only after a failed wall check (:50-52); the config version is prepared by Thomas (:41-42); live Calibrate without a take is offered (:49). The note itself has 6 numbered steps. Chips: only the OSC chip is a flag (src/gui.py:1771-1774); the camera badge and reconnect label (src/gui.py:2068-2076), the GPU temperature colour (src/gui.py:1994-1999), the FPS colour and the engine and CPU-fallback badges show live state. Verified as stated: boot in RUN (src/app.py:521, src/gui.py:316), slot click (src/runtime/recording_controller.py:426-428), toasts (src/ui/adapter.py:34, 345-348), stalls printed only (src/runtime/main_loop.py:1244-1246).
- **holds** — Finding 1: the light-change remedy, and any stray click on a slot digit, replaces the live camera by a looped recording in RUN while OSC keeps flowing; nothing returns to LIVE.  
  Two precisions. This is a consequence of D29's flow (docs/PLAN_25M_2026-10.md:376; src/gui_builder.py:756-757), so it should be tagged related to D29, not plain 'new'. The recordings bar does show a blue 'SLOT n' and the transport (src/gui.py:1160-1162, 1207-1212), so playback is signalled, just not as an alarm. The take is an empty wall, so the ids vanish after Hold and TouchDesigner drops its video (D5). Not run on the rig.  
  Evidence: (src/ = application/src/). src/runtime/recording_controller.py:385-430 has no system_state check and no confirmation; :509 stops IDS acquisition. src/runtime/main_loop.py:636-640 reads playback frames and :813-816 gates processing on RUN only. src/core/pipeline.py:1027: osc_on depends on osc_enabled only. src/core/video_recorder.py:563-570 loops at end of file. src/core/ops_monitor.py:412-413 and 450 mute the fps and no-detection alerts during playback. src/core/pipeline.py:1111-1114: a take gets no D34 hold. src/runtime/calibration_flows.py only reads is_playing (237, 252, 270, 294) and never goes back to live. docs/TERRAIN_DEMO_FR.md:84 says 'le spectacle continue', and :45-54 never tells the operator to press LIVE. docs/CHECK_TEST.md:41-42 confirms OSC from a recording in RUN.
- **holds** — Finding 2: no single health indicator; every alert but light_changed is an 8 s single-slot toast; the OSC chip is a flag; a remote SetState leaves the RUN badge stale; /status carries no alerts; stalls reach only the console.  
  The title overreaches on 'the chips' (one of about six is configuration). 'See console' (src/core/ops_monitor.py:95) is stale wording for the on-demand check, which does render rows on screen (src/app.py:2327-2333); it is literal only for the Go-Live thread path (src/app.py:2299-2312).  
  Evidence: (src/ = application/src/). src/ui/adapter.py:34 (STRIP_ALERTS = light_changed only) and :345-348 (toast, 8.0 s). src/gui.py:2929-2930 deletes the previous toast. src/core/config.py:1124 with src/core/ops_monitor.py:392-397: 120 s cooldown per kind, so 8 s in 120 s. src/core/ops_monitor.py:386 and 507-522: only light_changed is ever cleared. src/gui.py:1771-1774: OSC chip = osc_enabled. src/ui/adapter.py:413 ignores StateChanged, and src/gui.py:858-873 sets the badge only from the local click. src/app.py:1020-1048: no alert field in /status. src/runtime/main_loop.py:1244-1246 and 693: stall and silent reconnect are print only.
- **holds** — Finding 3: no show lock; the app boots in RUN, D13's restart into STANDBY is not implemented, and the local surface refuses almost nothing (project combo, safe defaults, CALIBRATE, close, bare E key, readiness on the frame loop).  
  The finding stands. Its list misses the preview double-click path into ROI editing (first item under missed), and its fix as worded (POLICY applied at submit) does not fit the table or cover that path; see the rethink check.  
  Evidence: (src/ = application/src/). src/app.py:521 and src/gui.py:316 start in RUN. docs/ROADMAP.md:235 (D13) against src/runtime/main_loop.py:490-493: the crash marker only produces a toast, and a grep finds no code that sets STANDBY from it. State checks exist only at src/app.py:993, 2343, 2442, 2572. Unconfirmed in RUN: src/gui.py:1067-1069 (project), src/gui.py:1040-1048 (safe defaults), src/runtime/calibration_flows.py:227-241 (checks model and camera only). No set_exit_callback anywhere in the GUI code. src/ui/adapter.py:278-279 with src/app.py:1166-1173: bare E flips enhancement with a console print. src/gui.py:537-541 with src/app.py:2314-2318: the docstring itself says the check runs synchronously on the main loop, about 0.3 s. POLICY has 93 entries (counted: 62 control, 15 never, 11 heavy, 5 safe).
- **holds** — Finding 5: Dial A's 'losing the dancer? raise it' half does nothing at the shipped defaults.  
  Scope: a stored project keeps its seed. On dev37 the newest mirrored configs of default, mur30m-0710 and mur25m-ceinture-0610 (mur25m-ceinture-0610_20261007_103331.json) store 0.25, where the upper half is live (0.25 down to 0.15); only mur25m-night-common stores 0.15. The laptop's current files were not checked (its manifest lists newer saves). The note's 'Ajuster sur place' table (docs/TERRAIN_DEMO_FR.md:76-86) never sends the operator to Dial A, so the operator impact is smaller than the tooltip suggests.  
  Evidence: (src/ = application/src/). src/core/sensitivity_macro.py:48-52; src/core/config.py:54, 726, 1045-1047. I ran macro_to_settings: seed 0.15 gives confidence 0.15 at dial 50, 75 and 100; varThreshold moves only above 75 and only when the anchor exceeds 8 (16 becomes 8 at 100). src/runtime/calibration_flows.py:420: Calibrate re-anchors varThreshold only, never the confidence seed. tmp_analysis/plan25m/apply_d33.py (docstring): a migrated project gets seed 0.15. Tooltip at src/gui_builder.py:1011.
- **weakened** — Rethink: make a mode (PRÉPARATION / SPECTACLE) and a health model first-class in the runtime; move the remote POLICY table to the command seam for every source; one-press Calibrate; French strings; about 9 working days, or one day for the cheapest step; no toolkit change.  
  The direction is sound and respects the keep-as-is list (DearPyGui, the runtime/api seam). The mechanism ('move the POLICY table', 'policy at the seam') and the costs (2 days for the lock, 1 day for the cheapest step) are not supported by the code; the estimates are unverified guesses.  
  Evidence: (src/ = application/src/). 1) POLICY cannot simply move. Its 'control' class (62 of 93) mixes what SPECTACLE must keep (SetMaxDancers, SetStability, SetCoastSeconds, src/services/remote_api.py:66-67) with what it must refuse (SelectSlot, PlaySlotRecording, CapturePlate, StartCalibration, SelectSource, :62, 70, 79). Its 'never' class is dialogs nobody answers remotely (ShowQr, EditMask, SaveConfigAs, :90-95). A new classification is needed. 2) Local changes bypass the seam: the preview double-click and ROI drag (src/ui/adapter.py:90; src/ui/roi_mask_editor.py:689-700; polled at src/runtime/main_loop.py:604-606) and the window close. The GUI also flips its RUN/STANDBY badge before submitting (src/gui.py:858-886) and ignores StateChanged (src/ui/adapter.py:413), so a refused local command leaves the widget wrong. 3) Health views: EventBus.publish runs on the caller's thread (src/runtime/api.py:1110-1116), and the Go-Live readiness thread already publishes a Toast that rebuilds a DPG window off the main thread (src/app.py:2231-2232; src/gui.py:2929-2951). Continuous readiness off the frame loop needs a main-thread event queue first; it is not costed. 4) The lock refuses playback and calibration, but nothing replaces the light-change remedy while dancers are on the wall: D29 retired lighting profiles from the operator flow (src/gui_builder.py:756-769) and the target sends profiles to remote only. 5) The target surface contradicts current instructions: exclusion paint to expert against D23 'operator paints exclusion now' (docs/ROADMAP.md:228; docs/TERRAIN_DEMO_FR.md:50-52); Image Size remote only against TERRAIN_DEMO_FR.md:70-71; IR belt is a per-show operator choice (:63). 6) French strings reopen D10 (docs/ROADMAP.md:234), which the report declares.

### Missed or out of proportion

- A double-click on the preview toggles ROI edit mode from any tab and in any state, and switches the ROI on if it was off (global handler at application/src/ui/adapter.py:90; application/src/ui/roi_mask_editor.py:689-700 and 661-665). A drag then rewrites the ROI in RUN (main_loop.py:604-606) without passing RuntimeAPI.submit. The report's list of unguarded acts omits it, and a lock placed at the command seam would not stop it.
- The mid-show light change is left without a local remedy. Once playback is refused under the lock, the only documented path (TERRAIN_DEMO_FR.md:54, 84) is gone, and the plan costs nothing to replace it. The missing piece is a stored calibration result per lighting (snapshot, exposure/gain, gamma/CLAHE, varThreshold) applied in one act with no replay. The report leaves it as an owner question and sends profiles to remote only. The blind scan says profiles as built share the snapshot (motion-calibration#9; I did not re-check that).
- Mode and restart are not reconciled. Neither the lock nor SPECTACLE says what happens after a crash or power cut. D13 (STANDBY), the no_close picker, the TensorRT prompt and a mode to re-enter add human gates, against D5 (presence is the product). The blind scan already counts up to five such gates (config-ops#1, gui-operator#6). The rethink needs a persisted mode with a stated resume rule.
- Live changes exist only in memory: a grep finds no autosave and no unsaved mark in gui.py, app.py or runtime/config_manager.py. Hold, Stability and Max dancers tuned during a show, and the remote-control tick, are lost at a restart. tmp_analysis/plan25m/apply_d33.py warns that an app save overwrites a remote migration with the old in-memory values. The report mentions this only as journey step 22, and SPECTACLE saves only at entry.
- Proportion: TouchDesigner runs on the same laptop (D17), so a health light inside the WallDance window may not be visible during a show. There is no OSC message for source or health (only /walldance/meta/latency_ms, application/src/core/osc_output.py:165-171), so TouchDesigner cannot tell a take from the camera. The report makes the OSC health message optional and should say which answer to its own first question the 9-day plan assumes. Also, the alerts strip is one text line joining full sentences (gui.py:574-580), so 'every kind in the strip' needs short labels first.
