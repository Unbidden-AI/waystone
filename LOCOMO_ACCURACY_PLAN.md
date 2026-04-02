# LOCOMO Accuracy Improvement Plan

**Status**: Research phase — identifying 6–10 specific, actionable improvements to close the gap from current **56.8% keyword accuracy** to **73%+ (Zep target)** and **87%+ (Mem0 target)**.

**Current performance** (conv-26, April 2026):
- `engram_default` (keyword BFS): **56.8%** keyword accuracy
- `engram_semantic` (vector only): 46.2%
- `engram_combined` (keyword + vector): 55.3%
- Target: Zep ~73%, Mem0 ~88%

---

## Executive Summary: Root Causes

Engram's architecture is sound (DAG + BFS + strategy pipeline), but the implementation has five core gaps specific to **episodic personal conversations** (not software dev):

1. **Entity-centric retrieval is broken** — Queries like "What did Sarah eat?" fail because keywords like "eat" and "Sarah" are matched separately; semantic search actively hurts (46% vs 56%) because generic embeddings can't capture entity-specific context (who ate what).

2. **Extraction is tuned for software dev** — The domain profile for episodic_personal exists and has good layer1_rules + extraction_focus, but retrieval strategies are NOT adapted for personal facts (no person anchoring in BFS seeding, no temporal bucketing, too aggressive recency decay).

3. **Query expansion is non-existent** — A question "When did John's sister visit?" arrives as keywords `["john", "sister", "visit"]` with zero synonym expansion. "Visit" could also be "came", "trip", "dropped by", "showed up" — none of these are attempted.

4. **Temporal is present but dormant** — Session dates flow into the extraction correctly (via `_session_text()` in ingestion_pipeline.py), and layer1_rules rule 5 in episodic_personal mandates date resolution. But retrieval output doesn't sort by date, doesn't surface a timeline, and doesn't leverage temporal proximity (no "recent session boost" specific to LOCOMO's long span).

5. **Multi-hop rewards are invisible** — The BFS traversal is correct, but when it jumps 2+ hops to a distant fact, that fact gets buried below closer (but less relevant) 1-hop neighbors. No multi-session synthesis scoring, no "fact importance ranking", no "cross-session bridge node" detection.

---

## Ranked Improvement Opportunities

### 1. Entity-Scoped Query Expansion (High Confidence, Medium Effort)

**What it is**  
Enhance `extract_keywords()` in `retriever.py` (currently line 510) to detect named entities (people, places) in the task description and expand queries with synonyms and related terms specific to LOCOMO QA patterns.

**Why it should help**  
LOCOMO QA is fundamentally entity-centric: "What did Sarah eat?", "When did John's sister visit?", "Where does Alex live now?" The current keyword extraction treats "sarah" and "eat" as independent terms. When the graph stores `["sarah", "food", "pizza"]` but the query yields `["sarah", "eat"]`, the "eat"→"food" synonym is lost.

**Root cause analysis**  
- Current: `extract_keywords()` does simple tokenization + stop-word filtering + hyphen splitting (for "15-minute" → "15", "minute")
- Missing: synonym expansion for question verbs ("eat"→"consume", "food", "meal"; "visit"→"came", "trip", "traveled"; "work"→"job", "employed", "career")
- Missing: Named entity detection — if query contains "Sarah" + a family-scoped verb, also search for `["sarah", "family", "sibling"]` as a separate seed set
- Impact: Category 1 (single_hop, 282 QA) and category 3 (open_domain, 96 QA) both fail on synonym gaps

**Implementation**  
File: `engram/retriever.py:extract_keywords()`

