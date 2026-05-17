"""Domain profiles for Waystone extraction.

Each profile defines the node types, edge relations, and their descriptions
used by the extraction LLM. Select a profile via config.yaml:

    domain:
      name: episodic_personal

Built-in profiles:
  - software_dev       (default) — technical decisions, constraints, implementations
  - episodic_personal            — personal conversations, life events, relationships
  - medical_clinical             — clinical notes, patient encounters, care plans
  - legal                        — case law, contracts, statutes, legal arguments
  - meeting_notes                — action items, decisions, blockers, follow-ups
  - academic_research            — papers, methods, benchmarks, empirical results
  - financial                    — earnings calls, guidance, metrics, transactions
  - customer_support             — support tickets, helpdesk chats, issue resolution
  - education_tutoring           — tutoring sessions, classroom discussions, Q&A
  - news_events                  — news articles, event coverage, public statements
  - agentic_workflow             — AI agent orchestration, tool calls, prompt engineering
  - creative_writing             — characters, plot, worldbuilding, narrative craft
  - product_management           — features, prioritization, user stories, roadmap decisions
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DomainProfile:
    name: str
    node_types: dict[str, str]       # type_name -> description for prompt
    edge_relations: dict[str, str]   # relation -> description for prompt
    node_types_note: str = ""        # extra note appended after node type list (optional)
    extraction_focus: str = ""       # domain-specific extraction guidance (Layer 3); empty = no-op
    layer1_rules: str = ""           # domain-specific Layer 1 rules (replaces default rules 1-10); empty = use default
    extraction_examples: list[tuple[str, str]] = field(default_factory=list)
    # List of (transcript_snippet, json_output) few-shot examples injected before the transcript.
    # Keep snippets short (3-6 turns). Targeted at known model failure modes (numerics, rationale).


SOFTWARE_DEV = DomainProfile(
    name="software_dev",
    node_types={
        "process": "a pattern, protocol, or iterative procedure that the team converged on through trying approaches",
        "decision": "a choice between alternatives that was made",
        "constraint": "a limitation, requirement, or non-negotiable",
        "implementation": "a concrete technical detail that was established",
        "question": "an open question not yet resolved",
        "resolved": "the answer to a previously open question",
        "preference": "a stated preference for future work, not yet decided",
        "lesson_learned": "a failed approach, rejected alternative, or anti-pattern discovered",
        "best_practice": (
            "a reusable, validated pattern or technique the team has converged on and recommends "
            "following — proven through experience, not just preferred. Distinct from lesson_learned "
            "(which is a failure or rejection). Use when the fact says 'always do X' or 'the right "
            "way to do X is Y' with evidence behind it."
        ),
        "tech_update": (
            "a change in the technology landscape — new library version, deprecation, tool change, "
            "API breaking change, or ecosystem shift — that affects how the project approaches a "
            "problem. Capture: what changed, from what to what, and the implication for the project."
        ),
        "transition": (
            'an evolution — captures BOTH the original state AND the new state AND why it changed, '
            'in a single self-contained node. Use for additive evolution, partial changes, and any '
            '"originally X, later Y" trajectory that isn\'t a clean replacement. '
            'Fact text MUST use "from X to Y" or "originally X, now Y" language. '
            "Tags must cover BOTH the old and new terms. "
            'Example: "Cold storage switched from S3 to GCS (ML team already on GCP; '
            'cross-cloud egress costs too high)" — tags: ["s3", "gcs", "cold storage", '
            '"cloud storage", "egress", "gcp"].'
        ),
    },
    edge_relations={
        "depends_on": "target is required for source to work",
        "flows_to": "data or control flows from source to target",
        "relates_to": "loosely related — use this for decision→rationale links",
        "supersedes": "source replaces or overrides target",
        "conflicts_with": "source and target decisions are in active unresolved tension — neither has won yet",
        "addresses": "source node (solution, implementation, decision) directly resolves the target (constraint, problem, question)",
        "rejected_alternative": "source decision or implementation rejected the target approach — use when one path was explicitly not taken in favor of source",
        "elaborates_on": "source node provides additional detail, depth, or specificity about the target without replacing it",
        "part_of": "source node is a component, sub-step, or subset of the target — use for containment or decomposition",
        "implements": "source node is the concrete realization of the target decision, design, or specification",
        "enables": "source node unlocks, unblocks, or makes possible the target — use when source is a prerequisite that opens capability",
    },
    node_types_note=(
        'When a "decision" or "transition" node supersedes a prior approach, its tags MUST include '
        "the old term so the decision is retrievable from both the old and new directions. "
        'Example: a decision to switch from JSON to Avro must have tags: ["json", "avro", "event format", ...].'
    ),
    extraction_examples=[
        # Example 1: numeric thresholds — model must emit separate nodes per distinct value
        (
            """\
[0] Engineer A: Rate limiting — what are we going with?
[1] Engineer B: 1000 requests per minute for authenticated users, 100 per minute for unauthenticated. \
Hard cap enforced at the Kong gateway using X-RateLimit-Remaining headers.
[2] Engineer A: And lockout policy for failed logins?
[3] Engineer B: Five consecutive failures triggers a 5-minute lockout. \
Minimum password length is 12 characters.""",
            """\
{
  "nodes": [
    {"id":"n1","fact":"Authenticated users are rate-limited to 1000 requests per minute","type":"constraint",\
"confidence":0.9,"source_message":1,"supersedes":[],"tags":["rate limit","rate limiting","authenticated",\
"1000","rpm","requests per minute","throttle"]},
    {"id":"n2","fact":"Unauthenticated users are rate-limited to 100 requests per minute","type":"constraint",\
"confidence":0.9,"source_message":1,"supersedes":[],"tags":["rate limit","rate limiting","unauthenticated",\
"100","rpm","requests per minute","throttle"]},
    {"id":"n3","fact":"Rate limiting is enforced at the Kong API Gateway using X-RateLimit-Remaining headers",\
"type":"implementation","confidence":0.9,"source_message":1,"supersedes":[],"tags":["kong","gateway",\
"api gateway","rate limit","x-ratelimit-remaining","header","enforcement"]},
    {"id":"n4","fact":"5 consecutive failed login attempts trigger a 5-minute account lockout","type":"constraint",\
"confidence":0.9,"source_message":3,"supersedes":[],"tags":["lockout","failed login","5 attempts",\
"5 minutes","brute force","authentication","security"]},
    {"id":"n5","fact":"Minimum password length is 12 characters","type":"constraint","confidence":0.9,\
"source_message":3,"supersedes":[],"tags":["password","password length","12 characters","minimum",\
"policy","security"]}
  ],
  "edges": [
    {"from":"n1","to":"n3","relation":"depends_on"},
    {"from":"n2","to":"n3","relation":"depends_on"}
  ]
}"""
        ),
        # Example 2: rationale-embedded decision + transition with prior-state tags
        (
            """\
