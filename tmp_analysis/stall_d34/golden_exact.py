#!/usr/bin/env python3
"""TEST-2 emitted goldens, exactly: dump the full-precision emitted stream of every fixture
(tests/golden/emitted) for one application tree, to compare two trees byte for byte (the
golden test itself tolerates 1 px).

  golden_exact.py APP_DIR OUT.json      (APP_DIR = an `application/` folder: src + tests)
"""
import json
import sys
from pathlib import Path

app = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(app / "src"), str(app / "tests")]
import emitted_golden as eg  # noqa: E402

out = {}
for name in eg.fixtures():
    rows, kpi = eg.simulate(eg.load_fixture(name))
    out[name] = {"kpi": {k: repr(v) for k, v in kpi.items()},
                 "stream": [[r["frame"], [[t["id"], t["state"], repr(t["centroid"][0]), repr(t["centroid"][1]),
                                           [repr(v) for v in t["bbox"]]] for t in r["emitted"]["tracks"]]]
                            for r in rows]}
Path(sys.argv[2]).write_text(json.dumps(out))
print({k: len(v["stream"]) for k, v in out.items()})
