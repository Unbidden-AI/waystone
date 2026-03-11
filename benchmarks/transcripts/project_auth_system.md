# Project: Authentication & Authorization System Design
# Participants: Taylor (security lead), Drew (backend), Avery (platform)

---

**Taylor**: Let's design the auth system from scratch. We have a chance to do this right. First priority: what's our threat model?

**Drew**: We're primarily worried about credential stuffing, session hijacking, and insider threats. MITM is handled by TLS everywhere.

**Taylor**: Good. For the primary auth mechanism — are we building our own or using an identity provider?

**Avery**: I'd strongly recommend an IdP. Auth is too easy to get wrong. We have Auth0 and Okta on the shortlist.

**Taylor**: We're going with Keycloak. Self-hosted. Compliance requires data residency in our own infrastructure — both Auth0 and Okta store user data outside our control and that's a non-starter for our enterprise customers.

**Drew**: Understood. Keycloak it is. What's the token format?

**Taylor**: JWT, OIDC-compliant. Access tokens, refresh tokens, and ID tokens — standard OIDC flow.

**Avery**: Token lifetimes?

**Taylor**: Access tokens: 15 minutes. Refresh tokens: 24 hours with sliding window — each use extends by 24 hours up to a maximum of 30 days of continuous activity. After 30 days of inactivity, full re-auth required.

**Drew**: What about the signing algorithm?

**Taylor**: RS256 for access and ID tokens. The private key stays in Keycloak, never leaves. Refresh tokens use AES-256 encryption, not JWT format — they're opaque tokens stored in our database.

**Avery**: Why opaque refresh tokens?

**Taylor**: Because opaque tokens can be instantly revoked. JWT refresh tokens can't be revoked without maintaining a denylist. Security wins over convenience.

**Drew**: Speaking of revocation — what's the revocation strategy for access tokens?

**Taylor**: Short expiry handles most cases. For immediate revocation (user reports compromise), we maintain a Redis-backed denylist with TTL matching the token expiry. Check on every request — Redis lookup is sub-millisecond.

**Avery**: What's the MFA strategy?

**Taylor**: TOTP as the primary second factor. WebAuthn/FIDO2 as the preferred option — we'll push users toward hardware keys or passkeys. SMS OTP is explicitly not supported — SIM swapping makes it too weak for our threat model.

**Drew**: What about recovery codes for when users lose their MFA device?

**Taylor**: 10 single-use recovery codes generated at MFA enrollment. Stored as bcrypt hashes on our side. Shown to user once, never again. Users can regenerate them but it triggers a security alert email.

**Avery**: Password policy?

**Taylor**: Minimum 12 characters. Must include uppercase, lowercase, digit, and special character. Check against HaveIBeenPwned API on registration and password change. Block any password in the top 10,000 common passwords list. No maximum length — we're not the password police.

**Drew**: Are we enforcing password rotation?

**Taylor**: No mandatory rotation for regular users. Research shows forced rotation leads to weaker passwords. We DO enforce rotation if we detect the password in a breach via HIBP monitoring.

**Avery**: What's the lockout policy for failed attempts?

**Taylor**: Progressive lockout. 5 failed attempts: 5-minute lockout. 10 failed attempts: 1-hour lockout. 20 failed attempts in 24 hours: account locked, requires admin or email verification to unlock. All failed attempts trigger a rate limit at the IP level too.

**Drew**: RBAC model?

**Taylor**: Attribute-based access control, not just RBAC. ABAC gives us finer-grained control. We'll use Open Policy Agent (OPA) as the policy engine.

**Avery**: Initially I thought we'd go with a simpler role-based system. What changed?

**Taylor**: The enterprise requirements came in last week. Enterprise customers need tenant isolation, resource-level permissions, and time-based access windows. Pure RBAC can't express "this user can read project X but only between 9am-5pm on weekdays in their timezone." ABAC can.

**Drew**: OPA policies live where?

**Taylor**: Git repo, deployed to a sidecar container alongside each service. Policy updates go through CI/CD with mandatory review. No ad-hoc policy changes in production.

**Avery**: Session management — are we doing server-side sessions anywhere?

**Taylor**: No server-side sessions at all. Stateless JWT-based auth everywhere. The only server-side state is: (1) the refresh token store (opaque tokens), (2) the revocation denylist in Redis. Both are explicit and bounded.

**Drew**: CORS and CSRF protection?

**Taylor**: CSRF protection via Double Submit Cookie pattern — for any state-mutating request, the client sends a CSRF token in both a cookie and a header. They must match server-side. CORS: allowlist only, no wildcards.

**Avery**: Audit logging?

**Taylor**: Every auth event logged: login success, login failure, MFA success, MFA failure, token refresh, token revocation, permission denied. Logs are immutable — written to an append-only store. We're using AWS CloudTrail for the audit trail, with a separate read-only account for auditors.

**Drew**: What about API key auth for service-to-service calls?

**Taylor**: Service accounts in Keycloak using client credentials flow. API keys are not the pattern — too easy to leak. mTLS between internal services where possible. For external integrations that need API keys, we generate 32-byte random keys, store only the SHA-256 hash, prefix with a service identifier (e.g., `cb_live_` for production).

**Avery**: Last thing — penetration testing?

**Taylor**: Quarterly external pen test, mandatory. We also run OWASP ZAP as part of CI/CD pipeline on every deploy. Bug bounty program to launch in Q2 — public scope, coordinated disclosure policy.
