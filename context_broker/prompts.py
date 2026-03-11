"""Extraction prompt template for Context Broker."""

EXTRACTION_PROMPT = """You are a context extraction engine. Analyze the following conversation transcript and extract every meaningful fact, decision, constraint, and implementation detail into a structured graph.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, concise statement of the fact",
      "type": "implementation",
      "confidence": 0.95,
      "source_message": 12,
      "supersedes": [],
      "tags": ["keyword1", "keyword2"]
    }
  ],
  "edges": [
    {
      "from": "n1",
      "to": "n2",
      "relation": "depends_on"
    }
  ]
}

EXTRACTION RULES:

1. Extract FACTS, not filler (greetings, confirmations, thinking-out-loud).

2. Each fact must be self-contained — readable without surrounding context.

3. Capture BOTH the decision AND its rationale as separate nodes linked by "relates_to":
   - Decision node: "JWT was chosen for authentication"
   - Rationale node: "Sessions were rejected because they don't scale horizontally across instances"
   - Rationale node: "JWT is stateless, which eliminates server-side session storage"

4. Capture HISTORICAL STATES when something changes. If a decision is revised:
   - Create a node for the ORIGINAL position: "Originally planned to use S3 for cold storage"
   - Create a node for the NEW position: "Cold storage changed to GCS"
   - Set supersedes: ["original_node_id"] on the new node
   - Add a rationale node explaining why it changed: "ML team infrastructure already on GCP; cross-cloud egress costs too high"

5. Extract SPECIFIC VALUES exhaustively — never omit concrete details:
   - Every numeric threshold, limit, duration, or count ("15-minute token expiry", "1000 req/min", "30-second dedup window")
   - Every named tool, library, protocol, or standard ("Kong", "fastavro", "RS256", "RFC 7807", "HaveIBeenPwned")
   - Every HTTP header, config key, or schema field name ("X-RateLimit-Remaining", "SameSite=Strict")
   - Every policy value or rule ("minimum 12 characters", "5 failed attempts triggers 5-minute lockout")
   - If a topic has multiple distinct values (e.g. different rate limits for different user types), create a separate node for each, or include all values explicitly in one node's fact text.

6. Capture EXPLICIT EXCLUSIONS and rejected alternatives as their own nodes:
   - "SMS OTP was explicitly rejected due to SIM-swapping risk"
   - "localStorage was ruled out due to XSS vulnerability"
   - "Redis was rejected for deduplication state — network overhead added 20ms per event"
   These are high-signal facts that explain WHY decisions were made.

7. Tag nodes richly with every keyword a future query might use to find this node.
   Include: the primary term, synonyms, abbreviations, related concepts, and tool names.
   - Bad:  ["jwt", "auth"]
   - Good: ["jwt", "json web token", "authentication", "auth", "token", "bearer", "rs256", "signing"]
   - Always include the names of tools, libraries, and standards mentioned in the fact.
   - Always include both the generic concept AND the specific implementation:
     ["rate limit", "rate limiting", "throttle", "throttling", "kong", "gateway", "rpm", "requests per minute"]

8. Split compound facts into separate nodes when they have different retrieval keywords:
   - Don't: one node for "Rate limits: 1000/min authenticated, 100/min unauthenticated, enforced by Kong via X-RateLimit headers"
   - Do: separate nodes for (a) the authenticated rate limit, (b) the unauthenticated rate limit, (c) the enforcement layer and headers

9. "source_message" is the 0-based index of the message where the fact appears.

10. Confidence:
    - 0.3-0.5: mentioned or discussed, not decided
    - 0.6-0.8: decided but not yet implemented
    - 0.9-1.0: implemented or verified

11. Node types:
    - "decision": a choice between alternatives that was made
    - "constraint": a limitation, requirement, or non-negotiable
    - "implementation": a concrete technical detail that was established
    - "question": an open question not yet resolved
    - "resolved": the answer to a previously open question
    - "preference": a stated preference for future work, not yet decided

12. Edge relations:
    - "depends_on": target is required for source to work
    - "flows_to": data or control flows from source to target
    - "relates_to": loosely related — use this for decision→rationale links
    - "supersedes": source replaces or overrides target

13. Secondary/addendum facts MUST get their own nodes. When a fact is introduced as a secondary
    detail — with "also", "in addition", "as well", "additionally", "where possible", "as needed",
    or any similar qualifier — create a SEPARATE node for it. Do NOT merge it as a parenthetical
    into the primary fact node.
    - Don't: one node for "Rate limit: 1000/min. IP-level rate limiting also applied."
    - Do: one node for the 1000/min rate limit, a second node for IP-level rate limiting.

TRANSCRIPT:
{transcript}"""


