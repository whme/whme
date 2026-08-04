"""The template that places the generated README sections.

The README is the template: it declares ``<!-- name:start -->`` /
``<!-- name:end -->`` markers wherever it wants a generated block, one
per :class:`~readme_updater.markup.Marker`. This module fills those
markers and never decides their order — swapping two sections is an edit
to the README, not to the code.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from readme_updater.markup import Marker, replace_block

if TYPE_CHECKING:
    from collections.abc import Mapping

_MARKER = re.compile(r"<!-- ([\w-]+):(start|end) -->")


def _declared_markers(readme: str) -> set[str]:
    """Parses the balanced marker ids a README declares.

    Args:
      readme:  README text scanned for start and end marker comments.

    Returns:
      The set of ids that have both a start and an end marker.

    Raises:
      ValueError:  If a start marker has no matching end, or an end no
                   matching start.
    """
    starts: set[str] = set()
    ends: set[str] = set()
    for name, kind in _MARKER.findall(readme):
        (starts if kind == "start" else ends).add(name)
    if unbalanced := starts ^ ends:
        raise ValueError(f"unbalanced markers: {sorted(unbalanced)}")
    return starts


def declared_markers(readme: str) -> set[Marker]:
    """Returns the markers a README declares, narrowed to the enum.

    A test enforces that the profile README declares exactly the known
    markers, so the parsed ids are the enum's values and narrowing them
    with a cast is sound.

    Args:
      readme:  README text scanned for start and end marker comments.

    Returns:
      The declared ids as Marker members.

    Raises:
      ValueError:  If a start marker has no matching end, or an end no
                   matching start.
    """
    return cast("set[Marker]", _declared_markers(readme))


def apply(sections: Mapping[Marker, str], readme: str) -> str:
    """Replaces each section's marker block in the README with its content.

    Args:
      sections:  Rendered content keyed by the marker whose block it fills.
      readme:    README text whose marker blocks are rewritten.

    Returns:
      The README with every section's block replaced by its content.
    """
    for marker, content in sections.items():
        readme = replace_block(readme, marker, content)
    return readme
