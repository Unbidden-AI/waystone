"""Extraction prompt templates for Waystone.

Prompts are parameterized by DomainProfile so the same extraction logic works
across different domains (software_dev, episodic_personal, etc.).
"""

from __future__ import annotations

_DEFAULT_LAYER1_RULES = """1. Extract FACTS, not filler (greetings, confirmations, thinking-out-loud).

2. Each fact must be self-contained — readable without surrounding context.

3. Capture BOTH the decision AND its rationale as separate nodes linked by "relates_to":
   - Decision node: "JWT was chosen for authentication"
   - Rationale node: "Sessions were rejected because they don't scale horizontally across instances"
   - Rationale node: "JWT is stateless, which eliminates server-side session storage"

4. Capture HISTORICAL STATES when something changes. If a decision is REPLACED:
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
    - 0.9-1.0: implemented or verified"""


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

{layer1_rules_section}

{node_types_section}

{edge_relations_section}

{extraction_focus_section}13. Secondary/addendum facts MUST get their own nodes. When a fact is introduced as a secondary
    detail — with "also", "in addition", "as well", "additionally", "where possible", "as needed",
    or any similar qualifier — create a SEPARATE node for it. Do NOT merge it as a parenthetical
    into the primary fact node.
    - Don't: one node for "Rate limit: 1000/min. IP-level rate limiting also applied."
    - Do: one node for the 1000/min rate limit, a second node for IP-level rate limiting.

14. Do NOT extract facts that describe the operation of the extraction/retrieval system itself —
    e.g. "a test query was run", "synthesis was executed", "retrieval returned X nodes", "a query
    was proposed", or real-time debugging observations about context retrieval behavior. These are
    transient operational artifacts, not durable project knowledge. Benchmark results,
    architectural decisions, and analytical findings ARE still valid facts even if they concern
    this system.

TRANSCRIPT:
{transcript}"""


def _format_node_types_section(profile: "DomainProfile") -> str:
    """Format rule 11 (node types) for the extraction prompt."""
    lines = ["11. Node types:"]
    for type_name, description in profile.node_types.items():
        lines.append(f'    - "{type_name}": {description}')
    valid_types = ", ".join(f'"{t}"' for t in profile.node_types)
    lines.append(
        f"   - CRITICAL: Use ONLY these exact type names: {valid_types}. "
        "Never invent new types such as \"fact\", \"rationale\", \"goal\", \"risk\", "
        "\"pending_task\", \"assessment\", \"benefit\", \"blocker\", \"instruction\", "
        "or any other name not in the list above. When in doubt, use \"implementation\"."
    )
    if profile.node_types_note:
        lines.append(f"   - IMPORTANT: {profile.node_types_note}")
    return "\n".join(lines)


def _format_edge_relations_section(profile: "DomainProfile") -> str:
    """Format rule 12 (edge relations) for the extraction prompt."""
    lines = ["12. Edge relations:"]
    for rel, description in profile.edge_relations.items():
        lines.append(f'    - "{rel}": {description}')
    return "\n".join(lines)


def _format_layer1_rules_section(profile: "DomainProfile") -> str:
    """Return domain-specific Layer 1 rules, falling back to the default dev rules."""
    return profile.layer1_rules if profile.layer1_rules else _DEFAULT_LAYER1_RULES


def _format_extraction_focus_section(profile: "DomainProfile") -> str:
    """Format the domain-specific extraction focus (Layer 3). Returns empty string if not set."""
    if not profile.extraction_focus:
        return ""
    return profile.extraction_focus + "\n\n"


def build_extraction_prompt(
    transcript_text: str,
    domain_profile=None,
    existing_nodes: list[dict] | None = None,
) -> str:
    """Format the extraction prompt with the given transcript and domain profile.

    When *existing_nodes* is provided, an EXISTING CONTEXT section is injected
    before the TRANSCRIPT so the LLM can detect supersedence at extraction time
    and avoid re-extracting already-captured facts.
    """
    from .domain_profiles import SOFTWARE_DEV
    profile = domain_profile or SOFTWARE_DEV
    base = (
        EXTRACTION_PROMPT
        .replace("{layer1_rules_section}", _format_layer1_rules_section(profile))
        .replace("{node_types_section}", _format_node_types_section(profile))
        .replace("{edge_relations_section}", _format_edge_relations_section(profile))
        .replace("{extraction_focus_section}", _format_extraction_focus_section(profile))
    )

    if profile.extraction_examples:
        example_lines = []
        for i, (snippet, json_out) in enumerate(profile.extraction_examples, 1):
            example_lines.append(f"## EXAMPLE {i}\n\nTranscript:\n{snippet}\n\nExpected output:\n{json_out}")
        examples_section = (
            "\nEXAMPLES (study these before extracting — they demonstrate correct extraction quality):\n\n"
            + "\n\n---\n\n".join(example_lines)
            + "\n\n"
        )
        base = base.replace("TRANSCRIPT:", examples_section + "TRANSCRIPT:")

    if existing_nodes:
        lines = []
        for node in existing_nodes:
            tags_str = ", ".join(node.get("tags", [])[:5])
            lines.append(
                f'[{node["id"]}] ({node["type"]}, conf={node.get("confidence", 0.5):.1f})'
                f' "{node["fact"]}" tags:[{tags_str}]'
            )
        existing_section = (
            "\nEXISTING CONTEXT (already in graph — do NOT re-extract these facts):\n"
            + "\n".join(lines)
            + "\n\nFor existing nodes above:\n"
            "- Use their exact IDs (e.g. n_a1b2c3d4) in supersedes arrays and edges."
            " Do NOT emit them as new nodes.\n"
            "- When a fact in this transcript SUPERSEDES an existing one, set"
            ' supersedes: ["n_existingid"] on the new node.\n'
            "- Do NOT create new nodes for facts already captured above — skip them entirely.\n"
        )
        base = base.replace("TRANSCRIPT:", existing_section + "\nTRANSCRIPT:")

    return base.replace("{transcript}", transcript_text)


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
   - When a node is one of several items explicitly enumerated together (steps, requirements, alternatives), also include tags from the enumeration's label or grouping context (e.g., all three "hot-path transformation" nodes should include "hot-path" and "transformation" tags).
   - Split compound facts into separate nodes when they have different retrieval keywords.
   - source_message: 0-based index of the message within THIS TURN.
   - Confidence: 0.3-0.5 discussed, 0.6-0.8 decided, 0.9-1.0 implemented/verified.
   - Do NOT extract facts that describe the operation of the extraction/retrieval system itself (e.g. "a test query was run", "retrieval returned X nodes", "synthesis was executed"). Benchmark results and architectural decisions about this system ARE valid.

7. RELATIONSHIP SCAN: After drafting all other nodes, explicitly ask yourself: did anything CHANGE between two named people in this turn? Check specifically for: newly engaged or married, divorced or separated, reuniting after a long absence (months or years apart), one person becoming a caregiver or emotional anchor for another, a major conflict or falling out, a reconciliation, a friendship noticeably deepening or cooling. For EACH "yes" — create a relationship_update node if one does not already exist in EXISTING CONTEXT. Do NOT default to "event" or "fact" for these milestones. IMPORTANT: static descriptions (e.g. "X is Y's sister", "X and Y are coworkers") are NOT relationship changes — leave those as facts. Only create relationship_update when something shifted.

{node_types_section}

{edge_relations_section}

{extraction_focus_section}TURN:
{turn_text}"""


