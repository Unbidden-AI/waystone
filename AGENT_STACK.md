# Engram in the Agent Stack

*How Engram fits into the architecture of high-quality, production-grade AI agents*

---

## The Core Problem Engram Solves for Agents

Every high-performing agent eventually hits the same wall: context management. The specific failure modes are:

- Passing the entire conversation history on every call (expensive, slow, degrades quality)
- No structured way to reference results from earlier subtasks without re-injecting them
- No mechanism for multi-agent handoff without redundant re-explanation
- Contradictory facts in context when decisions evolve — agent must guess which is current

Engram addresses all four.

---

## Where Engram Plugs Into the Agent Stack

### 1. Pre-Context Injection (the most important slot)

Every high-quality agent call should begin with: *what do I already know that's relevant to this task?*

Without Engram, the answer is either nothing or the entire transcript. Engram provides a third option: structured, filtered, ranked facts delivered via a single `context_broker_query` call — at 90–95% token reduction vs. raw transcript.

This maps directly to the research-validated pattern: **don't pass entire conversation history; distill it to relevant facts.**

### 2. Episodic Memory Across Subtasks

In a multi-step ReAct loop, subtask results need to persist somewhere they can be retrieved later — without being stuffed into the active context window for every intermediate step.

Engram's per-agent databases + DAG structure is the right architecture for this:
- Extract the result of subtask 3 into Engram
- Retrieve it when subtask 7 needs it
- Zero context overhead for everything in between

### 3. Multi-Agent Shared State

The cross-agent shared knowledge layer is what most memory tools lack. When Agent A (researcher) finishes and Agent B (writer) begins, there should be no re-explanation. Engram is the handoff layer.

Planned v5 features align directly with production multi-agent requirements:
- Context routing rules — agent A receives fact types X and Y, agent B receives Z
- Agent specialization profiles — Engram learns what each agent cares about
- Broadcast vs. targeted context delivery
- Cross-session continuity — seamless pickup across sessions per agent

### 4. The Supersedes Mechanism (uniquely valuable)

No vector database provides this. When an agent makes a decision and later revises it, both facts exist in memory. Without `superseded_pruning`, the agent receives contradictory context and must guess which is current.

Engram resolves contradictions at **storage time**, before the agent ever sees the context. This is a quality mechanism, not just a storage mechanism — and it's one of the most defensible differentiators in the retrieval layer.

---

## Honest Assessment: The One Gap

Current retrieval is keyword tag matching + BFS graph traversal. This is well-suited for structured factual knowledge ("what decisions were made about auth") and outperforms pure vector similarity for typed, relational queries.

The gap is **pure semantic search** — "find everything related to authentication even if not tagged 'auth'." This requires the v2 sqlite-vec work (vector search inside SQLite, zero new dependencies). That work is the right next priority and closes the remaining gap vs. vector-based memory tools.

---

## How This Affects Agent Design

| Agent requirement | Without Engram | With Engram |
|---|---|---|
| Multi-session continuity | Re-explain everything or paste full transcript | Query relevant facts, token-efficient |
| Subtask result reuse | Re-inject full earlier output | Extract → retrieve by task keyword |
| Multi-agent handoff | Full context pass or manual summary | Shared knowledge layer, routed by agent |
| Decision evolution | Model receives contradictions | Superseded pruning resolves at retrieval time |
| Token budget | Bloated context on every call | 90-95% reduction vs raw transcript |

---

## The Positioning Statement

Engram is not adjacent to high-quality agent work — it is the **memory infrastructure layer underneath** whatever agents get built on top of it.

Specialized agents that run more than one session or more than one step are leaving reliability and token efficiency on the table without a structured memory layer. Engram is what makes them:

- **Reliable across sessions** — decisions persist, contradictions resolve
- **Cheap on tokens** — 90-95% context reduction vs transcript injection
- **Capable of clean handoff** — structured state transfer between agents and across sessions

*Context that arrives unbidden. Agents that never have to ask twice.*

---

## Related Documents

- [engram-roadmap.md](engram-roadmap.md) — Full phased roadmap (v1–v8)
- [PROJECT.md](PROJECT.md) — Architecture, strategy pipeline, performance estimates
- [ORCHESTRATOR_PLAN.md](ORCHESTRATOR_PLAN.md) — Multi-agent orchestration design
