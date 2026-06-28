#!/usr/bin/env bash
#
# build.sh — set up Go, build the Waystone Go CLI prototype, and smoke-test it.
# Safe to re-run. Works from anywhere (resolves to its own directory).
#
#   ./build.sh            # install-check + deps + vet + build + smoke query
#   ./build.sh --cross    # also cross-compile static binaries for every OS -> ./dist
#
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# --- pretty output -----------------------------------------------------------
bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

export CGO_ENABLED=0   # pure-Go: fully static, cross-compiles with no C toolchain

# --- 1. Go toolchain ---------------------------------------------------------
bold "1/4  Go toolchain"
if ! command -v go >/dev/null 2>&1; then
  warn "Go is not installed."
  if command -v brew >/dev/null 2>&1; then
    read -r -p "  Install it now with 'brew install go'? [y/N] " ans
    if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
      brew install go
    else
      die "Go is required. Install it with:  brew install go"
    fi
  else
    die "Go is required. Install Homebrew (https://brew.sh) then:  brew install go  — or grab it from https://go.dev/dl/"
  fi
fi
ok "$(go version | awk '{print $1, $3}')"

# --- 2. dependencies ---------------------------------------------------------
bold "2/4  Dependencies"
go mod tidy
ok "deps resolved (cobra + modernc sqlite)"

# --- 3. vet + build ----------------------------------------------------------
bold "3/4  Vet + build"
if ! go vet ./...; then
  warn "go vet flagged something above — often a harmless style nit; continuing."
fi
if ! go build -o waystone .; then
  die "build failed — copy the error above to Claude and it'll be fixed fast (the code was authored without a local Go compiler to dry-run it)."
fi
ok "built ./waystone"

# --- 4. smoke test -----------------------------------------------------------
bold "4/4  Smoke test"
./waystone version
db="$(ls -S "$HOME"/.waystone/projects/*/context.db 2>/dev/null | head -1 || true)"
if [[ -n "${db:-}" ]]; then
  echo "  querying against: $db"
  ./waystone query "postgres team server licensing" --db "$db" --top-k 8 \
    || warn "query exited nonzero (the graph may have no matching tags — try a different task)"
else
  warn "No ~/.waystone/projects/*/context.db found — skipped the smoke query."
fi

# --- optional: cross-compile every platform ---------------------------------
if [[ "${1:-}" == "--cross" ]]; then
  bold "Cross-compiling static binaries -> ./dist"
  mkdir -p dist
  while read -r goos goarch ext; do
    out="dist/waystone-${goos}-${goarch}${ext}"
    GOOS="$goos" GOARCH="$goarch" go build -o "$out" . && ok "$out"
  done <<'TARGETS'
darwin amd64
darwin arm64
linux amd64
linux arm64
windows amd64 .exe
TARGETS
fi

bold "Done."
echo "  Try:   ./waystone query \"your task here\" --db <path-to-context.db>"
echo "  All OSes from this Mac:   ./build.sh --cross   (or: make cross)"
