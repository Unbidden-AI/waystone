#!/usr/bin/env bash
#
# Team Server acceptance battery — black-box tests against a real running server
# (the PUBLISHED public image, exactly what a buyer gets). Spins up a throwaway
# stack, runs each feature area, prints PASS/FAIL, and cleans up after itself.
#
#   ci/acceptance_teamserver.sh           # structural tests only (no LLM cost)
#   ci/acceptance_teamserver.sh --full    # also areas 4-6 (real extraction = LLM cost)
#
# Env:
#   WAYSTONE_IMAGE   server image to test (default: ghcr.io/unbidden-ai/waystone-server:latest)
#   LLM_API_KEY      required for --full (or GEMINI_API_KEY); the server's extraction key
#   ACCEPT_PORT      host port base (default 8097)
#
# Structural areas (always): 1 boot & safety, 2 auth, 3 licensing & seats, 7 persistence.
# Full areas (--full):       4 multiplayer (shared graph), 5 tenant isolation, 6 remote-client wiring.
# Exit code is non-zero if any area fails.
# NOTE: deliberately NOT using `pipefail` — several checks pipe a long/streaming
# producer (e.g. `docker compose logs`) into `grep -q`, which closes the pipe on
# first match and SIGPIPEs the producer; with pipefail that false-fails a real match.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$REPO/deploy/docker-compose.yml"
PROJ="ws_accept"
PORT="${ACCEPT_PORT:-8097}"
URL="http://localhost:$PORT"
IMAGE="${WAYSTONE_IMAGE:-ghcr.io/unbidden-ai/waystone-server:latest}"
LLM_KEY="${LLM_API_KEY:-${GEMINI_API_KEY:-}}"
FULL=0; [ "${1:-}" = "--full" ] && FULL=1
WORK="$(mktemp -d)"
PASS=0; FAIL=0

c_g="\033[32m"; c_r="\033[31m"; c_b="\033[1m"; c_0="\033[0m"
ok()   { PASS=$((PASS+1)); echo -e "  ${c_g}✓${c_0} $1"; }
bad()  { FAIL=$((FAIL+1)); echo -e "  ${c_r}✗ $1${c_0}"; }
area() { echo -e "\n${c_b}── $1${c_0}"; }

