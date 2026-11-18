import ast, subprocess, sys
def keys(rev, path, fn):
    try: src=subprocess.check_output(['git','show',f'{rev}:{path}'], stderr=subprocess.DEVNULL).decode()
    except Exception: return None
    t=ast.parse(src); out=set()
    for node in ast.walk(t):
        if isinstance(node, ast.FunctionDef) and node.name==fn:
            for n in ast.walk(node):
                if isinstance(n, ast.Dict):
                    for k in n.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value,str): out.add(k.value)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr=='get' and isinstance(n.func.value, ast.Name) and n.func.value.id=='config' and n.args and isinstance(n.args[0], ast.Constant): out.add(n.args[0].value)
                if isinstance(n, ast.Compare) and isinstance(n.left, ast.Constant) and isinstance(n.left.value,str): out.add(n.left.value)
                if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id=='config' and isinstance(n.slice, ast.Constant): out.add(n.slice.value)
    return out
for rev in ('0a987af','4fbace3','HEAD'):
    s=keys(rev,'application/src/app.py','_get_saveable_config')
    a=keys(rev,'application/src/app.py','_apply_config_without_model')
    r=keys(rev,'application/tests/replay.py','_build_processor')
    print(rev, 'save', len(s) if s else None, 'apply', len(a) if a else None, 'replay', len(r) if r else None)
    if r and a: print('   replay-not-apply', sorted(r-a-{'fg_plate_path'}))
