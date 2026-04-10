# Unbidden AI — Marketing Plan

**Company:** Unbidden AI (`unbidden.ai`)
**Product:** Engram — persistent memory for AI development workflows
**Stage:** Pre-launch / first revenue

---

## Part 1: Website Spec — unbidden.ai

### Positioning

Unbidden AI is the company. Engram is the first product. The site should establish a brand identity around the idea of AI that surfaces what you need *before you ask for it* — memory that works the way human intuition does, automatically present when relevant.

The name "Unbidden" does a lot of work here: it implies the assistant brings things up proactively, without being asked. Lean into that framing.

---

### Site Architecture

```
unbidden.ai/               → Company home (brand + product intro)
unbidden.ai/engram         → Engram product page
unbidden.ai/pricing        → Pricing tiers
unbidden.ai/docs           → Getting started, integration guides
unbidden.ai/blog           → Technical posts, case studies
unbidden.ai/changelog      → Product updates
```

---

### Page Specs

#### `/` — Company Home

**Above the fold:**
- Tagline: *"AI that remembers. Context that compounds."*
- Subhead: *"Unbidden builds memory infrastructure for AI development workflows. Your AI gets smarter the longer it works with you."*
- Single CTA: `Try Engram →` (links to `/engram`)
- No pricing on home. Establish the vision first.

**Below the fold:**
- One-liner on the problem: *"Every AI session starts from zero. Unbidden fixes that."*
- Product card: Engram (with brief description and link)
- Optional: "More products coming" placeholder — positions this as a company, not a one-trick site
- Footer: GitHub, docs, pricing, blog, contact email

**Tone:** Technical but not academic. Confident. Not startup-bro. The copy should read like it was written by a developer who is tired of bad tooling.

---

#### `/engram` — Product Page

**Structure:**

1. **Hero** — problem statement, not feature list
   > *"Your AI forgets everything between sessions. Engram doesn't."*

   Subhead: *"Persistent memory for AI-assisted development. Works with any OpenAI-compatible model via MCP or REST API."*

   CTAs: `Get Started (free)` | `View Docs`

2. **Problem section** — three cards, each targeting a buyer type:
   - *Local model users:* "Your 4K context fills up at turn 15. Engram extends that to unlimited."
   - *API teams:* "You're paying for every token in history. Engram cuts that by 60–80%."
   - *Long-running projects:* "Week 6 AI contradicts week 1 decisions. Engram prevents that."

3. **How it works** — three steps, no implementation details:
   - Step 1: *Connect Engram to your AI editor or workflow via MCP or API*
   - Step 2: *Engram automatically extracts and stores what matters from every session*
   - Step 3: *On your next session, Engram surfaces only what's relevant — not everything, not nothing*

4. **Social proof section** (placeholder for now — fill in with real quotes/numbers post-launch):
   - "95% recall across 23 benchmark questions"
   - Benchmark comparison table (Engram vs raw context window)

5. **Integration list:**
   - Claude Code, Cursor, Windsurf, Continue.dev, OpenClaw, any MCP-compatible client
   - REST API for custom integrations

6. **Pricing teaser** — cards linking to `/pricing`

7. **FAQ** (see Part 4 below)

---

#### `/pricing`

| | Free | Pro | Team |
|---|---|---|---|
| Price | $0 | $20/mo | $80/mo |
| Projects | 1 | Unlimited | Unlimited |
| Memory capacity | 500 facts | Unlimited | Unlimited |
| API calls | 10/min | 100/min | 500/min |
| Support | Community | Email | Priority |
| Users | 1 | 1 | Up to 10 |

Notes:
- Free tier requires no payment — just an API key
- Annual pricing: 2 months free (Pro $200/yr, Team $800/yr)
- CTA: "Start free — no credit card required"

---

#### Email Capture

Add a persistent email capture on every page — not a modal, just a slim banner or footer bar: *"Get the benchmark report + release notes → [email] [Subscribe]"*

