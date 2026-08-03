"""The "Currently working on" log of my recent contributions."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from readme_updater import github
from readme_updater.markup import escape, image, pad

REPOS_PER_GROUP = 2

Kind = Literal["pr", "issue", "commit"]

ICONS: dict[Kind, str] = {
    "pr": "assets/git-pull-request.svg",
    "issue": "assets/issue-opened.svg",
    "commit": "assets/git-commit.svg",
}
TOTAL_ICON = "assets/mark-github.svg"

STAMP_FORMAT = "%Y-%m-%d %H:%M UTC"
STAMP_WIDTH = len("2026-08-01 09:01 UTC")
# Longer contribution titles wrap into the next line and break the log's
# column layout, so they get truncated with an ellipsis.
TITLE_LIMIT = 72
WEEKS_PER_MONTH = 5  # beyond this many calendar weeks, count in months
MONTHS_PER_YEAR = 12


@dataclass(frozen=True)
class RepoTotals:
    """How much I contributed to one repository, in total."""

    commits: int
    pull_requests: int
    issues: int


@dataclass(frozen=True)
class Contribution:
    """A single public contribution: a pull request, an issue or a commit."""

    repo: str
    title: str
    url: str
    date: str
    kind: Kind

    @property
    def owned(self) -> bool:
        """Whether the contribution went to a repository I own."""
        return self.repo.partition("/")[0].lower() in github.MY_ACCOUNTS

    @property
    def timestamp(self) -> datetime:
        """The contribution date, parsed for sorting across timezones."""
        return datetime.fromisoformat(self.date)


def fetch_totals(repo: str) -> RepoTotals:
    """Count all my commits, pull requests and issues in one repository."""
    return RepoTotals(
        commits=github.count("commits", f"repo:{repo}"),
        pull_requests=github.count("issues", f"type:pr repo:{repo}"),
        issues=github.count("issues", f"type:issue repo:{repo}"),
    )


def issue_contribution(item: dict[str, Any]) -> Contribution:
    """Map a pull request or issue from the issue search API to a contribution."""
    return Contribution(
        repo=item["repository_url"].removeprefix(f"{github.API}/repos/"),
        title=item["title"],
        url=item["html_url"],
        date=item["created_at"],
        kind="pr" if "pull_request" in item else "issue",
    )


def commit_contribution(item: dict[str, Any]) -> Contribution:
    """Map a commit from the commit search API to a contribution.

    Uses the committer date, not the author date: it is what the GitHub UI
    shows, and commits landing through a review pipeline are committed well
    after they are authored.
    """
    return Contribution(
        repo=item["repository"]["full_name"],
        title=item["commit"]["message"].splitlines()[0],
        url=item["html_url"],
        date=item["commit"]["committer"]["date"],
        kind="commit",
    )


def fetch_contributions() -> list[Contribution]:
    """Collect my recent public contributions from the GitHub search API."""
    # My own repos flood the plain author query, so foreign repos
    # additionally get a query of their own.
    foreign = " ".join(f"-user:{account}" for account in sorted(github.MY_ACCOUNTS))
    issues = github.search("issues", sort="created") + github.search(
        "issues", sort="created", qualifiers=foreign
    )
    commits = github.public_commits(github.search("commits", sort="committer-date"))
    return [issue_contribution(item) for item in issues] + [
        commit_contribution(item) for item in commits
    ]


def select_highlights(
    contributions: list[Contribution], per_group: int = REPOS_PER_GROUP
) -> list[Contribution]:
    """Pick the most recent contribution to each of the last distinct repositories.

    Picks up to ``per_group`` repositories I don't own and the same number
    of repositories I do, listing the ones I don't own first.
    """
    seen: set[str] = set()
    groups: dict[bool, list[Contribution]] = {False: [], True: []}
    for contribution in sorted(
        contributions, key=lambda contribution: contribution.timestamp, reverse=True
    ):
        if contribution.repo in seen or contribution.repo == github.PROFILE_REPO:
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
    """Name the age of a contribution the way the GitHub UI would."""
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


def _totals_cell(repo: str, totals: RepoTotals) -> str:
    """List my total contributions to the repository, each count linked."""
    user = github.USER
    counted = {
        "commit": (totals.commits, f"https://github.com/{repo}/commits?author={user}"),
        "pull request": (
            totals.pull_requests,
            f"https://github.com/{repo}/pulls?q=is%3Apr+author%3A{user}",
        ),
        "issue": (
            totals.issues,
            f"https://github.com/{repo}/issues?q=is%3Aissue+author%3A{user}",
        ),
    }
    parts = [
        f"[{value} {noun}{'s' if value != 1 else ''}]({url})"
        for noun, (value, url) in counted.items()
        if value
    ]
    return " · ".join(parts)


def render(
    highlights: list[Contribution],
    totals: dict[str, RepoTotals] | None = None,
    now: datetime | None = None,
) -> str:
    """Render the highlights as a log: newest first, one entry per repository.

    Each entry is two lines sharing the same column layout, so they align
    by construction: a timestamp slot, the repository (avatar and name),
    an icon and the content. The first line carries the timestamp and the
    contribution; the second the age of the contribution in parentheses
    and my total contributions to that repository behind the octocat.
    Both slots are ``<code>`` elements padded to the same character count,
    which is what makes the columns line up: the pill's own horizontal
    padding cannot be replicated with whitespace. Entries are separated
    as paragraphs so they don't crowd each other.
    """
    totals = totals or {}
    ordered = sorted(
        highlights, key=lambda contribution: contribution.timestamp, reverse=True
    )
    if not ordered:
        return ""
    width = max(len(contribution.repo) for contribution in ordered)
    entries = []
    for contribution in ordered:
        owner = contribution.repo.partition("/")[0]
        avatar = image(f"https://github.com/{owner}.png?size=32", alt="")
        icon = image(ICONS[contribution.kind], alt=contribution.kind)
        stamp = contribution.timestamp.astimezone(UTC).strftime(STAMP_FORMAT)
        repo_url = f"https://github.com/{contribution.repo}"
        repo_cell = (
            f'{avatar} <a href="{repo_url}"><code>{contribution.repo}</code></a>'
            f"{pad(width - len(contribution.repo))}"
        )
        lines = [
            (
                f"<code>{stamp}</code>&emsp;{repo_cell} "
                f"{icon} [{escape(_shorten(contribution.title))}]({contribution.url})"
            )
        ]
        if contribution.repo in totals:
            if now:
                label = f"({relative_label(contribution.timestamp, now)})"
                slot = f"<code>{label}</code>{pad(STAMP_WIDTH - len(label))}"
            else:
                slot = pad(STAMP_WIDTH)
            octocat = image(TOTAL_ICON, alt="total")
            counts = _totals_cell(contribution.repo, totals[contribution.repo])
            lines.append(f"{slot}&emsp;{repo_cell} {octocat} <sub>{counts}</sub>")
        entries.append("\\\n".join(lines))
    return "\n\n".join(entries)
