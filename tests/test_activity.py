from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest

from readme_updater.activity import (
    TITLE_LIMIT,
    Contribution,
    Kind,
    RepoTotals,
    commit_contribution,
    issue_contribution,
    relative_label,
    render,
    select_highlights,
)


def contribution(
    repo: str = "whme/csshw",
    title: str = "Add a thing",
    url: str = "https://github.com/whme/csshw/pull/1",
    date: str = "2026-08-01T10:00:00Z",
    kind: Kind = "pr",
) -> Contribution:
    return Contribution(repo=repo, title=title, url=url, date=date, kind=kind)


class TestContribution:
    @pytest.mark.parametrize("repo", ["whme/csshw", "whmade/cssh-rs", "WHME/other"])
    def test_owned(self, repo: str) -> None:
        assert contribution(repo=repo).owned

    def test_not_owned(self) -> None:
        assert not contribution(repo="Checkmk/checkmk").owned


class TestParsing:
    ISSUE_ITEM: ClassVar[dict[str, Any]] = {
        "repository_url": "https://api.github.com/repos/Checkmk/otter",
        "title": "Add dispatch trigger",
        "html_url": "https://github.com/Checkmk/otter/pull/7",
        "created_at": "2026-06-05T08:00:00Z",
    }

    def test_issue_contribution(self) -> None:
        assert issue_contribution(self.ISSUE_ITEM) == Contribution(
            repo="Checkmk/otter",
            title="Add dispatch trigger",
            url="https://github.com/Checkmk/otter/pull/7",
            date="2026-06-05T08:00:00Z",
            kind="issue",
        )

    def test_pull_requests_are_detected_by_their_payload_key(self) -> None:
        item = {**self.ISSUE_ITEM, "pull_request": {}}
        assert issue_contribution(item).kind == "pr"

    def test_commit_contribution_takes_message_subject(self) -> None:
        result = commit_contribution(self.COMMIT_ITEM)
        assert result.title == "metric-backend: add adapter"
        assert result.kind == "commit"

    def test_commit_contribution_uses_the_committer_date(self) -> None:
        # The GitHub UI shows the committer date; commits landing through a
        # review pipeline are committed well after they are authored.
        assert (
            commit_contribution(self.COMMIT_ITEM).date
            == "2026-08-03T09:00:00.000+02:00"
        )

    COMMIT_ITEM: ClassVar[dict[str, Any]] = {
        "repository": {"full_name": "Checkmk/checkmk"},
        "commit": {
            "message": "metric-backend: add adapter\n\nLong body.",
            "author": {"date": "2026-07-31T14:52:38.000+02:00"},
            "committer": {"date": "2026-08-03T09:00:00.000+02:00"},
        },
        "html_url": "https://github.com/Checkmk/checkmk/commit/abc123",
    }


class TestSelectHighlights:
    def test_keeps_only_most_recent_contribution_per_repo(self) -> None:
        newer = contribution(date="2026-08-01T10:00:00Z")
        older = contribution(date="2026-07-01T10:00:00Z", title="Old news")
        assert select_highlights([older, newer]) == [newer]

    def test_caps_repos_per_group_and_lists_foreign_repos_first(self) -> None:
        contributions = [
            contribution(repo="whme/a", date="2026-08-05T00:00:00Z"),
            contribution(repo="whme/b", date="2026-08-04T00:00:00Z"),
            contribution(repo="whme/c", date="2026-08-03T00:00:00Z"),
            contribution(repo="x/one", date="2026-01-02T00:00:00Z"),
            contribution(repo="x/two", date="2026-01-01T00:00:00Z"),
        ]
        highlights = select_highlights(contributions, per_group=2)
        assert [highlight.repo for highlight in highlights] == [
            "x/one",
            "x/two",
            "whme/a",
            "whme/b",
        ]

    def test_skips_the_profile_repo(self) -> None:
        assert select_highlights([contribution(repo="whme/whme")]) == []

    def test_sorts_across_timezone_offsets(self) -> None:
        utc = contribution(repo="x/one", date="2026-08-01T09:30:00Z")
        offset = contribution(repo="x/two", date="2026-08-01T11:00:00.000+02:00")
        assert select_highlights([offset, utc]) == [utc, offset]