def build_incremental_prompt(turn_text: str, existing_nodes: list[dict], domain_profile=None) -> str:
    """Format the incremental extraction prompt with existing context nodes and domain profile."""
    from .domain_profiles import SOFTWARE_DEV
    profile = domain_profile or SOFTWARE_DEV

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
        .replace("{node_types_section}", _format_node_types_section(profile))
        .replace("{edge_relations_section}", _format_edge_relations_section(profile))
        .replace("{extraction_focus_section}", _format_extraction_focus_section(profile))
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

HUNT SPECIFICALLY for these five missed-fact categories. Emit a node for EVERY instance you find:

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

CATEGORY 3 — Transition/migration statements (use type "transition"):
  Any statement describing a change from one approach to another, OR additive evolution:
  "moved away from X", "switched from X to Y", "replaced X with Y",
  "originally used X but now Y", "migrated from X to Y",
  "started with X, later added Y", "originally had X roles, Y was added".
  Capture BOTH the original state AND the new state in a single node.
  Examples:
  - "moved away from JSON to Avro for schema evolution"
  - "originally planned S3, switched to GCS"
  - "role list started with admin/member/viewer, guest role added later for external collaborators"

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
- Node types: decision, constraint, implementation, question, resolved, preference, lesson_learned, transition.
- Edge relations: depends_on, flows_to, relates_to, supersedes.
- If nothing is missed, return {"nodes": [], "edges": []}.

TRANSCRIPT:
{transcript}"""


def build_verification_prompt(
    transcript_text: str,
    existing_nodes: list[dict],
    candidate_sentences: list[str] | None = None,
) -> str:
    """Format the verification prompt with transcript and already-extracted nodes.

    candidate_sentences: optional list of specific sentences from the transcript
    (produced by Tier 1 heuristic extraction) to check coverage for first.
    """
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

    prompt = (
        VERIFICATION_PROMPT
        .replace("{existing_context}", existing_context)
        .replace("{transcript}", transcript_text)
    )

    if candidate_sentences:
        sentences_block = "\n".join(f"  - {s}" for s in candidate_sentences)
        priority_section = (
            f"\nPRIORITY CHECK — verify coverage of these specific sentences from the transcript.\n"
            f"For each sentence below not already captured (semantically) in EXISTING NODES, emit a new node:\n"
            f"{sentences_block}\n"
        )
        # Insert between existing_context block and HUNT section
        hunt_marker = "\nHUNT SPECIFICALLY"
        prompt = prompt.replace(hunt_marker, priority_section + hunt_marker, 1)

    return prompt


def build_extraction_json_schema(domain_profile=None) -> dict:
    """Build the OpenAI structured-outputs JSON schema for extraction results.

    Parameterized by domain profile so the enum values match the configured
    node types and edge relations. Enable via `llm.structured_outputs: true`.
    """
    from .domain_profiles import SOFTWARE_DEV
    profile = domain_profile or SOFTWARE_DEV
    node_type_enum = list(profile.node_types.keys())
    edge_relation_enum = list(profile.edge_relations.keys())
    return {
        "name": "extraction_result",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "fact": {"type": "string"},
                            "type": {"type": "string", "enum": node_type_enum},
                            "confidence": {"type": "number"},
                            "source_message": {"type": ["integer", "null"]},
                            "supersedes": {"type": "array", "items": {"type": "string"}},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": [
                            "id", "fact", "type", "confidence",
                            "source_message", "supersedes", "tags",
                        ],
                        "additionalProperties": False,
                    },
                },
                "edges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string"},
                            "to": {"type": "string"},
                            "relation": {"type": "string", "enum": edge_relation_enum},
                        },
                        "required": ["from", "to", "relation"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["nodes", "edges"],
            "additionalProperties": False,
        },
    }


# Backward-compatible constant for callers that haven't migrated to the function.
# Uses the software_dev profile (original behavior).
EXTRACTION_JSON_SCHEMA = build_extraction_json_schema()


TARGETED_PASS_PROMPTS = {
    "lessons": """You are a context extraction engine hunting specifically for lessons learned, failed approaches, and rejected alternatives.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, concise statement of the lesson or rejected alternative",
      "type": "lesson_learned",
      "confidence": 0.85,
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

