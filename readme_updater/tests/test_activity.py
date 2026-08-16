"""Tests for the "Recent activity" rendering."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from readme_updater.activity import (
    TOTAL_LABEL,
    relative_label,
    render,
    select_highlights,
    title_limit,
)
from readme_updater.github import Contribution, RepoTotals, Status


def contribution(
    repo: str = "whme/csshw",
    title: str = "Add a thing",
    url: str = "https://github.com/whme/csshw/pull/1",
    date: str = "2026-08-01T10:00:00Z",
    status: Status = "pr_open",
) -> Contribution:
    return Contribution(
        repo=repo,
        title=title,
        url=url,
        date=datetime.fromisoformat(date),
        status=status,
        owned=repo.partition("/")[0].lower() in {"whme", "whmade"},
    )


class TestSelectHighlights:
    NOW = datetime(2026, 8, 15, tzinfo=UTC)  # window cutoff: 2026-08-01T00:00Z

    def test_prefers_a_distinct_repo_per_slot_and_lists_foreign_first(self) -> None:
        contributions = [
            contribution(repo="whme/a", date="2026-08-14T00:00:00Z"),
            contribution(repo="whme/b", date="2026-08-13T00:00:00Z"),
            contribution(repo="whme/c", date="2026-08-12T00:00:00Z"),
            contribution(repo="x/one", date="2026-08-11T00:00:00Z"),
            contribution(repo="x/two", date="2026-08-10T00:00:00Z"),
            contribution(repo="x/three", date="2026-08-09T00:00:00Z"),
        ]
        highlights = select_highlights(contributions, per_group=2, now=self.NOW)
        assert [highlight.repo for highlight in highlights] == [
            "x/one",
            "x/two",
            "whme/a",
            "whme/b",
        ]

    def test_fills_a_group_from_its_shown_repo_when_diversity_is_short(self) -> None:
        first = contribution(repo="x/one", date="2026-08-14T00:00:00Z")
        second = contribution(repo="x/one", date="2026-08-12T00:00:00Z", title="Two")
        owned_a = contribution(repo="whme/a", date="2026-08-13T00:00:00Z")
        owned_b = contribution(repo="whme/b", date="2026-08-11T00:00:00Z")
        highlights = select_highlights(
            [second, first, owned_a, owned_b], per_group=2, now=self.NOW
        )
        # Only one recent foreign repo, so its second contribution takes the slot.
        assert highlights == [first, second, owned_a, owned_b]

    def test_fills_across_groups_to_reach_the_combined_target(self) -> None:
        active = contribution(repo="x/active", date="2026-08-14T00:00:00Z")
        active_two = contribution(
            repo="x/active", date="2026-08-13T00:00:00Z", title="Two"
        )
        other = contribution(repo="x/other", date="2026-08-12T00:00:00Z")
        # The only owned repo is stale with a single contribution, so it fills
        # one slot; the fourth is borrowed from a shown foreign repo.
        lone_owned = contribution(repo="whme/csshw", date="2026-07-10T00:00:00Z")
        highlights = select_highlights(
            [active, active_two, other, lone_owned], per_group=2, now=self.NOW
        )
        assert len(highlights) == 4
        assert [highlight.repo for highlight in highlights] == [
            "x/active",
            "x/other",
            "whme/csshw",
            "x/active",
        ]

    def test_drops_a_stale_repo_for_a_second_recent_contribution(self) -> None:
        recent = contribution(repo="x/one", date="2026-08-14T00:00:00Z")
        recent_older = contribution(
            repo="x/one", date="2026-08-13T00:00:00Z", title="Two"
        )
        stale = contribution(repo="x/otter", date="2026-06-01T00:00:00Z", title="Stale")
        highlights = select_highlights(
            [stale, recent, recent_older], per_group=2, now=self.NOW
        )
        assert highlights == [recent, recent_older]
        assert all(highlight.repo == "x/one" for highlight in highlights)

    def test_anchors_on_the_most_recent_repo_when_all_activity_is_stale(self) -> None:
        newest = contribution(repo="x/one", date="2026-06-10T00:00:00Z")
        older = contribution(repo="x/one", date="2026-06-05T00:00:00Z", title="Older")
        other = contribution(repo="x/two", date="2026-06-08T00:00:00Z")
        highlights = select_highlights(
            [older, other, newest], per_group=2, now=self.NOW
        )
        assert highlights == [newest, older]

    def test_sorts_across_timezone_offsets(self) -> None:
        utc = contribution(repo="x/one", date="2026-08-14T09:30:00Z")
        offset = contribution(repo="x/two", date="2026-08-14T11:00:00.000+02:00")
        assert select_highlights([offset, utc], now=self.NOW) == [utc, offset]

    def test_collapses_a_merged_pr_and_its_result_commit_keeping_the_newer(
        self,
    ) -> None:
        pull_request = contribution(
            repo="x/one",
            title="Pin the toolchain",
            url="https://github.com/x/one/pull/262",
            date="2026-08-14T07:52:00Z",
            status="pr_merged",
        )
        result_commit = contribution(
            repo="x/one",
            title="Pin the toolchain (#262)",
            url="https://github.com/x/one/commit/abc123",
            date="2026-08-14T09:01:00Z",
            status="commit",
        )
        other = contribution(repo="x/two", date="2026-08-13T00:00:00Z")
        highlights = select_highlights(
            [pull_request, result_commit, other], per_group=2, now=self.NOW
        )
        # The older pull request is dropped; the freed slot goes to another repo.
        assert highlights == [result_commit, other]

    def test_keeps_a_commit_when_its_merged_pr_was_not_fetched(self) -> None:
        commit = contribution(
            repo="x/one",
            title="Fix the flake (#5)",
            url="https://github.com/x/one/commit/def456",
            date="2026-08-14T00:00:00Z",
            status="commit",
        )
        assert select_highlights([commit], now=self.NOW) == [commit]

    def test_does_not_collapse_when_the_pr_number_does_not_match(self) -> None:
        pull_request = contribution(
            repo="x/one",
            title="Pin the toolchain",
            url="https://github.com/x/one/pull/99",
            date="2026-08-14T07:52:00Z",
            status="pr_merged",
        )
        commit = contribution(
            repo="x/one",
            title="Unrelated change (#262)",
            url="https://github.com/x/one/commit/abc123",
            date="2026-08-14T09:01:00Z",
            status="commit",
        )
        highlights = select_highlights([pull_request, commit], now=self.NOW)
        assert set(highlights) == {pull_request, commit}


class TestRender:
    def test_renders_a_log_line_with_timestamp_repo_and_contribution(self) -> None:
        highlight = contribution(
            repo="whmade/cssh-rs",
            title="demo: expand the feature tour",
            url="https://github.com/whmade/cssh-rs/pull/252",
        )
        assert render([highlight]) == (
            "<code>2026-08-01 10:00 UTC</code><samp>&nbsp;</samp>&emsp;"
            '<picture><img src="https://github.com/whmade.png?size=32" width="16"'
            ' height="16" alt=""></picture>'
            ' <a href="https://github.com/whmade/cssh-rs">'
            "<code>whmade/cssh-rs</code></a> "
            '<picture><img src="assets/git-pull-request.svg"'
            ' width="16" height="16" alt="open pull request"'
            ' title="open pull request">'
            "</picture>"
            " [demo: expand the feature tour](https://github.com/whmade/cssh-rs/pull/252)"
        )

    def test_normalizes_timestamps_to_utc(self) -> None:
        highlight = contribution(date="2026-07-31T14:52:38.000+02:00")
        assert "<code>2026-07-31 12:52 UTC</code>" in render([highlight])

    def test_renders_timestamps_in_the_given_timezone(self) -> None:
        highlight = contribution(date="2026-07-31T23:30:00Z")
        assert "<code>2026-08-01 01:30 CEST</code>" in render(
            [highlight], tz=ZoneInfo("Europe/Berlin")
        )

    def test_widens_the_stamp_column_for_a_long_numeric_offset(self) -> None:
        now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        totals = {"whme/csshw": RepoTotals(commits=1, pull_requests=0, issues=0)}
        result = render(
            [contribution(repo="whme/csshw", date="2026-06-01T10:00:00Z")],
            totals,
            now=now,
            username="whme",
            tz=ZoneInfo("Asia/Kathmandu"),
        )
        first_line, totals_line = result.split("\\\n")
        # +0545 makes a 22-char stamp, one past the 21-char CEST baseline.
        assert first_line.startswith("<code>2026-06-01 15:45 +0545</code>&emsp;")
        assert f"<code>(june)</code><samp>{'&nbsp;' * 16}</samp>" in totals_line

    def test_pads_repo_names_to_equal_width_outside_the_link(self) -> None:
        result = render(
            [contribution(repo="whme/csshw"), contribution(repo="whmade/cssh-rs")]
        )
        assert (
            "<code>whme/csshw</code></a><samp>&nbsp;&nbsp;&nbsp;&nbsp;</samp>" in result
        )
        assert "<code>whmade/cssh-rs</code></a> " in result

    def test_renders_nothing_for_no_highlights(self) -> None:
        assert render([]) == ""

    def test_appends_a_totals_line_mirroring_the_contribution_line(self) -> None:
        totals = {"whme/csshw": RepoTotals(commits=210, pull_requests=57, issues=1)}
        result = render([contribution(repo="whme/csshw")], totals, username="whme")
        _, totals_line = result.split("\\\n")
        assert totals_line == (
            f"<samp>{'&nbsp;' * 21}</samp>&emsp;"
            '<picture><img src="https://github.com/whme.png?size=32" width="16"'
            ' height="16" alt=""></picture>'
            ' <a href="https://github.com/whme/csshw"><code>whme/csshw</code></a> '
            '<picture><img src="assets/mark-github.svg"'
            ' width="16" height="16" alt="total GitHub contributions"'
            ' title="total GitHub contributions"></picture>'
            " <sub>"
            "[210 commits](https://github.com/whme/csshw/commits?author=whme) · "
            "[57 pull requests]"
            "(https://github.com/whme/csshw/pulls?q=is%3Apr+author%3Awhme) · "
            "[1 issue](https://github.com/whme/csshw/issues?q=is%3Aissue+author%3Awhme)"
            "</sub>"
        )

    def test_omits_zero_counts_from_the_totals_line(self) -> None:
        totals = {"whme/csshw": RepoTotals(commits=210, pull_requests=0, issues=0)}
        result = render([contribution(repo="whme/csshw")], totals, username="whme")
        assert "pulls?q=" not in result
        assert "issues?q=" not in result
        assert "commits?author=" in result

    def test_skips_the_totals_line_for_repos_without_totals(self) -> None:
        # An entry's two lines join on a trailing backslash; a lone line has none.
        result = render([contribution()])
        assert "\\\n" not in result
        assert TOTAL_LABEL not in result

    def test_orders_newest_first_and_separates_entries_as_paragraphs(self) -> None:
        older = contribution(repo="x/old", date="2026-07-01T10:00:00Z")
        newer = contribution(repo="x/new", date="2026-08-01T10:00:00Z")
        first_entry, second_entry = render([older, newer]).split("\n\n")
        assert "x/new" in first_entry
        assert "x/old" in second_entry

    @pytest.mark.parametrize(
        ("status", "icon", "label"),
        [
            ("commit", "assets/git-commit.svg", "commit"),
            ("pr_open", "assets/git-pull-request.svg", "open pull request"),
            ("pr_draft", "assets/git-pull-request-draft.svg", "draft pull request"),
            ("pr_merged", "assets/git-merge.svg", "merged pull request"),
            ("pr_closed", "assets/git-pull-request-closed.svg", "closed pull request"),
            ("issue_open", "assets/issue-opened.svg", "open issue"),
            ("issue_closed", "assets/issue-closed.svg", "closed issue"),
            ("issue_not_planned", "assets/skip.svg", "issue closed as not planned"),
        ],
    )
    def test_picks_the_icon_and_label_matching_the_contribution_status(
        self, status: Status, icon: str, label: str
    ) -> None:
        rendered = render([contribution(status=status)])
        assert icon in rendered
        assert f'title="{label}"' in rendered

    def test_escapes_brackets_in_titles(self) -> None:
        assert "\\[cli\\]" in render([contribution(title="[cli] fix flag")])

    def test_truncates_long_titles_on_a_word_boundary(self) -> None:
        result = render([contribution(title="chore: " + " ".join(["word"] * 30))])
        rendered_title = result.split("> [")[1].split("](")[0]
        assert rendered_title.endswith("…")
        assert len(rendered_title) <= title_limit(len("whme/csshw"))

    def test_cuts_through_unbroken_tokens_instead_of_dropping_them(self) -> None:
        long_token = "fix " + "very_long_function_name" * 5
        result = render([contribution(title=long_token)])
        rendered_title = result.split("> [")[1].split("](")[0]
        assert rendered_title == long_token[: title_limit(len("whme/csshw")) - 1] + "…"

    def test_a_long_repo_name_shrinks_the_title_budget(self) -> None:
        title = " ".join(["word"] * 40)
        short_repo = render([contribution(repo="a/b", title=title)])
        long_repo = render([contribution(repo="Checkmk/checkmk", title=title)])
        short_title = short_repo.split("> [")[1].split("](")[0]
        long_title = long_repo.split("> [")[1].split("](")[0]
        assert len(long_title) <= title_limit(len("Checkmk/checkmk"))
        assert len(long_title) < len(short_title)

    def test_names_the_age_in_a_padded_pill_next_to_the_totals(self) -> None:
        now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        totals = {"whme/csshw": RepoTotals(commits=1, pull_requests=0, issues=0)}
        result = render(
            [contribution(date="2026-06-01T10:00:00Z")],
            totals,
            now=now,
            username="whme",
        )
        assert result.startswith(
            "<code>2026-06-01 10:00 UTC</code><samp>&nbsp;</samp>&emsp;"
        )
        assert f"<code>(june)</code><samp>{'&nbsp;' * 15}</samp>" in result


class TestRelativeLabel:
    NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)  # a Wednesday

    @pytest.mark.parametrize(
        ("date", "label"),
        [
            ("2026-08-05T08:00:00Z", "today"),
            ("2026-08-05T23:30:00+02:00", "today"),  # UTC 21:30 the same day
            ("2026-08-04T23:00:00Z", "yesterday"),
            ("2026-08-03T08:00:00Z", "monday"),  # 2 days ago
            ("2026-07-31T08:00:00Z", "friday"),  # 5 days ago, last named day
            ("2026-07-30T08:00:00Z", "last week"),  # 6 days ago, past the horizon
            ("2026-07-29T08:00:00Z", "last week"),  # 7 days ago
            ("2026-07-22T08:00:00Z", "2 weeks ago"),
            ("2026-07-01T08:00:00Z", "last month"),
            ("2026-03-10T08:00:00Z", "march"),  # 5 months ago
            ("2025-10-10T08:00:00Z", "october"),  # 10 months ago, last named month
            ("2025-09-10T08:00:00Z", "11 months ago"),  # past the horizon
            ("2025-06-10T08:00:00Z", "last year"),
            ("2023-01-10T08:00:00Z", "2023"),
        ],
    )
    def test_labels_any_age(self, date: str, label: str) -> None:
        assert relative_label(datetime.fromisoformat(date), self.NOW) == label

    def test_measures_the_day_boundary_in_the_given_timezone(self) -> None:
        berlin = ZoneInfo("Europe/Berlin")
        timestamp = datetime.fromisoformat("2026-08-04T22:30:00Z")
        now = datetime.fromisoformat("2026-08-05T00:00:00Z")
        assert relative_label(timestamp, now) == "yesterday"
        assert relative_label(timestamp, now, berlin) == "today"