- **Lead magnet:** The cost calculator breakdown (from Pitch 1) as a one-page PDF, or the full benchmark methodology doc. Developers respond to data, not discounts.
- **Tool:** Buttondown or Resend — both are developer-friendly, cheap, and don't have bloated marketing UX. Avoid Mailchimp for a technical audience.
- **Sequence:** 3 emails over 2 weeks after signup:
  1. Immediate: the lead magnet + "here's what Engram does in 3 bullets"
  2. Day 4: The cost math post (from blog) — value-first, no ask
  3. Day 10: "We just launched" / soft CTA to try the free tier
- **CTA copy:** Avoid "Subscribe to our newsletter." Use *"Get the benchmark report"* or *"Follow the build"* — specific and lower commitment.

---

#### `/docs`

**Priority pages for launch:**
1. Quickstart (5 minutes, local + MCP)
2. MCP Server reference (see below — critical for public distribution)
3. Claude Code integration
4. Cursor/Windsurf integration
5. OpenClaw integration
6. REST API reference
7. CLI reference (`engram` commands)
8. Configuration (`config.yaml`)

Use existing `GETTING_STARTED.md` as the source for #1. Keep docs in-repo and render them statically — don't build a docs platform until you need to.

---

#### MCP Server Documentation (Priority — Required for Public Distribution)

Since Engram is distributed primarily as an MCP server, this is the most important documentation to get right. Developers evaluating an MCP server will check the docs before trying it — a poorly documented MCP integration is a conversion killer.

**Required MCP docs pages:**

**1. MCP Server Overview**
- What transport modes are supported (stdio, SSE, HTTP)
- Authentication model (API key, local-only, etc.)
- Compatibility matrix: which MCP clients have been tested (Claude Code, Cursor, Windsurf, Continue.dev, OpenClaw)

**2. Installation & Config Reference**
- Install command (`npm install -g engram` / `pip install engram` / binary download — whichever applies)
- Full annotated config block for each supported client:

```json
// claude_code settings.json
{
  "mcpServers": {
    "engram": {
      "command": "engram",
      "args": ["serve"],
      "env": { "ENGRAM_API_KEY": "your-key", "ENGRAM_PROJECT": "my-project" }
    }
  }
}
```

Provide a copy-paste block for every supported client. Developers shouldn't have to guess the config shape.

**3. Tool Reference (the MCP tools Engram exposes)**

Document every tool the MCP server registers:

| Tool | Description | Required params | Returns |
|------|-------------|-----------------|---------|
| `engram_query` | Retrieve relevant context for the current task | `query: string` | Ranked fact list + relevance scores |
| `engram_synthesize` | Extract and store facts from the current session | `transcript: string` | Stored fact count, superseded count |
| `engram_stats` | Project memory stats | none | Fact count, project age, last sync |

Include example inputs and outputs for each tool — this is what developers copy from when writing agent instructions.

**4. Agent Instruction Templates**

Provide ready-to-paste instruction blocks for each supported client showing how to wire Engram into the agent's system prompt or tool-use instructions. Example:

```
# Memory (Engram)
At the start of each session, call engram_query with a summary of the current task.
After completing significant work, call engram_synthesize with the session transcript.
Never write to MEMORY.md — use Engram instead.
```

**5. Hooks Integration**
For Claude Code specifically: show the UserPromptSubmit hook config that auto-queries Engram at session start. This is the zero-friction setup and should be the first thing shown.

**6. Troubleshooting**
- "engram not found" — PATH issues, install verification
- "no facts returned" — project name mismatch, empty memory
- Rate limiting — what the errors look like, how to handle them
- Local vs. hosted mode differences

**7. Privacy & Data Flow**
One page explaining exactly what data leaves the machine in each mode (local CLI vs. hosted API). Developers deploying on client projects will ask this before installing anything.

---

#### `/blog`

Launch with 2–3 posts ready to go. These double as Reddit posts (see Part 2).

