# Context Broker — Project Overview

## What It Is

Context Broker is a context intelligence layer that sits between your conversations and your LLM. It extracts structured facts from conversation transcripts, stores them as a directed acyclic graph (DAG), and retrieves the most relevant subset of that knowledge for any given task — delivering a compact, high-signal context block instead of raw conversation history.

The core insight is that LLM conversations contain lasting decisions, constraints, and implementation details buried in dialogue. Without a tool like this, you either re-explain everything at the start of each new session (expensive and error-prone), paste entire transcripts as context (wasteful and diluted), or rely on the LLM to remember things it can't (it can't).

---

## The Problem It Solves

### Context window exhaustion
A single design session transcript is typically 8,000–20,000 tokens. Multiple sessions compound this. Pasting raw transcripts consumes the majority of a model's context budget before any actual work begins.

### The "lost in the middle" problem
Research has consistently shown that LLMs — even large ones with long context windows — pay less attention to facts buried in the middle of long inputs. A 50-node graph retrieved as 800 tokens of structured, relevant context outperforms the same information buried in a 15,000-token transcript.

### Stale and contradicted facts
Conversations evolve. A decision made in session 1 may be reversed in session 3. Without tracking supersedes relationships, the LLM receives contradictory information and must guess which is current. Context Broker resolves this explicitly at storage time.

### Small model context limits
Local models running on consumer hardware (7B–35B parameters) commonly have 4,096–8,192 token context windows. A full transcript often won't fit at all. Context Broker makes these models viable for complex, multi-session projects by distilling the relevant knowledge down to hundreds of tokens.

---

## How It Works

### Three-stage pipeline

**1. Extract**
A transcript (any saved conversation — design sessions, architecture discussions, code reviews) is sent to a configured LLM. The LLM returns a structured JSON graph: nodes represent discrete facts, edges represent relationships between them. Short LLM-assigned temporary IDs are replaced with stable UUIDs before storage.

```
Transcript → LLM extraction prompt → JSON nodes + edges → SQLite graph
```

**2. Store**
Nodes and edges are merged into a SQLite database. Each node carries:
- The fact itself (a self-contained, context-independent statement)
- A type (`decision`, `constraint`, `implementation`, `question`, `resolved`, `preference`)
- A confidence score (how firmly established the fact is)
- Tags (keywords for retrieval)
- Source location (transcript file + message index)
- A `supersedes` list (IDs of older nodes this one replaces)

**3. Retrieve**
Given a task description, the engine extracts keywords, finds matching nodes by tag overlap, performs BFS graph traversal to collect related nodes, applies a configurable strategy pipeline to filter and rank results, and assembles a markdown context block grouped by node type.

```
Task description → keyword extraction → tag matching → BFS traversal → strategy pipeline → markdown
```

---

## Configuration Reference

### `llm` section

| Key | Default | Description |
|-----|---------|-------------|
| `base_url` | `http://localhost:1234/v1` | OpenAI-compatible API endpoint. Works with LM Studio, Ollama, OpenAI, Anthropic (via proxy), Gemini, and any other OpenAI-format provider. |
| `model` | `qwen3.5-35b-a3b` | Model identifier as expected by the endpoint. |
| `temperature` | `0.1` | Keep low. Extraction requires deterministic, structured output — higher values increase JSON parse failures and hallucinated schema fields. |
| `max_tokens` | `4096` | Maximum response length. Complex transcripts with many nodes can push against this; increase if extractions are truncated mid-JSON. |

### `defaults` section

| Key | Default | Description |
|-----|---------|-------------|
| `hops` | `3` | BFS traversal depth from entry nodes. Controls how far the engine walks the graph from the seed nodes matched by tag. |
| `top_k` | `10` | Maximum nodes returned after traversal and strategies. |
| `format` | `markdown` | Output format (currently only markdown is implemented). |

### `strategies` section

These are the most impactful settings. All are toggleable per-query via `--enable` / `--disable` flags, and overridable in any direction at runtime without changing the config file.

---

## Strategy Reference

### `superseded_pruning` (default: true)

**What it does:** Removes any node that has been explicitly superseded by a newer node. When a decision is reversed mid-conversation, the LLM extracts a new node with `supersedes: [old_node_id]`. This strategy drops the old node from results.

**Why it matters:** Without this, the model receives contradictory facts. For example: "We chose sessions for auth" (session 1) and "We switched to JWT because sessions don't scale" (session 3) would both appear. The model cannot know which is current.

**When to disable:** When you want historical context — e.g., auditing why a decision was made, or reviewing what was tried before.

---

### `confidence_threshold` (default: 0.0, disabled)

