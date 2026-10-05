import ast, os, sys, collections
SRC = "/data/WallDance/application/src"
mods = {}
for dp, dn, fn in os.walk(SRC):
    if "__pycache__" in dp: continue
    for f in fn:
        if f.endswith(".py"):
            p = os.path.join(dp, f); rel = os.path.relpath(p, SRC)[:-3].replace(os.sep, ".")
            if rel.endswith(".__init__"): rel = rel[:-9]
            mods[rel] = p
def resolve(name):
    # longest prefix match among internal modules
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in mods: return cand
    return None
edges = collections.defaultdict(set); lazy = collections.defaultdict(set)
for m, p in mods.items():
    tree = ast.parse(open(p).read())
    pkg = m.rsplit(".",1)[0] if "." in m else ""
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = m.split(".")[:-node.level] if not mods[m].endswith("__init__.py") else m.split(".")[:len(m.split("."))-node.level+1]
                modname = ".".join(base + ([node.module] if node.module else []))
            else:
                modname = node.module or ""
            names = [modname] + [modname + "." + a.name for a in node.names]
        for n in names:
            r = resolve(n)
            if r and r != m:
                # lazy if inside function
                edges[m].add(r)
    # detect function-level imports
    for fnode in ast.walk(tree):
        if isinstance(fnode, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fnode):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    nm = [a.name for a in node.names] if isinstance(node, ast.Import) else [(node.module or "")] + [(node.module or "")+"."+a.name for a in node.names]
                    for n in nm:
                        r = resolve(n)
                        if r and r != m: lazy[m].add(r)
def layer(m): return m.split(".")[0] if "." in m else m
print("modules:", len(mods))
for m in sorted(mods):
    e = sorted(edges[m])
    print(f"{m:32s} -> {', '.join(x + ('*' if x in lazy[m] else '') for x in e)}")
print("\nLayer edges (pkg -> pkg : count):")
lc = collections.Counter()
for m, es in edges.items():
    for e in es:
        a, b = layer(m), layer(e)
        if a != b: lc[(a,b)] += 1
for (a,b),c in sorted(lc.items()): print(f"  {a:14s} -> {b:14s} {c}")
# cycles (SCC)
idx = {}; low = {}; st=[]; on=set(); sccs=[]; i=[0]
sys.setrecursionlimit(10000)
def sc(v):
    idx[v]=low[v]=i[0]; i[0]+=1; st.append(v); on.add(v)
    for w in edges[v]:
        if w not in idx: sc(w); low[v]=min(low[v],low[w])
        elif w in on: low[v]=min(low[v],idx[w])
    if low[v]==idx[v]:
        comp=[]
        while True:
            w=st.pop(); on.discard(w); comp.append(w)
            if w==v: break
        if len(comp)>1: sccs.append(comp)
for v in list(mods):
    if v not in idx: sc(v)
print("\nImport cycles (SCCs >1):")
for c in sccs: print("  ", sorted(c))
# fan-in
fi = collections.Counter(e for es in edges.values() for e in es)
print("\nFan-in top:", fi.most_common(15))
print("Fan-out top:", sorted(((len(v),k) for k,v in edges.items()), reverse=True)[:12])