def build_extraction_prompt(transcript_text: str) -> str:
    """Format the extraction prompt with the given transcript."""
    return EXTRACTION_PROMPT.replace("{transcript}", transcript_text)


INCREMENTAL_EXTRACTION_PROMPT = """You are a context extraction engine. Analyze the following conversation turn and extract NEW facts, decisions, constraints, and implementation details that are NOT already captured in the existing context.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, concise statement of the fact",
      "type": "implementation",
      "confidence": 0.95,
      "source_message": 0,
      "supersedes": [],
      "tags": ["keyword1", "keyword2"]
    }
  ],
  "edges": [
    {
      "from": "n1",
      "to": "n2",
      "relation": "depends_on"
    }
  ]
}

EXISTING CONTEXT (already extracted — do NOT re-extract these facts):
{existing_context}

INCREMENTAL EXTRACTION RULES:

1. For NEW nodes in this turn: use short IDs like n1, n2, n3, etc.

2. For EXISTING nodes (from EXISTING CONTEXT): use their exact IDs (e.g., n_a1b2c3d4) when referencing them in edges or supersedes. Do NOT re-emit them as new nodes.

3. Do NOT create new nodes for facts already captured in EXISTING CONTEXT — skip them entirely.

4. When a fact in this turn SUPERSEDES an existing one, create the new node with supersedes: ["n_existingid"] using the existing node's exact ID.

5. You may add edges between new and existing nodes using exact existing IDs. For example:
   {"from": "n1", "to": "n_a1b2c3d4", "relation": "depends_on"}

6. Apply all standard extraction rules to new nodes:
   - Each fact must be self-contained and readable without context.
   - Capture decisions AND rationale as separate nodes linked by "relates_to".
   - Extract SPECIFIC VALUES exhaustively (thresholds, names, limits, config keys).
   - Capture explicit exclusions and rejected alternatives.
   - Tag nodes richly (6–12 tags): primary term, synonyms, abbreviations, tool names, related concepts.
   - Include both the generic concept AND the specific implementation in tags.
   - Split compound facts into separate nodes when they have different retrieval keywords.
   - source_message: 0-based index of the message within THIS TURN.
   - Confidence: 0.3-0.5 discussed, 0.6-0.8 decided, 0.9-1.0 implemented/verified.
   - Node types: decision, constraint, implementation, question, resolved, preference.
   - Edge relations: depends_on, flows_to, relates_to, supersedes.

TURN:
{turn_text}"""


def build_incremental_prompt(turn_text: str, existing_nodes: list[dict]) -> str:
    """Format the incremental extraction prompt with existing context nodes."""
    if existing_nodes:
        lines = []
        for node in existing_nodes:
            tags_str = ", ".join(node.get("tags", [])[:5])
            lines.append(
                f'[{node["id"]}] ({node["type"]}, conf={node.get("confidence", 0.5):.1f})'
                f' "{node["fact"]}" tags:[{tags_str}]'
            )
        existing_context = "\n".join(lines)
    else:
        existing_context = "(none — this is the first turn)"

    return (
        INCREMENTAL_EXTRACTION_PROMPT
        .replace("{existing_context}", existing_context)
        .replace("{turn_text}", turn_text)
    )


RECONCILE_PROMPT = """You are a knowledge graph maintenance engine. Below is a group of related nodes from the same project knowledge graph. Your task is to identify which nodes have been SUPERSEDED by newer, more accurate, or more specific information elsewhere in the group.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start with { and end with }.

Schema:
{
  "supersedes": [
    {
      "superseding_id": "n_abc12345",
      "superseded_id": "n_def67890"
    }
  ]
}

RULES:
1. Only emit a supersedes pair when you are CONFIDENT one node replaces another — not merely related.
2. A node supersedes another when:
   - It corrects a factual error in the other node
   - It is a more up-to-date version of the same decision, setting, or fact
   - It makes the other node obsolete or directly contradicts it with higher confidence
   - It explicitly updates a value, threshold, config key, or policy from the other
3. Do NOT supersede nodes that are simply on the same topic — only emit actual replacements.
4. When two nodes conflict, prefer the one with higher confidence or more recent created_at as the superseding node.
5. If a chain exists (A supersedes B, B supersedes C), emit all pairs.
6. If no supersedes relationships are found, return {"supersedes": []}.

NODES:
{nodes}"""


