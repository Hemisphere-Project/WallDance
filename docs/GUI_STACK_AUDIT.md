# GUI & Stack Overhaul Audit — Decision Report

**Date:** 2026-06-11
**Scope:** (1) GUI layer audit (DearPyGui), (2) keep/refactor/replace GUI toolkit, (3) Python architecture refactor vs language/framework switch, (4) deployment & auto-update constraints (Linux dev + Windows 11 show laptop).
**Method:** three parallel deep-dives (GUI code audit, architecture/perf/deployment audit, ecosystem research with primary sources), load-bearing claims spot-verified against source.

---

## TL;DR — Recommendation

**Stay in Python. Do not switch language — the hot path is already native and a rewrite buys ~nothing.** The GUI is the real debt, and the right sequence is:

1. **Now (~1–2 days):** four targeted DearPyGui fixes that address most of the day-to-day clunkiness (toast thread race, modal helper, texture-path reorder, layout constants).
2. **Next (~2–4 weeks):** extract a **headless core / decompose `app.py`** — this is already a ROADMAP maintainability item, it de-risks *every* GUI future, and it is the move that buys live-show reliability (GUI crash ≠ show crash).
3. **Then (decision point):** when a ~2-month calendar window exists, rework the operator console in **PySide6/Qt** — the evidence-backed choice (kills the DPI and sizing pains structurally; the stack used by SLEAP, DeepLabCut, napari, OBS). Until then, DearPyGui is safe to stay on: it is alive but in maintenance mode, so there is **no emergency, but also no future** (DPG2 will be a different API).
4. **Deployment:** keep the launcher + uv pattern — it is exactly what ComfyUI Desktop and InvokeAI converged on. Formalize it (release tags + `uv sync --frozen`); never ship `.engine` files (already the case).

---

## Part A — GUI layer audit (what's actually wrong)

### A.1 Inventory

| Metric | Value |
|---|---|
| GUI LOC | ~3,850 (`gui.py` ~2.6k + `gui_builder.py` ~1.25k) |
| `dpg.*` call sites | ~1,240 (plus ~80 in `app.py`) |
| Unique widget tag strings | **147** (no central registry; typos fail silently via `does_item_exist` guards) |
| Modal/dialog types | 10 (project picker, save/load config, model loading, TRT prompt, calib report, calib2 pool, issue report, QR, slot history) + toasts |
| Hardcoded color tuples | ~200 |
| Hardcoded pixel literals | ~100+ (wrapped in a manual `scaled()` helper) |
| Estimated pure boilerplate | 15–20% (~400–500 lines: modal centering math, section builder duplication, sync-method clones) |

State is shared across three uncoordinated channels: raw tag strings, a config dict passed from `app.py`, and instance attributes on `WallDanceGUI`. `app.py` reaches the GUI through ~100 `gui.sync_*/update_*` calls plus ~80 direct `dpg` calls — the abstraction layer is thin but *does exist*, which matters for migration cost.

### A.2 Root causes of the reported pains