EXISTING CONTEXT (already extracted — do NOT re-extract these):
{existing_context}

HUNT ONLY for these patterns — emit a node for EVERY instance found:

1. REJECTED ALTERNATIVES: "we considered X but rejected it", "X was ruled out because", "we decided against X", "X won't work because"
2. FAILED APPROACHES: "we tried X but it didn't work", "X caused problems", "we moved away from X"
3. EXPLICIT ANTI-PATTERNS: "don't do X", "avoid X", "X is problematic in this context"
4. HARD-WON INSIGHTS: "we learned that X", "X turned out to be", "the issue with X is"
5. REJECTED TOOLS / LIBRARIES / SERVICES: Any named tool, library, or service that was evaluated and rejected, with the reason

RULES:
- Use type: "lesson_learned" for all nodes from this pass (or "transition" if the rejection was partial — rejected for one purpose but used for another).
- Use short IDs like n1, n2, n3. Reference existing nodes with their exact IDs in edges.
- Each fact must be self-contained — include WHAT was rejected AND WHY in the same fact text.
- Tag richly (6-12 tags): the rejected thing, the reason, related concepts, synonyms.
- If nothing is found, return {"nodes": [], "edges": []}.

TRANSCRIPT:
{transcript}""",

    "decisions": """You are a context extraction engine hunting specifically for decisions — explicit choices between alternatives where one path was selected over others.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, concise statement of the decision including WHY and what was rejected",
      "type": "decision",
      "confidence": 0.85,
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

EXISTING CONTEXT (already extracted — do NOT re-extract these):
{existing_context}

HUNT ONLY for these patterns — emit a node for EVERY instance found:

1. EXPLICIT CHOICES: "we chose X", "we went with X", "we decided on X", "X was selected"
2. TRADEOFF DECISIONS: "X over Y because", "X instead of Y", "X rather than Y"
3. REJECTED ALTERNATIVES: For EVERY decision that mentions rejected alternatives, create a SEPARATE node for each rejected path with type "lesson_learned". Link it to the decision node with a "relates_to" edge. Do NOT embed the rejection into the decision fact.
   - Decision node: "Chose JWT for authentication"
   - Rationale node (lesson_learned): "Sessions rejected for auth because they don't scale horizontally across stateless instances"
   - Rationale node (lesson_learned): "Cookies rejected for auth API because CORS complexity with mobile clients"
4. MISLABELED DECISIONS: Any node already in EXISTING CONTEXT labeled "implementation" that actually describes a non-obvious choice — do NOT re-extract the node, but if new rationale or rejected alternatives are found, emit a new decision node that supersedes it.
5. IMPLICIT DECISIONS: Approaches described without explicit alternatives mentioned, but where the choice was non-obvious or contested.
6. EVOLUTION/TRAJECTORY: Things that were ADDED or MODIFIED over time — "started with X roles, later added Y", "originally used X, then added Z on top". Use type "transition" for these, not "decision". Fact must capture BOTH the original state AND the changed/added state AND why it happened.

RULES:
- Use type: "decision" for choices between alternatives. Use type: "transition" for additive evolution and trajectories. Use type: "lesson_learned" for rejected alternatives and rationale.
- Decision node fact: state WHAT was chosen and for what purpose. Keep it short and retrieval-friendly.
- Rationale/rejected nodes: each must name WHAT was rejected AND WHY in a single self-contained sentence.
- Use short IDs like n1, n2, n3. Reference existing nodes with their exact IDs in edges.
- Tag richly (6-12 tags): the chosen technology/approach, the rejected alternative(s), the problem domain, synonyms. Include tags for both sides of the tradeoff so the node is retrievable from either direction. For "transition" nodes, include tags for BOTH the old and new state.
- If nothing new is found, return {"nodes": [], "edges": []}.

TRANSCRIPT:
{transcript}""",

    "questions": """You are a context extraction engine hunting specifically for open questions, unresolved issues, and deferred decisions.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, concise statement of the open question or unresolved issue",
      "type": "question",
      "confidence": 0.7,
      "source_message": 0,
      "supersedes": [],
      "tags": ["keyword1", "keyword2"]
    }
  ],
  "edges": []
}

EXISTING CONTEXT (already extracted — do NOT re-extract these):
{existing_context}

HUNT ONLY for these patterns — emit a node for EVERY instance found:

1. EXPLICIT QUESTIONS: Direct questions left unanswered in the transcript
2. DEFERRED DECISIONS: "we'll figure out X later", "TBD", "to be determined", "not sure yet"
3. OPEN ITEMS: "we need to decide", "still need to address", "open question about"
4. FLAGGED UNCERTAINTIES: "unclear whether", "haven't decided", "might need to reconsider"
5. FOLLOW-UP TASKS: Action items that imply an open decision

