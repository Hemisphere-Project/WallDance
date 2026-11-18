import ast, sys, re, json
src = open('src/app.py').read()
tree = ast.parse(src)
save=set(); apply_=set()
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name=='_get_saveable_config':
        for n in ast.walk(node):
            if isinstance(n, ast.Dict):
                for k in n.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value,str): save.add(k.value)
    if isinstance(node, ast.FunctionDef) and node.name=='_apply_config_without_model':
        for n in ast.walk(node):
            # "x" in config ; config["x"]; config.get("x")
            if isinstance(n, ast.Compare) and isinstance(n.left, ast.Constant) and isinstance(n.left.value,str):
                apply_.add(n.left.value)
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id=='config':
                s=n.slice
                if isinstance(s, ast.Constant): apply_.add(s.value)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr=='get' and isinstance(n.func.value, ast.Name) and n.func.value.id=='config':
                if n.args and isinstance(n.args[0], ast.Constant): apply_.add(n.args[0].value)
            if isinstance(n, ast.Tuple):
                for e in n.elts:
                    if isinstance(e, ast.Tuple) and e.elts and isinstance(e.elts[0], ast.Constant) and isinstance(e.elts[0].value,str):
                        apply_.add(e.elts[0].value)
                    if isinstance(e, ast.Constant) and isinstance(e.value,str) and e.value.startswith('roi_'): apply_.add(e.value)
print('save', len(save)); print('apply', len(apply_))
print('apply-not-save', sorted(apply_-save))
print('save-not-apply', sorted(save-apply_))
json.dump({'save':sorted(save),'apply':sorted(apply_)}, open(sys.argv[1],'w'))
