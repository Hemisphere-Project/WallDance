"""Fixed-lag / RTS output smoother (Track X, phase X-2).  OUTPUT-ONLY.

A per-track look-ahead buffer at the OSC/preview boundary.  Each processed frame
``N`` it ingests the reported (original-space) tracks and **releases frame
``N-L``**, using those ``L`` "future" frames to de-jitter the trajectory with a
Rauch--Tung--Striebel (RTS) fixed-interval smoother running on the **raw** KF
centroid (``ScaledTrack.centroid_raw`` = ``kf.x[:2]``, NOT the EMA
``smoothed_centroid`` — no cascaded filtering, the design rule of
``docs/TRACK_X_SMOOTHER.md`` §3).

The smoother runs its **own** small constant-velocity (CV) Kalman over the
buffered centroids; it never reads or mutates ``DancerTrack.kf`` or any tracker
state.  Output-only by construction → replay goldens are unaffected (the case-1
lesson).

This module is the X-2 CORE: trajectory de-jitter + box-size smoothing + the
dual-tap release mechanics with latency exactly ``L`` frames.  **Retroactive
bridge correction falls out of the RTS pass automatically** — a bridged gap of
length ``<= L`` that is re-anchored by a future real skeleton inside the window
is corrected in hindsight with no special case.  **Case-2 flying-ghost
SUPPRESSION is phase X-3 and is deliberately NOT implemented here**; in X-2 the
lagged id set equals the causal id set (every reported track is released
``L`` frames late).

Pure and unit-testable: feed synthetic ``SmootherInput`` trajectories, assert
smoothness / latency / gap reconstruction (see ``tests/test_output_smoother``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

import numpy as np

try:  # imported by bare name inside the app's src/ (see tests/conftest.py)
    from core.config import (MOTION_BRIDGE_NOISE_STAGES, TRACKER_MAX_AGE,
                             KEYPOINT_CONFIDENCE)
except Exception:  # pragma: no cover - allow standalone import in tooling
    MOTION_BRIDGE_NOISE_STAGES = [(10, 1.5), (30, 2.5), (80, 4.0)]
    TRACKER_MAX_AGE = 45
    KEYPOINT_CONFIDENCE = 0.3


# --- Constant-velocity model (dt = 1 frame).  State x = [px, py, vx, vy]. -----
_F = np.array(
    [[1.0, 0.0, 1.0, 0.0],
     [0.0, 1.0, 0.0, 1.0],
     [0.0, 0.0, 1.0, 0.0],
     [0.0, 0.0, 0.0, 1.0]],
    dtype=np.float64,
)
_H = np.array(
    [[1.0, 0.0, 0.0, 0.0],
     [0.0, 1.0, 0.0, 0.0]],
    dtype=np.float64,
)
_I4 = np.eye(4, dtype=np.float64)
_I2 = np.eye(2, dtype=np.float64)

# Discrete white-noise-acceleration process noise (dt = 1), per axis scaled by
# the process-noise spectral density q.  x and y are independent.
_Q_UNIT = np.array(
    [[0.25, 0.0,  0.5, 0.0],
     [0.0,  0.25, 0.0, 0.5],
     [0.5,  0.0,  1.0, 0.0],
     [0.0,  0.5,  0.0, 1.0]],
    dtype=np.float64,
)

# Production defaults.  process_var (accel spectral density, px/frame^2) and
# meas_var (centroid measurement variance, px^2 ≈ jitter std^2) are chosen so the
# CV filter follows realistic dancer motion while de-jittering; with a full
# ``L``-frame look-ahead the RTS pass phase-corrects, so the result is not
# sensitive to exact tuning.  R is inflated on non-real-skeleton frames via the
# tracker's MOTION_BRIDGE_NOISE_STAGES ratios (one source of truth).
_DEFAULT_PROCESS_VAR = 1.0
_DEFAULT_MEAS_VAR = 4.0
_INIT_VEL_VAR = 1.0e3  # large initial velocity uncertainty at track birth

# Case-2 flying-ghost suppression defaults (Track X §5.1, engine-agent decisions).
# A track is suppressed from the LAGGED tap only if its released frame is bridged
# AND it fails to re-acquire a SOLID skeleton (≥ K confident hits) in the look-
# ahead, with none recent.  Conservative (bias to KEEP real aerials); F1 is
# PROVISIONAL pending a per-track-labeled flying-ghost clip.
_CASE2_K = 2                                  # confident re-acquisitions required to keep
_CASE2_CONF_FLOOR = 2.0 * KEYPOINT_CONFIDENCE  # 0.6 — 2× the skeleton floor (a SOLID hit)
# Sustained-bridge gate: only a track bridged for >= this many frames is a
# suppression candidate.  A real aerial's drops are BRIEF (fss resets on each
# re-acquisition, incl. low-conf ones at the 0.3 floor); a flying ghost is bridged
# for a long unbroken run (fss grows).  Count-proxy-tuned on hangar-aerial so the
# real dancer's brief drops are never suppressed (drop_rate ≈ 0).
_CASE2_MIN_BRIDGE = 8


@dataclass
class SmootherInput:
    """One reported track at one frame (original-space)."""
    track_id: int
    centroid: np.ndarray            # RAW centroid (x, y) == ScaledTrack.centroid_raw
    wh: np.ndarray                  # reported box size (w, h)
    is_real_skeleton: bool          # frames_since_skeleton == 0
    frames_since_skeleton: int = 0  # staleness → R inflation stage
    max_kpt_conf: float = 0.0       # max per-keypoint confidence (case-2 "confident" test)
    payload: Any = None             # opaque; carried through to the release


@dataclass
class SmootherOutput:
    """One released (lagged, smoothed) track for frame ``step``."""
    track_id: int
    step: int               # the released frame's ingest step (current_step - L in steady state)
    centroid: np.ndarray    # RTS-smoothed (x, y)
    velocity: np.ndarray    # RTS-smoothed (vx, vy) — the de-jittered velocity
    wh: np.ndarray          # smoothed box size (w, h)
    is_real_skeleton: bool  # of the released frame
    payload: Any            # the released frame's payload


@dataclass
class _Node:
    step: int
    z: np.ndarray        # measurement centroid (2,)
    is_real: bool
    fss: int             # frames_since_skeleton (sustained-bridge gate)
    wh: np.ndarray       # box size (2,)
    weight: float        # box-size weight (1 / noise_mult)
    x_post: np.ndarray   # filtered state (4,)
    P_post: np.ndarray   # filtered covariance (4, 4)
    max_kpt_conf: float  # max per-keypoint conf (case-2 confident test)
    payload: Any


class _TrackFilter:
    """Persistent forward CV-Kalman + trailing-window node buffer for one track.

    The forward filter accumulates the track's full history (so the newest node
    is fully filtered); a trailing window of nodes is kept so the RTS backward
    pass can smooth the oldest buffered frame using its look-ahead.
    """

    __slots__ = ("nodes", "x_post", "P_post", "last_step", "_q", "_r",
                 "_k", "_floor", "_min_bridge")

    def __init__(self, process_var: float, meas_var: float,
                 case2_k: int = _CASE2_K, case2_conf_floor: float = _CASE2_CONF_FLOOR,
                 case2_min_bridge: int = _CASE2_MIN_BRIDGE):
        self.nodes: List[_Node] = []
        self.x_post: Optional[np.ndarray] = None
        self.P_post: Optional[np.ndarray] = None
        self.last_step: int = -1
        self._q = float(process_var)
        self._r = float(meas_var)
        self._k = int(case2_k)
        self._floor = float(case2_conf_floor)
        self._min_bridge = max(1, int(case2_min_bridge))

    # -- forward pass ---------------------------------------------------------
    def ingest(self, step: int, inp: SmootherInput, noise_mult: float) -> None:
        z = np.asarray(inp.centroid, dtype=np.float64).reshape(2)
        R = (self._r * max(1.0, noise_mult)) * _I2
        if self.x_post is None:
            # Birth: seed position from the measurement, velocity unknown.
            self.x_post = np.array([z[0], z[1], 0.0, 0.0], dtype=np.float64)
            self.P_post = np.diag(
                [self._r, self._r, _INIT_VEL_VAR, _INIT_VEL_VAR]).astype(np.float64)
        else:
            Q = self._q * _Q_UNIT
            x_prior = _F @ self.x_post
            P_prior = _F @ self.P_post @ _F.T + Q
            S = _H @ P_prior @ _H.T + R
            K = P_prior @ _H.T @ _inv(S)
            y = z - _H @ x_prior
            self.x_post = x_prior + K @ y
            self.P_post = (_I4 - K @ _H) @ P_prior

        self.nodes.append(_Node(
            step=step,
            z=z,
            is_real=bool(inp.is_real_skeleton),
            fss=int(inp.frames_since_skeleton),
            wh=np.asarray(inp.wh, dtype=np.float64).reshape(2),
            weight=1.0 / max(1.0, noise_mult),
            x_post=self.x_post.copy(),
            P_post=self.P_post.copy(),
            max_kpt_conf=float(inp.max_kpt_conf),
            payload=inp.payload,
        ))
        self.last_step = step

    # -- backward (RTS) pass over the current window --------------------------
    def _rts_smoothed_oldest(self) -> np.ndarray:
        """RTS over the buffered window → smoothed FULL state [px,py,vx,vy] of
        nodes[0] (caller slices position / velocity)."""
        n = len(self.nodes)
        if n == 1:
            return self.nodes[0].x_post.copy()
        Q = self._q * _Q_UNIT
        xs_next = self.nodes[-1].x_post
        for k in range(n - 2, -1, -1):
            node = self.nodes[k]
            x_prior_next = _F @ node.x_post
            P_prior_next = _F @ node.P_post @ _F.T + Q
            C = node.P_post @ _F.T @ _inv(P_prior_next)
            xs_next = node.x_post + C @ (xs_next - x_prior_next)
        return xs_next.copy()

    def _smoothed_wh(self) -> np.ndarray:
        """Box size for the oldest frame: confidence-weighted window mean
        (real-skeleton frames weighted highest; bridged frames down-weighted via
        the same MOTION_BRIDGE_NOISE_STAGES ratios)."""
        wsum = 0.0
        acc = np.zeros(2, dtype=np.float64)
        for node in self.nodes:
            acc += node.weight * node.wh
            wsum += node.weight
        return acc / wsum if wsum > 0 else self.nodes[0].wh.copy()

    def _should_suppress(self, lag: int) -> bool:
        """Case-2 flying-ghost predicate (Track X §5.1) for the oldest node R.

        Suppress R from the lagged tap iff ALL hold:
          (1) R is deep in a SUSTAINED bridge (frames_since_skeleton >= min_bridge)
              — a brief drop of a real dancer (small fss, re-acquires) is exempt;
              only a track bridged for a long unbroken run is a suppression
              candidate (a true flying ghost never re-acquires, so its fss grows);
          (2) the look-ahead nodes (step ∈ (R … N]) contain < K CONFIDENT real
              skeletons (confident := is_real AND max_kpt_conf > floor);
          (3) AND none of those confident nodes is within the last ⌈lag/3⌉ steps
              of N (recency — a recent solid re-acquisition keeps the track).

        Output-only: suppression drops the EMIT; the node is still popped and the
        forward filter is untouched (a later window that re-acquires emits)."""
        if self.nodes[0].fss < self._min_bridge:
            return False                       # (1) not a sustained-enough bridge
        lookahead = self.nodes[1:]
        if not lookahead:
            return False
        confident_steps = [nd.step for nd in lookahead
                           if nd.is_real and nd.max_kpt_conf > self._floor]
        if len(confident_steps) >= self._k:
            return False                       # (2) enough solid re-acquisitions
        recency = max(1, lag // 3)
        newest = self.nodes[-1].step
        if any(s > newest - recency for s in confident_steps):
            return False                       # (3) a recent solid hit → keep
        return True

    def _release_oldest(self, suppress: bool, lag: int) -> Optional[SmootherOutput]:
        if suppress and self._should_suppress(lag):
            self.nodes.pop(0)                  # advance the buffer, emit nothing
            return None
        node = self.nodes[0]
        state = self._rts_smoothed_oldest()  # full [px,py,vx,vy]
        wh = self._smoothed_wh()
        out = SmootherOutput(
            track_id=-1,  # filled by caller
            step=node.step,
            centroid=state[:2].copy(),
            velocity=state[2:4].copy(),
            wh=wh,
            is_real_skeleton=node.is_real,
            payload=node.payload,
        )
        self.nodes.pop(0)
        return out

    def drain_overdue(self, lag: int, suppress: bool) -> List[SmootherOutput]:
        """Release every node that now has >= ``lag`` future frames (steady
        state releases exactly one; a live ``L`` decrease releases the backlog).
        Case-2-suppressed releases advance the buffer but are not emitted."""
        out: List[SmootherOutput] = []
        while len(self.nodes) > lag:
            rel = self._release_oldest(suppress, lag)
            if rel is not None:
                out.append(rel)
        return out

    def flush_one(self, lag: int, suppress: bool) -> Optional[SmootherOutput]:
        """Release the oldest remaining node even with < ``lag`` look-ahead —
        used to flush a dying track's tail (§12 lifecycle)."""
        if not self.nodes:
            return None
        return self._release_oldest(suppress, lag)

    def empty(self) -> bool:
        return not self.nodes