RULES:
- Use type: "question" for unresolved items.
- Use type: "resolved" if the question was raised AND answered within the same transcript (link with supersedes).
- Use short IDs. Reference existing nodes in edges using exact IDs.
- Tag with the domain area of the question so retrieval can find it.
- If nothing is found, return {"nodes": [], "edges": []}.

TRANSCRIPT:
{transcript}""",

    "constraints": """You are a context extraction engine hunting specifically for hard constraints, requirements, and non-negotiables.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, concise statement of the constraint or requirement",
      "type": "constraint",
      "confidence": 0.9,
      "source_message": 0,
      "supersedes": [],
      "tags": ["keyword1", "keyword2"]
    }
  ],
  "edges": []
}

EXISTING CONTEXT (already extracted — do NOT re-extract these):
{existing_context}

HUNT ONLY for these patterns — emit a node for EVERY instance found:

1. HARD REQUIREMENTS: "must", "required", "mandatory", "cannot", "must not"
2. EXTERNAL CONSTRAINTS: Compliance requirements (GDPR, SOC2, HIPAA), SLAs, contractual obligations
3. PERFORMANCE CONSTRAINTS: Latency SLOs, throughput minimums, availability targets
4. TECHNICAL CONSTRAINTS: "must be stateless", "must be idempotent", "must not use X", infrastructure lock-in
5. BUDGET / TIMELINE CONSTRAINTS: Cost limits, deadline constraints, resource limits

RULES:
- Use type: "constraint" for all hard requirements.
- Each fact must be specific and actionable — include the constraint value or threshold if stated.
- Use short IDs. Reference existing nodes in edges if the constraint links to a decision.
- Tag with the domain area AND the constraint type (performance, compliance, cost, etc.).
- If nothing is found, return {"nodes": [], "edges": []}.

TRANSCRIPT:
{transcript}""",

    "numerics": """You are a context extraction engine hunting specifically for numeric values, measurements, thresholds, and quantified facts.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, self-contained statement including the exact numeric value and its context",
      "type": "implementation",
      "confidence": 0.9,
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

EXISTING CONTEXT (already extracted — do NOT re-extract these):
{existing_context}

HUNT ONLY for these patterns — emit a node for EVERY instance found:

1. TIME ESTIMATES / DURATIONS: "6+ weeks", "2-3 days", "takes about 4 hours", migration timelines, effort estimates
2. PERFORMANCE THRESHOLDS: Latency budgets ("100ms p99"), throughput targets ("10k req/s"), availability SLOs ("99.9%")
3. SIZE / SCALE FIGURES: Data volumes ("10TB"), row counts, user counts, batch sizes, queue depths
4. CONFIGURATION VALUES: Timeout settings, connection pool sizes, retry counts, page sizes, thread counts
5. VERSION NUMBERS: Language/runtime versions ("Python 3.11"), library/framework versions pinned to specific numbers
6. COST / BUDGET FIGURES: Dollar amounts, percentage cost changes, resource unit costs
7. RATIONALE NUMERICS: Numbers embedded in decision rationale ("switching would cost 6+ weeks", "saves 30% cost") — these MUST be captured even when the main decision node exists; emit a relates_to edge to the existing decision node

RULES:
- Each fact MUST include the specific number — do NOT emit a node if the number is vague or missing.
- The fact must be self-contained: include WHAT the number measures AND the context (system/component it applies to).
- Use type: "constraint" if the numeric is a hard requirement or SLO. Use type: "implementation" for configuration values and estimates.
- Tag with: the thing being measured, the unit or domain, related component names. Include synonyms.
- If a numeric is already captured verbatim in EXISTING CONTEXT, do NOT re-extract it — but if the existing node omits the number, emit a new node with the full numeric and link it.
- If nothing is found, return {"nodes": [], "edges": []}.

TRANSCRIPT:
{transcript}""",

    "preferences": """You are a context extraction engine hunting for DURABLE personal preferences, habits, and tastes expressed by the user in casual conversation.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, self-contained statement of the preference: [Person] likes/dislikes/prefers [specific value] for [topic]",
      "type": "preference",
      "confidence": 0.85,
      "source_message": 0,
      "supersedes": [],
      "tags": ["topic", "value", "preference"]
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

EXISTING CONTEXT (already extracted — do NOT re-extract these):
{existing_context}

EXTRACT only DURABLE, MEANINGFUL preferences — the kind that would answer "what does this person like/dislike?" or "what are they interested in?":

1. EXPLICIT STATED LIKES/DISLIKES: "I like X", "I love X", "I hate X", "I can't stand X", "my favorite is X", "I prefer X"
2. RECURRING HABITS / ROUTINES: "I usually X", "I always X", "I typically X", "every morning I X" — only if the behavior is habitual, not one-time
3. STRONG IMPLICIT PREFERENCES: Clear positive/negative reaction to a SPECIFIC item (not vague reactions like "that was nice")
4. PREFERENCE UPDATES: "I used to like X but now I prefer Y" — use supersedes to link to the old preference node if already extracted
5. INTELLECTUAL INTERESTS & DOMAIN AFFINITIES: Professional, academic, or hobby domains the user actively studies, works in, or follows. Extract as "User is interested in [domain]". Examples:
   - User describes their research area, thesis topic, or field of study → preference node for that domain
   - User discusses following a specific industry, technology, or topic space → preference node
   - User asks detailed questions about a domain, revealing deep familiarity or active interest → preference node
   These answer questions like "what publications/conferences/topics would this person find interesting?"
