import re, sys, numpy as np
from collections import defaultdict
for f in sys.argv[1:]:
    d = defaultdict(list); n = 0
    for line in open(f, errors="replace"):
        if not line.startswith("[Budget]"): continue
        fps = re.search(r"FPS=([\d.]+)", line)
        kv = dict(re.findall(r"(\w+)=([\d.]+)", line.split("(FPS")[0]))
        if float(kv.get("process_wall", 0)) > 200: continue   # first/warm-up line
        n += 1
        for k, v in kv.items(): d[k].append(float(v))
        if fps: d["FPS"].append(float(fps.group(1)))
    print(f"== {f}: {n} budget lines")
    for k, v in d.items():
        a = np.array(v)
        print(f"  {k:20s} n={len(a):3d} p50 {np.percentile(a,50):6.1f}  mean {a.mean():6.1f}  p95 {np.percentile(a,95):6.1f}  max {a.max():6.1f}")
