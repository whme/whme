"""Command-line entry point: rewrite the README's dynamic sections."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click

from readme_updater import activity, languages, local
from readme_updater.markup import replace_block

logger = logging.getLogger(__name__)


def _configure_logging(*, verbose: bool) -> None:
    """Send readable, timestamped logs to stderr.

    Only the entry point configures logging; every module logs through its
    own ``getLogger(__name__)`` so the component is visible in each line.
    INFO tells the story of a run; ``--verbose`` adds the per-request and
    per-repository detail useful when a workflow run needs debugging.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def update_languages(
    base: Path, contributions: list[activity.Contribution]
) -> tuple[list[tuple[str, float]], ...]:
    """Refresh the cache from new commits and redraw both language bars."""
    colors = languages.load_colors(base)
    cache = languages.load_cache(base)
    logger.debug("loaded cache with %d repository slices", len(cache.repos))
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
    logger.info("language bars refreshed (all-time: %s)", top or "empty")
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
    logger.info("refreshing %s", readme)
    contributions = activity.fetch_contributions()
    highlights = activity.select_highlights(contributions)
    logger.info("selected %d highlighted repositories", len(highlights))
    totals = {
        contribution.repo: activity.fetch_totals(contribution.repo)
        for contribution in highlights
    }
    total_shares, recent_shares = update_languages(readme.parent, contributions)
    content = replace_block(
        readme.read_text(),
        "activity",
        activity.render(highlights, totals, now=datetime.now(UTC)),
    )
    content = replace_block(
        content, "languages", languages.render_languages(total_shares, recent_shares)
    )
    readme.write_text(content)
    logger.info("wrote %s", readme)
