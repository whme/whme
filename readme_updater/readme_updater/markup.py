"""README markup helpers for injecting content between marker comments."""

from __future__ import annotations

import re
from enum import StrEnum
from string.templatelib import Interpolation, Template

# Where the icons and generated images live, relative to the README at the
# repository root. Image sources in the README and the paths the script
# writes to share this prefix.
ASSET_DIR = "assets"


class Safe(str):
    """Markup that is already safe to embed and must not be escaped."""

    __slots__ = ()


def render_template(template: Template) -> str:
    """Renders a t-string, escaping every interpolation that is not Safe.

    This is what makes link safe by construction: the untrusted text of a
    link is escaped automatically while a URL wrapped in Safe passes
    through untouched, so escaping cannot be forgotten.

    Args:
      template:  Parsed t-string whose literal parts are kept verbatim and
                 whose interpolations are escaped unless wrapped in Safe.

    Returns:
      The rendered string with every non-Safe interpolation escaped.
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
    """Builds a markdown link, escaping the text and taking the URL as-is.

    Args:
      text:  Link label; markdown link-text metacharacters are escaped.
      url:   Destination, embedded verbatim as already-safe markup.

    Returns:
      The markdown link [text](url).
    """
    return render_template(t"[{text}]({Safe(url)})")


def image(src: str, alt: str, title: str = "") -> str:
    """Renders a 16px inline image that GitHub will not turn into a link.

    GitHub auto-links a bare <img> to its own source, so a decorative icon
    would be pointlessly clickable; an <img> inside <picture> is left
    alone, which keeps these icons non-clickable.

    Args:
      src:    Image source path or URL.
      alt:    Alternative text describing the image.
      title:  Hover tooltip explaining the icon; omitted when empty.

    Returns:
      The <picture>-wrapped inline image markup.
    """
    tooltip = f' title="{title}"' if title else ""
    return (
        f'<picture><img src="{src}" width="16" height="16"'
        f' alt="{alt}"{tooltip}></picture>'
    )


def escape(text: str) -> str:
    """Escapes the markdown link-text metacharacters in a string.

    Args:
      text:  Text that may contain the square brackets to escape.

    Returns:
      The text with [ and ] backslash-escaped.
    """
    return text.replace("[", "\\[").replace("]", "\\]")


def pad(count: int) -> str:
    """Builds a monospace spacer of non-breaking spaces.

    Args:
      count:  Number of non-breaking spaces; a non-positive count yields
              an empty string.

    Returns:
      A <samp> run of the requested non-breaking spaces, empty when count
      is not positive.
    """
    return f"<samp>{'&nbsp;' * count}</samp>" if count > 0 else ""


class Marker(StrEnum):
    """The README blocks the updater knows how to fill."""

    ACTIVITY = "activity"
    RECENT_LANGUAGE_BAR = "recent_language_bar"
    ALL_TIME_LANGUAGE_BAR = "all_time_language_bar"


_BLOCK_PATTERNS = {
    marker: re.compile(rf"<!-- {marker}:start -->\n.*?<!-- {marker}:end -->", re.DOTALL)
    for marker in Marker
}


def replace_block(content: str, marker: Marker, replacement: str) -> str:
    """Replaces the block between a marker's start and end comments.

    Args:
      content:      Full README text to update.
      marker:       Section whose block is rewritten.
      replacement:  New body placed between the marker comments, spanning
                    multiple lines when the rendered section is multi-line.

    Returns:
      The README with the marker's block replaced.

    Raises:
      ValueError:  If the marker's start or end comment is missing.
    """
    pattern = _BLOCK_PATTERNS[marker]
    if not pattern.search(content):
        raise ValueError(f"marker {marker.value!r} not found in README")
    block = f"<!-- {marker}:start -->\n{replacement}\n<!-- {marker}:end -->"
    return pattern.sub(lambda _: block, content)
