#!/usr/bin/env bash
#
# End-USER install acceptance: a brand-new user on a clean machine, installing the
# REAL published artifact from PyPI and running the actual commands. This is the
# "can people who buy/install it actually use it without breaking?" check —
# complementary to acceptance_teamserver.sh (which tests the server in Docker).
#
#   ci/acceptance_install.sh           # structural: install, console scripts, selfcheck, configure
#   ci/acceptance_install.sh --full    # + real extraction (solo) and a pip-client → server round-trip
#
# Env:
#   WAYSTONE_VERSION  version to install (default: latest on PyPI)
#   LLM_API_KEY       required for --full (or GEMINI_API_KEY)
#   WAYSTONE_IMAGE    server image for the --full client→server test (default: published :latest)
#   ACCEPT_PORT       host port for the test server (default 8110)
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$REPO/deploy/docker-compose.yml"
LLM_KEY="${LLM_API_KEY:-${GEMINI_API_KEY:-}}"
IMAGE="${WAYSTONE_IMAGE:-ghcr.io/unbidden-ai/waystone-server:latest}"
PORT="${ACCEPT_PORT:-8110}"
FULL=0; [ "${1:-}" = "--full" ] && FULL=1
WORK="$(mktemp -d)"
VBIN="$WORK/venv/bin"
PASS=0; FAIL=0
SCRIPTS="waystone waystone-mcp waystone-hook-submit waystone-hook-stop waystone-hook-summarize \
         waystone-hook-posttool waystone-hook-import-memory waystone-hook-sessionstart waystone-statusline"

c_g="\033[32m"; c_r="\033[31m"; c_b="\033[1m"; c_0="\033[0m"
ok()   { PASS=$((PASS+1)); echo -e "  ${c_g}✓${c_0} $1"; }
bad()  { FAIL=$((FAIL+1)); echo -e "  ${c_r}✗ $1${c_0}"; }
area() { echo -e "\n${c_b}── $1${c_0}"; }

