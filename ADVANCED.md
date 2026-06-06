# Advanced Configuration — How to Be a Waystone Rock Star

The default install (`pip install waystone` → `waystone configure` → open your editor) is intentionally minimal — it should just work. This guide is for power users who want to tune Waystone past the defaults.

Everything here lives in `~/.waystone/config.yaml`. Missing keys fall back to built-in defaults, so you only set what you want to change.

---

## Embeddings: local model vs. API

Semantic search and paraphrase de-duplication need an embedding model. There are two backends.

### `local` (default)

Uses `BAAI/bge-small-en-v1.5` via `sentence-transformers`. Fully offline, no API cost — but `sentence-transformers` pulls in **PyTorch** (a large download), so it's an opt-in extra:

```bash
pip install "waystone[semantic]"
```

`sqlite-vec` (the vector store) is always installed; only the embedding model is optional.

### `api` (no PyTorch)

Embed through your LLM provider's embedding endpoint via `litellm` (already a core dependency) — **no PyTorch, no local model download**. Great for lightweight environments (Windows especially) where you'd rather use an API key than install a multi-GB ML stack.

```yaml
# ~/.waystone/config.yaml
embeddings:
  backend: api
  model: gemini/text-embedding-004   # any litellm-supported embedding model
  dim: 768                           # MUST match the model's output dimension
  api_key_env: GEMINI_API_KEY        # optional; falls back to your llm api key
```

Common models and their dimensions:

| Provider | `model` | `dim` |
|---|---|---|
| Gemini | `gemini/text-embedding-004` | `768` |
| OpenAI | `text-embedding-3-small` | `1536` |
| OpenAI | `text-embedding-3-large` | `3072` |

> **`dim` must exactly match the model.** The vector table's column width is fixed at creation; a mismatch makes inserts fail.

### Switching backends → re-embed

Vector spaces from different models aren't comparable, and the vector table's dimension is fixed when it's created. So after changing `backend`, `model`, or `dim`, rebuild the embeddings:

```bash
waystone reembed <project>
```