[0] Engineer A: We were going to use Redis for dedup state — still the plan?
[1] Engineer B: No, we ruled out Redis. The network overhead was adding 20ms per event, \
which blows our 50ms SLA. We're using an in-process Bloom filter instead.
[2] Engineer A: What's the false-positive rate?
[3] Engineer B: 0.1% at 10 million entries. We'll reset it every 24 hours.""",
            """\
{
  "nodes": [
    {"id":"n1","fact":"Deduplication state storage changed from Redis to an in-process Bloom filter",\
"type":"transition","confidence":0.95,"source_message":1,"supersedes":[],"tags":["redis","bloom filter",\
"deduplication","dedup","in-process","state storage"]},
    {"id":"n2","fact":"Redis was rejected for deduplication state — network overhead added 20ms per event",\
"type":"lesson_learned","confidence":0.95,"source_message":1,"supersedes":[],"tags":["redis","rejected",\
"network overhead","20ms","latency","deduplication","dedup"]},
    {"id":"n3","fact":"Deduplication SLA is 50ms — Redis network overhead of 20ms violated this budget",\
"type":"constraint","confidence":0.95,"source_message":1,"supersedes":[],"tags":["sla","50ms","latency",\
"budget","deduplication","dedup"]},
    {"id":"n4","fact":"Bloom filter false-positive rate is 0.1% at 10 million entries",\
"type":"implementation","confidence":0.9,"source_message":3,"supersedes":[],"tags":["bloom filter",\
"false positive","0.1%","10 million","capacity","deduplication"]},
    {"id":"n5","fact":"Bloom filter is reset every 24 hours","type":"implementation","confidence":0.9,\
"source_message":3,"supersedes":[],"tags":["bloom filter","reset","24 hours","ttl","deduplication"]}
  ],
  "edges": [
    {"from":"n1","to":"n2","relation":"relates_to"},
    {"from":"n1","to":"n3","relation":"relates_to"},
    {"from":"n4","to":"n1","relation":"relates_to"},
    {"from":"n5","to":"n1","relation":"relates_to"}
  ]
}"""
        ),
    ],
)

