import sys, json, copy
sys.dont_write_bytecode = True
sys.path.insert(0, 'tests'); sys.path.insert(0, 'src')
import emitted_golden as eg, scoring, output_quality
for name in ("white-duo-full", "bdx-s5-static-ghost", "texture-duo-win", "hangar-aerial-win"):
    fx = eg.load_fixture(name)
    rows, kpi = eg.simulate(fx)
    man = fx["manifest"]
    fps = float(man.get("fps") or 19.8)
    tr = scoring.score_timeline(rows, man)
    em_rows = [dict(r, reported=r["emitted"]["reported"], ids=r["emitted"]["ids"]) for r in rows]
    em = scoring.score_timeline(em_rows, man)
    rep = output_quality.compare_streams(rows, man, fps=fps)
    t = rep["tracker"]; e = rep["emitted"]
    print(name, "| count score tracker %.3f emitted %.3f" % (tr["score"], em["score"]),
          "| on_dancer tracker %s emitted %s" % (t["continuity"].get("on_dancer"), e["continuity"].get("on_dancer")),
          "| cov tracker %s emitted %s" % (t["continuity"].get("coverage"), e["continuity"].get("coverage")),
          "| pass line:", man.get("pass"))
    try:
        print("    emitted pass verdict:", scoring.evaluate_pass(em, man).get("passed"), "| tracker:", scoring.evaluate_pass(tr, man).get("passed"))
    except Exception as ex:
        print("    evaluate_pass err", ex)