def _inv(m: np.ndarray) -> np.ndarray:
    """Robust matrix inverse (tiny diagonal jitter guards near-singular P)."""
    try:
        return np.linalg.inv(m)
    except np.linalg.LinAlgError:
        return np.linalg.inv(m + 1e-9 * np.eye(m.shape[0]))


class OutputSmoother:
    """Per-track fixed-lag / RTS smoother for the lagged OSC tap (Track X §2).

    Call :meth:`process` once per processed frame with the reported tracks; it
    returns the tracks released this frame (the lagged tap), ``L`` frames late.
    ``L`` may change live between calls (the operator slider) — a decrease
    releases now-overdue frames immediately; an increase pauses releases until
    the buffers refill (a one-time small discontinuity, §12).
    """

    def __init__(self, lag: int = 2, *,
                 process_var: float = _DEFAULT_PROCESS_VAR,
                 meas_var: float = _DEFAULT_MEAS_VAR,
                 max_age: int = TRACKER_MAX_AGE,
                 case2_k: int = _CASE2_K,
                 case2_conf_floor: float = _CASE2_CONF_FLOOR,
                 case2_min_bridge: int = _CASE2_MIN_BRIDGE):
        self._lag = max(1, int(lag))
        self._process_var = float(process_var)
        self._meas_var = float(meas_var)
        self._max_age = int(max_age)
        self._case2_k = int(case2_k)
        self._case2_conf_floor = float(case2_conf_floor)
        self._case2_min_bridge = max(1, int(case2_min_bridge))
        self._tracks: dict[int, _TrackFilter] = {}
        self._step = -1

    @property
    def lag(self) -> int:
        return self._lag

    @property
    def step(self) -> int:
        """The current (most recently ingested) frame step."""
        return self._step

    def reset(self) -> None:
        """Drop all buffered state (e.g. on a disable→enable toggle)."""
        self._tracks.clear()
        self._step = -1

    def _noise_mult(self, frames_since_skeleton: int) -> float:
        """Mirror the tracker's MOTION_BRIDGE_NOISE_STAGES R-inflation ladder.

        real skeleton (0) → 1.0; otherwise the staged multiplier indexed by how
        stale the track is (one source of truth with the tracker)."""
        if frames_since_skeleton <= 0:
            return 1.0
        for threshold, mult in MOTION_BRIDGE_NOISE_STAGES:
            if frames_since_skeleton <= threshold:
                return float(mult)
        return float(MOTION_BRIDGE_NOISE_STAGES[-1][1])

    def process(self, inputs: List[SmootherInput],
                lag: Optional[int] = None,
                suppress: bool = False) -> List[SmootherOutput]:
        """Ingest this frame's reported tracks; return the releases (frame N-L).

        ``suppress`` engages case-2 flying-ghost suppression (Track X §5.1): a
        released track that is bridged AND fails to re-acquire a solid skeleton
        in its look-ahead is dropped from the lagged tap (the id is simply absent
        that frame).  Off → the lagged id set equals the causal id set."""
        if lag is not None:
            self._lag = max(1, int(lag))
        self._step += 1
        step = self._step
        releases: List[SmootherOutput] = []
        present = set()

        for inp in inputs:
            tid = int(inp.track_id)
            present.add(tid)
            tf = self._tracks.get(tid)
            if tf is None or (tf.last_step >= 0 and step - tf.last_step > 1):
                # Fresh segment: a new track, OR the same id reappearing after a
                # non-contiguous reporting gap.  The CV forward pass assumes dt=1
                # per ingest, so never stitch a post-gap measurement onto stale
                # nodes as if one frame elapsed — start a clean window.
                tf = _TrackFilter(self._process_var, self._meas_var,
                                  self._case2_k, self._case2_conf_floor,
                                  self._case2_min_bridge)
                self._tracks[tid] = tf
            mult = self._noise_mult(int(inp.frames_since_skeleton))
            tf.ingest(step, inp, mult)
            for out in tf.drain_overdue(self._lag, suppress):
                out.track_id = tid
                releases.append(out)

        # Absent tracks: flush their tail (L-late) then prune (§12 lifecycle).
        for tid in list(self._tracks):
            if tid in present:
                continue
            tf = self._tracks[tid]
            out = tf.flush_one(self._lag, suppress)
            if out is not None:
                out.track_id = tid
                releases.append(out)
            if tf.empty() or (step - tf.last_step) > (self._lag + self._max_age):
                del self._tracks[tid]

        return releases