EPISODIC_PERSONAL = DomainProfile(
    name="episodic_personal",
    node_types={
        "event": (
            "something that happened, an experience, or an activity (past or future-scheduled) — "
            "a trip, a party, an accident, starting a job, a surgery, a celebration. "
            "Include who was involved, what happened, and any outcome or feeling expressed. "
            "Do NOT use for relationship milestones between two people (engagements, breakups, "
            "reunions, conflicts) — use relationship_update for those."
        ),
        "person": (
            "a named individual introduced or discussed — create one person node the FIRST TIME "
            "each person is named. Capture their name, their relationship to the speaker "
            "(friend, sibling, colleague, etc.), and one key identifying fact. "
            "Do NOT absorb this into a fact node — every named person gets their own person node."
        ),
        "place": (
            "a location, venue, city, neighborhood, or geographic area that was visited, "
            "discussed, or associated with an event."
        ),
        "fact": (
            "a stated fact or piece of information about someone or a situation — "
            "health details, beliefs, habits, job/school facts, possessions, opinions. "
            "Do NOT use for: relationship milestones (use relationship_update), "
            "future plans (use plan), event outcomes (use outcome), "
            "stated likes/dislikes (use preference), or places (use place)."
        ),
        "plan": (
            "a future intention, goal, or planned activity not yet completed at the time stated. "
            "Include the who, what, and when if mentioned."
        ),
        "outcome": (
            "the result, resolution, or consequence of an event or plan. "
            "Use when the speaker reports how something turned out."
        ),
        "preference": (
            "a stated like, dislike, opinion, taste, or recurring preference about food, "
            "activities, people, places, or anything else."
        ),
        "relationship_update": (
            "a CHANGE or milestone in the relationship between two specific named people — "
            "something that shifted their dynamic, not a static description of who they are. "
            "Use this — not event or fact — for: engagements and marriages "
            "(e.g. 'Jordan and Sam got engaged'), breakups or divorces, reunions after "
            "a long separation (e.g. two friends seeing each other again after 6+ months), "
            "major conflicts or falling outs, reconciliations, one person becoming a caregiver "
            "or emotional support for another, a friendship noticeably deepening or cooling. "
            "Do NOT use for static family relationships (e.g. 'X is Y's sister') — those are facts. "
            "Key test: did something CHANGE between these two people? If yes → relationship_update."
        ),
        "episode": (
            "a multi-turn arc inferred across several turns — NOT extractable from any single statement. "
            "Use ONLY when 3 or more event/fact/outcome/relationship_update nodes together reveal a "
            "sustained directional trajectory: a person going through a period of change, a situation "
            "unfolding over time, or a recurring behavioral pattern. "
            "Examples: a job search arc, a health recovery arc, a relationship deterioration arc, "
            "a period of travel, an extended creative project. "
            "The fact text must name: the person, the arc type, the inferred timeframe, the starting "
            "state, and the direction/trajectory. "
            "CRITICAL: The supersedes field MUST list the IDs of the 3+ constituent nodes this arc "
            "is inferred from. If you cannot cite 3+ constituent node IDs, do NOT create the episode node. "
            "Do NOT invent arcs — only extract when the constituent nodes clearly support the trajectory."
        ),
    },
    edge_relations={
        "involves": "person, place, or thing participates in or is central to the source event/fact",
        "located_at": "source event or experience occurred at the target place",
        "follows": "source event or outcome follows the target chronologically or causally",
        "updates": "source node revises, extends, or supersedes the target (use for changing facts/plans)",
        "references": "source node mentions or relates to the target without a stronger structural link",
    },
    node_types_note=(
        "ANCHOR NODES: Every named person must have exactly one person node (create it on first mention). "
        "Every named place must have exactly one place node. These anchors must exist even if all you "
        "know is the name — other nodes link to them via involves/located_at edges. "
        "When a fact changes (e.g. someone changes jobs, moves, or updates a plan), create a new "
        "node with updates: [<old_node_id>] rather than overwriting. "
        "Tag every node with the relevant person's name so queries about that person retrieve all their facts."
    ),
    layer1_rules=(
        "1. Extract FACTS, not filler (greetings, small talk, social niceties, filler phrases like "
        '"yeah", "right", "I see", "wow" — unless they carry genuine emotional signal).\n\n'
        "2. Each fact must be self-contained — readable without surrounding context. Include the "
        "person's name, what the fact is about, and any key detail (date, place, relationship) "
        "directly in the fact text.\n\n"
        "3. Capture BOTH the event or fact AND the speaker's reaction or perspective as separate "
        'nodes linked by "references":\n'
        '   - Event node: "Jordan started a new job at a marketing agency in Austin in March"\n'
        '   - Reaction node: "Jordan feels excited but nervous about the new role — it\'s her first '
        'time managing a team"\n'
        "   Only create a reaction node if the speaker explicitly states a feeling, opinion, or "
        "concern. Do NOT invent reactions.\n\n"
        "4. Capture CHANGE OVER TIME when a person's situation has changed. If a fact is an UPDATE "
        "to a prior state:\n"
        '   - Create a node for the PRIOR STATE: "Alex used to live in Boston before 2023"\n'
        '   - Create a node for the CURRENT STATE: "Alex moved to Seattle in early 2023 for work"\n'
        "   - Set supersedes: [\"prior_node_id\"] on the current state node\n"
        '   Look for signal words: "used to", "before", "moved", "switched", "left", "now", '
        '"changed", "broke up", "got a new".\n\n'
        "5. Extract SPECIFIC VALUES exhaustively — never omit concrete details:\n"
        "   - Every named person (full name or nickname as stated), their age if mentioned, their "
        "relationship to the speaker (best friend, cousin, roommate, coworker)\n"
        "   - Every named place (city, neighborhood, venue, country) mentioned in connection with "
        "an event or person\n"
        "   - Every date, timeframe, or duration — RESOLVE relative references to absolute dates "
        "using the session date header at the top of the transcript (e.g. [Session: 1 | Date: 2023-05-08]). "
        "REPLACE the relative phrase with the resolved date in the fact text — do NOT keep 'last week', "
        "'yesterday', 'last month' etc. in the fact. Examples: "
        "'yesterday' → '7 May 2023' (one day before session date); 'last month' → 'April 2023'; "
        "'three weeks ago' → 'late April 2023'; 'next March' → 'March 2024'. "
        "Write dates in natural English ('14 January 2022', 'week of 14 January 2022') not ISO format. "
        "Store the resolved date in both fact text and tags. When precision is only approximate "
        "('last summer' → 'summer 2022'), write that period. Undated events are still extracted — "
        "omit date resolution only when the session date is absent.\n"
        "   - Every stated quantity or threshold ('two kids', 'five years together', '$40k salary', "
        "'moved four times', 'hasn't spoken in months')\n\n"
        "6. Capture EMOTIONAL CONTEXT and STATED FEELINGS as their own nodes when they are "
        "explicit and meaningful:\n"
        '   - "Sam is devastated about the divorce — hadn\'t seen it coming"\n'
        '   - "The speaker feels guilty for missing the reunion"\n'
        '   - "Jordan is proud of finishing the marathon despite the injury"\n'
        "   These are high-signal personal facts. Do NOT extract vague or implied emotions — only "
        "what the speaker directly states.\n\n"
        "7. Tag nodes with every term a future query might use to find them:\n"
        "   - MANDATORY: include the full name (and nickname if used) of every person the node "
        "involves — this is how person-scoped queries work\n"
        '   - Include the event type, place name, and relationship label: ["wedding", "marriage", '
        '"sarah", "barcelona", "sister", "2022"]\n'
        "   - Include both specific and generic terms: if the fact is about a promotion, include "
        '["promotion", "job", "career", "work", "raise"] even if only one appears in the text\n\n'
        "8. Split compound facts into separate nodes when they involve different events, different "
        "people, or would be retrieved by different queries:\n"
        '   - Don\'t: one node for "Sarah got married in June, moved to Rome, and is now pregnant"\n'
        '   - Do: one node for the marriage, one for the move to Rome, one for the pregnancy\n'
        "   Each event has its own timeline, emotional weight, and retrieval path.\n\n"
        '9. "source_message" is the 0-based index of the message where the fact appears.\n\n'
        "10. Confidence:\n"
        "    - 0.3-0.5: mentioned in passing or indirectly implied — speaker didn't state it clearly\n"
        "    - 0.6-0.8: stated directly but not confirmed (plans, second-hand reports, uncertain "
        "timelines)\n"
        "    - 0.9-1.0: stated as a confirmed fact or direct personal experience"
    ),
    extraction_focus=(
        "PERSONAL CONVERSATION EXTRACTION FOCUS — apply these rules on every turn:\n\n"
        "PEOPLE: Create a person node for every named individual on their FIRST mention, even if the "
        "only known facts are their name and relationship to the speaker. Never absorb a person into "
        "another node.\n\n"
        "DATES & TIMEFRAMES: The transcript header contains the session date (e.g. [Session: 1 | "
        "Date: 2023-05-08]). Use it to resolve ALL relative temporal references to absolute dates "
        "before writing them into facts and tags. IMPORTANT: REPLACE the relative phrase in the "
        "fact text with the resolved date — do NOT keep 'last week', 'yesterday', 'last month', etc.\n"
        "  - 'yesterday' → one day before session date, written as '7 May 2023' (natural English)\n"
        "  - 'last week' → prior week, written as 'week of 30 April 2023'\n"
        "  - 'last month' → prior month and year, written as 'April 2023'\n"
        "  - 'three weeks ago' → approximate date, written as 'late April 2023'\n"
        "  - 'next March' → next March after session date, written as 'March 2024'\n"
        "Bad: 'Nate won the tournament last week.' (relative phrase kept)\n"
        "Good: 'Nate won the tournament the week of 14 January 2022.' (resolved, natural English)\n"
        "Write dates in natural English ('14 January 2022', 'week of 14 January 2022'), NOT ISO format. "
        "Store the resolved date in both the fact text and tags. "
        "When precision is only month or year level ('last summer' → 'summer 2022'), write that period. "
        "Undated events should still be extracted — omit date resolution only when "
        "the session date is absent from the header.\n\n"
        "PLANS & INTENTIONS: Any expression of future intention is a plan node — 'I want to', 'we're "
        "going to', 'I plan to', 'hopefully', 'I might', 'thinking about'. Include who, what, and "
        "when (if stated). Confidence: 0.7.\n\n"
        "OUTCOMES: Any report of how something turned out is an outcome node — 'it turned out', "
        "'ended up', 'finally', 'unfortunately it', 'it worked'. Link to the originating event or "
        "plan with a follows edge. Confidence: 0.9.\n\n"
        "RELATIONSHIP SCAN: After drafting all other nodes, explicitly check: did anything CHANGE "
        "between two named people in this turn? Check for engagements, breakups, reunions after "
        "a long separation, one person becoming a caregiver or emotional anchor, major conflicts, "
        "reconciliations, a friendship noticeably deepening or cooling. Each 'yes' gets a "
        "relationship_update node. Static descriptions ('X is Y's sister') are facts, not updates.\n\n"
        "TAGGING: Every node must include the names of all people it involves as tags. A health "
        "fact about Jordan must have 'jordan' in tags. A plan involving both Alex and Sam must "
        "have both names in tags. This is mandatory — it is how person-scoped queries work.\n\n"
        "SPEAKER SELF-TAGGING: When the transcript uses a consistent speaker label for the main "
        "speaker (e.g. 'User:', 'Speaker A:'), treat that label as their name:\n"
        "  1. Create a person node on the FIRST turn they appear: {\"fact\": \"User is the main "
        "speaker\", \"type\": \"person\", \"tags\": [\"user\", \"speaker\", \"main speaker\"]}\n"
        "  2. Tag EVERY fact, preference, plan, event, and outcome stated by that speaker with "
        "their role label (lowercased, e.g. 'user'). Example: 'User: I love hiking' → tags must "
        "include 'user'. This is required even if the speaker is never referred to by personal name."
    ),
    extraction_examples=[
        # Example 1: person anchors + date tagging + compound fact splitting
        # Targets: explicit memory (30%), multi-session (25%), temporal (20%)
        # Key lesson: create a separate person node for every named individual on first mention;
        # include date verbatim in fact text and tags; split compound events into separate nodes.
        (
            """\
[Session: 1 | Date: 2023-04-15]
[0] Speaker A: How's your friend group been lately?
[1] Speaker B: Really good! My college roommate Priya just started a new job at a biotech startup \
in San Francisco last month. She's really excited — big pay bump but a lot more responsibility.
[2] Speaker A: Nice! And your family?
[3] Speaker B: My brother Marcus graduated from community college in March. Super proud of him. \
Oh, and he's been dating someone new for about two months — her name is Chloe.""",
            """\
{
  "nodes": [
    {"id":"n1","fact":"Priya is Speaker B's college roommate","type":"person","confidence":0.95,\
"source_message":1,"supersedes":[],"tags":["priya","college roommate","friend"]},
    {"id":"n2","fact":"Priya started a new job at a biotech startup in San Francisco in March 2023",\
"type":"fact","confidence":0.9,"source_message":1,"supersedes":[],"tags":["priya","job","biotech",\
"startup","san francisco","career","march 2023","new job"]},
    {"id":"n3","fact":"San Francisco","type":"place","confidence":0.9,"source_message":1,\
"supersedes":[],"tags":["san francisco","sf","california"]},
    {"id":"n4","fact":"Priya feels excited about the new job — big pay increase but also more responsibility",\
"type":"fact","confidence":0.9,"source_message":1,"supersedes":[],"tags":["priya","excited",\
"pay raise","responsibility","feelings","job"]},
    {"id":"n5","fact":"Marcus is Speaker B's brother","type":"person","confidence":0.95,\
"source_message":3,"supersedes":[],"tags":["marcus","brother","family"]},
    {"id":"n6","fact":"Marcus graduated from community college in March 2023",\
"type":"event","confidence":0.95,"source_message":3,"supersedes":[],"tags":["marcus","graduated",\
"community college","graduation","march 2023","education"]},
    {"id":"n7","fact":"Chloe is Marcus's new girlfriend as of approximately February 2023 \
(two months before April 2023)","type":"fact","confidence":0.8,"source_message":3,"supersedes":[],\
"tags":["marcus","chloe","dating","girlfriend","relationship","february 2023"]},
    {"id":"n8","fact":"Chloe","type":"person","confidence":0.8,"source_message":3,\
"supersedes":[],"tags":["chloe","marcus","girlfriend"]}
  ],
  "edges": [
    {"from":"n2","to":"n1","relation":"involves"},
    {"from":"n2","to":"n3","relation":"located_at"},
    {"from":"n4","to":"n2","relation":"references"},
    {"from":"n6","to":"n5","relation":"involves"},
    {"from":"n7","to":"n5","relation":"involves"},
    {"from":"n7","to":"n8","relation":"involves"}
  ]
}"""
        ),
        # Example 2: plan + outcome + relationship_update
        # Targets: multi-session (25%), adversarial (15%), explicit (30%)
        # Key lesson: future intentions are plan nodes (confidence 0.7); relationship milestones
        # (engagement, breakup) are relationship_update nodes, NOT fact or event; outcomes link
        # back to the originating plan with a follows edge.
        (
            """\
[Session: 3 | Date: 2023-07-20]
[0] Speaker B: I'm planning to do a solo trip to Japan this fall — probably October. \
Tokyo and maybe Kyoto for a few days.
[1] Speaker A: Amazing! Big news from you lately?
[2] Speaker B: Oh — my friend Kayla and her boyfriend Derek just got engaged last weekend! \
They've been together about four years. They're thinking spring wedding but nothing's set.
[3] Speaker A: Congrats to them! How do you feel about the trip?
[4] Speaker B: Honestly nervous, it's my first solo trip abroad. But really excited too.""",
            """\
{
  "nodes": [
    {"id":"n1","fact":"Speaker B plans a solo trip to Japan in October 2023, visiting Tokyo and Kyoto",\
"type":"plan","confidence":0.7,"source_message":0,"supersedes":[],"tags":["japan","trip","travel",\
"solo","october 2023","fall","tokyo","kyoto","vacation","abroad"]},
    {"id":"n2","fact":"Tokyo","type":"place","confidence":0.9,"source_message":0,\
"supersedes":[],"tags":["tokyo","japan","city"]},
    {"id":"n3","fact":"Kyoto","type":"place","confidence":0.9,"source_message":0,\
"supersedes":[],"tags":["kyoto","japan","city"]},
    {"id":"n4","fact":"Kayla is Speaker B's friend","type":"person","confidence":0.95,\
"source_message":2,"supersedes":[],"tags":["kayla","friend"]},
    {"id":"n5","fact":"Derek is Kayla's boyfriend","type":"person","confidence":0.95,\
"source_message":2,"supersedes":[],"tags":["derek","kayla","boyfriend"]},
    {"id":"n6","fact":"Kayla and Derek got engaged in July 2023 after approximately four years together",\
"type":"relationship_update","confidence":0.95,"source_message":2,"supersedes":[],"tags":["kayla",\
"derek","engaged","engagement","july 2023","four years","relationship milestone"]},
    {"id":"n7","fact":"Kayla and Derek are considering a spring wedding but no date is set yet",\
"type":"plan","confidence":0.7,"source_message":2,"supersedes":[],"tags":["kayla","derek","wedding",\
"spring","plan","engaged"]},
    {"id":"n8","fact":"Speaker B feels nervous and excited about the Japan trip — first solo trip abroad",\
"type":"fact","confidence":0.9,"source_message":4,"supersedes":[],"tags":["japan","trip","nervous",\
"excited","solo","abroad","feelings"]}
  ],
  "edges": [
    {"from":"n1","to":"n2","relation":"involves"},
    {"from":"n1","to":"n3","relation":"involves"},
    {"from":"n6","to":"n4","relation":"involves"},
    {"from":"n6","to":"n5","relation":"involves"},
    {"from":"n7","to":"n6","relation":"follows"},
    {"from":"n8","to":"n1","relation":"references"}
  ]
}"""
        ),
        # Example 3: fact update (adversarial) — changed situation with updates edge + prior-state tags
        # Targets: adversarial (15%), explicit (30%)
        # Key lesson: when a person's situation changes, create a node for the PRIOR STATE and a
        # separate node for the NEW STATE. The new-state node sets supersedes on the prior-state
        # node and adds an updates edge. Tags on the new-state node must include prior-state vocab
        # (e.g. "coffee shop") so queries about the old state still find the transition.
        (
            """\
[Session: 7 | Date: 2023-11-10]
[0] Speaker A: Wasn't Marcus still working at the coffee shop?
[1] Speaker B: He quit in September actually — he got a full-time job at a logistics company, \
doing warehouse management. It pays a lot better.
[2] Speaker A: Good for him. And is he still living at home?
[3] Speaker B: No, he finally moved out! He found an apartment in the same neighborhood, \
moved in early November. He seems really happy about it.""",
            """\
{
  "nodes": [
    {"id":"n1","fact":"Marcus previously worked at a coffee shop (before September 2023)",\
"type":"fact","confidence":0.9,"source_message":1,"supersedes":[],"tags":["marcus","coffee shop",\
"job","previous job","work"]},
    {"id":"n2","fact":"Marcus started a full-time warehouse management job at a logistics company \
in September 2023","type":"fact","confidence":0.95,"source_message":1,"supersedes":["n1"],\
"tags":["marcus","logistics","warehouse management","full-time","job","career","september 2023",\
"coffee shop"]},
    {"id":"n3","fact":"Marcus's logistics job pays significantly more than his previous coffee shop role",\
"type":"fact","confidence":0.9,"source_message":1,"supersedes":[],"tags":["marcus","pay","salary",\
"logistics","coffee shop","raise","income"]},
    {"id":"n4","fact":"Marcus previously lived at his parents' house (before November 2023)",\
"type":"fact","confidence":0.9,"source_message":3,"supersedes":[],"tags":["marcus","parents house",\
"living situation","home","family"]},
    {"id":"n5","fact":"Marcus moved into his own apartment in the same neighborhood in early November 2023",\
"type":"event","confidence":0.95,"source_message":3,"supersedes":["n4"],"tags":["marcus","moved",\
"apartment","neighborhood","november 2023","moved out","parents house","independent living"]},
    {"id":"n6","fact":"Marcus seems happy after moving into his own apartment",\
"type":"fact","confidence":0.8,"source_message":3,"supersedes":[],"tags":["marcus","happy",\
"feelings","apartment","moved out"]}
  ],
  "edges": [
    {"from":"n2","to":"n1","relation":"updates"},
    {"from":"n3","to":"n2","relation":"references"},
    {"from":"n5","to":"n4","relation":"updates"},
    {"from":"n6","to":"n5","relation":"references"}
  ]
}"""
        ),
    ],
)


