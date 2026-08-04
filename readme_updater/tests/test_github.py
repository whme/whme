"""Tests for the GitHub API boundary."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from readme_updater import github

if TYPE_CHECKING:
    from collections.abc import Iterable

PROFILE = github.Profile("whme", frozenset({"whme", "whmade"}))

ISSUE_ITEMS: list[dict[str, Any]] = [
    {
        "repository_url": f"{github.API}/repos/Checkmk/otter",
        "title": "Add dispatch trigger",
        "html_url": "https://github.com/Checkmk/otter/pull/7",
        "created_at": "2026-06-05T08:00:00Z",
        "pull_request": {},
    },
    {
        "repository_url": f"{github.API}/repos/Checkmk/otter",
        "title": "Flaky test",
        "html_url": "https://github.com/Checkmk/otter/issues/8",
        "created_at": "2026-06-04T08:00:00Z",
    },
    {
        "repository_url": f"{github.API}/repos/whme/whme",
        "title": "chore: refresh README",
        "html_url": "https://github.com/whme/whme/pull/9",
        "created_at": "2026-06-06T08:00:00Z",
        "pull_request": {},
    },
]
COMMIT_ITEMS: list[dict[str, Any]] = [
    {
        "repository": {"full_name": "whme/csshw", "private": False},
        "commit": {
            "message": "metric-backend: add adapter\n\nLong body.",
            "author": {"date": "2026-07-31T14:52:38.000+02:00"},
            "committer": {"date": "2026-08-03T09:00:00.000+02:00"},
        },
        "html_url": "https://github.com/whme/csshw/commit/abc123",
    },
    {
        "repository": {"full_name": "whme/secret", "private": True},
        "commit": {
            "message": "private work",
            "committer": {"date": "2026-08-02T09:00:00Z"},
        },
        "html_url": "https://github.com/whme/secret/commit/def456",
    },
]


def _fetch_json_canned(_self: github.Profile, url: str) -> dict[str, Any]:
    if "search/commits" in url:
        return {"items": COMMIT_ITEMS}
    if "-user" in url:  # the second, external-only issue search
        return {"items": []}
    return {"items": ISSUE_ITEMS}


def _contribution(
    contributions: Iterable[github.Contribution], repo: str
) -> github.Contribution:
    return next(c for c in contributions if c.repo == repo)


class TestQuery:
    def test_restricts_every_search_to_public_activity(self) -> None:
        assert PROFILE._build_query() == "author:whme is:public"
        assert (
            PROFILE._build_query("type:pr repo:x/y")
            == "author:whme is:public type:pr repo:x/y"
        )


class TestRecentContributions:
    def test_separates_kinds_and_types_and_typed_at_the_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(github.Profile, "_fetch_json", _fetch_json_canned)
        contributions = PROFILE.fetch_recent_contributions()
        by_kind = {contribution.kind for contribution in contributions}
        assert by_kind == {"pr", "issue", "commit"}
        pull_request = _contribution(contributions, "Checkmk/otter")
        assert pull_request.kind == "pr"
        assert pull_request.url == "https://github.com/Checkmk/otter/pull/7"
        assert isinstance(pull_request.date, datetime)
        assert not pull_request.owned

    def test_uses_the_committer_date_and_marks_owned_repos(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(github.Profile, "_fetch_json", _fetch_json_canned)
        commit = _contribution(PROFILE.fetch_recent_contributions(), "whme/csshw")
        assert commit.date == datetime.fromisoformat("2026-08-03T09:00:00.000+02:00")
        assert commit.owned

    def test_drops_private_commits_and_the_profile_repo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(github.Profile, "_fetch_json", _fetch_json_canned)
        repos = {
            contribution.repo for contribution in PROFILE.fetch_recent_contributions()
        }
        assert "whme/secret" not in repos
        assert PROFILE.profile_repo not in repos


class TestTotals:
    def test_counts_commits_pull_requests_and_issues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake(_self: github.Profile, url: str) -> dict[str, int]:
            if "search/commits" in url:
                return {"total_count": 210}
            return {"total_count": 57 if "type%3Apr" in url else 1}

        monkeypatch.setattr(github.Profile, "_fetch_json", fake)
        assert PROFILE.fetch_totals("whme/csshw") == github.RepoTotals(
            commits=210, pull_requests=57, issues=1
        )


class TestFetch:
    class _Response:
        def __init__(self, status: int, data: bytes) -> None:
            self.status = status
            self.data = data

    def test_returns_parsed_json_and_authenticates_with_the_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, dict[str, str]] = {}

        def request(
            _method: str, _url: str, headers: dict[str, str]
        ) -> TestFetch._Response:
            captured["headers"] = headers
            return TestFetch._Response(200, b'{"ok": true}')

        monkeypatch.setattr(github._http, "request", request)
        profile = github.Profile("whme", frozenset({"whme"}), token="secret")  # noqa: S106
        assert profile._fetch_json("https://api.github.com/x") == {"ok": True}
        assert captured["headers"]["Authorization"] == "Bearer secret"
        assert captured["headers"]["User-Agent"] == "whme-readme-updater"

    def test_sends_no_authorization_without_a_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, dict[str, str]] = {}

        def request(
            _method: str, _url: str, headers: dict[str, str]
        ) -> TestFetch._Response:
            captured["headers"] = headers
            return TestFetch._Response(200, b"{}")

        monkeypatch.setattr(github._http, "request", request)
        github.Profile("whme", frozenset({"whme"}))._fetch_json("https://x/y")
        assert "Authorization" not in captured["headers"]

    def test_raises_on_error_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            github._http,
            "request",
            lambda *_a, **_k: TestFetch._Response(404, b"{}"),
        )
        with pytest.raises(github.GitHubError, match="404"):
            PROFILE._fetch_json("https://api.github.com/missing")


class TestGhAuthToken:
    def test_returns_the_stripped_token_the_cli_reports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def run(*_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(stdout="gho_secret\n")

        monkeypatch.setattr(github.subprocess, "run", run)
        assert github.gh_auth_token() == "gho_secret"

    def test_returns_none_when_the_cli_is_missing_or_logged_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def run(*_args: object, **_kwargs: object) -> SimpleNamespace:
            raise FileNotFoundError

        monkeypatch.setattr(github.subprocess, "run", run)
        assert github.gh_auth_token() is None
