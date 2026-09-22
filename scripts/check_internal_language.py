"""Check maintained internal text without reading ignored local configuration."""

from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tokenize


ROOT = Path(__file__).resolve().parents[1]
HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]")
TEXT_SUFFIXES = {".py", ".spec", ".md", ".toml", ".ps1", ".yml", ".yaml", ".txt"}
MANIFEST = Path(__file__).with_name("internal_language_exceptions.json")


def repository_files(root: Path = ROOT) -> list[Path]:
    """Include tracked and new visible files, but exclude ignored user data."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, check=True, capture_output=True,
    )
    names = set(result.stdout.decode("utf-8").split("\0")) - {""}
    return sorted(root / name for name in names if (root / name).is_file())


def excluded(name: str) -> bool:
    """Keep copied upstream code and historical/user documents outside migration."""
    return (
        "/export/" in name
        or name.startswith("core/tools/zhconv/")
        or name.startswith(("docs/archive/", "docs/user/"))
        or name in {"readme.md", "docs/README.md"}
    )


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_python(name: str, source: str, exceptions: dict) -> list[str]:
    """Check comments, documentation strings, identifiers, and remaining literals."""
    tree = ast.parse(source, filename=name)
    docs = {
        (node.lineno, node.col_offset)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    approved = exceptions.get(name, {}).get("tokens", [])
    errors = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if not HAN.search(token.string):
            continue
        is_literal = token.type == tokenize.STRING and token.start not in docs
        # Fixtures/catalog payloads can be multilingual; their internal prose cannot.
        if is_literal and (
            name.startswith("tests/") or name == "core/i18n/zh_cn.py"
        ):
            continue
        if token.type == tokenize.STRING and digest(token.string) in approved:
            continue
        errors.append(f"{name}:{token.start[0]}: non-English internal token ({tokenize.tok_name[token.type]})")
    return errors


def check_text(name: str, source: str, exceptions: dict) -> list[str]:
    """Check other maintained text with exact, documented line exceptions."""
    approved = exceptions.get(name, {}).get("lines", [])
    return [
        f"{name}:{number}: non-English internal text"
        for number, line in enumerate(source.splitlines(), 1)
        if HAN.search(line) and digest(line.strip()) not in approved
    ]


def main() -> int:
    exceptions = json.loads(MANIFEST.read_text(encoding="utf-8"))
    errors = []
    checked = 0
    for path in repository_files():
        name = path.relative_to(ROOT).as_posix()
        if excluded(name) or (path.suffix not in TEXT_SUFFIXES and name != ".gitignore"):
            continue
        source = path.read_text(encoding="utf-8-sig")
        checked += 1
        if path.suffix in {".py", ".spec"}:
            errors.extend(check_python(name, source, exceptions))
        else:
            errors.extend(check_text(name, source, exceptions))
    for error in errors:
        print(error)
    print(f"Internal language: {checked} files checked; {len(errors)} violations.")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
