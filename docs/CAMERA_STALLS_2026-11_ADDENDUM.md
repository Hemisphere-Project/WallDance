# U2c · The stalls come with the app — addendum to the stall verdict of 26/11 (night of 26–27/11)

The afternoon's verdict is `docs/CAMERA_STALLS_2026-11.md` (U2, 26/11). This addendum is what the night measured on
tango-lenovo, on the desk (camera on XHC1 port 3), with the DEV app of release 1 (f60684f) on the copy project
`jeudi0809_mur2-cordee` and the bare camera tool `extra/ids_stall_ab.py` (U2c conditions), 22:14 → 00:56.

## In plain words

- **What brings the stalls: the app's own processing while it runs.** On the desk, at exactly the app's camera
  settings, the bare camera tool had 0 stalls in 6 minutes; the WallDance app in RUN had 39 in 6 minutes (6.5 a
  minute). The same app in STANDBY (the camera still read, the pictures not processed) had 0. So the stalls come
  with what the app does to each picture in RUN, not with the camera, the cable or the desk alone. Every stall
  tonight is the afternoon's kind: one picture's USB transfer breaks and the camera's driver takes 1.65–1.72 s to
  repair it.
- **Which part: the motion stage is involved; the detector, the GPU and the preview are not.** Turning the
  preview off changed nothing (45 against 38, chance). The bare tool running the show's detector (TensorRT) on every
  picture had 0 stalls, like the tool alone. Shrinking the app's motion stage (MOG2, the background model that finds
  what moves) to a quarter of the picture took the stalls to about zero: 1 in 6 minutes against 35 at the show's
  own size (0.7), and 0 against 15 at full size. The bare tool running that motion stage alone had a few stalls (5
  in 6 minutes against 0: suggestive, not conclusive on its own). One oddity, stated as measured and not explained:
  at full size the motion stage gave 2.5 stalls a minute, at the show's 0.7 about 6 to 7, at quarter size about 0 —
  bigger is not simply worse.
- **What removes them on the desk: the camera's throughput limit at 124 MB/s.** With the project key at 124 (the
  camera sends each picture about 30 % more slowly, still 20 pictures a second), the app in RUN had 0 stalls in 6
  minutes against 46 at the camera's own 162 MB/s (alternated), then 0 again in 15 minutes straight, at 20 pictures a second (one more run, not alternated: it bounds what is left at 124 to at most about one stall every 5 minutes). The motion stage at quarter
  size removes them too, but what that does to tracking was not measured: it is not recommended.
- **What of the afternoon's verdict stands and what falls.** Stands: what a stall is (a broken transfer and a
  1.65 s repair, the same in every run tonight); that pushing data faster makes stalls likelier — and now the
  reverse, slowing it to 124 removes them; that another program's GPU load does not bring them. Falls: « the level
  is set by the place » as the reason the desk looked clean. The desk's 0.11 a minute was the bare tool; the app on
  the same desk stalls 6.5 a minute, the same order as the façade's 3 to 4 a minute on 08/10, which were counted in
  the app's own camera logs. The façade-against-desk gap was mostly app-against-tool. And the 124 setting is no
  longer « not proven »: on the desk, in the app, it took the stalls from 7.7 a minute to 0.
- **For the rehearsal:** put `"ids_throughput_limit_mbps": 124` in the rehearsal project (keep the crop; the frame
  rate stays 20); watch the stall rate on the health line — it is the witness that 124 holds there. If it turns
  orange anyway, the afternoon's swaps (the cable, the port) come next.
- **Not known:** whether 124 holds on the façade (only the desk was measured); whether it holds with TouchDesigner
  running beside the app; why the motion stage brings the stalls (three hypotheses, and the one measurement each
  needs, below); what a smaller motion stage would do to tracking (not measured).

## Answers

