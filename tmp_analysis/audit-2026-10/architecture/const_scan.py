import ast, re, os, glob, json, collections
ROOT="/data/WallDance/application"
cfg=open(f"{ROOT}/src/core/config.py").read()
tree=ast.parse(cfg)
consts=[]
for node in tree.body:
    if isinstance(node,(ast.Assign,ast.AnnAssign)):
        targets = node.targets if isinstance(node,ast.Assign) else [node.target]
        for t in targets:
            if isinstance(t,ast.Name) and re.fullmatch(r"[A-Z][A-Z0-9_]*",t.id):
                consts.append((t.id,node.lineno))
def files(pat): return [f for f in glob.glob(pat,recursive=True) if "/.venv/" not in f and "__pycache__" not in f]
src=[f for f in files(f"{ROOT}/src/**/*.py") if not f.endswith("core/config.py")]
tests=files(f"{ROOT}/tests/**/*.py")+files(f"{ROOT}/*.py")+files("/data/WallDance/extra/**/*.py")
txt={f:open(f,encoding="utf-8",errors="replace").read() for f in src+tests}
# internal refs inside config.py (derived constants)
cfg_body_refs=collections.Counter(re.findall(r"\b[A-Z][A-Z0-9_]+\b",cfg))
# persisted keys in app.py config dict (string keys)
app=open(f"{ROOT}/src/app.py").read()
schema=open(f"{ROOT}/src/core/config_schema.py").read()
gui_files=[f for f in src if re.search(r"/(gui|gui_builder|gui_constants)\.py$|/ui/",f)]
live_files=[f for f in src if re.search(r"/core/(pipeline|tracker|gpu_pipeline|motion_detector|motion_model|enhancer|output_smoother|osc_output|background|ops_monitor)\.py$|/runtime/main_loop\.py$|/camera/",f)]
calib_files=[f for f in src if re.search(r"/core/(calibration|calib2|sensitivity_macro)\.py$|/runtime/calibration_flows\.py$",f)]
rows=[]
for name,ln in consts:
    pat=re.compile(r"\b"+name+r"\b")
    s=[f for f in src if pat.search(txt[f])]
    t=[f for f in tests if pat.search(txt[f])]
    nsrc=sum(len(pat.findall(txt[f])) for f in s)
    ntest=sum(len(pat.findall(txt[f])) for f in t)
    internal=cfg_body_refs[name]-1
    key=name.lower()
    persisted = (f'"{key}"' in app) or (f'"{key}"' in schema)
    cls=[]
    if any(f in live_files for f in s): cls.append("live")
    if any(f in calib_files for f in s): cls.append("calib")
    if any(f in gui_files for f in s): cls.append("gui")
    if s and not cls: cls.append("app/other")
    if not s and t: cls.append("TEST/TOOL-ONLY")
    if not s and not t and internal>0: cls.append("config-internal-only")
    if not s and not t and internal<=0: cls.append("DEAD")
    rows.append(dict(name=name,line=ln,src_refs=nsrc,src_files=len(s),test_refs=ntest,internal=internal,persisted_key=persisted,cls=",".join(cls),files=[os.path.relpath(f,ROOT) for f in s]))
json.dump(rows,open(os.path.join(os.path.dirname(__file__),"const_scan.json"),"w"),indent=1)
c=collections.Counter()
for r in rows:
    for k in r["cls"].split(","): c[k]+=1
print("total UPPER constants:",len(rows))
print("by class (multi-label):",dict(c))
print("persisted-key match:",sum(r["persisted_key"] for r in rows))
print("src_refs==0:",sum(1 for r in rows if r["src_refs"]==0))
for r in rows:
    if r["src_refs"]==0:
        print(f'  {r["name"]} (config.py:{r["line"]}) tests={r["test_refs"]} internal={r["internal"]}')
