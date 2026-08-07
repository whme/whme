"""Shared test fixtures."""

from __future__ import annotations

import pytest

from readme_updater import markup


@pytest.fixture(autouse=True)
def _relative_asset_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep asset srcs repository-relative unless a test configures a base.

    The base URL is process-wide module state; resetting it before each test
    stops a test that configures it (or invokes the CLI, which does) from
    leaking absolute URLs into the many tests that assert the relative form.
    """
    monkeypatch.setattr(markup, "_asset_base_url", [None])


@pytest.fixture(autouse=True)
def _github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hand every test a token so the CLI never shells out to `gh` or exits.

    The CLI refuses to run unauthenticated, so tests that invoke it need a
    token. Supplying it through the environment keeps them from depending on
    the developer's own `gh` login; a test that exercises the missing-token
    path deletes it again.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