**Answer Q1** (is it the app, or the camera at the app's settings?): **the app.** The bare tool at the app's exact
settings (crop 1824x1260+432+136, Mono10g40IDS, 20 fps, 3.6 ms, 18.6 dB, 162 MB/s, 16 buffers) had **0 stalls in
6.0 min**; the DEV app in RUN, alternated with it in the same window, **39 in 6.0 min** (6.5/min), p 3.6e-12.

**Answer Q3** (does the throughput limit at 124 MB/s remove them in the app?): **yes, on the desk.** The DEV app in
RUN with the project key at 124 had **0 stalls in 6.0 min** against **46 in 6.0 min** at the camera's own 162
(key 0), alternated over four app sessions, p 2.8e-14; each run's log reads back « DeviceLinkThroughputLimit: 124
MB/s (project 124, was 162) » and 20.0 FPS. The 15-minute run straight at 124 (W9, a **single run**, not a
comparison): **0 stalls in 15.0 min** (00:41:09 → 00:56:09), its log reading back « DeviceLinkThroughputLimit: 124 MB/s (project 124, was 162); frame rate 20.0 FPS (max 24.4) », no stall line nor short hole in the whole log, the Budget lines at 20.0 FPS median (lowest 19.7, 179 lines), the stages as at 162 (mog2_feed 16.0, mog2_wait 3.0, process_wall 22.4 ms). Zero in 15 minutes bounds what is left at 124 on the desk to **at most 0.20 a minute** (95 % upper bound, one stall every 5 minutes; 0.14 a minute, one every 7, counting Q3's 6 minutes at 124 too), against 7.7 a minute at 162 the same night: at least 97 % fewer.

## The comparisons

Each: alternated in one window (A B A B, 3 min a run, the knob turned by the app's API in one app session, or by
an app restart for the key), stalls = holes of 1 s or more, the exact two-sided test of equal rates (conditional
binomial). Recounted from the raw records by `analysis/count.py` and `analysis/compare.py`.

<!-- cmp -->
| id | A | stalls A | B | stalls B | order | p (exact, two-sided) |
|---|---|---|---|---|---|---|
| Q1 | tool at the app's settings | 0 in 6.0 min (0.00/min; runs 0, 0) | DEV app, RUN | 39 in 6.0 min (6.50/min; runs 19, 20) | ABAB | 3.6e-12 |
| Q2a | app RUN | 39 in 6.0 min (6.50/min; runs 23, 16) | app STANDBY | 0 in 6.0 min (0.00/min; runs 0, 0) | ABAB | 3.6e-12 |
| Q2b | app RUN, preview on | 45 in 6.0 min (7.50/min; runs 23, 22) | app RUN, preview off | 38 in 6.0 min (6.33/min; runs 18, 20) | ABAB | 0.51 |
| Q2c | tool, plain read | 0 in 6.0 min (0.00/min; runs 0, 0) | tool, TensorRT per frame | 0 in 6.0 min (0.00/min; runs 0, 0) | ABAB | 1 |
| Q2d | app RUN, MOG2 x1.0 | 15 in 6.0 min (2.50/min; runs 9, 6) | app RUN, MOG2 x0.25 | 0 in 6.0 min (0.00/min; runs 0, 0) | ABAB | 6.1e-05 |
| Q3 | app RUN, limit 162 (key 0) | 46 in 6.0 min (7.67/min; runs 24, 22) | app RUN, limit 124 | 0 in 6.0 min (0.00/min; runs 0, 0) | ABAB | 2.8e-14 |
| Q2e | tool, plain read | 0 in 6.0 min (0.00/min; runs 0, 0) | tool, MOG2 stage x0.7 | 5 in 6.0 min (0.83/min; runs 2, 3) | ABAB | 0.062 |
| Q2f | app RUN, MOG2 x0.7 (the show's) | 35 in 6.0 min (5.83/min; runs 21, 14) | app RUN, MOG2 x0.25 | 1 in 6.0 min (0.17/min; runs 1, 0) | ABAB | 1.1e-09 |
<!-- /cmp -->

Q2a also says the camera reading alone is not enough: in STANDBY the app still reads each picture into pinned
memory and up to the GPU (read_gpu). Q2c: 3588 and 3593 TensorRT inferences (15.8 ms, GPU 28 %) in the tool's
process, on a constant GPU tensor beside the camera's read: 0 stalls. Q2e is the motion stage alone in the tool
(dark → median 5 + Gaussian 5 at full size, INTER_AREA resize to 0.7, MOG2 with shadows, the cleaned mask opened,
contours; 4 OpenCV threads, a worker thread): 5 against 0, p 0.062 — the stage alone brings some, the app's whole
RUN more.

**The motion stage's size, not monotonic (said, not explained).** In the app, RUN: x1.0 → 15 in 6.0 min (2.5/min,
W5); x0.7, the show project's own (`profiles.show.mog2_scale`) → 35 in 6.0 min (5.8/min, W8; W1–W3 and W6's key-0
runs also ran at 0.7: 5.3–8.0/min); x0.25 → 0 and 1 in 6.0 min (W5, W8). Full size stalls less than 0.7. The app's
Budget lines (medians, ms): x1.0 mog2_feed 21.9, the main loop waiting for it (mog2_wait) 8.5, process_wall 29.5;
x0.7 about 16 / about 3 (2.9–4.3) / about 22.5; x0.25 12.0 / never waits / 17.0. The frame rate held 20 FPS (median) at every size.

## The runs

Every run, what it really had (from its own record: the app's log and status, or the tool's camera read-back):

<!-- runs -->
| window | run | what ran | start (mgr-ran) | min | stalls | /min | code | project | crop | pixel format, fps, exp, gain | limit | laptop load (cpu %, busiest core %, dpc %, gpu %) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w1-q1b | T1 | tool Q1T: plain read | 22:14:05 | 3.00 | 0 | 0.00 | tool u2c on f60684f | - | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3595 us, x8.51 | 162 MB/s (read back) | 3.9, 20.0, 0.2, 0.0 |
| w1-q1b | A1 | app run | 22:17:42 | 3.00 | 19 | 6.33 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.6, 39.3, 0.5, 18.8 |
| w1-q1b | T2 | tool Q1T: plain read | 22:20:52 | 3.00 | 0 | 0.00 | tool u2c on f60684f | - | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3595 us, x8.51 | 162 MB/s (read back) | 4.1, 21.7, 0.2, 0.0 |
| w1-q1b | A2 | app run | 22:24:27 | 3.00 | 20 | 6.67 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.6, 39.1, 0.5, 17.0 |
| w2-state | R1 | app SetState state=run | 22:32:19 | 3.00 | 23 | 7.67 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.4, 38.7, 0.5, 16.5 |
| w2-state | S1 | app SetState state=standby | 22:35:25 | 3.00 | 0 | 0.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 2.5, 23.1, 0.2, 5.6 |
| w2-state | R2 | app SetState state=run | 22:38:30 | 3.00 | 16 | 5.33 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.7, 39.5, 0.5, 20.1 |
| w2-state | S2 | app SetState state=standby | 22:41:36 | 3.00 | 0 | 0.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 2.4, 20.9, 0.2, 5.3 |
| w3-preview | PON1 | app TogglePreview enabled=true quiet=true | 22:49:34 | 3.00 | 23 | 7.67 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.3, 38.2, 0.5, 18.1 |
| w3-preview | POFF1 | app TogglePreview enabled=false quiet=true | 22:52:40 | 3.00 | 18 | 6.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.9, 41.2, 0.5, 18.7 |
| w3-preview | PON2 | app TogglePreview enabled=true quiet=true | 22:55:45 | 3.00 | 22 | 7.33 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.5, 38.1, 0.5, 18.1 |
| w3-preview | POFF2 | app TogglePreview enabled=false quiet=true | 22:58:51 | 3.00 | 20 | 6.67 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.3, 38.4, 0.5, 17.1 |
| w4-trt | PT.1 | tool P: plain read | 23:06:18 | 3.00 | 0 | 0.00 | tool u2c on f60684f | - | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3595 us, x8.51 | 162 MB/s (read back) | 3.8, 21.2, 0.2, 0.0 |
| w4-trt | PT.2 | tool TRT: TensorRT 3588 calls 15.84 ms; read_gpu | 23:09:20 | 3.00 | 0 | 0.00 | tool u2c on f60684f | - | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3595 us, x8.51 | 162 MB/s (read back) | 49.5, 99.5, 0.5, 28.0 |
| w4-trt | PT.3 | tool P: plain read | 23:12:21 | 3.00 | 0 | 0.00 | tool u2c on f60684f | - | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3595 us, x8.51 | 162 MB/s (read back) | 1.7, 18.0, 0.1, 0.0 |
| w4-trt | PT.4 | tool TRT: TensorRT 3593 calls 15.66 ms; read_gpu | 23:15:22 | 3.00 | 0 | 0.00 | tool u2c on f60684f | - | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3595 us, x8.51 | 162 MB/s (read back) | 49.3, 99.4, 0.4, 27.3 |
| w5-mog | M1001 | app SetMog2Scale value=1.0 | 23:27:05 | 3.00 | 9 | 3.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 6.6, 45.8, 0.5, 19.3 |
| w5-mog | M0251 | app SetMog2Scale value=0.25 | 23:30:10 | 3.00 | 0 | 0.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.0, 42.5, 0.6, 22.4 |
| w5-mog | M1002 | app SetMog2Scale value=1.0 | 23:33:16 | 3.00 | 6 | 2.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 6.8, 46.6, 0.5, 20.0 |
| w5-mog | M0252 | app SetMog2Scale value=0.25 | 23:36:22 | 3.00 | 0 | 0.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 4.8, 41.5, 0.5, 23.2 |
| w6-q3 | TL0_1 | app key=0 | 23:48:42 | 3.00 | 24 | 8.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.5, 38.4, 0.5, 18.3 |
| w6-q3 | TL124_2 | app key=124 | 23:52:15 | 3.00 | 0 | 0.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 124 MB/s (project 124, was 162) | 6.5, 45.2, 0.6, 21.2 |
| w6-q3 | TL0_3 | app key=0 | 23:55:47 | 3.00 | 22 | 7.33 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.5, 38.7, 0.5, 18.1 |
| w6-q3 | TL124_4 | app key=124 | 23:59:20 | 3.00 | 0 | 0.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 124 MB/s (project 124, was 162) | 6.4, 44.4, 0.6, 21.3 |
| w7-mog | PM.1 | tool P: plain read | 00:06:46 | 3.00 | 0 | 0.00 | tool u2c on f60684f | - | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3595 us, x8.51 | 162 MB/s (read back) | 3.8, 21.7, 0.2, 0.0 |
| w7-mog | PM.2 | tool MOG: MOG2 x0.7 3534 calls 13.08 ms, 4 cv2 thr | 00:09:47 | 3.00 | 2 | 0.67 | tool u2c on f60684f | - | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3595 us, x8.51 | 162 MB/s (read back) | 3.8, 31.9, 0.2, 0.0 |
| w7-mog | PM.3 | tool P: plain read | 00:12:47 | 3.00 | 0 | 0.00 | tool u2c on f60684f | - | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3595 us, x8.51 | 162 MB/s (read back) | 4.0, 20.5, 0.2, 0.0 |
| w7-mog | PM.4 | tool MOG: MOG2 x0.7 3501 calls 13.08 ms, 4 cv2 thr | 00:15:48 | 3.00 | 3 | 1.00 | tool u2c on f60684f | - | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3595 us, x8.51 | 162 MB/s (read back) | 3.7, 31.7, 0.2, 0.0 |
| w8-mog07 | M071 | app SetMog2Scale value=0.7 | 00:23:49 | 3.00 | 21 | 7.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.5, 39.3, 0.5, 16.8 |
| w8-mog07 | M0251 | app SetMog2Scale value=0.25 | 00:26:54 | 3.00 | 1 | 0.33 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.0, 42.8, 0.6, 22.4 |
| w8-mog07 | M072 | app SetMog2Scale value=0.7 | 00:30:00 | 3.00 | 14 | 4.67 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 5.8, 39.9, 0.5, 19.3 |
| w8-mog07 | M0252 | app SetMog2Scale value=0.25 | 00:33:06 | 3.00 | 0 | 0.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 162 MB/s (the camera's own) | 4.9, 41.7, 0.6, 22.2 |
| w9-tl124long | TL124_L | app key=124 | 00:41:09 | 15.00 | 0 | 0.00 | f60684f (DEV app) | jeudi0809_mur2-cordee | 1824x1260+432+136 | Mono10g40IDS, 20.0, 3576 us, 18.6 dB | 124 MB/s (project 124, was 162) | 6.4, 44.6, 0.6, 21.3 |
<!-- /runs -->

The app's own Budget lines in each app run (every ~5 s; median FPS and, in brackets, the lowest line — the lowest
line of a stalled run falls during a stall; stage medians in ms). In STANDBY (w2-state S1, S2) the stage values are the last RUN values, frozen (24 identical lines): no processing runs there; only camera_read and the FPS are live:

<!-- budget -->
| window | run | budget lines | FPS median (lowest) | camera_read ms | process_wall ms | yolo ms | mog2_feed ms | mog2_wait ms |
|---|---|---|---|---|---|---|---|---|
| w1-q1b | A1 | 34 | 19.9 (0.6) | 1.4 | 22.9 | 11.8 | 16.8 | 3.5 |
| w1-q1b | A2 | 35 | 19.9 (0.6) | 1.4 | 22.9 | 11.9 | 16.3 | 3.1 |
| w2-state | R1 | 35 | 19.8 (2.2) | 1.4 | 22.3 | 11.8 | 15.9 | 3.0 |
| w2-state | S1 | 36 | 20.0 (19.8) | 1.4 | 24.7 | 12.7 | 17.7 | 4.2 |
| w2-state | R2 | 35 | 20.0 (1.1) | 1.4 | 22.7 | 12.0 | 16.3 | 3.2 |
| w2-state | S2 | 36 | 20.0 (19.8) | 1.4 | 23.4 | 12.4 | 16.7 | 3.3 |
| w3-preview | PON1 | 35 | 19.9 (0.6) | 1.4 | 22.8 | 11.9 | 16.3 | 3.1 |
| w3-preview | POFF1 | 34 | 19.9 (1.1) | 1.4 | 22.5 | 12.2 | 16.1 | 3.2 |
| w3-preview | PON2 | 34 | 19.9 (2.1) | 1.4 | 22.3 | 11.9 | 16.0 | 3.1 |
| w3-preview | POFF2 | 35 | 19.9 (1.7) | 1.4 | 22.8 | 11.7 | 16.3 | 4.3 |
| w5-mog | M1001 | 35 | 20.0 (3.1) | 1.4 | 29.5 | 12.0 | 21.9 | 8.4 |
| w5-mog | M0251 | 36 | 20.0 (19.9) | 1.4 | 17.0 | 10.9 | 12.0 | - |
| w5-mog | M1002 | 36 | 20.0 (1.1) | 1.3 | 29.4 | 12.1 | 21.8 | 8.6 |
| w5-mog | M0252 | 36 | 20.0 (19.7) | 1.3 | 16.8 | 11.0 | 12.0 | - |
| w6-q3 | TL0_1 | 34 | 19.95 (1.6) | 1.4 | 22.8 | 12.1 | 16.1 | 3.0 |
| w6-q3 | TL124_2 | 36 | 20.0 (19.8) | 1.4 | 22.2 | 12.0 | 15.9 | 2.6 |
| w6-q3 | TL0_3 | 35 | 19.9 (2.1) | 1.4 | 22.5 | 11.9 | 16.1 | 2.9 |
| w6-q3 | TL124_4 | 36 | 20.0 (19.8) | 1.4 | 22.0 | 12.1 | 15.8 | 2.6 |
| w8-mog07 | M071 | 35 | 20.0 (3.4) | 1.4 | 22.5 | 11.7 | 16.4 | 3.4 |
| w8-mog07 | M0251 | 36 | 20.0 (7.5) | 1.3 | 16.9 | 10.8 | 11.8 | - |
| w8-mog07 | M072 | 35 | 20.0 (0.6) | 1.3 | 22.6 | 11.6 | 16.4 | 3.4 |
| w8-mog07 | M0252 | 36 | 20.0 (19.8) | 1.3 | 16.9 | 10.9 | 11.9 | - |
| w9-tl124long | TL124_L | 179 | 20.0 (19.7) | 1.4 | 22.4 | 11.8 | 16.0 | 3.0 |
<!-- /budget -->

## Not known

- **The façade.** Everything tonight is the desk. The façade's 08/10 level (3–4 a minute, in the app) is the same
  order as tonight's app-on-the-desk, which suggests the app is the main factor there too; it does not show that
  124 holds there. The rehearsal (and window 2 on the rig) is the measurement: the stall rate on the health line.
- **124 beside TouchDesigner.** Not measured (TouchDesigner is not to be run from here). The afternoon's
  verdict found another program's GPU load harmless at 162 on the bare tool; whether the show's TouchDesigner
  changes the app's rate at 124 is open.
- **Why the motion stage does it.** None of these explains the non-monotonic order by itself:
  1. *OpenCV's worker threads.* The stage wakes 4 OpenCV threads for each picture (blurs, resize, MOG2); they may
     compete on the same cores with the USB driver's deferred work, so a transfer misses its turn. One measurement:
     the tool's motion condition at 0.7 with 1 OpenCV thread against 4 (`--cv2-threads 1` / `4`), alternated
     4 × 3 min.
  2. *Memory traffic.* MOG2 keeps several Gaussians per pixel and reads and writes them at every picture: tens of
     MB a picture at 0.7, a few MB at 0.25 (a size that stays in the processor's cache); that traffic could delay
     the camera's transfers into memory. One measurement: the tool with a plain memory-copy load of the same bytes
     a picture (no OpenCV) against the motion stage at 0.7, alternated.
  3. *Timing.* At 0.7 the stage ends about when the next picture's transfer runs (the main loop waits ~3 ms for
     it); at 1.0 it runs longer (waits ~8.5 ms), at 0.25 it never holds the loop. If a stall needs the stage's
     burst to overlap a transfer, the rate would follow the phase, not the size. One measurement: an instrumented
     DEV run at 0.7 logging, per picture, the stage's start and end and the picture's arrival, set against the
     stall times.
- **Garbage-collection pauses** (a lead from the swipe session, `APP_LOGS_26NOV.md`): full collections of 94–159 ms
  in the frame loop, rarer than the stalls; not measured here. Its measurement: time each collection with
  `gc.callbacks` in a DEV run and set it against the stall timestamps.
- **What a smaller motion stage does to tracking.** Not measured; 0.25 is not recommended on tonight's numbers.

## What the build should change

1. **The key's default: 124.** `application/src/core/config.py`, the constant `IDS_THROUGHPUT_LIMIT_MBPS`
   (now `0.0`, with the comment « Not proven to lower the stalls … off unless the project says so »): set it to
   `124.0` and rewrite the comment with tonight's numbers (Q3: 46 against 0 in 6 min, then W9). It is read by
   `application/src/runtime/camera_controller.py` (`CameraController.__init__`, `set_ids_throughput_limit`) and
   written at every camera open by `application/src/camera/ids_camera.py` (`IDSCamera._apply_throughput_limit`,
   §8.1). The readiness row already says when the camera refuses the limit or its frame rate falls
   (`application/src/core/ops_monitor.py`, `camera_link_text`, `LINK_LIMIT_HOW`): that safety stays as it is. A
   project that wants the camera's own keeps `"ids_throughput_limit_mbps": 0`. For the rehearsal alone the
   project key is enough (no code); the default is the build's decision.
2. **The motion stage: propose, do not build tonight.** `application/src/core/motion_detector.py`
   (`MotionDetector.preprocess`: dark → median 5 + Gaussian 5 at full size, INTER_AREA resize;
   `MotionDetector.feed_preprocessed`: the MOG2 apply), called from `application/src/core/pipeline.py`
   (`_submit_motion_feed` → `_feed_motion_detectors` on the one `mog2-feed` worker, awaited by
   `_await_motion_feed` before the tracker), with `CV2_NUM_THREADS = 4` in `core/config.py`. Proposed, in order:
   (a) an opt-in timing of the stage per picture (start, end, the picture's arrival) beside the stall lines, for
   hypothesis 3; (b) the thread-count test in the tool (hypothesis 1); (c) only with a tracking score on the mur
   and hangar takes, a smaller `mog2_scale` in the show profile. Nothing changes in this stage before (c).
3. **The witness.** The stall rate on the health line (H2) is how the rehearsal tells whether 124 holds; keep it.

## The laptop, as left

Read at 00:56:42, after the last hold (ended 00:56:23), by `laptop/final_read.sh` (`reads/final_*.txt`, `reads/sysread_end.json`): no app (APPS 0), no show software (SHOWSW 0), no foreign Python (FOREIGN 0), nothing of ours running (the only Python was the read itself); `last_project.txt` = `jeudi0809_mur`; the copy project's config `jeudi0809_mur2-cordee_20261126_110000.json` back to its original bytes (sha a205bb2f… = the backup, mtime put back: no `ids_throughput_limit_mbps` key, `mog2_scale` 0.7); the power and USB settings read back identical to the first read of 22:09 (settings_sha256 d75fb6fe9d48ca1d at both, and the USB, power and IDS sections identical too). Never touched: the live slot, the launcher, another project, the camera's saved settings, a Windows setting, TouchDesigner. Left on the laptop: the scratch folder `C:\WallDance\_r2-1126\u2c` (57 files, 0.9 MB: the tool and the job records), for the orchestrator to keep or remove.

## How it was measured

Every window was one command on mgr-ran under `flock -x /tmp/wd-laptop.lock` (≤ 18 min), with a precheck that
nothing foreign ran (no live app, no launcher, no TouchDesigner, no other Python), a load sampler, the DEV app only
on `jeudi0809_mur2-cordee`, its pointer put back to `jeudi0809_mur` and the OSC listener stopped at the end. The
key was set in the copy project's config only (`laptop/setkey.py`: original bytes and mtime kept, put back after
each window; never saved into the camera). Scripts: `laptop/win_*.sh` on `laptop/winlib.sh`; records in
`runs/<window>/`; analysis `analysis/count.py` → `runs.jsonl`, `compare.py` → `comparisons_result*.{json,md}`,
`runs_table.py` → `runs_table.md`, `budget.py` → `budget_table.md`. Gate: `run_gate.sh`.
