"""Sentry error monitoring initialization."""

from __future__ import annotations

import os


def init_sentry(dsn: str | None = None) -> bool:
    """Initialize Sentry error monitoring.

    Args:
        dsn: Sentry DSN (uses SENTRY_DSN env var if not provided)

    Returns:
        bool: True if Sentry was initialized, False otherwise
    """
    sentry_dsn = dsn or os.environ.get("SENTRY_DSN")
    if not sentry_dsn:
        return False

    try:
        import sentry_sdk
        integrations: list = []
        try:
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.starlette import StarletteIntegration
            integrations = [StarletteIntegration(), FastApiIntegration()]
        except ImportError:
            pass
        sentry_sdk.init(
            dsn=sentry_dsn,
            traces_sample_rate=0.1,
            send_default_pii=False,
            integrations=integrations,
        )
        return True
    except ImportError:
        return False
