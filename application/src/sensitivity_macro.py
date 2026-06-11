"""Detection-sensitivity macro (UX_PLAN.md U5, mapping from KNOBS.md E2).

Collapses the drops↔ghosts dial into one operator slider, keeping the operator
in their own terms ("losing the dancer" → raise it, "too many ghosts" → lower
it) instead of confidence/var units.

* 50 = the calibrated seed (CALIBRATE / the dancer pool set it).
* primary: ``confidence`` — KNOBS E1 measured it as the master dial.  The dial
  interpolates from the seed to the ABSOLUTE corpus-measured best-τ bounds
  (SENS_CONF_MIN/MAX, Phase 2 ⑦) so the full measured range is reachable from
  any seed; a seed already at/outside a bound simply holds on that side.
* secondary, loose end only: ``varThreshold`` ramps toward the floor past the
  knee, waking MOG2 cold-blob recovery (Phase-C finding; safe post-Phase-F).

Pure function; the app owns the seed/anchor state.
"""
from __future__ import annotations

from config import (
    SENS_CONF_MAX,
    SENS_CONF_MIN,
    SENS_VAR_FLOOR,
    SENS_VAR_KNEE,
)

# Hard safety clamp; the span bounds sit strictly inside it.
CONF_BOUNDS = (0.10, 0.90)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def macro_to_settings(slider: float, conf_seed: float,
                      var_anchor: float) -> dict:
    """Map slider [0,100] to {'confidence', 'mog2_var_threshold'}.

    slider=50 returns the seeds unchanged; 0 is strictest (confidence at
    SENS_CONF_MAX), 100 loosest (SENS_CONF_MIN, var at the floor).
    """
    s = _clamp(float(slider), 0.0, 100.0)
    seed = float(conf_seed)
    if s <= 50.0:
        conf = seed + (50.0 - s) / 50.0 * max(0.0, SENS_CONF_MAX - seed)
    else:
        conf = seed - (s - 50.0) / 50.0 * max(0.0, seed - SENS_CONF_MIN)
    conf = round(_clamp(conf, *CONF_BOUNDS), 3)

    var = float(var_anchor)
    if s > SENS_VAR_KNEE and var > SENS_VAR_FLOOR:
        t = (s - SENS_VAR_KNEE) / (100.0 - SENS_VAR_KNEE)
        var = var - t * (var - SENS_VAR_FLOOR)
    return {"confidence": conf, "mog2_var_threshold": round(var, 1)}
