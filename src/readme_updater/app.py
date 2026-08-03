"""Command-line entry point: rewrite the README's dynamic sections."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from readme_updater import activity, languages
from readme_updater.markup import replace_block


def update_languages(base: Path) -> tuple[list[tuple[str, float]], ...]:
    """Refresh the cache from new commits and redraw both language bars."""
    colors = languages.load_colors(base)
    cache = languages.load_cache(base)
    contributions = activity.fetch_contributions()
    repos = languages.contributed_repos(languages.fetch_owned_repos(), contributions)
    languages.update_language_cache(
        cache, repos, after_repo=lambda: languages.save_cache(base, cache)
    )
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
    return total_shares, recent_shares


def main(argv: list[str] | None = None) -> None:
    """Rewrite the dynamic sections of the README with fresh data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("readme", nargs="?", type=Path, default=Path("README.md"))
    args = parser.parse_args(argv)
    highlights = activity.select_highlights(activity.fetch_contributions())
    totals = {
        contribution.repo: activity.fetch_totals(contribution.repo)
        for contribution in highlights
    }
    total_shares, recent_shares = update_languages(args.readme.parent)
    content = replace_block(
        args.readme.read_text(),
        "activity",
        activity.render(highlights, totals, now=datetime.now(UTC)),
    )
    content = replace_block(
        content, "languages", languages.render_languages(total_shares, recent_shares)
    )
    args.readme.write_text(content)