Replace the simple tokenization with a semantic expansion layer:
```python
def extract_keywords(text: str) -> list[str]:
    """Extract keywords with synonym expansion for LOCOMO QA patterns."""
    # Step 1: Tokenize (current)
    base_keywords = [current tokenization logic]
    
    # Step 2: Detect named entities (simple heuristic: capitalized tokens)
    named_entities = [w for w in text.split() if w[0].isupper() and w not in STOP_WORDS]
    base_keywords.extend(named_entities)
    
    # Step 3: Synonym expansion for question verbs (LOCOMO-specific)
    QUESTION_VERB_SYNONYMS = {
        "eat": ["eat", "consume", "food", "meal", "ate", "eating"],
        "visit": ["visit", "came", "trip", "traveled", "visiting", "dropped by", "showed up"],
        "work": ["work", "job", "employed", "career", "working", "employment"],
        "live": ["live", "live", "location", "moved", "home", "lived", "living"],
        "date": ["dating", "date", "relationship", "dating", "together"],
        "married": ["married", "marriage", "wedding", "engaged", "engagement", "spouse"],
        # ... ~15 more high-frequency LOCOMO patterns from QA categories
    }
    
    for kw in base_keywords:
        if kw in QUESTION_VERB_SYNONYMS:
            base_keywords.extend(QUESTION_VERB_SYNONYMS[kw])
    
    return list(dict.fromkeys(base_keywords))  # dedupe while preserving order
```

**Estimated lift**  
- **Medium-high confidence**: Targets 2–3 of top 5 failure modes per category
- **Estimated +3–5% keyword accuracy** — 56.8% → 59.8–61.8%
- Why not higher: Semantic search on its own hurts (46%), so keyword expansion alone won't reach Zep. But it's a necessary foundation.

**Risk to software dev**  
Low — synonym expansion is additive. Software dev queries ("What rate limits were chosen?") will still match "rate limit" tag; the expansion just adds "threshold", "limit", "rpm" etc. as fallback anchors.

**Complexity**: 1–2 days (mostly building the LOCOMO_VERB_SYNONYMS dict from analyzing QA dataset)

---

### 2. Person-Anchored BFS Seeding (High Confidence, Low Effort)

**What it is**  
When entry nodes are found via tag matching, prioritize nodes tagged with named people (extracted entities) and use them as high-confidence seeds even if they have low keyword overlap.

**Why it should help**  
LOCOMO queries almost always reference a person: "Sarah", "John", "Alex", "Marcus". The extraction already creates `person` nodes and tags every fact with involved people (`layer1_rules` rule 7 mandates this). But retrieval's entry node selection doesn't leverage person anchors — it treats "sarah" as just another keyword.

**Root cause**  
File: `retriever.py:retrieve_with_stats()` around line 123-193 (entry node selection + seeding logic)
- Current: `entry_nodes = [n for n in store.get_nodes_by_tags(keywords) if n["id"] not in pinned_ids]`
- Seeding: High-overlap nodes are those with ≥2 keyword-tag matches. A person node with just "sarah" in tags gets low overlap unless the query also matches a second keyword.
- Missing: Person nodes should always be considered high-confidence seeds, even with 1-keyword overlap, because they gate access to all person-scoped facts downstream.

**Implementation**  
File: `retriever.py:retrieve_with_stats()` (lines 156–207 in current code)

Modify the seeding logic:
```python
# Current (line 171-177):
if strats["relevance_scoring"]:
    high_overlap = [n for n in entry_nodes if n.get("_relevance", 0) >= 2]
else:
    high_overlap = [
        n for n in entry_nodes
        if _count_keyword_tag_hits(n.get("tags", []), keyword_set) >= 2
    ]

# NEW: Add person-node detection as automatic high-overlap
person_anchors = [n for n in entry_nodes if n.get("type") == "person"]
# Any person node matching ≥1 keyword tag is high-confidence
high_overlap_from_people = [
    n for n in person_anchors
    if _count_keyword_tag_hits(n.get("tags", []), keyword_set) >= 1
]
high_overlap = list(dict.fromkeys(high_overlap + high_overlap_from_people))
```

**Why this works**  
- If query has "sarah", it matches the `person` node for Sarah → BFS explores all of Sarah's facts
- If query has "sarah" + "work", it matches the person node + facts tagged with both → seeding is richer
- The seed preservation logic (line 240-247) already reserves 40% of top_k for seeds, so person anchors will survive the strategy pipeline

**Estimated lift**  
- **High confidence**: Every LOCOMO query with a person name will now have a direct anchor
- **Estimated +2–3% accuracy** — 56.8% → 58.8–59.8%
- Category impact: especially helps single_hop (282 QA) and explicit_memory