import dataclasses as _dc

# Variant of episodic_personal with date resolution rules removed from the extraction prompt.
# Used for the engram_no_temporal ablation: relative date phrases are kept as-is in fact text
# so the benchmark can measure the accuracy contribution of temporal resolution.
EPISODIC_PERSONAL_NO_DATES = _dc.replace(
    EPISODIC_PERSONAL,
    name="episodic_personal_no_dates",
    extraction_focus=(
        "PERSONAL CONVERSATION EXTRACTION FOCUS — apply these rules on every turn:\n\n"
        "PEOPLE: Create a person node for every named individual on their FIRST mention, even if the "
        "only known facts are their name and relationship to the speaker. Never absorb a person into "
        "another node.\n\n"
        "DATES & TIMEFRAMES: Preserve relative time references exactly as spoken in the fact text "
        "('last week', 'yesterday', 'last month', etc.). Do NOT resolve them to absolute dates. "
        "Undated events should still be extracted.\n\n"
        "PLANS & INTENTIONS: Any expression of future intention is a plan node — 'I want to', 'we're "
        "going to', 'I plan to', 'hopefully', 'I might', 'thinking about'. Include who, what, and "
        "when (if stated). Confidence: 0.7.\n\n"
        "OUTCOMES: Any report of how something turned out is an outcome node — 'it turned out', "
        "'ended up', 'finally', 'unfortunately it', 'it worked'. Link to the originating event or "
        "plan with a follows edge. Confidence: 0.9.\n\n"
        "RELATIONSHIP SCAN: After drafting all other nodes, explicitly check: did anything CHANGE "
        "between two named people in this turn? Check for engagements, breakups, reunions after "
        "a long separation, one person becoming a caregiver or emotional anchor, major conflicts, "
        "reconciliations, a friendship noticeably deepening or cooling. Each 'yes' gets a "
        "relationship_update node. Static descriptions ('X is Y's sister') are facts, not updates.\n\n"
        "TAGGING: Every node must include the names of all people it involves as tags. A health "
        "fact about Jordan must have 'jordan' in tags. A plan involving both Alex and Sam must "
        "have both names in tags. This is mandatory — it is how person-scoped queries work.\n\n"
        "SPEAKER SELF-TAGGING: When the transcript uses a consistent speaker label for the main "
        "speaker (e.g. 'User:', 'Speaker A:'), treat that label as their name:\n"
        "  1. Create a person node on the FIRST turn they appear: {\"fact\": \"User is the main "
        "speaker\", \"type\": \"person\", \"tags\": [\"user\", \"speaker\", \"main speaker\"]}\n"
        "  2. Tag EVERY fact, preference, plan, event, and outcome stated by that speaker with "
        "their role label (lowercased, e.g. 'user'). Example: 'User: I love hiking' → tags must "
        "include 'user'. This is required even if the speaker is never referred to by personal name."
    ),
)


