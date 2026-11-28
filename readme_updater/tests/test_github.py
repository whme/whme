"""Tests for the GitHub API boundary."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from traceback import format_exception
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import urllib3

from readme_updater import github
from readme_updater.cache import repo_key

if TYPE_CHECKING:
    from collections.abc import Iterable

PROFILE = github.Profile("whme", frozenset({"whme", "whmade"}))
CSSHW = github.Repo("whme/csshw", private=False)
SECRET = github.Repo("whme/secret", private=True)

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


class TestAdvancedSearch:
    def test_opts_into_advanced_search_for_issues_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        urls: list[str] = []

        def record(_self: github.Profile, url: str) -> dict[str, Any]:
            urls.append(url)
            return _fetch_json_canned(_self, url)

        monkeypatch.setattr(github.Profile, "_fetch_json", record)
        PROFILE.fetch_recent_contributions()
        issue_urls = [url for url in urls if "search/issues" in url]
        commit_urls = [url for url in urls if "search/commits" in url]
        assert issue_urls
        assert all("advanced_search=true" in url for url in issue_urls)
        assert commit_urls
        assert not any("advanced_search" in url for url in commit_urls)


class TestRecentContributions:
    def test_separates_kinds_and_types_and_typed_at_the_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(github.Profile, "_fetch_json", _fetch_json_canned)
        contributions = PROFILE.fetch_recent_contributions()
        by_status = {contribution.status for contribution in contributions}
        assert by_status == {"pr_open", "issue_open", "commit"}
        pull_request = _contribution(contributions, "Checkmk/otter")
        assert pull_request.status == "pr_open"
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

    def test_drops_private_commits_but_keeps_the_profile_repo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(github.Profile, "_fetch_json", _fetch_json_canned)
        repos = {
            contribution.repo for contribution in PROFILE.fetch_recent_contributions()
        }
        assert "whme/secret" not in repos
        # The author-scoped search never matches the bot's refresh commits.
        assert PROFILE.profile_repo in repos

    def test_announces_the_fetch_and_the_per_search_limit(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(github.Profile, "_fetch_json", _fetch_json_canned)
        with caplog.at_level(logging.INFO):
            PROFILE.fetch_recent_contributions()
        assert "fetching recent contributions for @whme" in caplog.text
        # The per-search cap explains why the total maxes out (3 searches x 50).
        assert str(github.SEARCH_LIMIT) in caplog.text


class TestIssueStatus:
    @pytest.mark.parametrize(
        ("item", "status"),
        [
            ({"pull_request": {"merged_at": "2026-06-05T09:00:00Z"}}, "pr_merged"),
            # A merged PR is also reported closed; the merge must win.
            (
                {
                    "state": "closed",
                    "pull_request": {"merged_at": "2026-06-05T09:00:00Z"},
                },
                "pr_merged",
            ),
            ({"state": "closed", "pull_request": {"merged_at": None}}, "pr_closed"),
            ({"state": "open", "draft": True, "pull_request": {}}, "pr_draft"),
            ({"state": "open", "draft": False, "pull_request": {}}, "pr_open"),
            ({"pull_request": {}}, "pr_open"),
            ({"state": "open"}, "issue_open"),
            # A reopened issue is reported open, so it needs no special case.
            ({"state": "open", "state_reason": "reopened"}, "issue_open"),
            ({"state": "closed", "state_reason": "completed"}, "issue_closed"),
            ({"state": "closed"}, "issue_closed"),
            ({"state": "closed", "state_reason": "not_planned"}, "issue_not_planned"),
        ],
    )
    def test_names_every_pull_request_and_issue_state(
        self, item: dict[str, Any], status: github.Status
    ) -> None:
        assert github._issue_status(item) == status


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


class TestOwnedRepos:
    def test_keeps_forks_and_drops_other_owners(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        listing = [
            {"full_name": "whme/csshw", "fork": False, "visibility": "public"},
            {"full_name": "whme/forked", "fork": True, "visibility": "private"},
            {"full_name": "someoneelse/x", "fork": False, "visibility": "public"},
        ]
        monkeypatch.setattr(github.Profile, "_fetch_json", lambda _self, *_: listing)
        assert PROFILE.fetch_owned_repos() == [
            CSSHW,
            github.Repo("whme/forked", private=True),  # forks count, visibility kept
        ]

    def test_hides_every_non_public_repository_of_the_owned_organizations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # whmade is an organization reached through organization_member; only
        # an explicitly public repository may be shown by name.
        listing = [
            {"full_name": "whmade/open", "visibility": "public"},
            {"full_name": "whmade/closed", "visibility": "private"},
            {"full_name": "whmade/staff", "visibility": "internal"},
            {"full_name": "whmade/unknown"},  # no visibility reported
            {"full_name": "otherorg/closed", "visibility": "private"},  # not owned
        ]
        monkeypatch.setattr(github.Profile, "_fetch_json", lambda _self, *_: listing)
        assert PROFILE.fetch_owned_repos() == [
            github.Repo("whmade/open", private=False),
            github.Repo("whmade/closed", private=True),
            github.Repo("whmade/staff", private=True),
            github.Repo("whmade/unknown", private=True),
        ]

    def test_falls_back_to_public_listings_without_a_user_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake(_self: github.Profile, url: str, *_: object) -> list[dict[str, Any]]:
            if "/user/repos" in url:
                raise github.GitHubError("no user context")
            account = url.partition("/users/")[2].partition("/")[0]
            return [{"full_name": f"{account}/repo", "visibility": "public"}]

        monkeypatch.setattr(github.Profile, "_fetch_json", fake)
        assert PROFILE.fetch_owned_repos() == [
            github.Repo("whmade/repo", private=False),
            github.Repo("whme/repo", private=False),
        ]


class TestCommitsSince:
    def test_stops_at_the_known_head_and_fetches_each_in_full(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        listing = [{"sha": "c", "url": "uc"}, {"sha": "b", "url": "ub"}]

        def fake(_self: github.Profile, url: str, *_: object) -> Any:
            return listing if "/commits?" in url else {"sha": url[1:]}

        monkeypatch.setattr(github.Profile, "_fetch_json", fake)
        commits, found = PROFILE.fetch_commits_since(CSSHW, "b")
        assert [commit["sha"] for commit in commits] == ["c"]
        assert found is True

    def test_enumerates_every_page_until_one_comes_back_short(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        full = [{"sha": str(i), "url": str(i)} for i in range(github.PER_PAGE)]
        pages = {1: full, 2: [{"sha": "last", "url": "last"}]}

        def fake(_self: github.Profile, url: str, *_: object) -> Any:
            if "/commits?" in url:
                page = int(url.rpartition("page=")[2])
                return pages.get(page, [])
            return {"sha": url}

        monkeypatch.setattr(github.Profile, "_fetch_json", fake)
        commits, found = PROFILE.fetch_commits_since(CSSHW, None)
        assert len(list(commits)) == github.PER_PAGE + 1  # both pages, no cap
        assert found is False

    def test_tolerates_inaccessible_repositories(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_error(_self: github.Profile, *_: object) -> Any:
            raise github.GitHubError("no access")

        monkeypatch.setattr(github.Profile, "_fetch_json", raise_error)
        commits, found = PROFILE.fetch_commits_since(SECRET, None)
        assert list(commits) == []
        assert found is False

    def test_fetches_details_lazily_not_when_the_iterator_is_built(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Laziness is what lets the caller checkpoint mid-repository: building
        # the iterator lists the refs but must not fetch any detail yet.
        fetched: list[str] = []

        def fake(_self: github.Profile, url: str, *_: object) -> Any:
            fetched.append(url)
            return [{"sha": "a", "url": "ua"}] if "/commits?" in url else {"sha": "a"}

        monkeypatch.setattr(github.Profile, "_fetch_json", fake)
        commits, _ = PROFILE.fetch_commits_since(CSSHW, None)
        assert "ua" not in fetched  # listed, but no detail fetched yet
        assert [commit["sha"] for commit in commits] == ["a"]
        assert "ua" in fetched  # consuming the iterator fetches the detail

    def test_logs_the_number_of_commit_details_it_will_fetch(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        listing = [{"sha": str(i), "url": str(i)} for i in range(3)]

        def fake(_self: github.Profile, url: str, *_: object) -> Any:
            return listing if "/commits?" in url else {"sha": url}

        monkeypatch.setattr(github.Profile, "_fetch_json", fake)
        with caplog.at_level(logging.INFO):
            PROFILE.fetch_commits_since(CSSHW, None)
        assert "whme/csshw: fetching 3 commit details" in caplog.text

    def test_records_oldest_first_progress_and_logs_error_when_a_detail_fails(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The listing is newest first; details are fetched oldest first, so a
        # failure part-way keeps the oldest, contiguous commits and stops.
        listing = [
            {"sha": "c", "url": "uc"},
            {"sha": "b", "url": "ub"},
            {"sha": "a", "url": "ua"},
        ]

        def fake(_self: github.Profile, url: str, *_: object) -> Any:
            if "/commits?" in url:
                return listing
            if url == "ub":
                raise github.GitHubError("rate limited")
            return {"sha": url[1:]}

        monkeypatch.setattr(github.Profile, "_fetch_json", fake)
        with caplog.at_level(logging.ERROR):
            commits, found = PROFILE.fetch_commits_since(CSSHW, None)
            shas = [commit["sha"] for commit in commits]  # consume within capture
        assert shas == ["a"]  # oldest only, stops before the failing "b"
        assert found is False
        assert "stopped fetching" in caplog.text
        assert caplog.records[-1].levelno == logging.ERROR

    def test_concurrent_fetch_yields_the_same_oldest_first_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Details are fetched in parallel windows but consumed in submission
        # order, so the yielded order matches the sequential path exactly.
        listing = [{"sha": str(i), "url": str(i)} for i in range(10)]  # newest first

        def fake(_self: github.Profile, url: str, *_: object) -> Any:
            return listing if "/commits?" in url else {"sha": url}

        monkeypatch.setattr(github.Profile, "_fetch_json", fake)
        oldest_first = [str(i) for i in reversed(range(10))]
        sequential, _ = PROFILE.fetch_commits_since(CSSHW, None, concurrency=1)
        concurrent, _ = PROFILE.fetch_commits_since(CSSHW, None, concurrency=4)
        assert [c["sha"] for c in sequential] == oldest_first
        assert [c["sha"] for c in concurrent] == oldest_first

    def test_a_later_success_in_the_window_is_dropped_after_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Window a, b, c, d with c failing: d is dropped even though it would
        # succeed, because head is a single high-water mark that cannot express a
        # gap (c missing, d kept). Re-fetching d next run keeps the totals exact.
        listing = [
            {"sha": "d", "url": "ud"},
            {"sha": "c", "url": "uc"},
            {"sha": "b", "url": "ub"},
            {"sha": "a", "url": "ua"},
        ]

        def fake(_self: github.Profile, url: str, *_: object) -> Any:
            if "/commits?" in url:
                return listing
            if url == "uc":
                raise github.GitHubError("rate limited")
            return {"sha": url[1:]}

        monkeypatch.setattr(github.Profile, "_fetch_json", fake)
        commits, _ = PROFILE.fetch_commits_since(CSSHW, None, concurrency=4)
        assert [c["sha"] for c in commits] == ["a", "b"]

    def test_fetch_commit_shas_collects_across_pages_without_fetching_details(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        full = [{"sha": str(i), "url": f"u{i}"} for i in range(github.PER_PAGE)]
        pages = {1: full, 2: [{"sha": "last", "url": "ulast"}]}
        fetched: list[str] = []

        def fake(_self: github.Profile, url: str, *_: object) -> Any:
            fetched.append(url)
            if "/commits?" in url:
                return pages.get(int(url.rpartition("page=")[2]), [])
            raise AssertionError("must not fetch a commit detail")

        monkeypatch.setattr(github.Profile, "_fetch_json", fake)
        shas = PROFILE.fetch_commit_shas(CSSHW)
        assert shas == {str(i) for i in range(github.PER_PAGE)} | {"last"}
        assert all("/commits?" in url for url in fetched)  # only listings

    def test_fetch_commit_shas_tolerates_an_inaccessible_repository(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_error(_self: github.Profile, *_: object) -> Any:
            raise github.GitHubError("no access")

        monkeypatch.setattr(github.Profile, "_fetch_json", raise_error)
        assert PROFILE.fetch_commit_shas(SECRET) == set()

    def test_skip_shas_drops_matching_refs_before_details_are_fetched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A fork's shared ancestor "b" is excluded, and its detail is never
        # fetched — only the successor's own tail costs a request.
        listing = [
            {"sha": "c", "url": "uc"},
            {"sha": "b", "url": "ub"},
            {"sha": "a", "url": "ua"},
        ]
        fetched: list[str] = []

        def fake(_self: github.Profile, url: str, *_: object) -> Any:
            fetched.append(url)
            return listing if "/commits?" in url else {"sha": url[1:]}

        monkeypatch.setattr(github.Profile, "_fetch_json", fake)
        commits, found = PROFILE.fetch_commits_since(
            github.Repo("whmade/cssh-rs", private=False), None, skip_shas={"b"}
        )
        assert [commit["sha"] for commit in commits] == ["a", "c"]  # oldest first
        assert "ub" not in fetched  # the skipped commit's detail is never fetched
        assert found is False

    def test_connection_pool_is_sized_for_the_concurrency_ceiling(self) -> None:
        # Without a matching pool size, concurrent workers would thrash
        # connections instead of reusing them.
        assert github._http.connection_pool_kw["maxsize"] >= github.MAX_CONCURRENCY


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


class TestRepo:
    def test_shows_a_public_name_but_only_the_key_of_a_private_one(self) -> None:
        assert str(CSSHW) == "whme/csshw"
        assert str(SECRET) == repo_key("whme/secret")

    def test_redacts_only_a_private_name_in_any_case(self) -> None:
        key = repo_key("whme/secret")
        assert SECRET.redact("/repos/WHME/Secret/x") == f"/repos/{key}/x"
        assert CSSHW.redact("/repos/whme/csshw/x") == "/repos/whme/csshw/x"


class TestPrivateRepoStaysOutOfLogs:
    """The workflow logs are public, so a private repo may appear only by key."""

    PRIVATE = github.Repo("whme/hidden-project", private=True)

    @pytest.mark.parametrize("failing", ["listing", "detail"])
    def test_no_log_line_names_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        failing: str,
    ) -> None:
        detail_url = f"{github.API}/repos/{self.PRIVATE.name}/commits/a"
        body = json.dumps([{"sha": "a", "url": detail_url}]).encode()

        def request(_method: str, url: str, **_kwargs: object) -> TestFetch._Response:
            ok = ("/commits?" in url) == (failing == "detail")
            return TestFetch._Response(200 if ok else 404, body)

        monkeypatch.setattr(github._http, "request", request)
        with caplog.at_level(logging.DEBUG):
            commits, _ = PROFILE.fetch_commits_since(self.PRIVATE, None)
            assert list(commits) == []
        assert repo_key(self.PRIVATE.name) in caplog.text
        assert "hidden-project" not in caplog.text

    def test_an_error_status_does_not_name_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            github._http, "request", lambda *_a, **_k: TestFetch._Response(404, b"{}")
        )
        with pytest.raises(github.GitHubError) as error:
            PROFILE._fetch_json(f"{github.API}/repos/whme/hidden-project", self.PRIVATE)
        assert str(error.value) == (
            f"GitHub returned 404 for {github.API}/repos/{self.PRIVATE!s}"
        )

    def test_an_unreachable_github_does_not_name_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = "/repos/whme/hidden-project/commits"

        def request(*_a: object, **_k: object) -> None:
            pool = urllib3.HTTPSConnectionPool("api.github.com")
            raise urllib3.exceptions.MaxRetryError(pool, path)

        monkeypatch.setattr(github._http, "request", request)
        with pytest.raises(ConnectionError) as error:
            PROFILE._fetch_json(f"{github.API}{path}", self.PRIVATE)
        # The suppressed urllib3 error, which names the path, stays out too.
        assert "hidden-project" not in "".join(format_exception(error.value))
        assert str(self.PRIVATE) in str(error.value)


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
