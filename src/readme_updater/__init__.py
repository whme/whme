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
from datetime import datetime
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


def _search(endpoint: str, sort: str, qualifiers: str = "") -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "q": f"author:{USER} {qualifiers}".strip(),
            "sort": sort,
            "order": "desc",
            "per_page": 50,
        }
    )
    return list(_fetch(f"{API}/search/{endpoint}?{params}")["items"])


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
    """Map a commit from the commit search API to a contribution."""
    return Contribution(
        repo=item["repository"]["full_name"],
        title=item["commit"]["message"].splitlines()[0],
        url=item["html_url"],
        date=item["commit"]["author"]["date"],
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
    commits = _search("commits", sort="author-date")
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


def render(highlights: list[Contribution]) -> str:
    """Render the highlights as a markdown list, one repository per entry."""
    lines = []
    for contribution in highlights:
        owner = contribution.repo.partition("/")[0]
        avatar = _image(f"https://github.com/{owner}.png?size=32", alt="")
        icon = _image(ICONS[contribution.kind], alt=contribution.kind)
        repo_url = f"https://github.com/{contribution.repo}"
        lines.append(f"- {avatar} [**{contribution.repo}**]({repo_url})")
        lines.append(f"  - {icon} [{_escape(contribution.title)}]({contribution.url})")
    return "\n".join(lines)


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
    content = replace_block(args.readme.read_text(), "activity", render(highlights))
    args.readme.write_text(content)