6. POSITIVE EXPERIENTIAL PREFERENCES: Named venues, events, or activities the user attended and clearly enjoyed — even a single visit implies a genre/format preference. Extract the GENRE/TYPE preference, not just the instance:
   - Attended and enjoyed a horror-themed event → "User enjoys horror-themed experiences / Halloween events"
   - Visited a specific type of restaurant and loved it → "User enjoys [cuisine type] restaurants"
   - Attended a specialized conference and found it valuable → "User values [conference type] events"
   These answer "what should I recommend for this person's next trip / event / outing?"

SKIP:
- Purely casual one-off filler reactions with no genre implication ("that was a nice lunch" with no further context)
- Opinions about other people's choices or external news events
- Context already captured in EXISTING CONTEXT

RULES:
- Use type: "preference" for all nodes from this pass.
- The fact must be self-contained: state WHO has the preference, WHAT they like/dislike/do (specific value, not just a category), and any qualifying context.
- Format as a declarative statement: "The user prefers oat milk lattes over regular coffee" NOT "I like lattes".
- Tag with the topic (broad: "coffee", "exercise") AND specific value ("oat milk latte", "trail running"). Always include "preference" as a tag.
- CRITICAL — CATEGORY BRIDGING TAGS: For any preference involving a specific brand, product model, named item, domain, or experience, ALSO add generic category tags that a question would use to find it. Examples:
    • "Zagg screen protectors for iPhone 13 Pro" → also tag: "phone accessories", "mobile accessories", "phone", "accessories"
    • "prefers Merrell trail shoes" → also tag: "footwear", "shoes", "hiking gear"
    • "wind down by 9:30pm" or "reads before bed" → also tag: "evening", "evening routine", "nighttime", "bedtime"
    • "morning cold brew" → also tag: "morning", "morning routine", "coffee", "morning drink"
    • "vegetarian diet" or "avoids gluten" → also tag: "diet", "food", "eating", "dietary restrictions"
    • "uses standing desk at work" → also tag: "work setup", "workspace", "office"
    • "interested in AI applications in healthcare" → also tag: "AI", "artificial intelligence", "healthcare", "research", "publications", "conferences"
    • "enjoys horror-themed events / Halloween Horror Nights" → also tag: "theme park", "Halloween", "horror", "events", "entertainment", "attractions", "Universal Studios"
    • "interested in machine learning research" → also tag: "ML", "machine learning", "AI", "research", "papers", "conferences", "NeurIPS", "ICML"
    • "prefers ocean-view hotel rooms" → also tag: "hotel", "accommodation", "travel", "lodging", "ocean view", "view"
  The goal: if someone asks "what publications would this person find interesting?" or "what hotel should I recommend?", the broad category tags must be present to surface these nodes via keyword matching.
- AIM FOR 3–8 NODES per call. Do NOT emit trivial or redundant preferences. Hard cap: 10 nodes maximum per call.
- If a preference supersedes a previously extracted one, set supersedes to the old node's ID.
- If nothing meaningful is found, return {"nodes": [], "edges": []}.

TRANSCRIPT:
{transcript}""",

    "decisions_constraints_tradeoffs": """You are a context extraction engine hunting specifically for three interrelated concept types: DECISIONS (choices between alternatives), CONSTRAINTS (hard requirements and non-negotiables), and TRADEOFFS (explicit comparisons of options with rationale).

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, concise statement of the decision, constraint, or tradeoff",
      "type": "decision",
      "confidence": 0.85,
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

EXISTING CONTEXT (already extracted — do NOT re-extract these):
{existing_context}

HUNT ONLY for these patterns — emit a node for EVERY instance found:

**DECISIONS:**
1. EXPLICIT CHOICES: "we chose X", "we went with X", "we decided on X", "X was selected"
2. TRADEOFF DECISIONS: "X over Y because", "X instead of Y", "X rather than Y"
3. REJECTED ALTERNATIVES: For EVERY decision with a mentioned rejected path, create a SEPARATE "lesson_learned" node for each rejection — state WHAT was rejected AND WHY. Link to the decision node via "relates_to". Do NOT embed the rejection into the decision fact.
4. IMPLICIT DECISIONS: Approaches described without explicit alternatives mentioned, but where the choice was non-obvious or contested.

**CONSTRAINTS:**
1. HARD REQUIREMENTS: "must", "required", "mandatory", "cannot", "must not"
2. EXTERNAL CONSTRAINTS: Compliance requirements (GDPR, SOC2, HIPAA), SLAs, contractual obligations
3. TECHNICAL CONSTRAINTS: "must be stateless", "must be idempotent", "must not use X"
4. PERFORMANCE CONSTRAINTS: Latency SLOs, throughput minimums, availability targets

**TRADEOFFS (synthesis layer):**
1. COST-VS-COMPLEXITY: "chose simpler approach even though it costs more", "paid performance penalty for maintainability"
2. SPEED-VS-QUALITY: Decisions about MVP vs. polish, technical debt trade-ins
3. GENERALITY-VS-SPECIALIZATION: Broad vs. narrow design choices and why

