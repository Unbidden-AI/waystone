# Waystone Go CLI — design sketch

**Status:** prototype / exploration. Not wired into the Python package.
**Goal:** ship the Waystone client as a single static binary (no Python runtime),
killing the install pain (Python version pinning, PyInstaller, the npm-wrapper plan)
and shaving the per-prompt hook latency further.

---

## The core insight that makes this feasible

Waystone's SQLite schema is **mostly plain SQLite** that any language reads:

| Table | Purpose | Needs sqlite-vec? |
|-------|---------|-------------------|
| `nodes` | facts (id, fact, type, confidence, tags, supersedes, …) | no |
| `edges` | graph (from_id, to_id, relation) | no |
| `node_tags` | normalized tag → node index | no |
| `nodes_fts` | FTS5 full-text over `fact` | no (FTS5 is built into SQLite) |
| `node_embeddings` | **vec0 virtual table** (vectors) | **yes — semantic only** |

The **default retrieval path** (keywords → tag/FTS match → BFS over edges → strategy
pipeline → grouped markdown) — the one that scores **86% recall** in our benchmark —
touches **none of the vec0 table.** sqlite-vec is required *only* for the optional
`semantic` rerank strategy, which is already turned **off** in the hooks.

➡️ **A pure-Go static binary can do the entire default read path with zero C
dependencies**, using `modernc.org/sqlite` (a pure-Go SQLite, FTS5 included — no cgo,
trivially cross-compiles to every OS). Semantic rerank, when wanted, is a small
brute-force cosine over the candidate set's stored f32 vectors — also pure Go (the
post-BFS candidate set is small, so we don't need an ANN index at query time).

This is the whole reason the project is tractable: **we are not reimplementing a
database.** We're reading an existing SQLite file and porting ~200 lines of
retrieval logic.

---

## What goes native-Go vs. stays Python (strangler-fig)

Port the **read/hot path** first (highest distribution + latency value, lowest risk);
leave the **LLM-heavy write path** in Python until/if it's worth porting.

**Phase 1 — read path + hooks (native Go, the 80/20):**
- `waystone query` — keyword → tag/FTS → BFS → strategy pipeline → markdown
- `waystone show` / `story` / `last-context` — pure SQLite reads
- The **UserPromptSubmit hook** — read transcript tail (byte-offset watermark),
  build context, emit injection JSON. Pure I/O + the query path. *Go is great at
  exactly this, and faster — the hook hot-path latency basically vanishes.*
- `waystone doctor` / `selfcheck` / `--version`

**Phase 2 — extraction (optional native Go):**
- Extraction is just **HTTP POST to an OpenAI-compatible endpoint + JSON parse +
  merge into SQLite.** Go does this natively (`net/http`, `encoding/json`). The
  prompt templates port directly. The only thing lost is the Python embedding
  model for semantic dedup — call an embedding **API** or skip dedup at write time.
- The detached background worker (`Stop`/`PostToolUse` spawns) becomes a goroutine
  or a re-exec of the same static binary — no `python -m` spawn semantics to debug
  per-OS (the exact class of bug that bit us on Windows).

**Stays Python (for now):** the heavy benchmark/eval harness, `catchup-summarize`,
anything that leans on the Python ML ecosystem. These aren't on the client hot path.

**Coexistence:** both clients read the **same SQLite file + same config**, so we can
ship the Go binary for the hooks/read path while Python still owns extraction —
no big-bang rewrite, no data migration.

---

## The one real risk, and the call

**Risk:** "single static binary" + "load the sqlite-vec C extension" don't mix
cleanly — loadable C extensions want cgo, which breaks easy static cross-compiles.

**Call:** *don't load sqlite-vec from Go at all.*
- Default path: pure-Go SQLite (`modernc.org/sqlite`) + FTS5 — fully static.
- Semantic rerank (opt-in): read the candidate nodes' stored vectors, brute-force
  cosine in Go. Small candidate set → no ANN needed → still no C.
- Keep cgo + sqlite-vec entirely on the **Python** side, which owns writes/embeddings.

This keeps the Go binary `CGO_ENABLED=0` → one `go build` per target, no toolchain
per platform.

---

## Build & distribution

- **Build:** `CGO_ENABLED=0 go build` → a single static binary per OS/arch.
- **Release:** `goreleaser` cross-compiles the matrix (darwin/amd64, darwin/arm64,
  linux/amd64, linux/arm64, windows/amd64) in one CI job and publishes archives +
  checksums.
- **Install channels** (all now trivial because it's one static file):
  - `curl … | sh` (the Go-tool convention)
  - Homebrew tap
  - **npm wrapper** that just downloads the right prebuilt binary (this is the
    clean version of `NPM_DISTRIBUTION_PLAN.md` — no PyInstaller, no Python)
  - Scoop/winget on Windows
- **Size:** ~8–15 MB static binary vs. a Python env. Starts in milliseconds (no
  interpreter warmup, no sqlite-vec cold-start scan — which also kills the Windows
  Defender stall we documented).

---

## Why Go (not Rust) for *this* layer

This is the **client / CLI / hook / control-plane** layer — networked I/O, fast
startup, single-binary distribution, simple concurrency. That's Go's bullseye.
Rust is the answer for the *storage engine* (LanceStore) when that trigger fires;
it is overkill (and slower to ship) for a CLI that mostly reads SQLite and POSTs
JSON. Build the engine in Rust later; build the binary people install in Go now.

---

## What's in this prototype dir

A minimal, buildable read-path proof (see `README.md` to run):
- `main.go` — cobra root + `version`
- `query.go` — `waystone query <task>`: keyword → tag match → BFS → grouped output
- `store.go` — opens a real Waystone `context.db` and reads `nodes`/`edges`/`node_tags`

It deliberately reuses an **existing** graph DB to prove the read path is portable
and fast — no schema changes, no migration.
