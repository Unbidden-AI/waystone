"""Domain profiles for Engram extraction.

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


SOFTWARE_DEV = DomainProfile(
    name="software_dev",
    node_types={
        "decision": "a choice between alternatives that was made",
        "constraint": "a limitation, requirement, or non-negotiable",
        "implementation": "a concrete technical detail that was established",
        "question": "an open question not yet resolved",
        "resolved": "the answer to a previously open question",
        "preference": "a stated preference for future work, not yet decided",
        "lesson_learned": "a failed approach, rejected alternative, or anti-pattern discovered",
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
    },
    node_types_note=(
        'When a "decision" or "transition" node supersedes a prior approach, its tags MUST include '
        "the old term so the decision is retrievable from both the old and new directions. "
        'Example: a decision to switch from JSON to Avro must have tags: ["json", "avro", "event format", ...].'
    ),
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
    extraction_focus=(
        "PERSONAL CONVERSATION EXTRACTION FOCUS — apply these rules on every turn:\n\n"
        "PEOPLE: Create a person node for every named individual on their FIRST mention, even if the "
        "only known facts are their name and relationship to the speaker. Never absorb a person into "
        "another node.\n\n"
        "DATES & TIMEFRAMES: When an event has any stated date or timeframe ('last summer', 'three "
        "weeks ago', 'in 2019', 'next month'), include it verbatim in the event node's fact text and "
        "tags. Undated events should still be extracted — omit the date field rather than guessing.\n\n"
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
        "have both names in tags. This is mandatory — it is how person-scoped queries work."
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


BUILTIN_PROFILES: dict[str, DomainProfile] = {
    "software_dev": SOFTWARE_DEV,
    "episodic_personal": EPISODIC_PERSONAL,
    "medical_clinical": MEDICAL_CLINICAL,
    "legal": LEGAL,
    "meeting_notes": MEETING_NOTES,
    "academic_research": ACADEMIC_RESEARCH,
    "financial": FINANCIAL,
    "customer_support": CUSTOMER_SUPPORT,
    "education_tutoring": EDUCATION_TUTORING,
    "news_events": NEWS_EVENTS,
}


def get_profile(name: str) -> DomainProfile:
    """Return a built-in profile by name, raising ValueError if unknown."""
    if name not in BUILTIN_PROFILES:
        raise ValueError(
            f"Unknown domain profile '{name}'. "
            f"Built-in profiles: {sorted(BUILTIN_PROFILES)}"
        )
    return BUILTIN_PROFILES[name]
