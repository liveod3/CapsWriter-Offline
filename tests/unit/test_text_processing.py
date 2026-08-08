from core.client.hotword.hot_rule import RuleCorrector
from core.server.merger import (
    merge_by_text,
    process_tokens_safely,
    remove_trailing_punctuation,
    tokens_to_text,
)
from core.tools.format_tools import adjust_space


def test_text_merger_removes_overlap_without_losing_suffix() -> None:
    assert merge_by_text("今天学习机器学习", "机器学习很有趣") == "今天学习机器学习很有趣"
    assert merge_by_text("第一段。", "第二段") == "第一段。第二段"


def test_token_helpers_handle_bytes_markers_and_punctuation() -> None:
    assert process_tokens_safely([b"\xe4\xbd\xa0", b"\xff", "好"]) == ["你", "", "好"]
    assert tokens_to_text(["Caps@@", "Writer"]) == "CapsWriter"
    assert remove_trailing_punctuation(["你", "好", "。"], [0.0, 0.1, 0.2]) == (
        ["你", "好"],
        [0.0, 0.1],
    )


def test_formatting_preserves_technical_terms_and_chinese_boundaries() -> None:
    assert adjust_space("文件在C盘Windows目录下") == "文件在 C 盘 Windows 目录下"
    assert adjust_space("尝试一下 C O M F Y U I怎么样") == "尝试一下 COMFYUI 怎么样"


def test_rule_corrector_replaces_valid_rules_and_ignores_invalid_regex() -> None:
    corrector = RuleCorrector()
    count = corrector.update_rules(
        "# comment\n"
        "毫安时 = mAh\n"
        r"(艾特)\s*(\w+)\s*(点)\s*(\w+) = @$2.$4"
        "\n[ = invalid"
    )

    assert count == 3
    assert corrector.substitute("5000毫安时") == "5000mAh"
