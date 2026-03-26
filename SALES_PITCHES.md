# Engram — Sales Pitches by Buyer Type

---

## Pitch 1: Teams Using Frontier API Models (GPT-4o, Claude, Gemini)

### The Problem

When you build with GPT-4o, Claude, or Gemini, you pay for every token you send — including the entire conversation history on every single call. A 50-turn coding session doesn't cost like 50 calls. It costs like 1 + 2 + 3 + ... + 50 calls. By the time you're at turn 50, you're sending the equivalent of 1,275 turns worth of tokens.

That cost accumulates fast, especially on an active development team.

### What Engram Does

Engram keeps only what matters in the context window at any given moment. Instead of replaying the full conversation history on every call, it maintains a persistent memory index of your project and retrieves only the facts relevant to the current question — typically 2,000–3,000 tokens regardless of how long the session has been running.

### The Numbers

Assumptions: GPT-4o at $2.50/1M input tokens. Average message length of 200 tokens (including code snippets). 5-developer team, 4 AI sessions per day per developer, 22 working days per month.

**Per session (50 turns):**

| | Without Engram | With Engram | Savings |
|---|---|---|---|
| Total input tokens | ~255,000 | ~90,000 | ~65% |
| Cost per session | $0.64 | $0.23 | **$0.41** |

**Per month (440 sessions):**

| | Without Engram | With Engram | Savings |
|---|---|---|---|
| Monthly API spend | ~$281 | ~$99 | **~$182/month** |

Scale to 20 developers or longer sessions (100+ turns) and savings exceed 80%.

The extraction model runs on Gemini Flash at $0.15/1M tokens — the overhead is under $1/month for the whole team.

### The Pitch

> "You're paying your AI provider to remember things it said five minutes ago. Engram fixes that. For an active team on GPT-4o, it typically cuts your input token bill by 60–80% — without changing how the model responds or what it knows."

---

## Pitch 2: Local Model Users

### The Standard Local Setup

Most local AI setups look something like this:
- LM Studio or Ollama running a quantized 7B–14B model (Llama 3, Qwen, Mistral)
- 4,096–8,192 token context window
- Used for coding help, documentation, Q&A about a specific project

That setup works great — for about 15 turns. Then the model starts forgetting the beginning of the conversation. By turn 30, critical context has been pushed out of the window entirely. At session end, everything is gone. Tomorrow you start over.

The model doesn't know what you decided last week. It doesn't know you already tried that approach and it didn't work. It doesn't know your current architecture, your constraints, or your team's preferences. Every session is turn one.

### What Changes With Engram

Engram gives a 4K local model the project memory of a much larger system.

**Session persistence.** Project knowledge accumulates on disk. When you start a new session tomorrow, the model already knows what was decided in every session before — not because the transcript is replayed, but because the relevant facts are retrieved and injected at the start.

**Fits every time.** Instead of cramming the full conversation into a 4K window (which stops working around 20 turns), Engram keeps the context footprint constant at ~2,000–2,500 tokens per call. A project with 500 turns of history takes the same context space as one with 10.

**No more contradictions.** When you change direction — switching libraries, reversing a decision, updating an architecture — Engram marks the old fact as superseded. The model stops suggesting approaches you've already ruled out.

**Gets smarter over time.** A raw-history setup degrades as the project grows (older context gets pushed out). Engram improves: more sessions mean richer memory, and retrieval precision increases because there's more signal to match against.

### The Pitch

> "A 7B local model has a 4,000-token memory. Engram gives it a 4,000-token window into an unlimited project history. Same hardware. Same model. But now it remembers everything — what you built, what you tried, what you decided — across every session, going back as far as the project does."

---

## Pitch 3: Engineering Teams (Quality and Consistency)

### The Problem No One Talks About

Context management tools — including the ones built into Claude Code and ChatGPT — handle the memory problem by summarizing or dropping the oldest messages when the context window fills up. The AI keeps working. But something is quietly lost.

Architectural decisions made in week one get summarized away by week four. The constraint that ruled out a whole class of solutions is gone. The rationale behind a tech choice — the part that would tell you *not* to revisit it — disappears. The model starts giving advice that contradicts what the team already decided, and no one notices until someone builds the wrong thing.

This is the hidden cost of long-running AI-assisted development: **accumulated context debt**. The longer the project, the more the AI "forgets," and the less trustworthy its recommendations become.

### What Engram Does for Teams

**Decisions survive.** Every architectural decision, constraint, and rationale is extracted and persisted. When a new decision supersedes an old one, the old one is retired from future context. The model always sees the current state of the project — not a summary of what it was six weeks ago.

