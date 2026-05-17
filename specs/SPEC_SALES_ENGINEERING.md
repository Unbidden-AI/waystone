# SPEC: `solution_design` Domain Profile (Sales Engineering)

**Status:** Draft  
**Author:** Les Grossman / Waystone  
**Date:** 2026-05-17  
**Related:** `waystone/domain_profiles.py`, `SPEC_LAYER0_AUTO_INJECTION.md`

---

## 1. Motivation

Sales engineering conversations — discovery calls, proof-of-concept design sessions, RFP
responses, and competitive evaluations — contain dense, high-value knowledge that is
currently not captured by any built-in Waystone domain profile.

The `software_dev` profile extracts technical decisions well, but misses the sales-specific
structure: requirements that drive evaluation criteria, proposals that must satisfy those
criteria, objections that threaten deals, and outcomes that close or lose them. A dedicated
profile lets sales engineers (SEs) extract, retrieve, and accumulate institutional memory
across accounts, products, and competitive motions.

The profile is named `solution_design` (not `sales_engineering`) because the knowledge it
captures — requirements, proposals, evaluations, objections — applies to any solution design
context: internal RFPs, vendor selection, consulting engagements, and partner technical
reviews.

---

## 2. Node Types

### 2.1 `requirement`

A technical or business requirement stated by the prospect or customer — something their
solution **must** do or satisfy. Distinct from a `constraint` (which is an internal
limitation) because requirements come from the buyer and drive evaluation.

**What to capture:** the requirement statement, who stated it, which workload or use case it
applies to, and any priority signal (blocker vs. nice-to-have). Include numeric thresholds
where present (e.g. "99.99% uptime", "sub-100ms P99 latency").

**Tags:** system name, use case, priority signal, domain terms from the requirement.

**Example fact:** `"Real-time inventory sync must complete within 100ms — stated by CTO as a
hard blocker for Phase 1 go-live"`

---

### 2.2 `evaluation_criterion`

A specific dimension along which the prospect is scoring or comparing solutions — a rubric
item in their evaluation. More granular than a requirement: a requirement says "we need X",
a criterion says "we will score you on whether you can do X, and here is how we weight it".

**What to capture:** the criterion name, how it is weighted or ranked, who owns the
evaluation on the prospect side, and any known scoring rubric.

**Tags:** domain area, evaluation phase, prospect role who owns it.

**Example fact:** `"Integration capability is weighted 40% of total vendor score — owned by
Head of Engineering; assessment is hands-on POC, not demo"`

---

### 2.3 `proposal`

A specific solution design, architecture, or configuration offered to a prospect in response
to their requirements or evaluation criteria. Captures what was proposed, not just that a
product exists — include the specific configuration, trade-offs explained, and which
requirements it addresses.

**What to capture:** what was proposed, the specific configuration or architecture, which
requirements or criteria it targets, and any trade-offs acknowledged. Link to the
`requirement` or `evaluation_criterion` nodes it satisfies via `satisfies` edges.

**Tags:** product name, feature name, architecture pattern, account name.

**Example fact:** `"Proposed bi-directional sync via change-data-capture with a 50ms
guaranteed SLA — targeting Acme Corp's real-time inventory requirement"`

---

### 2.4 `objection`

A concern, risk, or blocker raised by the prospect that threatens deal progress. Not the
same as a requirement — an objection is reactive (raised in response to something they
heard or saw) rather than proactive. Objections have a status: open, addressed, or resolved.

**What to capture:** the objection verbatim or close paraphrase, who raised it, what stage
of the deal it arose in, and whether it has been addressed. Link to the `proposal` that
triggered it or the `requirement` it connects to.

**Tags:** deal stage, prospect role, objection category (price, security, integration,
performance, etc.).

**Example fact:** `"VP of Security raised concern that CDC connector requires DBA-level
database access — flagged as potential blocker during technical deep-dive"`

---