class TestRender:
    def test_renders_a_log_line_with_timestamp_repo_and_contribution(self) -> None:
        highlight = contribution(
            repo="whmade/cssh-rs",
            title="demo: expand the feature tour",
            url="https://github.com/whmade/cssh-rs/pull/252",
        )
        assert render([highlight]) == (
            "<code>2026-08-01 10:00 UTC</code>&emsp;"
            '<picture><img src="https://github.com/whmade.png?size=32" width="16"'
            ' height="16" alt=""></picture>'
            ' <a href="https://github.com/whmade/cssh-rs">'
            "<code>whmade/cssh-rs</code></a> "
            '<picture><img src="assets/git-pull-request.svg" width="16" height="16"'
            ' alt="pr"></picture>'
            " [demo: expand the feature tour](https://github.com/whmade/cssh-rs/pull/252)"
        )

    def test_normalizes_timestamps_to_utc(self) -> None:
        highlight = contribution(date="2026-07-31T14:52:38.000+02:00")
        assert "<code>2026-07-31 12:52 UTC</code>" in render([highlight])

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
        result = render([contribution(repo="whme/csshw")], totals)
        _, totals_line = result.split("\\\n")
        assert totals_line == (
            f"<samp>{'&nbsp;' * 20}</samp>&emsp;"
            '<picture><img src="https://github.com/whme.png?size=32" width="16"'
            ' height="16" alt=""></picture>'
            ' <a href="https://github.com/whme/csshw"><code>whme/csshw</code></a> '
            '<picture><img src="assets/mark-github.svg" width="16" height="16"'
            ' alt="total"></picture>'
            " <sub>"
            "[210 commits](https://github.com/whme/csshw/commits?author=whme) · "
            "[57 pull requests]"
            "(https://github.com/whme/csshw/pulls?q=is%3Apr+author%3Awhme) · "
            "[1 issue](https://github.com/whme/csshw/issues?q=is%3Aissue+author%3Awhme)"
            "</sub>"
        )

    def test_omits_zero_counts_from_the_totals_line(self) -> None:
        totals = {"whme/csshw": RepoTotals(commits=210, pull_requests=0, issues=0)}
        result = render([contribution(repo="whme/csshw")], totals)
        assert "pull request" not in result
        assert "issue" not in result

    def test_skips_the_totals_line_for_repos_without_totals(self) -> None:
        assert "<samp>" not in render([contribution()])

    def test_orders_newest_first_and_separates_entries_as_paragraphs(self) -> None:
        older = contribution(repo="x/old", date="2026-07-01T10:00:00Z")
        newer = contribution(repo="x/new", date="2026-08-01T10:00:00Z")
        result = render([older, newer])
        first_entry, second_entry = result.split("\n\n")
        assert "x/new" in first_entry
        assert "x/old" in second_entry

    def test_joins_the_lines_of_one_entry_with_a_hard_break(self) -> None:
        totals = {"whme/csshw": RepoTotals(commits=1, pull_requests=0, issues=0)}
        result = render([contribution(repo="whme/csshw")], totals)
        assert "\\\n<samp>" in result
        assert "\n\n" not in result

    @pytest.mark.parametrize(
        ("kind", "icon"),
        [
            ("pr", "assets/git-pull-request.svg"),
            ("issue", "assets/issue-opened.svg"),
            ("commit", "assets/git-commit.svg"),
        ],
    )
    def test_picks_the_icon_matching_the_contribution_kind(
        self, kind: Kind, icon: str
    ) -> None:
        assert icon in render([contribution(kind=kind)])

    def test_escapes_brackets_in_titles(self) -> None:
        assert "\\[cli\\]" in render([contribution(title="[cli] fix flag")])

    def test_truncates_long_titles_with_an_ellipsis(self) -> None:
        long_title = "chore: " + " ".join(["word"] * 30)
        result = render([contribution(title=long_title)])
        rendered_title = result.split("> [")[1].split("](")[0]
        assert rendered_title.endswith("…")
        assert len(rendered_title) <= TITLE_LIMIT

    def test_keeps_short_titles_untouched(self) -> None:
        assert "[Add a thing](" in render([contribution(title="Add a thing")])

    def test_cuts_through_unbroken_tokens_instead_of_dropping_them(self) -> None:
        long_token = "fix " + "very_long_function_name" * 5
        result = render([contribution(title=long_token)])
        rendered_title = result.split("> [")[1].split("](")[0]
        assert rendered_title == (long_token[: TITLE_LIMIT - 1] + "…")

    def test_the_first_line_always_shows_the_plain_timestamp(self) -> None:
        now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        result = render([contribution(date="2026-08-01T10:00:00Z")], now=now)
        assert result.startswith("<code>2026-08-01 10:00 UTC</code>&emsp;")

    def test_the_totals_slot_names_the_age_in_a_padded_pill(self) -> None:
        now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        totals = {"whme/csshw": RepoTotals(commits=1, pull_requests=0, issues=0)}
        result = render([contribution(date="2026-08-01T10:00:00Z")], totals, now=now)
        _, totals_line = result.split("\\\n")
        assert totals_line.startswith(
            f"<code>(today)</code><samp>{'&nbsp;' * 13}</samp>&emsp;"
        )

    def test_older_entries_name_their_age_too(self) -> None:
        now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        totals = {"whme/csshw": RepoTotals(commits=1, pull_requests=0, issues=0)}
        result = render([contribution(date="2026-06-01T10:00:00Z")], totals, now=now)
        assert f"<code>(2 months ago)</code><samp>{'&nbsp;' * 6}</samp>" in result


class TestRelativeLabel:
    NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)  # a Wednesday

    @pytest.mark.parametrize(
        ("date", "label"),
        [
            ("2026-08-05T08:00:00Z", "today"),
            ("2026-08-05T23:30:00+02:00", "today"),  # UTC 21:30 the same day
            ("2026-08-04T23:00:00Z", "yesterday"),
            ("2026-08-03T08:00:00Z", "this week"),  # Monday
            ("2026-08-02T08:00:00Z", "last week"),  # Sunday, previous ISO week
            ("2026-07-22T08:00:00Z", "2 weeks ago"),
            ("2026-07-01T08:00:00Z", "last month"),
            ("2026-03-10T08:00:00Z", "5 months ago"),
            ("2025-06-10T08:00:00Z", "last year"),
            ("2023-01-10T08:00:00Z", "3 years ago"),
        ],
    )
    def test_labels_any_age(self, date: str, label: str) -> None:
        timestamp = datetime.fromisoformat(date)
        assert relative_label(timestamp, self.NOW) == label