**Planned posts:**
1. *"Why your AI forgets everything (and what to do about it)"* — problem education, no product pitch
2. *"How we got 95% recall on a 23-question benchmark with a local model"* — technical credibility post
3. *"The real cost of long context windows: a breakdown"* — cost analysis, targets API teams

---

### Design Direction

- Dark background, monospace accent font (project memory = dev tool)
- Minimal — no carousels, no animations, no gradients
- Code snippet on the hero showing a single `engram extract` or MCP config block
- Primary color: pick one accent (not blue — everyone is blue)
- Mobile-responsive but desktop-first (developers are on desktop)

---

## Part 2: Reddit Strategy

### Philosophy

Reddit developers can smell a marketing account from a mile away. The playbook is:
1. **Be the founder, not the brand.** Post as yourself.
2. **Lead with the problem, not the product.** Posts that open with "I built X" perform worse than posts that open with "X was driving me crazy so I fixed it."
3. **Give value first.** Every post should be worth reading even if they never click the link.
4. **Engage authentically in comments.** Respond to every comment for the first 48 hours. The engagement signal matters for visibility.

---

### Target Subreddits

#### r/LocalLLaMA (~200K members)
**Who's here:** People running local models (Ollama, LM Studio, mlx). Context window limitations are a daily frustration.

**Angle:** "I got a 7B model to remember a 6-month project"

**Post template:**
```
Title: I built a memory layer that gives a 7B local model persistent project history

Been running local models for a year. The 4K context wall was killing me —
every session starts cold, model forgets what we built last week, suggests
approaches I already ruled out.

Built a tool called Engram that extracts the decisions and facts from each
session and stores them. Next session it retrieves only what's relevant —
about 1,200 tokens, regardless of project age.

Tested it on a project with 835 conversation turns across 6 months.
Retrieval held at 95% recall. The model knows about decisions from
month 1 without seeing the transcript.

[link to unbidden.ai/engram or GitHub]

Happy to answer questions about how the retrieval side works.
```

**Good follow-up comments to engage:**
- Questions about what model to use for extraction → "I use Gemini Flash because it's cheap; local-only option is in the roadmap"
- "How is this different from RAG?" → explain the distinction (session memory vs. document retrieval)
- "Does it work with Ollama?" → yes, answer with specifics

---

#### r/ClaudeAI (~150K) + r/OpenClaw (~40K)
**Who's here:** Claude Code and OpenClaw users. Memory management is a known pain.

**Angle for r/ClaudeAI:** "I use Engram to give Claude Code cross-session memory"

**Angle for r/OpenClaw:** Direct pitch on the OpenClaw problem (see Pitch 4)

**Post template (r/OpenClaw):**
```
Title: OpenClaw's 150KB memory limit was killing my long-running projects —
here's what I replaced it with

Three months into a project, MEMORY.md hit the cap. Older entries started
disappearing. The model was working from an increasingly stale picture of
what we'd built.

Built Engram as a replacement. Instead of a flat file, it stores each fact
individually and retrieves only what's relevant to the current question.
Memory size doesn't matter — it's been tested at 11,000+ stored facts with
no degradation.

Integration is one config block in openclaw.json. Existing MEMORY.md content
imports in a single command.

[link]

If anyone's hit the same wall, happy to walk through the migration.
```

---

#### r/vibecoding (~80K) + r/ChatGPTCoding (~200K)
**Who's here:** Vibe coders, AI-first developers. Less technical, more focused on productivity.

**Angle:** "I gave my AI a memory and my project velocity doubled"

**Post template:**
```
Title: My AI kept forgetting what we'd built. I fixed it.

Was deep into a project with Claude and kept running into the same problem:
at some point the model would suggest something I'd already tried, or
contradict a decision we made three sessions ago. It had forgotten.

Built a tool that solves this. It watches your sessions, extracts the key
decisions and facts, and injects them at the start of the next session —
only the relevant ones, not everything.

95% recall on decisions across a 6-month project. The model stopped
suggesting things I'd already ruled out.

It's called Engram. Free tier available. Works with Claude Code, Cursor,
Windsurf, and basically anything that supports MCP.

[link]
```