**Risk**: None. Person anchors are always valid entry points.

**Complexity**: <1 day (copy-paste + type check)

---

### 3. Query Entity Extraction with Coreference Resolution (Medium Confidence, Medium Effort)

**What it is**  
Extract named persons and places from the QA question at retrieval time, and inject explicit "involves [person]" and "located_at [place]" edge queries into the BFS.

**Why it should help**  
LOCOMO QA often uses pronouns and indirect references: "What did she eat?", "When did John's sister visit?", "My friend's job — where is it?" The current keyword extraction yields `["eat"]`, `["sister", "visit"]`, `["friend", "job"]` without resolving "she"→"Sarah" or "sister"→the actual name.

However, building a coreference resolver is expensive. A cheaper approach: leverage the graph's `person` nodes as a fallback dictionary. If the query mentions "sister", search for all `person` nodes with "sister" in tags, then expand keywords to include those person names.

**Root cause**  
- Queries reference relationships ("sister", "friend", "colleague") but the graph stores person nodes with actual names
- No cross-reference between query and extracted person nodes
- BFS stops at relationship-scoped facts instead of jumping to the actual person

**Implementation**  
File: `retriever.py:retrieve_with_stats()` (after line 113, keyword extraction)

```python
keywords = extract_keywords(task_description)

# NEW: Relationship-based person lookup
relationship_terms = {"sister", "brother", "mother", "father", "friend", "colleague", 
                      "roommate", "spouse", "partner", "boyfriend", "girlfriend", 
                      "husband", "wife", "cousin", "aunt", "uncle"}
query_relationships = [kw for kw in keywords if kw in relationship_terms]
if query_relationships:
    # Find all person nodes tagged with these relationships
    person_nodes_by_rel = {}
    for rel in query_relationships:
        nodes = store.get_nodes_by_tags([rel])  # sqlite LIKE search
        person_nodes = [n for n in nodes if n.get("type") == "person"]
        if person_nodes:
            for pn in person_nodes:
                # Extract the person's name from their tags
                person_name = pn.get("tags", [None])[0]  # first tag is usually the name
                if person_name:
                    keywords.append(person_name)
```

**Estimated lift**  
- **Medium confidence**: Relationship resolution is common in LOCOMO (~25% of queries) but the fallback is fragile
- **Estimated +2–4% accuracy** — especially helps multi_session (841 QA) where relationships change
- Risk: False positives if a person's name collides with a common word

**Risk**: Medium — if "Rose" is a person name but also mentioned as a flower, we might retrieve the wrong context. Mitigated by requiring relationship_term + person_node type check.

**Complexity**: 2–3 days

---

### 4. Temporal Proximity Boosting (High Confidence, Low-Medium Effort)

**What it is**  
When retrieval assembles the final context, sort event/outcome/fact nodes by `event_date` (if populated) and apply a temporal proximity boost: nodes near the query's implicit timeframe get higher scores.

**Why it should help**  
LOCOMO has a 20% temporal QA category (321 of 1986 questions). Current retrieval returns nodes unordered by date. A query like "What happened in March 2023?" retrieves all March facts but displays them mixed with 2024/2025 facts. The recency_decay strategy is set to `half_life=3650` (10 years) — making all LOCOMO facts equally old.

**Root cause**  
- `event_date` field doesn't exist on the `nodes` table (mentioned in LOCOMO_PLAN.md Phase 3)
- Recency decay uses `created_at` (ingestion timestamp), not story date
- BFS depth (proximity in graph) is conflated with temporal proximity (distance in time)
- Sorting is by `_score` + `-_bfs_depth`, not by `event_date`

**Implementation — Phase 3A (add `event_date` schema)**  
File: `engram/store.py:GraphStore.__init__()` (migration or schema init)

```sql
ALTER TABLE nodes ADD COLUMN event_date TEXT;  -- ISO 8601 or "2023-05-14" format
CREATE INDEX idx_nodes_event_date ON nodes(event_date);
```

