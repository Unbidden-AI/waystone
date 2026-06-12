"""Offline, signed license verification for the self-hosted Team Server.

A license is an Ed25519-signed JWT carrying a seat count + (optional) expiry. The
server verifies it LOCALLY against a bundled public key — no phone-home — which is
the whole point of the self-hosted privacy pitch. Unbidden issues licenses with the
matching private key (kept off-repo).

  token  = issue_license(private_pem, seats=10, org="acme", expires_at=...)
  lic    = verify_license(token)          # raises LicenseError if invalid/expired
  lic    = load_license()                 # from WAYSTONE_LICENSE / *_FILE env, or None

Seat enforcement lives in the `waystone team` CLI: members are issued keys in the
admin DB up to `lic.seats`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Ed25519 public key for verifying license tokens. The matching private key is held
# by Unbidden (never in the repo); see scripts/issue_license.py for issuance.
LICENSE_PUBLIC_KEY = """\
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEACLDqYB1wx8oL++WN5YsMtul0F3FsVHtj6uDqMkS2IZ0=
-----END PUBLIC KEY-----
"""

_ALG = "EdDSA"
_ISS = "waystone"

# Seats available without a license — enough to try team mode before buying.
TRIAL_SEATS = 3


def seats_for(license: "License | None") -> int:
    """Resolve the seat entitlement: the license's seats, or the trial allowance."""
    return license.seats if license is not None else TRIAL_SEATS


class LicenseError(Exception):
    """Raised when a license token is missing required claims, fails signature
    verification, or has expired."""


@dataclass(frozen=True)
class License:
    seats: int
    org: str = ""
    plan: str = "team"
    expires_at: str | None = None  # ISO-8601, or None for perpetual

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return True
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp < datetime.now(timezone.utc)


def _require_jwt():
    try:
        import jwt  # PyJWT[crypto]
    except ImportError as e:  # pragma: no cover - depends on install extras
        raise LicenseError(
            "License verification needs PyJWT[crypto] — install waystone[api]."
        ) from e
    return jwt


def verify_license(token: str, public_key: str | None = None) -> License:
    """Verify a license token's signature + expiry and return the License.

    Raises LicenseError on a bad signature, expiry, or a missing ``seats`` claim.
    """
    jwt = _require_jwt()
    key = public_key or LICENSE_PUBLIC_KEY
    try:
        claims = jwt.decode(
            token, key, algorithms=[_ALG],
            # Pinning algorithms=[EdDSA] already rejects alg=none; verify_signature
            # is made explicit as defense-in-depth against library default drift.
            options={"require": ["seats"], "verify_aud": False, "verify_signature": True},
        )
    except jwt.ExpiredSignatureError as e:
        raise LicenseError("License has expired.") from e
    except Exception as e:  # InvalidSignature, DecodeError, etc.
        raise LicenseError(f"Invalid license: {e}") from e

    try:
        seats = int(claims["seats"])
    except (KeyError, TypeError, ValueError) as e:
        raise LicenseError("License is missing a valid 'seats' claim.") from e
    if seats < 0:
        raise LicenseError("License 'seats' must be non-negative.")

    expires_at = None
    if "exp" in claims:
        expires_at = datetime.fromtimestamp(
            int(claims["exp"]), tz=timezone.utc).isoformat()

    return License(
        seats=seats,
        org=str(claims.get("org", "")),
        plan=str(claims.get("plan", "team")),
        expires_at=expires_at,
    )


def load_license() -> License | None:
    """Load + verify the active license from the environment, or None if unlicensed.

    Source order: ``WAYSTONE_LICENSE`` (the token), then the file at
    ``WAYSTONE_LICENSE_FILE``. Returns None when neither is set. Raises LicenseError
    if a token IS provided but fails verification (fail-closed: a tampered license
    must not silently grant seats).
    """
    token = os.environ.get("WAYSTONE_LICENSE", "").strip()
    if not token:
        path = os.environ.get("WAYSTONE_LICENSE_FILE", "").strip()
        if path and Path(path).exists():
            token = Path(path).read_text(encoding="utf-8").strip()
    if not token:
        return None
    return verify_license(token)


def issue_license(
    private_key: str,
    *,
    seats: int,
    org: str = "",
    plan: str = "team",
    issued_at: datetime,
    expires_at: datetime | None = None,
) -> str:
    """Mint a signed license token (Unbidden-side / tests). ``issued_at`` is passed
    explicitly so callers control the clock."""
    jwt = _require_jwt()
    claims: dict = {
        "seats": int(seats),
        "org": org,
        "plan": plan,
        "iss": _ISS,
        "iat": int(issued_at.timestamp()),
    }
    if expires_at is not None:
        claims["exp"] = int(expires_at.timestamp())
    return jwt.encode(claims, private_key, algorithm=_ALG)
