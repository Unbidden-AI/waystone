"""Billing and API key management for Waystone hosted service.

Admin DB schema (separate from per-project graph DBs):
  api_keys  — key_hash, tier, email, org_id, stripe_customer_id, created_at, last_used, is_revoked
  usage_log — key_hash, project, action, input_chars, timestamp

Environment variables:
  CB_ADMIN_DB              — path to admin.db (default: ~/.waystone/admin.db)
  STRIPE_WEBHOOK_SECRET    — Stripe webhook endpoint signing secret
  STRIPE_PRO_PRICE_ID      — Stripe price ID for Pro monthly
  STRIPE_PRO_ANNUAL_PRICE_ID  — Stripe price ID for Pro annual
  STRIPE_TEAM_PRICE_ID     — Stripe price ID for Team monthly
  STRIPE_TEAM_ANNUAL_PRICE_ID — Stripe price ID for Team annual
  STRIPE_TEAM_LICENSE_PRICE_ID — price ID(s), comma-separated, for the SELF-HOSTED
                                 Team Server license (mints a signed token, not a key)
  WAYSTONE_LICENSE_PRIVKEY / _FILE — Ed25519 PRIVATE signing key (PEM / path) used to
                                 mint Team licenses on purchase (billing server only)
  RESEND_API_KEY           — Resend email API key (omit for dev/stdout mode)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

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
        "requests_per_minute": 100,
    },
    "team": {
        "requests_per_day": 50000,
        "requests_per_minute": 500,
    },
}

KEY_PREFIX = "waystone_"


# ---------------------------------------------------------------------------
# Admin DB
# ---------------------------------------------------------------------------

def get_admin_db_path() -> Path:
    custom = os.environ.get("CB_ADMIN_DB", "")
    if custom:
        return Path(custom)
    return Path("~/.waystone/admin.db").expanduser()


def open_admin_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_admin_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    init_admin_db(conn)
    return conn


def init_admin_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_keys (
            key_hash           TEXT PRIMARY KEY,
            tier               TEXT NOT NULL DEFAULT 'free',
            email              TEXT NOT NULL DEFAULT '',
            org_id             TEXT NOT NULL DEFAULT '',
            created_at         TEXT NOT NULL,
            last_used          TEXT,
            is_revoked         INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_api_keys_email ON api_keys(email);
""")
    # Migrations: add columns added after initial schema
    for col, definition in [
        ("raw_key", "TEXT"),
        ("stripe_customer_id", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE api_keys ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass  # Column already exists
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_stripe ON api_keys(stripe_customer_id)")
        conn.commit()
    except Exception:
        pass
    conn.executescript("""

        CREATE TABLE IF NOT EXISTS usage_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash    TEXT NOT NULL,
            project     TEXT NOT NULL DEFAULT '',
            action      TEXT NOT NULL,
            input_chars INTEGER NOT NULL DEFAULT 0,
            timestamp   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_key ON usage_log(key_hash);

        CREATE TABLE IF NOT EXISTS dead_letter_emails (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL,
            api_key     TEXT NOT NULL,
            tier        TEXT NOT NULL,
            created_at  REAL NOT NULL,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_attempt REAL
        );
        CREATE INDEX IF NOT EXISTS idx_dead_letter_email ON dead_letter_emails(email);
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


def create_key(
    conn: sqlite3.Connection,
    email: str,
    tier: str = "free",
    org_id: str = "",
    stripe_customer_id: str = "",
) -> str:
    """Insert a new API key row and return the raw key (caller must deliver it)."""
    if tier not in TIERS:
        raise ValueError(f"Unknown tier: {tier!r}")
    raw, key_hash = generate_api_key()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO api_keys (key_hash, raw_key, tier, email, org_id, stripe_customer_id, created_at, is_revoked) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
        (key_hash, raw, tier, email, org_id, stripe_customer_id, now),
    )
    conn.commit()
    return raw


def get_or_create_key_by_email(conn: sqlite3.Connection, email: str, tier: str = "free") -> str:
    """Return the active raw API key for email, creating a free-tier key if none exists.

    If the user has an existing key but it predates the raw_key column (no raw_key stored),
    a new key is issued and the old hash-only row is revoked.
    """
    row = conn.execute(
        "SELECT key_hash, raw_key, tier FROM api_keys WHERE email = ? AND is_revoked = 0 ORDER BY created_at DESC LIMIT 1",
        (email,),
    ).fetchone()
    if row and row["raw_key"]:
        return row["raw_key"]
    if row and not row["raw_key"]:
        # Pre-migration row — revoke it and issue a new key preserving the tier
        tier = row["tier"]
        conn.execute(
            "UPDATE api_keys SET is_revoked = 1 WHERE key_hash = ?",
            (row["key_hash"],),
        )
        conn.commit()
    return create_key(conn, email, tier=tier)


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


def count_active_members(conn: sqlite3.Connection) -> int:
    """Distinct active (non-revoked) member emails — a 'seat' is one email."""
    return conn.execute(
        "SELECT COUNT(DISTINCT email) FROM api_keys WHERE is_revoked = 0"
    ).fetchone()[0]


def list_active_members(conn: sqlite3.Connection) -> list[dict]:
    """Active members (one row per email, newest key's fields), for `team members`."""
    rows = conn.execute(
        "SELECT email, tier, MAX(created_at) AS created_at, MAX(last_used) AS last_used "
        "FROM api_keys WHERE is_revoked = 0 GROUP BY email ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


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

def send_key_email(email: str, raw_key: str, tier: str, admin_conn: sqlite3.Connection | None = None) -> None:
    """Send API key to customer via Resend. Falls back to stdout in dev mode.

    On delivery failure, inserts a row into the dead_letter_emails table for retry.

    Args:
        email: Customer email address
        raw_key: The raw API key to send
        tier: Tier name (free, pro, team)
        admin_conn: Optional AdminDB connection for dead-letter queueing. If not provided
                   and delivery fails, the key is lost (dev mode).
    """
    resend_key = os.environ.get("RESEND_API_KEY", "")
    tier_label = TIERS.get(tier, {}).get("label", tier.title())

    subject = f"Your Waystone {tier_label} API Key"
    body = (
        f"Hi,\n\n"
        f"Thanks for subscribing to Waystone {tier_label}!\n\n"
        f"Your API key:\n\n"
        f"  {raw_key}\n\n"
        f"Add it to your environment:\n\n"
        f"  export WAYSTONE_API_KEY={raw_key}\n\n"
        f"Or in your config:\n\n"
        f"  Authorization: Bearer {raw_key}\n\n"
        f"Keep this key secret — it cannot be recovered if lost. "
        f"Contact support to rotate it.\n\n"
        f"— Waystone Team\n"
    )

    if not resend_key:
        # Dev mode: print to stdout so tests/local runs can see the key
        log.info("[DEV] Would send email to %r: Subject: %s", email, subject)
        print(f"[DEV] Email to {email}\n{subject}\n\n{body}")
        return

    try:
        import httpx
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}"},
            json={
                "from": "Waystone <noreply@waystone.unbidden.ai>",
                "to": [email],
                "subject": subject,
                "text": body,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
    except Exception as exc:
        # Non-fatal: key was already created; queue for retry if we have a connection
        error_msg = str(exc)
        log.error("Email delivery failed for %r: %s", email, error_msg)
        print(f"[WARN] Email delivery failed for {email!r}: {error_msg}")

        if admin_conn:
            now = time.time()
            try:
                admin_conn.execute(
                    """INSERT INTO dead_letter_emails (email, api_key, tier, created_at, retry_count, last_attempt)
                       VALUES (?, ?, ?, ?, 0, ?)""",
                    (email, raw_key, tier, now, now),
                )
                admin_conn.commit()
                log.info("Queued email for retry: %r", email)
            except Exception as queue_exc:
                log.error("Failed to queue email for retry: %s", queue_exc)


# ---------------------------------------------------------------------------
# Dead-letter email retry
# ---------------------------------------------------------------------------

def retry_dead_letter_emails(admin_conn: sqlite3.Connection, max_retries: int = 3) -> int:
    """Retry sending emails from the dead-letter queue.

    Returns the number of successfully sent emails.
    """
    # Get all pending emails with retry_count < max_retries
    rows = admin_conn.execute(
        """SELECT id, email, api_key, tier FROM dead_letter_emails
           WHERE retry_count < ?
           ORDER BY created_at ASC""",
        (max_retries,),
    ).fetchall()

    success_count = 0
    for row in rows:
        email_id = row[0]
        email = row[1]
        raw_key = row[2]
        tier = row[3]

        try:
            # Try to send (without passing admin_conn to avoid infinite recursion)
            resend_key = os.environ.get("RESEND_API_KEY", "")
            if not resend_key:
                # Skip in dev mode
                continue

            import httpx
            tier_label = TIERS.get(tier, {}).get("label", tier.title())
            subject = f"Your Waystone {tier_label} API Key"
            body = (
                f"Hi,\n\n"
                f"Thanks for subscribing to Waystone {tier_label}!\n\n"
                f"Your API key:\n\n"
                f"  {raw_key}\n\n"
                f"Add it to your environment:\n\n"
                f"  export WAYSTONE_API_KEY={raw_key}\n\n"
                f"Or in your config:\n\n"
                f"  Authorization: Bearer {raw_key}\n\n"
                f"Keep this key secret — it cannot be recovered if lost. "
                f"Contact support to rotate it.\n\n"
                f"— Waystone Team\n"
            )

            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}"},
                json={
                    "from": "Waystone <noreply@waystone.unbidden.ai>",
                    "to": [email],
                    "subject": subject,
                    "text": body,
                },
                timeout=10.0,
            )
            resp.raise_for_status()

            # Success: remove from dead-letter queue
            admin_conn.execute("DELETE FROM dead_letter_emails WHERE id = ?", (email_id,))
            admin_conn.commit()
            success_count += 1
            log.info("Email retry succeeded for %r", email)

        except Exception as exc:
            # Update retry count and last_attempt
            now = time.time()
            admin_conn.execute(
                """UPDATE dead_letter_emails
                   SET retry_count = retry_count + 1, last_attempt = ?
                   WHERE id = ?""",
                (now, email_id),
            )
            admin_conn.commit()
            log.error("Email retry failed for %r: %s", email, exc)

    return success_count


# ---------------------------------------------------------------------------
# Stripe webhook helpers
# ---------------------------------------------------------------------------

def verify_stripe_signature(body: bytes, sig_header: str) -> bool:
    """Verify Stripe-Signature header from Stripe webhook.

    Stripe signs webhooks with HMAC-SHA256 of "{timestamp}.{raw_body}".
    The header format is: t=<timestamp>,v1=<hex_sig>[,v1=<hex_sig>...]
    """
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        return False

    timestamp = ""
    v1_sigs: list[str] = []
    for part in sig_header.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k == "t":
            timestamp = v.strip()
        elif k == "v1":
            v1_sigs.append(v.strip())

    if not timestamp or not v1_sigs:
        return False

    signed_payload = timestamp.encode() + b"." + body
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in v1_sigs)


def tier_from_price(price_id: str) -> str:
    """Map a Stripe price ID to a tier name.

    Checks both monthly and annual price IDs for each tier:
      STRIPE_PRO_PRICE_ID          — Pro monthly
      STRIPE_PRO_ANNUAL_PRICE_ID   — Pro annual
      STRIPE_TEAM_PRICE_ID         — Team monthly
      STRIPE_TEAM_ANNUAL_PRICE_ID  — Team annual
    """
    team_ids = {
        os.environ.get("STRIPE_TEAM_PRICE_ID", ""),
        os.environ.get("STRIPE_TEAM_ANNUAL_PRICE_ID", ""),
    } - {""}
    if price_id in team_ids:
        return "team"
    pro_ids = {
        os.environ.get("STRIPE_PRO_PRICE_ID", ""),
        os.environ.get("STRIPE_PRO_ANNUAL_PRICE_ID", ""),
    } - {""}
    if price_id in pro_ids:
        return "pro"
    return "free"


def is_team_license_price(price_id: str) -> bool:
    """True if this Stripe price is a SELF-HOSTED Team Server license (not the hosted
    Team API tier). Set ``STRIPE_TEAM_LICENSE_PRICE_ID`` (comma-separated for multiple
    seat-count tiers)."""
    if not price_id:
        return False
    ids = {p.strip() for p in os.environ.get("STRIPE_TEAM_LICENSE_PRICE_ID", "").split(",")} - {""}
    return price_id in ids


def send_license_email(email: str, token: str, seats: int,
                       expires_at: str | None = None,
                       admin_conn: sqlite3.Connection | None = None) -> None:
    """Email a self-hosted Team Server license token to the customer (Resend; stdout
    in dev). Mirrors send_key_email's delivery + dead-letter handling."""
    subject = f"Your Waystone Team Server license ({seats} seats)"
    exp_line = f"Valid until: {expires_at[:10]}\n\n" if expires_at else ""
    body = (
        "Hi,\n\n"
        f"Thanks for purchasing a Waystone Team Server license — {seats} seats.\n\n"
        f"{exp_line}"
        "Set this token on your server, then `docker compose up`:\n\n"
        f"  WAYSTONE_LICENSE={token}\n\n"
        "Issue a key per teammate with:  waystone team issue <email>\n"
        "Full setup: https://unbidden.ai/docs/team-server/\n\n"
        "The license is verified offline on your own server — no phone-home.\n"
        "Keep the token safe; contact support to re-issue.\n\n"
        "— Waystone Team\n"
    )

    resend_key = os.environ.get("RESEND_API_KEY", "")
    if not resend_key:
        log.info("[DEV] Would send license email to %r: %s", email, subject)
        print(f"[DEV] License email to {email}\n{subject}\n\n{body}")
        return
    try:
        import httpx
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}"},
            json={
                "from": "Waystone <noreply@waystone.unbidden.ai>",
                "to": [email],
                "subject": subject,
                "text": body,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.error("License email delivery failed for %r: %s", email, exc)
        print(f"[WARN] License email delivery failed for {email!r}: {exc}")
        if admin_conn is not None:
            try:
                admin_conn.execute(
                    """INSERT INTO dead_letter_emails (email, api_key, tier, created_at, retry_count, last_attempt)
                       VALUES (?, ?, ?, ?, 0, ?)""",
                    (email, f"LICENSE:{token}", "team_license", time.time(), time.time()),
                )
                admin_conn.commit()
            except Exception:
                pass


def revoke_key_by_stripe_customer(conn: sqlite3.Connection, stripe_customer_id: str) -> int:
    """Revoke all active keys for a Stripe customer ID (subscription deleted). Returns count."""
    cur = conn.execute(
        "UPDATE api_keys SET is_revoked = 1 WHERE stripe_customer_id = ? AND is_revoked = 0",
        (stripe_customer_id,),
    )
    conn.commit()
    return cur.rowcount


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