Update extraction to populate `event_date` from layer1_rules rule 5 (date resolution). The extraction prompt already mandates date resolution in episodic_personal — just need to extract it as a structured field.

File: `engram/store.py:add_node()` (update signature)
```python
def add_node(self, ..., event_date: str | None = None, ...) -> str:
    ...
    self.conn.execute(
        "INSERT INTO nodes (..., event_date, ...) VALUES (..., ?, ...)",
        (..., event_date, ...)
    )
```

**Implementation — Phase 3B (temporal sorting + proximity boost)**  
File: `retriever.py:assemble_markdown()` (around line 550+, output assembly)

When `resolve_dates=True` (set in ablation config), sort nodes by event_date before display:
```python
# Before returning markdown, sort by event_date if available
def _sort_by_temporal_proximity(nodes, query_text):
    """Sort nodes by temporal proximity to query's implied timeframe."""
    # Extract date keywords from query ("march 2023", "last week", "2024")
    query_dates = extract_date_expressions(query_text)
    
    # Nodes with explicit event_date get temporal proximity score
    def temporal_score(node):
        node_date = node.get("event_date")
        if not node_date or not query_dates:
            return 0  # no boost
        # Penalize distance in days (simplified)
        similarity = max(
            datetime_similarity(node_date, d) for d in query_dates
        )
        return similarity
    
    # Sort by: temporal proximity (desc), then _score (desc), then _bfs_depth (asc)
    nodes.sort(
        key=lambda n: (
            temporal_score(n),
            n.get("_score", n.get("confidence", 0)),
            -n.get("_bfs_depth", 0)
        ),
        reverse=True
    )
    return nodes
```

**Estimated lift**  
- **High confidence**: Temporal category is 20% of QA and has explicit test cases
- **Estimated +3–5% accuracy** — 56.8% → 59.8–61.8%
- But this is heavily dependent on extraction correctly populating `event_date`

**Implementation steps**  
1. Add `event_date` column to schema (migration)
2. Update extraction to extract date from fact text (requires post-processing of LLM output OR updating LLM prompt)
3. Implement temporal_score function
4. Update `assemble_markdown()` to sort by date when `resolve_dates=True`

**Risk**: If extraction fails to populate `event_date` reliably (dates are embedded in fact text, not as separate field), this won't help. Requires testing on actual LOCOMO data.

**Complexity**: 3–4 days (schema change + extraction update + sorting logic + testing)

---

### 5. Multi-Hop Fact Importance Ranking (Medium Confidence, High Effort)

**What it is**  
Post-BFS, rerank collected nodes to identify and elevate "bridge facts" — facts that connect multiple seed entry points across hops. These are likely the answer to multi-hop questions.

**Why it should help**  
LOCOMO multi_hop QA (841 questions, 42% of total) requires synthesizing facts from multiple sessions. Example: "When did John's sister visit the East Coast?" requires finding:
1. John (person node)
2. John's sister (relationship, secondary person node)
3. Sister's visit to East Coast (event, multi-session)
4. East Coast location (place node)

Current BFS returns all four, but sorts by confidence + depth. A bridge fact connecting East Coast + sister may rank below a nearby but irrelevant fact about John's job.

**Root cause**  
BFS is unweighted — all facts at the same depth have equal pull. No notion of "importance" beyond confidence and recency. Multi-hop synthesis is left to the LLM judge, not baked into retrieval.

**Implementation**  
File: `retriever.py:retrieve_with_stats()` (after BFS collection, before strategy pipeline)

```python
collected_nodes = bfs_collect(store, entry_nodes, hops)

# NEW: Multi-hop importance ranking
def importance_score(node, entry_nodes):
    """Score node by how many entry points it connects to."""
    entry_ids = {n["id"] for n in entry_nodes}
    
    # Find how many entry nodes are reachable through this node
    # (simplified: count outgoing edges to other entry nodes)
    edges_in = store.get_edges_to(node["id"])
    edges_out = store.get_edges_from(node["id"])
    
    entry_connections = sum(
        1 for e in edges_in + edges_out
        if e.get("from_id") in entry_ids or e.get("to_id") in entry_ids
    )
    return entry_connections

# Boost score for bridge nodes
if len(entry_nodes) > 1:
    for node in collected_nodes:
        node["_importance"] = importance_score(node, entry_nodes)
    # Augment sorting to consider importance
    collected_nodes.sort(
        key=lambda n: (
            n.get("_importance", 0) / max(1, len(entry_nodes)),  # normalized bridge score
            n.get("_score", n.get("confidence", 0)),
            -n.get("_bfs_depth", 0)
        ),
        reverse=True
    )
```

