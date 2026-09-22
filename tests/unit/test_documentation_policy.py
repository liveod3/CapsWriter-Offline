"""Keep language and documentation guards strict without translating semantic data."""

from pathlib import Path

from scripts.check_docs import anchors, check_page
from scripts.check_internal_language import check_python, check_text, digest, excluded


def test_internal_prose_and_identifiers_are_checked_even_in_fixture_files():
    assert check_python("tests/unit/example.py", '# 中文 comment\n', {})
    assert check_python("tests/unit/example.py", '"""中文 documentation."""\n', {})
    assert check_python("core/example.py", '中文 = 1\n', {})
    assert not check_python("tests/unit/example.py", 'value = "中文 fixture"\n', {})


def test_exceptions_allow_only_the_reviewed_string_token():
    name = "core/example.py"
    approved = {name: {"tokens": [digest('"中文"')]}}
    assert not check_python(name, 'rule = "中文"\n', approved)
    assert check_python(name, 'raise ValueError("新错误")\n', approved)
    assert check_python(name, '# 中文\n', approved)


def test_text_exceptions_do_not_allow_new_prose():
    name = "docs/development/example.md"
    approved = {name: {"lines": [digest('UI label: `简体中文`.')]}}
    assert not check_text(name, 'UI label: `简体中文`.\n', approved)
    assert check_text(name, '新说明\n', approved)


def test_migration_excludes_upstream_but_includes_maintained_inference():
    assert excluded("core/server/engines/qwen_asr_gguf/export/model.py")
    assert excluded("core/tools/zhconv/zhconv.py")
    assert not excluded("core/server/engines/qwen_asr_gguf/inference/asr.py")
    assert not excluded("docs/development/localization.md")


def test_heading_slugs_preserve_duplicates_and_skip_fenced_examples():
    result = anchors('# Title\n## Repeat\n## Repeat\n```md\n# Example\n```\n## 设置语言\n')
    assert result == {"title", "repeat", "repeat-1", "设置语言"}


def test_links_support_relative_paths_and_encoded_anchors(tmp_path: Path):
    page = tmp_path / "index.md"
    (tmp_path / "guide.md").write_text('# Guide\n## 设置语言\n', encoding="utf-8")
    page.write_text(
        '# Index\n\n[Guide](guide.md#%E8%AE%BE%E7%BD%AE%E8%AF%AD%E8%A8%80)\n'
        '[External](https://example.com/missing)\n```md\n[Example](absent.md)\n```\n',
        encoding="utf-8",
    )
    assert check_page(page, tmp_path) == []
    page.write_text('# Index\n\n[Bad](guide.md#missing)\n[Missing](absent.md)\n', encoding="utf-8")
    assert len(check_page(page, tmp_path)) == 2


def test_page_structure_rejects_missing_title_and_skipped_level(tmp_path: Path):
    page = tmp_path / "guide.md"
    page.write_text('## No title\n#### Skipped\n', encoding="utf-8")
    errors = check_page(page, tmp_path)
    assert any("one H1" in error for error in errors)
    assert any("skipped heading" in error for error in errors)


def test_private_evidence_exception_is_limited_to_the_dated_incident(tmp_path: Path):
    folder = tmp_path / "docs/archive"
    folder.mkdir(parents=True)
    incident = folder / "QWEN-ASR-2026-08-09.md"
    content = '# Evidence\n\n[Private](../../2026/08/assets/)\n'
    incident.write_text(content, encoding="utf-8")
    assert check_page(incident, tmp_path) == []
    unrelated = folder / "unrelated.md"
    unrelated.write_text(content, encoding="utf-8")
    assert check_page(unrelated, tmp_path)
