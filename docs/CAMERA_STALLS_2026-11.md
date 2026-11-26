# U2 · Why the IDS camera stalls for 1.67 s — verdict (26/11)

Camera IDS U3-34ExXCP-M (Sony IMX664, serial 4110042130, firmware 3.80.26143) on the show laptop tango-lenovo,
controller XHC1 port 3, link 5 Gb/s (USB 3, `DeviceLinkSpeed` 500 MB/s), IDS peak 2.19 / U3V driver 1.8.1.0.
Hub row walldance #t-043 (L2), Thomas's ask of 26/11 15:45. Tool: `extra/ids_stall_ab.py` (branch `r2-stall`).
Records: `gates/stall/runs/<job>/`, table `gates/stall/analysis/runs_table.md`, gate `gates/stall/run_gate.sh`.

## In plain words

- **What a stall is.** Each time, the USB transfer of one picture breaks halfway; the camera's driver then repairs
  the connection, and that repair always takes 1.65 s, during which the camera sends nothing. WallDance does not
  make it longer, and Windows does not even notice it (no USB reset, no event in any log).
- **What makes it likelier: how fast the camera pushes each picture down the cable.** The camera reads its sensor
  line by line and sends each line at once; a wider crop, or a higher « throughput limit » in the camera, means more
  data per millisecond during each picture. Raising that limit from 162 (the default, what the show uses) to
  300 MB/s gave 10 stalls in 5 minutes against 1 (same quarter-hour, alternated). The old records say the same for
  the crop: on 06/10 the narrow 1488-wide crop never stalled in 20 minutes while the wide crops around it stalled
  39 times. The idle time between pictures does not matter (12, 20, 34 pictures a second: same chance per picture),
  so it is not Windows putting the USB link to sleep between pictures; the laptop's load from another program
  did not matter either.
- **But the level is set by the place, and it drifts.** At the show's own settings the camera stalled **0.11 times a
  minute today on the desk (2 in 19 minutes), against 3 a minute on the façade on 08/10** — about 30 times more,
  same camera, same laptop, same settings. Even on the desk the rate moved from 2 a minute to zero within the hour with nothing visible
  changing. Something physical around the link sets the level: the cable and its connectors (on 08/10 the camera
  dropped off the bus 35 times in a row during set-up), the port it is on, what runs beside it on the show night, or
  the temperature. Those cannot be tested from a distance.
- **What to do.** (1) On the rig (window 2, 4/12): three swaps, 20 minutes each — the show cable against a short
  one, the camera's port against the empty controller's port, and the camera's throughput limit 162 against 124
  (§4, §5). (2) In the build meanwhile: keep the crop; lower the camera's throughput limit to 124 MB/s at runtime
  (no pixel lost, 20 fps kept; proposed to the orchestrator, not proven on the façade yet); keep U1 (a stall no
  longer costs a dancer her slot) and the stall rate on the health line; tell the technician to reseat or swap the
  cable when the stall rate turns orange. The three changes, file by file, are at the end (§8).

## 1. What a stall is (strand A: the app's code and the old records)

- **One event, always the same.** 244 stalls in the 13 camlogs of 08/10 (59 min; the brief says 243: one more
  after de-duplication) and 1,093 of 1,093 logged stalls with counters since 06/10 carry one signature: one
  frame arrives **incomplete** (its USB transfer broke mid-frame), the IDS driver runs one **pipe error recovery**
  (`PipeErrorRecoveryCount +1`, `PipeTotalErrorCount +1`, `DroppedFrameCount +2`), the camera's frame id steps
  **+2** (one frame lost) and the next frame comes **1.65 s later by the camera's own clock** (08/10: 1,636–1,693 ms,
  median 1,664.5). Kind `lost_in_transfer` for all; never `camera_silent`. Every incomplete frame ends in a stall
  (08/10 mur2 7: 92 incomplete = 92 stalls = 92 recoveries), and no stall comes without one.
- **The app does not make it longer.** Host gap minus camera gap: median +9 ms, p95 +29, max +39 (08/10, 244
  stalls); +7 / +29 / +46 over 1,093 logged stalls; today −6 ms median (−27…+18) over 26. The 1.65 s is the camera
  and the driver: the app's `WaitForFinishedBuffer` timeout (249 ms at 20 fps) only loops, and its silent-reopen
  watchdog fires at 2.5 s, above the stall. So no fix of the app's waiting shortens a stall. One flaw nearby
  (not a lengthening): the incomplete frame itself is published as a normal frame (to the detector and to the
  recording), one torn image per stall — fix proposed to the orchestrator (§8.2).
