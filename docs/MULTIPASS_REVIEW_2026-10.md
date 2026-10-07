# Multi-pass analysis -- review and plan (2026-10-07)

**Question (Thomas, 2026-10-07):** lower the analysis to 10-12 fps and spend the freed GPU time on several YOLO
passes (other settings, input sizes or confidences; a warm-up; a search for a lost dancer). Can that improve the
results significantly, lower the operator's setup load and widen the situations we cover (distance, light,
background)? Not if the gains are marginal: no complexity for its own sake.

**Verdict: nothing measured justifies multi-pass on the WallDance wall. Keep one x@1280 pass at 20 fps.**

1. **Keep 20 fps.** Analysing every 2nd frame costs 8-21 points of presence on 3 of 6 scenes and adds 25-135 ms of
   lag (§3.1).
2. **No round-robin settings, no verification pass, no tiles.** Alternating the global enhancement only helps where
   one gamma is simply wrong, which Calibrate fixes (§3.5). A zoomed second look confirms person-like ghosts as
   readily as dancers (§3.4).
3. **Do not build the track-guided zoom pass.** It is the one second pass with a real effect -- on small people
   (facade, ~65 px in YOLO's input: on-dancer +9.5 points, coasting 12.8 -> 1.3 %) -- but unguarded it locks onto
   person-like static objects (bdx1005-s5: on-dancer -12 points, jitter x15), and once made safe with the
   empty-wall snapshot its gain shrinks to +2.8 points on facade while duos lose (white duo -15, textured duo -4
   points and +33 id switches) (§3.6). Shelved on its branch.
4. **No model swap either.** The lighter model at a bigger input (l@1536, ~19 ms on the laptop) wins on the small /
   dark stress scenes (facade on-dancer +12.7, outdoor-night presence +21.6 points) but **loses on the night wall
   itself** -- s2c, the dark still dancer at the far wall, the closest proxy for tomorrow: presence 0.884 -> 0.823,
   ghost frames 1 -> 9 -- and collapses on the textured duo (on-dancer 0.852 -> 0.438); s4 (dark, moving) is
   identical at x@1280, l@1536, x@960 and l@1280 (§3.7). The same x model with more pixels is worse still on s2c
   (x@1536: presence 0.789, a 22 s hole): the night dancer is past YOLO's size knee at 1280, and more pixels add
   dark noise. **For ~30 m: keep x@1280, keep the ROI on the dancers' area, and let the 30 m demo takes -- not the
   stress scenes -- decide any size change.**
5. **Most "lost" frames are not lost.** In 34-84 % of the frames where a slot has no fresh skeleton (7 of 9
   scenes) the track is still updated (motion, or a box with a weak skeleton) and the point continues; true detector
   misses are 6-40 % of them (outdoor-night 89 %). The motion cross-check is not the culprit: switching it off
   changes nothing on 2 scenes and trades presence against id switches on 3 (§3.8).

## 1. The ideas

| # | Idea | What it would buy | Tested as |
|---|---|---|---|
| A | 10-12 fps analysis (+ interpolation for TouchDesigner) | 2-2.5x GPU time per analysed frame | E1: the whole pipeline on every 2nd frame |
| B | Round-robin settings (gamma / CLAHE / size alternating per frame) | robustness to light | E5: union of 3 global gammas on the lost frames |
| C | Track-guided zoom ("focus"): a native-resolution crop around each tracked dancer, 640 input | far / small dancers, better skeletons | E2 offline + end-to-end prototype |
| D | Search for a lost dancer: the zoom around a slot YOLO lost, optionally local brightening | shorter holes, still dancers | E3 offline + prototype |
| E | Verification of new / doubtful tracks: a zoomed second look before a track may take a slot | ghost rejection | E4 |
| F | Tiles for wide ROIs (2 half-ROI passes) | small dancers in a wide frame | not run: 2x cost; bounded by C and by the bigger-input result (§3.7) |
| G | Warm-up passes at start / on entry | faster first lock | not run separately: the entry rule + snapshot gate entries; C/D cover re-acquisition |

## 2. Method

- **Footage (dev37, local):** the scenario corpus with known dancer counts -- white duo and textured duo (~200 /
  300 px, contact), hangar-aerial (aerial, low light), texture-aerial, bdx1005-s5 (operator near / far, a
  person-like static figure by the door), bdx1005-s8 (shadows), outdoor-night (extreme dark), facade-ghosts (4
  walkers of ~98 px in a 1920 x 1080 phone frame: the small-dancer case), blur-runner; the night project's empty
  take.
- **Truth:** replay timelines (`replay.py --quality --internal`, TRT x@1280). A slot is *seen* when its track got a
  YOLO skeleton this frame; a *lost frame* is a slot emitted without one (coasting / belt / snapshot / weak), its
  position interpolated between the seen frames around it (<= 3 s). The interpolation knows where the dancer
  reappears: the offline find rates on lost frames are upper bounds.
- **Passes (offline, `tmp_analysis/multipass/mp_study.py`, yolo11x-pose PyTorch FP16):** FULL = the whole ROI
  letterboxed to 480-1280 with the scene's gamma / CLAHE; ZOOM = a 3 h square crop at native resolution
  letterboxed to 640, with the scene's (`g`) or a crop-local (`l`) gamma; DECOY = the same zoom at a spot >= 2 h
  from every dancer (what a search "finds" on empty wall). Matched = centre within 0.5 h, height 0.5-2 x.
- **End-to-end prototype** (`core/zoom_pass.py` + a pipeline hook, replay-only, off by default): before the
  tracker, a 640 crop around each slot that is lost (`lost`), small in the main pass's input (< 100 px, `small`) or
  both (`both`); found dancers injected in the tracker's space (a small slot's coarse detection is replaced); with
  `zoom_need_fg` (default) a lost-dancer find needs foreground against the empty-wall snapshot. Scored with
  `kpi.py`'s metrics on the emitted stream against the same code with the zoom off.