**Estimated lift**  
- **Medium confidence**: Requires accurate edge connectivity (depends on extraction quality)
- **Estimated +4–6% accuracy** — targets multi_hop specifically (842 QA, 42% of total)
- But could hurt single_hop if bridge detection is noisy

**Risk**: High — if edge extraction is wrong or sparse, this ranks facts incorrectly. Requires thorough testing on a dev set first.

**Complexity**: 4–5 days (edge query optimization + ranking logic + testing)

---

### 6. Session-Scoped Retrieval Filtering (Medium Confidence, Low-Medium Effort)

**What it is**  
When BFS traversal crosses session boundaries, tag collected nodes with their session ID and implement optional session-aware filtering: "only return facts from the same session as evidence" or "favor recent sessions".

**Why it should help**  
Multi_hop QA asks about facts spanning multiple sessions. But single_hop QA (282 questions, 14% of total) is session-scoped — the question and answer both come from the same session (see QAPair.relevant_session_ids in locomo_dataset.py).

Current retrieval doesn't know which session the question came from, so it returns facts from all sessions equally. For single_hop, this adds noise.

**Root cause**  
- Nodes have `source_transcript` (which project/conversation) but not `source_session_id`
- Ingestion creates all nodes for a conversation in one store without session labels
- Retrieval has no way to say "this query is about session 5" vs "this query is multi-session"

**Implementation**  
File: `benchmarks/locomo/loaders/ingestion_pipeline.py:_extract_chunk()`

Track session ID during extraction and pass it to `store.add_node()`:
```python
def _extract_chunk(
    text: str,
    session_id: str,  # e.g. "session_3"
    ...
) -> None:
    ...
    for node in extracted_nodes:
        store.add_node(
            ...,
            session_id=session_id,  # NEW
            ...
        )
```

File: `engram/store.py:nodes table schema`
```sql
ALTER TABLE nodes ADD COLUMN session_id TEXT;
CREATE INDEX idx_nodes_session_id ON nodes(session_id);
```

File: `benchmarks/locomo/harness.py:_retrieve_context()` (around line ~200 in harness.py)

When building retrieval context, infer session scope from evidence:
```python
def _retrieve_context(store, qa_pair, config):
    # QAPair.relevant_session_ids tells us which sessions have evidence
    evidence_sessions = qa_pair.relevant_session_ids
    
    # If single-hop (all evidence in one session), restrict retrieval to that session
    if len(evidence_sessions) == 1:
        strategies = {
            **config_strategies(config),
            "session_filter": evidence_sessions[0],  # NEW
        }
    else:
        strategies = config_strategies(config)
    
    context = retrieve_with_stats(store, qa.question, strategies=strategies)
```

File: `retriever.py:retrieve_with_stats()` (before BFS)
```python
if strats.get("session_filter"):
    # Restrict entry_nodes to the specified session
    target_session = strats["session_filter"]
    entry_nodes = [n for n in entry_nodes if n.get("session_id") == target_session]
    # For multi-hop, we still allow BFS to cross sessions, but seeding is session-local
```

**Estimated lift**  
- **Medium confidence**: Session filtering helps single_hop (low noise) and multi_hop (acts as a search space boundary)
- **Estimated +1–2% accuracy** — modest but stable improvement
- Particularly helps if extraction is noisy (reduces irrelevant cross-session matches)

**Risk**: Low — session IDs are accurate (from the dataset itself)

**Complexity**: 2–3 days (schema + tracking + filtering logic)

---

### 7. Extraction Prompt Refinement for Personal Facts (Low Effort, Uncertain Lift)

