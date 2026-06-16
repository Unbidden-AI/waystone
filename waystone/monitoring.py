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
        try:
            from . import __version__ as _ver
        except Exception:
            _ver = "unknown"
        sentry_sdk.init(
            dsn=sentry_dsn,
            traces_sample_rate=0.1,
            send_default_pii=False,
            integrations=integrations,
            # Release + environment make the dashboard navigable: which version
            # introduced a regression, and prod vs staging vs a dev box.
            release=f"waystone@{_ver}",
            environment=os.environ.get("WAYSTONE_ENV", "production"),
        )
        return True
    except ImportError:
        return False


def capture_exception(exc: BaseException) -> None:
    """Send an exception to Sentry if it's initialized; a no-op (never raises)
    otherwise. Lets call sites capture explicitly without importing sentry_sdk or
    caring whether monitoring is configured."""
    try:
        import sentry_sdk

        if sentry_sdk.Hub.current.client is not None:
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass
