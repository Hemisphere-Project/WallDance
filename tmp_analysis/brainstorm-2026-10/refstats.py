import json, sys, math, collections
tl = json.load(open(sys.argv[1])); man = json.load(open(sys.argv[2]))
spots = man.get('reference', {}).get('exclude_spots', [])
for thr in (0.5, 0.25, 0.10):
    c = collections.Counter()
    for r in tl:
        if r['frame'] < 15: continue
        refs = [q for q in r.get('ref') or [] if (q['conf'] or 1) >= thr and not any(math.hypot(q['c'][0]-sx, q['c'][1]-sy) <= sr for sx, sy, sr in spots)]
        c[min(len(refs), 3)] += 1
    print('ref conf >= %.2f (spots excluded): frames by count' % thr, dict(sorted(c.items())))
