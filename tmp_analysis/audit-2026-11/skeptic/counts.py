import ast, re, glob, os
def cls_fields(path, name):
    t=ast.parse(open(path).read())
    for n in ast.walk(t):
        if isinstance(n, ast.ClassDef) and n.name==name:
            return [s.target.id for s in n.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)]
t=ast.parse(open('src/core/config.py').read())
names=[]
for s in t.body:
    if isinstance(s, ast.Assign):
        for tg in s.targets:
            if isinstance(tg, ast.Name) and tg.id.isupper(): names.append(tg.id)
    elif isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name) and s.target.id.isupper(): names.append(s.target.id)
print('config consts', len(names), 'unique', len(set(names)))
print('SlotParams', len(cls_fields('src/core/identity_slots.py','SlotParams')))
print('ProcessingSettings', len(cls_fields('src/core/pipeline.py','ProcessingSettings')))
# commands in api.py
t=ast.parse(open('src/runtime/api.py').read())
classes=[n for n in t.body if isinstance(n, ast.ClassDef)]
print('api classes', len(classes))
bases={}
for c in classes:
    bases[c.name]=[getattr(b,'id',getattr(b,'attr',None)) for b in c.bases]
import collections
print(collections.Counter(tuple(v) for v in bases.values()))
# env switches
envs=set()
for root in ('src','tests','../launcher','../extra'):
    for p in glob.glob(root+'/**/*.py', recursive=True):
        if '.venv' in p: continue
        for m in re.finditer(r'environ(?:\.get)?\s*[\[(]\s*["\'](WD_[A-Z0-9_]+|WALLDANCE_[A-Z0-9_]+)', open(p, errors='ignore').read()):
            envs.add((m.group(1), root))
print(sorted(envs)); print(len({e for e,_ in envs}))
