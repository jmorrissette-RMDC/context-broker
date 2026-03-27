import ast
import os

dirs = ["app", "packages", "alerter"]
files_to_parse = []
for d in dirs:
    for root, _, files in os.walk(d):
        if "build" in root or "__pycache__" in root:
            continue
        for file in files:
            if file.endswith(".py"):
                files_to_parse.append(os.path.join(root, file))

for fp in files_to_parse:
    try:
        with open(fp, "r", encoding="utf-8") as f:
            code = f.read()
        tree = ast.parse(code)
        
        print(f"\n--- {fp} ---")
        docstring = ast.get_docstring(tree)
        if docstring:
            print(f"Module Doc: {docstring.splitlines()[0]}")
            
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                print(f"Function: {node.name}")
                doc = ast.get_docstring(node)
                if doc:
                    print(f"  Doc: {doc.splitlines()[0]}")
            elif isinstance(node, ast.ClassDef):
                print(f"Class: {node.name}")
                doc = ast.get_docstring(node)
                if doc:
                    print(f"  Doc: {doc.splitlines()[0]}")
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) or isinstance(item, ast.AsyncFunctionDef):
                        print(f"  Method: {item.name}")
    except Exception as e:
        print(f"Error parsing {fp}: {e}")
