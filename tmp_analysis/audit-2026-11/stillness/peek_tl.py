import json, sys
p = sys.argv[1]
d = json.load(open(p))
print(type(d).__name__, len(d))
for i in (0, 1200, 2500, 2501):
    print(i, json.dumps(d[i])[:2200])
    print()