### 2.5 `comparison`

A structured side-by-side of two or more solutions (our product vs. a competitor, or
alternative internal approaches) on a specific dimension. Not just "we beat them on price"
— captures the specific technical or commercial axis, the evidence used, and which
evaluation criteria it maps to.

**What to capture:** what was compared, on which dimension, what the conclusion was, and
what evidence supported it. Nodes should be linkable to the `evaluation_criterion` they
address via `addresses` edges, and to the compared solutions via `compared_against` edges.

**Tags:** competitor name, dimension (latency, price, scalability, compliance, etc.), deal
stage.

**Example fact:** `"On data freshness: our CDC-based sync achieves 50ms vs. competitor
batch ETL at 15-minute intervals — verified in POC environment on 10M-row table"`

---

### 2.6 `outcome`

The result of a deal, POC, or evaluation phase. Captures wins, losses, stalls, and
no-decisions — and crucially, **why**. Outcome nodes are the most valuable for institutional
memory: they close the feedback loop between proposals/objections and real results.

**What to capture:** win/loss/stall/no-decision, the decisive factor(s), which requirements
or objections were decisive, and any prospect-stated reason. If lost, what did the
competitor offer that we could not?

**Tags:** deal result, decisive factor, account segment, product area.

**Example fact:** `"Lost Acme Corp to Fivetran — decisive factor was native Salesforce
connector out-of-box; prospect unwilling to wait 60 days for custom connector build"`

---

## 3. Edge Relations

### 3.1 Existing relations (no changes needed)

| Relation | Use in this profile |
|---|---|
| `addresses` | `proposal` or `comparison` addresses a `requirement` or `evaluation_criterion` |
| `produces` | `proposal` produces a POC artifact; `outcome` is produced by an evaluation |
| `supersedes` | Updated `proposal` supersedes prior version; resolved `objection` supersedes open one |
| `relates_to` | Loose linkage — e.g. `objection` relates_to an `evaluation_criterion` |
| `depends_on` | `proposal` depends on a product feature or integration |
| `elaborates_on` | `comparison` elaborates on an `evaluation_criterion` |

### 3.2 New edge relations required

Two new relations are needed that do not map cleanly onto existing options:

#### `satisfies`

**Direction:** `proposal` → `requirement` or `proposal` → `evaluation_criterion`  
**Meaning:** The proposal specifically meets (satisfies) the target requirement or criterion.
Stronger than `addresses` (which means "deals with") — `satisfies` means the criterion is
met. Use when the SE has explicitly confirmed or the prospect has acknowledged that the
requirement is fulfilled.

**Why not `addresses`?** `addresses` is used for "this solution is directed at this problem"
— it does not imply success. `satisfies` is a completion signal: the requirement is checked
off. Conflating the two loses the distinction between "we proposed something" and "the
prospect accepted it as meeting the bar."

#### `compared_against`

**Direction:** `comparison` → competitor or alternative solution (represented as a node)  
**Meaning:** The comparison node evaluates the source solution against the target
(competitor, alternative, or prior approach).

**Why not `relates_to`?** `relates_to` is undirected and weak. Competitive comparisons have
directionality (we are scoring ourselves against them on a specific axis) and are a primary
retrieval target in SE workflows. A dedicated relation allows retrieval queries like "show
me all comparisons involving Fivetran" without graph noise from unrelated `relates_to`
edges.

---

## 4. Schema Fit Assessment

| Proposed type | Fits existing schema? | Notes |
|---|---|---|
| `requirement` | New type, clean fit | No existing type captures buyer-stated requirements |
| `evaluation_criterion` | New type, clean fit | Distinct from requirement; drives scoring |
| `proposal` | New type, clean fit | Fills gap between decision and implementation |
| `objection` | New type, clean fit | No equivalent in any current profile |
| `comparison` | New type, clean fit | Competitive intel; needs `compared_against` |
| `outcome` | New type, clean fit | Closes deal feedback loop |
| `satisfies` | **New edge required** | Stronger than `addresses`; signals criterion met |
| `compared_against` | **New edge required** | Directed competitor linkage |

