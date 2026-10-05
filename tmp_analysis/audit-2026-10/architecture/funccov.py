import json, ast, sys
cov = json.load(open(sys.argv[1]))
for rel in sys.argv[2:]:
    path = "src/" + rel
    d = cov["files"].get(path)
    if not d: print("no data", path); continue
    executed = set(d["executed_lines"]); missing = set(d["missing_lines"])
    tree = ast.parse(open(path).read())
    rows = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines = set(range(node.lineno, node.end_lineno + 1))
            ex = len(lines & executed); mi = len(lines & missing)
            if ex + mi == 0: continue
            rows.append((node.lineno, node.name, ex, mi, node.end_lineno - node.lineno + 1))
    print(f"== {rel}")
    rows.sort()
    for ln, name, ex, mi, size in rows:
        if size >= 25:
            pct = 100 * ex / (ex + mi)
            print(f"  {ln:5d} {name:45s} {size:4d} lines  {pct:5.0f}%")