RULES:
- Use type: "decision" for choices between alternatives. Use type: "constraint" for hard requirements. Use type: "lesson_learned" for rejected alternatives and rationale.
- Decision node fact: state WHAT was chosen and for what purpose — keep it short and retrieval-friendly.
- Each rejected alternative and its reason gets its own "lesson_learned" node, linked to the decision via "relates_to".
- Tag richly (6-12 tags): the chosen option, the rejected alternative(s), the problem domain, synonyms. Tag both sides of every tradeoff so the node is findable from either direction.
- Use short IDs like n1, n2, n3. Reference existing nodes with their exact IDs in edges.
- If nothing new is found, return {"nodes": [], "edges": []}.

TRANSCRIPT:
{transcript}""",
}


def build_targeted_prompt(category: str, transcript_text: str, existing_nodes: list[dict]) -> str:
    """Build a targeted extraction prompt for a specific category.

    Categories: 'lessons', 'decisions', 'questions', 'constraints', 'numerics', 'preferences', 'decisions_constraints_tradeoffs'
    """
    if category not in TARGETED_PASS_PROMPTS:
        raise ValueError(f"Unknown targeted pass category '{category}'. Valid: {list(TARGETED_PASS_PROMPTS)}")

    template = TARGETED_PASS_PROMPTS[category]

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
        existing_context = "(none — this is the first pass)"

    return (
        template
        .replace("{existing_context}", existing_context)
        .replace("{transcript}", transcript_text)
    )


SYNTHESIS_PROMPT = """You are a knowledge synthesis engine. Below is a set of nodes from a project knowledge graph. Your task is to identify CLUSTERS of parallel nodes — groups where each node represents the same metric, attribute, or outcome measured across different subjects (models, services, configurations, systems, etc.) — and create ONE comprehensive summary node per cluster.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Comprehensive summary combining all instances",
      "type": "implementation",
      "confidence": 0.95,
      "source_message": null,
      "supersedes": [],
      "tags": ["keyword1", "keyword2"]
    }
  ],
  "edges": [
    {
      "from": "n_existingid",
      "to": "n1",
      "relation": "relates_to"
    }
  ]
}

EXISTING NODES:
{existing_nodes}

SYNTHESIS RULES:

1. IDENTIFY CLUSTERS: Find 3 or more nodes that each cover the same attribute/metric for a different subject. Examples of valid clusters:
   - Five nodes with benchmark recall percentages, one per model → one summary node ranking all models
   - Four nodes with latency figures for different services → one summary node listing all latencies
   - Three nodes with node counts per extraction run → one summary node comparing all counts

2. CREATE ONE SUMMARY NODE per cluster:
   - State ALL values explicitly in the fact text. Format: "[Metric]: [Subject1] = [value1], [Subject2] = [value2], ..."
   - Rank or order subjects where meaningful (e.g. highest recall first)
   - Include ALL subjects — never omit one
   - Tags must include: every subject name, the metric name, "summary", "comparison", "ranking", "overview", plus relevant synonyms
   - Edges must point FROM each source node TO the summary node (so BFS traversal from any source reaches the summary). Use: "from": "n_existingid", "to": "n1"

3. MINIMUM CLUSTER SIZE: Only synthesize when 3 or more nodes share the same metric. Do not synthesize pairs.

4. DO NOT create summary nodes that merely restate a single existing node. The summary must be genuinely cross-cutting — it must combine information from multiple source nodes into a single retrievable fact.

5. Use short IDs like n1, n2. Reference existing nodes using their exact IDs (e.g. n_a1b2c3d4) in edges.

6. If no valid clusters exist, return {"nodes": [], "edges": []}."""


def build_synthesis_prompt(existing_nodes: list[dict]) -> str:
    """Format the synthesis prompt with all existing graph nodes.

    Serializes nodes compactly (one line each, fact truncated to 120 chars)
    to keep prompt size manageable for large graphs.
    """
    lines = []
    for node in existing_nodes:
        fact = node["fact"]
        if len(fact) > 120:
            fact = fact[:117] + "..."
        tags_str = ", ".join(node.get("tags", [])[:6])
        lines.append(
            f'[{node["id"]}] ({node["type"]}, conf={node.get("confidence", 0.5):.1f})'
            f' "{fact}" tags:[{tags_str}]'
        )
    nodes_block = "\n".join(lines) if lines else "(none)"
    return SYNTHESIS_PROMPT.replace("{existing_nodes}", nodes_block)


IMPLICIT_PREFS_PROMPT = """You are a preference inference engine. Below are preference and activity nodes extracted from a person's conversations. Your task is to infer IMPLICIT preferences that are strongly implied by the explicit ones — specifically preferences in ADJACENT USAGE DOMAINS that the person would likely have, but did not state directly.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, self-contained statement of the inferred preference",
      "type": "preference",
      "confidence": 0.75,
      "source_message": null,
      "supersedes": [],
      "tags": ["topic", "inferred-domain", "preference"]
    }
  ],
  "edges": [
    {
      "from": "n1",
      "to": "n_sourceid",
      "relation": "relates_to"
    }
  ]
}

EXISTING NODES (source of inference):
{existing_nodes}

INFERENCE RULES:

1. GARDENING → COOKING: If the person grows, plants, or tends specific herbs/vegetables — including via companion planting — infer they likely prefer cooking with those homegrown ingredients.
   Trigger phrases: "grows", "planted", "companion plant for", "tends", "harvest", "garden".
   Example: multiple "prefers X as companion plant for tomatoes" nodes → person grows tomatoes and those herbs → "User grows cherry tomatoes with companion herbs (basil, mint, chives) in their garden; prefers using these homegrown ingredients in cooking and recipes"
   Combine multiple companion-plant nodes into ONE consolidated inference node listing all the produce.