MEDICAL_CLINICAL = DomainProfile(
    name="medical_clinical",
    node_types={
        "condition": (
            "a diagnosis, disease, disorder, or symptom — include onset, severity, and "
            "status (active, resolved, chronic) if stated."
        ),
        "medication": (
            "a drug, supplement, or therapeutic agent — capture name, dose, route, "
            "frequency, and indication when mentioned."
        ),
        "procedure": (
            "a clinical intervention, test, surgery, or diagnostic study — include "
            "the type, body site, date, and result or finding if reported."
        ),
        "finding": (
            "a clinical observation, lab value, vital sign, or imaging result — "
            "record the measurement or description and the date if given."
        ),
        "care_plan": (
            "a treatment plan, follow-up instruction, referral, or clinical goal — "
            "include who is responsible and the timeline if stated."
        ),
        "provider": (
            "a clinician, specialist, facility, or care team — capture role/specialty "
            "and any stated relationship to the patient."
        ),
        "allergy": (
            "a documented allergy or adverse reaction — include the agent, reaction "
            "type, and severity."
        ),
        "history": (
            "a past medical, surgical, family, or social history item — include the "
            "condition or event and approximate timeframe."
        ),
    },
    edge_relations={
        "treats": "source medication or procedure is used to manage the target condition",
        "indicates": "target condition or finding is the reason for the source medication/procedure",
        "contraindicated_with": "source and target should not be used together",
        "ordered_for": "source procedure or medication was ordered in response to target finding/condition",
        "involves": "source encounter or care plan involves the target provider, condition, or body site",
        "updates": "source node revises or supersedes the target (use for changing conditions or plans)",
    },
    node_types_note=(
        "Always tag nodes with the patient identifier so all facts about a patient are "
        "retrievable together. When a condition or medication status changes, create a new "
        "node with updates: [<old_node_id>] rather than overwriting."
    ),
)

LEGAL = DomainProfile(
    name="legal",
    node_types={
        "case": (
            "a legal proceeding, dispute, or matter — include case name/number, "
            "jurisdiction, court, and current status."
        ),
        "statute": (
            "a law, regulation, code section, or rule — include the jurisdiction, "
            "citation, and the legal requirement or prohibition it establishes."
        ),
        "party": (
            "a named entity in a legal matter — person, company, or organization — "
            "with their role (plaintiff, defendant, counsel, etc.)."
        ),
        "claim": (
            "a legal cause of action, allegation, or theory of liability — include "
            "the legal basis, elements asserted, and which party advances it."
        ),
        "argument": (
            "a legal argument, motion, or position advanced by a party — capture "
            "the reasoning and the relief or outcome sought."
        ),
        "ruling": (
            "a court decision, order, judgment, or holding — include the court, "
            "date, outcome, and which claims or issues it resolves."
        ),
        "precedent": (
            "a cited case or authority that supports or constrains legal analysis — "
            "include citation, holding, and how it applies."
        ),
        "obligation": (
            "a contractual duty, legal requirement, or compliance obligation — "
            "include who owes it, to whom, and the deadline or trigger."
        ),
    },
    edge_relations={
        "cites": "source argument, ruling, or claim cites the target precedent or statute as authority",
        "rules_on": "source ruling decides or addresses the target claim or argument",
        "involves": "source case or proceeding involves the target party",
        "affirms": "source ruling affirms (upholds) the target ruling or lower-court decision",
        "reverses": "source ruling reverses or overturns the target ruling",
        "establishes": "source statute or ruling establishes the target obligation or right",
        "relates_to": "source and target are legally related without a more specific structural link",
    },
    node_types_note=(
        "Always tag nodes with all party names and the case citation so retrieval works "
        "from any angle. When a ruling modifies or overrules an earlier ruling, link with "
        "affirms or reverses rather than creating a standalone node."
    ),
)

