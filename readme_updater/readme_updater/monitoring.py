"""Optional Sentry error reporting for scheduled README updates.

The updater runs unattended on a schedule, so a failure would otherwise pass
unnoticed until the README quietly went stale. When a ``SENTRY_DSN`` is
configured, Sentry's default integrations report any unhandled exception
through ``sys.excepthook`` and flush it before the process exits, which is what
sends the notification. Without a DSN this is a no-op, so local runs and forks
need no Sentry account.
"""

from __future__ import annotations

import logging
import os

import sentry_sdk
from sentry_sdk.utils import BadDsn

logger = logging.getLogger(__name__)

DSN_ENV = "SENTRY_DSN"
ENVIRONMENT_ENV = "SENTRY_ENVIRONMENT"
DEFAULT_ENVIRONMENT = "production"


def init_sentry() -> bool:
    """Enable Sentry error reporting when a DSN is configured.

    Reads the DSN from ``SENTRY_DSN``; when it is unset or empty, error
    reporting stays off and this is a no-op. Only unhandled exceptions are
    reported: a deliberate ``SystemExit`` (such as the missing-token exit) never
    reaches ``sys.excepthook``, so it raises no false alarm.

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
        )
    except BadDsn:
        logger.exception(
            "invalid %(env)s; Sentry error reporting disabled", {"env": DSN_ENV}
        )
        return False
    logger.info("Sentry error reporting enabled")
    return True
