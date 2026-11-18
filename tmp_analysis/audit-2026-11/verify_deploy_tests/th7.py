import json, sys, gzip
from pathlib import Path
T = Path('/data/WallDance/application/tests')
sys.path.insert(0, str(T)); sys.path.insert(0, str(T.parent/'src'))
import emitted_golden as eg
import test_emitted_golden as teg
print("env here:", eg.env_versions())
for name in eg.fixtures():
    g = json.loads((eg.DIR/f"{name}.golden.json").read_text())
    fx = eg.load_fixture(name)
    man = fx["manifest"]
    k = g["kpi"]
    cov, ond, coast = teg.FLOORS[name]
    print(f"\n{name}: env={g.get('env')} source={fx.get('source')!r} rows={len(fx['rows'])} max_dancers={fx['max_dancers']}")
    print(f"  golden kpi: coverage={k['continuity.coverage']} on_dancer={k['continuity.on_dancer']} coasting={k['quality.coasting_share']} gap_max_s={k['continuity.gap_max_s']} id_switches={k['quality.id_switches']} jitter={k['quality.jitter_rest_pct']}")
    print(f"  floor: coverage>={cov} on_dancer>={ond} coasting<={coast}  -> margins: cov {k['continuity.coverage']-cov:+.4f}, ond {k['continuity.on_dancer']-ond:+.4f}, coast {coast-k['quality.coasting_share']:+.4f}")
    print(f"  manifest pass: {json.dumps(man.get('pass'))}")
    # which row keys exist in fixture tracks
    keys=set()
    for r in fx['rows'][:200]:
        for t in r.get('tracks') or []:
            keys |= set(t)
    print(f"  fixture track keys: {sorted(keys)}")
