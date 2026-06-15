# WallDance — running a new show

Operator procedure for taking WallDance from a cold rig to live OSC output. The
UI **is** this procedure: the phase rail (① → ⑥) is the checklist. Work left to
right; each phase has **one primary action** and a plain-language status line.

> ① Rig & Frame → ② Profile → ③ Aim → ④ Calibrate → ⑤ Verify → ⑥ Go Live

**Golden rule:** set up once, calibrate once (explicit, logged), then go live and
**only nudge a dial or two**. There is no continuous auto-tuning — live, you have
other things to handle. (Companion: [OPERATOR_V2.md](OPERATOR_V2.md) is the design
spec; this is the field checklist.)

---

## ① Rig & Frame
**You:** mount the camera + IR illuminator; **manual-focus** from the stage using
the phone monitor; draw the stage **ROI**; **paint known dead zones** (balcony,
reflective wall, doorway, bystander strip).
**System:** phone MJPEG preview + focus / uniformity / darkest-tile readout; the
**exclusion mask stays visible at all times** (dimmed cells), so you always know
what's blinded.
**Notes:** exclusion is **manual only** — paint what you know is dead; nothing is
auto-masked. Re-do only when the rig moves.

## ② Profile
**You:** pick or create a **Profile** — **Show** (night) or **Rehearsal** (day).
**System:** applies the whole bundle atomically, including IDS hardware settings.
**Notes:** one profile per lighting condition. Switching profiles re-applies its
saved calibration; the two live dials (⑥) re-center on the profile's seed.

## ③ Aim & empty scene  *(live, clear stage)*
**You:** press **Aim**; read the brightness / blur / scene report; adjust IR and
focus if flagged; press again until happy.
**System:** runs the exposure/gain **servo** (blur-capped) → sets gamma from scene
brightness → MOG2 variance → captures a **clean plate** of the empty stage.
**Notes:** idempotent — re-running replaces the scene settings in the profile. Do
this on a **clear stage** (no dancers).

## ④ Calibrate dancers
**You — preferred (A):** **record** one or more rehearsal runs → review the pool →
**Apply**.
**You — fallback (B), short install:** skip recording and run the dancer pass
**live on show-open** as the dancers enter; re-**Apply** as more evidence arrives.
**System:** pools dancer evidence → derives detection enhancement (CLAHE),
person-height + ratios, image size, a confidence seed, and the blur budget.
**Notes:** ④ warns if ③ never ran in this profile. The pool accumulates — Apply
once for a usable result, keep refining without disrupting a running show.

## ⑤ Verify
**You:** glance at **readiness**; optionally **dry-run** on the last recording.
**System:** a one-press **readiness check** — camera/FPS, TensorRT engine, OSC
reachability, calibration age, disk space, GPU temp — each shown **ok / warn /
fail** with a one-line reason. Nothing here ever blocks Go-Live; it's a
pre-flight glance. The optional **dry-run** replays the last recording through the
current settings and reports the track/drop summary so you can sanity-check the
config before the room fills.
**Notes:** a `warn` is informational (e.g. "calibration saved 30 h ago — consider
recalibrating after a re-rig"); a `fail` (e.g. OSC nothing-listening, disk
critically low) is worth fixing before the show.

## ⑥ Go Live
**You:** press **Go Live** (RUN). Monitor. Live, touch **only** these, and only if
needed:
- **Detection — Drops ↔ Ghosts** (Dial A): losing the dancer? raise it (catches
  more, may add ghosts). Too many ghosts? lower it. 50 = calibrated.
- **Detection — Gap bridging** (Dial B): dancer dropping out during fast / aerial
  moves? raise it to bridge YOLO gaps ("fewer drops"). A modest fine-tune; inert
  on clean scenes. 50 = calibrated.
- **Output — Box-clamp** (default ON): keeps the reported box a stable
  dancer-sized rectangle through detection gaps (stops box flicker). Output-only.
- **Output — smooth L** (default 1): box-size smoothness vs latency. L=1 = light,
  minimal latency. Raise for a calmer box at the cost of a little lag.
**System:** full YOLO + tracking + **OSC output** (`/walldance/dancer/*` +
`/walldance/count`); live health alerts (FPS drop, no detection, camera down, GPU
temp, over-cap). See [OSC_CONTRACT.md](OSC_CONTRACT.md) for the message contract.
**Notes:** RUN is what turns on YOLO + OSC; STANDBY is preview + enhancement only.

---

## Short-install fallback (no rehearsal time)
Rig + manual focus (①) → pick the Profile (②) → **Aim on the clear stage** (③) →
**Go Live**, and run the dancer calibration (④, mode B) on the **first live
moments** as the dancers enter; re-Apply as evidence accumulates. The clean plate
is grabbed from the opening dancer-free frames. Once live, just nudge the two
dials.

## The dials at a glance
| Control | Phase | Default | When to touch |
|---------|-------|---------|---------------|
| Drops ↔ Ghosts (Dial A) | ⑥ | 50 (calibrated) | losing the dancer / too many ghosts |
| Gap bridging (Dial B) | ⑥ | 50 (calibrated) | drops during fast/aerial moves |
| Box-clamp | ⑥ | ON | leave on (stable reported box) |
| Output smooth L | ⑥ | 1 | smoother box if a consumer wants it (adds latency) |

The raw numeric knobs behind the dials live in the **⚙ Advanced** drawer; moving a
raw knob re-anchors its dial at 50 (with a toast) so the dial and the applied
value never silently disagree.

## Recordings
The **🎞 Recordings** drawer (off the live surface) holds LIVE/REC + slots +
playback — used to capture rehearsal evidence for ④ and to dry-run in ⑤.
