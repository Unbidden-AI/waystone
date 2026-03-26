"""Billing and API key management for Engram hosted service.

Admin DB schema (separate from per-project graph DBs):
  api_keys  — key_hash, tier, email, org_id, created_at, last_used, is_revoked
  usage_log — key_hash, project, action, input_chars, timestamp

Environment variables:
  CB_ADMIN_DB       — path to admin.db (default: ~/.context-broker/admin.db)
  LS_WEBHOOK_SECRET — LemonSqueezy webhook signing secret
  LS_PRO_VARIANT_ID — LemonSqueezy variant ID for Pro plan
  LS_TEAM_VARIANT_ID — LemonSqueezy variant ID for Team plan
  RESEND_API_KEY    — Resend email API key (omit for dev/stdout mode)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

TIERS: dict[str, dict] = {
    "free": {
        "max_projects": 1,
        "max_nodes": 500,
        "rate_limit": 10,      # requests/min
        "label": "Free",
    },
    "pro": {
        "max_projects": None,  # unlimited
        "max_nodes": 25_000,
        "rate_limit": 100,
        "label": "Pro",
    },
    "team": {
        "max_projects": None,
        "max_nodes": 250_000,
        "rate_limit": 500,
        "label": "Team",
    },
}

RATE_LIMITS: dict[str, dict] = {
    "free": {
        "requests_per_day": 100,
        "requests_per_minute": 10,
    },
    "pro": {
        "requests_per_day": 5000,
        "requests_per_minute": 60,
    },
    "team": {
        "requests_per_day": 50000,
        "requests_per_minute": 300,
    },
}

KEY_PREFIX = "engram_"


# ---------------------------------------------------------------------------
# Admin DB
# ---------------------------------------------------------------------------

def get_admin_db_path() -> Path:
    custom = os.environ.get("CB_ADMIN_DB", "")
    if custom:
        return Path(custom)
    return Path("~/.context-broker/admin.db").expanduser()


def open_admin_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_admin_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    init_admin_db(conn)
    return conn


def init_admin_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_keys (
            key_hash   TEXT PRIMARY KEY,
            tier       TEXT NOT NULL DEFAULT 'free',
            email      TEXT NOT NULL DEFAULT '',
            org_id     TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            last_used  TEXT,
            is_revoked INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_api_keys_email ON api_keys(email);

        CREATE TABLE IF NOT EXISTS usage_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash    TEXT NOT NULL,
            project     TEXT NOT NULL DEFAULT '',
            action      TEXT NOT NULL,
            input_chars INTEGER NOT NULL DEFAULT 0,
            timestamp   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_key ON usage_log(key_hash);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Key generation and validation
# ---------------------------------------------------------------------------

def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Return (raw_key, key_hash). Only the hash is stored."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, _hash_key(raw)


def create_key(conn: sqlite3.Connection, email: str, tier: str = "free", org_id: str = "") -> str:
    """Insert a new API key row and return the raw key (caller must deliver it)."""
    if tier not in TIERS:
        raise ValueError(f"Unknown tier: {tier!r}")
    raw, key_hash = generate_api_key()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO api_keys (key_hash, tier, email, org_id, created_at, is_revoked) VALUES (?, ?, ?, ?, ?, 0)",
        (key_hash, tier, email, org_id, now),
    )
    conn.commit()
    return raw


def validate_key(conn: sqlite3.Connection, raw_key: str) -> dict | None:
    """Return key row as dict if valid and active, else None. Updates last_used."""
    key_hash = _hash_key(raw_key)
    row = conn.execute(
        "SELECT * FROM api_keys WHERE key_hash = ? AND is_revoked = 0",
        (key_hash,),
    ).fetchone()
    if row is None:
        return None
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE api_keys SET last_used = ? WHERE key_hash = ?", (now, key_hash))
    conn.commit()
    return dict(row)


def revoke_key(conn: sqlite3.Connection, raw_key: str) -> bool:
    """Revoke by raw key. Returns True if a row was updated."""
    key_hash = _hash_key(raw_key)
    cur = conn.execute(
        "UPDATE api_keys SET is_revoked = 1 WHERE key_hash = ? AND is_revoked = 0",
        (key_hash,),
    )
    conn.commit()
    return cur.rowcount > 0


def revoke_key_by_email(conn: sqlite3.Connection, email: str) -> int:
    """Revoke all active keys for an email (subscription cancellation). Returns count."""
    cur = conn.execute(
        "UPDATE api_keys SET is_revoked = 1 WHERE email = ? AND is_revoked = 0",
        (email,),
    )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Tier enforcement
# ---------------------------------------------------------------------------

def check_node_limit(tier: str, current_node_count: int) -> None:
    """Raise ValueError if adding more nodes would exceed the tier limit."""
    limit = TIERS.get(tier, TIERS["free"])["max_nodes"]
    if limit is not None and current_node_count >= limit:
        raise ValueError(
            f"Node limit reached for {tier!r} tier ({limit:,} nodes). "
            "Upgrade your plan to continue extracting."
        )


def check_project_limit(conn: sqlite3.Connection, key_hash: str, tier: str, current_project_count: int) -> None:
    """Raise ValueError if the tier's project limit would be exceeded."""
    limit = TIERS.get(tier, TIERS["free"])["max_projects"]
    if limit is not None and current_project_count >= limit:
        raise ValueError(
            f"Project limit reached for {tier!r} tier ({limit} project). "
            "Upgrade your plan to create more projects."
        )


