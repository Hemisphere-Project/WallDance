# Rig optics — camera + lens working envelopes

**Date:** 2026-06-12 · Derived from the Phase 2b detection floors ([ROADMAP §4.2 2b](ROADMAP.md), trail in `tmp_analysis/phase2b/SUMMARY.md`) + manufacturer specs. Assumes a 1.70 m dancer and *medium* (even, adequate) lighting — under poor IR the corpus shows the floors degrade well before the optics do.

## Hardware

| Item | Spec |
|------|------|
| Camera | IDS **U3-34E0XCP-M-GL Rev 1.2** — Sony **IMX664** (Starvis 2), mono, 1/1.8", 4.13 MP, **2704×1536 @ 2.9 µm** (usable 2688×1528 per IDS), 12-bit, USB3, up to 40 fps |
| Lens A | Tamron **M118FM08** — 8 mm, f/1.4–16, C-mount, 1/1.8" circle, MOD 0.1 m |
| Lens B | Tamron **M118FM06** — 6 mm, f/1.4–16, C-mount, 1/1.8" circle, MOD 0.1 m |
| Capture | On-device crop, pixel budget `IDS_CROP_PIXELS` = 1528² ≈ 2.3 MP, aspect `IDS_RATIO` 0.5–2.0, **native pixels (no binning)** → crop limits *field of view*, never per-dancer resolution |

## Formulas (recompute for any future lens/camera)

- dancer px (original capture space) = `f[mm] × 1700 / (D[m] × 2.9 µm)` → **8 mm: 4690/D · 6 mm: 3517/D**
- FOV width = `D × capture_width_px × 2.9 µm / f[mm]`
- hyperfocal = `f² / (N × c)` with CoC c = 2 px = 5.8 µm

## Detection floors (Phase 2b, corpus-measured)

| original-space dancer px | verdict |
|---|---|
| ≥ 110 | comfortable — net-height target met without upscale gymnastics |
| 70–110 | workable — needs tight ROI + imgsz 1536–1920 + yolo11x; degraded on dark scenes |
| < 70 | unreliable — corpus: 56 px dancers fail class B (0.55+) even at oracle settings on dark scenes; hardware territory |

## Dancer size vs distance

| D (m) | 5 | 10 | 15 | 20 | 25 | 30 | 40 | 50 | 60 | 70 |
|-------|---|----|----|----|----|----|----|----|----|----|
| **8 mm** px | 938 | 469 | 313 | 234 | 188 | 156 | **117** | 94 | 78 | *67* |
| **6 mm** px | 703 | 352 | 234 | 176 | 141 | **117** | 88 | *70* | 59 | 50 |

**Camera-to-dancer distance limits: 8 mm ≤ 43 m comfortable / 67 m workable · 6 mm ≤ 32 m comfortable / 50 m workable.**

## Stage coverage (FOV)

Width factors (× distance D), at the standard 2.3 MP crop with the ratio-2.0 clamp (max 2161 px wide) vs full sensor (2688 px, costs fps):

| | crop W≈2161 | full sensor | vertical (1528 px) |
|---|---|---|---|
| **8 mm** | 0.78 × D | 0.97 × D | 0.55 × D |
| **6 mm** | 1.04 × D | 1.30 × D | 0.74 × D |

Minimum standoff for a stage of width W: **8 mm: D ≈ 1.28 W · 6 mm: D ≈ 0.96 W** (full sensor: 1.03 W / 0.77 W). Mount rotated 90° to trade these for tall-wall coverage (aerial).

**Lens-independent identity:** standing at the minimum coverage distance, dancer px = `capture_width_px × 1.7 / W` ≈ **3674 / W(m)** (2.3 MP crop) — the lens only chooses *where you stand*, not how many pixels a dancer gets at full-width framing. → max stage width ≈ **33 m comfortable / 52 m workable** (full sensor: 41 m / 65 m). At a *fixed* distance the 8 mm gives 1.33× the pixels of the 6 mm.

## Venue fit — the actionable procedure (standard 2.3 MP crop)

