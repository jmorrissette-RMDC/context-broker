import ast
import os

dirs = ['tests']
files_to_parse = []
for d in dirs:
    for root, _, files in os.walk(d):
        if 'build' in root or '__pycache__' in root:
            continue
        for file in files:
            if file.endswith('.py'):
                files_to_parse.append(os.path.join(root, file))

for fp in files_to_parse:
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            code = f.read()
        tree = ast.parse(code)
        
        has_mock = 'patch' in code or 'mock' in code or 'monkeypatch' in code or 'httpx_mock' in code
        print(f'\n--- {fp} (MOCK: {has_mock}) ---')
        docstring = ast.get_docstring(tree)
        if docstring:
            print(f'File Doc: {docstring.splitlines()[0]}')
            
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                if node.name.startswith('test_'):
                    doc = ast.get_docstring(node)
                    doc_preview = doc.splitlines()[0] if doc else ""
                    print(f'Test: {node.name} - {doc_preview}')
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) or isinstance(item, ast.AsyncFunctionDef):
                        if item.name.startswith('test_'):
                            doc = ast.get_docstring(item)
                            doc_preview = doc.splitlines()[0] if doc else ""
                            print(f'Test: {node.name}.{item.name} - {doc_preview}')
    except Exception as e:
        print(f'Error parsing {fp}: {e}')
