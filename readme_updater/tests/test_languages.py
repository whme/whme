import json
from datetime import date

import pytest

from readme_updater.activity import Contribution
from readme_updater.languages import (
    LanguageCache,
    RepoStats,
    commit_additions,
    contributed_repos,
    fetch_new_commits,
    ingest_commit,
    is_countable,
    language_bar,
    language_line,
    language_section,
    language_shares,
    prune_recent,
    recent_counts,
    repo_key,
    total_counts,
    update_repo,
)


def commit(day: str, **additions: int) -> dict[str, object]:
    return {
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

    def test_the_tail_is_grouped_as_other(self) -> None:
        shares = language_shares(self.COUNTS)
        assert ("Makefile", 0.5) not in shares
        assert shares[-1] == ("Other", 0.5)

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
        assert "Other 0.5%" in legend
        assert legend.count("<img") == 3  # Rust, TypeScript, Python; not Other

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
                {"filename": "src/main.rs", "additions": 40, "changes": 55},
                {"filename": "app/view.tsx", "additions": 12, "changes": 12},
                {"filename": "app/util.ts", "additions": 8, "changes": 8},
                {"filename": "README.md", "additions": 100, "changes": 100},  # no lang
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
        stats = RepoStats.empty()
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


class TestRewrittenHistoryIsSafe:
    """A vanished head SHA must rebuild a repo slice, never double-count."""

    def test_fetch_flags_whether_the_marker_was_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = [{"sha": "c"}, {"sha": "b"}, {"sha": "a"}]
        monkeypatch.setattr("readme_updater.github.fetch", lambda _url: page)
        found_commits, found = fetch_new_commits("r", "b")
        assert [c["sha"] for c in found_commits] == ["c"]
        assert found is True
        gone_commits, gone = fetch_new_commits("r", "zzz")
        assert [c["sha"] for c in gone_commits] == ["c", "b", "a"]
        assert gone is False

    def test_update_rebuilds_the_slice_when_the_head_is_gone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The cache already counted 999 Rust lines behind head "old"; a
        # rewritten history no longer contains "old", so the slice must be
        # rebuilt from the new commits, not added to.
        key = repo_key("whme/csshw")
        cache = LanguageCache(
            repos={key: RepoStats(head="old", all_time={"Rust": 999}, recent={})}
        )
        listing = [{"sha": "new", "url": "u"}]
        detail = commit("2026-08-01", Rust=10)
        monkeypatch.setattr(
            "readme_updater.github.fetch",
            lambda url: (
                listing
                if url.endswith("/commits?author=whme&per_page=100&page=1")
                else detail
            ),
        )
        update_repo(cache, "whme/csshw")
        assert cache.repos[key].all_time == {"Rust": 10}
        assert cache.repos[key].head == "new"


class TestPrivateNamesNeverReachTheCache:
    def test_repo_key_is_a_stable_opaque_hash(self) -> None:
        key = repo_key("whme/super-secret")
        assert key == repo_key("whme/super-secret")
        assert "secret" not in key
        assert len(key) == 16

    def test_the_cache_stores_hashed_keys_not_repo_names(self) -> None:
        cache = LanguageCache(repos={})
        cache.repos[repo_key("whme/super-secret")] = RepoStats(
            head="abc123", all_time={"Rust": 1}, recent={}
        )
        serialized = json.dumps(
            {
                key: {"head": s.head, "all_time": s.all_time, "recent": s.recent}
                for key, s in cache.repos.items()
            }
        )
        assert "whme/super-secret" not in serialized


class TestContributedRepos:
    def test_unions_owned_and_contributed_and_drops_the_profile_repo(self) -> None:
        owned = [
            {"full_name": "whme/csshw"},
            {"full_name": "whme/whme"},  # the profile repo, dropped
        ]
        contributions = [
            Contribution("Checkmk/checkmk", "t", "u", "2026-08-01T00:00:00Z", "commit"),
            Contribution("whme/csshw", "t", "u", "2026-08-01T00:00:00Z", "pr"),  # dup
        ]
        assert contributed_repos(owned, contributions) == [
            "Checkmk/checkmk",
            "whme/csshw",
        ]
