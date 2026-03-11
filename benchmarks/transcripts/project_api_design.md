# Project: REST API Design Session
# Participants: Alex (backend lead), Sam (frontend), Jordan (DevOps)

---

**Alex**: Alright, let's nail down the API design for the new user management service. We need to decide on the base URL structure first. I'm thinking `/api/v1/` as the prefix.

**Sam**: Works for me. Are we going RESTful all the way or mixing in some RPC-style endpoints?

**Alex**: Pure REST. No exceptions. We've had too many issues with inconsistent RPC patterns in the old service.

**Jordan**: Agreed. What about authentication? We need to decide that before anything else since it affects every endpoint.

**Alex**: I want to go with JWT tokens. Stateless, scales horizontally without sticky sessions. Sessions were a nightmare when we tried to add more instances last quarter.

**Sam**: JWT it is. HS256 or RS256?

**Alex**: RS256. Private key on the auth service, public keys distributed to other services. Asymmetric means we never have to share the secret.

**Jordan**: Good call. Where do we store the tokens client-side? LocalStorage or cookies?

**Sam**: HttpOnly cookies with SameSite=Strict. Never localStorage — XSS risk is too high.

**Alex**: Absolutely. And we'll set a 15-minute expiry on access tokens with 7-day refresh tokens stored separately.

**Jordan**: What's the base URL for the user endpoints?

**Alex**: `/api/v1/users` for the collection, `/api/v1/users/{id}` for individual users. Standard stuff.

**Sam**: What fields does the user object return? I need to know what I'm working with for the frontend.

**Alex**: `id`, `email`, `display_name`, `created_at`, `updated_at`, `role`. We're NOT returning `password_hash` obviously, and we're not returning `last_login` — that's an internal audit field only.

**Jordan**: What roles are we supporting?

**Alex**: Three roles: `admin`, `member`, `viewer`. Viewer is read-only, member can create content, admin can manage users.

**Sam**: Wait, do viewers need an account at all? Some of our content is public.

**Alex**: Good point. Public content is accessible without auth. The viewer role is for logged-in users who we've explicitly restricted. Actually, let me revise — we're adding a fourth role: `guest` for unauthenticated access to public endpoints. The roles in order are: `admin`, `member`, `viewer`, `guest`.

**Jordan**: For pagination, are we doing cursor-based or offset?

**Alex**: Cursor-based. Offset pagination breaks when rows are inserted during traversal. We'll use `cursor` and `limit` query params, return `next_cursor` in the response.

**Sam**: What's the max page size?

**Alex**: Hard cap at 100 items. Default is 20 if `limit` isn't specified.

**Jordan**: Error response format?

**Alex**: RFC 7807 Problem Details. JSON with `type`, `title`, `status`, `detail`, `instance`. Every error must include all five fields.

**Sam**: What about rate limiting?

**Alex**: 1000 requests per minute per user token. Unauthenticated requests get 100 per minute. Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

**Jordan**: Should rate limiting be at the gateway or the service level?

**Alex**: Gateway. Let Kong handle it. The service shouldn't need to know about rate limits.

**Sam**: One more thing — versioning strategy. What happens when we need v2?

**Alex**: URL versioning, not header versioning. `/api/v1/` and `/api/v2/` can coexist. We tried header versioning before and it made caching impossible.

**Jordan**: How long do we support old versions?

**Alex**: Minimum 12 months deprecation notice before we kill any version. We'll add a `Deprecation` header to sunset old endpoints.

**Sam**: Last thing from me — CORS. Which origins are allowed?

**Alex**: Only our production frontend domain and localhost:3000 for dev. Absolutely no wildcard origins on authenticated endpoints.

**Jordan**: Sounds solid. I'll set up the Kong gateway config to match these specs.
