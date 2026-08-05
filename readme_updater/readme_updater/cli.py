"""Command-line entry point: rewrite the README's dynamic sections."""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, override

import click

from readme_updater import activity, cache, github, languages, local, monitoring
from readme_updater.markup import Marker
from readme_updater.sections import apply

if TYPE_CHECKING:
    from typing import TextIO

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(threadName)s %(name)s %(levelname)s %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S.%f %z"
LEVEL_COLORS = {
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}
RESET = "\033[0m"

# Fast enough to backfill without tripping GitHub's per-minute secondary rate
# limit; raise it toward github.MAX_CONCURRENCY when a run has headroom.
DEFAULT_CONCURRENCY = 4


class _Formatter(logging.Formatter):
    """Formats log records in UTC, coloring warning and error levels."""

    def __init__(self, *, color: bool) -> None:
        """Configure UTC timestamps and record whether to colorize.

        Args:
          color:  Whether to wrap warning and error lines in ANSI color codes.
        """
        super().__init__(LOG_FORMAT, datefmt=LOG_DATEFMT)
        self._color = color

    @override
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """Render a record's timestamp in UTC with microsecond precision.

        Uses a timezone-aware datetime so ``%f`` (microseconds) and ``%z``
        (the ``+0000`` offset) both resolve; ``struct_time`` supports neither.

        Args:
          record:  Log record whose creation time is formatted.
          datefmt: Timestamp format; defaults to :data:`LOG_DATEFMT`.

        Returns:
          The formatted UTC timestamp.
        """
        created = datetime.fromtimestamp(record.created, tz=UTC)
        return created.strftime(datefmt or LOG_DATEFMT)

    def format(self, record: logging.LogRecord) -> str:
        """Render a record, wrapping it in color when its level has one.

        Args:
          record:  Log record to render.

        Returns:
          The formatted line, wrapped in ANSI color codes for colored levels.
        """
        line = super().format(record)
        color = self._color and LEVEL_COLORS.get(record.levelno)
        return f"{color}{line}{RESET}" if color else line


def _supports_color(stream: TextIO) -> bool:
    """Decide whether ANSI colors should be written to a stream.

    Honors NO_COLOR, then FORCE_COLOR and GITHUB_ACTIONS, before falling
    back to whether the stream is attached to a terminal.

    Args:
      stream:  Output stream that log lines are written to.

    Returns:
      Whether colored output is appropriate for the stream.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("GITHUB_ACTIONS") == "true":
        return True
    return stream.isatty()


def _configure_logging(*, verbose: bool) -> None:
    """Route colored, UTC log lines for the whole process to stderr.

    Args:
      verbose:  Whether to log at DEBUG instead of INFO.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter(color=_supports_color(sys.stderr)))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=[handler],
    )


def update_languages(  # noqa: PLR0913, PLR0917 - one parameter per orchestration input
    base: Path,
    profile: github.Profile,
    contributions: list[github.Contribution],
    local_repos: tuple[Path, ...] = (),
    local_authors: tuple[str, ...] = (),
    concurrency: int = 1,
) -> tuple[str, str]:
    """Refreshes the language cache and renders the recent and all-time bars.

    Args:
      base:           Directory the asset and cache paths are resolved against.
      profile:        Profile whose repositories and commits are read.
      contributions:  Recent contributions folded into the repository list.
      local_repos:    Local git repositories folded into the all-time bar only.
      local_authors:  Author identifiers whose commits count in the local
                      repositories; empty counts every commit.
      concurrency:    Number of commit details to fetch in parallel.

    Returns:
      The rendered recent and all-time language sections, in that order.
    """
    colors = languages.load_colors(base)
    cache_path = base / languages.CACHE_PATH
    language_cache = cache.load_cache(cache_path)
    logger.debug(
        "loaded cache with %(count)d repository slices",
        {"count": len(language_cache.repos)},
    )
    repos = languages.contributed_repos(
        profile.fetch_owned_repos(), contributions, profile.profile_repo
    )
    languages.update_language_cache(
        profile,
        language_cache,
        repos,
        after_repo=lambda: cache.save_cache(cache_path, language_cache),
        concurrency=concurrency,
    )
    if local_repos:
        local.update_local_repos(language_cache, list(local_repos), list(local_authors))
    today = datetime.now(UTC).date()
    languages.prune_recent(
        language_cache, today - timedelta(days=languages.RECENT_KEEP_DAYS)
    )
    cache.save_cache(cache_path, language_cache)
    total_shares = languages.language_shares(languages.total_counts(language_cache))
    recent_shares = languages.language_shares(
        languages.recent_counts(
            language_cache, today - timedelta(days=languages.RECENT_DAYS)
        )
    )
    (base / languages.TOTAL_BAR_PATH).write_text(
        languages.language_bar(total_shares, colors)
    )
    if recent_shares:
        (base / languages.RECENT_BAR_PATH).write_text(
            languages.language_bar(recent_shares, colors)
        )
    top = ", ".join(f"{s.language} {s.share:.0f}%" for s in total_shares[:3])
    logger.info("language bars refreshed (all-time: %(top)s)", {"top": top or "empty"})
    return (
        languages.language_section(
            f"Last {languages.RECENT_DAYS} days",
            languages.RECENT_BAR_PATH,
            recent_shares,
        ),
        languages.language_section("All time", languages.TOTAL_BAR_PATH, total_shares),
    )


