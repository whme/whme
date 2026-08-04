"""Command-line entry point: rewrite the README's dynamic sections."""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click

from readme_updater import activity, github
from readme_updater.markup import Marker
from readme_updater.sections import apply

if TYPE_CHECKING:
    from typing import TextIO

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(threadName)s %(name)s %(levelname)s %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S %z"
LEVEL_COLORS = {
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}
RESET = "\033[0m"


class _Formatter(logging.Formatter):
    """Formats log records in UTC, coloring warning and error levels."""

    def __init__(self, *, color: bool) -> None:
        """Configure UTC timestamps and record whether to colorize.

        Args:
          color:  Whether to wrap warning and error lines in ANSI color codes.
        """
        super().__init__(LOG_FORMAT, datefmt=LOG_DATEFMT)
        self.converter = time.gmtime  # log in UTC, so the offset is always +0000
        self._color = color

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
@click.option("-v", "--verbose", is_flag=True, help="Log at the DEBUG level.")
def main(  # noqa: PLR0913 - one parameter per CLI option
    readme_path: Path,
    github_username: str,
    other_owned_github_usernames: tuple[str, ...],
    github_api_url: str,
    github_token: str | None,
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
      verbose:                       Whether to lower the log threshold to
                                     DEBUG.
    """
    _configure_logging(verbose=verbose)
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
    highlights = activity.select_highlights(profile.fetch_recent_contributions())
    logger.info(
        "selected %(count)d highlighted repositories", {"count": len(highlights)}
    )
    totals = {
        contribution.repo: profile.fetch_totals(contribution.repo)
        for contribution in highlights
    }
    section = activity.render(
        highlights, totals, now=datetime.now(UTC), username=github_username
    )
    readme_path.write_text(apply({Marker.ACTIVITY: section}, readme_path.read_text()))
    logger.info("wrote %(readme)s", {"readme": readme_path})
