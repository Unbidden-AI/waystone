# waystone-go (prototype)

A single static binary that reads an existing Waystone `context.db` and serves the
default retrieval path with **zero C dependencies**. See `DESIGN.md` for the why.

> Status: read-path proof of concept. Not wired into the Python package. The Go
> toolchain was not available in the authoring environment, so this scaffold is
> **unbuilt** — the schema/queries were verified against a live `context.db`, but
> run `go vet ./...` + the build below before trusting it.

## Build

```bash
cd prototypes/go-cli
go mod tidy                      # resolve deps + go.sum
CGO_ENABLED=0 go build -o waystone .
```

`CGO_ENABLED=0` is the point: a fully static binary that cross-compiles to every
target with no per-platform toolchain. Cross-compile examples:

```bash
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -o waystone.exe .
GOOS=linux   GOARCH=arm64 CGO_ENABLED=0 go build -o waystone-linux-arm64 .
```

## Run (against a real graph)

```bash
./waystone version
./waystone query "postgres team server licensing" \
    --db ~/.waystone/projects/Waystone/context.db --hops 2 --top-k 10
```

## What it demonstrates

- Opening a real Waystone SQLite graph from pure-Go (`modernc.org/sqlite`).
- The default retrieval path — keyword extraction → `node_tags` entry lookup →
  BFS over `edges` → superseded-pruning → top-k by confidence → grouped markdown.
- No sqlite-vec, no cgo, no Python — the whole client hot path as one static file.

## Not yet ported (see DESIGN.md)

- The full strategy pipeline (recency decay, confidence threshold, token budget).
- Semantic rerank (would be a pure-Go brute-force cosine over candidate vectors).
- Extraction / writes (stay in Python for now — strangler-fig).
