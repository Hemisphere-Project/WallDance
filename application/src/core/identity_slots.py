"""Identity-slot output layer (audit 2026-10 CONT-6) + per-slot One-Euro smoothing.

OUTPUT-ONLY.  Sits between ``finalize`` and OSC.  The tracker keeps minting
monotonic, unbounded track ids (~30 new ids/min in field logs); TouchDesigner
drops its video whenever an id vanishes.  This layer turns the tracker's
reported tracks into **N stable slots** (ids ``1..N``, N = ``max_dancers``) that
keep their id for the whole show:

States (per slot):

* ``live``      its bound tracker track was updated this frame;
* ``belt``      no track, but the IR waist-belt hook found the belt near the
                slot's prediction -> position = belt + learned offset;
* ``coasting``  no fresh measurement: hold/predict (constant velocity, decaying
                with ``vel_decay_tau_s``) for up to ``coast_s`` seconds;
* ``lost``      not emitted (keeps its id for the next dancer that appears).

Binding (each frame):

1. **Continuation**: a slot stays on its bound track while the tracker reports
   it (a teleport > ``jump_h`` x h unbinds it).  When the tracker keeps the
   track alive and updated but *hides* it (frozen gate: a still dancer fed by
   motion blobs -- the wall-hang case), the slot keeps following it for up to
   ``hidden_max_s`` while it stays within ``hidden_drift_h`` of its last
   reported position; a reported track in the slot's gate overrides that.
2. **Same track**: a ``lost`` slot whose former track is reported again takes
   it back at once (same tracker identity, no re-establishment).
3. **Rebind** (Hungarian on predicted position): a slot whose track vanished
   may take a *new established* track inside a gate that grows with the coast
   time (``gate_h`` + ``gate_growth_h_per_s`` x t, capped at ``gate_max_h``,
   all in units of the dancer's height).  Prefer keeping the binding: an
   established, unbound track can only take a slot whose own track is gone.
4. **Entry**: a ``lost`` slot can be taken by an established track *anywhere*
   (a dancer entering), reusing the same id; the nearest lost slot (by its last
   position) wins.  (``rebind_any_after_s`` / ``hidden_yield_s`` let a
   coasting / hidden-following slot jump to a track anywhere; both OFF by
   default -- on the replays they mostly jumped onto ghost tracks.)

Anti-ghost (a slot only binds to an **established** track):

* tracker-confirmed (it was reported), reported ``min_streak`` consecutive
  frames, ``hits >= min_hits``, a real skeleton within ``max_fss_bind`` frames,
  inside the allowed zone (exclusion mask), optionally a minimum travel
  (``min_travel_h``, off by default: wall dancers hang still);
* entry into a *lost* slot is stricter (``entry_*``: a skeleton within 3 s, so
  a blob-born track never opens a slot);
* a track within ``dup_bind_h`` x h of another emitting slot never binds (it is
  that dancer's duplicate);
* two emitting slots converging within ``merge_h`` x h are merged: a
  coasting/belt slot inside a live one after ``merge_coast_hold_s``, two live
  slots after ``merge_hold_s`` and only if one has no fresh skeleton (a duo in
  contact keeps both ids -- replay-measured, looser merges dropped real dancers);
* tracks beyond N are simply not emitted.

Smoothing: a One-Euro filter per **slot** (so it never restarts on tracker id
churn) on the centroid, speed normalised by the dancer height so the operator
knob is scene-independent; a slower one on the box size.  ``stability`` (0..1)
maps to (min_cutoff, beta) - see ``stability_params``.

Belt hook: ``BeltMeasure`` (``measure(slot_id, x, y, gate_px) ->
Optional[(x, y, quality)]``).  While a slot is live the belt->centroid offset is
learned (EMA); when it would coast, the hook is asked and a hit keeps the slot
alive in state ``belt`` (capped at ``belt_max_s`` without any track).

Pure: numpy (+ scipy's Hungarian when available).  Time is passed in by the
caller (seconds), so replays are frame-clocked and deterministic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

import numpy as np

try:  # scipy is a runtime dependency (tracker); keep a tiny fallback anyway
    from scipy.optimize import linear_sum_assignment as _lsa
except Exception:  # pragma: no cover - exercised only without scipy
    _lsa = None

STATE_LIVE = "live"
STATE_BELT = "belt"
STATE_COASTING = "coasting"
STATE_WEAK = "weak"
STATE_LOST = "lost"
EMITTING_STATES = (STATE_LIVE, STATE_BELT, STATE_WEAK, STATE_COASTING)

# Stability knob (0..1) -> One-Euro (min_cutoff [Hz], beta [Hz per h/s]),
# interpolated geometrically.  Tuned on replays (OSC_CONTRACT.md §D.3) with
# the tracker's EMA centroid as input: 0 = responsive (lag ~ the legacy stream,
# jitter at rest -25 %), 0.5 = default (+15..25 ms lag on fast moves, jitter at
# rest about halved), 1 = calm (jitter -70 %, +60..80 ms lag on fast moves).
STABILITY_MIN_CUTOFF = (3.0, 0.2)
STABILITY_BETA = (8.0, 2.0)
D_CUTOFF_HZ = 1.0              # derivative low-pass, fixed
HIP_MIN_CONF = 0.3             # YOLO hip keypoint confidence to anchor the belt search
SIZE_MIN_CUTOFF_HZ = 0.4       # box size: calm, follows real size changes slowly
SIZE_BETA = 0.0


def stability_params(stability: float) -> Tuple[float, float]:
    """``(min_cutoff_hz, beta)`` for the operator's Stability knob (0..1)."""
    s = min(1.0, max(0.0, float(stability)))
    lo_c, hi_c = STABILITY_MIN_CUTOFF
    lo_b, hi_b = STABILITY_BETA
    return (lo_c * (hi_c / lo_c) ** s, lo_b * (hi_b / lo_b) ** s)


def _alpha(cutoff_hz: float, dt: float) -> float:
    r = 2.0 * math.pi * max(1e-6, cutoff_hz) * dt
    return r / (r + 1.0)


