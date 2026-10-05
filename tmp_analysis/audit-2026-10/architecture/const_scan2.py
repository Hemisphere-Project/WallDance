import ast, re, os, glob, json, collections
ROOT="/data/WallDance/application"
rows=json.load(open(os.path.join(os.path.dirname(__file__),"const_scan.json")))
names={r["name"] for r in rows}
def files(pat): return [f for f in glob.glob(pat,recursive=True) if "/.venv/" not in f and "__pycache__" not in f]
src=[f for f in files(f"{ROOT}/src/**/*.py") if not f.endswith("core/config.py")]
tests=files(f"{ROOT}/tests/**/*.py")+files(f"{ROOT}/*.py")+files("/data/WallDance/extra/**/*.py")
def loads(fs):
    c=collections.Counter(); where=collections.defaultdict(set)
    for f in fs:
        try: t=ast.parse(open(f,encoding="utf-8",errors="replace").read())
        except SyntaxError: continue
        for n in ast.walk(t):
            if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load) and n.id in names:
                c[n.id]+=1; where[n.id].add(os.path.relpath(f,ROOT))
            elif isinstance(n,ast.Attribute) and n.attr in names:
                c[n.attr]+=1; where[n.attr].add(os.path.relpath(f,ROOT))
            elif isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value in names:
                c[n.value]+=1; where[n.value].add(os.path.relpath(f,ROOT)+"(str)")
    return c,where
# config-internal uses
cfgt=ast.parse(open(f"{ROOT}/src/core/config.py").read())
ci=collections.Counter(n.id for n in ast.walk(cfgt) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load) and n.id in names)
sc,sw=loads(src); tc,tw=loads(tests)
import_only=[]; test_only=[]; internal_only=[]; dead=[]
for r in rows:
    n=r["name"]
    r["src_uses"]=sc[n]; r["test_uses"]=tc[n]; r["cfg_internal"]=ci[n]; r["use_files"]=sorted(sw[n])
    if sc[n]==0:
        if r["src_refs"]>0: import_only.append(n)
        if tc[n]>0: test_only.append(n)
        elif ci[n]>0: internal_only.append(n)
        else: dead.append(n)
json.dump(rows,open(os.path.join(os.path.dirname(__file__),"const_scan2.json"),"w"),indent=1)
print("total",len(rows))
print("used (load) in src:",sum(1 for r in rows if r["src_uses"]>0))
print("imported in src but never read:",len(import_only)); 
for n in import_only: 
    r=[x for x in rows if x["name"]==n][0]; print("   ",n,f"config.py:{r['line']}", "imported by",r["files"], "tests",r["test_uses"],"cfg-internal",r["cfg_internal"])
print("no src use, used only by tests/tools:",test_only)
print("no src/test use, only feeds other config consts:",internal_only)
print("fully dead:",dead)