This drops the vector table, recreates it at the new dimension, and re-embeds every node. (New/empty projects don't need this — the table is created at the configured dimension on first use.)

### Tradeoffs

| | `local` | `api` |
|---|---|---|
| Install weight | Heavy (PyTorch) | Light (litellm only) |
| Cost | Free | Per-embedding API cost (tiny) |
| Network | Offline | Required |
| Privacy | 100% local | Text sent to provider |

---

## Retrieval strategy tuning

The retrieval pipeline is a sequence of strategies, all toggleable in config or per-query with `--enable`/`--disable`:

```yaml
strategies:
  superseded_pruning: true     # drop facts that have been superseded
  confidence_threshold: 0.0    # e.g. 0.6 to hide tentative facts
  recency_decay: false         # weight recent facts higher
  recency_half_life_days: 30   # how fast old facts fade (when decay is on)
  token_budget: 0              # 0 = unlimited; e.g. 500 to cap injected context
  relevance_scoring: true      # rank entry nodes by tag overlap
defaults:
  hops: 3                      # BFS traversal depth
  top_k: 10                    # max facts returned per query
```

Tuning tips:
- **Noisy / over-large context?** Lower `top_k`, set a `token_budget`, or raise `confidence_threshold`.
- **Missing relevant facts?** Raise `hops` and `top_k`.
- **Fast-moving project?** Turn on `recency_decay` so stale decisions fade.

Test changes live: `waystone query <project> "<question>" --stats`.

---

## Chat attachment auto-extraction

Long Discord/Telegram messages arrive as `message.txt` attachments — their text never reaches the prompt directly. The submit hook now scans the plugin inbox (`.claude/discord/inbox/`, `.claude/telegram/inbox/`) and auto-extracts any new `.txt` attachment into the graph, just like a normal turn. A per-project ledger (`extracted_inbox.json`) prevents re-extraction. No action needed — it's automatic (a file downloaded mid-turn is picked up on the next prompt).

---

## Capturing long autonomous runs (PostToolUse)

In plan/auto mode the agent can run for a long time inside a *single* turn — and since neither `UserPromptSubmit` nor `Stop` fires until that turn ends, nothing reaches the graph until the very end. The **PostToolUse** hook fixes this: it summarizes state-changing tool calls (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `Bash`) into a buffer and flushes them to background extraction *mid-run*, so the graph fills in while the agent works.

It's installed automatically with the hooks integration. Tune or disable it in config:

```yaml
posttool:
  enabled: true
  min_events: 8       # flush after this many captured tool calls
  max_chars: 4000     # …or when buffered summaries exceed this many chars
  tools: [Write, Edit, MultiEdit, NotebookEdit, Bash]
```

Set `enabled: false` to turn it off, or trim `tools` to capture fewer kinds of action.

## Quick capture: `waystone remember` and `/btw`

`waystone remember` writes a fact straight to the graph — no LLM, no buffering, instantly retrievable:

```bash
waystone remember "We chose Postgres over MySQL for JSONB support" --pin
```

- Stored as one high-confidence node, keyword-tagged, `source=manual`.
- `--pin` makes it always-injected ("never forget this"); omit it for an ordinary fact.
- `--type` sets the node type (default `decision`); `--project` overrides the auto-detected project.
- Embeddings backfill on the next extraction or `waystone reembed` (the node is immediately retrievable via tags regardless).

`waystone configure` also installs a **`/btw`** Claude Code slash command that wraps it, so mid-task you can type:

```
/btw the staging DB password rotates every Sunday
```

and it lands in the graph without derailing what the agent is doing.

## Status line

The Claude Code status line shows a Waystone segment from the **start** of a session — `WS(<project>): ready` as soon as a `.waystone` marker is found — then live retrieval metrics once you start working. It also surfaces extraction errors with a `⚠` alert (e.g. `⚠ auth`, `⚠ rate`). Configure it:

```yaml
statusline:
  enabled: true          # show the Waystone segment at all
  alert_on_error: true   # surface a ⚠ alert when extraction errors occur
```

> **Windows note:** Waystone forces its hook/CLI/status-line output to UTF-8, so the Unicode glyphs (`✓`, `⚠`, …) no longer crash on legacy `cp1252` consoles. If you're on an older build and see `UnicodeEncodeError`, upgrade, or set `PYTHONUTF8=1` in the `env` block of `~/.claude/settings.json` as a stopgap.

## Pausing extraction

Extraction calls your LLM. To pause it (while keeping retrieval/injection from the existing graph):

```bash
waystone pause     # turns extracted while paused are still buffered, not lost
waystone resume
```

---

## Verifying your LLM / API key

Extraction needs a working LLM. To confirm yours is set up correctly — including that the API key actually works for *extraction*, not just that it's present — run:

```bash
waystone verify          # human-readable
waystone verify --json   # machine-readable (for scripts/CI)
```

`verify` resolves the API key exactly the way extraction does (configured `api_key_env` → inline `api_key` → generic env vars), then runs a **real tiny extraction round-trip** through whichever path is configured (native Gemini SDK or OpenAI-compatible HTTP) and asserts facts come back. This catches auth failures, wrong model names, missing JSON/structured-output support, and endpoint problems that a simple connectivity check misses. Exit code is `0` on success, `1` on failure, with a category (`auth` / `model` / `quota` / `network` / `config`). `waystone configure` and `waystone doctor` use the same resolver, so they can't disagree about which key extraction will use.

### Testing across providers (real keys)

To test the configure → verify → extract path against multiple providers at once, set the keys you want to test and run the matrix script:

```bash
export GEMINI_API_KEY=...
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
python3.13 scripts/verify_providers.py            # tests each provider with a key
python3.13 scripts/verify_providers.py --fresh-install   # also pip-installs into a throwaway venv first
```

Each provider runs in an isolated temp `HOME` (your real `~/.waystone` is untouched), does one real ~$0.001 extraction, and the script exits non-zero if any *configured* provider fails. Providers without a key are skipped. The always-on, no-network tier of these checks lives in `tests/test_cli.py::TestVerify`.

---

## Hermes Agent memory provider

Beyond Claude Code/MCP, Waystone ships a native Hermes Agent memory provider (`hermes_plugin/`). See the README's "Hermes Agent" section and [unbidden.ai/docs/integrations/hermes/](https://unbidden.ai/docs/integrations/hermes/).

---

## Windows: slow first query / one-time hang

On a **fresh Windows install**, the very first semantic query can pause for a long time — sometimes minutes — before responding (and an MCP call may appear to hang with "Tool result missing due to internal error"). This is a **one-time** cost: the first load of the native `sqlite-vec` vector-search extension triggers Windows Defender / SmartScreen scanning the unsigned binary. Once it's loaded once, every later query is instant.

Waystone already mitigates this:

- **Counts-only operations** (`waystone_stats`, `waystone_list_projects`, "how's my DB doing?") skip the extension entirely, so they're always instant.
- `waystone configure` **pre-warms** the extension at the end of setup — pay the one-time cost there (and answer any security prompt) instead of mid-query.
- The MCP server warms it in the background on startup.

If you still hit a stall on first use, either let it finish once (it won't recur), or add a **Microsoft Defender exclusion** for your Python environment's `site-packages` (where `sqlite_vec` lives) to skip the scan.
