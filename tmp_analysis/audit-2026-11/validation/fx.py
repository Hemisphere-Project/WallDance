import sys, json, dataclasses
sys.path.insert(0, 'tests'); sys.path.insert(0, 'src')
sys.dont_write_bytecode = True
import emitted_golden as eg
from core.identity_slots import SlotParams
print("env", eg.env_versions())
print("n SlotParams fields", len(dataclasses.fields(SlotParams)))
base = {}
for name in eg.fixtures():
    fx = eg.load_fixture(name)
    g = json.loads((eg.DIR / f"{name}.golden.json").read_text())
    cfg = fx["manifest"].get("config", {})
    rows, kpi = eg.simulate(fx)
    base[name] = eg.stream_of(rows)
    same = base[name] == g["stream"]
    print(name, "| src:", fx.get("source"), "| n rows", len(fx["rows"]), "| max", fx["max_dancers"], "| golden params", len(g["params"]), "| env", g.get("env"), "| exact-eq", same)
    print("   cfg: mode=%s conf=%s interm=%s imgsz=%s model=%s" % (cfg.get("tracking_mode"), cfg.get("confidence"), cfg.get("tracker_intermittent_confirm"), cfg.get("imgsz"), cfg.get("model")))
    print("   kpi:", {k: (round(v,4) if isinstance(v,float) else v) for k,v in kpi.items()})
# mutations
p0 = eg.shipped_params()
muts = {
 "smart_hold=False": dict(smart_hold=False),
 "stability=1.5": dict(stability=1.5),
 "static_guard=False,yield=False": dict(static_guard=False, static_yield=False),
}
fields = {f.name for f in dataclasses.fields(SlotParams)}
for cand in ("fg_veto","fg_hold","belt_backing","fg_enabled","use_fg","fg_backed_hold"):
    if cand in fields:
        muts[f"{cand}=False"] = {cand: False}
print("fields with fg/belt:", sorted(f for f in fields if 'fg' in f or 'belt' in f or 'fore' in f))
import test_emitted_golden as T
for mname, kw in muts.items():
    changed = 0; failed = 0; det = []
    for name in eg.fixtures():
        fx = eg.load_fixture(name)
        rows, kpi = eg.simulate(fx, dataclasses.replace(p0, **kw))
        s = eg.stream_of(rows)
        ch = s != base[name]
        changed += ch
        cov, ond, coast = T.FLOORS[name]
        ok = kpi["continuity.coverage"] >= cov and kpi["continuity.on_dancer"] >= ond and kpi["quality.coasting_share"] <= coast and kpi["quality.over_n_frames"] == 0
        failed += (not ok)
        det.append((name, ch, ok, kpi["quality.id_switches"], round(kpi["continuity.gap_max_s"],2)))
    print(mname, "changed", changed, "/6 ; floor fails", failed, "/6", det)