> Tool: `python extra/venue_fit.py --stage WxH [--distance D]` computes everything below (verdict per lens, distance ranges to request from the organiser, dancer px at a given spot).

Given a stage **W × H (m)**, the workable camera-distance window per lens is `D_min ≤ D ≤ D_max`:

- **D_min (coverage)** = the largest of: `1.81·√(W·H)`, `1.81·H`, `1.28·W` → for the **8 mm**; `1.35·√(W·H)`, `1.35·H`, `0.96·W` → for the **6 mm**.
- **D_max (dancer size)** = **42.6 m comfortable / 67 m workable (8 mm)** · **32 m / 50 m (6 mm)**.
- If `D_min > D_max` for both lenses, the venue needs full-sensor capture (`--full-sensor`, costs fps), partial-stage framing, or a different camera position.

Pre-computed windows for common stages (comfortable, i.e. dancer ≥ 110 px):

| Stage W×H (m) | 8 mm window | 6 mm window |
|---|---|---|
| 8 × 6 | 12.5 – 43 m | 9.4 – 32 m |
| 12 × 8 | 17.7 – 43 m | 13.3 – 32 m |
| 16 × 10 | 22.8 – 43 m | 17.1 – 32 m |
| 20 × 12 | 28.0 – 43 m | 21.0 – 32 m |
| 25 × 14 | 33.8 – 43 m | 25.3 – 32 m |
| 30 × 15 | 38.3 – 43 m (tight) | 28.7 – 32 m (tight) |
| 35 × 16 | workable only (44.7 – 67 m) | workable only (33.5 – 50 m) |

Reading the table: **got a fixed spot?** — the lens whose window contains your D fits; if both fit, take the 8 mm (1.33× the dancer pixels). **Negotiating with the organiser?** — ask for a position inside the 8 mm window; quote the 6 mm window as the short-throw fallback.

## Focus depth

Hyperfocal (c = 5.8 µm): 8 mm — 7.9 m @ f/1.4, 4.0 m @ f/2.8 · 6 mm — 4.4 m @ f/1.4, 2.2 m @ f/2.8.
Focused at the stage at any normal show distance (≥ 8 m for 8 mm, ≥ 4.5 m for 6 mm), everything from half that distance to infinity is sharp **even wide open** — depth of field is a non-issue; only sub-4 m placements need stopping down.

## Caveats

1. **The lenses, not the sensor, are the sharpness ceiling.** Both are 2 MP-rated (4 µm design pitch) on 2.9 µm pixels — expect ~20–30 % effective resolution derating wide open / off-center. Plan with the 110 px comfortable floor, not the workable one. A 4–5 MP-rated lens is the upgrade path if far/wide shows become routine.
2. **Not IR-corrected**: focus shifts under 850 nm. Always focus under the *show's* IR illumination (web-monitor focus score), never under white work light. Beyond hyperfocal distances the wide-open DoF mostly absorbs the shift.
3. Rolling shutter (IMX664): motion skew is handled by the calib blur budget (exposure ≤ 10 % of dancer height); irrelevant at show distances.
4. All of the above assumes the lighting is adequate — the corpus says **light, not optics, fails first**: dancers ≥ 110 px still drop on dark scenes that pass in daylight (P1.3 illuminators remain the root fix).

**Verdict:** the U3-34E0XCP + both Tamrons are suitable for any stage the FOV can frame — up to ~33 m stage width / ~43 m (8 mm) or ~32 m (6 mm) camera distance with comfortable margins. Pick the 6 mm for short-throw/cramped venues, the 8 mm as the default (more px per dancer at equal distance, less wide-angle geometry).

Sources: [IDS U3-34E0XCP product page](https://en.ids-imaging.com/store/u3-34e0xcp-rev-1-2.html) · [Edmund listing (Rev 1.2 mono)](https://www.edmundoptics.com/p/ids-imaging-u3-34e0xcp-m-gl-118-gige-monochrome-camera-rev-12/55735/) · [Tamron M118FM08](https://www.tamron.com/global/biz/products/fa/m118fm08.html) · [Tamron M118FM06](https://www.tamron.com/global/biz/products/fa/m118fm06.html)