- **Costs:** PyTorch timings on a shared GPU are meaningless; the laptop's TRT table (x@1280 21 ms, x@960 12.7 ms,
  x@1536 30 ms, l@1280 13.5 ms; 50 ms per frame at 20 fps, TouchDesigner on the same GPU at 84-87 C) and an x@640
  engine built on dev37 for this study: **x@640 = 0.31 x x@1280, ~6.7 ms per crop on the laptop.**

## 3. Results

### 3.1 Analysis at 10 fps (E1)

The whole pipeline on every 2nd frame (`--frame-skip 2`), scenario configs, emitted stream. Caveat: frame-based
tracker parameters (max age, warm-up windows) then last twice as long; a tuned 10 fps mode would recover part.

| scene | presence 20 -> 10 fps | longest hole (s) | lag (ms) |
|---|---|---|---|
| white duo | 0.909 -> **0.832** | 5.2 -> 8.6 | 57 -> 191 |
| textured duo | 0.937 -> **0.793** | 7.4 -> 13.3 | -- |
| outdoor-night | 0.587 -> **0.373** | 4.3 -> 7.3 | -- |
| texture-aerial | 0.980 -> 0.961 | 0.6 -> 1.1 | 86 -> 113 |
| bdx1005-s8 | 0.927 -> 0.920 | 2.6 -> 2.7 | 88 -> 147 |
| hangar-aerial | 0.997 -> 0.995 | 0.8 -> 0.8 | 61 -> 107 |

Fast, close or dark scenes lose the association between frames, and every scene pays lag before TouchDesigner's own
smoothing. Any extra pass worth having fits in the 20 fps budget anyway (§3.6 costs).

### 3.2 Dancer size in YOLO's input (E2)

Recall of the seen dancers (conf >= 0.25) by the full pass at decreasing input size vs the zoom:

| scene (dancer px) | net px at 1280 | full 1280 | full 960 | full 640 | full 480 | zoom 640 |
|---|---|---|---|---|---|---|
| facade (98, 1920 frame) | 65 | 90 % (54 % >= 0.5) | 2 % | 0 % | 0 % | **100 % (100 % >= 0.5)** |
| outdoor-night (137, dark) | 149 | 100 % | 88 % | 44 % | 38 % | 88-94 % |
| hangar-aerial (206) | 203 | 100 % | 97 % | 94 % | 80 % | 97 % |
| white duo (207) | 205 | 96 % | 92 % | -- | -- | 90 % |
| textured duo (296, contact) | 298 | 81 % | 59 % | 42 % | 38 % | **52 %** |

