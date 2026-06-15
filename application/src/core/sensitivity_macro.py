"""Detection dials (UX_PLAN.md U5 / OPERATOR_V2 §2.2).

The two legible operator dials, in the operator's own terms (not raw
confidence/var/motion units).  Both: 50 = the calibrated seed, higher = "catch
more dancer (may add ghosts)".  Pure functions; the app owns the seed/anchor.

**Dial A — "Drops ↔ Ghosts"** (``macro_to_settings``), confidence-led:
* primary: ``confidence`` — KNOBS E1 measured it as the master dial.  The dial
  interpolates from the seed to the ABSOLUTE corpus-measured best-τ bounds
  (SENS_CONF_MIN/MAX, Phase 2 ⑦) so the full measured range is reachable from
  any seed; a seed already at/outside a bound simply holds on that side.
* secondary, loose end only: ``varThreshold`` ramps toward the floor past the
  knee, waking MOG2 cold-blob recovery (Phase-C finding; safe post-Phase-F).

**Dial B — "Gap bridging"** (``bridge_macro_to_settings``), G1-validated:
* ``motion_sensitivity`` — MONOTONIC "fewer drops" (raising it cuts aerial
  drops at zero ghost/id cost; inert elsewhere).  Calibrated-seeded; spans
  SENS_BRIDGE_MIN/MAX (G1's grid).  A modest fine-tune, not a dramatic lever.
"""
from __future__ import annotations

from core.config import (
    SENS_CONF_MAX,
    SENS_CONF_MIN,
    SENS_VAR_FLOOR,
    SENS_VAR_KNEE,
    SENS_BRIDGE_MAX,
    SENS_BRIDGE_MIN,
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


def bridge_macro_to_settings(slider: float, sens_seed: float) -> dict:
    """Map the gap-bridging dial [0,100] to {'motion_sensitivity'} (Dial B).

    slider=50 returns the calibrated seed unchanged; >50 raises bridging toward
    SENS_BRIDGE_MAX (more gap-bridging → monotonically fewer drops), <50 lowers
    it toward SENS_BRIDGE_MIN.  G1-validated as a modest, calibrated-seeded
    fine-tune.
    """
    s = _clamp(float(slider), 0.0, 100.0)
    seed = float(sens_seed)
    if s >= 50.0:
        ms = seed + (s - 50.0) / 50.0 * max(0.0, SENS_BRIDGE_MAX - seed)
    else:
        ms = seed - (50.0 - s) / 50.0 * max(0.0, seed - SENS_BRIDGE_MIN)
    return {"motion_sensitivity": round(_clamp(ms, 0.0, 1.0), 3)}
