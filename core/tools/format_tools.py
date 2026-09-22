# coding: utf-8
"""
Text formatting helpers.

Adjust spacing in mixed Chinese and English text.
Normalize language boundaries in recognition output.
"""

from __future__ import annotations
import re
from typing import Match

# Match Latin letters, digits, and symbols bounded by Chinese characters.
_EN_IN_ZH_PATTERN = re.compile(r"""(?ix)
    ([\u4e00-\u9fa5])?                  # Left CJK boundary
    (                                   # Latin letters, digits, and technical symbols
        [a-z0-9+#]                      # Start: letter, digit, +, or #
        [a-z0-9\s'.,!+#/_:@%&?-]*       # Body: allow spaces and symbols; keep - last
        [a-z0-9+#%]                     # End: letter, digit, +, #, or %
        |
        [a-z0-9]                        # Or one letter or digit
    )
    ([\u4e00-\u9fa5])?                  # Right CJK boundary
""")


def _merge_parts(words: list[str]) -> str:
    """Merge fragmented ASR letter and digit sequences.

    Handle two common forms:
    - Single letters/digits: "C O M F Y" -> "COMFY".
    - A single letter followed by a letter-led word: "F P16" -> "FP16".
    """
    if len(words) <= 1:
        return words[0] if words else ''
    if all(len(w) == 1 or w.isdigit() for w in words):
        return ''.join(words)
    if len(words) == 2 and len(words[0]) == 1 and words[0].isalpha() and words[1][0].isalpha():
        return words[0] + words[1]
    return ' '.join(words)


def _replacer(match: Match) -> str:
    left, raw, right = (match.group(i) or '' for i in (1, 2, 3))
    center = _merge_parts(raw.strip().split())
    has_alpha = bool(re.search(r'[a-zA-Z]', center))

    if left and has_alpha:
        left += ' '
    if right and has_alpha:
        right = ' ' + right

    return f'{left}{center}{right}'



def adjust_space(text: str) -> str:
    """
    Adjust spacing between Chinese, Latin letters, and digits.

    1. Insert spaces at Chinese/Latin or Chinese/digit boundaries.
    2. Join consecutive spelled letters, such as "A B C" -> "ABC".
    3. Preserve technical symbols such as C++, TCP/IP, and 100%.

    Non-overlapping regular expression matches cannot reuse one boundary character.
    Repeat replacement when a Chinese character borders Latin text on both sides:
      the first pass spaces one side,
      and the second pass can space the other side.

    Args:
        text: Input text.

    Returns:
        Formatted text.
    """
    for _ in range(3):
        new_text = _EN_IN_ZH_PATTERN.sub(_replacer, text)
        if new_text == text:
            break
        text = new_text
    return text


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.i18n import initialize_tool_language, tr
    initialize_tool_language()
    # Example cases.
    test_cases = [
        # Basic Chinese/English spacing.
        "这是hello世界",
        "hello世界",
        "这是一个iPhone手机",
        "这是一个iPhone15手机",
        "Mixed中文English测试",
        # Shared Chinese boundary between two Latin spans.
        "文件在C盘Windows目录下",
        # Merge spelled letters.
        "尝试一下 C O M F Y U I怎么样",
        "你可以试一下 F P 32 和 F P 16 如何",
        "试一下F P16的效果",
        # Chinese text embedded in an English sentence.
        "他说I love you这句话很浪漫",
        "请执行git commit操作",
        # Preserve Latin-only text and symbols.
        "I have a phone",
        "C++是非常强的语言",
        "TCP/IP协议",
        "100%的安全",
        "C# 也是一门语言",
        "数字123也会测试",
    ]

    print(f"{tr('format.original'):<25} | {tr('format.adjusted')}")
    print("-" * 60)
    for text in test_cases:
        print(f"{text:<25} | {adjust_space(text)}")
