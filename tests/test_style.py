"""House rule: no function longer than 30 lines, so each one stays easy to read and to change."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 30


def test_functions_are_short():
    long = []
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines = node.end_lineno - node.lineno + 1
                if lines > LIMIT:
                    long.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name} ({lines} lines)")
    assert not long, "Functions over the limit:\n" + "\n".join(long)