@click.command()
@click.option(
    "--readme-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default="README.md",
    show_default=True,
    help="Path of the profile README to rewrite.",
)
@click.option(
    "--github-username",
    envvar="GITHUB_USERNAME",
    required=True,
    help=(
        "GitHub username (login) whose public activity fills the README, for "
        "example 'whme'. Not an email address or display name: the GitHub "
        "search API attributes activity by login."
    ),
)
@click.option(
    "--other-owned-github-username",
    "other_owned_github_usernames",
    multiple=True,
    help=(
        "Additional GitHub username (login) you also own; its repositories "
        "count as your own rather than external. Repeatable; same format as "
        "--github-username."
    ),
)
@click.option(
    "--github-api-url",
    default=github.API,
    show_default=True,
    help="Base URL of the GitHub REST API.",
)
@click.option(
    "--github-token",
    envvar="GITHUB_TOKEN",
    default=None,
    help=(
        "GitHub API token to authenticate with. Preferred over the "
        "GITHUB_TOKEN environment variable, which is preferred over the token "
        "the `gh` CLI is logged in with. A token is required: unauthenticated "
        "requests exhaust GitHub's 60/hour anonymous rate limit at once."
    ),
)
@click.option(
    "--local-repo",
    "local_repos",
    multiple=True,
    type=click.Path(path_type=Path),
    help=(
        "Filesystem path to a local git repository to fold into the all-time "
        "language totals. Only aggregated per-language line counts enter the "
        "cache, behind an opaque key, so nothing about the repository is "
        "published. Repeatable."
    ),
)
@click.option(
    "--local-author",
    "local_authors",
    multiple=True,
    help=(
        "Extra commit author to count in the local repositories, matched as a "
        "case-insensitive substring of the git commit author's name or email "
        "(local git attributes by name and email, not by GitHub login). The "
        "GitHub usernames given above are always matched too; add these for "
        "identities git records only locally, such as a work email. Repeatable."
    ),
)
@click.option(
    "--concurrency",
    type=click.IntRange(1, github.MAX_CONCURRENCY),
    default=DEFAULT_CONCURRENCY,
    show_default=True,
    help=(
        "How many commit details to fetch in parallel while backfilling the "
        "language cache. Higher is faster but risks GitHub's per-minute "
        "secondary rate limit, which only pauses the run (it resumes next time)."
    ),
)
@click.option("-v", "--verbose", is_flag=True, help="Log at the DEBUG level.")
def main(  # noqa: PLR0913, PLR0917 - one parameter per CLI option
    readme_path: Path,
    github_username: str,
    other_owned_github_usernames: tuple[str, ...],
    github_api_url: str,
    github_token: str | None,
    local_repos: tuple[Path, ...],
    local_authors: tuple[str, ...],
    concurrency: int,
    *,
    verbose: bool,
) -> None:
    """Rewrite the dynamic sections of the profile README in place.

    Args:
      readme_path:                   Profile README to rewrite.
      github_username:               GitHub login whose public activity fills
                                     the README.
      other_owned_github_usernames:  Extra logins whose repositories count as
                                     owned rather than external.
      github_api_url:                Base URL of the GitHub REST API.
      github_token:                  GitHub API token from the flag or the
                                     GITHUB_TOKEN environment variable; the
                                     `gh` CLI login is the final fallback.
      local_repos:                   Local git repositories folded into the
                                     all-time language bar only.
      local_authors:                 Extra author identifiers whose commits
                                     count in the local repositories, beyond
                                     the configured GitHub usernames.
      concurrency:                   Number of commit details to fetch in
                                     parallel while backfilling.
      verbose:                       Whether to lower the log threshold to
                                     DEBUG.
    """
    _configure_logging(verbose=verbose)
    monitoring.init_sentry()
    logger.info("refreshing %(readme)s", {"readme": readme_path})
    token = github_token or github.gh_auth_token()
    if not token:
        logger.error(
            "no GitHub token: pass --github-token, set GITHUB_TOKEN, or log in "
            "with `gh auth login`. Refusing to run unauthenticated because "
            "GitHub's 60/hour anonymous rate limit is exhausted immediately."
        )
        sys.exit(-1)
    profile = github.Profile(
        username=github_username,
        owned_usernames=frozenset(
            username.lower()
            for username in (github_username, *other_owned_github_usernames)
        ),
        api_url=github_api_url,
        token=token,
    )
    contributions = profile.fetch_recent_contributions()
    highlights = activity.select_highlights(contributions)
    logger.info(
        "selected %(count)d highlighted repositories", {"count": len(highlights)}
    )
    totals = {
        contribution.repo: profile.fetch_totals(contribution.repo)
        for contribution in highlights
    }
    recent_bar, all_time_bar = update_languages(
        readme_path.parent,
        profile,
        contributions,
        local_repos,
        (*profile.owned_usernames, *local_authors),
        concurrency,
    )
    sections = {
        Marker.ACTIVITY: activity.render(
            highlights, totals, now=datetime.now(UTC), username=github_username
        ),
        Marker.RECENT_LANGUAGE_BAR: recent_bar,
        Marker.ALL_TIME_LANGUAGE_BAR: all_time_bar,
    }
    readme_path.write_text(apply(sections, readme_path.read_text()))
    logger.info("wrote %(readme)s", {"readme": readme_path})
