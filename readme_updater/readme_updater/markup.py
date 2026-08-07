"""README markup helpers for injecting content between marker comments."""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from string.templatelib import Interpolation, Template

# Where the icons and generated images live, relative to the README at the
# repository root. The paths the script writes to share this prefix; the
# README instead points at the absolute URLs asset_url builds (see below).
ASSET_DIR = "assets"

# Host that serves a repository's raw files, from which asset <img> srcs are
# built.
RAW_ASSET_HOST = "https://raw.githubusercontent.com"

# The README's asset <img> srcs are absolute URLs, not the repository-relative
# paths the files live at, because the GitHub mobile app does not resolve
# relative image paths and shows every such image broken.
# See https://github.com/orgs/community/discussions/60416.
# Held in a one-slot list so configure_asset_base_url can rebind it without the
# `global` statement ruff's PLW0603 forbids; None until then keeps srcs relative.
_asset_base_url: list[str | None] = [None]


def raw_asset_base(repo: str, ref: str = "HEAD") -> str:
    """Builds the absolute URL of a repository's asset directory at a ref.

    Args:
      repo:  The ``owner/name`` repository hosting the assets.
      ref:   Git ref to pin the URL to; ``HEAD`` tracks the default branch.

    Returns:
      The absolute URL of the repository's asset directory.
    """
    return f"{RAW_ASSET_HOST}/{repo}/{ref}/{ASSET_DIR}"


def configure_asset_base_url(base_url: str) -> None:
    """Point the README's asset <img> srcs at an absolute URL base.

    Args:
      base_url:  Absolute URL of the asset directory, as :func:`raw_asset_base`
                 builds it; a trailing slash is ignored.
    """
    _asset_base_url[0] = base_url.rstrip("/")


def asset_url(path: str) -> str:
    """Resolves an asset path to the src the README should point at.

    Repository-relative asset paths become absolute once
    :func:`configure_asset_base_url` has run so they render in the GitHub
    mobile app; absolute srcs (the avatar URLs) and anything outside the asset
    directory pass through unchanged.

    Args:
      path:  Asset path such as ``assets/python.svg`` or an absolute URL.

    Returns:
      The absolute asset URL when one is configured, else the path unchanged.
    """
    prefix = f"{ASSET_DIR}/"
    base = _asset_base_url[0]
    if base is None or not path.startswith(prefix):
        return path
    return f"{base}/{path[len(prefix) :]}"


def content_version(content: str) -> str:
    """Derives a short cache-busting token from a file's content.

    Assets pinned to a moving ref keep the same URL as their bytes change, so
    GitHub's image proxy would serve a stale copy; appending this token as a
    query parameter makes the URL change exactly when the content does.

    Args:
      content:  File content the token is derived from.

    Returns:
      A short hex digest that changes with the content.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]


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
        f'<picture><img src="{asset_url(src)}" width="16" height="16"'
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


_ABBREVIATION_STEP = 1000
_ABBREVIATION_UNITS = ("k", "M", "B")


def abbreviate(count: int) -> str:
    """Formats a count concisely, e.g. 10000 -> "10k", 1234 -> "1.2k".

    Counts below 1000 are rendered as-is; larger ones are scaled into k/M/B
    units with a single decimal, dropping a trailing ``.0``.

    Args:
      count:  Non-negative count to format.

    Returns:
      The compact string, such as "999", "1.2k", "10k" or "1.5M".
    """
    if count < _ABBREVIATION_STEP:
        return str(count)
    value = float(count)
    for unit in _ABBREVIATION_UNITS:
        value /= _ABBREVIATION_STEP
        if value < _ABBREVIATION_STEP or unit == _ABBREVIATION_UNITS[-1]:
            return f"{value:.1f}".rstrip("0").rstrip(".") + unit
    raise AssertionError("unreachable: the last unit always returns")


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