class OneEuro2D:
    """One-Euro filter (Casiez et al. 2012) on a 2-vector, isotropic: one
    cutoff from the speed *magnitude*, the speed divided by ``scale`` (the
    dancer height in px) so ``beta`` is in Hz per (heights/s)."""

    def __init__(self, min_cutoff: float, beta: float,
                 d_cutoff: float = D_CUTOFF_HZ):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x: Optional[np.ndarray] = None
        self._dx = np.zeros(2)
        self._t: Optional[float] = None

    def reset(self, x=None, t: Optional[float] = None) -> None:
        self._x = None if x is None else np.asarray(x, dtype=np.float64).copy()
        self._dx = np.zeros(2)
        self._t = t

    @property
    def value(self) -> Optional[np.ndarray]:
        return None if self._x is None else self._x.copy()

    @property
    def speed(self) -> np.ndarray:
        """Filtered derivative (px/s)."""
        return self._dx.copy()

    def __call__(self, x, t: float, scale: float = 1.0) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if self._x is None or self._t is None:
            self._x = x.copy()
            self._dx = np.zeros(2)
            self._t = t
            return self._x.copy()
        dt = t - self._t
        if dt <= 1e-6:
            return self._x.copy()
        dx = (x - self._x) / dt
        a_d = _alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1.0 - a_d) * self._dx
        speed = float(np.hypot(self._dx[0], self._dx[1])) / max(1e-6, float(scale))
        cutoff = self.min_cutoff + self.beta * speed
        a = _alpha(cutoff, dt)
        self._x = a * x + (1.0 - a) * self._x
        self._t = t
        return self._x.copy()


class BeltMeasure(Protocol):
    """IR waist-belt hook: look for the belt near ``(x, y)`` (full-frame px)
    within ``gate_px``; return ``(x, y, quality)`` of the belt or None.

    Optional ``batch(queries) -> {slot_id: (x, y, quality) | None}`` with
    ``queries = [(slot_id, x, y, gate_px, band_w_px | None), ...]``: all of a
    frame's queries at once, so one belt answers at most one slot
    (``BeltDetector.detect_near``).  Without it the slot layer calls the hook
    once per slot."""

    def __call__(self, slot_id: int, x: float, y: float,
                 gate_px: float) -> Optional[Tuple[float, float, float]]: ...


@dataclass
class WeakMeasure:
    """Weak evidence for a coasting slot (never binds, never opens a slot)."""
    x: float
    y: float
    h: float
    kind: str = "weak"           # "warmup" (unreported tracker track) / "subtau" (YOLO under tau)


@dataclass
class SlotCandidate:
    """One tracker-reported track, as the slot layer sees it (original px)."""
    key: int                     # tracker track id
    x: float                     # filter input position
    y: float
    w: float                     # box size (box-clamped reported bbox)
    h: float
    hits: int = 0
    age: int = 0
    fss: int = 0                 # frames since a real skeleton
    tsu: int = 0
    src: Optional[str] = None
    zone_ok: bool = True         # outside the exclusion mask / inside the ROI
    payload: Any = None          # the ScaledTrack (keypoints for the output)
    hip: Optional[Tuple[float, float]] = None   # YOLO hip midpoint (belt height)
    fx: Optional[float] = None   # alternative One-Euro input (the tracker's raw KF centroid);
    fy: Optional[float] = None   # binding always uses (x, y) -- see SlotParams.filter_input


@dataclass
class SlotParams:
    max_dancers: int = 2
    coast_s: float = 2.0
    stability: float = 0.5
    # One-Euro input while live: "smoothed" = the binding position (the tracker's EMA
    # centroid, the shipped behaviour); "raw" = the bound track's raw Kalman centroid
    # (candidate fx/fy): removes the EMA's ~1 frame of lag before the One-Euro (review
    # 2026-10 §1.5: lag -30..-60 ms, jitter at rest x1.6); binding is unchanged;
    # "raw_skeleton" = raw only on frames where the bound track has a fresh YOLO skeleton
    # (blob-fed KF centroids jump on textured walls).
    filter_input: str = "smoothed"
    # -- establishment (anti-ghost) --
    min_streak: int = 3          # consecutive reported frames before a (re)bind
    min_hits: int = 8
    max_fss_bind: int = 100      # rebind near a coasting slot: a skeleton within 5 s
    entry_min_streak: int = 3    # stricter: entry into a lost slot (the tracker's
    entry_min_hits: int = 12     # own warm-up already took ~0.75 s)
    entry_max_fss: int = 60      # entry: a real skeleton within 3 s (never blob-born)
    min_travel_h: float = 0.0    # optional displacement evidence (0 = off)
    entry_min_travel_h: float = 0.0  # ... for an entry into a lost slot
    # -- hidden continuation: the bound track is alive and updated but the
    # tracker hides it (frozen gate: a still dancer fed by motion blobs) --
    hidden_max_s: float = 3.0
    hidden_drift_h: float = 1.5  # ... and it stays near its last reported position
    hidden_yield_s: float = 99.0  # ... and yields to an unclaimed established track (OFF:
                                  # replay-neutral, 0.5 s made white-duo worse)
    # -- gates, in dancer heights --
    gate_h: float = 1.0
    gate_growth_h_per_s: float = 2.0
    gate_max_h: float = 3.0
    rebind_any_after_s: float = 99.0  # "re-acquire anywhere" for a coasting slot: OFF
    # (it let a coasting slot jump onto ghost tracks: white-duo on-dancer 0.74 -> 0.64)
    snap_h: float = 1.0          # a rebind this far from the output snaps (no glide)
    jump_h: float = 2.0
    dup_bind_h: float = 0.5
    # Merges are deliberately conservative: on the duo replays (texture-duo:
    # dancers in contact) every looser merge dropped a REAL dancer (coverage
    # 0.90 -> 0.79); duplicates are mostly stopped at bind time (dup_bind_h).
    merge_h: float = 0.25
    merge_hold_s: float = 1.0        # two LIVE slots this close this long: merge ...
    merge_weak_fss: int = 5          # ... when one has no skeleton for > this many frames
    merge_coast_hold_s: float = 0.5  # a coasting/belt slot inside a live one
    # -- static ghosts (a YOLO-confirmed figure that never moves: a coat by a door,
    # a poster, a stain).  A reported track, or a slot since its entry, that stays
    # within static_travel_h x h for static_after_s marks a "static spot".  With the
    # guard on, a track at a static spot cannot enter a lost slot nor re-acquire a
    # coasting one; with static_yield on, when every slot emits, a slot that never
    # moved since its entry gives its id to an established, moving, skeleton-backed
    # track nobody holds (a real dancer the ghost was starving).  Continuation and
    # the gated rebind are never blocked, so a still dancer keeps the slot it has. --
    static_guard: bool = False
    static_yield: bool = False
    static_after_s: float = 5.0
    static_spread_h: float = 0.06    # a TRACK is static: p90 distance from its median position over the
                                     # last static_after_s < this x h (fixed figure 0.03 h; real dancers'
                                     # stillest 5 s windows >= 0.12 h on the corpus duos / wall-hang)
    static_travel_h: float = 0.15    # the newcomer that takes a static slot's id must have moved this much (x h)
    static_spot_r_h: float = 0.5
    static_spot_ttl_s: float = 30.0  # a spot no static track has confirmed for this long is forgotten
    yield_max_fss: int = 3           # the newcomer has a real skeleton this recent (frames)
    # -- "look deeper" near the last known position (Thomas 2026-10-06): a coasting slot
    # takes WEAK measurements near its prediction (a warm-up tracker track with a fresh
    # skeleton, a YOLO box under the confidence threshold) instead of holding a stale
    # point; the binding is unchanged, a weak-only hold is capped. --
    weak_enabled: bool = False
    weak_gate_h: float = 0.75
    weak_gate_growth_h_per_s: float = 2.0
    weak_gate_max_h: float = 2.5
    weak_max_s: float = 3.0          # weak-only hold cap since the last strong measurement
    # -- plausible appearances (Thomas 2026-10-06): a dancer is there from the start or
    # enters from a side; an entry far from the ROI border AND far from every slot's last
    # position, after the start-up window, needs a longer confirmation. --
    entry_plausibility: bool = False
    entry_startup_s: float = 3.0
    entry_border_h: float = 1.0      # within this x h of the bounds = "from a side"
    entry_near_h: float = 2.0        # within this x h of a slot's last position = a re-appearance
    entry_far_min_streak: int = 20   # interior surprise: ~1 s of consecutive reports ...
    entry_far_max_fss: int = 3       # ... and a fresh skeleton
    static_release_s: float = 0.0    # > 0: a slot static this long is dropped even without a newcomer
                                     # (the ghost point TD gets while a dancer is off the wall); OFF until
                                     # a still-dancer take proves real dancers never look static that long
    # -- motion model --
    vel_decay_tau_s: float = 0.25
    vel_alpha: float = 0.5
    max_dt_s: float = 0.25       # clamp for prediction / filters (stalls)
    # -- belt (IR retroreflective waist belt, worn at the hips) --
    belt_offset_alpha: float = 0.1
    belt_gate_h: float = 0.6     # search radius around the predicted belt, x h
    belt_hip_gate_h: float = 0.25   # ... around the YOLO hips while live
    belt_band_w_h: float = 0.22  # expected band width hint (25-35 px at 25 m)
    belt_min_learn: int = 5      # consistent live sightings before the belt may hold a slot
    belt_learn_tol_h: float = 0.15
    belt_max_s: float = 8.0      # belt-only hold cap (a glint must not hold a slot forever)
    belt_min_quality: float = 0.0


