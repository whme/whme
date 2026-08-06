"""Tests for the language-bar computation."""

import logging
from datetime import UTC, date, datetime
from itertools import count

import pytest

from readme_updater import github, languages
from readme_updater.cache import LanguageCache, RepoStats, repo_key
from readme_updater.github import Contribution
from readme_updater.languages import (
    BarSegment,
    LanguageShare,
    bar_segments,
    commit_additions,
    contributed_repos,
    ingest_commit,
    is_countable,
    language_bar,
    language_line,
    language_section,
    language_shares,
    language_title,
    prune_recent,
    recent_counts,
    total_counts,
    update_language_cache,
    update_repo,
)


def commit(day: str, *, sha: str = "", **additions: int) -> dict[str, object]:
    return {
        "sha": sha,
        "commit": {"committer": {"date": f"{day}T12:00:00Z"}},
        "files": [
            {"filename": f"f{i}.rs" if lang == "Rust" else f"f{i}.py", "additions": n}
            for i, (lang, n) in enumerate(additions.items())
        ],
    }


class TestLanguageShares:
    COUNTS: dict[str, int] = {  # noqa: RUF012
        "Rust": 500,
        "TypeScript": 330,
        "Python": 165,
        "Makefile": 5,
    }
    COLORS: dict[str, str] = {  # noqa: RUF012
        "Rust": "#dea584",
        "TypeScript": "#3178c6",
        "Python": "#3572A5",
    }

    def test_shares_are_percentages_sorted_descending(self) -> None:
        assert language_shares(self.COUNTS) == [
            LanguageShare("Rust", 50.0, 500),
            LanguageShare("TypeScript", 33.0, 330),
            LanguageShare("Python", 16.5, 165),
            LanguageShare("Other", 0.5, 5),
        ]

    def test_languages_below_five_percent_are_grouped_into_other(self) -> None:
        # Go 4% is under the threshold and joins Other with the 1% tail.
        shares = language_shares({"Rust": 950, "Go": 40, "Makefile": 10})
        assert shares == [
            LanguageShare("Rust", 95.0, 950),
            LanguageShare("Other", 5.0, 50),
        ]

    def test_largest_grouped_languages_are_promoted_when_other_exceeds_the_cap(
        self,
    ) -> None:
        # Six 4% languages would group into a 24% Other; the largest are pulled
        # back out one at a time until Other drops below 20%.
        counts = {"Rust": 760} | {f"L{i}": 40 for i in range(6)}
        shares = language_shares(counts)
        assert shares == [
            LanguageShare("Rust", 76.0, 760),
            LanguageShare("L0", 4.0, 40),
            LanguageShare("L1", 4.0, 40),
            LanguageShare("Other", 16.0, 160),
        ]

    def test_promoted_languages_are_not_in_the_bordered_other_region(self) -> None:
        # Promotion frees a language from Other in the bar too: the promoted
        # L0/L1 are normal segments; only the still-grouped tail is in_other.
        counts = {"Rust": 760} | {f"L{i}": 40 for i in range(6)}
        assert bar_segments(counts) == [
            BarSegment("Rust", 76.0, in_other=False),
            BarSegment("L0", 4.0, in_other=False),
            BarSegment("L1", 4.0, in_other=False),
            BarSegment("L2", 4.0, in_other=True),
            BarSegment("L3", 4.0, in_other=True),
            BarSegment("L4", 4.0, in_other=True),
            BarSegment("L5", 4.0, in_other=True),
        ]

    def test_no_languages_render_nothing(self) -> None:
        assert language_shares({}) == []

    def test_legend_language_without_an_icon_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Go clears MIN_SHARE but has no icon, so it warns.
        with caplog.at_level(logging.WARNING, logger=languages.__name__):
            language_shares({"Rust": 900, "Go": 100})
        assert "no icon for Go" in caplog.text

    def test_legend_languages_with_icons_do_not_warn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=languages.__name__):
            language_shares(self.COUNTS)
        assert caplog.records == []

    def test_grouped_other_does_not_warn_for_a_missing_icon(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Makefile has no icon but falls into Other, so it must not warn.
        with caplog.at_level(logging.WARNING, logger=languages.__name__):
            language_shares({"Rust": 960, "Makefile": 40})
        assert caplog.records == []

    def test_bar_draws_legend_languages_edge_to_edge(self) -> None:
        # The legend's own languages tile the bar left to right at full height.
        bar = language_bar(bar_segments(self.COUNTS), self.COLORS)
        assert '<rect x="0.0" width="600.0" height="14" fill="#dea584"/>' in bar
        assert '<rect x="600.0" width="396.0" height="14" fill="#3178c6"/>' in bar
        assert '<rect x="996.0" width="198.0" height="14" fill="#3572A5"/>' in bar

    def test_bar_segments_color_the_tail_but_flag_it_as_other(self) -> None:
        # TypeScript and Makefile (4%) stay their own colored segments but are
        # flagged in_other; everything under BAR_MIN_SHARE collapses into one
        # gray Other segment.
        counts = {"Rust": 915, "TypeScript": 40, "Makefile": 40, "Bit": 5}
        assert bar_segments(counts) == [
            BarSegment("Rust", 91.5, in_other=False),
            BarSegment("TypeScript", 4.0, in_other=True),
            BarSegment("Makefile", 4.0, in_other=True),
            BarSegment("Other", 0.5, in_other=True),
        ]

    def test_bar_frames_the_other_region_without_inner_borders(self) -> None:
        # The grouped languages sit on a white block that frames the region
        # inside the bar (inset y, reduced height, and a left frame on the first
        # cell). They butt edge to edge — Makefile starts exactly where
        # TypeScript ends — so no white line separates them.
        counts = {"Rust": 915, "TypeScript": 40, "Makefile": 40, "Bit": 5}
        bar = language_bar(bar_segments(counts), self.COLORS)
        assert '<rect x="0.0" width="1098.0" height="14" fill="#dea584"/>' in bar
        assert '<rect x="1098.0" width="102.0" height="14" fill="#ffffff"/>' in bar
        assert 'x="1101.0" y="3.0" width="45.0" height="8.0" fill="#3178c6"' in bar
        assert 'x="1146.0" y="3.0" width="48.0" height="8.0" fill="#ededed"' in bar
        assert 'x="1194.0" y="3.0" width="6.0" height="8.0" fill="#ededed"' in bar

    def test_unknown_languages_get_the_fallback_color(self) -> None:
        assert 'fill="#ededed"' in language_bar(
            [BarSegment("Brainfuck", 100.0, in_other=False)], self.COLORS
        )

    def test_legend_shows_icons_only_for_known_languages(self) -> None:
        legend = language_line(language_shares(self.COUNTS))
        assert '<img src="assets/rust.svg"' in legend
        assert "Rust 50.0% (500)" in legend
        assert legend.count("<img") == 3  # Rust, TypeScript, Python; not Other

    def test_legend_shows_icons_for_javascript_vue_and_lua(self) -> None:
        legend = language_line(
            [
                LanguageShare("JavaScript", 40.0, 40),
                LanguageShare("Vue", 35.0, 35),
                LanguageShare("Lua", 25.0, 25),
            ]
        )
        assert '<img src="assets/javascript.svg"' in legend
        assert '<img src="assets/vue.svg"' in legend
        assert '<img src="assets/lua.svg"' in legend

    def test_legend_shows_line_counts_concisely(self) -> None:
        legend = language_line([LanguageShare("Rust", 100.0, 12000)])
        assert "Rust 100.0% (12k)" in legend

    def test_language_title_lists_every_language_including_grouped(self) -> None:
        # The tooltip spells out the whole distribution: Makefile (0.5%) is
        # folded into Other in the legend but named in full here.
        assert language_title(self.COUNTS) == (
            "Rust 50.0% (500) · TypeScript 33.0% (330) · "
            "Python 16.5% (165) · Makefile 0.5% (5)"
        )

    def test_language_title_is_empty_without_data(self) -> None:
        assert language_title({}) == ""

    def test_language_section_is_a_labeled_bar_with_legend(self) -> None:
        section = language_section(
            "All time", "assets/languages.svg", [LanguageShare("Rust", 100.0, 500)]
        )
        assert section.startswith("<sub>All time</sub>")
        assert 'src="assets/languages.svg"' in section
        assert "title=" not in section  # no hover text without one
        assert section.endswith("Rust 100.0% (500)")

    def test_language_section_adds_the_full_distribution_as_a_hover_title(self) -> None:
        section = language_section(
            "All time",
            "assets/languages.svg",
            [LanguageShare("Rust", 100.0, 500)],
            title="Rust 100.0% (500) · Makefile 0.5% (5)",
        )
        assert 'title="Rust 100.0% (500) · Makefile 0.5% (5)"' in section

    def test_language_section_is_empty_without_data(self) -> None:
        assert language_section("All time", "assets/languages.svg", []) == ""

    def test_commit_additions_maps_extensions_and_sums_added_lines(self) -> None:
        payload = {
            "files": [
                {"filename": "src/main.rs", "additions": 40},
                {"filename": "app/view.tsx", "additions": 12},
                {"filename": "app/util.ts", "additions": 8},
                {"filename": "README.md", "additions": 100},  # no language
            ]
        }
        assert commit_additions(payload) == {"Rust": 40, "TypeScript": 20}

    def test_commit_additions_skips_generated_and_vendored_files(self) -> None:
        payload = {
            "files": [
                {"filename": "src/main.rs", "additions": 10},
                {"filename": "Cargo.lock", "additions": 500},
                {"filename": "web/node_modules/x/i.ts", "additions": 900},
                {"filename": "app/bundle.min.js", "additions": 700},
            ]
        }
        assert commit_additions(payload) == {"Rust": 10}


class TestCountability:
    @pytest.mark.parametrize(
        "path",
        [
            "package-lock.json",
            "sub/Cargo.lock",
            "web/node_modules/react/index.js",
            "crate/target/debug/build.rs",
            "app/main.min.js",
            "api/client.generated.ts",
        ],
    )
    def test_generated_and_vendored_paths_are_excluded(self, path: str) -> None:
        assert not is_countable(path)

    @pytest.mark.parametrize("path", ["src/main.rs", "app/view.tsx", "pkg/util.py"])
    def test_real_source_paths_are_counted(self, path: str) -> None:
        assert is_countable(path)


class TestLanguageCache:
    def test_ingest_accumulates_all_time_and_daily_buckets(self) -> None:
        stats = RepoStats()
        ingest_commit(stats, commit("2026-08-01", Rust=30))
        ingest_commit(stats, commit("2026-08-01", Rust=10))
        ingest_commit(stats, commit("2026-07-01", Python=5))
        assert stats.all_time == {"Rust": 40, "Python": 5}
        assert stats.recent["2026-08-01"] == {"Rust": 40}
        assert stats.recent["2026-07-01"] == {"Python": 5}

    def test_total_counts_merges_every_repository_slice(self) -> None:
        cache = LanguageCache(
            repos={
                "a": RepoStats(head="x", all_time={"Rust": 40}, recent={}),
                "b": RepoStats(head="y", all_time={"Rust": 10, "Python": 5}, recent={}),
            }
        )
        assert total_counts(cache) == {"Rust": 50, "Python": 5}

    def test_recent_counts_sums_only_buckets_within_the_window(self) -> None:
        cache = LanguageCache(
            repos={
                "a": RepoStats(
                    head="x",
                    all_time={},
                    recent={"2026-08-01": {"Rust": 40}, "2026-07-01": {"Python": 5}},
                )
            }
        )
        assert recent_counts(cache, date(2026, 7, 15)) == {"Rust": 40}

    def test_prune_drops_buckets_before_the_cutoff(self) -> None:
        cache = LanguageCache(
            repos={
                "a": RepoStats(
                    head="x",
                    all_time={},
                    recent={"2026-08-01": {"Rust": 1}, "2026-07-01": {"Python": 1}},
                )
            }
        )
        prune_recent(cache, date(2026, 7, 15))
        assert set(cache.repos["a"].recent) == {"2026-08-01"}


def test_update_rebuilds_the_slice_when_the_head_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cache already counted 999 Rust lines behind head "old"; a rewritten
    # history no longer contains "old", so the slice must be rebuilt from the
    # new commits (found=False), not added to.
    key = repo_key("whme/csshw")
    cache = LanguageCache(
        repos={key: RepoStats(head="old", all_time={"Rust": 999}, recent={})}
    )
    profile = github.Profile("whme", frozenset({"whme"}))
    monkeypatch.setattr(
        github.Profile,
        "fetch_commits_since",
        lambda *_: ([commit("2026-08-01", sha="new", Rust=10)], False),
    )
    update_repo(profile, cache, "whme/csshw")
    assert cache.repos[key].all_time == {"Rust": 10}
    assert cache.repos[key].head == "new"


def test_update_advances_head_to_the_newest_of_an_oldest_first_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # fetch_commits_since returns commits oldest first, so the head must be the
    # last one; on a partial fetch that keeps the un-fetched newer commits for
    # the next run instead of skipping them.
    key = repo_key("whme/csshw")
    cache = LanguageCache(repos={})
    profile = github.Profile("whme", frozenset({"whme"}))
    commits = [
        commit("2026-07-01", sha="old", Rust=1),
        commit("2026-07-02", sha="mid", Rust=2),
    ]
    monkeypatch.setattr(
        github.Profile,
        "fetch_commits_since",
        lambda *_: (commits, False),
    )
    update_repo(profile, cache, "whme/csshw")
    assert cache.repos[key].head == "mid"
    assert cache.repos[key].all_time == {"Rust": 3}


def test_checkpoint_fires_every_n_commits_once_the_interval_has_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commits = [commit("2026-08-01", sha=str(i), Rust=1) for i in range(120)]
    monkeypatch.setattr(
        github.Profile, "fetch_commits_since", lambda *_: (iter(commits), False)
    )
    # A clock that always reports well past the CHECKPOINT_MIN_SECONDS interval.
    monkeypatch.setattr(languages.time, "monotonic", count(0, 1000).__next__)
    calls: list[int] = []
    update_repo(
        github.Profile("whme", frozenset({"whme"})),
        LanguageCache(repos={}),
        "whme/csshw",
        checkpoint=lambda: calls.append(1),
    )
    assert len(calls) == 2  # 120 commits, checkpoints after 50 and 100


def test_checkpoint_waits_for_the_min_interval_even_past_n_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commits = [commit("2026-08-01", sha=str(i), Rust=1) for i in range(120)]
    monkeypatch.setattr(
        github.Profile, "fetch_commits_since", lambda *_: (iter(commits), False)
    )
    monkeypatch.setattr(languages.time, "monotonic", lambda: 5.0)  # frozen clock
    calls: list[int] = []
    update_repo(
        github.Profile("whme", frozenset({"whme"})),
        LanguageCache(repos={}),
        "whme/csshw",
        checkpoint=lambda: calls.append(1),
    )
    assert calls == []  # 120 commits but no time elapsed, so no checkpoint


def test_checkpoint_sees_the_partial_slice_already_in_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = repo_key("whme/csshw")
    cache = LanguageCache(repos={})
    commits = [commit("2026-08-01", sha=str(i), Rust=1) for i in range(60)]
    monkeypatch.setattr(
        github.Profile, "fetch_commits_since", lambda *_: (iter(commits), False)
    )
    monkeypatch.setattr(languages.time, "monotonic", count(0, 1000).__next__)
    seen: list[tuple[str, int]] = []

    def checkpoint() -> None:
        partial = cache.repos[key]  # slice is in the cache before the stream ends
        seen.append((partial.head, partial.all_time["Rust"]))

    update_repo(
        github.Profile("whme", frozenset({"whme"})),
        cache,
        "whme/csshw",
        checkpoint=checkpoint,
    )
    assert seen == [("49", 50)]  # one checkpoint at 50 commits: head "49", 50 lines


def test_resume_from_the_checkpointed_head_adds_without_double_counting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = repo_key("whme/csshw")
    cache = LanguageCache(repos={})
    profile = github.Profile("whme", frozenset({"whme"}))
    monkeypatch.setattr(
        github.Profile,
        "fetch_commits_since",
        lambda *_: (
            iter(
                [
                    commit("2026-08-01", sha="a", Rust=1),
                    commit("2026-08-02", sha="b", Rust=2),
                ]
            ),
            False,
        ),
    )
    update_repo(profile, cache, "whme/csshw")
    assert cache.repos[key].head == "b"
    assert cache.repos[key].all_time == {"Rust": 3}

    def resume(
        _self: github.Profile,
        _repo: str,
        head: str | None,
        _concurrency: int = 1,
        _skip_shas: set[str] | None = None,
    ) -> object:
        assert head == "b"  # the next run resumes from the checkpointed head
        return (iter([commit("2026-08-03", sha="c", Rust=4)]), True)

    monkeypatch.setattr(github.Profile, "fetch_commits_since", resume)
    update_repo(profile, cache, "whme/csshw")
    assert cache.repos[key].head == "c"
    assert cache.repos[key].all_time == {"Rust": 7}  # 3 + 4, nothing recounted


def test_update_repo_reports_its_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = github.Profile("whme", frozenset({"whme"}))
    cache = LanguageCache(repos={})

    # No new commits leaves the slice untouched: unchanged.
    monkeypatch.setattr(
        github.Profile, "fetch_commits_since", lambda *_: (iter([]), False)
    )
    assert update_repo(profile, cache, "whme/csshw") == "unchanged"

    # A first fetch with commits builds the slice from scratch: rebuilt.
    monkeypatch.setattr(
        github.Profile,
        "fetch_commits_since",
        lambda *_: (iter([commit("2026-08-01", sha="a", Rust=1)]), False),
    )
    assert update_repo(profile, cache, "whme/csshw") == "rebuilt"

    # New commits on top of the known head (found=True) add incrementally.
    monkeypatch.setattr(
        github.Profile,
        "fetch_commits_since",
        lambda *_: (iter([commit("2026-08-02", sha="b", Rust=2)]), True),
    )
    assert update_repo(profile, cache, "whme/csshw") == "incremental"


def test_fork_counts_shared_history_once_under_the_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # cssh-rs branched off csshw: the shared commit "shared" is counted once,
    # under csshw; cssh-rs keeps only its own tail ("new").
    histories = {
        "whme/csshw": [
            commit("2026-07-01", sha="shared", Rust=100),
            commit("2026-07-02", sha="p2", Rust=10),
        ],
        "whmade/cssh-rs": [
            commit("2026-07-01", sha="shared", Rust=100),
            commit("2026-08-01", sha="new", Rust=5),
        ],
    }

    def fake_commits(
        _self: github.Profile,
        repo: str,
        _head: str | None,
        _concurrency: int = 1,
        skip_shas: set[str] | None = None,
    ) -> tuple[object, bool]:
        skip = skip_shas or set()
        return ([c for c in histories[repo] if c["sha"] not in skip], False)

    monkeypatch.setattr(github.Profile, "fetch_commits_since", fake_commits)
    monkeypatch.setattr(
        github.Profile,
        "fetch_commit_shas",
        lambda _self, repo: {c["sha"] for c in histories[repo]},
    )
    cache = LanguageCache(repos={})
    update_language_cache(
        github.Profile("whme", frozenset({"whme", "whmade"})),
        cache,
        ["whme/csshw", "whmade/cssh-rs"],
        forks={"whmade/cssh-rs": "whme/csshw"},
    )
    # 100 (shared, under csshw) + 10 (csshw tail) + 5 (cssh-rs tail); "shared"
    # counted once rather than 100 twice.
    assert total_counts(cache) == {"Rust": 115}


def test_fork_exclusion_is_skipped_when_the_parent_is_not_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With the parent absent from the refreshed set, excluding its commits would
    # lose those lines, so the successor counts its full history and the parent's
    # SHAs are never even fetched.
    fetched_parent: list[str] = []

    def fake_commits(
        _self: github.Profile,
        _repo: str,
        _head: str | None,
        _concurrency: int = 1,
        skip_shas: set[str] | None = None,
    ) -> tuple[object, bool]:
        assert skip_shas is None
        return ([commit("2026-07-01", sha="shared", Rust=100)], False)

    monkeypatch.setattr(github.Profile, "fetch_commits_since", fake_commits)
    monkeypatch.setattr(
        github.Profile,
        "fetch_commit_shas",
        lambda _self, repo: fetched_parent.append(repo) or {"shared"},
    )
    cache = LanguageCache(repos={})
    update_language_cache(
        github.Profile("whme", frozenset({"whme", "whmade"})),
        cache,
        ["whmade/cssh-rs"],  # parent whme/csshw not in the refreshed set
        forks={"whmade/cssh-rs": "whme/csshw"},
    )
    assert fetched_parent == []  # parent SHAs never fetched
    assert total_counts(cache) == {"Rust": 100}


def test_update_language_cache_logs_a_per_repo_heartbeat(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    profile = github.Profile("whme", frozenset({"whme"}))
    cache = LanguageCache(repos={})
    # No repo has new commits, so each is "unchanged" — exactly the case that
    # used to produce total silence through the whole backfill.
    monkeypatch.setattr(
        github.Profile, "fetch_commits_since", lambda *_: (iter([]), False)
    )
    with caplog.at_level(logging.INFO):
        update_language_cache(profile, cache, ["whme/a", "whme/b"])
    assert "[1/2] whme/a: unchanged" in caplog.text
    assert "[2/2] whme/b: unchanged" in caplog.text


def _contribution(repo: str) -> Contribution:
    when = datetime(2026, 8, 1, tzinfo=UTC)
    return Contribution(repo, "t", "u", when, "commit", owned=True)


def test_contributed_repos_unions_and_drops_the_profile_repo() -> None:
    owned = ["whme/csshw", "whme/whme"]  # the profile repo is dropped
    contributions = [_contribution("Checkmk/checkmk"), _contribution("whme/csshw")]
    assert contributed_repos(owned, contributions, "whme/whme") == [
        "Checkmk/checkmk",
        "whme/csshw",
    ]
