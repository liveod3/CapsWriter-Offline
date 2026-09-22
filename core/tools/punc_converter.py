"""
Punctuation conversion.

Select full-width to half-width conversion according to the foreground application.
"""

# Map full-width punctuation to half-width forms.
FULL_TO_HALF = {
    '，': ', ',
    '。': '. ',
    '？': '? ',
    '！': '! ',
    '：': ': ',
    '；': '; ',
    '（': '(',
    '）': ')',
    '【': '[',
    '】': ']',
    '「': '"',
    '」': '"',
    '『': '\'',
    '』': '\'',
    '"': '"',
    '"': '"',
    "'" : "'",
    "'" : "'",
}


def convert_full_to_half(text: str) -> str:
    """
    Convert full-width punctuation to half-width forms.

    Args:
        text: Text to convert.

    Returns:
        Converted text.
    """
    result = text
    for full, half in FULL_TO_HALF.items():
        result = result.replace(full, half)
    return result


def should_convert_punctuation(window_title: str, keywords: list) -> bool:
    """
    Return whether punctuation conversion is needed.

    Args:
        window_title: Window title.
        keywords: Case-insensitive application keywords, including localized names.

    Returns:
        True when conversion is needed.
    """
    if not window_title:
        return False

    title_lower = window_title.lower()
    return any(keyword.lower() in title_lower for keyword in keywords)
