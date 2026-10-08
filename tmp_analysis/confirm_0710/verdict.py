#!/usr/bin/env python3
"""Text + tables of the 30 m confirmation report -> verdict.json (read by report.py).  Numbers come from
results.json / light.json / extra.json / door_scale.json / empty_probe.json; the prose is the analysis."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = json.loads((HERE / "results.json").read_text())
L = json.loads((HERE / "light.json").read_text())
X = json.loads((HERE / "extra.json").read_text())
D = json.loads((HERE / "door_scale.json").read_text())
S = ["slot_2_20261007_200742", "slot_3_20261007_201246", "slot_4_20261007_201949", "slot_5_20261007_202412"]
SH = {s: f"slot {s[5]}" for s in S}
V = R["variants"]


def tbl(head, rows, cls=None):
    h = ["<div class='wrap'><table><tr>" + "".join(f"<th>{x}</th>" for x in head) + "</tr>"]
    for r in rows:
        h.append("<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>")
    return "".join(h) + "</table></div>"


def pct(x):
    return "-" if x is None else f"{100 * x:.0f} %"


def rng(vals, fmt="{:.0f}"):
    vals = [v for v in vals if v is not None]
    lo, hi = min(vals), max(vals)
    return fmt.format(lo) if fmt.format(lo) == fmt.format(hi) else f"{fmt.format(lo)}-{fmt.format(hi)}"


# ---------------------------------------------------------------- size + recall
size_rows = []
for s in S:
    yh = R["size"][s]["yolo_h"]          # [min, p10, med, p90]
    med = yh[2]
    rec = R["recall"][s]
    size_rows.append([SH[s], f"{R['takes'][s]['wall_s'][0]:.0f}-{R['takes'][s]['wall_s'][1]:.0f} s",
                      f"{yh[2]:.0f} ({yh[1]:.0f} / {yh[0]:.0f})",
                      f"{med * 800 / 1776:.0f}", f"{med * 1280 / 1776:.0f}", f"{med * 1536 / 1776:.0f}",
                      f"{med * 1280 / 1300:.0f}",
                      f"{pct(rec['V1b']['0.15'])} / {pct(rec['V1b']['0.5'])}",
                      f"{pct(rec['V1']['0.15'])} / {pct(rec['V1']['0.5'])}",
                      f"{pct(rec['V1r']['0.15'])} / {pct(rec['V1r']['0.5'])}"])
size_table = tbl(["take", "wall window", "YOLO box at the wall, px: median (p10 / min)", "in YOLO's input at 800",
                  "at 1280", "at 1536", "at 1280 with a 1300 px ROI",
                  "YOLO recall at the wall, x@800 (conf &ge; 0.15 / &ge; 0.5)", "x@1280", "x@1280 + ROI 1300"],
                 size_rows)

# ---------------------------------------------------------------- light
def l30(k, sub="med"):
    return [L["30"][s][k][sub][1] if L["30"][s][k][sub] else None for s in S]


bright = ["slot_2_20261006_211831", "slot_3_20261006_212855"]
dark = ["slot_4_20261006_213310", "slot_6_20261006_214306", "slot_2_20261006_214531", "slot_7_20261006_220640"]


def l25(stems, path):
    out = []
    for s in stems:
        d = L["25"][s]
        for k in path:
            d = d[k]
        out.append(d[1] if isinstance(d, list) else d)
    return out


nodes30 = {(L["30"][s]["nodes"]["ExposureTime_us"], L["30"][s]["nodes"]["Gain_node"]) for s in S}
nodes25 = {(L["25"][s]["nodes"]["ExposureTime_us"], L["25"][s]["nodes"]["Gain_node"]) for s in L["25"]}
light_rows = [
    ["camera (each take's .meta camera.nodes)", "ExposureTime 24999 us, Gain 31.62 (config: 25 ms, &laquo;36 dB&raquo;)",
     "same", "same"],
    ["door (lit roll-up door), median DN", rng(l25(bright, ["boxes", "door"])), rng(l25(dark, ["boxes", "door"])),
     rng(l30("boxes", "door") if False else [L["30"][s]["boxes"]["door"][1] for s in S])],
    ["plain wall", rng(l25(bright, ["boxes", "wall"])), rng(l25(dark, ["boxes", "wall"])),
     rng([L["30"][s]["boxes"]["wall"][1] for s in S])],
    ["floor at the wall base", rng(l25(bright, ["boxes", "floor"])), rng(l25(dark, ["boxes", "floor"])),
     rng([L["30"][s]["boxes"]["floor"][1] for s in S])],
    ["dancer's torso at the wall (median of the box centre)", rng(l25(bright, ["torso_far", "med"])),
     rng(l25(dark, ["torso_far", "med"]), "{:.1f}"), rng([L["30"][s]["torso_far"]["med"][1] for s in S], "{:.1f}")],
    ["worn belt: peak / blob mean DN", f"255 / {rng(l25(bright, ['worn_belt', 'mean']))}",
     f"255 / {rng(l25(dark, ['worn_belt', 'mean']))}",
     f"{rng([L['30'][s]['worn_belt_far']['peak'][1] for s in S])} / {rng([L['30'][s]['worn_belt_far']['mean'][1] for s in S])}"],
    ["belt on the stand: peak / blob mean / box mean DN (empty end)", "-", "-",
     f"255 / {rng([L['30'][s]['stand_belt_end']['mean'][1] for s in S])} / "
     f"{rng([L['30'][s]['stand_belt_end']['win_mean'][1] for s in S])} ("
     f"{rng([L['30'][s]['stand_belt_end']['sat'][1] * 100 for s in S])} % of its pixels saturated, "
     f"{rng([L['30'][s]['stand_belt_end']['w'][1] for s in S])} x {rng([L['30'][s]['stand_belt_end']['h'][1] for s in S])} px, "
     f"local background {rng([L['30'][s]['stand_belt_end']['bg'][1] for s in S])} DN)"],
]
light_table = tbl(["8-bit DN as recorded", "2026-10-06 bright takes (s2a, s3)", "2026-10-06 dark takes (s4, s6, s2c, s7)",
                   "2026-10-07 takes (slots 2-5)"], light_rows)

# ---------------------------------------------------------------- 25 vs 30
n25 = R["night25"]
cmp_rows = []
for s, m in n25.items():
    cmp_rows.append([f"25 m {m['name']}", f"{m['win_s'][0]:.0f}-{m['win_s'][1]:.0f} s", f"{m['h_med']:.0f}",
                     f"{m['h_med'] * 1280 / 1322:.0f}", pct(m["yolo_seen"]), f"{m['cov_wall']:.3f}",
                     "; ".join(f"{t:.1f} s +{d:.1f} s" for t, d in m["holes"]) or "none", m["ghost_frames"],
                     m["jumps"], m["id_switches"], f"{m['on_dancer']:.3f}", f"{m['lag_ms']:.0f}"])
for s in S:
    m = V["V2"][s]
    yh = R["size"][s]["yolo_h"][2]
    holes_wall = [h for h in m["holes"] if m["wall_s"][0] <= h["t"] <= m["wall_s"][1]]
    jumps_wall = [j for j in m["jumps"] if m["wall_s"][0] <= j[0] <= m["wall_s"][1]]
    cmp_rows.append([f"30 m {SH[s]} (V2)", f"{m['wall_s'][0]:.0f}-{m['wall_s'][1]:.0f} s", f"{yh:.0f}",
                     f"{yh * 1280 / 1776:.0f}", pct(m["yolo_seen_wall"]), f"{m['cov_wall']:.3f}",
                     "; ".join(f"{h['t']:.1f} s +{h['dur_s']:.1f} s" for h in holes_wall) or "none",
                     m["ghost_wall"], len(jumps_wall), m["id_switches"], f"{m['on_dancer']:.3f}", f"{m['lag_ms']:.0f}"])
cmp_table = tbl(["take", "wall window", "dancer YOLO box (px)", "in YOLO's input at 1280", "YOLO &ge; 0.5 in the window",
                 "coverage in the window", "holes &ge; 1 s in the window", "2-point frames", "jumps", "id switches (take)",
                 "on dancer (vs YOLO)", "lag (ms)"], cmp_rows)

# ---------------------------------------------------------------- prose
v = {}
v["verdicts"] = [
    "<b>(a) The release (46a0262 + D27) behaves well at the wall on all four takes, and better than as shot.</b> "
    "V2: a point on the walker in 96-100 % of the wall frames, coverage 100 %, no hole and no 2-point frame at the wall; "
    "the only holes are at the start of each take (1.9-2.7 s, walker passing right in front of the lens). "
    "No regression against as-shot (V0), except that the first point near the lens comes 0.6-1.7 s later (x@1280: "
    "V1 at x@800 matches V0 there), so coverage in view is 1-3 points lower. V0 kept the point on the stand's static belt for 18 s in "
    "slot 2 while the walker crossed the door. V1/V2 do not. "
    "<span class='bad'>One real defect, in the code (all variants):</span> the empty-wall snapshot is updated on "
    "&laquo;plate stale&raquo; frames, so the walk-out right in front of the lens corrupts it. Its gain then settles "
    "at ~0.85 and the lit door shows as a 30-100k px&sup2; foreground blob for 30-60 s. In slot 4 a camera stall "
    "(14.3-16.5 s, 12 frames in 2.2 s) lost the walker's track, and that blob held the orphaned slot on the door. "
    "Result: 2 points for 24 s (V1) / 5 s (V2), plus ~4 s of a point on the door after the walker left. A second "
    "point like that would keep TouchDesigner's video on.",
    "<b>(b) x@1280 is enough at the output, not at YOLO.</b> The walker at the wall is ~128-139 px (YOLO box), so "
    "~92-100 px in YOLO's input at 1280 on the full 1776 px crop: right at the ~100 px knee. YOLO finds the walker "
    "in 31-67 % of the wall frames (conf &ge; 0.15) and the belt, the snapshot and coasting carry the rest. "
    "x@800 is not enough: 0-17 % recall (58-63 px input), and the static belt captured the slot in slot 2. "
    "A 1300 px-wide ROI (~126-137 px input) raises recall to 34-76 % (28-62 % at &ge; 0.5): +3 to +14 points at "
    "&ge; 0.15, +13 to +23 at &ge; 0.5; "
    "the output was already at 100 % at the wall, so this is margin. Recommend a ROI with a long side &le; ~1650 px. "
    "The readiness &laquo;dancer size&raquo; row would not ask for it: its measure stays at the walk-out (237-309 px at x@1280), "
    "because the far-wall skeletons never fill its 10 s window.",
    "<b>(c) Belt at 30 m: visible, detected, no YOLO ghost.</b> The stand belt saturates (peak 255, blob mean "
    "~200 DN) and the detector finds it in 100 % of the frames where the walker is away. The belt static map learns "
    "it in every V1/V2 take. YOLO never calls the stand a person at gamma 0.8 / CLAHE 1.0 (0 detections, even at "
    "conf 0.15; Calibrate's empty-wall check 0 / 120 frames). It does at Calibrate's brighter scene rung, gamma 1.8 / "
    "CLAHE 1.5 (7 / 100 frames, conf &le; 0.52), which the empty-wall check then rejects. The belt makes no point on its "
    "own. At x@800 (V0, and V1 at 800) it captured the walker's slot for 18 s, the 2-point / wrong-point case; "
    "not at x@1280.",
    f"<b>(d) Light: same exposure, same Gain node, same distance, much more light.</b> Every take on both nights "
    f"reads ExposureTime 24999 us and Gain node 31.62 (requested &laquo;36 dB&raquo;, so the node is clamped; "
    f"x31.6 = 30 dB if linear). Last night: door ~54 DN, plain wall 7, walker's torso at the wall "
    f"{rng([L['30'][s]['torso_far']['med'][1] for s in S])} DN (median), belts saturated (255). The 2026-10-06 night: "
    "torso 14 DN on the bright takes and 5-8.5 on the dark ones, door 24 / 7-9. So the body got ~3x (bright) to "
    "~5-10x (dark) the 10-06 light, the door ~2x / ~6-8x. The wall is at the <i>same</i> distance on both nights (door 318 vs 317 px "
    "tall), so the extra light comes from the projectors, not the distance.",
]
v["changes"] = (
    "<ul>"
    "<li><b>V0 &rarr; V1 (D27)</b>. YOLO at the wall: recall 0-14 % &rarr; 31-67 % (x@800 &rarr; x@1280). "
    "Slot 2: the point no longer stays on the stand's belt (18.4 s wrong, then 7 s with 2 points, in V0), "
    "so on-the-walker goes 0.82 &rarr; 0.97 and the guard's person height comes back to 151 px at 76 s "
    "(it stays at 764 px at x@800). Slot 4: 2-point frames 606 &rarr; 464 (the snapshot-held orphan, see (a)). "
    "Start of each take: the first point near the lens comes 0.6-1.7 s later, "
    "so coverage in view drops 1-3 points. That is the input size: V1 at x@800 gives V0's start exactly. "
    "V1 at x@800 (V1b) behaves like V0: the gain comes from the input size, not from conf 0.15 or intermittent confirm.</li>"
    "<li><b>V1 &rarr; V2 (Calibrate on the empty end of slot 2)</b>. Calibrate keeps gamma 0.80 / CLAHE 1.0 and "
    "MOG2 8 @ 0.5: it seeds gamma 4.0, caps it at 1.8, and its empty-wall check passes the current rung 0.80 / 1.0 "
    "first (0 / 120 frames with a person &ge; 0.12). Only the snapshot changes: the new one has the belt lit on the "
    "stand. Slots 2-3 are unchanged frame for frame. Slot 4: 2-point frames 464 &rarr; 100 (none at the wall), "
    "off-the-walker frames 544 &rarr; 186. Slot 5 (stand moved since the snapshot): 21 vs 15 off-walker frames, one "
    "2.1-height snap at 30.4 s. The second Calibrate run gave the same result (deterministic). "
    "tests/flow_check.py's own run (flow_v2/v2.json) reads the same timelines: slot 4 ghost frames 464 &rarr; 100, "
    "the other takes unchanged.</li>"
    "<li><b>Control: V1 + ROI 1300 x 900</b> (the 25 m project's ROI width). YOLO recall at the wall +3 to +14 points "
    "at &ge; 0.15 and +13 to +23 at &ge; 0.5. The output is the same at the wall (100 %). Slot 4's snapshot orphan "
    "is still there, and the walk-out near the lens is outside the ROI, as intended.</li>"
    "</ul>")
v["guard"] = (
    "<ul>"
    "<li><b>Slot 2 (configured 150 px).</b> Every variant adopts the walk-out height at 12.2 s: 761 px (x@1280) / "
    "764 px (x@800). The dense rule fires because &ge; 80 % of 10 s of confident skeletons fall outside 45-375 px, "
    "all of them close to the camera. The live DEV app did the same: its 20:04 session ended at 764 px. "
    "At x@1280 the follow rule brings it back to 151 px at 76 s (15 s of wall skeletons in its 60 s window). "
    "At x@800 it stays at 764 px to the end, since there are too few confident skeletons at the wall. "
    "With a ROI: 546 &rarr; 135 px.</li>"
    "<li><b>Slots 3-5 (configured 253 px, ~2x the wall size).</b> The release never touches 253 in these takes. "
    "The wall walker (~125-140 px) is inside the 76-632 px gate, so neither rule fires, and the walk-out is too "
    "spread to adopt. So 46a0262 does not <i>adopt</i> ~253 from these walk-outs; the 253 was inherited from the "
    "live DEV session (764 at 20:09, then 253 at 20:10).</li>"
    "<li><b>Own-size gates.</b> Slot 2: on in 98 % of the wall frames (global 761 vs own ~177-209 px), and the walker "
    "is tracked on 100 % of the wall frames. Slots 3-5: never on, because the own size at the end of the wall window "
    "(138-170 px, still pulled up by the walk-out skeletons) times 2.0 stays above 253. The walker is tracked anyway: "
    "96-100 % on the walker at the wall, no id switch at the wall, one 1.2-height jump (slot 5, 25.2 s). "
    "So with a person height of ~2x the dancer the release holds at the wall, but on the global gates, not the own-size "
    "ones.</li>"
    "<li><b>Readiness &laquo;dancer size&raquo;</b> (processor.dancer_height). It stays at the walk-out value through "
    "the whole wall window: " + ", ".join(f"{X['V1'][s]['dh_wall_med']:.0f}" for s in S) + " px at x@1280. "
    "So the row would read &laquo;~240 px &rarr; ~170 px in YOLO's input: ok&raquo; while the wall dancer is ~94 px "
    "there. It needs &ge; 40 confident full skeletons in 10 s, which a 30 m dancer never gives at 1280.</li>"
    "</ul>")
v["size"] = (
    "<p>The YOLO box is the walker's height (YOLO &ge; 0.5 on the walker inside the wall window, V1). The "
    "difference-image ground truth under-counts the dark legs, so it is not used for size. In YOLO's input = "
    "median x imgsz / 1776, since the ROI is off and the letterbox fits the 1776 px side. Recall = the share of "
    "the wall-window frames (walker in view) with a YOLO box on the walker. The window includes the walker standing "
    "still beside the stand among the belts (slot 2, 29-64 s), where YOLO almost never fires even at 1280. "
    "The readiness rule wants ~100 px in YOLO's input. The full crop at 1280 gives 92-100 px, so the ROI long side "
    "should be &le; 130 x 1280 / 100 &asymp; 1660 px, or Image Size 1536 (110-120 px; no dev37 engine, not run).</p>")
v["size_table"] = size_table
v["calibrate"] = (
    "<p>Calibrate ran on frames 1700-1800 of slot 2 (5.4 s, empty, the belt lit on the stand at its slot 2-4 place), "
    "with the D27 config (x@1280, conf 0.15). Exposure / gain come from the take (25 ms / 36 dB). The scene step "
    "seeds gamma 4.0 and caps it at 1.8 (noise 4.38), MOG2 8 @ 0.5 (0.35 % false foreground), CLAHE 1.5. "
    "Its diagnostic height=486 px (n = 67) is YOLO seeing false persons on the empty wall at that brighter rung; "
    "it is not applied. The empty-wall check tries the current gamma 0.80 / CLAHE 1.0 first: 0 / 120 frames with a "
    "person &ge; 0.12, clean, kept. "
    "<b>Result: gamma 0.80 / CLAHE 1.0 / MOG2 8 @ 0.5, the same as the live Calibrate at 20:06.</b> "
    "The snapshot is 1776 x 1304, noise 0.33 DN (projects/mur30m-0710/plates/flowcheck_flow_v2_...npz). "
    "Does the empty-wall check see the belt stand or the lit door as a person? Not at the kept 0.80 / 1.0. At 1.8 / 1.5 "
    "YOLO sees the stand + belt (7 / 100 frames, conf &le; 0.52) and two wall fixtures (left x &lt; 250, right x ~1536, "
    "conf &le; 0.67). The check exists to reject such a rung, and it would.</p>")
v["belt"] = (
    "<ul>"
    "<li><b>Visible.</b> The belt on the stand is saturated (peak 255, blob mean ~200 DN, 57-58 % of its pixels at "
    "&ge; 250), ~37-39 x 21 px, on a ~26-31 DN background. Belts worn at the wall: peak 255 (median), blob mean "
    "184-200 DN.</li>"
    "<li><b>Detected.</b> core/belt_detector.py (global mode, no static map) finds the stand belt in 100 % of the "
    "frames where the walker is &gt; 150 px away and not near the lens (408-672 frames per take). It moved in slot 4: "
    "(1097, 667) &rarr; (989, 668), the right edge of the door. The app's online static map learns it as a static "
    "glint in every V1/V2 take, and in V0 slots 3-5.</li>"
    "<li><b>No YOLO ghost.</b> No YOLO detection on the stand in any variant (conf &ge; 0.15 at 1280), and no point "
    "ever created by the belt.</li>"
    "<li><b>But it can capture a slot (x@800).</b> V0 / V1b slot 2: the walker stands beside the stand from 28 s, and "
    "the slot is belt-backed with the stand belt next to the walker. When the walker leaves at 66 s, x@800 has no "
    "skeleton and the gated belt query keeps answering with the static belt. The point stays on the stand for 18.4 s, "
    "then 2 points for 7 s once the walker gets a new slot on the way back. The snapshot of 20:06 (no belt yet) "
    "&laquo;backs&raquo; that belt as foreground, so the static map never learns it in V0 slot 2. "
    "At x@1280 YOLO re-acquires the walker at 66 s and nothing sticks.</li>"
    "</ul>")
v["light"] = (light_table +
              "<p>DN are 8-bit as recorded (Mono10g40IDS from the camera, FFV1 8-bit files), BlackLevel 0. Torso = "
              "median of the centre 24 % x 25 % of the YOLO box (conf &ge; 0.5) at the wall, then the median over "
              "frames (30 m: " + ", ".join(str(L['30'][s]['torso_far']['n']) for s in S) + " frames). "
              "The box median includes the door behind when the walker stands in front of it; the median of the "
              "foreground pixels only gives " + rng([L['30'][s]['torso_far']['fg_med'][1] for s in S]) + " DN. "
              "Boxes: door 790-960 x 450-700, plain wall 1200-1450 x 420-560, floor 400-1400 x 760-850 "
              "(shifted 100 px left on the 10-06 frame, same scale). "
              "<b>For the IR order:</b> at 25 ms and the Gain node at 31.6, last night's light puts a dancer's "
              "body at ~40-50 DN at the wall and saturates a belt. That is ~3x the 10-06 bright takes (14 DN), where "
              "YOLO already saw the dancer in 62-84 % of the wall frames with a ROI. So last night's projector "
              "setup has ~1.5 stops of margin for the body: room to shorten the exposure against motion blur, or to "
              "lower the gain. The belt needs far less (it saturates on both nights, even with the body at "
              "5-8 DN). Caveat: the Gain node value (31.62) means x31.6 (30 dB) if it is linear, as on IDS U3 "
              "cameras, or 31.6 dB (x38) if it is dB. Both nights used the same node value, so the comparison "
              "holds either way; only absolute dB-normalised numbers depend on it.</p>")
v["compare"] = (
    "<p>Same camera-to-wall distance both nights: the lit door is "
    f"{D['30m slot 3 end']['door_h']} px tall on 2026-10-07 and {D['25m slot 1 (bright, empty)']['door_h']} px on "
    "2026-10-06 (the 10-06 files are a crop of the same sensor pixels, not a resize). The dancer at the wall measures "
    "the same, ~125-140 px. By docs/OPTICS.md (8 mm, 2.9 um, 1.70 m dancer: 4690 / D px), ~130 px means ~36 m; with "
    "the 6 mm it would be ~27 m (3517 / D). Either the distances are nominal, or the lens is not the 8 mm of the rig "
    "sheet: worth checking. What differs: the 10-06 flow check ran with a ROI 1322 px wide (dancer ~123 px in YOLO's "
    "input at 1280), the 10-07 takes without one (~94 px), and last night had ~3x (bright) to ~5-10x (dark) more light on the body. "
    "Both: the 46a0262 code, D27 and Calibrate (10-06: 'after s1' in tmp_analysis/flowcheck/night_rc_46a0262). "
    "Windows: the dancer at the wall (10-06: validate_night.py's windows; 10-07: walker at &le; 160 px).</p>"
    + cmp_table +
    "<p class='mut'>&laquo;YOLO &ge; 0.5 in the window&raquo;: the share of window frames with a YOLO box &ge; 0.5 "
    "(no ground truth on 10-06). On 10-07 (V2) it is 15-48 %, against 31-84 % on 10-06, which had the larger input "
    "and less light. The output is at the same level: 100 % coverage at the wall on 10-07, 95-100 % on 10-06.</p>")
v["per_take"] = {
    S[0]: "Out 3-23 s, stands beside the light stand 28-64 s (YOLO hardly fires: belt-backed), crosses the door "
          "66-77 s, back 77-91 s. Camera stalls at +42, +52, +71, +76 s did not disturb it. "
          "V0: the point stays on the stand's belt 66-84 s. V1/V2: the point follows the walker.",
    S[1]: "Out 1-17 s, in front of the door 17-45 s with two crouches, back 45-55 s. Clean in V1/V2: no hole at the "
          "wall, no 2-point frame, no jump.",
    S[2]: "Right in front of the lens at ~4 s (white frame), out along the right side, camera stall 14.3-16.5 s "
          "(walker track lost), moves the stand 19-32 s, crosses the door 35-48 s, back 51-63 s. The orphaned slot "
          "D1 is held on the door, then near the stand's old place, by the corrupted snapshot (fg / coasting): "
          "15.7-40 s in V1, 15.7-21 s in V2. A point is also left on the door for ~4 s after the walker exits "
          "(63-67 s).",
    S[3]: "Enters from the left close to the camera, right of the door 14-24 s, left 28 s, right 31-42 s, back "
          "45-55 s. V1: one 1.2-height jump at 25.2 s (live). V2: a short snapshot-foreground hold and a "
          "2.1-height snap at 30.4 s, as the walker passes behind the moved stand.",
}
v["method"] = (
    "<ul>"
    "<li>Code: the main checkout (branch remote-ops, application/ = 46a0262 + a flow_check.py tool change). Replays: "
    "tmp_analysis/confirm_0710/run_variant.py. These are tests/replay.py's rows (TRT, frame clock = the take's .meta "
    "fps) plus observation-only extras: raw YOLO boxes before the size gate, person height in force, dancer_height, "
    "own size, belt static map. Its emitted stream is identical to tests/replay.py's (slot 5, V1: 0 / 1117 frames "
    "differ). V2 = tests/flow_check.py, unchanged, driven by flow_v2.py, which applies D27 + the recorded person "
    "heights in memory only; the project's config files are untouched.</li>"
    "<li>Engines: x@1280 = models/dev37. x@800 was built here (TRT 11.3, FP16, the same way as "
    "audit-2026-10/perf/build_engines.py, 3 min), because models/yolo11x-pose_800.engine is from an older TRT "
    "(tag 243 vs 244) and dev37 refuses it. FP16 results can differ slightly from the laptop's engines. No 1536 "
    "engine on dev37: 1536 numbers are geometry only.</li>"
    "<li>Walker ground truth (scene_measure.py): pixels that differ by &gt; 10 DN from both the take's empty start and "
    "its empty end (median frames), opened and tall-closed; the largest blob. It agrees with all 736 YOLO "
    "boxes &ge; 0.5 of V1 slot 2, and finds no blob where YOLO has none. It loses the dark legs, so the walker's "
    "height H = the V1 YOLO box height interpolated in time. Zones: near the lens H &ge; 400 px, far wall "
    "H &le; 160 px, walking otherwise. A point is on the walker when it is within max(0.6 H, half the blob + 20 px) "
    "in x and from 0.4 H above the blob top to 1.3 H below it.</li>"
    "<li>Times are frame / the take's fps. The camera stalls (1-2 s without frames, several per take) are one frame "
    "step in a replay, as in a recording. Lag comes from output_quality (fast moves against YOLO &ge; 0.5): few "
    "samples at x@800, so V0's 0 / 345 ms are noise.</li>"
    "<li>The snapshot defect is in the code, not in these settings: core/foreground.py process() sets _cur / _gain "
    "before the stale check, and pipeline.py calls update_plate() whenever last_fg is not None, stale frames "
    "included. Suggested fix (not applied, app code untouched): skip the plate update on invalid / stale frames, "
    "or when the gain is clamped.</li>"
    "<li>Side effect: Calibrate wrote projects/mur30m-0710/plates/flowcheck_flow_v2_slot_2_20261007_200742_1700.npz "
    "(flow_check's normal output); latest.npz and the configs are untouched.</li>"
    "</ul>")
(HERE / "verdict.json").write_text(json.dumps(v, indent=1))
print("wrote verdict.json")
