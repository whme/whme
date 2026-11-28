"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hand every test a token so the CLI never shells out to `gh` or exits.

    The CLI refuses to run unauthenticated, so tests that invoke it need a
    token. Supplying it through the environment keeps them from depending on
    the developer's own `gh` login; a test that exercises the missing-token
    path deletes it again.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
