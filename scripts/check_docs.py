"""Validate repository Markdown links, anchors, and basic page structure."""

from __future__ import annotations

import html
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

try:
    from .check_internal_language import ROOT, repository_files
except ImportError:
    from check_internal_language import ROOT, repository_files


LINK = re.compile(r"!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)")


def prose_lines(source: str):
    """Skip fenced code while retaining source line numbers."""
    fence = None
    for number, line in enumerate(source.splitlines(), 1):
        match = re.match(r"\s*(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None:
            yield number, line


def anchors(source: str) -> set[str]:
    """Build GitHub-style heading slugs, including duplicate suffixes."""
    counts: dict[str, int] = {}
    result = set(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', source))
    for _, line in prose_lines(source):
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", match.group(1))
        title = html.unescape(re.sub(r"<[^>]*>", "", title)).lower()
        slug = re.sub(r"[^\w\- ]", "", title).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(slug if count == 0 else f"{slug}-{count}")
    return result


def check_page(path: Path, root: Path = ROOT) -> list[str]:
    name = path.relative_to(root).as_posix()
    source = path.read_text(encoding="utf-8-sig")
    errors = []
    headings = [(number, len(m.group(1))) for number, line in prose_lines(source)
                if (m := re.match(r"^(#{1,6})\s+", line))]
    historical = name.startswith("docs/archive/")
    if not historical:
        if sum(level == 1 for _, level in headings) != 1:
            errors.append(f"{name}: expected one H1 title")
        previous = 0
        for number, level in headings:
            if level > previous + 1:
                errors.append(f"{name}:{number}: skipped heading level")
            previous = level
    for number, line in prose_lines(source):
        for match in LINK.finditer(line):
            target = match.group(1).strip("<>")
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            dest = (path.parent / unquote(url.path)).resolve() if url.path else path
            # The dated incident links to ignored local audio and rotating logs.
            if name == "docs/archive/QWEN-ASR-2026-08-09.md" and (
                target.rstrip("/") == "../../2026/08/assets"
                or target in {"../../logs/client_latest.log", "../../logs/server_latest.log"}
            ):
                continue
            if not dest.is_relative_to(root.resolve()) or not dest.exists():
                errors.append(f"{name}:{number}: missing local target: {target}")
            elif url.fragment and dest.is_file() and dest.suffix == ".md":
                if unquote(url.fragment) not in anchors(dest.read_text(encoding="utf-8-sig")):
                    errors.append(f"{name}:{number}: missing heading: {target}")
    return errors


def main() -> int:
    pages = [path for path in repository_files() if path.suffix == ".md"]
    errors = [error for path in pages for error in check_page(path)]
    for error in errors:
        print(error)
    print(f"Documentation: {len(pages)} pages checked; {len(errors)} violations.")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
