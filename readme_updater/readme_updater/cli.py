"""Command-line entry point: rewrite the README's dynamic sections."""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import click

from readme_updater import activity, languages, local
from readme_updater.sections import Sections

if TYPE_CHECKING:
    from typing import TextIO

logger = logging.getLogger(__name__)

# timestamp with offset, thread, logger, level, message; single process,
# so no pid.
LOG_FORMAT = "%(asctime)s %(threadName)s %(name)s %(levelname)s %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S %z"
# DEBUG and INFO stay the terminal's default color; only warnings and
# errors are colored, so they stand out.
LEVEL_COLORS = {
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}
RESET = "\033[0m"


class _Formatter(logging.Formatter):
    """The bracketed format above, with per-level color when enabled."""

    def __init__(self, *, color: bool) -> None:
        super().__init__(LOG_FORMAT, datefmt=LOG_DATEFMT)
        self.converter = time.gmtime  # timestamps in UTC, so +0000 everywhere
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        color = self._color and LEVEL_COLORS.get(record.levelno)
        return f"{color}{line}{RESET}" if color else line


def _supports_color(stream: TextIO) -> bool:
    """Whether ANSI color should be emitted, honoring the usual switches."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("GITHUB_ACTIONS") == "true":
        return True
    return hasattr(stream, "isatty") and stream.isatty()


def _configure_logging(*, verbose: bool) -> None:
    """Send readable, colored, timestamped logs to stderr.

    Only the entry point configures logging; every module logs through its
    own ``getLogger(__name__)`` so the component is visible in each line.
    INFO tells the story of a run; ``--verbose`` adds the per-request and
    per-repository detail useful when a workflow run needs debugging.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter(color=_supports_color(sys.stderr)))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=[handler],
    )


def update_languages(
    base: Path, contributions: list[activity.Contribution]
) -> tuple[list[tuple[str, float]], ...]:
    """Refresh the cache from new commits and redraw both language bars."""
    colors = languages.load_colors(base)
    cache = languages.load_cache(base)
    logger.debug(
        "loaded cache with %(count)d repository slices", {"count": len(cache.repos)}
    )
    repos = languages.contributed_repos(languages.fetch_owned_repos(), contributions)
    languages.update_language_cache(
        cache, repos, after_repo=lambda: languages.save_cache(base, cache)
    )
    if local_paths := local.local_repos():
        local.update_local_repos(cache, local_paths)
    today = datetime.now(UTC).date()
    languages.prune_recent(cache, today - timedelta(days=languages.RECENT_KEEP_DAYS))
    languages.save_cache(base, cache)
    total_shares = languages.language_shares(languages.total_counts(cache))
    recent_shares = languages.language_shares(
        languages.recent_counts(cache, today - timedelta(days=languages.RECENT_DAYS))
    )
    (base / languages.TOTAL_BAR_PATH).write_text(
        languages.language_bar(total_shares, colors)
    )
    if recent_shares:
        (base / languages.RECENT_BAR_PATH).write_text(
            languages.language_bar(recent_shares, colors)
        )
    top = ", ".join(f"{name} {share:.0f}%" for name, share in total_shares[:3])
    logger.info("language bars refreshed (all-time: %(top)s)", {"top": top or "empty"})
    return total_shares, recent_shares


@click.command()
@click.argument(
    "readme",
    type=click.Path(dir_okay=False, path_type=Path),
    default="README.md",
)
@click.option("-v", "--verbose", is_flag=True, help="Log every request at DEBUG.")
def main(readme: Path, *, verbose: bool) -> None:
    """Rewrite the dynamic sections of the profile README."""
    _configure_logging(verbose=verbose)
    logger.info("refreshing %(readme)s", {"readme": readme})
    contributions = activity.fetch_contributions()
    highlights = activity.select_highlights(contributions)
    logger.info(
        "selected %(count)d highlighted repositories", {"count": len(highlights)}
    )
    totals = {
        contribution.repo: activity.fetch_totals(contribution.repo)
        for contribution in highlights
    }
    total_shares, recent_shares = update_languages(readme.parent, contributions)
    sections = Sections(
        activity=activity.render(highlights, totals, now=datetime.now(UTC)),
        recent_language_bar=languages.language_section(
            f"Last {languages.RECENT_DAYS} days",
            languages.RECENT_BAR_PATH,
            recent_shares,
        ),
        all_time_language_bar=languages.language_section(
            "All time", languages.TOTAL_BAR_PATH, total_shares
        ),
    )
    readme.write_text(sections.apply(readme.read_text()))
    logger.info("wrote %(readme)s", {"readme": readme})