MEETING_NOTES = DomainProfile(
    name="meeting_notes",
    node_types={
        "action_item": (
            "a task or commitment made during the meeting — include the assignee, "
            "deliverable, and deadline if stated."
        ),
        "decision": (
            "a choice or agreement reached during the meeting — capture what was "
            "decided, who decided it, and the rationale if given."
        ),
        "topic": (
            "a subject, agenda item, or discussion thread covered in the meeting — "
            "include a brief summary of what was discussed."
        ),
        "blocker": (
            "an impediment, risk, or open dependency that is preventing progress — "
            "include what is blocked, why, and who owns resolution."
        ),
        "update": (
            "a status report or progress update on ongoing work — include the project "
            "or area, current state, and any notable change."
        ),
        "question": (
            "an open question raised but not resolved during the meeting — include "
            "who asked it and what information is needed."
        ),
        "participant": (
            "a person who attended or was mentioned as a stakeholder — include their "
            "role and any commitments they made."
        ),
        "goal": (
            "a stated objective, success criterion, or desired outcome — include the "
            "scope (project, sprint, quarter) and owner if given."
        ),
    },
    edge_relations={
        "assigned_to": "source action item or goal is owned by the target participant",
        "relates_to": "source item is topically connected to the target topic or project",
        "blocks": "source blocker prevents progress on the target action item or goal",
        "resolves": "source decision or action item resolves the target question or blocker",
        "follows_up_on": "source item is a follow-up to the target from a previous meeting",
        "owns": "source participant is responsible for the target topic, goal, or update",
    },
    node_types_note=(
        "Always tag action items with the assignee's name. Tag items with the meeting "
        "date or sprint/project name so retrieval scoped to a time period works correctly. "
        "When an action item from a prior meeting is revisited, link with follows_up_on."
    ),
)

ACADEMIC_RESEARCH = DomainProfile(
    name="academic_research",
    node_types={
        "paper": (
            "a research publication, preprint, or technical report — include title, "
            "authors, venue/year, and the core contribution."
        ),
        "method": (
            "a technique, algorithm, model architecture, or experimental approach — "
            "describe what it does and when it should be used."
        ),
        "dataset": (
            "a benchmark, corpus, or evaluation dataset — include domain, size, task "
            "type, and any known limitations."
        ),
        "result": (
            "an empirical finding or quantitative outcome — include the metric, value, "
            "dataset, and comparison baseline."
        ),
        "task": (
            "a research problem, benchmark task, or evaluation setting — include the "
            "input/output specification and standard metrics."
        ),
        "concept": (
            "a theoretical idea, hypothesis, or named phenomenon — describe the "
            "definition and its significance to the field."
        ),
        "claim": (
            "a stated assertion about the world, a model, or a method — distinguish "
            "empirical claims (backed by results) from theoretical ones."
        ),
        "limitation": (
            "a stated weakness, failure mode, or scope boundary of a method or "
            "result — include what conditions trigger the limitation."
        ),
    },
    edge_relations={
        "proposes": "source paper introduces or defines the target method, concept, or claim",
        "evaluates_on": "source paper or method is tested against the target dataset or task",
        "outperforms": "source method achieves better results than the target on a shared benchmark",
        "cites": "source paper cites the target paper as prior work or evidence",
        "addresses": "source method or paper targets the target task or limitation",
        "supports": "source result or evidence supports the target claim",
        "contradicts": "source result or argument contradicts the target claim or finding",
        "extends": "source method or paper builds directly on and extends the target",
    },
    node_types_note=(
        "Always tag nodes with author surnames, venue abbreviation, and year so citation "
        "queries work. When reporting results, always link the result node to both the "
        "method and dataset so comparisons are traversable."
    ),
)

FINANCIAL = DomainProfile(
    name="financial",
    node_types={
        "company": (
            "a public or private firm, subsidiary, or business entity — include ticker, "
            "sector, and any relevant size or classification."
        ),
        "metric": (
            "a financial or operating KPI — include the name, value, period, and "
            "year-over-year or sequential change if stated."
        ),
        "guidance": (
            "forward-looking management guidance or analyst estimate — include the "
            "metric, range, period, and any stated assumptions or risks."
        ),
        "risk": (
            "a stated business risk, headwind, or uncertainty — include the source "
            "(macro, regulatory, competitive) and potential impact."
        ),
        "transaction": (
            "an M&A deal, investment, divestiture, buyback, or financing event — "
            "include parties, size, terms, and status."
        ),
        "segment": (
            "a business line, product category, or geographic region — include its "
            "contribution to revenue or earnings and growth trajectory."
        ),
        "market": (
            "an industry, end market, or competitive landscape — include size, "
            "growth rate, and key dynamics affecting the company."
        ),
        "regulatory": (
            "a regulatory action, compliance requirement, legal proceeding, or policy "
            "change affecting the company — include status and financial exposure."
        ),
    },
    edge_relations={
        "reports": "source company reports the target metric or segment result for a period",
        "guides_to": "source company issues the target guidance for a future period",
        "faces": "source company faces the target risk or regulatory issue",
        "announces": "source company announces the target transaction or strategic initiative",
        "operates_in": "source company or segment operates in the target market",
        "updates": "source metric or guidance revises the target from a prior period",
        "impacts": "source risk, market, or regulatory item materially impacts the target metric or segment",
    },
    node_types_note=(
        "Always tag metric and guidance nodes with the company ticker and fiscal period "
        "(e.g. 'Q3 FY2024') so time-series queries work. When guidance is revised, "
        "create a new guidance node with updates: [<prior_guidance_id>]."
    ),
)


CUSTOMER_SUPPORT = DomainProfile(
    name="customer_support",
    node_types={
        "issue": (
            "a problem, complaint, or request reported by a customer — include the "
            "product/feature affected, error message or symptom, and severity."
        ),
        "resolution": (
            "a fix, workaround, or answer provided to resolve an issue — include "
            "the steps taken, who resolved it, and whether the customer confirmed it worked."
        ),
        "customer": (
            "an end-user or account involved in the support interaction — capture "
            "name, account tier/plan, and any relevant account history mentioned."
        ),
        "product": (
            "a product, feature, service, or component that is the subject of the "
            "support interaction — include version or configuration details if stated."
        ),
        "escalation": (
            "a handoff to a higher support tier, specialist team, engineering, or "
            "management — include the reason, urgency, and who it was escalated to."
        ),
        "workaround": (
            "a temporary solution or mitigation that addresses the symptom without "
            "fully resolving the root cause — note limitations and expected fix timeline."
        ),
        "policy": (
            "a support policy, SLA commitment, refund rule, or contractual obligation "
            "referenced during the interaction — include the specific terms cited."
        ),
        "feedback": (
            "a customer's stated opinion, feature request, or product feedback — "
            "distinguish praise from complaint and capture the specific suggestion."
        ),
    },
    edge_relations={
        "affects": "source issue affects the target product, feature, or customer account",
        "resolves": "source resolution or workaround resolves or mitigates the target issue",
        "escalates_to": "source issue or case is escalated to the target team or specialist",
        "references": "source resolution or response references the target policy or documentation",
        "follows_up_on": "source interaction is a follow-up to the target prior issue or ticket",
        "reported_by": "source issue was reported by the target customer",
    },
    node_types_note=(
        "Always tag nodes with the customer account identifier and ticket/case ID so all "
        "interactions for an account are retrievable together. When an issue recurs, link "
        "the new issue node to the prior one with follows_up_on."
    ),
)