- **Random in time, not periodic.** Intervals on 08/10: median 9.1 s, minimum 1.7 s, coefficient of variation
  1.02, lag-1 correlation 0.15 (a Poisson process: "one every 15 s" is an average, not a period); no warm-up
  (3.75/min in a take's first minute, 4.22 after).
- **The crop's width matters, the crop's pixel count does not explain it.** The app keeps about 2.3 Mpx, so a
  wider sensor crop has fewer, longer lines. On 06/10 the app **alternated** crops in one session:
  1488 × 1528 had **0 stalls in 19.8 min** (three blocks) between landscape blocks with **39 stalls in 53.2 min**
  (1776 × 1300 and 1824 × 1264) — an ABAB in the records (`analysis/old_sequence.txt`; exact test p < 0.0001). On 08/10 the portrait
  crops (1200 and 1248 wide) had 0 in 5.0 min while 1776 ran at 3.1/min, 1824 at 5.5, 1968 at 7.9 (not alternated:
  the portrait minutes were each session's first ones, so this agrees with 06/10 but is not a comparison by itself).
- **The day matters more than anything.** The same 1824 × 1260 crop: 5.55/min on the 08/10 façade, 1.44 on
  06/10, 0.55 on 18/11; 1776 wide: 3.1 (08/10), 0.34 (06/10), **0.11 today** on the desk (§2). A factor of 10 to 30
  at identical settings: something physical around the link (cable, connector, routing, interference, temperature,
  port) sets the level; the camera's settings scale it.
- **08/10 also shows a bad connection at setup**: 35 « removed unexpectedly, missing on the bus » for the camera in
  35 s at 13:59, single ones at 14:34 and 18:04, and an 8 s camera start at 13:34 (Kernel-PnP logs). None since the
  camera was plugged in today (12:56).
- **No camera temperature exists to log**: the U3-34ExXCP exposes no temperature node (818 camera nodes read);
  the old logs hold 12 GPU readings in an evening, too few to correlate.

## 2. The bare camera today (strand B)

The tool opens the camera exactly as the app does (UserSet1, the app's crop model, NewestOnly, 16 buffers), then
sets ONE thing at runtime, alternates the conditions (ABCABC, 2.6 min a run, so 5.2 min and two alternations a
side), and records per run what the camera reads back, the stalls with the camera's clock and the stream counters,
and the laptop's load (a 1 s sampler: CPU, DPC, GPU, the busiest processes). Exposure 12 ms, gain 27.5 dB, 20 fps
unless stated. Every one of today's 26 stalls has the 08/10 signature: incomplete frame, one pipe recovery,
frame id +2, host gap 1,648–1,705 ms (median 1,662), camera clock 1,647–1,692 ms (median 1,670).

**What the camera does with a frame.** It reads the sensor line by line at a line time set by its throughput
limit (`DeviceLinkThroughputLimit`, 162 MB/s by default: about 22.8 µs a line whatever the crop's width; at
300 MB/s, 13.6 µs) and sends each line as it comes. So a frame goes out in a burst — 29.6 ms for 1,296 lines at
162 MB/s — at a data rate set by **crop width × bytes per pixel ÷ line time**, then the link is idle until the next
frame. The table gives both for every run (read back from the camera: `AcquisitionFrameRateMax` and `PayloadSize`):

| condition (today) | crop | limit MB/s | fps | data rate in the burst MB/s | idle ms / frame | stalls / minutes | per 1,000 frames |
|---|---|---|---|---|---|---|---|
| narrow, 06/10's no-stall crop | 1488 × 1528 / 1296 | 162 | 20 | 82 | 16–20 | 0 / 10.4 | 0 |
| **08/10 settings** | 1776 × 1296 + 456 + 120 | 162 | 20 | 97 | 20 | **2 / 18.7** | 0.09 |
| wide | 2688 × 856 | 162 | 20 | 141 | 30 | 2 / 5.2 | 0.32 |
| raised limit | 1776 × 1296 | 300 | 20 | 164 | 32 | 15 / 18.2 | 0.70 |
| raised limit, 12 / 33.8 fps | 1776 × 1296 | 300 | 12 / 33.8 | 164 | 66 / 12 | 0 / 5.2 · 7 / 5.2 | 0 · 0.69 |
| raised limit, narrow / 12-bit | 1488 × 1528 · Mono12g24 | 300 | 20 | 139 · 167 | 30 · 29 | 0 / 5.2 · 0 / 5.2 | 0 · 0 |

(pooled over the day; the per-run table is `analysis/runs_table.md`.)

**The comparisons (ABAB, ≥ 5 min a side; the load of every run is in the gate's output).** Load between the A and
the B sides: **changed in window 1 only** (its first A run had the GPU idle, its five others ran beside the video
generator at 90–100 % GPU: A 51 % against B 95–100 % on average); windows 3 to 6 ran on a quiet laptop, both sides
alike (GPU 0–1 %, CPU 3–6 %, DPC < 0.2 %, the same background processes: Task Manager, RustDesk, Defender).

1. **Reproduction (gate clause 1).** At the 08/10 settings the stalls came back, with their exact signature, but
   rarely: **2 stalls in 18.7 min = 0.11 per minute** (8 runs: 1 in the 30 s smoke run at 15:54, 0 / 5.2 min in
   window 1, 0 / 2.6 in window 2, 1 / 5.2 in window 3, 0 / 5.2 in window 6), against 2.5–3.1 per minute on 08/10
   at the same crop.
   What differs from 08/10: the camera on a desk with today's cable and port, not on the façade rig; no WallDance
   app, no TouchDesigner, no GPU pipeline; indoors.
2. **Throughput limit 162 → 300 MB/s (window 3, quiet laptop): 1 stall in 5.2 min against 10 in 5.2 min, p = 0.012**
   (runs A 0, B 4, A 1, B 6). Raising the data rate of the burst multiplies the stalls. (Window 2 tried the same
   under the video generator's load but stopped after one run a side, on a bug since fixed: not a comparison; its
   runs are in the table.)
3. **Frame rate at 300 MB/s (window 4, quiet): 12 / 20 / 33.8 fps → 0 / 4 / 7 stalls in 5.2 min each**, i.e. the
   **same chance per frame** at 20 and 33.8 fps (0.65 and 0.69 per 1,000) and none at 12 (2.5 expected per frame:
   p ≈ 0.08). The idle time between frames went from 12 to 66 ms: more idle link time did **not** bring more
   stalls, so a link falling asleep between frames (Windows' USB 3 Link Power Management, « Moderate » on this
   laptop) is not what drives them; it is something that happens **during** a frame's transfer. (« 40 fps » asked
   ran at 33.8: the rate was set before the limit was raised; the tool now asks again.)
4. **Crop width at 162 MB/s (window 3): 2688 × 856 gave 2 in 5.2 min against 1** — the right direction (141 against
   97 MB/s), not significant. **Window 1 (1488 wide against 1776, at 162): 0 against 0** in 5.2 min a side: today's
   level at 162 MB/s is too low to separate them; the records do (06/10: 0 in 19.8 min against 39 in 53.2, §1).
5. **Window 5, 17:48–18:04 (quiet): nothing stalled at all**, not even 300 MB/s, which gave 10 in 5.2 min at 17:00
   and 4 at 17:15: the narrow crop and the 12-bit format at 300 MB/s (0 against 0) could not be compared. **The
   level drifts by itself within the hour** (300 MB/s at 20 fps: 1.9, 0.8 then 0 per minute in three windows of
   identical load; 1 / 2.6, 10 / 5.2, 4 / 5.2, 0 / 5.2 min are not one steady rate: exact test p = 0.004): the alternation keeps each
   comparison fair, but the absolute rate is set by something that varies over tens of minutes and that no load
   figure or Windows log shows.

6. **Throughput limit 124 and 230 against 162 at the 08/10 crop (window 6, 18:18–18:34, quiet): 0 / 0 / 0 in
   5.2 min each.** The camera takes 124 MB/s at runtime and still runs 20.0 fps (its ceiling falls from 33.8 to
   26.1 fps, the burst from 97 to 75 MB/s); 230 MB/s (a 136 MB/s burst) did not stall either at that hour.
   February's « 125 MB/s → a stall every 5 s » would have meant about 60 here: it does not repeat at this crop.

7. **No seventh window.** The host-side window queued as `w7-host` (64 host buffers and a 500 ms wait timeout,
   against the app's 16 and 249 ms) was dropped: the camera's own clock shows it pausing with all its buffers
   queued, so a host setting can neither prevent nor shorten a stall (§6). The window planned in its place did not
   run either: from 18:40 the laptop was taken by another session's timing runs. The commands are in §6.

## 3. The laptop around the stalls (strand C, read only)

- **Windows sees nothing.** In every window, ±2 min: no System event from any provider, no Application error or
  warning, nothing in Kernel-PnP/Configuration, nothing in the enabled USB/PnP/Power/Thermal logs
  (`runs/<job>/events.txt`) — while the camera stalled 26 times. A stall is not a USB reset, a re-enumeration, a
  power-state change or a hardware error report: it lives inside the IDS driver's stream recovery.
- **Settings read (unchanged at the end, §7):** power plan « Utilisation normale »; USB selective suspend off (AC and
  DC); **USB 3 Link Power Management = Moderate (AC and DC)**; hub selective-suspend timeout 50 ms; « IOC on all TDs »
  on (AC) / off (DC); PCIe ASPM off; the camera's controller XHC1 and its root hub `IdleInWorkingState = 1`; on
  mains (AC) during every window and, per Kernel-Power 105, during the whole 08/10 evening (last switch 19:03:14).
- **The camera shares XHC1** (port 3) with an internal Genesys hub (port 2 USB 2 / port 4 USB 3) carrying the
  Bluetooth adapter and a keyboard controller; **XHC0 has nothing plugged** (USB 3.1 controller, empty root hub);
  XHC2 is USB 2 only (webcam, keyboard). Two xHCI errors on XHC1 in April 2026 (before the camera) only.
- **What ran (per run in the table):** 15:59–~16:40 a generative-video tool (`python wgp.py` under Pinokio, GPU
  90–100 %, not ours) during windows 1 (runs 1–5) and 2; it had ended by window 3; from window 3 on the machine was
  quiet (GPU 0–1 %, Task Manager open: someone at the laptop). No TouchDesigner, no WallDance app, ever. Our tool
  took ~75–90 % of one core; total CPU 4–12 %, DPC 0.15–0.7 %.

## 4. Verdict: the suspects, in order, and the one test each needs

The cause is **not found to the last link**; what is established, and what is left:

**Established.** A stall is a USB transfer of one frame that breaks, then the IDS driver's fixed 1.65 s repair.
The chance that a frame's transfer breaks **rises with how fast the camera pushes data during the frame**
(throughput limit 300 against 162 MB/s: ×10 in the same quarter-hour; wider crops: ×4–×∞ in the old records,
right direction today); it does **not** depend on the idle time between frames (frame-rate test), on the laptop's
GPU or CPU load from another program (0 stalls at 162 MB/s with a video generator holding the GPU at 100 %), on
anything Windows logs, or on the app's waiting (camera clock). Its **level** varies 10–30× between places and
days, and even within an hour on the desk, at identical settings and load.

**Suspects, most likely first** — each with the one test a person at the laptop must make. A test is 4 runs of
5 minutes, alternating the two set-ups (A B A B), the camera tool running the stress condition so that stalls come
fast enough to count (command in §6, ~1 stall a minute today at 300 MB/s on a bad quarter-hour, 0 on a good one —
so first check that set-up A stalls at all; if not, use the narrowest A/B the time allows and say so).

1. **The physical link on the show rig: cable, extension or hub, connectors, routing.** Why first: a factor 10–30
   between places at identical settings; the camera dropped off the bus 35 times in 35 s while the rig was being set
   up on 08/10 (13:59); a marginal link makes each burst likelier to break, and faster bursts break more — which is
   exactly what the throughput test shows. **Test:** the cable exactly as on the façade (same length, same
   extension or hub if any, same route next to the IR floods' power supplies) against a short (≤ 1 m) certified
   USB 3 cable plugged straight into the laptop, at the show crop and settings, on the rig.
2. **The port and what shares its controller.** The camera sits on XHC1 port 3, with the internal hub that carries
   the Bluetooth radio and a keyboard controller; XHC0 has nothing on it. **Test:** find which socket is XHC0 (plug
   the camera into each socket and read its location — `laptop/sysread.py` prints `ACPI(XHC0)` or `ACPI(XHC1)` in
   the camera's LocationPaths), then XHC1 port 3 against the XHC0 socket, same cable. Bluetooth devices
   in use during the show (headphones, a remote) should be noted in each run.
3. **The show's own traffic on the laptop: the WallDance app's GPU path and TouchDesigner.** In February (doc
   `docs/archives/IDS_CAMERA_STALL_INVESTIGATION.md`, sequential tests, not ABAB) more GPU↔memory traffic gave more
   stalls; today the bare tool never ran beside the app's pipeline (forbidden: no GPU job), and the video generator
   that ran beside it showed nothing. **Test** (no hands needed, a window with the laptop free): the camera tool with
   its GPU read path (`gpu=1`, the app's pinned upload) against the plain read, then with TouchDesigner playing the
   show file against without — §6.
4. **Temperature / the drift within the hour.** Unexplained: 300 MB/s stalled 10 times in 5 min at 17:00 and not
   once at 17:50, load unchanged. The camera has no thermometer. **Test:** on the rig, log the camera's housing
   temperature (a probe or an IR thermometer on the housing at each run's start) and the air temperature beside the
   stall counts of the four runs above; a cold façade night against a warm room is the contrast that matters.
5. **Windows' USB 3 Link Power Management (« Moderate », AC and DC) — unlikely** after the frame-rate test (more
   idle time did not bring more stalls). Changing it is a Windows setting: Thomas's decision, not done. **Test:**
   Settings → Power → « USB 3 Link Power Management » = Off on mains, A/B as above, then back to Moderate (or
   `powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 d4e98f31-5ffe-4ce1-be31-1b38b384c009 0`
   + `powercfg /setactive SCHEME_CURRENT`; back with value `2`).

## 5. What the build should do meanwhile

- **Keep the crop the wall needs; lower the camera's throughput limit instead (proposed, app change, not made
  here).** At the 08/10 crop, 124 MB/s instead of the default 162 slows the sensor's readout so the frame's burst
  carries 75 MB/s instead of 97 — less than the narrow 1488-wide crop at 162 MB/s (82 MB/s), which never stalled
  (0 in 19.8 min alternated on 06/10, 0 in 10.4 min today). Read back today: the camera accepts it at runtime, still
  runs 20.0 fps (ceiling 26.1 fps instead of 33.8), and gave 0 stalls in 5.2 min (window 6) — February's « 125 MB/s
  → a stall every 5 s » was measured on the full 2688 × 1528 sensor, where 125 MB/s cannot carry 20 fps; it does not
  repeat here. **Cost:** no pixel and no field of view lost; each frame is read out in 38 ms instead of 30 ms (a
  little more rolling-shutter lean on a fast movement), and the frame rate can no longer go above 26 fps (the show
  runs at 20). **Not proven:** that it lowers the stalls on the façade — today's level was too low to show a
  reduction (0 against 0). It needs the rig: window 2 (4/12), ABAB 162 against 124 at the show settings with the
  real pipeline, 4 × 5 min. File by file: §8.1.
- **The narrow crop is a fallback, not the plan.** 1488 wide on the sensor = 1488 px of image height after the
  rotation, against 1776: **16 % of the wall's height lost**, or, with the camera ~19 % farther from the wall (or a
  5 mm lens for the 6 mm) to keep it all, **the dancer 16 % smaller in camera pixels** (≈ 110 → ≈ 92 px tall on
  08/10's geometry; the 1920 × 1056 network input would scale her back up to the same size, with less detail in
  it). The 08/10 wall's ROI was 965 × 1771: it does not fit in 1488 px as it stood.
- **What the build already has, and should keep:** U1 (a stall no longer eats a slot's hold: the hold runs on the
  frame clock) and H2/U3 (the stall rate on the health line, orange at 2 a minute, red at 6). With those, a stall
  costs 1.67 s of blindness, not a lost dancer. The technician's sheet should say: **stall rate orange or red →
  reseat the camera cable at both ends, use the short cable straight into the laptop, keep it away from the IR
  floods' power supplies.** (§8.3)
- **The torn frame** (one incomplete image published per stall): a small fix in the app's acquisition loop,
  proposed to the orchestrator (not made here; §8.2).

## 6. Not tested today, and how to run it later

All from mgr-ran; `ab.sh` takes the laptop lock itself (shared for its checks, exclusive ≤ 18 min for the camera),
waits while a WallDance app runs, and pulls the records into `gates/stall/runs/<job>/`; `run_gate.sh` re-reads
them. `L=/data/WallDance/tmp_analysis/r2-1126/gates/stall/laptop`, and every command below ends with
`--fps 20 --exposure-us 12000 --gain-db 27.5 --roi 1776x1296+456+120` (the 08/10 settings).

| not done | why | command |
|---|---|---|
| the app's GPU read path beside the camera (pinned upload per frame) | no GPU job of ours today (the laptop was someone's) | `$L/ab.sh g1-gpu 17 --minutes 2.6 --reps 2 --throughput-mbps 300 --cond T300: --cond T300GPU:gpu=1 …` |
| the show's load: TouchDesigner playing the show file beside the camera | nothing may start TouchDesigner but its user | someone starts TD with the show file; then the same `g1-gpu` command as job `g2-td` (the sampler records TD) |
| a quiet laptop against the video generator's load | not a knob we control; it ended by itself at ~16:40 | done in effect: windows 1–2 loaded (GPU 90–100 %), 3–5 quiet (0–1 %); no change visible at 162 MB/s (0 / 10.4 min loaded, 1 / 5.2 quiet) |
| frame rate 15 / 30 at the 08/10 settings, exposure 9 against 12 ms, buffers 16 against 64, wait timeout 249 against 500 ms | at 162 MB/s today's level (0.11/min) gives about one stall per 5-min side: an A/B cannot separate anything; the frame rate ran at 300 MB/s instead (§2.3); buffers and timeout act on the host, while the camera's own clock shows the camera pausing with all 16 buffers queued (every one of the 1,093 stalls logged since 06/10 and the 26 of today) — they cannot shorten or prevent it | `$L/ab.sh h1-host 17 --minutes 2.6 --reps 2 --throughput-mbps 300 --cond T300: --cond B64:buf=64 --cond TMO500:tmo=500 …` · `$L/ab.sh e1-exp 17 --minutes 2.6 --reps 2 --throughput-mbps 300 --cond T300: --cond E9:exp=9000 --cond F15:fps=15 …` |
| 8-bit pixels | the camera offers only Mono10g40IDS and Mono12g24IDS (Mono8 « not available » on this model) | 10 against 12-bit ran (§2.5, 0 against 0) |
| the camera's own error events | needs EventNotification switched on in the camera, a runtime setting outside today's list | — (ask first) |
| cable, port, temperature, LPM | hands on the rig, or a Windows setting | §4: one job per set-up, swapped by hand between jobs, e.g. `$L/ab.sh p1-showcable-1 6 --minutes 5 --reps 1 --cond T300:tl=300 …`, then `p1-shortcable-1`, `p1-showcable-2`, `p1-shortcable-2`; on the rig also at 162 MB/s (`--cond A0810:`) if the rig stalls on its own |
| throughput limit 124 against 162 where the camera stalls on its own (the rig) | today's desk level at 162 MB/s was too low to show a reduction (0 against 0, §2.6) | `$L/ab.sh r1-tl124 17 --minutes 4 --reps 2 --cond A0810: --cond T124:tl=124 …` (8 min a side, two alternations; then the app itself, §8.1) |
| the camera's own final read-back (gate clause 3) | the camera was not to be opened again on the evening of 26/11 | `$L/final_read.sh` when the laptop is free (exclusive lock, no app running) |

## 7. The laptop: what was set, what is left (gate clause 3)

**Set at runtime, through the IDS peak API, by our runs only while the camera was ours (no WallDance app running,
checked before every window and every 30 s):** at each open, what the app's own open does (UserSet1 *loaded*, the
app's crop, 20 fps, exposure 12 ms, gain 27.5 dB, free-running, NewestOnly, 16 host buffers); then one of: an exact
crop (1776 × 1296 + 456 + 120, 1488 × 1528, 1488 × 1296, 2688 × 856, 2688 × 1536), Mono12g24IDS, 12 / 20 / 33.8 fps,
`DeviceLinkThroughputLimit` 124 / 230 / 300 MB/s (put back to 162 at the end of every run: `restored` in each run
record). Never a user set saved, never `UserSetDefault`, no firmware, no event switched on, no vendor GUI.
Read only: the node maps (once, `runs/w1-shape/nodes.json`), the Windows event logs, powercfg, the registry, the
device tree. Nothing written outside `C:\WallDance\_r2-1126\stall\`.

**The final read-back, 26/11 18:47:54** (`laptop/final_batch.sh`: one call under the shared laptop lock, 19 s on
the laptop, the camera not opened): the power and USB settings — 9 sections: the power plan and its overlay, the
USB, PCIe, sleep and processor groups of `powercfg` with their mains and battery values, the USB stack's registry
values, the power flags of the camera, its root hub and its controller — are **identical** to the first complete
read of 16:01 (`reads/sys_base.json`, same SHA-256 `d75fb6fe9d48ca1d…`), and to the very first read of 15:46
(`reads/sys_first.json`) on the 7 sections it holds (its USB group and its WMI power-flag reads came back empty;
the 16:01 read filled them, and nothing ran between the two but the 30 s smoke run, which only talks to the camera).
On mains, battery 100 %. No System, Kernel-PnP or hardware-error event since 18:30. No process of ours runs (the
only two `python.exe` at that moment were the read itself); no WallDance app and no TouchDesigner open then. Our
folder `C:\WallDance\_r2-1126\stall\` holds 56 files, 0.94 MB (the tool's copy, the job records, already pulled to
mgr-ran, and the 1 s load samples, about 110 KB a window): nothing bulky, nothing deleted.

**The camera itself was not read back (a limit):** the rule of the evening forbids opening it again. What its runs
recorded instead: the last run (18:31) read back the 08/10 crop 1776 × 1296 + 456 + 120, Mono10g40IDS, 20 fps,
exposure 12 ms, gain 27.5 dB, and put the throughput limit back to 162 MB/s (read back after the write); 19 of 19
runs that changed something beyond the app's own open put it back. Against the first read (15:46), the crop,
exposure and gain differ: those were what the app's last session had left (1824 × 1260, 3.6 ms, 18.6 dB). They do
not survive: every open of the app loads UserSet1 and sets its own pixel format, crop, rate, exposure and gain, and a power
cycle clears them. The one node the app does not set, the throughput limit (it keeps what the camera holds), is back
at its first-read value, 162 MB/s, and so is the pixel format. No user set saved, no default user set chosen, no reset, no event, no firmware: neither the tool nor the
app's camera code contains such a write (the gate checks it). One visible trace: IDS peak Cockpit, opened before the
app, would show the 08/10 crop and exposure. To read the camera back when the laptop is free: `$L/final_read.sh`
(it takes the lock exclusively, puts the first read's values back at runtime and reads every node).

## 8. What the build should change

Three changes, none made here (this unit only measured). Paths are in `application/` of release 1; line numbers
are release 1's. Each is small and local; each needs its own test before it ships.

**8.1 Lower the camera's throughput limit to 124 MB/s when the app opens the camera.**
In plain words: the camera sends each picture down the cable a little more slowly (in 38 ms instead of 30), so the
data comes in a gentler stream; the picture, its size and the 20 frames a second stay the same.
- *Where:* `src/core/config.py`, a new constant `IDS_THROUGHPUT_LIMIT_MBPS = 124` beside `IDS_USER_SET` (l. 126;
  0 = keep the camera's own, today's behaviour). `src/camera/ids_camera.py`: a new field
  `throughput_limit_mbps: float = 0.0` in `IDSCameraSettings` (l. 256); `UnifiedCamera._open_ids` (l. 1959) passes
  the constant; a new method `IDSCamera._apply_throughput_limit()` called from `IDSCamera._configure_camera`
  (l. 609) in place of the read-only « Device-Link Throughput Limit » block (l. 766, whose comment quotes
  February's « 125 MB/s → a stall every 5 s », not reproduced at this crop, §2.6). Where that block stands
  (after the UserSet load and the frame rate), it sets the node, snapped to the camera's steps (minimum + k × 2 MB/s,
  as `extra/ids_stall_ab.py apply_condition` does), then asks the frame rate again (its ceiling falls to 26.1 fps at
  the show crop), reads both back and logs them.
- *Test:* `tests/test_ids_stream_diag.py` (its `_camera()` builds an `IDSCamera` without the SDK): a fake node map
  whose `DeviceLinkThroughputLimit` has minimum 10 MB/s, step 2 MB/s, maximum 400 MB/s → `_apply_throughput_limit`
  with 124 writes 124,000,000 and asks the frame rate again; with 0 writes nothing; a node that refuses the write
  leaves the camera running with a logged line, never a failed open.
- *Proven (desk, 26/11):* the camera takes 124 MB/s at runtime (read back 124,000,000) and still runs 20.0 fps; the
  burst falls from 97 to 75 MB/s, below the narrow crop's 82 MB/s that never stalled (0 in 19.8 min on 06/10, 0 in
  10.4 min today); a faster burst makes stalls likelier (300 against 162 MB/s: 10 against 1 in 5.2 min, p = 0.012).
- *Not proven:* that 124 lowers the stalls. At 162 MB/s nothing stalled in that hour either (0 against 0 in 5.2 min
  a side, window 6), so there was nothing to lower; and not on the façade. Not measured: the slower readout's
  extra lean on a fast movement (38 against 30 ms). The proof is the rig test (§6, `r1-tl124`), then the app on the
  rig with the constant at 124 against 0.

**8.2 Do not publish the broken picture.**
In plain words: at each stall, the one picture whose transfer broke is today handed on as if it were whole, to the
detector and to the recording; it should be counted and dropped.
- *Where:* `src/camera/ids_camera.py`, `IDSCamera._acquisition_loop` (l. 1142). `buffer_meta()` already reads the
  buffer's `IsIncomplete()` and `_track_frame` (l. 1199) counts it; right after that call, an incomplete frame
  skips the `_latest_frame` update and the recording callback (`_frame_callback`, l. 1215) and goes on to the next
  buffer. The buffer still goes back to the pool (it already has, before the unpack).
- *Test:* `tests/test_ids_stream_diag.py`, beside `test_acquisition_loop_tracks_frame_ids` (l. 157, the real loop
  fed by a fake stream): three buffers, the middle one incomplete → the callback runs twice, `_latest_frame` is the
  third frame, `get_stream_diagnostics()["incomplete"] == 1`, every buffer queued back.
- *Proven:* every stall starts with exactly one incomplete frame (1,093 of 1,093 logged stalls since 06/10, 26 of 26
  today; 08/10 mur2 7: 92 incomplete = 92 stalls = 92 driver repairs), and release 1 publishes it like any other
  (code reading).
- *Not proven:* what that picture holds (how much of it arrived, and what fills the rest: no picture was saved
  today, by rule), and whether it ever caused a false detection or a visible glitch in a recording. Cost:
  one frame fewer per stall, which already loses about 33; U1's hold runs on the frame clock, so its owner should
  confirm one missing tick per stall changes nothing.

**8.3 Tell the technician what to do with their hands when the stall rate turns orange.**
In plain words: the health line already turns orange at 2 stalls a minute and red at 6; its words should name the
cable first, since the cable and its connectors are the first suspect (§4).
- *Where:* `src/core/ops_monitor.py`, `StallRate.state` (l. 1158), its orange and red texts, today « at the next
  break: check the camera's USB3 cable and power, try another USB3 port » and « the picture freezes often - … ».
  A new constant `STALL_HOW` beside `CAMERA_HOW` (l. 964; that one stays, the camera row uses it), for example
  « reseat the camera cable at both ends; still orange: the short cable straight into the laptop, away from the IR
  floods' power supplies ». No « - » inside it: the health line shows the text after the detail's last « - ».
  The same words go into `docs/FICHE_TECHNICIEN_FR.md`, the line that starts with `camera stalls:` (§ 3 « Orange ou
  rouge : la seule chose à faire », l. 57), in French.
- *Test:* `tests/test_h2_health_line.py`, `test_stall_rate_thresholds` (l. 375): the orange and red details carry
  the new words, and `health_line` shows the instruction whole (nothing cut at a « - »).
- *Proven:* a stall is the link breaking during one picture's transfer, then the driver's fixed repair; its level is
  set by something physical around the link, 10–30 times apart between days at identical settings, and on 08/10
  the camera dropped off the bus 35 times while the rig was set up.
- *Not proven:* that reseating or the short cable lowers the rate — that is the cable and port test of 4/12 (§4.1,
  §4.2); the words are the best bet, not a measured cure. The thresholds (2 and 6 a minute) are H2/U3's, unchanged.
