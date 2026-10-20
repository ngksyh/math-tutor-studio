"""Make AI-written LaTeX display correctly in Streamlit (which renders $...$ and $$...$$ only)."""
from __future__ import annotations

import re

_MATH_SEGMENT = re.compile(r"\$\$.*?\$\$|\$[^$]*\$", re.S)
# A LaTeX command, a \\ row break, or a sub/superscript on a lone variable (x^2, A_t, (a+b)^2).
# "snake_case" words don't count: the letter before _ must not be part of a longer word.
_BARE_COMMAND = re.compile(r"\\[A-Za-z]+|\\\\|(?:(?<![A-Za-z])[A-Za-z]|[0-9)\]}])[_^][{A-Za-z0-9]")


def normalize_math(text: str | None) -> str:
    """Convert \\( \\) and \\[ \\] delimiters to $ and $$ (ignoring the \\\\[2pt] line-break form)."""
    if not isinstance(text, str) or not text:  # None / NaN from a table cell
        return ""
    text = re.sub(r"(?<!\\)\\\[(.*?)(?<!\\)\\\]", lambda m: f"$${m.group(1).strip()}$$", text, flags=re.S)
    text = re.sub(r"(?<!\\)\\\((.*?)(?<!\\)\\\)", lambda m: f"${m.group(1).strip()}$", text, flags=re.S)
    return text


def has_unwrapped_math(text: str | None) -> bool:
    """True if LaTeX commands appear outside $...$ — they'll show as raw code."""
    if not isinstance(text, str) or not text:
        return False
    outside = _MATH_SEGMENT.sub(" ", normalize_math(text))
    return bool(_BARE_COMMAND.search(outside))
