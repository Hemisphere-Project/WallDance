import ast, sys, re
src = open('src/core/identity_slots.py').read()
tree = ast.parse(src)
for n in tree.body:
    if isinstance(n, ast.ClassDef) and n.name == 'SlotParams':
        fields = [(s.target.id, ast.unparse(s.annotation), ast.unparse(s.value), s.lineno) for s in n.body if isinstance(s, ast.AnnAssign)]
print('SlotParams fields:', len(fields))
# classify units by name suffix
bykind = {}
for name, ann, val, ln in fields:
    if name.endswith('_s') or '_s_' in name or name.endswith('_tau_s'):
        k = 'seconds'
    elif name.endswith('_h') or name.endswith('_h2') or '_h_per_s' in name:
        k = 'heights'
    elif ann == 'bool':
        k = 'bool'
    elif ann == 'int':
        k = 'int(frames/count)'
    elif ann == 'str':
        k='str'
    else:
        k = 'ratio/other'
    bykind.setdefault(k, []).append((name, val))
for k, v in bykind.items():
    print(k, len(v), v)