The 6 node types and 2 new edges fit the existing schema shape cleanly — no changes to
the graph store, retrieval engine, or extraction pipeline are needed. The additions are
purely in `domain_profiles.py`.

---

## 5. Retrieval Behavior

The `solution_design` profile benefits from BFS traversal because requirements chain into
criteria, criteria into proposals, proposals into objections, and objections into outcomes.
A query like "what did we propose for real-time sync at Acme?" should traverse:

```
requirement(real-time sync) → proposal(CDC, 50ms) → objection(DBA access) → outcome(lost)
```

Recommended retrieval config:
- `hops: 3` (default; covers the full requirement→outcome chain)
- `top_k: 20`
- `superseded_pruning: true` (updated proposals should shadow prior versions)
- `confidence_threshold: 0.6` (objections and comparisons may be lower confidence)

---

## 6. Buyer Segment Node Type — Parking Decision

Les Grossman's original proposal included a `buyer_segment` node type: a named customer
segment (enterprise, mid-market, SMB) with characteristics and buying patterns. This was
assessed and parked for the following reasons:

1. **Narrow reuse.** Buyer segments are stable reference data — they change slowly and are
   usually documented in CRM or sales playbooks. Extracting them per-conversation adds noise
   without proportional retrieval value.
2. **Existing coverage.** The `persona` node type in `product_management` covers user
   segments well. Buyer segments are the external equivalent and can be modeled with
   `preference` or `constraint` nodes tagged with segment identifiers until there is evidence
   the dedicated type is needed.
3. **Retrieval pressure.** In a BFS starting from a `requirement`, a `buyer_segment` node
   would appear at distance 2–3 via `relates_to` edges and would expand the neighborhood
   without adding signal. This raises `top_k` pressure unnecessarily.

**Decision:** Do not add `buyer_segment` to `solution_design`. Revisit if SEs report that
segment-level retrieval is a pain point in practice.

---

## 7. Implementation Plan

### Phase 1 — Add `solution_design` profile to `domain_profiles.py`

1. Define the 6 node types with full prompt-facing descriptions.
2. Add `satisfies` and `compared_against` to the profile's `edge_relations` dict.
3. Add `solution_design` to `BUILTIN_PROFILES`.
4. Add to the module docstring.

Note: `satisfies` and `compared_against` are **profile-local** in Phase 1 — they appear
only in the `solution_design` profile's edge_relations, not in SOFTWARE_DEV or others.
They are not added to the global graph store schema because edge relation names are stored
as free text in the `edges` table; no migration is needed.

### Phase 2 — Extraction examples (optional)

Add 1–2 few-shot extraction examples to `solution_design.extraction_examples` targeting
known model failure modes:
- Separating `requirement` from `evaluation_criterion` (model tends to merge them)
- Emitting `satisfies` edges vs. `addresses` (model needs explicit guidance on the
  distinction)

### Phase 3 — Retriever type_order (optional)

Add `solution_design` node types to the retriever's `type_order` list in `retriever.py`
so grouped markdown output renders in a sensible order:

```
outcome > proposal > requirement > evaluation_criterion > objection > comparison
```

---

## 8. Open Questions

- Should `comparison` nodes be scoped to a specific deal (tagged with account name), or
  treated as reusable competitive intelligence across deals? Both have value; tagging
  strategy affects retrieval behavior.
- Should `satisfies` edges be propagated back to SOFTWARE_DEV and other profiles as a
  general-purpose "requirement fulfilled" relation, or remain profile-local?
- Is there a meaningful distinction between an `objection` that was successfully handled
  (leading to a win) and one that was not (leading to a loss)? Current model captures this
  via linkage to `outcome` rather than a status field on `objection` — is that sufficient?
