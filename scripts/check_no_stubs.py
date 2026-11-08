"""Check production Python for unfinished implementations and unreferenced API functions."""

import ast
import re
from pathlib import Path


def check_file(path: Path, root: Path, references: set[str]) -> list[str]:
    source = path.read_text(encoding="utf-8")
    relative = path.relative_to(root)
    errors = []
    if re.search(r"\bTO[D]O\b", source):
        errors.append(f"{relative}: unfinished marker")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return errors + [f"{relative}:{exc.lineno}: {exc.msg}"]
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name) and node.id == "NotImplementedError"
        ) or (
            isinstance(node, ast.Attribute) and node.attr == "NotImplementedError"
        ):
            errors.append(f"{relative}:{node.lineno}: unimplemented exception")
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        if not body or all(isinstance(statement, ast.Pass) for statement in body):
            errors.append(f"{relative}:{node.lineno}: empty function {node.name}")
        if relative.parts[:3] == ("src", "ndis", "api") and node.name not in references:
            errors.append(f"{relative}:{node.lineno}: API function {node.name} has no test reference")
    return errors


def check_repository(root: Path) -> list[str]:
    tests = list((root / "tests").rglob("test_*.py")) + list((root / "evals").rglob("test_*.py"))
    references = set()
    for test in tests:
        for node in ast.walk(ast.parse(test.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name):
                references.add(node.id)
            elif isinstance(node, ast.Attribute):
                references.add(node.attr)
    paths = sorted((root / "src").rglob("*.py")) + sorted((root / "scripts").rglob("*.py"))
    return [error for path in paths for error in check_file(path, root, references)]


def main() -> int:
    errors = check_repository(Path(__file__).resolve().parents[1])
    if errors:
        print("\n".join(errors))
        return 1
    print("No-stubs and API-test-reference checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