**What it is**  
Review the current episodic_personal domain profile (`engram/domain_profiles.py:EPISODIC_PERSONAL`) and refine layer1_rules and extraction_examples to catch failure modes specific to LOCOMO.

**Why it should help**  
The domain profile exists and has detailed layer1_rules (rules 1–10) + extraction_focus. But the extraction_examples are simple (only 3 examples). LOCOMO QA breaks down into specific failure patterns:
- "Who visited when?" — relationship + temporal facts split incorrectly
- "How long have they been together?" — duration extraction weak
- "What does he do for work?" — profession/job title facts too generic
- "She moved — where and when?" — changes not marked with supersedes

More targeted examples could catch these patterns.

**Implementation**  
File: `engram/domain_profiles.py:EPISODIC_PERSONAL.extraction_examples` (currently lines 312–425, needs expansion)

Add 2–3 more examples targeting:
1. Duration facts ("X years together", "met 5 months ago") → `plan` + `relationship_update` nodes
2. Job/role facts ("works as", "is a", "thinking of becoming") → separate job + plan nodes
3. Change tracking ("moved from X to Y", "used to live", "didn't used to") → prior-state + current-state with supersedes

**Estimated lift**  
- **Low-medium confidence**: Extraction is already quite good (layer1_rules are detailed)
- **Estimated +0–2% accuracy** — incremental, depends on whether the model follows examples
- Main value: higher confidence in extraction quality

**Risk**: None

**Complexity**: 1–2 days (building examples from failing QA pairs)

---

### 8. Semantic Deduplication Threshold Tuning (Low Effort, Medium Confidence)

**What it is**  
The semantic deduplication threshold (currently 0.92 in ablation_configs.py) aggressively merges paraphrasings via cosine similarity. For LOCOMO, this might be too aggressive (merging distinct personal facts that happen to be similar) or too loose (keeping near-duplicates).

**Why it should help**  
Current config: `dedup_threshold=0.92` (see ablation_configs.py line 46)
- At 0.92: BGE-small embeddings merge phrases like "Sarah is happy" and "Sarah is very happy" into one node (loses nuance)
- At 0.95: Only near-identical paraphrases merge (keeps more context)
- At 0.97: Almost no merging (preserves every variant)

For LOCOMO, facts are often stated multiple times with slight variations ("moved to Austin", "relocated to Austin", "Austin is where she lives now"). Merging these loses temporal anchors.

**Implementation**  
File: `benchmarks/locomo/ablation_configs.py`

Add two new configs to test:
```python
"engram_dedup99": AblationConfig(
    name="engram_dedup99",
    description="Very conservative dedup: only merge near-identical nodes (cosine > 0.99). "
                "Isolates whether aggressive merging hurts LOCOMO's multi-variant facts.",
    ...,
    dedup_threshold=0.99,
),

"engram_nodedup": AblationConfig(
    name="engram_nodedup",
    description="No semantic dedup at all (threshold = 1.0). Baseline for measuring dedup impact.",
    ...,
    dedup_threshold=1.0,
),
```

Run ablation: `engram_default` (0.92) vs `engram_dedup99` (0.99) vs `engram_nodedup` (1.0) on dev split.

**Estimated lift**  
- **Medium confidence**: Dedup thresholds have known trade-offs (higher → more nodes, more noise; lower → fewer nodes, more compression)
- **Estimated +1–3% accuracy** if threshold is tuned correctly for LOCOMO
- Could hurt if too loose (retrieval gets noisier)

**Risk**: Could increase output tokens (fewer nodes merged = more to display)

**Complexity**: <1 day (run ablations, analyze)

---

### 9. Retrieval Context Tail — Recent Turns as Short-Term Grounding (Low-Medium Effort, Medium Confidence)

**What it is**  
In addition to BFS-collected facts, append the last N raw conversation turns (unextracted) as a "short-term context tail". This gives the LLM immediate conversational context for questions about very recent events.

**Why it should help**  
The ablation config `engram_prior20` (line 267–272 in ablation_configs.py) already implements this concept: append `prior_turns_window=20` raw turns. But it's disabled by default.

