"""Tests for the optional Sentry error-reporting setup."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.utils import BadDsn

from readme_updater import monitoring

if TYPE_CHECKING:
    import pytest


def test_init_sentry_is_a_no_op_without_a_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(monitoring.DSN_ENV, raising=False)

    def fail(**_kwargs: Any) -> None:
        msg = "sentry_sdk.init must not be called without a DSN"
        raise AssertionError(msg)

    monkeypatch.setattr(sentry_sdk, "init", fail)
    assert monitoring.init_sentry() is False


def test_init_sentry_configures_sentry_when_a_dsn_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv(monitoring.DSN_ENV, "https://public@example.test/1")
    monkeypatch.setenv(monitoring.ENVIRONMENT_ENV, "github-actions")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: captured.update(kwargs))

    assert monitoring.init_sentry() is True
    assert captured["dsn"] == "https://public@example.test/1"
    assert captured["environment"] == "github-actions"
    assert captured["traces_sample_rate"] == 0.0


def test_init_sentry_reports_warnings_as_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv(monitoring.DSN_ENV, "https://public@example.test/1")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: captured.update(kwargs))

    assert monitoring.init_sentry() is True
    integration = next(
        integration
        for integration in captured["integrations"]
        if isinstance(integration, LoggingIntegration)
    )
    # WARNING and above (not just the SDK's default ERROR) become events, so a
    # run that degrades quietly still raises a notification.
    assert integration._handler.level == logging.WARNING


def test_init_sentry_defaults_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv(monitoring.DSN_ENV, "https://public@example.test/1")
    monkeypatch.delenv(monitoring.ENVIRONMENT_ENV, raising=False)
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: captured.update(kwargs))

    assert monitoring.init_sentry() is True
    assert captured["environment"] == monitoring.DEFAULT_ENVIRONMENT


def test_init_sentry_swallows_an_invalid_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(monitoring.DSN_ENV, "not-a-dsn")

    def raise_bad_dsn(**_kwargs: Any) -> None:
        raise BadDsn(monitoring.DSN_ENV)

    monkeypatch.setattr(sentry_sdk, "init", raise_bad_dsn)
    # A bad DSN is reported and swallowed, not raised, so the run continues.
    assert monitoring.init_sentry() is False