**What it does:** Filters out nodes below a minimum confidence score. Confidence is assigned by the extraction LLM at extraction time:
- `0.3–0.5`: Mentioned or discussed, not decided
- `0.6–0.8`: Decided but not yet implemented
- `0.9–1.0`: Implemented and verified

**Recommended value:** `0.6` to exclude speculative discussion. Use `0.8` to restrict to only decided/implemented facts.

**Why it matters:** Conversations contain a lot of noise — ideas floated but rejected, hypotheticals, questions without answers. These get extracted at low confidence and can pollute retrieval results if included.

**When to leave at 0.0:** Early project stages where low-confidence constraints are still relevant, or when you want the full picture including open questions.

---

### `recency_decay` (default: false)

**What it does:** Applies exponential time-based decay to node scores. Older facts score lower. Formula: `score = confidence × 2^(-age_days / half_life_days)`. The decayed score is used for sorting and top_k selection.

**Companion setting:** `recency_half_life_days` (default: 30). A node that is 30 days old has half the score of an otherwise identical node created today. At 60 days, it has a quarter.

**Why it matters:** In long-running projects, early architectural decisions become less operationally relevant than recent ones. This ensures the retrieval surface reflects the current state of the project rather than its history.

**When to disable:** When historical accuracy matters more than recency — audit use cases, debugging regressions, retrospectives.

---

### `token_budget` (default: 0, unlimited)

**What it does:** Packs the highest-scored nodes into a hard token ceiling. Uses a rough 4 characters-per-token estimate. Nodes are included greedily in score order until the budget is reached; remaining nodes are dropped.

**Recommended values:**
- `500` — tight, for very context-limited models
- `1000` — balanced for most use cases
- `2000` — generous, suitable for large models

**Why it matters:** Without a budget, `top_k=10` could return anywhere from 200 to 2,000+ tokens depending on fact verbosity. A token budget makes output size predictable and controllable — critical for local models where exceeding the context window causes silent truncation or failure.

---

### `relevance_scoring` (default: true)

**What it does:** Before BFS traversal, ranks the entry nodes (those matched by tag) by how many of your query keywords they match. Nodes with more overlap are traversed first. Since BFS collects neighbors outward from these seeds, higher-relevance entry points lead to a more on-topic neighborhood.

**Why it matters:** Tag matching is binary — a node either matches a keyword or it doesn't. Without relevance scoring, a node matching one keyword has equal priority as one matching five. Enabling this biases BFS toward the most relevant cluster in the graph.

**When to disable:** When you want broader coverage and don't want to risk missing related context that happens to have weaker direct tag overlap.

---

## Performance Estimates

These estimates are based on the structural properties of the extraction and retrieval pipeline. Empirical benchmarks are planned; figures below represent expected ranges based on token reduction math and known LLM behavior patterns.

### Baseline: No Context Broker

A typical multi-session project transcript is 10,000–25,000 tokens. The model receives all of it as raw context.

---

### Small Local LLMs (7B–35B parameters, 4K–8K context windows)

These models are most dramatically affected by context length. Many cannot accept a full transcript at all without truncation.

**Without Context Broker:**
- 4K context model: Can fit ~3,000 tokens of transcript; remainder is silently dropped
- 8K context model: Can fit ~6,000 tokens; still truncates mid-session transcripts
- Model quality degrades on instruction-following when context is near-full
- No awareness of superseded decisions

**With Context Broker (default settings):**
- Typical retrieval output: 400–900 tokens for top_k=10
- **Token reduction: ~90–95%** vs. raw transcript
- The model receives only the relevant, structured facts for the current task
- Fits comfortably within any context window

**Strategy impact for small local LLMs:**

| Strategy | Impact | Notes |
|----------|--------|-------|
| `token_budget: 500` | **Critical** | Guarantees output fits. Without this, verbosity of extracted facts is unpredictable. |
| `superseded_pruning` | **High** | Small models are easily confused by contradictory facts. Removing stale nodes prevents reasoning errors. |
| `confidence_threshold: 0.6` | **High** | Small models struggle to weigh uncertain information. Filtering to decided facts reduces ambiguity. |
| `relevance_scoring` | **Medium** | Helps when the graph is large; less impactful on small projects. |
| `recency_decay` | **Medium** | Useful for active projects; negligible on fresh graphs. |
| `hops: 2` (reduce from 3) | **Recommended** | Fewer hops = smaller neighborhood = less noise. Small models perform better with a tighter context. |