The knee sits around 80-110 px of dancer in YOLO's input (Phase 2b: 83-110). Above it the zoom adds nothing; far
below it (facade) it is the difference between seeing and not seeing. When dancers touch, the zoom is worse (a
crop around one dancer holds both).

### 3.3 The lost frames (E3)

What the frames without a fresh skeleton are (`gap_causes.py`), and what a second look finds there (offline):

| scene | lost frames | track still updated, no skeleton | YOLO had >= 0.5 there | < 0.5 there | nothing: a miss | full 1280 finds (>= 0.25) | zoom `g` / `l` | decoy `g` / `l` |
|---|---|---|---|---|---|---|---|---|
| facade | 387 | 84 % | 1 % | 5 % | 10 % | 5 % | **99 / 98 %** | 2 / 2 % |
| outdoor-night | 55 | 0 % | 2 % | 9 % | **89 %** | 51 % | 56 / **98 %** | 0 / 0 % |
| bdx1005-s5 | 315 | 47 % | 16 % | 4 % | 34 % | 58 % | **84** / 78 % | 2 / 3 % |
| hangar-aerial | 2391 | 69 % | 25 % | 0 % | 6 % | 77 % | 75 / 71 % | 1 / 0 % |
| texture-aerial | 213 | 55 % | 23 % | 11 % | 10 % | 51 % | 53 / 54 % | 0 / 0 % |
| textured duo | 2724 | 38 % | 11 % | 24 % | 27 % | 26 % | 21 / 21 % | 0 / 0 % |
| white duo | 457 | 34 % | 13 % | 13 % | 40 % | 33 % | 36 / 38 % | 0 / 0 % |
| blur-runner | 68 | 68 % | 0 % | 0 % | 32 % | 100 % | 100 / 100 % | -- |
| bdx1005-s8 | 40 | 5 % | 60 % | 0 % | 35 % | 60 % | 60 / 62 % | 0 / 0 % |

- **Most lost frames are bridged, not lost**: the tracker keeps updating the track (motion blobs, or a YOLO box whose
  keypoints are too weak to count as a skeleton) and the point goes on. True detector misses are 6-40 % of the lost
  frames, except outdoor-night (89 %).
- **A zoomed second look finds the dancer where the full pass is blind** -- small people (facade) and darkness with
  local brightening (outdoor-night, s5) -- with 0-3 % false finds on empty wall. Elsewhere it finds what the full
  pass already finds.

### 3.4 A second look does not separate ghosts from people (E4)

Box confidence of each candidate under the scene's pass and under zoomed looks (`mp_verify.py`; static = a track
YOLO kept seeing that never moved: the known ghosts):

| scene | candidate | full | full, neutral | zoom `g` | zoom neutral | zoom `l` |
|---|---|---|---|---|---|---|
| bdx1005-s5 | static figure by the door (ghost) | 47 % >= 0.25 (median 0.23) | 0 % | **92 % (0.58)** | 12 % | 82 % |
| bdx1005-s5 | the operator walking | 80 % (0.62) | 42 % | 92 % (0.59) | 38 % | 82 % |
| night empty take, gamma 1.8 / CLAHE 1.5 | the equipment ghost | 10 % | 0 % | 15 % | 0 % | 12 % |
| same, gamma 0.73 / CLAHE 2.5 | same spot | 0 % | 0 % | 0 % | 0 % | 8 % |

Zooming raises the ghost's confidence as much as the person's; only neutral enhancement suppresses the ghost, and it
suppresses dark dancers just as much. **No verification pass**: the empty-wall snapshot (no foreground on a static
object) and the static-ghost guard remain the tools -- and they are what makes any search safe (§3.6).

### 3.5 Round-robin global settings (E5)

On the lost frames, the union of three global gammas (x 1/1.6, 1, x 1.6) vs the scene's own, at >= 0.25: white duo
38 vs 33 %, textured duo 30 vs 26 %, s5 60 vs 58 %, s8 68 vs 60 %, hangar-aerial 84 vs 77 %, texture-aerial 65 vs
51 %, facade 29 vs 5 %, outdoor-night 96 vs 51 %. Outdoor-night's gain is one setting (x 1.6 alone: 93 %): its gamma
0.76 is too dark, a calibration issue the empty-wall Calibrate addresses, not a reason to alternate settings (which
also feeds the tracker inconsistent boxes). **Not worth it.**

### 3.6 End-to-end prototype: track-guided zoom (C + D)