**Window/modal sizing "a bit off"** — confirmed, structural:
- 5+ modals manually center with integer math against live viewport reads: `pos=[vp_w//2 - dlg_w//2, ...]` (`gui.py:958`, `gui.py:1766`, `gui.py:1997`, plus model-loading/TRT/QR dialogs). Stale viewport metrics at creation time → misplacement; sizes are fixed at creation and never re-fit to content.
- Mixed sizing policy: some popups use `autosize=True`, most use hardcoded `scaled(W)×scaled(H)`.
- Viewport itself is hardcoded `1340×850 × dpi_scale` at startup (`app.py:4085`).
- **Upstream:** these are known DearPyGui behaviors, not local bugs — autosize-with-collapsing-headers grows unbounded ([DPG #2500](https://github.com/hoffstadt/DearPyGui/issues/2500)); exposing computed autosize was closed "not planned" ([#1815](https://github.com/hoffstadt/DearPyGui/issues/1815)).

**DPI handling** — confirmed, and **unfixable while on DPG**:
- The app does its best with a hand-rolled `get_display_scale()` (`gui.py:48–195`: Win32 GetDeviceCaps, GDK_SCALE/gsettings on Linux, resolution-tier heuristics) + one global font scale + manual `scaled()` math everywhere.
- DearPyGui has **no per-monitor DPI awareness at all**: [#1081 "Support High DPI"](https://github.com/hoffstadt/DearPyGui/issues/1081) open since **July 2021** (priority-high), blurry-font and wrong-position-on-HiDPI issues open for years (#1380, #2247, #2362, #2336). The maintainers' own roadmap defers this to DPG2 — a different engine and API.

**Preview performance** — confirmed, partially fixable in place:
- Per-frame path (`gui.py:1252–1281`): CPU `cv2.resize` → **full-frame `cv2.cvtColor(BGR2RGBA)`** (`gui.py:1270`) → **`astype(np.float32)/255.0` allocation + copy** (`gui.py:1277`) → full-buffer `dpg.set_value` upload (`gui.py:1281`). At 960×540 that's an 8.3 MB float buffer rebuilt and re-uploaded every frame (~3–8 ms, ~10–15% of the 20 FPS frame budget per TUNING.md's ~12 ms GUI line).
- The float32-RGBA requirement is **DPG's raw-texture design** (no uint8 path) — order-of-operations and allocation waste can be fixed in place; the format tax cannot.
- The frame is also round-tripped GPU→CPU for overlay drawing even when the pipeline already holds it on GPU.

**A real thread-safety bug found:** toast auto-hide spawns a daemon thread that calls `dpg.delete_item` off the main thread (`gui.py:2503–2527`). DPG is not thread-safe; this is a latent crash — worst possible failure mode for a live-show console. Cheap fix (main-loop expiry queue).

### A.3 What must survive any rework

Expert-mode gating (Ctrl+Shift+E / `WD_EXPERT`), single-key show shortcuts (S/K/B/T/I/…), color-coded status badges, the one-dial sensitivity macro, ROI corner-drag + mask painting on the preview, project picker flow, toasts, the phone web monitor. Port-cost inventory (per-widget) totals **~40–50 h of pure widget reimplementation** — that is the *floor* for any toolkit migration, before integration/testing/field validation.

---

## Part B — Architecture & performance audit

### B.1 Is Python the bottleneck? **No — measured.**

From the repo's own instrumentation (`docs/TUNING.md`): live pipeline ≈ **125 ms/frame (~20 FPS)**, of which YOLO @ imgsz 1280 ≈ **65 ms** (TensorRT/CUDA — native), motion feed (MOG2 + frame-diff, OpenCV C++) ≈ 30 ms, tracker (Kalman+Hungarian, n ≤ 6 people) < 8 ms, GUI ≈ 12 ms. Capture, inference, enhancement, and motion all run in C/C++/CUDA and release the GIL; encoding is an ffmpeg subprocess. **A Rust/C++ rewrite would improve the hot path by roughly nothing** — the only compiled-language win on the table (motion feed 2–3×) is equally reachable by the existing P-3/P-4 backlog items (resolution cap, blur-after-downscale) in Python/OpenCV.

### B.2 The real debt: `app.py` god-object

`app.py` ≈ 4.8k lines, ~177 methods across ~13 domains (camera retry, ROI/mask editor ~30 methods, config persistence, model/TRT orchestration, calibration flows, playback/recording, ops, main loop). ROADMAP already lists "`app.py` decomposition — not started." Circular coupling (`app ↔ gui`, `pipeline → tracker → config`) blocks isolated testing of anything UI-adjacent.

**Good news — the seams already exist:** `FrameProcessor.process()` is effectively headless (the replay harness proves it), `ProcessingSettings` is injected, all **19 test suites (~3.3k lines, 123 tests) are headless**, and the golden/replay harness was just re-founded on the annotated corpus. A decomposition has an unusually strong safety net for a project this size. Estimated **13 focused days (~2–4 calendar weeks)** for: move modules into `core/ runtime/ ui/`, split `app.py` into controllers, route GUI callbacks through controllers instead of app internals.

### B.3 Language-switch blockers (if it were considered anyway)

Load-bearing Python-only assets: **Ultralytics** (pose models + pre/post), **torch/Kornia** (GPU enhancement), **IDS Peak Python bindings** (1.9k-line camera module with stall recovery), the **validated tracker tuning**, and the replay/golden corpus. Research verdict on rewrites: Rust 6–12 months (no TensorRT bindings maintained, kornia-rs has no GPU ops, DIY IDS FFI), C++ 6–12 months and the worst solo-maintenance profile, C#/Avalonia the most coherent (IDS ships first-party .NET bindings) at 4–8 months but solves no problem this project actually has. **All rejected.**

---

## Part C — GUI direction options

### DearPyGui status (research, primary sources)

Alive but **officially in maintenance mode**, day-to-day sustained by one volunteer (@v-ein); releases continue (v2.3.1, May 2026). The long-term plan, **DearPyGui 2 on the "Pilot Light" engine, abandons Dear ImGui and will be a different API** — so a migration is eventually unavoidable even when loyal. DPI and autosize — the exact reported pains — are explicitly not getting fixed in the current line. ([Wiki: "What's going on?"](https://github.com/hoffstadt/DearPyGui/wiki/What%27s-going-on%3F), May 2026.)

### Options matrix

| Option | Effort | Kills DPI pain | Kills sizing pain | Preview perf | Show reliability | Risk / notes |
|---|---|---|---|---|---|---|
| **0. Targeted DPG fixes** | 1–2 days | ✗ (heuristic stays) | partial (modal helper) | partial (~3–8→1–3 ms) | fixes toast crash race | none; do regardless |
| **1. Refactor-in-place on DPG** (tag registry, section templates, palette, dispatcher) | ~1 week | ✗ | partial | partial | unchanged | invests in a maintenance-mode API that DPG2 will break anyway |
| **2. Headless-core decomposition** (Stage A) | 2–4 weeks | n/a | n/a | enables GPU-side preview later | **GUI crash ≠ show crash** (process/loop isolation becomes possible) | already a ROADMAP item; no-regrets prerequisite for 3a/3b |
| **3a. PySide6/Qt console** (Stage B, recommended) | **6–10 weeks** realistic (40–50 h widget floor + integration + field validation) | ✅ per-monitor-V2 by default | ✅ real layout managers, QWizard for calib flows | ✅ zero-copy QImage/QPainter or QOpenGLWidget @1080p30 | ✅ | paradigm shift (retained widgets, signals/slots) — a rewrite, not a port; GPL-clean (PySide6 is LGPL/GPL); the stack of SLEAP, DeepLabCut, napari, OBS |
| **3b. Web console (NiceGUI/FastAPI)** on the headless core | 4–8 weeks | ✅ (browser) | ✅ | ⚠️ weakest link: MJPEG/WS preview ~30fps feasible on loopback but jittery; overlays move client-side | ✅✅ (full process isolation, remote/tablet operation) | choose only if remote operation is actually wanted; dense control panels cost more in web tech |
| **3c. imgui-bundle sideways move** | 3–6 weeks | partial (em-based, not Qt-grade) | partial | ✅ (ImmVision is purpose-built for CV) | unchanged | same single-maintainer structural risk being fled; still a full GUI rewrite |
| **4. Language rewrite (Rust/C++/C#)** | 4–12 months | — | — | ~0 gain (hot path already native) | ✗ re-validating the CV core is the biggest show risk there is | **rejected** |

### Option 0 detail (do these now)

1. **Toast thread race** → main-loop expiry queue instead of daemon-thread `delete_item` (`gui.py:2503–2527`). ~1 h.
2. **Modal helper** → one `centered_modal()` factory replacing the 5+ hand-rolled centering blocks; centers at show-time with fresh viewport metrics. ~2 h.
3. **Texture path reorder** → resize *before* color-convert, reuse the float buffer with `np.multiply(..., out=...)` instead of fresh `astype` allocation per frame (`gui.py:1270–1281`). ~3–5 h, ~2–4× on preview cost.
4. **Layout/color constants module** → gather the ~100 pixel literals and ~200 color tuples. ~2 h.

---

## Part D — Deployment & auto-update verdict

**Current state is already the industry-convergent pattern — grade A-, keep it.**

- The `launcher/` (customtkinter + dulwich) already classifies UP_TO_DATE/BEHIND/AHEAD/DIVERGED, guards dirty files, exempts field artifacts, and re-runs install when `pyproject.toml` changes. ComfyUI Desktop and InvokeAI both converged on exactly this shape: thin launcher + **uv-managed env** + git-based code sync.
- **Do not freeze/bundle**: PyInstaller/Nuitka with torch+CUDA → 2.6 GB fragile binaries, broken `torch.compile`, TRT deserialization failures, plus Windows SmartScreen signing burden. Source+uv sidesteps all of it.
- Hardening to adopt: update to **release tags** rather than branch head (`git reset --hard <tag>` + `uv sync --frozen`), keep the existing dirty-file safety (closes the ROADMAP "launcher update safety" item), and ship a **TensorRT timing cache** to speed first-run engine builds. Continue never shipping `.engine` files (GPU/version-bound).
- A language switch would *not* simplify deployment here: the multi-GB torch/CUDA payload exists regardless of GUI language, and the uv launcher already tames it on both OSes.

---

## Decision inputs still open (operator call)

1. **Remote/tablet operation** of the console during rigging/shows — if attractive, Option 3b (web) overtakes 3a; the phone monitor suggests appetite.
2. **How much does DPI actually hurt in the field?** If the show laptop + dev box are both ~100%/125% single-monitor, Option 0+1 may keep DPG tolerable for another season.
3. **Calendar**: Stage B needs a ~2-month window without show pressure; current ROADMAP priority (detection robustness + Phase 7 ops before show season) argues for sequencing it after.

---

## Bottom line

- **Language/framework switch: no.** Measured evidence says the rewrite gains ~0 performance and risks the one irreplaceable asset (the validated CV core + corpus).
- **Python architecture refactor: yes** — the `app.py` decomposition into a headless core is the highest-leverage move in the whole audit, is already on the ROADMAP, has an unusually good safety net (123 headless tests + replay goldens), and is the prerequisite that makes the GUI question low-stakes.
- **GUI: fix the four small things now; plan a PySide6 console as the default Stage B** when a window opens. DPG is not dying this year, but its DPI/sizing pains are officially permanent, and DPG2 breaks the API anyway — every week of new DPG code is investment in a sunsetting target.