**New contributors onboard instantly.** A developer who joins the project in month three has an AI that already knows the full project history. They don't have to hunt through docs or Slack to understand why something was built a certain way — the AI can tell them, grounded in the actual decisions made during development.

**Cross-session continuity.** Whether your team uses the AI daily or comes back to a project after a two-week break, the accumulated memory is waiting. There's no "warm-up" period where the model has to be re-taught what the project is.

**The AI catches contradictions.** When someone proposes an approach that conflicts with a prior decision, the model surfaces that conflict — because the prior decision is still in context, not summarized away.

**Retrieval is task-targeted, not time-ordered.** Standard context windows surface what was said *recently*. Engram surfaces what's *relevant to the current question*. A constraint established in week one retrieves just as cleanly in week twelve if it applies to the current task — no recency bias.

### The Pitch

> "The longer your project runs, the less your AI understands it. Engram reverses that. Every decision your team makes accumulates into persistent memory that makes the AI more useful over time — not less. It's the difference between an AI assistant that helps you build the right thing and one that confidently suggests you rebuild what you already have."

---

## Pitch 4: OpenClaw Users

### The Problem

OpenClaw's memory system is one of the best flat-file approaches available. But it has a ceiling — literally. After 150KB of memory across all files (~37,500 tokens), OpenClaw starts truncating. Older entries disappear. Long-running projects quietly lose their history.

Even before hitting that cap, there's a subtler problem: OpenClaw loads your entire `MEMORY.md` into every session, regardless of what you're actually doing. Asking about a Python bug? The model gets every fact about your email setup, your calendar agent, your home automation config — all of it, every time. The context fills up with things that don't matter for this question.

And when facts change — you switch approaches, update a config, reverse a decision — the old fact stays in `MEMORY.md` alongside the new one. The model sees both. It may act on either.

### What Engram Does Differently

**Structured memory, not a flat file.** Instead of appending facts to markdown, Engram stores each fact with type, context, and relevance signals. A fact about your auth setup and a fact about your API design don't blur together — they're queryable independently.

**Retrieves what's relevant, not everything.** Instead of injecting your entire memory at session start, Engram retrieves only what matters right now. A 3-year-old project retrieves the same ~1,200 tokens of relevant context as a 3-day-old one.

**No ceiling.** Engram's memory grows without bound. It's been tested with 11,000+ stored facts and retrieval quality is unchanged. You never lose history.

**Superseded facts disappear from context.** When you change direction, the old fact is retired. It no longer appears in retrieval. The model sees your current decisions, not your full decision history including the ones you reversed.

**Plugs in via MCP.** OpenClaw has native MCP support. Adding Engram is one config block. Your agents call `engram_query`, `engram_synthesize`, and `engram_stats` as tools — no rewiring your workflow.

### The Numbers

| Scenario | OpenClaw memory tokens/session | Engram tokens/session | Monthly savings (Claude Sonnet) |
|---|---|---|---|
| Early project (20KB) | 5,000 | ~1,200 | ~$4 |
| Mature project (75KB) | 18,750 | ~1,200 | ~$15 |
| At the 150KB cap | 37,500 | ~1,200 | ~$30 |
| 5-agent team at cap | 187,500 | ~6,000 | ~$150 |

Recall quality: OpenClaw's semantic search degrades as MEMORY.md grows. Engram's retrieval holds at **95% recall** regardless of memory size (verified across 23 benchmark questions).

### The Pitch

> "OpenClaw's memory tops out at 150KB. After that, your agent starts forgetting. Engram removes that ceiling — your project history grows without bound, and your agent only sees what's relevant to the current question, not everything you've ever told it. One MCP config line. No workflow changes."

### Integration (30 minutes)

1. Add Engram as an MCP server in `openclaw.json`
2. Replace `write to MEMORY.md` agent instructions with `call engram_synthesize`
3. Add an `engram_query` call at session start — or let the UserPromptSubmit hook do it automatically

Existing `MEMORY.md` content can be imported as seed facts in a single `engram extract` run.

---

## Summary Table

| | Frontier API Teams | Local Model Users | Engineering Teams | OpenClaw Users |
|---|---|---|---|---|
| Primary value | Cost reduction | Feasibility | Quality & consistency | Memory ceiling + recall |
| Quantified benefit | 60–80% input token savings | Unlimited history in 4K window | Full project memory, no context debt | 95% recall, no 150KB cap, $15–$150/mo savings |
| Without Engram | Bill grows with session length | Model forgets after ~15 turns | AI contradicts prior decisions | Memory truncates, stale facts conflict, search degrades |
| With Engram | Constant ~2–3K tokens/turn | Same hardware, unlimited project memory | Every decision persists and retrieves by relevance | Unlimited memory, relevant-only retrieval, supersedes cleans up stale facts |