@dataclass
class SlotOutput:
    slot_id: int
    state: str
    x: float                     # smoothed centroid
    y: float
    w: float                     # smoothed box size
    h: float
    vx: float                    # px/s (filtered)
    vy: float
    age_s: float                 # seconds in the current state
    key: Optional[int]           # bound tracker id (None when coasting/belt)
    raw_x: float                 # unsmoothed slot position (RTS input at L>1)
    raw_y: float
    fss: Optional[int] = None
    payload: Any = None          # bound ScaledTrack when live, else last one


@dataclass
class _Slot:
    sid: int
    state: str = STATE_LOST
    key: Optional[int] = None
    pos: Optional[np.ndarray] = None       # unsmoothed position (meas or prediction)
    fpos: Optional[np.ndarray] = None      # this frame's One-Euro input (pos, or the raw centroid)
    vel: np.ndarray = field(default_factory=lambda: np.zeros(2))   # px/s
    wh: Optional[np.ndarray] = None
    last_meas_t: float = -1e9
    state_since: float = 0.0
    bound_since: float = 0.0
    belt_since: Optional[float] = None
    belt_offset: Optional[np.ndarray] = None
    belt_seen: int = 0                     # consistent live belt sightings
    fss: Optional[int] = None
    payload: Any = None
    ever: bool = False
    last_key: Optional[int] = None         # key when the slot went lost
    anchor: Optional[np.ndarray] = None    # last position the tracker REPORTED
    hidden_since: Optional[float] = None   # following a hidden bound track since
    entry_pos: Optional[np.ndarray] = None  # where the slot was entered (static-ghost test)
    entry_t: float = 0.0
    hist: List[Tuple[float, float, float]] = field(default_factory=list)   # live (t, x, y) over static_after_s
    static_since: Optional[float] = None    # first time the slot was found static (this binding)
    strong_t: float = -1e9                  # last LIVE / belt measurement (weak-hold cap)
    filt: Optional[OneEuro2D] = None
    size_filt: Optional[OneEuro2D] = None


def _assign(cost: np.ndarray) -> List[Tuple[int, int]]:
    """Min-cost assignment ignoring ``inf`` cells."""
    if cost.size == 0:
        return []
    big = 1e9
    c = np.where(np.isfinite(cost), cost, big)
    if _lsa is not None:
        rows, cols = _lsa(c)
        pairs = list(zip(rows.tolist(), cols.tolist()))
    else:  # greedy fallback
        pairs, used_r, used_c = [], set(), set()
        for idx in np.argsort(c, axis=None):
            r, k = divmod(int(idx), c.shape[1])
            if r in used_r or k in used_c:
                continue
            used_r.add(r)
            used_c.add(k)
            pairs.append((r, k))
    return [(r, k) for r, k in pairs if c[r, k] < big]