VERIFICATION_PROMPT = """You are a context extraction auditor. A first-pass extraction has already been run on the transcript below, producing the EXISTING NODES listed. Your job is to find facts that were MISSED.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, concise statement of the missed fact",
      "type": "implementation",
      "confidence": 0.95,
      "source_message": 0,
      "supersedes": [],
      "tags": ["keyword1", "keyword2"]
    }
  ],
  "edges": [
    {
      "from": "n1",
      "to": "n_existingid",
      "relation": "relates_to"
    }
  ]
}

EXISTING NODES (already captured — do NOT re-extract these):
{existing_context}

HUNT SPECIFICALLY for these four missed-fact categories. Emit a node for EVERY instance you find:

CATEGORY 1 — Secondary/addendum details:
  Facts introduced with "also", "in addition", "as well", "additionally", "where possible",
  "as needed", "on top of that", or any similar qualifier.
  These are systematically skipped by first-pass extraction. Find them all.
  Examples of what to look for:
  - "...IP-level rate limiting is also applied."
  - "...GCS cross-region replication for cold storage as well."
  - "...TOTP as primary second factor; hardware keys and passkeys also supported."

CATEGORY 2 — Numeric values and specific thresholds buried in context:
  Any concrete number, duration, limit, count, or threshold that does not already appear
  word-for-word in an existing node's fact text.
  Examples:
  - token expiry durations ("access tokens: 15 minutes", "refresh tokens: 7 days")
  - window sizes ("30-second dedup window")
  - replica counts ("minimum in-sync replicas of 2")
  - specific rate limit values per user type

CATEGORY 3 — Transition/migration statements:
  Any statement describing a change from one approach to another:
  "moved away from X", "switched from X to Y", "replaced X with Y",
  "originally used X but now Y", "migrated from X to Y".
  Examples:
  - "moved away from JSON to Avro for schema evolution"
  - "originally planned S3, switched to GCS"

CATEGORY 4 — Rationale containing time estimates or cost data:
  Any explanation for a decision that includes a duration, timeline, or cost figure.
  Examples:
  - "switching would take 6+ weeks"
  - "cross-cloud egress costs too high"
  - "migration estimated at 3 months"

RULES FOR NEW NODES:
- Use short IDs like n1, n2, n3.
- Reference existing nodes in edges using their exact IDs (e.g. n_a1b2c3d4).
- Each fact must be self-contained and readable without surrounding context.
- Tag nodes richly (6-12 tags): primary term, synonyms, abbreviations, tool names, related concepts.
- source_message: 0-based index of the message in the transcript where the fact appears.
- Confidence: 0.3-0.5 discussed, 0.6-0.8 decided, 0.9-1.0 implemented/verified.
- Node types: decision, constraint, implementation, question, resolved, preference.
- Edge relations: depends_on, flows_to, relates_to, supersedes.
- If nothing is missed, return {"nodes": [], "edges": []}.

TRANSCRIPT:
{transcript}"""


def build_verification_prompt(transcript_text: str, existing_nodes: list[dict]) -> str:
    """Format the verification prompt with transcript and already-extracted nodes."""
    if existing_nodes:
        lines = []
        for node in existing_nodes:
            tags_str = ", ".join(node.get("tags", [])[:6])
            lines.append(
                f'[{node["id"]}] ({node["type"]}, conf={node.get("confidence", 0.5):.1f})'
                f' "{node["fact"]}" tags:[{tags_str}]'
            )
        existing_context = "\n".join(lines)
    else:
        existing_context = "(none)"

    return (
        VERIFICATION_PROMPT
        .replace("{existing_context}", existing_context)
        .replace("{transcript}", transcript_text)
    )


def build_reconcile_prompt(nodes: list[dict]) -> str:
    """Format the reconcile prompt with a group of candidate nodes."""
    lines = []
    for node in nodes:
        tags_str = ", ".join(node.get("tags", [])[:6])
        created = node.get("created_at", "")[:10]
        lines.append(
            f'[{node["id"]}] ({node["type"]}, conf={node.get("confidence", 0.5):.1f}, date={created})\n'
            f'  Fact: "{node["fact"]}"\n'
            f'  Tags: [{tags_str}]'
        )
    return RECONCILE_PROMPT.replace("{nodes}", "\n\n".join(lines))