TRT x@1280 main pass, zoom x@640; emitted stream (presence, coasting share, point on a dancer, jitter at rest % h,
lag ms, id switches):

| scene | zoom | presence | coasting | on dancer | jitter | lag | switches | crops / frame |
|---|---|---|---|---|---|---|---|---|
| facade (4 x ~98 px), no snapshot | off | 0.899 | 0.128 | 0.757 | 1.29 | 60 | 0 | -- |
| | lost | 0.899 | 0.116 | 0.755 | 1.37 | 60 | 0 | 0.4 |
| | small | 0.908 | 0.095 | 0.789 | 1.07 | 69 | 0 | 3.0 |
| | both, unguarded | **0.908** | **0.013** | **0.852** | 1.55 | 68 | 0 | 3.5 |
| facade, take-median snapshot | off | 0.906 | 0.117 | 0.761 | 1.29 | 60 | 0 | -- |
| | both, guarded | 0.908 | 0.078 | 0.789 | 1.07 | 69 | 0 | 3.5 |
| outdoor-night (no snapshot: lost search unguarded) | off | 0.587 | 0.508 | 0.661 | 0.51 | -- | 0 | -- |
| | both | 0.613 | 0.477 | 0.661 | 0.51 | -- | 0 | 0.3 |
| | both, local gamma | 0.587 | 0.416 | 0.661 | 0.53 | -- | 0 | 0.25 |
| texture-aerial | off / both | 0.980 / 0.980 | 0.058 / 0.038 | 0.830 / 0.862 | 0.62 / 0.77 | 86 / 132 | 0 / 0 | 0.04 |
| bdx1005-s8 | off / both | 0.927 / 0.926 | 0.050 / 0.045 | 0.967 / 0.967 | 0.78 / 0.78 | 88 / 88 | 0 / 0 | 0.04 |
| **bdx1005-s5**, unguarded | off / both | 0.999 / 0.998 | 0.131 / 0.155 | 0.912 / **0.793** | 1.69 / **24.98** | 181 / -- | 0 / 0 | 0.15 |
| bdx1005-s5, snapshot + guard | off / both | 0.993 / 0.993 | 0.131 / 0.131 | 0.912 / 0.912 | 1.70 / 1.70 | 181 / 181 | 0 / 0 | 104 finds refused |
| white duo, unguarded | off / both | 0.909 / 0.905 | 0.232 / 0.211 | 0.741 / 0.842 | 4.57 / 3.96 | 57 / 60 | 10 / 12 | 0.4 |
| **white duo, snapshot + guard** | off / both | 0.916 / 0.916 | 0.148 / 0.188 | 0.843 / **0.690** | 3.99 / 4.05 | 62 / 57 | 16 / 10 | 0.5 |
| textured duo (no snapshot: search refused) | off / both | 0.937 / 0.937 | 0.252 / 0.252 | 0.897 / 0.897 | 8.78 / 8.78 | -- | 89 / 89 | 131 finds refused |
| **textured duo, snapshot + guard** | off / both | 0.950 / 0.951 | 0.108 / 0.086 | 0.901 / **0.861** | 6.95 / 6.51 | -- | 61 / **94** | 0.5 |

- The live search (`lost`) finds far less than the offline upper bound: it only knows the slot's last / predicted
  spot, and outdoor-night's long holes are stretches where no slot exists yet (nothing to search around).
- **Unguarded it is dangerous:** on s5 it found the person-like figure by the door next to the lost operator and
  handed it to his track. Requiring foreground against the empty-wall snapshot at the found spot removes it -- and
  most of the gains with it (facade: on-dancer +2.8 instead of +9.5).
- **Duos in contact:** with the snapshot, the white duo's +10 points come from the snapshot itself (off: 0.741 ->
  0.843); the guarded zoom then *costs* 15 points there and 4 points + 33 id switches on the textured duo -- a crop
  around one dancer returns the other.
- Cost: ~6.7 ms per crop on the laptop; facade's 3.5 crops / frame = +23 ms on top of x@1280's 21 ms.

### 3.7 The configuration alternative: a bigger input with the lighter model

Single full pass, no zoom, PyTorch path on dev37 (no engines at these sizes; compare rows within a scene), laptop
cost from the TRT table (l@1536 / l@1920 / x@1920 extrapolated by input area):