**Expected improvement over truncated raw transcript:** Quality is very high; latency is not a meaningful benefit. The baseline (truncated transcript) is often worse than no context at all due to mid-sentence cutoffs and missing resolution of decisions. Structured retrieval of even a small number of relevant facts dramatically outperforms this on answer quality. However, extraction adds an upfront LLM call (5–30+ seconds per transcript), and local model prefill is fast enough that smaller inputs don't translate to meaningfully faster generation. The payoff is accuracy and fit, not speed.

---

### Large Online LLMs (GPT-4, Gemini 1.5/2.0, Claude 3.5+, 32K–1M context windows)

These models can technically accept entire transcripts. The benefit is different: cost reduction and attention quality. Latency improvement is a minor side effect of sending fewer tokens, not a primary benefit.

**Without Context Broker:**
- Full transcript fits in context, but costs proportionally
- 20,000-token context costs ~10–20× more per query than a 1,000-token context
- Long-context attention quality degrades for facts buried in the middle of long inputs
- No structured tracking of superseded decisions; model must infer from narrative

**With Context Broker (default settings):**
- Typical retrieval output: 400–900 tokens
- **Token reduction: ~90–95%** vs. raw transcript
- Retrieval is task-targeted, so only relevant facts are present — not all facts
- Supersedes relationships are resolved before the model sees anything

**Strategy impact for large online LLMs:**

| Strategy | Impact | Notes |
|----------|--------|-------|
| `token_budget: 1000–2000` | **Medium** | Useful for cost control across many queries; minimal quality impact if set generously. |
| `superseded_pruning` | **Medium–High** | Large models handle contradiction better but still benefit from clean, unambiguous context. |
| `confidence_threshold: 0.6` | **Medium** | Reduces noise; large models are better at ignoring low-confidence facts but still perform better without them. |
| `relevance_scoring` | **Medium** | Meaningfully improves precision when the graph is large (50+ nodes across multiple transcripts). |
| `recency_decay` | **Low–Medium** | Useful for long-running projects; negligible on short ones. |
| `hops: 3–4` | **Flexible** | Higher hops retrieve broader context; large models can use it effectively. |

**Expected improvement over raw transcript:**
- **Cost:** 85–95% reduction in input token cost per query — the primary financial benefit
- **Time-to-first-token:** Modest improvement proportional to input reduction, but this is a secondary effect, not the reason to use the tool. Extraction adds an upfront LLM call that partially offsets TTFT savings on early queries; the benefit is amortized across many queries against the same graph
- **Recall on specific decisions:** Comparable or better — relevant facts are surfaced at the top of context rather than buried mid-transcript where attention is weakest
- **Cross-session continuity:** High benefit when accumulating 3+ transcripts; a single transcript may not justify the extraction overhead

**Note on latency:** Context Broker's core value is cost reduction and answer quality, not raw speed. The downstream LLM call is faster with a smaller input, but the extraction step adds an upfront cost of its own. Treat any latency reduction as a side effect of token reduction, not a primary feature.

---

## Node Type Reference

| Type | Meaning | Typical Confidence |
|------|---------|-------------------|
| `decision` | A choice between alternatives that was made | 0.7–1.0 |
| `constraint` | A limitation, requirement, or non-negotiable | 0.7–1.0 |
| `implementation` | A concrete technical detail that was established | 0.8–1.0 |
| `question` | An open question not yet resolved | 0.3–0.5 |
| `resolved` | The answer to a previously open question | 0.7–0.9 |
| `preference` | A stated preference for future work, not yet decided | 0.4–0.6 |

Retrieval output is always grouped and ordered by type: decisions first, then constraints, implementations, resolved, preferences, questions last.

---

## Edge Relation Reference

| Relation | Meaning |
|----------|---------|
| `depends_on` | The source node requires the target to function |
| `flows_to` | Data or control flows from source to target |
| `relates_to` | Loosely related concepts in the same domain |
| `supersedes` | The source node replaces or overrides the target |

`supersedes` edges are the most semantically important: they are used both as graph edges and as a field on the superseding node, enabling `superseded_pruning` to work without traversing the full edge table.

---

## Practical Recommendations

**For local models on constrained hardware:**
```yaml
defaults:
  hops: 2
  top_k: 8
strategies:
  superseded_pruning: true
  confidence_threshold: 0.6
  token_budget: 500
  relevance_scoring: true
```

**For large online models where cost matters:**
```yaml
defaults:
  hops: 3
  top_k: 10
strategies:
  superseded_pruning: true
  confidence_threshold: 0.6
  token_budget: 1500
  relevance_scoring: true
```

**For maximum recall (auditing, retrospectives):**
```yaml
defaults:
  hops: 4
  top_k: 20
strategies:
  superseded_pruning: false
  confidence_threshold: 0.0
  token_budget: 0
  relevance_scoring: false
```
