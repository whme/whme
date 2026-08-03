from typing import Any, ClassVar

import pytest

from readme_updater import (
    Contribution,
    Kind,
    commit_contribution,
    issue_contribution,
    render,
    replace_block,
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
        item: dict[str, Any] = {
            "repository": {"full_name": "Checkmk/checkmk"},
            "commit": {
                "message": "metric-backend: add adapter\n\nLong body.",
                "author": {"date": "2026-07-31T14:52:38.000+02:00"},
            },
            "html_url": "https://github.com/Checkmk/checkmk/commit/abc123",
        }
        result = commit_contribution(item)
        assert result.title == "metric-backend: add adapter"
        assert result.kind == "commit"


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
            '<img src="https://github.com/whmade.png?size=32" width="16"'
            ' height="16" alt="">'
            " [**whmade/cssh-rs**](https://github.com/whmade/cssh-rs) "
            '<img src="assets/git-pull-request.svg" width="16" height="16"'
            ' alt="pr">'
            " [demo: expand the feature tour](https://github.com/whmade/cssh-rs/pull/252)"
        )

    def test_normalizes_timestamps_to_utc(self) -> None:
        highlight = contribution(date="2026-07-31T14:52:38.000+02:00")
        assert "<code>2026-07-31 12:52 UTC</code>" in render([highlight])

    def test_orders_newest_first_and_joins_with_hard_breaks(self) -> None:
        older = contribution(repo="x/old", date="2026-07-01T10:00:00Z")
        newer = contribution(repo="x/new", date="2026-08-01T10:00:00Z")
        result = render([older, newer])
        first_line, second_line = result.split("\\\n")
        assert "x/new" in first_line
        assert "x/old" in second_line

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


class TestReplaceBlock:
    CONTENT = "before\n<!-- activity:start -->\nstale\n<!-- activity:end -->\nafter\n"

    def test_replaces_marker_delimited_block(self) -> None:
        result = replace_block(self.CONTENT, "activity", "fresh")
        assert result == (
            "before\n<!-- activity:start -->\nfresh\n<!-- activity:end -->\nafter\n"
        )

    def test_is_idempotent(self) -> None:
        once = replace_block(self.CONTENT, "activity", "fresh")
        assert replace_block(once, "activity", "fresh") == once

    def test_raises_on_missing_marker(self) -> None:
        with pytest.raises(ValueError, match="releases"):
            replace_block(self.CONTENT, "releases", "fresh")
