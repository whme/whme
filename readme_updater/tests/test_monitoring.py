"""Tests for the optional Sentry error-reporting setup."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.utils import BadDsn

from readme_updater import languages, monitoring

if TYPE_CHECKING:
    import pytest
    from sentry_sdk.types import Event


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


def test_init_sentry_reports_warnings_as_events_with_info_breadcrumbs(
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
    # _handler drives events (WARNING and above, not just the SDK's default
    # ERROR); _breadcrumb_handler drives breadcrumbs (INFO for context).
    assert integration._handler.level == logging.WARNING
    assert integration._breadcrumb_handler.level == logging.INFO


def _record_with(**extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        "readme_updater.example", logging.WARNING, __file__, 0, "boom", None, None
    )
    record.__dict__.update(extra)
    return record


def test_apply_declared_fingerprint_uses_the_declared_value() -> None:
    record = _record_with(**{monitoring.FINGERPRINT_LOG_KEY: ["no-icon", "HTML"]})
    event = monitoring._apply_declared_fingerprint({}, {"log_record": record})
    assert event["fingerprint"] == ["no-icon", "HTML"]


def test_apply_declared_fingerprint_strips_the_key_from_extra() -> None:
    record = _record_with(**{monitoring.FINGERPRINT_LOG_KEY: ["no-icon", "HTML"]})
    event: Event = {"extra": {monitoring.FINGERPRINT_LOG_KEY: ["x"], "a": 1}}
    monitoring._apply_declared_fingerprint(event, {"log_record": record})
    assert event["extra"] == {"a": 1}


def test_apply_declared_fingerprint_leaves_other_events_alone() -> None:
    event: Event = {}
    hint = {"log_record": _record_with()}
    result = monitoring._apply_declared_fingerprint(event, hint)
    assert "fingerprint" not in result


def test_the_no_icon_warning_declares_a_per_language_fingerprint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Guards the cross-module contract end to end: languages.py declares the
    # fingerprint the hook consumes. Go clears MIN_SHARE but has no icon.
    with caplog.at_level(logging.WARNING, logger=languages.__name__):
        languages.language_shares({"Rust": 900, "Go": 100})
    (record,) = [
        record
        for record in caplog.records
        if hasattr(record, monitoring.FINGERPRINT_LOG_KEY)
    ]
    event = monitoring._apply_declared_fingerprint({}, {"log_record": record})
    assert event["fingerprint"] == ["no-icon-for-language", "Go"]


def test_init_sentry_registers_the_fingerprint_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv(monitoring.DSN_ENV, "https://public@example.test/1")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: captured.update(kwargs))

    assert monitoring.init_sentry() is True
    assert captured["before_send"] is monitoring._apply_declared_fingerprint


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