class IdentitySlots:
    """N stable output slots over the tracker's reported tracks (see module doc)."""

    def __init__(self, params: Optional[SlotParams] = None):
        self.p = params or SlotParams()
        self._slots: List[_Slot] = []
        self._t: Optional[float] = None
        self._streak: Dict[int, int] = {}
        self._first_pos: Dict[int, np.ndarray] = {}
        self._max_travel: Dict[int, float] = {}
        self._close_since: Dict[Tuple[int, int], float] = {}
        self._first_t: Dict[int, float] = {}
        self._hist: Dict[int, List[Tuple[float, float, float]]] = {}   # key -> [(t, x, y)] over static_after_s
        self._t0: Optional[float] = None
        self._bounds: Optional[Tuple[float, float, float, float]] = None
        self._entry_kind = "entry"
        # static spots: [x, y, h, t_last, kind]; kind "static" (soft: a track or a slot that
        # never moved) or "ghost" (hard: a static slot that had to yield to a moving dancer)
        self._spots: List[list] = []
        self.events: List[dict] = []          # this frame's bind/drop events
        self.counters = {"binds": 0, "rebinds": 0, "entries": 0,
                         "unbind_jump": 0, "merges": 0, "belt_frames": 0,
                         "hidden_frames": 0, "yields": 0, "static_blocked": 0}
        self._resize(self.p.max_dancers)

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------
    def _resize(self, n: int) -> None:
        n = max(1, int(n))
        while len(self._slots) < n:
            self._slots.append(self._new_slot(len(self._slots) + 1))
        del self._slots[n:]

    def _new_slot(self, sid: int) -> _Slot:
        mc, beta = stability_params(self.p.stability)
        return _Slot(sid=sid, filt=OneEuro2D(mc, beta),
                     size_filt=OneEuro2D(SIZE_MIN_CUTOFF_HZ, SIZE_BETA))

    def configure(self, **kw) -> None:
        """Update params live (operator knobs).  ``max_dancers`` resizes the
        slot table (dropping the highest ids); ``stability`` retunes filters."""
        for k, v in kw.items():
            if v is None or not hasattr(self.p, k):
                continue
            setattr(self.p, k, type(getattr(self.p, k))(v))
        if "max_dancers" in kw and kw["max_dancers"] is not None:
            self._resize(self.p.max_dancers)
        if "stability" in kw and kw["stability"] is not None:
            mc, beta = stability_params(self.p.stability)
            for s in self._slots:
                s.filt.min_cutoff, s.filt.beta = mc, beta

    def reset(self) -> None:
        n = len(self._slots)
        self._slots = [self._new_slot(i + 1) for i in range(n)]
        self._t = None
        self._streak.clear()
        self._first_pos.clear()
        self._max_travel.clear()
        self._close_since.clear()
        self._first_t.clear()
        self._hist.clear()
        self._t0 = None
        self._spots = []
        self.events = []

    @property
    def slots(self) -> List[_Slot]:
        return self._slots

    def states(self) -> Dict[int, str]:
        return {s.sid: s.state for s in self._slots}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _established(self, c: SlotCandidate, entry: bool) -> bool:
        p = self.p
        streak = self._streak.get(c.key, 0)
        if streak < (p.entry_min_streak if entry else p.min_streak):
            return False
        if int(c.hits or 0) < (p.entry_min_hits if entry else p.min_hits):
            return False
        if c.fss is not None and int(c.fss) > (p.entry_max_fss if entry else p.max_fss_bind):
            return False
        if not c.zone_ok:
            return False
        travel = p.entry_min_travel_h if entry else p.min_travel_h
        if travel > 0 and self._max_travel.get(c.key, 0.0) < travel * max(1.0, c.h):
            return False
        if p.static_guard and self._static_blocked(c, self._entry_kind if entry else "rebind"):
            self.counters["static_blocked"] += 1
            return False
        if entry and p.entry_plausibility and self._entry_kind == "entry" and self._surprising(c):
            if streak < p.entry_far_min_streak or (c.fss is not None and int(c.fss) > p.entry_far_max_fss):
                self.counters["implausible_wait"] = self.counters.get("implausible_wait", 0) + 1
                return False
        return True

    def _surprising(self, c: SlotCandidate) -> bool:
        """An entry nobody expects: after the start-up window, away from the ROI
        border, and away from every slot's last known position."""
        p = self.p
        if self._t is None or self._t0 is None or self._t - self._t0 < p.entry_startup_s:
            return False
        h = max(1.0, c.h)
        b = self._bounds
        if b is not None:
            x0, y0, x1, y1 = b
            if min(c.x - x0, x1 - c.x, c.y - y0, y1 - c.y) <= p.entry_border_h * h:
                return False
        for s in self._slots:
            if s.pos is not None and float(np.hypot(c.x - s.pos[0], c.y - s.pos[1])) <= p.entry_near_h * h:
                return False
        return True

    # ------------------------------------------------------------------
    # static ghosts
    # ------------------------------------------------------------------
    def _is_static_track(self, c: SlotCandidate, t: float) -> bool:
        p = self.p
        t0 = self._first_t.get(c.key)
        if t0 is None or t - t0 < p.static_after_s:
            return False
        return self._spread_small(self._hist.get(c.key) or [], c.h, t)

    def _is_static_slot(self, s: _Slot, t: float) -> bool:
        """The slot has been measured (live / belt) for the whole last static_after_s and its
        positions stayed within static_spread_h x h (p90 distance from their median)."""
        p = self.p
        if s.state == STATE_LOST or s.wh is None or t - s.entry_t < p.static_after_s:
            return False
        return self._spread_small(s.hist, float(s.wh[1]), t)

    def _spread_small(self, hist, h: float, t: float) -> bool:
        p = self.p
        if len(hist) < 5 or t - hist[0][0] < 0.8 * p.static_after_s:
            return False
        a = np.asarray([(x, y) for _t, x, y in hist], dtype=np.float64)
        med = np.median(a, axis=0)
        d = np.hypot(a[:, 0] - med[0], a[:, 1] - med[1])
        # enough samples over the window (a slot coasting most of the time is not "static")
        expect = p.static_after_s / max(1e-3, (hist[-1][0] - hist[0][0]) / max(1, len(hist) - 1))
        return len(hist) >= 0.6 * expect and float(np.percentile(d, 90)) < p.static_spread_h * max(1.0, h)

    def _add_spot(self, x: float, y: float, h: float, t: float, kind: str) -> None:
        r = self.p.static_spot_r_h
        for sp in self._spots:
            if math.hypot(sp[0] - x, sp[1] - y) < r * max(1.0, h, sp[2]):
                sp[3] = t
                if kind == "ghost":
                    sp[4] = "ghost"
                return
        self._spots.append([float(x), float(y), float(h), t, kind])

    def _at_spot(self, c: SlotCandidate, kinds: Tuple[str, ...]) -> bool:
        r = self.p.static_spot_r_h
        return any(sp[4] in kinds and math.hypot(sp[0] - c.x, sp[1] - c.y) < r * max(1.0, c.h, sp[2])
                   for sp in self._spots)

    def _static_blocked(self, c: SlotCandidate, kind: str) -> bool:
        """Entry into a lost slot is blocked only at proven ghost spots; a re-acquire
        anywhere or a yield takeover also at soft static spots, or by a static track."""
        static_now = self._t is not None and self._is_static_track(c, self._t)
        if kind == "entry":
            return self._at_spot(c, ("ghost",)) or static_now
        if kind == "rebind":
            return self._at_spot(c, ("ghost",)) or static_now
        return self._at_spot(c, ("static", "ghost")) or static_now

    def _event(self, kind: str, sid: int, **kw) -> None:
        self.events.append({"ev": kind, "slot": sid, **kw})

    def _bind(self, s: _Slot, c: SlotCandidate, t: float, kind: str) -> None:
        prev_key = s.key
        if s.state == STATE_LOST or s.pos is None:
            s.filt.reset()
            s.size_filt.reset()
            s.vel = np.zeros(2)
            s.belt_offset = None
            s.belt_seen = 0
            s.entry_pos = np.array([c.x, c.y], dtype=np.float64)
            s.entry_t = t
            s.hist = []
            s.static_since = None
            s.strong_t = t
            self.counters["entries"] += 1
        else:
            s.vel = np.zeros(2)          # a rebind is a new trajectory
            self.counters["rebinds"] += 1
            # Re-acquired far from where it was held: snap instead of a fake
            # sweep through space (a near rebind glides through the filter).
            out = s.filt.value
            if (out is not None and float(np.hypot(c.x - out[0], c.y - out[1]))
                    > self.p.snap_h * max(1.0, float(c.h))):
                s.filt.reset()
                s.size_filt.reset()
        self.counters["binds"] += 1
        s.key = c.key
        s.last_key = None
        s.bound_since = t
        s.hidden_since = None
        s.anchor = np.array([c.x, c.y], dtype=np.float64)
        self._event(kind, s.sid, key=c.key, prev=prev_key)

    def _measure(self, s: _Slot, x: np.ndarray, wh: Optional[np.ndarray],
                 t: float, dt: float, state: str, fpos: Optional[np.ndarray] = None) -> None:
        if s.pos is not None and dt > 0 and s.state in (STATE_LIVE, STATE_BELT, STATE_WEAK):
            v = (x - s.pos) / dt
            a = self.p.vel_alpha
            s.vel = a * v + (1.0 - a) * s.vel
        s.pos = x.copy()
        s.fpos = x.copy() if fpos is None else np.asarray(fpos, dtype=np.float64).copy()
        if self.p.static_guard or self.p.static_yield:
            s.hist.append((t, float(x[0]), float(x[1])))
            while s.hist and t - s.hist[0][0] > self.p.static_after_s:
                s.hist.pop(0)
        if wh is not None:
            s.wh = wh.copy()
        s.last_meas_t = t
        if s.state != state:
            s.state = state
            s.state_since = t
        s.ever = True

    # ------------------------------------------------------------------
    # main step
    # ------------------------------------------------------------------
    def bound_keys(self) -> List[int]:
        """Tracker ids currently (or last) bound to a non-lost slot -- the
        caller passes their hidden (unreported) state to ``update``."""
        return [s.key for s in self._slots if s.key is not None and s.state != STATE_LOST]

    def update(self, candidates: Sequence[SlotCandidate], t: float,
               belt: Optional[BeltMeasure] = None,
               hidden: Optional[Dict[int, SlotCandidate]] = None,
               weak: Optional[Sequence["WeakMeasure"]] = None,
               bounds: Optional[Tuple[float, float, float, float]] = None) -> List[SlotOutput]:
        """One output frame.  ``candidates`` = the tracker's REPORTED tracks;
        ``hidden`` = {track id: candidate} for bound tracks the tracker kept
        alive but did not report this frame (``bound_keys``)."""
        p = self.p
        hidden = hidden or {}
        self.events = []
        self._bounds = bounds
        if self._t0 is None:
            self._t0 = t
        dt_raw = 0.0 if self._t is None else max(0.0, t - self._t)
        dt = min(dt_raw, p.max_dt_s)
        self._t = t

        cands: Dict[int, SlotCandidate] = {}
        for c in candidates:
            if c.h is None or not np.isfinite(c.x) or not np.isfinite(c.y):
                continue
            cands[int(c.key)] = c
        # establishment bookkeeping (consecutive reported frames, travel)
        self._streak = {k: self._streak.get(k, 0) + 1 for k in cands}
        for k, c in cands.items():
            xy = np.array([c.x, c.y], dtype=np.float64)
            if k not in self._first_pos:
                self._first_pos[k] = xy
                self._first_t[k] = t
            if p.static_guard or p.static_yield:
                h = self._hist.setdefault(k, [])
                h.append((t, float(c.x), float(c.y)))
                while h and t - h[0][0] > p.static_after_s:
                    h.pop(0)
            d = float(np.linalg.norm(xy - self._first_pos[k]))
            self._max_travel[k] = max(self._max_travel.get(k, 0.0), d)
        for k in list(self._first_pos):
            if k not in cands:
                self._first_pos.pop(k, None)
                self._max_travel.pop(k, None)
                self._first_t.pop(k, None)
                self._hist.pop(k, None)

        # 1. prediction for every non-lost slot
        decay = math.exp(-dt / p.vel_decay_tau_s) if p.vel_decay_tau_s > 0 else 0.0
        pred: Dict[int, np.ndarray] = {}
        for s in self._slots:
            if s.state == STATE_LOST or s.pos is None:
                continue
            if s.state != STATE_LIVE:
                s.vel = s.vel * decay
            pred[s.sid] = s.pos + s.vel * dt

        used: set = set()
        measured: Dict[int, Tuple[np.ndarray, Optional[np.ndarray], str]] = {}
        provisional: set = set()     # slots measured only by a HIDDEN track

        # 2. continuation on the bound track (reported, else hidden-but-alive)
        for s in self._slots:
            if s.key is None:
                continue
            c = cands.get(s.key)
            is_hidden = False
            if c is None:
                c = hidden.get(s.key)
                is_hidden = c is not None
            if c is None:
                continue          # keep the (dangling) key: same-track rebind later
            xy = np.array([c.x, c.y], dtype=np.float64)
            h_ref = max(1.0, float(c.h))
            ref_pos = pred.get(s.sid, s.pos)
            if (ref_pos is not None and float(np.linalg.norm(xy - ref_pos))
                    > p.jump_h * h_ref + float(np.linalg.norm(s.vel)) * dt):
                self.counters["unbind_jump"] += 1
                self._event("unbind_jump", s.sid, key=s.key)
                s.key = None
                continue
            used.add(c.key)
            if int(c.tsu or 0) > 0:
                # Alive but not updated this frame (the tracker is only
                # predicting it): keep the binding, coast on our own prediction.
                continue
            if is_hidden:
                if s.state == STATE_LOST:
                    continue      # hidden tracks never (re)open a lost slot
                if s.hidden_since is None:
                    s.hidden_since = t
                drift = (float(np.linalg.norm(xy - s.anchor)) if s.anchor is not None else 0.0)
                if (t - s.hidden_since > p.hidden_max_s
                        or drift > p.hidden_drift_h * h_ref):
                    continue      # coast (then lost): a frozen ghost is bounded
                provisional.add(s.sid)
            else:
                s.hidden_since = None
                s.anchor = xy.copy()
            measured[s.sid] = (xy, np.array([c.w, c.h], dtype=np.float64), c)

        def emitting_positions(exclude_sid: int) -> List[Tuple[np.ndarray, float]]:
            out = []
            for o in self._slots:
                if o.sid == exclude_sid:
                    continue
                if o.sid in measured:
                    out.append((measured[o.sid][0], float(measured[o.sid][1][1])))
                elif o.state != STATE_LOST and o.sid in pred:
                    out.append((pred[o.sid], float(o.wh[1]) if o.wh is not None else 1.0))
            return out

        def is_duplicate(c: SlotCandidate, sid: int) -> bool:
            xy = np.array([c.x, c.y])
            for pos, h in emitting_positions(sid):
                if float(np.linalg.norm(xy - pos)) < p.dup_bind_h * max(h, c.h, 1.0):
                    return True
            return False

        # 3a. a lost slot's own former track is reported again: take it back
        # (same tracker identity -> same dancer; no re-establishment needed)
        for s in self._slots:
            if s.sid in measured or s.state != STATE_LOST or s.last_key is None:
                continue
            c = cands.get(s.last_key)
            if (c is None or c.key in used or int(c.tsu or 0) > 0
                    or is_duplicate(c, s.sid)):
                continue
            self._bind(s, c, t, "same_track")
            used.add(c.key)
            measured[s.sid] = (np.array([c.x, c.y], dtype=np.float64),
                               np.array([c.w, c.h], dtype=np.float64), c)

        # 3b. rebind slots whose track vanished, gated around their prediction.
        # A slot only following a hidden track is free too: a REPORTED,
        # established track in its gate beats it (the hidden one may be a
        # zombie left on the wall by the slow MOG2 background).
        free_slots = [s for s in self._slots
                      if s.state != STATE_LOST and s.sid in pred
                      and (s.sid not in measured or s.sid in provisional)]
        pool = [c for k, c in cands.items() if k not in used]
        if free_slots and pool:
            cost = np.full((len(free_slots), len(pool)), np.inf)
            for i, s in enumerate(free_slots):
                h_ref = max(1.0, float(s.wh[1]) if s.wh is not None else 1.0)
                gate = min(p.gate_max_h, p.gate_h + p.gate_growth_h_per_s
                           * max(0.0, t - s.last_meas_t)) * h_ref
                for j, c in enumerate(pool):
                    if not self._established(c, entry=False) or is_duplicate(c, s.sid):
                        continue
                    d = float(np.linalg.norm(np.array([c.x, c.y]) - pred[s.sid]))
                    if d <= gate:
                        cost[i, j] = d / h_ref
            for i, j in sorted(_assign(cost), key=lambda ij: cost[ij]):
                s, c = free_slots[i], pool[j]
                if is_duplicate(c, s.sid):   # a slot bound just before took this body
                    continue
                self._bind(s, c, t, "rebind")
                used.add(c.key)
                provisional.discard(s.sid)
                measured[s.sid] = (np.array([c.x, c.y], dtype=np.float64),
                                   np.array([c.w, c.h], dtype=np.float64), c)
        for sid in provisional:
            self.counters["hidden_frames"] += 1
        self._weak = {sid for sid in provisional}
        for sid, (_xy, _wh, c) in measured.items():
            if c.fss is not None and int(c.fss) > p.merge_weak_fss:
                self._weak.add(sid)

        # 4. entry: lost slots (then long-coasting slots) take established tracks anywhere
        pool = [c for k, c in cands.items() if k not in used]
        lost = [s for s in self._slots if s.state == STATE_LOST and s.sid not in measured]
        stale = [s for s in self._slots
                 if s.state != STATE_LOST and (
                     (s.sid not in measured and t - s.last_meas_t >= p.rebind_any_after_s)
                     # a slot only following a hidden (frozen-gated) track for a
                     # while yields to an established track nobody claims: the
                     # dancer most likely moved on, leaving a zombie behind
                     or (s.sid in provisional and s.hidden_since is not None
                         and t - s.hidden_since >= p.hidden_yield_s))]
        for group, kind in ((lost, "entry"), (stale, "reacquire")):
            pool = [c for c in pool if c.key not in used]
            if not group or not pool:
                continue
            self._entry_kind = kind
            cost = np.full((len(group), len(pool)), np.inf)
            for i, s in enumerate(group):
                for j, c in enumerate(pool):
                    if not self._established(c, entry=True) or is_duplicate(c, s.sid):
                        continue
                    if s.pos is not None:
                        cost[i, j] = float(np.linalg.norm(np.array([c.x, c.y]) - s.pos)) / max(1.0, c.h)
                    else:
                        cost[i, j] = 1e3 + s.sid    # never-used slot: lowest id first
            for i, j in sorted(_assign(cost), key=lambda ij: cost[ij]):
                s, c = group[i], pool[j]
                if is_duplicate(c, s.sid):   # a slot bound just before took this body
                    continue
                self._bind(s, c, t, kind)
                used.add(c.key)
                provisional.discard(s.sid)
                measured[s.sid] = (np.array([c.x, c.y], dtype=np.float64),
                                   np.array([c.w, c.h], dtype=np.float64), c)

        # 4b. static ghost yield: every slot emits, a moving skeleton-backed dancer
        # is left out, and a slot has not moved since its entry -> it gives way
        if p.static_yield:
            self._static_yield_round(cands, used, measured, pred, provisional, t, is_duplicate)

        # 5. belt queries (one batch: a belt answers at most one slot), then
        # apply measurements; belt / coast / lost for the rest
        belt_res = self._belt_round(belt, measured, pred, t) if belt is not None else {}
        weak_res = (self._weak_round(weak, measured, belt_res, pred, t)
                    if (p.weak_enabled and weak) else {})
        for s in self._slots:
            if s.sid in measured:
                xy, wh, c = measured[s.sid]
                if s.sid in belt_res:
                    self._learn_belt(s, xy, belt_res[s.sid])
                fpos = None
                use_raw = p.filter_input == "raw" or (
                    p.filter_input == "raw_skeleton" and c.fss is not None and int(c.fss) == 0)
                if use_raw and c.fx is not None and c.fy is not None \
                        and np.isfinite(c.fx) and np.isfinite(c.fy):
                    fpos = np.array([c.fx, c.fy], dtype=np.float64)
                self._measure(s, xy, wh, t, dt, STATE_LIVE, fpos=fpos)
                s.strong_t = t
                s.fss = c.fss
                s.payload = c.payload
                s.belt_since = None
                continue
            if s.state == STATE_LOST:
                continue
            got = None
            res = belt_res.get(s.sid)
            if res is not None:
                got = np.array(res[:2], dtype=np.float64) + s.belt_offset
            if got is not None:
                if s.belt_since is None:
                    s.belt_since = t
                self._measure(s, got, None, t, dt, STATE_BELT)
                s.strong_t = t
                s.fss = None
                self.counters["belt_frames"] += 1
                continue
            w = weak_res.get(s.sid)
            if w is not None:
                self._measure(s, np.array([w.x, w.y], dtype=np.float64), None, t, dt, STATE_WEAK)
                s.fss = None
                self.counters["weak_frames"] = self.counters.get("weak_frames", 0) + 1
                continue
            # no measurement: coast on the (decaying) prediction
            s.pos = pred[s.sid]
            s.fpos = s.pos
            if s.state != STATE_COASTING:
                s.state = STATE_COASTING
                s.state_since = t
            if t - s.last_meas_t > p.coast_s:
                self._drop(s, t, "coast_expired")

        # 6. never two slots on one dancer
        self._merge_converged(t)

        # 6b. static spots: tracks and slots that never moved (soft evidence)
        if p.static_guard or p.static_yield:
            for c in cands.values():
                if self._is_static_track(c, t):
                    self._add_spot(c.x, c.y, c.h, t, "static")
            for s in self._slots:
                if s.pos is not None and self._is_static_slot(s, t):
                    self._add_spot(float(s.pos[0]), float(s.pos[1]), float(s.wh[1]), t, "static")
                    if s.static_since is None:
                        s.static_since = t
                    if p.static_release_s > 0 and t - s.static_since >= p.static_release_s:
                        self._add_spot(float(s.pos[0]), float(s.pos[1]), float(s.wh[1]), t, "ghost")
                        self._event("static_release", s.sid, key=s.key)
                        self.counters["yields"] += 1
                        self._drop(s, t, "yield")
                elif s.state != STATE_LOST:
                    s.static_since = None
            self._spots = [sp for sp in self._spots if t - sp[3] <= p.static_spot_ttl_s]

        # 7. smoothing + outputs
        out: List[SlotOutput] = []
        for s in self._slots:
            if s.state == STATE_LOST or s.pos is None:
                continue
            h_ref = max(1.0, float(s.wh[1]) if s.wh is not None else 1.0)
            fx = s.filt(s.fpos if s.fpos is not None else s.pos, t, scale=h_ref)
            fwh = s.size_filt(s.wh if s.wh is not None else np.array([h_ref * 0.4, h_ref]),
                              t, scale=h_ref)
            v = s.filt.speed
            out.append(SlotOutput(
                slot_id=s.sid, state=s.state, x=float(fx[0]), y=float(fx[1]),
                w=float(fwh[0]), h=float(fwh[1]), vx=float(v[0]), vy=float(v[1]),
                age_s=round(max(0.0, t - s.state_since), 3), key=s.key,
                raw_x=float(s.pos[0]), raw_y=float(s.pos[1]),
                fss=s.fss if s.state == STATE_LIVE else None,
                payload=s.payload))
        return out

    # ------------------------------------------------------------------
    def _weak_round(self, weak, measured, belt_res, pred, t) -> Dict[int, "WeakMeasure"]:
        """Coasting slots look deeper near their prediction: each weak measurement goes to
        at most one slot (nearest, inside a gate growing with the time since the last
        measurement), never next to another emitting slot, and a weak-only hold is capped
        at weak_max_s since the slot's last strong measurement."""
        p = self.p
        seekers = [s for s in self._slots
                   if s.state != STATE_LOST and s.sid not in measured and s.sid not in belt_res
                   and s.sid in pred and t - s.strong_t <= p.weak_max_s]
        if not seekers:
            return {}
        others = []      # positions of the other emitting slots this frame
        for o in self._slots:
            if o.sid in measured:
                others.append((o.sid, measured[o.sid][0], float(measured[o.sid][1][1])))
            elif o.state != STATE_LOST and o.sid in pred:
                others.append((o.sid, pred[o.sid], float(o.wh[1]) if o.wh is not None else 1.0))
        ws = list(weak)
        cost = np.full((len(seekers), len(ws)), np.inf)
        for i, s in enumerate(seekers):
            h = max(1.0, float(s.wh[1]) if s.wh is not None else 1.0)
            gate = min(p.weak_gate_max_h, p.weak_gate_h + p.weak_gate_growth_h_per_s
                       * max(0.0, t - s.last_meas_t)) * h
            for j, w in enumerate(ws):
                d = float(np.hypot(w.x - pred[s.sid][0], w.y - pred[s.sid][1]))
                if d > gate:
                    continue
                if any(sid != s.sid and float(np.hypot(w.x - pos[0], w.y - pos[1]))
                       < p.dup_bind_h * max(oh, w.h, 1.0) for sid, pos, oh in others):
                    continue
                cost[i, j] = d / h
        out = {}
        for i, j in _assign(cost):
            out[seekers[i].sid] = ws[j]
        return out

    def _static_yield_round(self, cands, used, measured, pred, provisional, t, is_duplicate) -> None:
        p = self.p
        if any(s.state == STATE_LOST for s in self._slots):
            return      # a free slot exists: the entry step handles newcomers
        self._entry_kind = "yield"
        movers = [c for k, c in cands.items()
                  if k not in used and c.fss is not None and int(c.fss) <= p.yield_max_fss
                  and self._max_travel.get(k, 0.0) >= p.static_travel_h * max(1.0, c.h)
                  and self._established(c, entry=True)]
        if not movers:
            return
        static = sorted((s for s in self._slots if self._is_static_slot(s, t)),
                        key=lambda s: s.entry_t)
        for s in static:
            movers = [c for c in movers if c.key not in used and not is_duplicate(c, s.sid)]
            if not movers:
                return
            c = max(movers, key=lambda c: (int(c.hits or 0), -int(c.fss or 0)))
            if s.pos is not None:
                self._add_spot(float(s.pos[0]), float(s.pos[1]),
                               float(s.wh[1]) if s.wh is not None else c.h, t, "ghost")
            self._event("yield", s.sid, key=s.key, to=c.key)
            self.counters["yields"] += 1
            if s.key is not None:
                used.add(s.key)              # the ghost's track stays unbound this frame
            self._drop(s, t, "yield")
            measured.pop(s.sid, None)
            provisional.discard(s.sid)
            self._bind(s, c, t, "yield_entry")
            used.add(c.key)
            measured[s.sid] = (np.array([c.x, c.y], dtype=np.float64),
                               np.array([c.w, c.h], dtype=np.float64), c)

    def _drop(self, s: _Slot, t: float, why: str) -> None:
        self._event("lost", s.sid, why=why, key=s.key)
        s.state = STATE_LOST
        s.state_since = t
        s.last_key = s.key if why not in ("merged", "yield") else None
        s.key = None
        s.belt_since = None
        s.hidden_since = None

    def _merge_converged(self, t: float) -> None:
        p = self.p
        live = [s for s in self._slots if s.state != STATE_LOST and s.pos is not None]
        seen = set()
        for i in range(len(live)):
            for j in range(i + 1, len(live)):
                a, b = live[i], live[j]
                if a.state == STATE_LOST or b.state == STATE_LOST:
                    continue
                pair = (a.sid, b.sid)
                h = max(float(a.wh[1]) if a.wh is not None else 1.0,
                        float(b.wh[1]) if b.wh is not None else 1.0, 1.0)
                if float(np.linalg.norm(a.pos - b.pos)) >= p.merge_h * h:
                    continue
                seen.add(pair)
                a_live, b_live = a.state == STATE_LIVE, b.state == STATE_LIVE
                since = self._close_since.setdefault(pair, t)
                if a_live != b_live:
                    if t - since < p.merge_coast_hold_s:
                        continue
                    victim = b if a_live else a
                elif not a_live:
                    victim = a if a.last_meas_t < b.last_meas_t else b
                else:
                    # Two live slots on two tracker tracks: a duplicate only if
                    # one of them has no fresh skeleton (YOLO gives one box per
                    # body; two dancers in contact usually both keep theirs).
                    weak = getattr(self, "_weak", set())
                    if a.sid not in weak and b.sid not in weak:
                        self._close_since.pop(pair, None)
                        continue
                    if t - since < p.merge_hold_s:
                        continue
                    if (a.sid in weak) != (b.sid in weak):
                        victim = a if a.sid in weak else b
                    else:
                        victim = a if a.bound_since > b.bound_since else b
                self.counters["merges"] += 1
                self._drop(victim, t, "merged")
                self._close_since.pop(pair, None)
        for pair in list(self._close_since):
            if pair not in seen:
                self._close_since.pop(pair, None)

    def _belt_round(self, belt: BeltMeasure, measured: dict, pred: dict,
                    t: float) -> Dict[int, Optional[Tuple[float, float, float]]]:
        """This frame's belt queries: a live slot looks at its YOLO hips (else
        at centroid - learned offset) to learn the belt->centroid offset; a
        slot about to coast looks around its predicted belt position (only
        once the offset is confirmed, and for at most ``belt_max_s``)."""
        p = self.p
        queries = []
        for s in self._slots:
            if s.sid in measured:
                xy, wh, c = measured[s.sid]
                h = max(1.0, float(wh[1]))
                if c.hip is not None:
                    at, gate = np.asarray(c.hip, dtype=np.float64), p.belt_hip_gate_h * h
                elif s.belt_offset is not None:
                    at, gate = xy - s.belt_offset, p.belt_gate_h * h
                else:   # no hips yet: the belt sits a little below the centroid
                    at, gate = xy + np.array([0.0, 0.1 * h]), p.belt_gate_h * h
            elif (s.state != STATE_LOST and s.belt_offset is not None and s.wh is not None
                  and s.belt_seen >= p.belt_min_learn and s.sid in pred
                  and (s.belt_since is None or t - s.belt_since <= p.belt_max_s)):
                h = max(1.0, float(s.wh[1]))
                at, gate = pred[s.sid] - s.belt_offset, p.belt_gate_h * h
            else:
                continue
            queries.append((s.sid, float(at[0]), float(at[1]), float(gate),
                            p.belt_band_w_h * h))
        if not queries:
            return {}
        raw: Dict[int, Any] = {}
        try:
            batch = getattr(belt, "batch", None)
            if callable(batch):
                raw = dict(batch(queries) or {})
            else:
                for sid, x, y, gate, _bw in queries:
                    raw[sid] = belt(sid, x, y, gate)
        except Exception:   # a hook failure must never stop the show
            return {}
        out = {}
        for sid, *_rest in queries:
            res = raw.get(sid)
            if res is None:
                continue
            try:
                x, y = float(res[0]), float(res[1])
                q = float(res[2]) if len(res) > 2 else 1.0
            except (TypeError, ValueError, IndexError):
                continue
            if np.isfinite(x) and np.isfinite(y) and q >= p.belt_min_quality:
                out[sid] = (x, y, q)
        return out

    def _learn_belt(self, s: _Slot, xy: np.ndarray, res) -> None:
        """Belt->centroid offset (EMA) from a live sighting.  A sighting far
        from the learned offset resets the confirmation count (a glint near a
        moving dancer does not keep a consistent offset)."""
        off = xy - np.array(res[:2], dtype=np.float64)
        h = max(1.0, float(s.wh[1]) if s.wh is not None else 1.0)
        if s.belt_offset is None:
            s.belt_offset, s.belt_seen = off, 1
            return
        if float(np.linalg.norm(off - s.belt_offset)) > self.p.belt_learn_tol_h * h:
            s.belt_seen = 0
        else:
            s.belt_seen += 1
        a = self.p.belt_offset_alpha
        s.belt_offset = a * off + (1.0 - a) * s.belt_offset