# ---------------------------------------------------------------------------
# Usage logging
# ---------------------------------------------------------------------------

def log_usage(
    conn: sqlite3.Connection,
    key_hash: str,
    project: str,
    action: str,
    input_chars: int = 0,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO usage_log (key_hash, project, action, input_chars, timestamp) VALUES (?, ?, ?, ?, ?)",
        (key_hash, project, action, input_chars, now),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Email delivery (Resend)
# ---------------------------------------------------------------------------

def send_key_email(email: str, raw_key: str, tier: str) -> None:
    """Send API key to customer via Resend. Falls back to stdout in dev mode."""
    resend_key = os.environ.get("RESEND_API_KEY", "")
    tier_label = TIERS.get(tier, {}).get("label", tier.title())

    subject = f"Your Engram {tier_label} API Key"
    body = (
        f"Hi,\n\n"
        f"Thanks for subscribing to Engram {tier_label}!\n\n"
        f"Your API key:\n\n"
        f"  {raw_key}\n\n"
        f"Add it to your environment:\n\n"
        f"  export CB_API_KEY={raw_key}\n\n"
        f"Or in your config:\n\n"
        f"  Authorization: Bearer {raw_key}\n\n"
        f"Keep this key secret — it cannot be recovered if lost. "
        f"Contact support to rotate it.\n\n"
        f"— Engram Team\n"
    )

    if not resend_key:
        # Dev mode: print to stdout so tests/local runs can see the key
        print(f"[DEV] Would send email to {email!r}:\nSubject: {subject}\n{body}")
        return

    try:
        import httpx
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}"},
            json={
                "from": "Engram <noreply@contextbroker.ai>",
                "to": [email],
                "subject": subject,
                "text": body,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
    except Exception as exc:
        # Non-fatal: key was already created; log and continue
        print(f"[billing] Email delivery failed for {email!r}: {exc}")


# ---------------------------------------------------------------------------
# LemonSqueezy webhook helpers
# ---------------------------------------------------------------------------

def verify_ls_signature(body: bytes, signature: str) -> bool:
    """Verify X-Signature header from LemonSqueezy webhook."""
    secret = os.environ.get("LS_WEBHOOK_SECRET", "")
    if not secret:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def tier_from_variant(variant_id: str) -> str:
    """Map a LemonSqueezy variant ID to a tier name."""
    if variant_id == os.environ.get("LS_TEAM_VARIANT_ID", ""):
        return "team"
    if variant_id == os.environ.get("LS_PRO_VARIANT_ID", ""):
        return "pro"
    return "free"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class RateLimiter:
    """In-memory sliding window rate limiter per API key.

    Tracks timestamps for requests in two windows: per-minute and per-day.
    Thread-safe using a lock.

    Returns (allowed: bool, reason: str) from check().
    reason is empty string if allowed.
    """

    def __init__(self):
        self._minute: dict[str, deque] = {}  # key -> deque of timestamps
        self._day: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, api_key: str, tier: str) -> tuple[bool, str, int, int]:
        """Check if a request is allowed.

        Args:
            api_key: API key to check.
            tier: Tier name (free, pro, team).

        Returns:
            (allowed, reason, remaining_minute, remaining_day)
            remaining_* are the counts AFTER this request is recorded (if allowed).
            If not allowed, remaining_* are the current counts (at limit).
        """
        limits = RATE_LIMITS.get(tier, RATE_LIMITS["free"])
        now = time.time()

        with self._lock:
            # Initialize deques if needed
            if api_key not in self._minute:
                self._minute[api_key] = deque()
            if api_key not in self._day:
                self._day[api_key] = deque()

            minute_window = self._minute[api_key]
            day_window = self._day[api_key]

            # Prune old timestamps (older than 60 sec for minute, 86400 sec for day)
            while minute_window and now - minute_window[0] > 60:
                minute_window.popleft()
            while day_window and now - day_window[0] > 86400:
                day_window.popleft()

            # Check limits
            minute_limit = limits["requests_per_minute"]
            day_limit = limits["requests_per_day"]

            if len(minute_window) >= minute_limit:
                day_remaining = day_limit - len(day_window)
                return (
                    False,
                    f"Rate limit exceeded: {minute_limit} requests/minute",
                    0,
                    day_remaining,
                )
            if len(day_window) >= day_limit:
                minute_remaining = minute_limit - len(minute_window)
                return (
                    False,
                    f"Rate limit exceeded: {day_limit} requests/day",
                    minute_remaining,
                    0,
                )

            # Record this request
            minute_window.append(now)
            day_window.append(now)

            # Return remaining counts AFTER recording
            minute_remaining = minute_limit - len(minute_window)
            day_remaining = day_limit - len(day_window)
            return True, "", minute_remaining, day_remaining