cleanup() {
  LLM_API_KEY=x docker compose -p ws_install -f "$COMPOSE" down -v >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# Use python3.13 (3.14 breaks sqlite-vec on macOS). Fall back to python3 if absent.
PY=python3.13; command -v "$PY" >/dev/null || PY=python3
VER="${WAYSTONE_VERSION:-$(curl -s https://pypi.org/pypi/waystone/json | "$PY" -c 'import sys,json;print(json.load(sys.stdin)["info"]["version"])' 2>/dev/null)}"
echo -e "${c_b}End-user install acceptance — waystone==$VER from PyPI  (full=$FULL)${c_0}"

# ── Area 1: clean-room install from PyPI ────────────────────────────────────
area "1. Install from PyPI into a fresh virtualenv"
"$PY" -m venv "$WORK/venv" 2>/dev/null && ok "created a clean virtualenv ($("$PY" --version 2>&1))" || bad "could not create a venv"
"$VBIN/pip" install -q --upgrade pip wheel >/dev/null 2>&1
if "$VBIN/pip" install -q "waystone==$VER" >"$WORK/pip.log" 2>&1; then
  ok "pip install waystone==$VER succeeded"
else
  bad "pip install failed"; tail -5 "$WORK/pip.log"
fi

# ── Area 2: the package is actually runnable ────────────────────────────────
area "2. Console scripts + version"
missing=""
for s in $SCRIPTS; do [ -x "$VBIN/$s" ] || missing="$missing $s"; done
[ -z "$missing" ] && ok "all 9 console scripts installed" || bad "missing console scripts:$missing"
got="$("$VBIN/waystone" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
[ "$got" = "$VER" ] && ok "waystone --version reports $got" || bad "version mismatch (got '$got', expected '$VER')"

# ── Area 3: offline self-check (hooks + MCP + entry points) ─────────────────
area "3. selfcheck --deep (runs every hook + MCP, offline)"
if HOME="$WORK/home" "$VBIN/waystone" selfcheck --deep >"$WORK/selfcheck.log" 2>&1; then
  ok "selfcheck --deep passed (package imports, config, hooks, MCP all OK)"
else
  bad "selfcheck --deep failed"; grep -iE "✗|fail|error" "$WORK/selfcheck.log" | head -5
fi

# ── Area 4: one-command setup wires Claude Code ─────────────────────────────
area "4. configure (wire Claude Code, non-interactive)"
export HOME="$WORK/home"; mkdir -p "$HOME"
if "$VBIN/waystone" configure --non-interactive >"$WORK/configure.log" 2>&1; then
  ok "configure ran without error"
else
  bad "configure failed"; tail -5 "$WORK/configure.log"
fi
if grep -rqiE "waystone-hook|UserPromptSubmit" "$HOME/.claude/settings.json" 2>/dev/null; then
  ok "Claude Code hooks were written to ~/.claude/settings.json"
else
  bad "configure did not wire Claude Code hooks"
fi
unset HOME

# ── Full areas (real extraction) ────────────────────────────────────────────
if [ "$FULL" = "1" ]; then
  if [ -z "$LLM_KEY" ]; then
    area "5-6. Full (skipped)"; bad "--full requires LLM_API_KEY or GEMINI_API_KEY"
  else
    H="$WORK/solohome"; mkdir -p "$H/.waystone"
    cat > "$H/.waystone/config.yaml" <<EOF
llm:
  base_url: https://generativelanguage.googleapis.com/v1beta/openai
  model: gemini-2.5-flash
  api_key: $LLM_KEY
EOF
    area "5. Solo user: real extraction round-trip (local SQLite)"
    HOME="$H" "$VBIN/waystone" verify >/dev/null 2>&1 \
      && ok "waystone verify: a real extraction round-trip works" || bad "waystone verify failed (LLM/auth/model issue)"
    echo "We migrated the cache layer from Redis to Memcached for lower latency." > "$WORK/note.txt"
    HOME="$H" "$VBIN/waystone" extract solo-demo "$WORK/note.txt" >/dev/null 2>&1 \
      && ok "waystone extract wrote to the local graph" || bad "waystone extract failed"
    HOME="$H" "$VBIN/waystone" query solo-demo "what cache did we move to" 2>/dev/null | grep -qi memcached \
      && ok "waystone query retrieves the stored fact" || bad "waystone query did not retrieve the fact"

    area "6. Team member: pip-installed client → Team Server"
    if command -v docker >/dev/null; then
      SK="waystone_install_$$_${RANDOM}"
      cat > "$WORK/srv.env" <<EOF
WAYSTONE_IMAGE=$IMAGE
WAYSTONE_PORT=$PORT
WAYSTONE_LICENSE=
WAYSTONE_API_KEY=$SK
LLM_API_KEY=$LLM_KEY
EOF
      docker compose -p ws_install --env-file "$WORK/srv.env" -f "$COMPOSE" up -d >/dev/null 2>&1
      up=0; for _ in $(seq 1 40); do curl -sf "http://localhost:$PORT/v1/health" >/dev/null 2>&1 && { up=1; break; }; sleep 2; done
      if [ "$up" = "1" ]; then
        ok "Team Server is up for the client to connect to"
        CH="$WORK/memberhome"; mkdir -p "$CH/.waystone"
        cat > "$CH/.waystone/config.yaml" <<EOF
backend: remote
api_url: http://localhost:$PORT
api_key: $SK
EOF
        echo "The team picked GraphQL over REST for the public API." > "$WORK/m.txt"
        ( export HOME="$CH"; cd "$CH"; "$VBIN/waystone" extract team-proj "$WORK/m.txt" >/dev/null 2>&1 ) \
          && ok "the pip-installed client extracts to the server (backend: remote)" || bad "remote extract from the installed client failed"
        ( export HOME="$CH"; cd "$CH"; "$VBIN/waystone" query team-proj "what API style did we choose" 2>/dev/null ) | grep -qi graphql \
          && ok "the pip-installed client queries the server and gets the team's fact" || bad "remote query from the installed client failed"
      else bad "Team Server did not come up — cannot test the client path"; fi
    else bad "docker not available — skipping the client→server test"; fi
  fi
else
  echo -e "\n${c_b}── 5-6. Full extraction tests skipped${c_0} (pass --full to run; uses your LLM key)"
fi

echo -e "\n${c_b}Result: ${c_g}$PASS passed${c_0}, $( [ "$FAIL" -gt 0 ] && echo -e "${c_r}$FAIL failed${c_0}" || echo "0 failed" )"
[ "$FAIL" -eq 0 ]
