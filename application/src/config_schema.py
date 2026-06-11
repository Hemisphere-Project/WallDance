"""Project-config schema v2: lighting profiles + validation (UX_PLAN U2).

A v2 config separates *lighting-coupled* values (different between a daytime
rehearsal and a night+IR show) from *shared* values (geometry, model, OSC):

    {
      "config_version": 2,
      "active_profile": "show",
      "profiles": {
        "show":      { ...PROFILE_KEYS... },
        "rehearsal": { ...PROFILE_KEYS... }
      },
      ...shared keys...
    }

The rest of the app keeps consuming **flat** dicts: load = ``flatten()`` (the
active profile merged over shared keys), save = ``structure()`` (current flat
values split back, carrying the inactive profile bundle along). v1 flat
configs are migrated by wrapping their profile keys as ``show`` and seeding
``rehearsal`` with a copy, so a freshly migrated project behaves exactly as
before until the second profile is calibrated.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

SCHEMA_VERSION = 2
PROFILE_NAMES = ("show", "rehearsal")
DEFAULT_PROFILE = "show"

# Lighting-coupled keys (stored per profile). Everything else is shared.
PROFILE_KEYS = frozenset({
    "ids_gain_db",
    "ids_exposure_us",
    "gamma",
    "clahe_clip",
    "mog2_var_threshold",
    "mog2_scale",
    "exclusion_grid",
    "exclusion_cells",
    "exclusion_manual_add",
    "exclusion_manual_remove",
    "confidence",
    "sensitivity",
    "sensitivity_conf_seed",
    "sensitivity_var_anchor",
})

_STRUCTURE_KEYS = ("config_version", "active_profile", "profiles")

# key -> (min, max) numeric clamps, applied on load.
_RANGES = {
    "confidence": (0.05, 0.95),
    "gamma": (0.2, 4.0),
    "clahe_clip": (0.5, 10.0),
    "mog2_var_threshold": (4.0, 256.0),
    "mog2_scale": (0.25, 1.0),
    "ids_gain_db": (0.0, 48.0),
    "ids_exposure_us": (50.0, 1_000_000.0),
    "person_height_px": (10, 1500),
    "person_height_min_ratio": (0.05, 1.0),
    "person_height_max_ratio": (1.0, 10.0),
    "tracker_max_age": (1, 300),
    "max_persons": (1, 32),
    "motion_sensitivity": (0.0, 1.0),
    "brightness_threshold": (0, 255),
    "osc_port": (1, 65535),
    "blur_budget_ms": (5.0, 60.0),
    "sensitivity": (0.0, 100.0),
    "sensitivity_conf_seed": (0.05, 0.95),
    "sensitivity_var_anchor": (4.0, 256.0),
}

_IMGSZ_PRESETS = (640, 800, 960, 1280, 1536, 1920)


def split_profile(flat: Dict) -> Tuple[Dict, Dict]:
    """Split a flat config into (shared, profile-bundle)."""
    shared = {k: v for k, v in flat.items()
              if k not in PROFILE_KEYS and k not in _STRUCTURE_KEYS}
    profile = {k: flat[k] for k in PROFILE_KEYS if k in flat}
    return shared, profile


def migrate(config: Dict) -> Dict:
    """Return a v2 structured config; v1 flat configs are wrapped, v2 normalized."""
    cfg = dict(config)
    if cfg.get("config_version", 1) >= SCHEMA_VERSION and isinstance(cfg.get("profiles"), dict):
        profiles = {name: dict(bundle) for name, bundle in cfg["profiles"].items()
                    if isinstance(bundle, dict)}
        seed = profiles.get(DEFAULT_PROFILE) or next(iter(profiles.values()), {})
        for name in PROFILE_NAMES:
            profiles.setdefault(name, dict(seed))
        cfg["profiles"] = profiles
        if cfg.get("active_profile") not in PROFILE_NAMES:
            cfg["active_profile"] = DEFAULT_PROFILE
        cfg["config_version"] = SCHEMA_VERSION
        return cfg

    shared, profile = split_profile(cfg)
    out = dict(shared)
    out["config_version"] = SCHEMA_VERSION
    out["active_profile"] = DEFAULT_PROFILE
    # Seed both profiles identically: a migrated project behaves exactly as
    # before until the second condition is calibrated.
    out["profiles"] = {name: dict(profile) for name in PROFILE_NAMES}
    return out


def flatten(config: Dict) -> Dict:
    """Flat view of a config (active profile merged over shared keys).

    Accepts v1 or v2 input. Does NOT validate — pair with ``validate_flat``
    so the caller can surface the warnings.
    """
    cfg = migrate(config)
    active = cfg.get("active_profile", DEFAULT_PROFILE)
    flat = {k: v for k, v in cfg.items() if k not in _STRUCTURE_KEYS}
    flat.update(cfg["profiles"].get(active, {}))
    return flat


def structure(flat: Dict, profiles: Dict[str, Dict], active: str) -> Dict:
    """Build a v2 payload: current flat values become the active profile,
    the other bundles are carried along unchanged."""
    if active not in PROFILE_NAMES:
        active = DEFAULT_PROFILE
    shared, current = split_profile(flat)
    out = dict(shared)
    out["config_version"] = SCHEMA_VERSION
    out["active_profile"] = active
    bundles = {name: dict(bundle) for name, bundle in (profiles or {}).items()
               if isinstance(bundle, dict)}
    for name in PROFILE_NAMES:
        bundles.setdefault(name, dict(current))
    bundles[active] = current
    out["profiles"] = bundles
    return out


def validate_flat(flat: Dict) -> Tuple[Dict, List[str]]:
    """Clamp out-of-range numerics; return (clamped, warnings).

    Non-numeric junk in a numeric key is dropped (falls back to the in-app
    default) rather than crashing the load.
    """
    out = dict(flat)
    warnings: List[str] = []
    for key, (lo, hi) in _RANGES.items():
        if key not in out or out[key] is None:
            continue
        try:
            val = float(out[key])
        except (TypeError, ValueError):
            warnings.append(f"{key}: invalid value {out[key]!r} dropped")
            out.pop(key)
            continue
        clamped = min(max(val, lo), hi)
        if clamped != val:
            warnings.append(f"{key}: {val} clamped to {clamped} (range {lo}-{hi})")
        if isinstance(flat[key], bool) or not isinstance(flat[key], (int, float)):
            out[key] = clamped
        elif isinstance(flat[key], int) and float(clamped).is_integer():
            out[key] = int(clamped)
        else:
            out[key] = clamped

    if "yolo_imgsz" in out and out["yolo_imgsz"] is not None:
        try:
            sz = int(out["yolo_imgsz"])
            nearest = min(_IMGSZ_PRESETS, key=lambda p: abs(p - sz))
            if nearest != sz:
                warnings.append(f"yolo_imgsz: {sz} snapped to preset {nearest}")
            out["yolo_imgsz"] = nearest
        except (TypeError, ValueError):
            warnings.append(f"yolo_imgsz: invalid value {out['yolo_imgsz']!r} dropped")
            out.pop("yolo_imgsz")

    _validate_cross_field(out, warnings)
    return out, warnings


def _validate_cross_field(out: Dict, warnings: List[str]) -> None:
    """Structural checks the apply path depends on (it must never crash).

    Mutates `out` in place; appends to `warnings`.
    """
    # Height ratios: _RANGES already pins min <= 1.0 <= max, but keep the
    # ordering invariant explicit in case the ranges ever loosen.
    lo, hi = out.get("person_height_min_ratio"), out.get("person_height_max_ratio")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
        out["person_height_min_ratio"], out["person_height_max_ratio"] = hi, lo
        warnings.append(
            f"person_height_min_ratio {lo} > person_height_max_ratio {hi}: swapped")

    # ROI: the apply path int()-coerces these raw; drop anything that would
    # crash it or describe an impossible rectangle (apply falls back to the
    # in-app current/default ROI for missing keys).
    for key in ("roi_x", "roi_y", "roi_w", "roi_h", "roi_source_w", "roi_source_h"):
        if key not in out or out[key] is None:
            continue
        try:
            val = int(float(out[key]))
        except (TypeError, ValueError):
            warnings.append(f"{key}: invalid value {out[key]!r} dropped")
            out.pop(key)
            continue
        minimum = 0 if key in ("roi_x", "roi_y") else 1
        if val < minimum:
            warnings.append(f"{key}: {out[key]!r} dropped (must be >= {minimum})")
            out.pop(key)
        else:
            out[key] = val

    # Exclusion mask: set_cells() indexes grid[0]/grid[1] and each cell's
    # [0]/[1]; a malformed shape crashes the project load. Drop the bundle
    # whole (incl. the manual overlays) rather than half-repairing it.
    def _cell_list_ok(cells):
        if cells is None:
            return True
        if not isinstance(cells, (list, tuple)):
            return False
        for c in cells:
            if not isinstance(c, (list, tuple)) or len(c) != 2:
                return False
            try:
                int(c[0]), int(c[1])
            except (TypeError, ValueError):
                return False
        return True

    def _mask_ok():
        grid = out.get("exclusion_grid")
        if grid is not None:
            if not isinstance(grid, (list, tuple)) or len(grid) != 2:
                return False
            try:
                if int(grid[0]) < 1 or int(grid[1]) < 1:
                    return False
            except (TypeError, ValueError):
                return False
        return all(_cell_list_ok(out.get(k)) for k in (
            "exclusion_cells", "exclusion_manual_add", "exclusion_manual_remove"))

    if not _mask_ok():
        warnings.append("exclusion_grid/exclusion_cells: malformed mask dropped")
        out.pop("exclusion_grid", None)
        out.pop("exclusion_cells", None)
        out.pop("exclusion_manual_add", None)
        out.pop("exclusion_manual_remove", None)
