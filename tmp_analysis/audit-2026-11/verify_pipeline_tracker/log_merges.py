"""Read-only: co-located TRACK_MERGED events in an existing duo replay log; were both tracks skeleton-fed?"""
import json, sys
path = sys.argv[1]
ev = [json.loads(l) for l in open(path) if l.strip()]
summ = {}
for e in ev:
    if e.get("event") == "FRAME_SUMMARY":
        summ[e["frame"]] = e["data"]
frames = sorted(summ)
print(path.split("/sessions/")[-1], "frames", len(frames), "range", frames[0], frames[-1])
settings = [e for e in ev if e.get("event") in ("SETTINGS", "SESSION_SETTINGS")]
def emitted(fr):
    d = summ.get(fr) or {}
    return d.get("emitted_slots") if d.get("emitted_slots") is not None else d.get("emitted")
n2 = sum(1 for fr in frames if len(emitted(fr) or []) >= 2)
print("frames with >= 2 emitted ids: %d of %d (%.1f %%)  [emitted_slots present: %s]"
      % (n2, len(frames), 100.0 * n2 / len(frames), any("emitted_slots" in summ[f] for f in frames)))
for e in ev:
    if e.get("event") != "TRACK_MERGED" or "mode" in e.get("data", {}):
        continue
    d = e["data"]; fr = e["frame"]
    prev = max([f for f in frames if f < fr], default=None)
    ts = {t["id"]: t for t in (summ.get(prev, {}).get("track_states") or [])}
    v, k = ts.get(d["victim_id"]), ts.get(d["keeper_id"])
    # streak window: how many of the 8 frames before had BOTH tracks fed by a real skeleton (src yolo, fss 0)?
    both = 0; seen = 0
    for f in [x for x in frames if fr - 9 < x < fr]:
        s = {t["id"]: t for t in summ[f]["track_states"]}
        a, b = s.get(d["victim_id"]), s.get(d["keeper_id"])
        if a and b:
            seen += 1
            if a.get("src") == "yolo" and b.get("src") == "yolo":
                both += 1
    # after: frames until 2 ids are emitted again
    after = next((f - fr for f in frames if f > fr and len(emitted(f) or []) >= 2), None)
    before2 = len(emitted(prev) or []) >= 2 if prev is not None else None
    def desc(t):
        return None if t is None else "src=%s fss=%s t_miss=%s emit=%s hits=%s occ=%s" % (
            t.get("src"), t.get("fss"), t.get("t_miss"), t.get("emit"), t.get("hits"), t.get("occluded"))
    print(" f%-5d victim %s (%s) | keeper %s (%s) | dist %.0f px | both YOLO-fed on %d of the %d prior frames | 2 ids emitted before: %s, again after: %s frames"
          % (fr, d["victim_id"], desc(v), d["keeper_id"], desc(k), d["distance"], both, seen, before2, after))
