# The 08/10 presence truth (Plan Cordée rev 2, M1)

Thirteen takes of the façade rehearsal of 2026-10-08 (`jeudi0809_mur` 1–5, `jeudi0809_mur2` 1–8), one file per
take: who is on the wall on every frame, where, and in what state. The takes themselves stay on mgr-ran
(`/data/WallDance/projects/jeudi0809_mur*/recordings/`); only the truth is here.

## How it was made

1. `truth_build.py blobs` — foreground groups per frame from a half-size proxy of the take: a temporal-median
   background (re-estimated without the first pass's foreground, patched where a dancer hung still), a
   difference over 22 levels inside the wall polygon, components joined at about half a body.
2. `<stem>.plan.json` — declared by hand from the x(t)/y(t) plots and contact sheets: how many dancers at
   frame 0, where, and every entry and exit (`plan` inside each file keeps it).
3. `truth_build.py build` — a tracker that may not create or drop a dancer outside the declared events assigns
   the groups. A rebuild from the same blobs and plan gives the same frames, positions, states and boxes; the
   body height `h_take` depends on the replay used for support, so the stored value is the reference.

It is independent of the detector being scored (background subtraction and hand-declared presence), and it says
where dancers are, not which is which after a contact.

## The file

`<stem>.npz`, read by `truth_score.Truth.load`: `stem`, `frames`, `size` (portrait w, h), `h_take` (median body
height, px), and one row per dancer-frame — `f`, `d` (0 = A), `x10`, `y10` (group centroid, tenths of a px),
`st` (0 own group · 1 shared group = contact · 2 coasted, interpolated · 3 uncertain), `box` (group box, px),
`bg` and `clip` (mean level and clipped share of the take's background under the box); `meta` (the take's
start, fps, exposure, gain, enhancement) and `plan` as JSON.

## Scoring

```
python tests/truth_score.py summary --replays <dir of <stem>.ship.jsonl> [--live <dir of live_*.csv>] [--check summary.json]
python tests/truth_score.py replay tests/truth0810/<stem>.npz <stem>.<tag>.jsonl
```

On 2026-11-26 the repo's scorer reproduced the counter-analysis summary (`tmp_analysis/counter-0810/score/
summary.json`, mgr-ran) exactly, every take and every number (`test_gate_reproduces_counter_analysis`).