| scene | main pass | laptop cost (est.) | presence | coasting | on dancer | jitter | lag | switches |
|---|---|---|---|---|---|---|---|---|
| facade (small) | x@1280 (today) | 21 ms | 0.904 | 0.128 | 0.757 | 3.58 | 60 | 0 |
| | x@1536 | 30 ms | 0.946 | 0.168 | 0.759 | 0.40 | 65 | 0 |
| | **l@1536** | **~19 ms** | **0.934** | 0.119 | **0.884** | **0.34** | 58 | 0 |
| | l@1920 | ~30 ms | 0.944 | 0.017 | 0.805 | 1.44 | 60 | 8 |
| | x@1920 | ~47 ms | 0.955 | 0.050 | 0.908 | 0.77 | 57 | 0 |
| outdoor-night (dark) | x@1280 | 21 ms | 0.524 | 0.527 | 0.577 | 0.51 | -- | 0 |
| | **l@1536** | ~19 ms | **0.740** | 0.468 | **0.843** | 0.47 | -- | 0 |
| white duo (big) | x@1280 | 21 ms | 0.889 | 0.286 | 0.726 | 8.24 | 72 | 5 |
| | l@1536 | ~19 ms | 0.884 | 0.209 | 0.793 | 2.00 | 111 | 18 |
| textured duo (contact, textured wall) | x@1280 | 21 ms | 0.965 | 0.222 | 0.852 | 7.72 | -- | 68 |
| | l@1536 | ~19 ms | 0.981 | 0.320 | **0.438** | **15.93** | -- | 42 |

**The night project's own takes** (validated recipe: D27 yolo_first / conf 0.15 / intermittent, D28 gamma 0.73 /
CLAHE 2.5 / MOG2 8 @ 0.7, belt backing, entry rule, height guard; wall-band ROIs; scored in the 'operator at the
wall' window, N = 1; PyTorch path for every row):

| take | main pass | presence | longest hole (s) | holes >= 1 s | coasting | on dancer | jitter | lag | ghost frames |
|---|---|---|---|---|---|---|---|---|---|
| s4 (dark, moving, ~127 px) | x@1280 | 0.955 | 4.35 (an exit) | 1 | 0.015 | 1.000 | 0.59 | 75 | 0 |
| | l@1536 | 0.955 | 4.35 | 1 | 0.011 | 1.000 | 0.70 | 71 | 0 |
| | x@960 | 0.955 | 4.35 | 1 | 0.017 | 1.000 | 0.59 | 79 | 0 |
| | l@1280 | 0.955 | 4.35 | 1 | 0.012 | 1.000 | 0.63 | 79 | 0 |
| s2c (dark, 2 min still at the far wall) | x@1280 | **0.884** | 11.55 | 4 | 0.039 | 0.949 | 0.50 | 35 | 1 |
| | l@1536 | 0.823 | 12.65 | 5 | 0.050 | 0.931 | 3.86 | 75 | 9 |
| | x@1536 | 0.789 | **22.20** | 5 | 0.053 | 0.941 | 0.77 | 72 | 0 |

On the scenes this study was looking for (small people in a wide frame, extreme dark) more input pixels with the
lighter model win clearly, at a lower cost. On the WallDance night wall they do not: the moving take is identical
at every size from 960 up (the night dancer, ~127 px, is not small for YOLO), the still dancer loses 6 points of
presence and gains ghost frames with l@1536 and loses 9.5 points with a 22 s hole at x@1536, and the textured duo
collapses with l@1536. Past the size knee, more pixels of a dark scene are more noise (Phase 2b: oversizing past
the knee worsens quality). Size and model are scene-dependent in both directions -- not something to switch blind.

### 3.8 The motion cross-check bound

The cross-check switched off (`crossval_enabled=false`), x@1280 TRT: texture-aerial and s8 unchanged; textured duo
presence 0.937 -> 0.957 but id switches 89 -> 126; white duo on-dancer 0.741 -> 0.899 but presence 0.909 -> 0.888 and
switches 10 -> 17; outdoor-night presence 0.587 -> 0.648, on-dancer 0.661 -> 0.630. It trades presence against
swaps scene by scene: not a lever on its own, and not what loses the bridged frames of §3.3.

## 4. What it means

