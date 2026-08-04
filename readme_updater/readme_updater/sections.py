"""The generated README sections and the template that places them.

The README is the template: it declares ``<!-- name:start -->`` /
``<!-- name:end -->`` markers wherever it wants a generated block. This
module fills those markers and never decides their order — swapping two
sections is an edit to the README, not to the code.

:class:`Sections` is the typed set of blocks the code knows how to
produce; :func:`consistency_errors` keeps it and the README in step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import ClassVar

from readme_updater.markup import replace_block

_START_MARKER = re.compile(r"<!-- ([\w-]+):start -->")


@dataclass(frozen=True)
class Sections:
    """One field per generated README block, holding its rendered content.

    Field names are the marker ids, so adding a block is adding a field
    here and a matching marker in the README. A missing field is a type
    error at construction; a mismatch with the README is caught by
    :func:`consistency_errors`.
    """

    activity: str
    recent_language_bar: str
    all_time_language_bar: str

    # Blocks still under construction, exempt from the consistency check so
    # a field or its marker may exist on only one side for a while.
    WIP: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def names(cls) -> set[str]:
        """Return the section names the code knows how to fill."""
        return {field.name for field in fields(cls)}

    def apply(self, readme: str) -> str:
        """Insert each section into its marker, leaving the order to the README.

        Markers absent from the README are skipped rather than an error,
        so a WIP field without a marker yet does not break a run.
        """
        present = markers(readme)
        for field in fields(self):
            if field.name in present:
                readme = replace_block(readme, field.name, getattr(self, field.name))
        return readme


def markers(readme: str) -> set[str]:
    """Return the section markers the README declares."""
    return set(_START_MARKER.findall(readme))


def consistency_errors(readme: str) -> list[str]:
    """Report where the README's markers and the code's sections disagree.

    A README marker with no matching field, or a non-WIP field never
    placed in the README, is an error; WIP sections are exempt from both.
    """
    present = markers(readme)
    known = Sections.names()
    unknown = [
        f"README declares unknown section {name!r}" for name in sorted(present - known)
    ]
    missing = [
        f"section {name!r} is never placed in the README"
        for name in sorted((known - Sections.WIP) - present)
    ]
    return unknown + missing