2. SPORTS/FITNESS → NUTRITION: If the person does specific athletic activities (marathon running, cycling, CrossFit), infer complementary nutrition/meal preferences.
   Example: "runs marathons" → "likely prefers high-carb meals before long runs, recovery nutrition after"

3. TRAVEL → FOOD/ACCOMMODATION: If the person visits specific places, infer preferences for local cuisine or accommodation style.
   Example: "excited about Tokyo trip" → "likely interested in trying authentic Japanese cuisine in Tokyo"

4. HOBBY DOMAINS: Other clear domain crossings where an explicit preference implies a strong adjacent preference.

CRITICAL RULES:
- Only emit inferences with HIGH confidence (≥ 0.7) — ones where ANY reasonable person would agree the inference is correct.
- The inference must be PRACTICALLY USEFUL: would it help answer "what does this person want?" in a different context than where the preference was stated?
- Use type: "preference" for all nodes.
- Link each inferred node to its source node(s) via relates_to edges.
- Tag with BOTH the source domain AND the inferred domain, plus "inferred-preference". For food/cooking inferences, always include "cooking", "recipe", "homegrown" (if applicable), and the specific ingredient names as tags — these are the terms a retrieval query would use.
- The fact must state the inferred preference clearly and be self-contained.
- AIM FOR 3–8 NODES. Hard cap: 10 nodes maximum.
- If no strong inferences are possible, return {"nodes": [], "edges": []}."""


def build_implicit_prefs_prompt(existing_nodes: list[dict]) -> str:
    """Build the implicit preference inference prompt from existing extracted nodes.

    Takes preference and implementation nodes and generates inferred cross-domain
    preference nodes (e.g. gardening → cooking, fitness → nutrition).
    """
    # Filter to preference and activity-relevant implementation nodes
    relevant_types = {"preference", "implementation", "transition"}
    relevant = [n for n in existing_nodes if n.get("type") in relevant_types]
    if not relevant:
        relevant = existing_nodes

    lines = []
    for node in relevant:
        fact = node["fact"]
        if len(fact) > 120:
            fact = fact[:117] + "..."
        tags_str = ", ".join(node.get("tags", [])[:6])
        lines.append(
            f'[{node["id"]}] ({node["type"]}, conf={node.get("confidence", 0.5):.1f})'
            f' "{fact}" tags:[{tags_str}]'
        )
    nodes_block = "\n".join(lines) if lines else "(none)"
    return IMPLICIT_PREFS_PROMPT.replace("{existing_nodes}", nodes_block)


REFLECT_PROMPT = """You are a process discovery engine. Your task is to find PATTERNS and PROTOCOLS that emerged from iteration in the conversation below.

A "process" node captures something the team converged on through trying multiple approaches — not a single decision, but a repeatable procedure, implicit protocol, or hard-won insight about workflow.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "When X, do Y because Z (imperative-style description of the process)",
      "type": "process",
      "confidence": 0.7,
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

EXISTING PROCESS NODES (do NOT re-extract these):
{existing_process_nodes}

CONVERSATION WINDOW:
{transcript_window}

HUNT for these patterns:

1. ITERATIVE CONVERGENCE: "We tried X, Y, Z — Z worked best because..." → extract the final Z procedure
2. IMPLICIT PROTOCOLS: "Going forward we will..." or "From now on we use..." → capture the established workflow
3. USEFUL NEGATIVES: "Don't combine X and Y" or "Avoid X when doing Y" → document anti-patterns that guide future work
4. HARD-WON INSIGHTS: Insights that span multiple turns and represent something the team settled on after exploration
5. WORKFLOW OPTIMIZATIONS: Steps or checks the team agreed to add to their process to avoid recurring problems

RULES:

1. Confidence range: 0.7-0.95. Processes are inferred patterns, not explicit declarations, so confidence is lower than decisions.
2. Fact text MUST be imperative and actionable: "When X, do Y" not "We should X" or "X is important"
3. Tags must be specific enough for retrieval. Include the action verb, the scenario, and key nouns.
4. SPLIT facts: If a node describes multiple steps or decisions, split into separate nodes with relates_to edges.
5. AVOID re-extracting decisions/constraints/implementations — focus only on PROCEDURAL PATTERNS that emerged through iteration.
6. DO NOT invent processes — only extract patterns explicitly discussed or strongly implied by the conversation.
"""


REFLECT_EPISODE_PROMPT = """You are an episodic arc discovery engine. Your task is to find SUSTAINED TRAJECTORIES that emerged across multiple turns in the personal conversation below.

An "episode" node captures a multi-turn arc — a period of change, unfolding situation, or recurring behavioral pattern that is NOT stated in any single turn but is clearly inferable from 3 or more individual events, facts, or outcomes together.

Return ONLY a valid JSON object — no markdown fences, no commentary, no preamble. Start your response with { and end with }.

Schema:
{
  "nodes": [
    {
      "id": "n1",
      "fact": "Over [timeframe], [person] went through [arc description] — starting from [initial state] and moving toward [trajectory/direction]",
      "type": "episode",
      "confidence": 0.75,
      "source_message": 0,
      "supersedes": ["constituent_node_id_1", "constituent_node_id_2", "constituent_node_id_3"],
      "tags": ["person_name", "arc_type", "timeframe"]
    }
  ],
  "edges": [
    {
      "from": "n1",
      "to": "constituent_node_id_1",
      "relation": "follows"
    }
  ]
}

