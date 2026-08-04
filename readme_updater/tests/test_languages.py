"""Tests for the language-bar computation."""

from datetime import UTC, date, datetime
from itertools import count

import pytest

from readme_updater import github, languages
from readme_updater.cache import LanguageCache, RepoStats, repo_key
from readme_updater.github import Contribution
from readme_updater.languages import (
    commit_additions,
    contributed_repos,
    ingest_commit,
    is_countable,
    language_bar,
    language_line,
    language_section,
    language_shares,
    prune_recent,
    recent_counts,
    total_counts,
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
            ("Rust", 50.0),
            ("TypeScript", 33.0),
            ("Python", 16.5),
            ("Other", 0.5),
        ]

    def test_no_languages_render_nothing(self) -> None:
        assert language_shares({}) == []

    def test_bar_segments_cover_the_full_width_in_order(self) -> None:
        bar = language_bar(language_shares(self.COUNTS), self.COLORS)
        assert '<rect x="0.0" width="600.0" height="14" fill="#dea584"/>' in bar
        assert 'x="600.0" width="396.0"' in bar
        assert bar.count("<rect") == 5  # 4 segments + the clip rect

    def test_unknown_languages_get_the_fallback_color(self) -> None:
        assert 'fill="#ededed"' in language_bar([("Brainfuck", 100.0)], self.COLORS)

    def test_legend_shows_icons_only_for_known_languages(self) -> None:
        legend = language_line(language_shares(self.COUNTS))
        assert '<img src="assets/rust.svg"' in legend
        assert "Rust 50.0%" in legend
        assert legend.count("<img") == 3  # Rust, TypeScript, Python; not Other

    def test_legend_shows_icons_for_javascript_vue_and_lua(self) -> None:
        legend = language_line([("JavaScript", 40.0), ("Vue", 35.0), ("Lua", 25.0)])
        assert '<img src="assets/javascript.svg"' in legend
        assert '<img src="assets/vue.svg"' in legend
        assert '<img src="assets/lua.svg"' in legend

    def test_language_section_is_a_labeled_bar_with_legend(self) -> None:
        section = language_section(
            "All time", "assets/languages.svg", [("Rust", 100.0)]
        )
        assert section.startswith("<sub>All time</sub>")
        assert 'src="assets/languages.svg"' in section
        assert section.endswith("Rust 100.0%")

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
        lambda _self, _repo, _head: ([commit("2026-08-01", sha="new", Rust=10)], False),
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
        lambda _self, _repo, _head: (commits, False),
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

    def resume(_self: github.Profile, _repo: str, head: str | None) -> object:
        assert head == "b"  # the next run resumes from the checkpointed head
        return (iter([commit("2026-08-03", sha="c", Rust=4)]), True)

    monkeypatch.setattr(github.Profile, "fetch_commits_since", resume)
    update_repo(profile, cache, "whme/csshw")
    assert cache.repos[key].head == "c"
    assert cache.repos[key].all_time == {"Rust": 7}  # 3 + 4, nothing recounted


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
