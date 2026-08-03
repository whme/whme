"""README markup helpers and injecting content between marker comments."""

from __future__ import annotations

import re


def image(src: str, alt: str) -> str:
    """Render a 16px inline image."""
    return f'<img src="{src}" width="16" height="16" alt="{alt}">'


def escape(text: str) -> str:
    """Escape the markdown link-text metacharacters in ``text``."""
    return text.replace("[", "\\[").replace("]", "\\]")


def pad(count: int) -> str:
    """Build a monospace spacer of ``count`` non-breaking spaces (empty at zero)."""
    return f"<samp>{'&nbsp;' * count}</samp>" if count > 0 else ""


def replace_block(content: str, marker: str, replacement: str) -> str:
    """Replace the block between the start and end comments of ``marker``."""
    pattern = re.compile(
        rf"<!-- {marker}:start -->\n.*?<!-- {marker}:end -->", re.DOTALL
    )
    if not pattern.search(content):
        raise ValueError(f"marker {marker!r} not found in README")
    block = f"<!-- {marker}:start -->\n{replacement}\n<!-- {marker}:end -->"
    return pattern.sub(lambda _: block, content)
