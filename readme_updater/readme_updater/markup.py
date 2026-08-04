"""README markup helpers and injecting content between marker comments."""

from __future__ import annotations

import re
from string.templatelib import Interpolation, Template

# Where the icons and generated images live, relative to the README at the
# repository root. Image sources in the README and the paths the script
# writes to share this prefix.
ASSET_DIR = "assets"


class Safe(str):
    """Markup that is already safe to embed and must not be escaped."""

    __slots__ = ()


def render_template(template: Template) -> str:
    """Render a t-string, escaping every interpolation that isn't ``Safe``.

    This is what makes :func:`link` safe by construction: the untrusted
    text of a link is escaped automatically, while the URL is marked
    ``Safe`` and passes through untouched. Escaping can't be forgotten.
    """
    parts: list[str] = []
    for item in template:
        if isinstance(item, Interpolation):
            value = item.value
            parts.append(str(value) if isinstance(value, Safe) else escape(str(value)))
        else:
            parts.append(item)
    return "".join(parts)


def link(text: str, url: str) -> str:
    """Build a markdown link, escaping the text and taking the URL as-is."""
    return render_template(t"[{text}]({Safe(url)})")


def image(src: str, alt: str) -> str:
    """Render a 16px inline image that GitHub won't turn into a link.

    GitHub auto-links a bare ``<img>`` to its own source, so every icon
    would be pointlessly clickable; an ``<img>`` inside ``<picture>`` is
    left alone, which keeps these decorative icons non-clickable.
    """
    return f'<picture><img src="{src}" width="16" height="16" alt="{alt}"></picture>'


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