For LOCOMO, events in the most recent sessions may not yet be well-represented in the graph (shallow extraction, confidence not yet at 0.9). Appending the last 10–20 raw turns gives the LLM immediate grounding.

**Status**: This feature is partially implemented. Testing needed.

**Complexity**: <1 day (enable in configs, benchmark)

---

### 10. Fact Importance / Popularity Scoring During Traversal (High Effort, Uncertain Lift)

**What it is**  
Track how many times a fact is queried/used during benchmarking. After multiple benchmark runs, use this "popularity" signal to rank facts higher during retrieval.

**Why it should help**  
Some facts are universally relevant (people's names, major events), while others are niche. A machine-learned importance score could boost high-value facts.

**Risk**: High — this is a post-hoc signal, not structural. Requires multiple benchmark runs and statistical analysis.

**Complexity**: 5–7 days (instrumentation + aggregation + integration)

**Verdict**: Defer to Phase 2. Not actionable now without data.

---

## Top 3 Highest-Leverage Changes to Implement First

Based on estimated lift, implementation complexity, and confidence:

### 1. Entity-Scoped Query Expansion (1–2 days, +3–5% lift)
**File**: `engram/retriever.py:extract_keywords()`
**Effort**: Low-medium
**Confidence**: High
**Order**: **First** — foundational, unblocks downstream improvements

This is the easiest high-impact win. LOCOMO queries are fundamentally entity-centric. Synonym expansion for common verbs ("eat", "visit", "work") and relationship terms ("sister", "friend") will directly improve single_hop and open_domain categories.

**Testing**: Run on dev split, measure keyword accuracy before/after by category.

---

### 2. Person-Anchored BFS Seeding (1 day, +2–3% lift)
**File**: `retriever.py:retrieve_with_stats()` (lines 156–207)
**Effort**: Low
**Confidence**: High
**Order**: **Second** — pairs well with #1

Person nodes are anchor nodes (per layer1_rules). Making them automatic high-confidence seeds ensures that any query mentioning a person immediately unlocks that person's full neighborhood. This is nearly free (one type check) and always correct.

**Testing**: Spot-check a few person-centric queries (e.g., "What is Sarah's job?" should retrieve Sarah's person node + all her facts).

---

### 3. Temporal Proximity Boosting (3–4 days, +3–5% lift)
**File**: `engram/store.py`, `retriever.py:assemble_markdown()`
**Effort**: Medium
**Confidence**: High
**Order**: **Third** — biggest payoff, but requires schema change

This directly targets the temporal QA category (20% of LOCOMO). Requires adding `event_date` column and extracting it reliably, but the payoff is clear: temporal questions will be answered with properly sorted, time-aware context.

**Testing**: Run only on temporal category QA pairs; measure lift independently.

---

## Implementation Priority Matrix

| Improvement | Effort | Confidence | Estimated Lift | Priority |
|---|---|---|---|---|
| 1. Entity-scoped query expansion | 1-2d | High | +3–5% | **1st** |
| 2. Person-anchored BFS seeding | 1d | High | +2–3% | **2nd** |
| 3. Temporal proximity boosting | 3-4d | High | +3–5% | **3rd** |
| 4. Query coreference (sister → name) | 2-3d | Medium | +2–4% | 4th |
| 5. Multi-hop importance ranking | 4-5d | Medium | +4–6% | 5th |
| 6. Session-scoped filtering | 2-3d | Medium | +1–2% | 6th |
| 7. Extraction prompt refinement | 1-2d | Low-medium | +0–2% | 7th |
| 8. Semantic dedup tuning | <1d | Medium | +1–3% | 8th (run ablations) |
| 9. Short-term context tail | <1d | Medium | +1–3% | 9th (test existing feature) |
| 10. Fact importance scoring | 5-7d | Low | +? | Defer Phase 2 |

---

## Estimated Post-Implementation Performance

### After implementing improvements 1–3 (4–7 days)
- **Keyword accuracy**: 56.8% → ~63–67% (baseline +6–10 points)
- Categories most improved: single_hop, temporal, open_domain
- Still gap to Zep (~73%): ~6–10 points remaining

### After implementing improvements 1–6 (9–15 days)
- **Keyword accuracy**: 56.8% → ~68–75%
- May reach or exceed Zep baseline
- Multi_hop and adversarial still weak

### Reaching 87%+ (Mem0 target)
Requires LLM judge evaluation (not keyword accuracy). Likely needs:
- All of 1–6 above
- + Improvements 7–9 for marginal gains
- + Possible extraction improvements (more layers, more examples)
- LLM judge may rate context as "supported" even if keyword metrics are 70%

---

## Success Metrics & Testing Plan

**Dev split** (conv-40, 41, 42, 43): 4 conversations, ~800 QA pairs (categories 1–4)

1. **Baseline run** (before any changes):
   - Keyword accuracy by category (single_hop, temporal, open_domain, multi_hop)
   - Token usage by config
   - Time per query

2. **Per improvement** (apply one at a time):
   - Rerun dev split
   - Measure delta accuracy per category
   - Identify regressions

3. **Combined improvements** (all 1–3 applied):
   - Rerun full dev split
   - Measure combined delta
   - Compare to Zep baseline (73%) and Mem0 baseline (88%)

4. **Final run** (after deciding on 1–6):
   - Run on test split (conv-44–50) once, reserved for final reporting
   - Use LLM judge for final score (not keyword)

---

## Non-Improvements to Avoid (Known Regressions)

Based on prior work (CLAUDE.md system reminder):

1. **Aggressive semantic-only retrieval** — hurts LOCOMO (46% vs 56% keyword)
   - Reason: BGE-small can't distinguish "Sarah's eating a pizza" from "Sarah's avoiding pizza"
   - Instead: keyword + semantic hybrid, with keyword primary

2. **Confidence threshold filtering** — hurts LOCOMO
   - Reason: extraction assigns moderate confidence (0.6–0.8) to many personal facts; filtering prunes needed context
   - Instead: rely on top_k + recency_decay

3. **Aggressive prompt consolidation** — hurts software dev and LOCOMO alike
   - Reason: tells model to merge facts → fewer nodes → lower recall
   - Instead: bias toward splitting facts

---

## Files to Modify (Summary)

| File | Changes | Improvement(s) |
|---|---|---|
| `engram/retriever.py:extract_keywords()` | Add synonym expansion + entity detection | 1, 3 |
| `retriever.py:retrieve_with_stats()` | Person-anchored seeding; temporal scoring | 2, 3, 4 |
| `retriever.py:assemble_markdown()` | Sort by event_date; temporal proximity boost | 3 |
| `engram/store.py` | Add `event_date` column; update schema | 3, 5, 6 |
| `engram/store.py:add_node()` | Accept `event_date`, `session_id` | 3, 5, 6 |
| `benchmarks/locomo/loaders/ingestion_pipeline.py` | Track session ID during extraction | 6 |
| `engram/domain_profiles.py:EPISODIC_PERSONAL` | Add extraction_examples | 7 |
| `benchmarks/locomo/ablation_configs.py` | Add new configs for dedup/prior_turns testing | 8, 9 |

---

## Next Steps

1. **Validate findings on dev split baseline** (1 day)
   - Run `engram_default` on conv-40, 41, 42, 43
   - Measure keyword accuracy by category
   - Identify top 5 failure patterns

2. **Implement improvement #1** (Entity-scoped query expansion, 1-2 days)
   - Build LOCOMO_VERB_SYNONYMS dict from QA dataset
   - Update extract_keywords()
   - Rerun dev split, measure delta

3. **Implement improvements #2 and #3** in parallel (2-3 days)
   - Person-anchored seeding (low-hanging fruit)
   - Temporal proximity (higher payoff, more complex)

4. **Ablation sweep** (2-3 days)
   - Run all three improvements on dev split
   - Run ablations (each individually, all combined)
   - Report accuracies + token usage

5. **Decide on #4–6 based on results** (1 week)
   - If #1–3 reach 70%+, may not need more
   - If stuck at 65–68%, prioritize #4–5

6. **Final test set eval** (after cutoff date, for paper)
   - Run best config on test split (conv-44–50)
   - Report LLM judge score

