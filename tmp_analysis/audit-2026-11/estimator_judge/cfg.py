import ast, sys, re
trk = open('src/core/tracker.py').read()
cfg_src = open('src/core/config.py').read()
cfg_lines = cfg_src.splitlines()
t = ast.parse(trk)
names = []
for n in t.body:
    if isinstance(n, ast.ImportFrom) and n.module == 'core.config':
        names = [a.name for a in n.names]
print("imported from core.config:", len(names))
c = ast.parse(cfg_src)
vals = {}
for n in c.body:
    if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
        nm = n.targets[0].id
        seg = ast.get_source_segment(cfg_src, n.value)
        # trailing comment on the line
        line = cfg_lines[n.lineno-1]
        cm = line.split('#',1)[1].strip() if '#' in line else ''
        vals[nm] = (n.lineno, seg, cm)
    elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
        nm = n.target.id
        seg = ast.get_source_segment(cfg_src, n.value)
        line = cfg_lines[n.lineno-1]
        cm = line.split('#',1)[1].strip() if '#' in line else ''
        vals[nm] = (n.lineno, seg, cm)
for nm in names:
    v = vals.get(nm)
    if v is None:
        print(f"{nm:45s} ??")
    else:
        print(f"{nm:45s} L{v[0]:<5d} = {v[1][:28]:28s} # {v[2][:90]}")
