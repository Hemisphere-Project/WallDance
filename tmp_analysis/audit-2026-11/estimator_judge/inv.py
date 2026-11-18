import ast, sys
p = sys.argv[1]
src = open(p).read()
tree = ast.parse(src)
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        print(f"FUNC  {node.name:55s} {node.lineno:5d}-{node.end_lineno:5d} {node.end_lineno-node.lineno+1:5d}")
    elif isinstance(node, ast.ClassDef):
        print(f"CLASS {node.name:55s} {node.lineno:5d}-{node.end_lineno:5d} {node.end_lineno-node.lineno+1:5d}")
        for m in node.body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                print(f"   .  {m.name:55s} {m.lineno:5d}-{m.end_lineno:5d} {m.end_lineno-m.lineno+1:5d}")
