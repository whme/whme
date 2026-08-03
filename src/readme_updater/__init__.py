"""Keep the dynamic section of my profile README fresh.

Queries the GitHub search API for my most recent public contributions
(pull requests, issues and commits), picks the most recent one for each
of the repositories I contributed to last, both my own and other
people's, and rewrites the marker-delimited block in the README.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

USER = "whme"
MY_ACCOUNTS = frozenset({"whme", "whmade"})
PROFILE_REPO = "whme/whme"
REPOS_PER_GROUP = 2
API = "https://api.github.com"

Kind = Literal["pr", "issue", "commit"]

ICONS: dict[Kind, str] = {
    "pr": "assets/git-pull-request.svg",
    "issue": "assets/issue-opened.svg",
    "commit": "assets/git-commit.svg",
}


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
        return self.repo.partition("/")[0].lower() in MY_ACCOUNTS

    @property
    def timestamp(self) -> datetime:
        """The contribution date, parsed for sorting across timezones."""
        return datetime.fromisoformat(self.date)


def _fetch(url: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USER}-readme-updater",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 (always https, see API)
    with urllib.request.urlopen(request) as response:  # noqa: S310
        return json.load(response)


def public_query(qualifiers: str = "") -> str:
    """Build a search query for my contributions in public repositories only.

    The README must never leak private activity, no matter how much the
    token running the script is allowed to see.
    """
    return " ".join(filter(None, [f"author:{USER}", "is:public", qualifiers]))


def public_commits(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop commits from private repositories, whatever the search returned."""
    return [item for item in items if not item["repository"].get("private")]


def _search(endpoint: str, sort: str, qualifiers: str = "") -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "q": public_query(qualifiers),
            "sort": sort,
            "order": "desc",
            "per_page": 50,
        }
    )
    return list(_fetch(f"{API}/search/{endpoint}?{params}")["items"])


def _count(endpoint: str, qualifiers: str) -> int:
    params = urllib.parse.urlencode({"q": public_query(qualifiers), "per_page": 1})
    return int(_fetch(f"{API}/search/{endpoint}?{params}")["total_count"])


def fetch_totals(repo: str) -> RepoTotals:
    """Count all my commits, pull requests and issues in one repository."""
    return RepoTotals(
        commits=_count("commits", f"repo:{repo}"),
        pull_requests=_count("issues", f"type:pr repo:{repo}"),
        issues=_count("issues", f"type:issue repo:{repo}"),
    )


def issue_contribution(item: dict[str, Any]) -> Contribution:
    """Map a pull request or issue from the issue search API to a contribution."""
    return Contribution(
        repo=item["repository_url"].removeprefix(f"{API}/repos/"),
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
    foreign = " ".join(f"-user:{account}" for account in sorted(MY_ACCOUNTS))
    issues = _search("issues", sort="created") + _search(
        "issues", sort="created", qualifiers=foreign
    )
    commits = public_commits(_search("commits", sort="committer-date"))
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
        if contribution.repo in seen or contribution.repo == PROFILE_REPO:
            continue
        group = groups[contribution.owned]
        if len(group) < per_group:
            seen.add(contribution.repo)
            group.append(contribution)
    return groups[False] + groups[True]


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _image(src: str, alt: str) -> str:
    return f'<img src="{src}" width="16" height="16" alt="{alt}">'


STAMP_FORMAT = "%Y-%m-%d %H:%M UTC"
STAMP_WIDTH = len("2026-08-01 09:01 UTC")
TOTAL_ICON = "assets/mark-github.svg"


WEEKS_PER_MONTH = 5  # beyond this many calendar weeks, count in months
MONTHS_PER_YEAR = 12


def relative_label(timestamp: datetime, now: datetime) -> str:
    """Name the age of a contribution the way the GitHub UI would."""
    date, today = timestamp.astimezone(UTC).date(), now.astimezone(UTC).date()
    monday = today - timedelta(days=today.weekday())
    weeks = (monday - (date - timedelta(days=date.weekday()))).days // 7
    months = (today.year - date.year) * MONTHS_PER_YEAR + today.month - date.month
    years = months // MONTHS_PER_YEAR
    ladder = [
        (date >= today, "today"),
        ((today - date).days == 1, "yesterday"),
        (weeks == 0, "this week"),
        (weeks == 1, "last week"),
        (weeks < WEEKS_PER_MONTH, f"{weeks} weeks ago"),
        (months == 1, "last month"),
        (months < MONTHS_PER_YEAR, f"{months} months ago"),
        (years == 1, "last year"),
    ]
    return next((label for applies, label in ladder if applies), f"{years} years ago")


def _pad(count: int) -> str:
    return f"<samp>{'&nbsp;' * count}</samp>" if count > 0 else ""


def _totals_cell(repo: str, totals: RepoTotals) -> str:
    """List my total contributions to the repository, each count linked."""
    counted = {
        "commit": (totals.commits, f"https://github.com/{repo}/commits?author={USER}"),
        "pull request": (
            totals.pull_requests,
            f"https://github.com/{repo}/pulls?q=is%3Apr+author%3A{USER}",
        ),
        "issue": (
            totals.issues,
            f"https://github.com/{repo}/issues?q=is%3Aissue+author%3A{USER}",
        ),
    }
    parts = [
        f"[{count} {noun}{'s' if count != 1 else ''}]({url})"
        for noun, (count, url) in counted.items()
        if count
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
        avatar = _image(f"https://github.com/{owner}.png?size=32", alt="")
        icon = _image(ICONS[contribution.kind], alt=contribution.kind)
        stamp = contribution.timestamp.astimezone(UTC).strftime(STAMP_FORMAT)
        repo_url = f"https://github.com/{contribution.repo}"
        repo_cell = (
            f'{avatar} <a href="{repo_url}"><code>{contribution.repo}</code></a>'
            f"{_pad(width - len(contribution.repo))}"
        )
        lines = [
            (
                f"<code>{stamp}</code>&emsp;{repo_cell} "
                f"{icon} [{_escape(contribution.title)}]({contribution.url})"
            )
        ]
        if contribution.repo in totals:
            if now:
                label = f"({relative_label(contribution.timestamp, now)})"
                slot = f"<code>{label}</code>{_pad(STAMP_WIDTH - len(label))}"
            else:
                slot = _pad(STAMP_WIDTH)
            octocat = _image(TOTAL_ICON, alt="total")
            counts = _totals_cell(contribution.repo, totals[contribution.repo])
            lines.append(f"{slot}&emsp;{repo_cell} {octocat} <sub>{counts}</sub>")
        entries.append("\\\n".join(lines))
    return "\n\n".join(entries)


def replace_block(content: str, marker: str, replacement: str) -> str:
    """Replace the block between the start and end comments of ``marker``."""
    pattern = re.compile(
        rf"<!-- {marker}:start -->\n.*?<!-- {marker}:end -->", re.DOTALL
    )
    if not pattern.search(content):
        raise ValueError(f"marker {marker!r} not found in README")
    block = f"<!-- {marker}:start -->\n{replacement}\n<!-- {marker}:end -->"
    return pattern.sub(lambda _: block, content)


def main(argv: list[str] | None = None) -> None:
    """Rewrite the activity section of the README with fresh data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("readme", nargs="?", type=Path, default=Path("README.md"))
    args = parser.parse_args(argv)
    highlights = select_highlights(fetch_contributions())
    totals = {
        contribution.repo: fetch_totals(contribution.repo)
        for contribution in highlights
    }
    content = replace_block(
        args.readme.read_text(),
        "activity",
        render(highlights, totals, now=datetime.now(UTC)),
    )
    args.readme.write_text(content)
