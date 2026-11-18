import ast, sys
p = sys.argv[1]
src = open(p).read()
tree = ast.parse(src)
lines = src.count('\n')+1
def walk(node, prefix=''):
    for n in node.body:
        if isinstance(n, ast.ClassDef):
            print(f"{n.lineno:5d}-{n.end_lineno:5d} ({n.end_lineno-n.lineno+1:4d}) class {n.name}")
            walk(n, prefix+n.name+'.')
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            print(f"{n.lineno:5d}-{n.end_lineno:5d} ({n.end_lineno-n.lineno+1:4d})   {prefix}{n.name}")
walk(tree)
