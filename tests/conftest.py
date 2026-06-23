"""Shared pytest fixtures + hermetic environment.

CI runs with a clean environment; a developer's shell often has app-config env
vars set (Stripe keys, an API key, a license). When a test's control flow keys
off one of those WITHOUT setting it explicitly, it passes locally and silently
fails in CI — e.g. `_hosted_saas()` reads STRIPE_WEBHOOK_SECRET, so a test that
assumed hosted-SaaS mode passed locally (secret present) and failed in CI
(absent). That divergence is invisible until someone reads the CI logs.

The autouse fixture below clears the mode-determining env vars before every
test, so local == CI by default. A test that needs one set does so explicitly
via `monkeypatch.setenv(...)` — making the dependency visible instead of ambient.
"""

import pytest

# Env vars that change application CONTROL FLOW (not just credentials). Leaving
# any of these to ambient shell state is what causes local-vs-CI drift.
_MODE_ENV_VARS = (
    "STRIPE_WEBHOOK_SECRET",   # -> _hosted_saas(): hosted-SaaS vs self-hosted mode
    "STRIPE_PRO_PRICE_ID",
    "STRIPE_TEAM_PRICE_ID",
    "WAYSTONE_API_KEY",        # -> whether the API requires auth
    "WAYSTONE_LICENSE",        # -> per-seat license mode
    "CB_ADMIN_DB",             # -> admin DB location
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Strip mode-determining env vars so tests run as they would in clean CI.

    Tests that need a specific mode set it explicitly with monkeypatch.setenv,
    which is restored automatically at teardown.
    """
    for var in _MODE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
