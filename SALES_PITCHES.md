# Context Broker — Sales Pitches by Buyer Type

---

## Pitch 1: Teams Using Frontier API Models (GPT-4o, Claude, Gemini)

### The Problem

When you build with GPT-4o, Claude, or Gemini, you pay for every token you send — including the entire conversation history on every single call. A 50-turn coding session doesn't cost like 50 calls. It costs like 1 + 2 + 3 + ... + 50 calls. By the time you're at turn 50, you're sending the equivalent of 1,275 turns worth of tokens.

That cost accumulates fast, especially on an active development team.

### What Context Broker Does

Context Broker keeps only what matters in the context window at any given moment. Instead of replaying the full conversation history on every call, it maintains a structured knowledge graph of your project and retrieves only the facts relevant to the current question — typically 2,000–3,000 tokens regardless of how long the session has been running.

### The Numbers

Assumptions: GPT-4o at $2.50/1M input tokens. Average message length of 200 tokens (including code snippets). 5-developer team, 4 AI sessions per day per developer, 22 working days per month.

**Per session (50 turns):**

| | Without CB | With CB | Savings |
|---|---|---|---|
| Total input tokens | ~255,000 | ~90,000 | ~65% |
| Cost per session | $0.64 | $0.23 | **$0.41** |

**Per month (440 sessions):**

| | Without CB | With CB | Savings |
|---|---|---|---|
| Monthly API spend | ~$281 | ~$99 | **~$182/month** |

Scale to 20 developers or longer sessions (100+ turns) and savings exceed 80%.

The extraction model runs on Gemini Flash at $0.15/1M tokens — the overhead is under $1/month for the whole team.

### The Pitch

> "You're paying your AI provider to remember things it said five minutes ago. Context Broker fixes that. For an active team on GPT-4o, it typically cuts your input token bill by 60–80% — without changing how the model responds or what it knows."

---

## Pitch 2: Local Model Users

### The Standard Local Setup

Most local AI setups look something like this:
- LM Studio or Ollama running a quantized 7B–14B model (Llama 3, Qwen, Mistral)
- 4,096–8,192 token context window
- Used for coding help, documentation, Q&A about a specific project

That setup works great — for about 15 turns. Then the model starts forgetting the beginning of the conversation. By turn 30, critical context has been pushed out of the window entirely. At session end, everything is gone. Tomorrow you start over.

The model doesn't know what you decided last week. It doesn't know you already tried that approach and it didn't work. It doesn't know your current architecture, your constraints, or your team's preferences. Every session is turn one.

### What Changes With Context Broker

Context Broker gives a 4K local model the project memory of a much larger system.

**Session persistence.** The project knowledge graph accumulates on disk. When you start a new session tomorrow, the model already knows what was decided in every session before — not because the transcript is replayed, but because the relevant facts are retrieved and injected at the start.

**Fits every time.** Instead of cramming the full conversation into a 4K window (which stops working around 20 turns), CB keeps the context footprint constant at ~2,000–2,500 tokens per call. A project with 500 turns of history takes the same context space as one with 10.

**No more contradictions.** When you change direction — switching libraries, reversing a decision, updating an architecture — CB marks the old fact as superseded. The model stops suggesting approaches you've already ruled out.

**Gets smarter over time.** A raw-history setup degrades as the project grows (older context gets pushed out). CB improves: more sessions mean a richer knowledge graph, and retrieval precision increases because there's more signal to match against.

### The Pitch

> "A 7B local model has a 4,000-token memory. Context Broker gives it a 4,000-token window into an unlimited project history. Same hardware. Same model. But now it remembers everything — what you built, what you tried, what you decided — across every session, going back as far as the project does."

---

## Pitch 3: Engineering Teams (Quality and Consistency)

### The Problem No One Talks About

Context management tools — including the ones built into Claude Code and ChatGPT — handle the memory problem by summarizing or dropping the oldest messages when the context window fills up. The AI keeps working. But something is quietly lost.

Architectural decisions made in week one get summarized away by week four. The constraint that ruled out a whole class of solutions is gone. The rationale behind a tech choice — the part that would tell you *not* to revisit it — disappears. The model starts giving advice that contradicts what the team already decided, and no one notices until someone builds the wrong thing.

This is the hidden cost of long-running AI-assisted development: **accumulated context debt**. The longer the project, the more the AI "forgets," and the less trustworthy its recommendations become.

### What Context Broker Does for Teams

**Decisions survive.** Every architectural decision, constraint, and rationale is extracted into a persistent knowledge graph. When a new decision supersedes an old one, the old one is marked and removed from future context. The model always sees the current state of the project — not a summary of what it was six weeks ago.

**New contributors onboard instantly.** A developer who joins the project in month three has an AI that already knows the full project history. They don't have to hunt through docs or Slack to understand why something was built a certain way — the AI can tell them, grounded in the actual decisions made during development.

**Cross-session continuity.** Whether your team uses the AI daily or comes back to a project after a two-week break, the knowledge graph is waiting. There's no "warm-up" period where the model has to be re-taught what the project is.

**The AI catches contradictions.** When someone proposes an approach that conflicts with a prior decision, the model surfaces that conflict — because the prior decision is still in the context, not summarized away.

**Retrieval is task-targeted, not time-ordered.** Standard context windows surface what was said *recently*. CB surfaces what's *relevant to the current question*. A constraint established in week one retrieves just as cleanly in week twelve if it applies to the current task — no recency bias.

### The Pitch

> "The longer your project runs, the less your AI understands it. Context Broker reverses that. Every decision your team makes accumulates into a knowledge graph that makes the AI more useful over time — not less. It's the difference between an AI assistant that helps you build the right thing and one that confidently suggests you rebuild what you already have."

---

## Summary Table

| | Frontier API Teams | Local Model Users | Engineering Teams |
|---|---|---|---|
| Primary value | Cost reduction | Feasibility | Quality & consistency |
| Quantified benefit | 60–80% input token savings | Unlimited history in 4K window | Full project memory, no context debt |
| Without CB | Bill grows with session length | Model forgets after ~15 turns | AI contradicts prior decisions |
| With CB | Constant ~2–3K tokens/turn | Same hardware, unlimited project memory | Every decision persists and retrieves by relevance |