- **30 m (tomorrow).** A dancer at 30 m is ~105 px if last night's back wall (~127 px) was ~25 m away: ~103 px in
  YOLO's input at x@1280 with the night's 1304 px wall band (above the knee), ~75 px if the ROI has to widen to
  ~1800 px. In order: (1) keep the ROI on the dancers' area only (free); (2) read the pre-show "dancer size" line
  with a dancer at the far wall; (3) keep x@1280 even if the line is orange, unless the 30 m takes replayed at
  x@1536 say otherwise (on s2c x@1536 lost 9.5 points and made a 22 s hole); (4) nothing else until the 30 m takes
  say otherwise.
  The night takes show x@1280 holds the night dancer at every size from 960 up (s4) and that no other size or model
  improves the still dancer (s2c).
- **Dark walls.** The lever is the enhancement chosen by Calibrate on the empty wall (one brighter gamma finds 93 %
  of outdoor-night's lost frames) -- not alternating settings, not a search pass, not a model swap.
- **Textured walls / duos in contact.** No multi-pass gain; the zoom and the lighter model are both harmful there.
- **Operator load.** Nothing here adds an operator step, and nothing removes one either: the measured gains all sit
  in stress scenes outside the WallDance wall (phone street footage, extreme-dark outdoor).

## 5. Plan (ranked)

| # | Step | Effort | Expected gain | Risk |
|---|---|---|---|---|
| 1 | **Record the 30 m demo** (two dancers, ~5 min, one empty take per lighting) and replay it at x@1280 vs x@1536 (both engines exist on the laptop) with the dancer-size line; process time and GPU temperature with TouchDesigner running | 1 h + the takes | the only evidence that can move the image size for the show | heat at 1536 (30 ms engine-only) |
| 2 | Keep 20 fps and x@1280; if heat forces a cut, test a lighter main pass on the venue's own takes first (the l model lost on s2c and the textured duo) | -- | -- | a blind swap |
| 3 | Look at the bridged frames (34-84 % of the frames without a fresh skeleton: the track is still updated, the point goes on): is "live = fresh skeleton" too strict for a far dancer with weak keypoints? No GPU cost | 0.5 day | point state, not presence | ghost slots if loosened carelessly |
| 4 | Shelve the zoom pass (branch). Revisit only for venues where the dancers stay under ~70 px in YOLO's input after the ROI and the image size -- then with the snapshot guard, no crop holding another slot, GPU crops through a batched 640 engine | 2-3 days | facade-like scenes only | ghost lock, duos in contact, +6.7 ms per dancer |

**Not to do:** 10-12 fps analysis; round-robin global settings; a verification pass for new tracks; tiles; a zoom
search without the empty-wall snapshot guard; a model swap not validated on the venue's own takes.

## 6. Not tested / caveats

- No footage at 30 m with two dancers yet: the small-dancer evidence is one phone-recorded street scene (facade),
  the extreme-dark outdoor take and input-size emulation; the night takes (s2c, s4) are one dancer at the night's
  distance. Record the 30 m demo and replay it (plan step 1).
- §3.7 runs on the PyTorch path (FP32) at sizes without dev37 engines; rows are comparable within a scene, not with
  the TRT rows of §3.6. Laptop costs for l@1536 / l@1920 / x@1920 are extrapolated by input area from the measured
  table, not measured.
- Offline passes use cv2's CLAHE + gamma (close to, not identical to, the GPU enhancer); the prototype's zoom runs
  on the CPU frame through PyTorch. Quality numbers carry, timings do not.
- Lost-frame truth is interpolated (it knows where the dancer reappears): the offline find rates are upper bounds;
  the end-to-end prototype is the real measure.
- E1 is the plain pipeline at 10 fps (frame-based parameters not rescaled).
- Warm-up (G) and tiles (F) were reasoned, not measured. Dark-crowd (YOLO sees almost nothing) could not be scored
  with a YOLO-based truth.

Tools: `tmp_analysis/multipass/` (mp_study / mp_report / mp_verify / gap_causes / e1_score / sizes / score and the
run scripts) and the prototype `application/src/core/zoom_pass.py` + its pipeline hook and replay keys (`zoom_mode`,
`zoom_k`, `zoom_size`, `zoom_conf`, `zoom_small_net_px`, `zoom_local`, `zoom_need_fg`, `crossval_enabled`), on this
branch only (default off: zoom off, cross-check on).