---

#### r/programming + r/SoftwareEngineering
**Who's here:** Broader developer audience, more skeptical.

**Angle:** Long-form problem post, lead with cost analysis

**Post template:**
```
Title: The hidden cost in your AI API bill: you're paying for history

Every call to GPT-4o or Claude includes your entire conversation history.
By turn 50, you've sent ~255,000 input tokens for a session that started
at 200 tokens/turn.

[insert cost table from Pitch 1]

Built a tool that reduces this. It keeps ~2,000–3,000 tokens in context
regardless of session length by replacing the raw history with retrieved
relevant facts.

For a 5-person team on GPT-4o: $182/month savings on a 50-turn average session.

[link to blog post on cost analysis — send to blog first, Reddit second]
```

---

### Timing and Cadence

- **Don't post everywhere at once.** Start with the subreddit most relevant to your current feature set.
- **Recommended order:** r/LocalLLaMA first (most specific pain, most technical), then r/OpenClaw, then r/ClaudeAI, then broader subs.
- **Wait 2–4 weeks between major posts** in the same subreddit to avoid looking like a marketing account.
- **Participate in threads between posts.** Answer questions about local models, context windows, MCP — not always about Engram.

---

## Part 3: Other Channels

### Hacker News — Show HN

**When:** After Reddit traction, before or alongside Product Hunt.

**Format:** `Show HN: Engram – persistent memory layer for AI development workflows`

**HN-specific rules:**
- The HN crowd will ask hard technical questions. Have answers ready for: architecture decisions, benchmark methodology, comparison to vector DBs, comparison to simple summarization.
- Don't oversell. HN respects "this solves my problem" more than "this is the future of AI."
- Post on a weekday morning (9–10am ET).

---

### Product Hunt

**When:** After you have 20–30 users and a few testimonials.

