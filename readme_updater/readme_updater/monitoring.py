"""Optional Sentry error reporting for scheduled README updates.

The updater runs unattended on a schedule, so a failure would otherwise pass
unnoticed until the README quietly went stale. When a ``SENTRY_DSN`` is
configured, Sentry reports unhandled exceptions and every ``WARNING`` and above
as events, flushing them before the process exits. Warnings become events too
because the updater degrades quietly: a 403 that falls back to public listings
leaves a run "successful" but wrong, so it must raise its own notification.
``INFO`` records ride along as breadcrumbs for context on those events. Without
a DSN this is a no-op, so local runs and forks need no Sentry account.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.utils import BadDsn

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

logger = logging.getLogger(__name__)

DSN_ENV = "SENTRY_DSN"
ENVIRONMENT_ENV = "SENTRY_ENVIRONMENT"
DEFAULT_ENVIRONMENT = "production"

FINGERPRINT_LOG_KEY = "sentry_fingerprint"
"""``logging`` ``extra`` key a log call sets to pick its own Sentry issue.

Sentry groups events by message template, collapsing a parameterized warning
into one issue. Setting ``extra={FINGERPRINT_LOG_KEY: [...]}`` makes that list
the event's
`fingerprint <https://docs.sentry.io/platforms/python/usage/sdk-fingerprinting/>`_
instead, so a call site controls grouping without importing ``sentry_sdk``.
"""


def _apply_declared_fingerprint(event: Event, hint: Hint) -> Event:
    """Apply a fingerprint a log call declared via ``FINGERPRINT_LOG_KEY``.

    The list is used verbatim as the event fingerprint, so the call site fully
    controls grouping; events without the key keep Sentry's default. The key is
    stripped from the event's extra so it does not linger beside the grouping it
    drives.
    """
    record = hint.get("log_record")
    fingerprint = getattr(record, FINGERPRINT_LOG_KEY, None)
    if isinstance(fingerprint, list):
        event["fingerprint"] = fingerprint
        extra = event.get("extra")
        if isinstance(extra, dict):
            extra.pop(FINGERPRINT_LOG_KEY, None)
    return event


def init_sentry() -> bool:
    """Enable Sentry error reporting when a DSN is configured.

    Reads the DSN from ``SENTRY_DSN``; when it is unset or empty, error
    reporting stays off and this is a no-op. Unhandled exceptions and every
    ``WARNING`` and above are reported as events, while ``INFO`` records are
    captured as breadcrumbs for context on those events. A deliberate
    ``SystemExit`` (such as the missing-token exit) never reaches
    ``sys.excepthook``, so it raises no false alarm.

    A misconfigured DSN must not take the whole run down with it, so an invalid
    DSN is logged and swallowed rather than raised.

    Returns:
      Whether Sentry error reporting was initialized.
    """
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        logger.warning(
            "%(env)s unset; Sentry error reporting disabled", {"env": DSN_ENV}
        )
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            # Only unhandled errors matter here; there is no throughput to trace.
            traces_sample_rate=0.0,
            environment=os.environ.get(ENVIRONMENT_ENV, DEFAULT_ENVIRONMENT),
            # Default event_level is ERROR; report WARNING and above too. INFO
            # records stay breadcrumbs for context on those events.
            integrations=[
                LoggingIntegration(level=logging.INFO, event_level=logging.WARNING)
            ],
            before_send=_apply_declared_fingerprint,
        )
    except BadDsn:
        logger.exception(
            "invalid %(env)s; Sentry error reporting disabled", {"env": DSN_ENV}
        )
        return False
    logger.info("Sentry error reporting enabled")
    return True