def candidate_from_track(st, zone_ok: bool = True,
                         position: str = "smoothed") -> Optional[SlotCandidate]:
    """``SlotCandidate`` from a pipeline ``ScaledTrack`` (or any object with the
    same attributes).  ``position``: ``smoothed`` = the tracker's EMA centroid
    (the default: the KF centroid jumps between YOLO and blob measurements, and
    on the replays the One-Euro on the EMA centroid halves the jitter at rest
    for the same lag), ``raw`` = the KF centroid (``centroid_raw``); both fall
    back to the box centre."""
    bbox = np.asarray(getattr(st, "bbox"), dtype=np.float64)
    if bbox.shape[0] < 4 or not np.all(np.isfinite(bbox[:4])):
        return None
    xy = None
    if position == "raw":
        xy = getattr(st, "centroid_raw", None)
    if xy is None:
        xy = getattr(st, "smoothed_centroid", None)
    if xy is None:
        xy = (bbox[0] + bbox[2] / 2.0, bbox[1] + bbox[3] / 2.0)
    fss = getattr(st, "frames_since_skeleton", None)
    hip = None
    kp = getattr(st, "keypoints", None)
    kc = getattr(st, "confidence", None)
    if kp is not None and kc is not None and fss == 0 and len(kc) > 12:
        idx = [i for i in (11, 12) if float(kc[i]) > HIP_MIN_CONF]
        if idx:
            hip = (float(np.mean([kp[i][0] for i in idx])),
                   float(np.mean([kp[i][1] for i in idx])))
    raw = getattr(st, "centroid_raw", None)
    fx = fy = None
    if raw is not None and len(raw) >= 2 and np.all(np.isfinite(np.asarray(raw[:2], dtype=np.float64))):
        fx, fy = float(raw[0]), float(raw[1])
    return SlotCandidate(
        key=int(st.track_id), x=float(xy[0]), y=float(xy[1]), fx=fx, fy=fy,
        w=float(bbox[2]), h=float(bbox[3]),
        hits=int(getattr(st, "hits", None) or 0),
        age=int(getattr(st, "age", None) or 0),
        fss=None if fss is None else int(fss),
        tsu=int(getattr(st, "time_since_update", None) or 0),
        src=getattr(st, "feed_src", None), zone_ok=bool(zone_ok), payload=st, hip=hip)


def params_from_config(cfg: Dict[str, Any], base: Optional[SlotParams] = None) -> SlotParams:
    """``SlotParams`` from the operator config keys (``max_dancers``,
    ``stability``, ``coast_s``)."""
    p = base or SlotParams()
    if cfg.get("max_dancers") is not None:
        p.max_dancers = int(cfg["max_dancers"])
    if cfg.get("stability") is not None:
        p.stability = float(cfg["stability"])
    if cfg.get("coast_s") is not None:
        p.coast_s = float(cfg["coast_s"])
    if cfg.get("static_ghost_guard") is not None:
        p.static_guard = p.static_yield = bool(cfg["static_ghost_guard"])
    if cfg.get("static_release_s") is not None:
        p.static_release_s = max(0.0, float(cfg["static_release_s"]))
    if cfg.get("slot_filter_input") in ("smoothed", "raw", "raw_skeleton"):
        p.filter_input = str(cfg["slot_filter_input"])
    return p