EXISTING EPISODE NODES (do NOT re-extract these):
{existing_episode_nodes}

EXISTING EVENT/FACT/OUTCOME NODES (potential arc constituents — use their IDs in supersedes):
{existing_constituent_nodes}

CONVERSATION WINDOW:
{transcript_window}

HUNT for these arc patterns:

1. LIFE TRANSITIONS: A person moving through a sustained change — job search, career pivot, relocation, health recovery, grief process
2. UNFOLDING SITUATIONS: A situation that develops across turns — a relationship evolving, a project being built, a conflict escalating or resolving
3. RECURRING BEHAVIORAL PATTERNS: Something a person consistently does across multiple events that reveals a pattern — not a one-time event but a trait visible across several instances
4. EMOTIONAL ARCS: A person's emotional state shifting directionally over the course of the conversation — not a single expressed feeling but a trajectory

RULES:

1. MINIMUM CONSTITUENCY: The supersedes field MUST list 3 or more constituent node IDs. If you cannot cite 3+ node IDs from the EXISTING NODES list above, do NOT create the episode node.
2. NO HALLUCINATION: Only extract arcs that are CLEARLY supported by the constituent nodes. Do not impose narrative on disconnected facts.
3. Confidence range: 0.6-0.85. Episodes are inferred arcs — lower confidence than explicit events.
4. Fact text format: "Over [timeframe], [person] [arc description] — from [initial state] toward [direction/outcome]"
5. Tags MUST include: the person's name, the arc category (job-search, health, relationship, travel, grief, creative-project, etc.), and the approximate timeframe.
6. Add follows edges from the episode node to each constituent node in chronological order.
7. DO NOT duplicate arcs already in EXISTING EPISODE NODES.
8. If the conversation window shows no clear multi-turn arcs meeting the 3-constituent minimum, return {"nodes": [], "edges": []}.
"""


_EPISODIC_DOMAINS = {"episodic_personal", "episodic_personal_no_dates"}


def _format_node_list(nodes: list[dict], max_fact_len: int = 120) -> str:
    lines = []
    for node in nodes:
        fact = node["fact"]
        if len(fact) > max_fact_len:
            fact = fact[:max_fact_len - 3] + "..."
        tags_str = ", ".join(node.get("tags", [])[:6])
        lines.append(
            f'[{node["id"]}] (type={node.get("type","?")}, conf={node.get("confidence", 0.5):.1f}) "{fact}" tags:[{tags_str}]'
        )
    return "\n".join(lines) if lines else "(none)"


def build_reflect_prompt(
    transcript_window: str, existing_process_nodes: list[dict] | None = None, domain: str = "software_dev"
) -> str:
    """Format the reflect prompt with existing nodes and transcript window.

    For episodic domains, uses REFLECT_EPISODE_PROMPT (episode arc extraction).
    For all other domains, uses REFLECT_PROMPT (process pattern extraction).

    Args:
        transcript_window: multi-turn conversation text
        existing_process_nodes: already-extracted reflect-type nodes (process or episode) for dedup
        domain: domain name

    Returns:
        formatted prompt string
    """
    if existing_process_nodes is None:
        existing_process_nodes = []

    if domain in _EPISODIC_DOMAINS:
        existing_episodes = [n for n in existing_process_nodes if n.get("type") == "episode"]
        existing_constituents = [n for n in existing_process_nodes if n.get("type") != "episode"]
        prompt = REFLECT_EPISODE_PROMPT.replace("{existing_episode_nodes}", _format_node_list(existing_episodes))
        prompt = prompt.replace("{existing_constituent_nodes}", _format_node_list(existing_constituents))
        prompt = prompt.replace("{transcript_window}", transcript_window)
        return prompt

    # Default: software_dev and other non-episodic domains
    existing_block = _format_node_list(existing_process_nodes)
    prompt = REFLECT_PROMPT.replace("{existing_process_nodes}", existing_block)
    prompt = prompt.replace("{transcript_window}", transcript_window)
    return prompt


CONFIG_EXTRACTION_PROMPT = """\
You are extracting instructions and rules from a configuration file into a structured knowledge graph.

Each item in the file should become one node. For each node, decide:
- **pinned: true** if the item should ALWAYS be present in every AI context window, regardless of what the user is working on. Examples: persona/identity instructions, core behavioral rules, vault-level access rules, communication style.
- **pinned: false** if the item is only relevant when the current task/topic matches. Examples: project-specific commands, domain technical rules, workflow steps for specific tools.

Return a JSON object with a "nodes" array. Each node must have:
  - "id": unique slug (snake_case, max 40 chars)
  - "fact": the instruction or rule, verbatim or lightly cleaned, as a complete sentence
  - "type": one of: constraint, preference, instruction, identity, workflow
  - "confidence": 1.0 (all config items are verified facts)
  - "pinned": true or false
  - "tags": 3-8 lowercase keywords relevant to when this rule applies

Configuration file:
---
{config_text}
---

Return only valid JSON. No explanation.
"""


def build_config_extraction_prompt(config_text: str) -> str:
    """Build the prompt for extracting and classifying config file items."""
    return CONFIG_EXTRACTION_PROMPT.replace("{config_text}", config_text)


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