**Prep work (2 weeks before launch):**
- Build a hunter network — find an active PH hunter to post for you
- Line up 10 people who will upvote and comment on launch day (customers, friends who've used the product)
- Prepare a GIF/video demo (30–60 seconds showing a session, then the next session "remembering" it)
- Write a maker comment that tells the founding story

**Goal:** Top 5 of the day. Not necessarily #1 — top 5 gets you in the weekly digest.

---

### Discord Communities

**Where to be:**
| Server | Angle |
|---|---|
| Anthropic / Claude Code Discord | Claude Code memory integration |
| Cursor Discord | Cross-session project context |
| Continue.dev Discord | Open-source local-model users |
| OpenClaw Discord | Direct replacement pitch |
| LangChain / LlamaIndex | Developer tooling ecosystem |

**Strategy:** Don't post links. Answer questions about context management, memory, long sessions. When directly asked for a solution, mention Engram. Build reputation first.

---

### Twitter / X

**Audience:** AI developer Twitter is real and active. Best for brand building, not direct conversion.

**Content mix:**
- 40% problem education ("Here's why your AI forgets things")
- 30% behind-the-scenes / building in public (benchmark results, interesting failures)
- 20% product updates (changelog posts, new integrations)
- 10% engagement bait (polls, questions)

**Building in public posts that work well:**
- "Hit 95% recall on our benchmark today. Here's what finally worked."
- "Our worst model benchmark result: [table]. This is why we don't recommend [model X]."
- "The context window cost calculator I built for our landing page — the math surprised me."

---

### MCP Registries

**Submit Engram to:**
- Anthropic's official MCP directory (highest signal for Claude users)
- Cursor marketplace
- Windsurf marketplace
- `awesome-mcp-servers` GitHub repo (high-traffic community list)
- glama.ai MCP directory

**These are discovery channels, not conversion channels.** Developers browse these lists looking for tools that solve specific problems. Being listed here is table stakes.

---

### 21st.dev — Integration Partnership

**What they are:** YC W26 company (1.4M developers, 200K MAU) building agent infrastructure — a React component registry, a Magic MCP server for UI generation in IDEs (Cursor, Windsurf, Cline), and an agent SDK with built-in memory, observability, and sandboxed execution.

**Why Engram fits:** Their agent SDK includes session-scoped memory, but no persistent cross-session fact graph. Engram is the layer underneath — long-term structured memory that survives across sessions. Their observability captures traces and token cost; Engram makes those traces semantically queryable. The audiences are identical: developers building production AI agents.

**How to pursue:**
1. **MCP integration first** — add Engram as a supported memory backend example in their agent SDK docs or as a companion MCP server in their Magic MCP ecosystem. Low friction, high visibility.
2. **Blog post / co-marketing** — a technical post on "short-term vs long-term agent memory" written jointly or cross-promoted. Reaches their 200K MAU directly.
3. **Direct outreach** — Serafim Korablev (CEO) or Sergey Bunas (CTO). Frame as: "your SDK ships session memory; Engram is the persistent layer for users who need facts to survive across sessions."

**Distribution value:** 21st.dev has the developer audience Engram needs. A listing or integration mention in their ecosystem is worth more than a Product Hunt launch.

---

### dev.to / Hashnode

**When:** Alongside or after Reddit posts. Repurpose the same content.

**Posts to write:**
1. *"Building persistent memory for AI coding assistants"* — technical deep-dive
2. *"The 60–80% token cost reduction you're leaving on the table"* — practical cost post
3. *"Why AI forgets your architecture decisions (and how to fix it)"* — problem/solution

dev.to has SEO value. Posts rank for long-tail searches like "AI memory between sessions" and "context window too small."

---

### GitHub

- Ensure README is polished — this is often a developer's first impression
- Add to relevant `awesome-*` lists: `awesome-mcp`, `awesome-llm-tools`
- Keep Issues active — responding quickly signals the project is alive
- Consider GitHub Sponsors as a free-tier monetization signal before charging

---

## Part 4: FAQ (for Reddit, website, and pitches)

**Q: How is this different from RAG?**
RAG retrieves from a document corpus — you put documents in, it fetches chunks when asked. Engram builds memory from *conversations* — it watches what you're working on, extracts decisions and facts as you go, and surfaces them in future sessions automatically. It's session memory, not document search.

**Q: Does it work with local models (Ollama, LM Studio)?**
Yes. Engram works with any OpenAI-compatible endpoint. Local models are used for both the extraction (if you configure it that way) and the downstream AI assistant. Gemini Flash is the recommended extraction model for accuracy; local extraction is supported.

**Q: How is this different from the built-in memory in Claude Code / ChatGPT?**
Built-in memory tools summarize old context or drop it when the window fills. This means architectural decisions from early in a project eventually disappear. Engram extracts structured facts — it doesn't summarize or discard. A decision from week 1 retrieves just as accurately in week 12 as it did on day 2.

**Q: Does my conversation data leave my machine?**
Local CLI mode: nothing leaves your machine. The MCP server runs locally, extraction calls your configured endpoint (local or cloud), the memory store is a local SQLite file. Hosted API mode: transcripts are sent to the Engram API for extraction and stored in your project's database on our servers. See the privacy policy for details.

**Q: What AI editors does it support?**
Claude Code, Cursor, Windsurf, Continue.dev, OpenClaw, and any editor with MCP support. REST API mode works with anything that can make HTTP calls.

**Q: How does the free tier work?**
Free tier gives you 1 project and 500 stored facts (enough for a meaningful project). No credit card required. Rate-limited to 10 API calls/min. Upgrade when you hit the ceiling.

**Q: What happens to old facts when I change direction?**
When a new decision supersedes an old one, Engram marks the old fact as retired. It stops appearing in retrieval. You see the current state of the project — not a history of every decision including the ones you reversed.

**Q: How accurate is the memory extraction?**
95% recall on our 23-question benchmark across three synthetic projects (api design, auth system, data pipeline). Full benchmark methodology and results are in the repo under `benchmarks/`.

**Q: Can I import my existing MEMORY.md from OpenClaw?**
Yes. `engram extract` can process a MEMORY.md as a source document. Run it once to seed your Engram project with existing facts before switching.

**Q: What does it cost to run?**
The extraction model (Gemini Flash) costs ~$0.15/1M tokens. A typical 50-turn session generates ~5,000 tokens of transcript. Extraction cost per session: ~$0.001. For a 5-person team doing 440 sessions/month: under $0.50/month in extraction costs.

---

## Part 5: Sales Pitches by Buyer Type

---

### Pitch 1: Teams Using Frontier API Models (GPT-4o, Claude, Gemini)

#### The Problem

When you build with GPT-4o, Claude, or Gemini, you pay for every token you send — including the entire conversation history on every single call. A 50-turn coding session doesn't cost like 50 calls. It costs like 1 + 2 + 3 + ... + 50 calls. By the time you're at turn 50, you're sending the equivalent of 1,275 turns worth of tokens.

That cost accumulates fast, especially on an active development team.

#### What Engram Does

Engram keeps only what matters in the context window at any given moment. Instead of replaying the full conversation history on every call, it maintains a persistent memory index of your project and retrieves only the facts relevant to the current question — typically 2,000–3,000 tokens regardless of how long the session has been running.

#### The Numbers

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

#### The Pitch

> "You're paying your AI provider to remember things it said five minutes ago. Engram fixes that. For an active team on GPT-4o, it typically cuts your input token bill by 60–80% — without changing how the model responds or what it knows."

---

### Pitch 2: Local Model Users

#### The Standard Local Setup

Most local AI setups look something like this:
- LM Studio or Ollama running a quantized 7B–14B model (Llama 3, Qwen, Mistral)
- 4,096–8,192 token context window
- Used for coding help, documentation, Q&A about a specific project

That setup works great — for about 15 turns. Then the model starts forgetting the beginning of the conversation. By turn 30, critical context has been pushed out of the window entirely. At session end, everything is gone. Tomorrow you start over.

The model doesn't know what you decided last week. It doesn't know you already tried that approach and it didn't work. It doesn't know your current architecture, your constraints, or your team's preferences. Every session is turn one.

#### What Changes With Engram

Engram gives a 4K local model the project memory of a much larger system.

**Session persistence.** Project knowledge accumulates on disk. When you start a new session tomorrow, the model already knows what was decided in every session before — not because the transcript is replayed, but because the relevant facts are retrieved and injected at the start.

**Fits every time.** Instead of cramming the full conversation into a 4K window, Engram keeps the context footprint constant at ~2,000–2,500 tokens per call. A project with 500 turns of history takes the same context space as one with 10.

**No more contradictions.** When you change direction, Engram marks the old fact as superseded. The model stops suggesting approaches you've already ruled out.

**Gets smarter over time.** A raw-history setup degrades as the project grows. Engram improves: more sessions mean richer memory, and retrieval precision increases because there's more signal to match against.

#### The Pitch

> "A 7B local model has a 4,000-token memory. Engram gives it a 4,000-token window into an unlimited project history. Same hardware. Same model. But now it remembers everything — what you built, what you tried, what you decided — across every session, going back as far as the project does."

---

### Pitch 3: Engineering Teams (Quality and Consistency)

#### The Problem No One Talks About

Context management tools — including the ones built into Claude Code and ChatGPT — handle the memory problem by summarizing or dropping the oldest messages when the context window fills up. The AI keeps working. But something is quietly lost.

Architectural decisions made in week one get summarized away by week four. The constraint that ruled out a whole class of solutions is gone. The rationale behind a tech choice — the part that would tell you *not* to revisit it — disappears. The model starts giving advice that contradicts what the team already decided, and no one notices until someone builds the wrong thing.

This is the hidden cost of long-running AI-assisted development: **accumulated context debt**. The longer the project, the more the AI "forgets," and the less trustworthy its recommendations become.

#### What Engram Does for Teams

**Decisions survive.** Every architectural decision, constraint, and rationale is extracted and persisted. When a new decision supersedes an old one, the old one is retired. The model always sees the current state of the project.

**New contributors onboard instantly.** A developer who joins in month three has an AI that already knows the full project history.

**Cross-session continuity.** Whether your team uses the AI daily or comes back after a two-week break, the accumulated memory is waiting.

**The AI catches contradictions.** When someone proposes an approach that conflicts with a prior decision, the model surfaces that conflict — because the prior decision is still in context.

**Retrieval is task-targeted, not time-ordered.** Engram surfaces what's *relevant to the current question*. A constraint established in week one retrieves just as cleanly in week twelve.

#### The Pitch

> "The longer your project runs, the less your AI understands it. Engram reverses that. Every decision your team makes accumulates into persistent memory that makes the AI more useful over time — not less. It's the difference between an AI assistant that helps you build the right thing and one that confidently suggests you rebuild what you already have."

---

### Pitch 4: OpenClaw Users

#### The Problem

OpenClaw's memory system is one of the best flat-file approaches available. But it has a ceiling. After 150KB of memory (~37,500 tokens), OpenClaw starts truncating. Older entries disappear. Long-running projects quietly lose their history.

Even before that cap, OpenClaw loads your entire `MEMORY.md` into every session. Asking about a Python bug? The model gets every fact about your email setup, your calendar agent, your home automation config — all of it, every time.

And when facts change, the old fact stays in `MEMORY.md` alongside the new one. The model sees both. It may act on either.

#### What Engram Does Differently

**Structured memory, not a flat file.** Each fact is stored individually and queryable independently.

**Retrieves what's relevant, not everything.** A 3-year-old project retrieves the same ~1,200 tokens of relevant context as a 3-day-old one.

**No ceiling.** Engram's memory grows without bound. Tested at 11,000+ stored facts with no degradation.

**Superseded facts disappear from context.** When you change direction, the old fact is retired.

**Plugs in via MCP.** One config block in `openclaw.json`. No workflow changes.

#### The Numbers

| Scenario | OpenClaw memory tokens/session | Engram tokens/session | Monthly savings (Claude Sonnet) |
|---|---|---|---|
| Early project (20KB) | 5,000 | ~1,200 | ~$4 |
| Mature project (75KB) | 18,750 | ~1,200 | ~$15 |
| At the 150KB cap | 37,500 | ~1,200 | ~$30 |
| 5-agent team at cap | 187,500 | ~6,000 | ~$150 |

Recall quality: OpenClaw's semantic search degrades as MEMORY.md grows. Engram's retrieval holds at **95% recall** regardless of memory size (verified across 23 benchmark questions).

#### The Pitch

> "OpenClaw's memory tops out at 150KB. After that, your agent starts forgetting. Engram removes that ceiling — your project history grows without bound, and your agent only sees what's relevant to the current question, not everything you've ever told it. One MCP config line. No workflow changes."

#### Integration (30 minutes)

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

---

## Part 6: Launch Sequencing Timeline

### Pre-Launch (Weeks 1–3)

**Goal:** Infrastructure ready, first content live, email list seeded.

- [ ] Polish README — benchmark table, install steps, MCP config example, screenshot/GIF
- [ ] Set up email capture on site with lead magnet (benchmark report or cost calculator PDF)
- [ ] Write Blog Post 1: *"Why your AI forgets everything (and what to do about it)"* — publish to dev.to, hold Reddit version
- [ ] Submit to `awesome-mcp-servers` GitHub repo
- [ ] Submit to Anthropic MCP directory and glama.ai
- [ ] Share blog post link with 5–10 personal contacts who use AI coding tools — get first real feedback

---

### Week 4 — First Public Post

**Target:** r/LocalLLaMA

Use the "I built a memory layer that gives a 7B local model persistent project history" template. This subreddit has the most specific pain and the most technical audience — best first signal on whether the positioning lands.

**Watch for:** Questions about extraction model choice, Ollama compatibility, RAG comparison. Have answers ready. Respond to every comment in the first 24 hours.

---

### Weeks 5–6 — Second Wave

**Target:** r/OpenClaw

OpenClaw users are the highest-intent audience. Use the "150KB cap" template. This is a direct replacement pitch — shorter funnel to paid.

Also: write Blog Post 2 (*"How we got 95% recall on a 23-question benchmark"*), publish to dev.to. Cross-post the technical content to r/MachineLearning if reception is positive.

---

### Weeks 7–8 — Broader Developer Audience

**Target:** r/ClaudeAI, r/vibecoding, r/ChatGPTCoding

Adapt messaging to be less technical. Lead with outcomes ("my AI stopped contradicting itself") rather than architecture.

Start participating in Discord communities (Cursor, Continue.dev, OpenClaw) — not posting links yet, just answering questions about context management.

---

### Month 2 — Hacker News

**When:** After at least one Reddit post with meaningful upvotes (50+) and 20+ signups.

Submit: `Show HN: Engram – persistent memory layer for AI development workflows`

HN is high-variance — a great Show HN can drive 200+ signups in 24 hours. Be on standby to answer technical comments for the full day. Have the benchmark methodology docs ready to link.

---

### Month 2–3 — Product Hunt

**When:** 20–30 active users, at least 2 real testimonials, demo video ready.

**Prep (2 weeks before):**
- 30–60 second screen recording: start a session → Engram captures it → start a new session → context is already there
- Maker comment written (the founding story: the specific frustration that caused you to build this)
- 10 people lined up to upvote on launch day

**Goal:** Top 5 of the day → weekly digest inclusion → 2nd wave of discovery traffic.

---

### Ongoing (Month 2+)

- Twitter/X: building-in-public posts on a cadence (2–3/week). Benchmark results, interesting edge cases, new integrations.
- Email list: monthly update with what shipped, what's next, and one technical insight.
- Discord: keep participating. When users mention Engram organically, that's the signal to post more directly.
- Blog Post 3 (*"The real cost of long context windows"*) — publish after HN, use it as the Reddit post for r/programming and r/SoftwareEngineering.

---

## Part 7: Success Metrics

### Pre-Launch (Weeks 1–3)
| Metric | Target | What it tells you |
|--------|--------|-------------------|
| GitHub stars | 50+ | Enough signal to look alive |
| Email signups | 30+ | Baseline interest before launch post |
| README → docs CTR | >20% | Positioning is clear enough to drive action |

### Launch (First Reddit post through month 1)
| Metric | Target | What it tells you |
|--------|--------|-------------------|
| Reddit post upvotes | 100+ on first post | Positioning resonates with target audience |
| Free tier signups | 50 in first 30 days | Distribution is working |
| Activation rate | >40% of signups actually configure + use it | Onboarding is not a blocker |
| Email open rate | >35% | List is quality, not noise |

### Month 2–3
| Metric | Target | What it tells you |
|--------|--------|-------------------|
| Paid conversions | 5+ in month 2 | Product has real value, not just curiosity signups |
| Free → paid conversion rate | >5% | Pricing and value prop are aligned |
| Churn (paid) | <10%/month | Retention is healthy |
| DAU/MAU ratio | >20% | Product is being used regularly, not just installed |

### Month 3–6
| Metric | Target | What it tells you |
|--------|--------|-------------------|
| MRR | $500+ | Enough to cover infrastructure + validate the business |
| Organic signups (non-Reddit) | >30% of new signups | SEO/word-of-mouth is building |
| NPS | >40 | Users would recommend it — advocacy is possible |
| Support requests resolved without docs change | <50% | Docs need work if this is low |

### The one metric that matters most (pre-$1K MRR)

**Activation rate** — the % of signups who actually configure Engram and run at least one session. A high signup rate with low activation means the marketing is working but the product or onboarding isn't. Fix that before scaling distribution.

If activation is above 40%, double down on distribution. If it's below 20%, stop marketing and fix the setup experience.
