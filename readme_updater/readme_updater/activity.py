"""The "Recent activity" log of the latest public contributions."""

from __future__ import annotations

import logging
import textwrap
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from readme_updater.markup import ASSET_DIR, image, link, pad

if TYPE_CHECKING:
    from readme_updater.github import Contribution, RepoTotals, Status

logger = logging.getLogger(__name__)

PER_GROUP = 2
# Only repositories active within this window count toward diversity: a lone,
# months-old contribution to some repo would surface as a stale entry just to
# fill a slot, so beyond this window we prefer more contributions from the
# repositories we already show.
DIVERSITY_WINDOW = timedelta(days=14)

# One octicon per state. A single neutral tint reads on both themes, so the
# state shows through each glyph's shape and tooltip, not its color.
STATUS_ICONS: dict[Status, str] = {
    "commit": f"{ASSET_DIR}/git-commit.svg",
    "pr_open": f"{ASSET_DIR}/git-pull-request.svg",
    "pr_draft": f"{ASSET_DIR}/git-pull-request-draft.svg",
    "pr_merged": f"{ASSET_DIR}/git-merge.svg",
    "pr_closed": f"{ASSET_DIR}/git-pull-request-closed.svg",
    "issue_open": f"{ASSET_DIR}/issue-opened.svg",
    "issue_closed": f"{ASSET_DIR}/issue-closed.svg",
    "issue_not_planned": f"{ASSET_DIR}/skip.svg",
}
# Hover tooltip and alt text naming each state in words.
STATUS_LABELS: dict[Status, str] = {
    "commit": "commit",
    "pr_open": "open pull request",
    "pr_draft": "draft pull request",
    "pr_merged": "merged pull request",
    "pr_closed": "closed pull request",
    "issue_open": "open issue",
    "issue_closed": "closed issue",
    "issue_not_planned": "issue closed as not planned",
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
    contributions: list[Contribution],
    per_group: int = PER_GROUP,
    now: datetime | None = None,
) -> list[Contribution]:
    """Pick contributions to highlight, aiming for ``per_group`` of each ownership.

    The target is ``2 * per_group`` contributions total — ``per_group`` owned and
    ``per_group`` external. Within each group, diversity (a distinct repository
    per slot) is pursued only within :data:`DIVERSITY_WINDOW`: a new repository
    earns a slot only when its most recent contribution is that recent, except
    for the single most recent repository, which always anchors its group so a
    group with any activity is never empty. Slots a group cannot fill from
    distinct recent repositories go to further, older contributions from the
    repositories it already shows; any that still remain are filled across groups
    from every repository already shown, so the combined target is met whenever
    enough contributions exist, rather than reaching back for a stale repo.

    Args:
      contributions:  Candidate contributions to choose the highlights from.
      per_group:      Contributions to aim for per ownership group.
      now:            Moment the diversity window is measured back from;
                      defaults to the current time.

    Returns:
      The chosen contributions, external repositories first; the same repository
      may appear more than once.
    """
    if now is None:
        now = datetime.now(UTC)
    cutoff = now - DIVERSITY_WINDOW
    external = _select_group(
        [c for c in contributions if not c.owned], per_group, cutoff
    )
    owned = _select_group([c for c in contributions if c.owned], per_group, cutoff)
    highlights = external + owned
    # Make up any per-group shortfall across groups: draw further contributions
    # from every repository already shown, newest first, until the combined
    # N + M target is reached.
    everything = sorted(contributions, key=lambda c: c.date, reverse=True)
    _fill(highlights, everything, per_group * 2)
    return highlights


def _select_group(
    contributions: list[Contribution], per_group: int, cutoff: datetime
) -> list[Contribution]:
    """Choose one ownership group's highlights, newest first.

    Args:
      contributions:  Candidates that share an ownership (all owned or all not).
      per_group:      Most contributions to return.
      cutoff:         Earliest date a new repository may still earn a slot.

    Returns:
      Up to ``per_group`` contributions: the most recent per distinct repo
      within the window (the newest repo always anchors), then older
      contributions from those repos once the recent repos run out.
    """
    group = sorted(contributions, key=lambda c: c.date, reverse=True)
    picked: list[Contribution] = []
    shown: list[str] = []
    # Diversity pass: the most recent contribution of each distinct repo,
    # admitting a new repo only within the window (the first anchors always).
    for contribution in group:
        if len(picked) >= per_group:
            break
        recent_enough = not picked or contribution.date >= cutoff
        if contribution.repo not in shown and recent_enough:
            shown.append(contribution.repo)
            picked.append(contribution)
    _fill(picked, group, per_group)
    return picked


def _fill(
    picked: list[Contribution], candidates: list[Contribution], limit: int
) -> None:
    """Top up ``picked`` in place with more contributions from repos it shows.

    Args:
      picked:      Contributions chosen so far; appended to until ``limit``.
      candidates:  Contributions to draw from, already sorted newest first.
      limit:       Size to grow ``picked`` up to.
    """
    shown = {contribution.repo for contribution in picked}
    for contribution in candidates:
        if len(picked) >= limit:
            break
        already = any(contribution is chosen for chosen in picked)
        if contribution.repo in shown and not already:
            picked.append(contribution)


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
    """Render the highlights as a log: newest first, one entry per contribution.

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
      highlights:  Contributions to render; a repository may repeat.
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
        label = STATUS_LABELS[contribution.status]
        icon = image(STATUS_ICONS[contribution.status], alt=label, title=label)
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
