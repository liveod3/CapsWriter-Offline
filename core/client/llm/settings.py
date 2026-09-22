"""仅修改本机配置中的文本动作选项，不执行配置、不重写其他用户取值。"""

from __future__ import annotations

from core.i18n import Notice

import ast
import os
from pathlib import Path
import tempfile


def llm_options(config) -> dict[str, bool]:
    """返回总开关约束后的实际状态；旧配置默认允许两种能力。"""
    enabled = bool(getattr(config, "llm_enabled", False))
    return {
        "correct_asr": enabled and bool(getattr(config, "llm_correction_enabled", True)),
        "translate": enabled and bool(getattr(config, "llm_translation_enabled", True)),
    }


def save_llm_options(path: Path, *, correction: bool, translation: bool) -> None:
    """保存独立开关及总状态，保留默认路由和其他用户配置。"""
    if not isinstance(correction, bool) or not isinstance(translation, bool):
        raise ValueError(Notice('validation.settings.llm_options_must_be_boolean'))
    _save_options(path, {
        "llm_enabled": repr(correction or translation),
        "llm_correction_enabled": repr(correction),
        "llm_translation_enabled": repr(translation),
    })


def save_ui_language(path: Path, language: str) -> None:
    """Save the UI preference without executing or replacing other settings."""
    from core.i18n import LANGUAGES

    if language not in LANGUAGES:
        raise ValueError(Notice('validation.settings.unsupported_ui_language'))
    _save_options(path, {"ui_language": repr(language)})


def _save_options(path: Path, values: dict[str, str]) -> None:
    original = path.read_bytes()
    source = original.decode("utf-8-sig")
    tree = ast.parse(source)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ClientConfig"]
    if len(classes) != 1:
        raise ValueError(Notice('validation.settings.expected_one_clientconfig_class'))
    config = classes[0]
    # AST 列偏移以 UTF-8 字节计；保留 BOM、换行、注释和所有无关源码。
    data = source.encode("utf-8")
    lines = data.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = []
    found = set()
    for node in config.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for target in targets:
            if not isinstance(target, ast.Name) or target.id not in values:
                continue
            if target.id in found or len(targets) != 1 or node.value is None:
                raise ValueError(Notice('validation.settings.ambiguous_text_mode_assignment'))
            found.add(target.id)
            value = node.value
            edits.append((
                offsets[value.lineno - 1] + value.col_offset,
                offsets[value.end_lineno - 1] + value.end_col_offset,
                values[target.id].encode("utf-8"),
            ))
    missing = values.keys() - found
    if missing:
        newline = b"\r\n" if b"\r\n" in data else b"\n"
        indent = lines[config.body[0].lineno - 1][:config.body[0].col_offset]
        addition = newline + newline.join(
            indent + f"{name} = {values[name]}".encode("utf-8") for name in sorted(missing)
        ) + newline
        # 在类最后一条语句后插入，兼容旧配置缺少字段。
        position = offsets[config.end_lineno]
        edits.append((position, position, addition))
    for start, end, replacement in sorted(edits, reverse=True):
        data = data[:start] + replacement + data[end:]
    ast.parse(data)
    if original.startswith(b"\xef\xbb\xbf"):
        data = b"\xef\xbb\xbf" + data
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".text-mode-", delete=False) as out:
            temporary = Path(out.name)
            out.write(data)
        if path.read_bytes() != original:
            raise OSError(Notice('validation.settings.client_settings_changed_during_save'))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