EDUCATION_TUTORING = DomainProfile(
    name="education_tutoring",
    node_types={
        "concept": (
            "a topic, principle, theorem, or skill that is being taught or learned — "
            "include the subject area and the level of understanding demonstrated."
        ),
        "misconception": (
            "an incorrect belief, common error, or knowledge gap the learner holds — "
            "capture the specific wrong idea and what the correct understanding is."
        ),
        "explanation": (
            "a teaching explanation, analogy, or worked example used to clarify a "
            "concept — note the approach (analogy, diagram, derivation) and whether it landed."
        ),
        "exercise": (
            "a practice problem, homework question, or assessment item — include the "
            "topic, difficulty, and the learner's approach or answer."
        ),
        "student": (
            "a learner in the interaction — capture name, grade/level, subject, goals, "
            "and any stated strengths or struggles."
        ),
        "learning_goal": (
            "an explicit objective, exam preparation target, or skill the student is "
            "working toward — include the timeline (upcoming exam, end of unit, etc.)."
        ),
        "progress": (
            "an observed change in understanding or skill — note what the student "
            "could not do before and can do now, or what still needs work."
        ),
        "assignment": (
            "a homework task, project, or reading assigned during the session — include "
            "the deliverable, due date, and purpose."
        ),
    },
    edge_relations={
        "prerequisite_for": "target concept or skill requires mastery of the source first",
        "clarifies": "source explanation or analogy clarifies the target concept",
        "addresses": "source exercise or explanation addresses the target misconception",
        "assigned_to": "source assignment is given to the target student",
        "assesses": "source exercise or question tests understanding of the target concept",
        "leads_to": "mastery of source concept or skill leads to the target learning goal",
    },
    node_types_note=(
        "Always tag nodes with the student's name and subject area. Link misconceptions "
        "to the concepts they concern so gap analysis is traversable. When a student "
        "demonstrates mastery, create a progress node linked to the relevant concept."
    ),
)

NEWS_EVENTS = DomainProfile(
    name="news_events",
    node_types={
        "event": (
            "a newsworthy occurrence — include what happened, where, when, who was "
            "involved, and the immediate reported outcome or impact."
        ),
        "actor": (
            "a person, organization, government, or institution that took action or "
            "is central to an event — include their role and stated position."
        ),
        "statement": (
            "a public statement, quote, press release, or official communication — "
            "include who said it, in what context, and the key assertion."
        ),
        "policy": (
            "a law, regulation, executive action, or official policy being reported "
            "on — include jurisdiction, status (proposed/enacted/repealed), and effect."
        ),
        "consequence": (
            "a reported impact, reaction, or downstream effect of an event — include "
            "who or what is affected and the magnitude or timeline."
        ),
        "context": (
            "background information, historical precedent, or ongoing situation that "
            "frames the current event — note how it connects to the reported story."
        ),
        "claim": (
            "a factual assertion made in reporting — distinguish verified facts from "
            "allegations, disputed claims, or attributed statements."
        ),
        "source": (
            "a named source, publication, official report, or data source cited in "
            "the reporting — include their relationship to the story and credibility note."
        ),
    },
    edge_relations={
        "involves": "source event involves the target actor as participant, subject, or affected party",
        "caused_by": "source event or consequence was caused or triggered by the target event or actor",
        "responds_to": "source statement or action is a response to the target event or statement",
        "cites": "source claim or article cites the target source or prior event as evidence",
        "updates": "source event or claim revises or supersedes the target earlier report or claim",
        "relates_to": "source and target are part of the same ongoing story without a direct causal link",
    },
    node_types_note=(
        "Always tag nodes with named actors and the date/location of the event. "
        "Distinguish verified facts from claims and allegations — use claim nodes for "
        "unverified assertions. When a story develops, link new event nodes to prior "
        "ones with updates or caused_by rather than overwriting."
    ),
)


AGENTIC_WORKFLOW = DomainProfile(
    name="agentic_workflow",
    node_types={
        "tool_call": (
            "a call made to an external tool, API, file system, or capability — include "
            "the tool name, inputs provided, result or output received, and whether it "
            "succeeded or failed. Capture error messages verbatim when present."
        ),
        "agent_decision": (
            "a choice made by the agent about how to proceed — include what was decided, "
            "the alternatives considered or rejected, the reasoning, and the outcome. "
            "Use for: model selection, approach selection, task delegation, retry vs. "
            "abort decisions."
        ),
        "workflow_step": (
            "a discrete, named phase or step in a multi-step agentic task — describe what "
            "was accomplished, what it depended on, and what it enables next. Use for "
            "clearly bounded stages in a pipeline or plan."
        ),
        "prompt_template": (
            "a system prompt, task prompt, persona, or reusable instruction template — "
            "include its purpose, any constraints or persona it establishes, and the context "
            "in which it is applied. Capture notable rules or boundaries verbatim."
        ),
        "model_config": (
            "a model selection, parameter setting, or inference configuration — include "
            "model name or family, temperature, context window size, or any other settings "
            "that affect generation behavior. Note the rationale if given."
        ),
        "task_decomposition": (
            "a breakdown of a complex or ambiguous goal into concrete subtasks — include "
            "the parent goal, the subtasks identified, the decomposition rationale, and "
            "any ordering constraints between subtasks."
        ),
        "error_recovery": (
            "a failure, unexpected output, or tool error and the agent's response to it — "
            "include what failed, the root cause if identified, and the recovery strategy "
            "chosen (retry, fallback, escalate, abandon)."
        ),
        "capability": (
            "a named tool, skill, integration, or agent capability available in the "
            "workflow — describe what it does, its inputs and outputs, any known limitations "
            "or rate limits, and when it should or should not be used."
        ),
        "evaluation": (
            "an assessment of whether a workflow run, agent output, or sub-task met its "
            "success criteria — include what was evaluated, the criteria or rubric used, "
            "the result (pass/fail/score), and any notable failure modes or edge cases "
            "observed. Use for evals pipelines, automated grading, and human review."
        ),
        "artifact": (
            "the produced output of a workflow run or agent task — a document, code file, "
            "dataset, report, image, or other concrete deliverable. Include the artifact "
            "type, what produced it, what task or goal it satisfies, and any quality or "
            "format notes. Tag with the workflow name and run identifier so all outputs "
            "of a run are retrievable together."
        ),
    },
    edge_relations={
        "executes": "source workflow step or agent decision executes the target tool call or capability",
        "produces": "source tool call or step produces the target output, artifact, or state change",
        "follows": "source step follows the target step in the workflow sequence",
        "informs": "source tool call result or observation informs the target agent decision",
        "recovers_from": "source error_recovery node addresses and responds to the target error or failure",
        "uses": "source workflow step or agent decision uses the target prompt template or model config",
        "decomposes_into": "source goal or task_decomposition breaks the target complex goal into subtasks",
        "supersedes": "source prompt template or model config replaces the target earlier version",
        "conflicts_with": "source and target decisions or configs are in active unresolved tension",
    },
    node_types_note=(
        "Always tag nodes with the agent name, workflow name, or task identifier so all "
        "steps of a workflow are retrievable together. Tag tool_call nodes with the tool "
        "name even when the call failed. Tag error_recovery nodes with both the error type "
        "and the recovery strategy so failure patterns are queryable."
    ),
)


