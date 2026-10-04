"""Small safe Markdown helpers used by the chat transcript renderer."""

from __future__ import annotations

import re


def fenced_code_block(content: str, language: str = "") -> str:
    """Wrap literal output in a fence longer than any backtick run in the body."""
    longest_run = max((len(match.group(0)) for match in re.finditer(r"`+", content)), default=0)
    fence = "`" * max(3, longest_run + 1)
    info = re.sub(r"[^A-Za-z0-9_+-]", "", language)
    return f"{fence}{info}\n{content}\n{fence}\n\n"
