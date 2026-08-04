"""The "Recent activity" log of the latest public contributions."""

from __future__ import annotations

import logging
import textwrap
from datetime import UTC, timedelta
from typing import TYPE_CHECKING

from readme_updater.markup import ASSET_DIR, image, link, pad

if TYPE_CHECKING:
    from datetime import datetime

    from readme_updater.github import Contribution, Kind, RepoTotals

logger = logging.getLogger(__name__)

REPOS_PER_GROUP = 2

ICONS: dict[Kind, str] = {
    "pr": f"{ASSET_DIR}/git-pull-request.svg",
    "issue": f"{ASSET_DIR}/issue-opened.svg",
    "commit": f"{ASSET_DIR}/git-commit.svg",
}
# Hover tooltips (and alt text) that spell out what each icon means.
KIND_LABELS: dict[Kind, str] = {
    "pr": "pull request",
    "issue": "issue",
    "commit": "commit",
}
TOTAL_ICON = f"{ASSET_DIR}/mark-github.svg"
TOTAL_LABEL = "total GitHub contributions"

STAMP_FORMAT = "%Y-%m-%d %H:%M UTC"
STAMP_WIDTH = len("2026-08-01 09:01 UTC")
# Longer contribution titles wrap into the next line and break the log's
# column layout, so they get truncated with an ellipsis.
TITLE_LIMIT = 72
WEEKS_PER_MONTH = 5  # beyond this many calendar weeks, count in months
MONTHS_PER_YEAR = 12


def select_highlights(
    contributions: list[Contribution], per_group: int = REPOS_PER_GROUP
) -> list[Contribution]:
    """Pick the most recent contribution to each of the last distinct repositories.

    Keeps up to ``per_group`` repositories that are not owned and the same
    number that are, listing the ones that are not owned first.

    Args:
      contributions:  Candidate contributions to choose the highlights from.
      per_group:      Most repositories to keep per ownership group.

    Returns:
      One contribution per highlighted repository, external repositories
      first and newest within each group.
    """
    seen: set[str] = set()
    groups: dict[bool, list[Contribution]] = {False: [], True: []}
    for contribution in sorted(
        contributions, key=lambda contribution: contribution.date, reverse=True
    ):
        if contribution.repo in seen:
            continue
        group = groups[contribution.owned]
        if len(group) < per_group:
            seen.add(contribution.repo)
            group.append(contribution)
    return groups[False] + groups[True]


def _shorten(title: str) -> str:
    if len(title) <= TITLE_LIMIT:
        return title
    on_word_boundary = textwrap.shorten(title, width=TITLE_LIMIT, placeholder="…")
    if len(on_word_boundary) >= TITLE_LIMIT // 2:
        return on_word_boundary
    # A long unbroken token (function_names_like_this) would leave little or
    # nothing to a word-boundary cut, so cut mid-token instead.
    return title[: TITLE_LIMIT - 1].rstrip() + "…"


def relative_label(timestamp: datetime, now: datetime) -> str:
    """Name the age of a contribution the way the GitHub UI would.

    Args:
      timestamp:  When the contribution happened.
      now:        Moment to measure the age against.

    Returns:
      A human phrase such as ``today`` or ``2 weeks ago``.
    """
    day, today = timestamp.astimezone(UTC).date(), now.astimezone(UTC).date()
    monday = today - timedelta(days=today.weekday())
    weeks = (monday - (day - timedelta(days=day.weekday()))).days // 7
    months = (today.year - day.year) * MONTHS_PER_YEAR + today.month - day.month
    years = months // MONTHS_PER_YEAR
    ladder = [
        (day >= today, "today"),
        ((today - day).days == 1, "yesterday"),
        (weeks == 0, "this week"),
        (weeks == 1, "last week"),
        (weeks < WEEKS_PER_MONTH, f"{weeks} weeks ago"),
        (months == 1, "last month"),
        (months < MONTHS_PER_YEAR, f"{months} months ago"),
        (years == 1, "last year"),
    ]
    return next((label for applies, label in ladder if applies), f"{years} years ago")


def _totals_cell(repo: str, totals: RepoTotals, username: str) -> str:
    """List the total contributions to the repository, each count linked."""
    counted = {
        "commit": (
            totals.commits,
            f"https://github.com/{repo}/commits?author={username}",
        ),
        "pull request": (
            totals.pull_requests,
            f"https://github.com/{repo}/pulls?q=is%3Apr+author%3A{username}",
        ),
        "issue": (
            totals.issues,
            f"https://github.com/{repo}/issues?q=is%3Aissue+author%3A{username}",
        ),
    }
    parts = [
        link(f"{value} {noun}{'s' if value != 1 else ''}", url)
        for noun, (value, url) in counted.items()
        if value
    ]
    return " · ".join(parts)


def render(
    highlights: list[Contribution],
    totals: dict[str, RepoTotals] | None = None,
    now: datetime | None = None,
    username: str = "",
) -> str:
    """Render the highlights as a log: newest first, one entry per repository.

    Each entry is two lines sharing the same column layout, so they align
    by construction: a timestamp slot, the repository (avatar and name),
    an icon and the content. The first line carries the timestamp and the
    contribution; the second the age of the contribution in parentheses
    and the total contributions to that repository behind the octocat.
    Both slots are ``<code>`` elements padded to the same character count,
    which is what makes the columns line up: the pill's own horizontal
    padding cannot be replicated with whitespace. Entries are separated
    as paragraphs so they don't crowd each other.

    Args:
      highlights:  Contributions to render, one per repository.
      totals:      Per-repository totals to append below each entry, keyed
                   by ``owner/name``; entries without totals get one line.
      now:         Moment to measure each contribution's age against; the
                   age is omitted when it is not given.
      username:    GitHub login the totals links are scoped to.

    Returns:
      The rendered log, or the empty string when there are no highlights.
    """
    totals = totals or {}
    ordered = sorted(
        highlights, key=lambda contribution: contribution.date, reverse=True
    )
    if not ordered:
        return ""
    width = max(len(contribution.repo) for contribution in ordered)
    entries = []
    for contribution in ordered:
        owner = contribution.repo.partition("/")[0]
        avatar = image(f"https://github.com/{owner}.png?size=32", alt="")
        label = KIND_LABELS[contribution.kind]
        icon = image(ICONS[contribution.kind], alt=label, title=label)
        stamp = contribution.date.astimezone(UTC).strftime(STAMP_FORMAT)
        repo_url = f"https://github.com/{contribution.repo}"
        repo_cell = (
            f'{avatar} <a href="{repo_url}"><code>{contribution.repo}</code></a>'
            f"{pad(width - len(contribution.repo))}"
        )
        lines = [
            (
                f"<code>{stamp}</code>&emsp;{repo_cell} "
                f"{icon} {link(_shorten(contribution.title), contribution.url)}"
            )
        ]
        if contribution.repo in totals:
            if now:
                age = f"({relative_label(contribution.date, now)})"
                slot = f"<code>{age}</code>{pad(STAMP_WIDTH - len(age))}"
            else:
                slot = pad(STAMP_WIDTH)
            octocat = image(TOTAL_ICON, alt=TOTAL_LABEL, title=TOTAL_LABEL)
            counts = _totals_cell(
                contribution.repo, totals[contribution.repo], username
            )
            lines.append(f"{slot}&emsp;{repo_cell} {octocat} <sub>{counts}</sub>")
        entries.append("\\\n".join(lines))
    return "\n\n".join(entries)