CREATIVE_WRITING = DomainProfile(
    name="creative_writing",
    node_types={
        "character": (
            "a named character in the story — create one node per character on their first "
            "mention. Include name, role (protagonist, antagonist, supporting, etc.), key "
            "personality traits, core motivation, and relationship to other characters. "
            "Do NOT split a character across multiple nodes."
        ),
        "plot_event": (
            "a significant story event, scene, or narrative beat — include who is involved, "
            "what happens, the setting, and its consequence for the characters or plot. "
            "Use for events that change something: circumstances, relationships, character "
            "understanding, or the direction of the story."
        ),
        "world_detail": (
            "a piece of worldbuilding — a location, culture, rule of the world, piece of "
            "history, or physical law — include how it affects the story, characters, or "
            "the constraints it places on the narrative."
        ),
        "relationship": (
            "the dynamic or history between two named characters — include the current "
            "state of the relationship, its history, the tension or bond at its core, and "
            "how it drives the narrative."
        ),
        "theme": (
            "a recurring motif, symbol, or thematic concern in the story — include how it "
            "manifests across scenes or characters, and what the story seems to be saying "
            "about it."
        ),
        "narrative_choice": (
            "a craft decision made about the story — POV, tense, structure, genre "
            "convention, chapter structure, narrative distance, unreliable narrator — "
            "include the rationale and the intended effect on the reader."
        ),
        "conflict": (
            "an internal or external conflict driving a character or the plot — include "
            "who is in conflict, what is at stake, and the current state of resolution. "
            "Use for: character vs. self, character vs. character, character vs. world."
        ),
        "arc": (
            "a character arc or plot arc — describe the starting state, intended "
            "trajectory, key turning points, and endpoint. Distinguish character arcs "
            "(internal transformation) from plot arcs (external sequence of events)."
        ),
    },
    edge_relations={
        "involves": "source plot event or conflict involves the target character as a participant",
        "located_in": "source event or scene takes place in the target world detail (location or setting)",
        "drives": "source conflict or relationship drives the target plot event or character arc",
        "manifests": "source plot event or character choice manifests or develops the target theme",
        "precedes": "source event or arc beat precedes the target in the story timeline",
        "develops": "source plot event advances or complicates the target character arc",
        "contradicts": "source character action contradicts an established trait (flag for consistency review)",
        "relates_to": "source and target are connected without a more specific structural link",
    },
    node_types_note=(
        "Always tag nodes with character names and the story or project title so queries "
        "scoped to a project or character work correctly. Maintain one character node per "
        "named character — do not create duplicates. When a character or world element is "
        "revised, create a new node and link with supersedes rather than overwriting, to "
        "preserve the evolution of creative choices."
    ),
)


PRODUCT_MANAGEMENT = DomainProfile(
    name="product_management",
    node_types={
        "feature": (
            "a product capability, enhancement, or new behavior — include the user problem "
            "it solves, the target user segment, scope boundaries, and current status "
            "(proposed, planned, in progress, shipped, cut). Note the motivating insight "
            "or request if stated."
        ),
        "user_story": (
            "a structured user need — capture the user type, desired action or outcome, "
            "and the business value; link to the feature it belongs to. Include acceptance "
            "criteria or definition of done if stated."
        ),
        "decision": (
            "a product decision — what was decided, who made it, the alternatives that were "
            "considered and rejected, and the reasoning. Use for scope cuts, prioritization "
            "calls, positioning choices, and architecture direction from a product angle."
        ),
        "priority": (
            "a stated prioritization of work relative to other work — include what is being "
            "elevated or deprioritized, the competing items, and the criteria used (impact, "
            "effort, strategic fit, urgency)."
        ),
        "hypothesis": (
            "an assumption or bet about user behavior, market conditions, or product-market "
            "fit — include the stated belief, what evidence exists for it, and how it would "
            "be validated or invalidated."
        ),
        "insight": (
            "a finding from user research, analytics, customer feedback, or competitive "
            "analysis — include the source, what was learned, and the product implication "
            "or action it informs."
        ),
        "stakeholder": (
            "a named person, team, or organization with a stake in the product — include "
            "their role, goals, and any stated concerns, blockers, or commitments."
        ),
        "constraint": (
            "a scope boundary, resource limitation, compliance requirement, or "
            "non-negotiable — include the source of the constraint and how it shapes "
            "the product decisions or timeline."
        ),
        "metric": (
            "a KPI, north star metric, or success measure being tracked for a feature, "
            "initiative, or product area — include the metric name, how it is measured, "
            "the current baseline, the target, and the owner. Link to the features or "
            "hypotheses it validates via 'validates' edges."
        ),
        "persona": (
            "a named user segment — a distinct group of users with shared goals, behaviors, "
            "or characteristics (e.g. 'power user', 'new hire', 'data team lead'). Include "
            "the segment name, defining characteristics, key jobs-to-be-done, and how it "
            "differs from adjacent segments. Distinct from stakeholder (internal actors) "
            "and insight (a research finding). A reusable reference node that multiple "
            "features and user stories can link to."
        ),
    },
    edge_relations={
        "solves": "source feature or decision addresses the target user problem or hypothesis",
        "depends_on": "source feature or work item requires the target to be complete first",
        "informed_by": "source decision or priority is informed by the target insight or research",
        "assigned_to": "source feature or action item is owned by the target stakeholder or team",
        "validates": "source shipped feature or experiment validates or invalidates the target hypothesis",
        "supersedes": "source decision or priority replaces the target earlier direction",
        "relates_to": "source and target are connected without a more specific structural link",
        "conflicts_with": "source and target priorities or decisions are in active unresolved tension",
    },
    node_types_note=(
        "Always tag features and decisions with the product area, team, and any associated "
        "quarter or milestone (e.g. 'Q3 launch', 'v2.0', 'beta'). When a decision or "
        "priority changes, create a new node with supersedes: [<old_node_id>] so the "
        "full decision history is preserved and traversable."
    ),
)


BUILTIN_PROFILES: dict[str, DomainProfile] = {
    "software_dev": SOFTWARE_DEV,
    "episodic_personal": EPISODIC_PERSONAL,
    "episodic_personal_no_dates": EPISODIC_PERSONAL_NO_DATES,
    "medical_clinical": MEDICAL_CLINICAL,
    "legal": LEGAL,
    "meeting_notes": MEETING_NOTES,
    "academic_research": ACADEMIC_RESEARCH,
    "financial": FINANCIAL,
    "customer_support": CUSTOMER_SUPPORT,
    "education_tutoring": EDUCATION_TUTORING,
    "news_events": NEWS_EVENTS,
    "agentic_workflow": AGENTIC_WORKFLOW,
    "creative_writing": CREATIVE_WRITING,
    "product_management": PRODUCT_MANAGEMENT,
}


def get_profile(name: str) -> DomainProfile:
    """Return a built-in profile by name, raising ValueError if unknown."""
    if name not in BUILTIN_PROFILES:
        raise ValueError(
            f"Unknown domain profile '{name}'. "
            f"Built-in profiles: {sorted(BUILTIN_PROFILES)}"
        )
    return BUILTIN_PROFILES[name]