compose() { docker compose -p "$PROJ" --env-file "$WORK/.env" -f "$COMPOSE" "$@"; }
cleanup() { compose down -v >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

write_env() {  # $1=license $2=apikey  (LLM key + image + port always included)
  cat > "$WORK/.env" <<EOF
WAYSTONE_IMAGE=$IMAGE
WAYSTONE_PORT=$PORT
WAYSTONE_LICENSE=${1:-}
WAYSTONE_API_KEY=${2:-}
LLM_API_KEY=${LLM_KEY:-dummy-not-used-by-structural-tests}
EOF
}
wait_health() { for _ in $(seq 1 40); do curl -sf "$URL/v1/health" >/dev/null 2>&1 && return 0; sleep 2; done; return 1; }
code() { curl -s -o /dev/null -w "%{http_code}" "$@"; }            # print HTTP status
issue() { compose exec -T server waystone team issue "$1" 2>/dev/null | grep -oE 'waystone_[A-Za-z0-9_-]+' | head -1; }

echo -e "${c_b}Team Server acceptance — image: $IMAGE  (full=$FULL)${c_0}"
command -v docker >/dev/null || { echo "docker not found"; exit 2; }

# Mint a real 4-seat license if the signing key is available; else fall back to
# the 3 trial seats so the cap test still runs (just at a different number).
LICENSE=""; CAP=3; LIC_KIND="trial (no signing key)"
if [ -f "$REPO/scripts/issue_license.py" ]; then
  if TOK="$(python3.13 "$REPO/scripts/issue_license.py" --seats 4 --org accept@test --years 1 2>/dev/null | grep -av '^#' | tail -1)" \
     && [ -n "$TOK" ]; then LICENSE="$TOK"; CAP=4; LIC_KIND="signed 4-seat"; fi
fi

# ── Area 1: boot & safety ───────────────────────────────────────────────────
area "1. Boot & safety"
write_env "" ""                                   # neither license nor key
compose up -d >/dev/null 2>&1 || true
sleep 7
if wait_health; then bad "server booted WIDE OPEN with no auth configured"; else ok "refuses to boot with no license and no API key"; fi
compose logs server 2>&1 | grep -qi "refusing to start" && ok "prints the actionable refusal message" || bad "missing refusal message in logs"
compose down -v >/dev/null 2>&1

write_env "$LICENSE" ""                            # license → per-seat mode
compose up -d >/dev/null 2>&1
if wait_health; then ok "boots healthy with a license (fresh DB)"; else bad "did not become healthy with a license"; compose logs server 2>&1 | tail -15; fi
ver="$(curl -s "$URL/v1/health" | python3.13 -c 'import sys,json;print(json.load(sys.stdin).get("version","?"))' 2>/dev/null)"
ok "/v1/health reports version $ver"

# ── Area 2: auth ────────────────────────────────────────────────────────────
area "2. Auth"
[ "$(code "$URL/v1/projects")" = "401" ] && ok "no key → 401" || bad "missing key was not rejected"
[ "$(code -H 'Authorization: Bearer waystone_bogus' "$URL/v1/projects")" = "401" ] && ok "bad key → 401" || bad "bogus key was not rejected"
ALICE="$(issue alice@test)"
[ -n "$ALICE" ] && [ "$(code -H "Authorization: Bearer $ALICE" "$URL/v1/projects")" = "200" ] && ok "valid member key → 200" || bad "valid member key was rejected"

# ── Area 3: licensing & seats ───────────────────────────────────────────────
area "3. Licensing & seats ($LIC_KIND, cap=$CAP)"
compose exec -T server waystone team license 2>&1 | grep -qiE "Seats:.*$CAP" && ok "team license reports $CAP seats" || bad "license seat count wrong"
n=1  # alice already holds 1 seat
while [ "$n" -lt "$CAP" ]; do issue "m$n@test" >/dev/null; n=$((n+1)); done
over="$(compose exec -T server waystone team issue overflow@test 2>&1)"
echo "$over" | grep -qi "seat limit" && ok "issuing past the cap is rejected ($CAP/$CAP)" || bad "cap not enforced (overflow allowed)"
compose exec -T server waystone team revoke m1@test >/dev/null 2>&1
issue "newhire@test" >/dev/null 2>&1 && ok "revoke frees a seat (re-issue succeeds)" || bad "revoke did not free a seat"
# Fail-closed verification (library-level, instant): a tampered token grants nothing.
python3.13 - "$LICENSE" <<'PY' && ok "tampered license fails closed" || bad "tampered license did not fail closed"
import sys
from waystone.licensing import verify_license, LicenseError
tok = sys.argv[1]
if not tok:
    sys.exit(0)  # no signing key this run; nothing to tamper
try:
    verify_license(tok[:-4] + "zzzz"); sys.exit(1)
except LicenseError:
    sys.exit(0)
PY

# Free the filler seats so the multiplayer test below can issue Bob (the cap test
# above intentionally filled every seat). Leave only alice@test.
for m in $(compose exec -T server waystone team members 2>/dev/null | grep -oE '[a-z0-9]+@test' | grep -v '^alice@test'); do
  compose exec -T server waystone team revoke "$m" >/dev/null 2>&1
done

# ── Area 7: persistence ─────────────────────────────────────────────────────
area "7. Persistence (restart survives)"
before="$(compose exec -T server waystone team members 2>/dev/null | grep -c @ || echo 0)"
compose restart server >/dev/null 2>&1; wait_health || true
after="$(compose exec -T server waystone team members 2>/dev/null | grep -c @ || echo 0)"
[ "$after" -ge "$before" ] && [ "$before" -gt 0 ] && ok "issued seats survive a restart ($before → $after members)" || bad "seats lost across restart ($before → $after)"

# ── Full areas (real extraction) ────────────────────────────────────────────
if [ "$FULL" = "1" ]; then
  if [ -z "$LLM_KEY" ]; then
    area "4-6. Full (skipped)"; bad "--full requires LLM_API_KEY or GEMINI_API_KEY"
  else
    BOB="$(issue bob@test)"
    extract() { code -X POST -H "Authorization: Bearer $1" -H 'Content-Type: application/json' \
                  -d "{\"text\":\"$2\"}" "$URL/v1/projects/$3/extract"; }
    queryj()  { curl -s -X POST -H "Authorization: Bearer $1" -H 'Content-Type: application/json' \
                  -d "{\"task\":\"$2\"}" "$URL/v1/projects/$3/query"; }

    area "4. Multiplayer (shared team graph)"
    [ "$(extract "$ALICE" 'The team chose Postgres over MongoDB for the datastore because we need ACID transactions.' team-demo)" = "200" ] \
      && ok "Alice extracts into project team-demo" || bad "Alice extract failed"
    if echo "$(queryj "$BOB" 'what datastore did we choose' team-demo)" | grep -qi postgres; then
      ok "Bob (different key) queries the SAME project and sees Alice's fact"
    else bad "Bob could not see Alice's fact (shared graph broken)"; fi

    area "5. Tenant isolation"
    extract "$ALICE" 'Our secret launch date is the 14th of Octember.' projx >/dev/null
    if echo "$(queryj "$ALICE" 'what is the secret launch date' projy)" | grep -qi octember; then
      bad "project projx data LEAKED into project projy"
    else ok "facts in one project do not leak into another"; fi

    area "6. Remote-client wiring (waystone CLI → server)"
    if command -v waystone >/dev/null; then
      export HOME="$WORK/home"; mkdir -p "$HOME/.waystone"   # temp HOME: never touches the real config
      cat > "$HOME/.waystone/config.yaml" <<EOF
backend: remote
api_url: $URL
api_key: $ALICE
EOF
      # Run from a clean dir: a stray ./config.yaml in CWD (e.g. the repo's own)
      # would shadow the temp-HOME remote config and route the CLI locally.
      ( cd "$HOME"
        echo "We standardized on Kafka for the event bus across all services." > note.txt
        waystone extract cli-demo note.txt >/dev/null 2>&1 ) \
        && ok "waystone extract (backend: remote) routes to the server" || bad "remote extract via CLI failed"
      ( cd "$HOME"; waystone query cli-demo "what did we use for the event bus" 2>/dev/null ) | grep -qi kafka \
        && ok "waystone query (backend: remote) retrieves from the team graph" || bad "remote query via CLI failed"
    else bad "waystone CLI not on PATH — cannot test client wiring"; fi
  fi
else
  echo -e "\n${c_b}── 4-6. Full extraction tests skipped${c_0} (pass --full to run; uses your LLM key)"
fi

echo -e "\n${c_b}Result: ${c_g}$PASS passed${c_0}, $( [ "$FAIL" -gt 0 ] && echo -e "${c_r}$FAIL failed${c_0}" || echo "0 failed" )"
[ "$FAIL" -eq 0 ]
