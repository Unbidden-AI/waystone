"""
Generate synthetic domain-specific conversation transcripts for Engram testing.

Each transcript is a realistic multi-turn dialogue designed to exercise the
node/edge types of a given domain profile.

Usage:
    # Generate all domains
    python -m benchmarks.generate_transcripts --output benchmarks/transcripts

    # Generate specific domains
    python -m benchmarks.generate_transcripts --domains episodic_personal medical_clinical \
        --output benchmarks/transcripts

    # Generate multiple transcripts per domain
    python -m benchmarks.generate_transcripts --count 3 --output benchmarks/transcripts
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))

from engram.config import load_config
pass  # imports resolved inside functions


# ---------------------------------------------------------------------------
# Per-domain generation scenarios
# ---------------------------------------------------------------------------

DOMAIN_SCENARIOS: dict[str, list[dict]] = {
    "episodic_personal": [
        {
            "slug": "family_reunion",
            "prompt": (
                "Write a realistic multi-turn conversation (20-30 turns) between two "
                "close friends named Maya and Jordan catching up after 6 months apart. "
                "They discuss: Maya's new job at a tech startup, Jordan's wedding planning "
                "with fiancé Sam, a family health scare with Maya's mother, a trip Jordan "
                "took to Portugal, and their shared memories of college. Include specific "
                "dates, place names, and personal details. Format as 'Speaker: text' lines."
            ),
        },
        {
            "slug": "life_update",
            "prompt": (
                "Write a realistic multi-turn conversation (20-30 turns) between siblings "
                "Alex (older, 32) and Riley (younger, 27) during a phone call. They discuss: "
                "Riley's recent move to Austin from Chicago, Alex's promotion at work, "
                "their parents' upcoming 30th anniversary party, Riley's new hobby (pottery), "
                "and a conflict between Riley and their college roommate. Include specific "
                "details, feelings, and outcomes. Format as 'Speaker: text' lines."
            ),
        },
        {
            "slug": "daily_checkin",
            "prompt": (
                "Write a realistic multi-turn conversation (25-35 turns) between two "
                "roommates, Casey and Drew, over dinner discussing their week. Topics: "
                "Casey's difficult project at work (they're a nurse), Drew's experience "
                "starting a new fitness routine, a neighbor dispute about noise, "
                "planning a road trip to the coast for next month, and Drew's rekindled "
                "romance with an ex. Include specific places, dates, and personal reactions. "
                "Format as 'Speaker: text' lines."
            ),
        },
    ],
    "medical_clinical": [
        {
            "slug": "intake_diabetes",
            "prompt": (
                "Write a realistic clinical intake conversation (20-30 turns) between "
                "Dr. Rivera and a new patient, Mr. Chen (58), presenting for diabetes "
                "management. The conversation covers: chief complaint (poorly controlled "
                "blood glucose), current medications (metformin 1000mg, lisinopril 10mg), "
                "allergies (penicillin — rash), relevant history (hypertension for 8 years, "
                "father had Type 2 diabetes), recent A1C of 8.9%, physical findings, and "
                "a revised care plan (adding empagliflozin, dietitian referral, glucose "
                "monitoring schedule). Format as 'Dr. Rivera: ...' and 'Mr. Chen: ...' turns."
            ),
        },
        {
            "slug": "followup_cardiology",
            "prompt": (
                "Write a realistic cardiology follow-up conversation (20-30 turns) between "
                "Dr. Okonkwo and patient Ms. Torres (64), 3 months post-MI. Covers: "
                "symptom review (mild exertional dyspnea, no chest pain), medication "
                "adherence (atorvastatin 80mg, clopidogrel 75mg, metoprolol 25mg twice daily), "
                "recent echo results (EF improved from 35% to 45%), lifestyle changes, "
                "planned stress test, and adjustment of metoprolol dose. Patient asks about "
                "returning to work. Format as 'Dr. Okonkwo: ...' and 'Ms. Torres: ...' turns."
            ),
        },
    ],
    "legal": [
        {
            "slug": "contract_negotiation",
            "prompt": (
                "Write a realistic legal consultation conversation (20-30 turns) between "
                "attorney Sarah Kim and her client, startup founder Marcus Webb, reviewing "
                "a SaaS vendor contract. Topics: limitation of liability clause (vendor "
                "capping liability at 3 months of fees), data processing agreement concerns "
                "under GDPR/CCPA, auto-renewal clause (90-day notice required), IP ownership "
                "of customer data, indemnification scope, and negotiation strategy. Sarah "
                "identifies 3 critical redlines and 2 preferred changes. "
                "Format as 'Sarah: ...' and 'Marcus: ...' turns."
            ),
        },
        {
            "slug": "employment_dispute",
            "prompt": (
                "Write a realistic legal consultation (20-30 turns) between employment "
                "attorney Diana Reyes and client Priya Sharma, who was terminated after "
                "filing an internal HR complaint about gender pay disparity. Topics: "
                "timeline of events, relevant evidence (emails, performance reviews), "
                "applicable statutes (Title VII, state FEPA), strength of wrongful "
                "termination vs retaliation claim, EEOC filing process, potential damages, "
                "and litigation vs settlement considerations. "
                "Format as 'Diana: ...' and 'Priya: ...' turns."
            ),
        },
    ],
    "meeting_notes": [
        {
            "slug": "sprint_planning",
            "prompt": (
                "Write a realistic sprint planning meeting transcript (25-35 turns) for a "
                "product team at a fintech company. Participants: Nadia (PM), Tom (engineering "
                "lead), Fatima (senior engineer), Chris (QA). Topics: reviewing last sprint "
                "(3 of 5 stories completed, blocker on payment gateway API), capacity for "
                "next sprint, prioritizing between 2 features (transaction history export "
                "vs improved fraud alerts), a decision to defer mobile push notifications, "
                "action items with owners and due dates, and a question about design resources. "
                "Format as 'Name: text' lines."
            ),
        },
        {
            "slug": "executive_review",
            "prompt": (
                "Write a realistic quarterly business review meeting transcript (25-35 turns) "
                "with 4 participants: CEO Elena Park, CFO David Nguyen, VP Sales Marco Silva, "
                "VP Engineering Keisha Brown. Topics: Q3 revenue miss (18M vs 20M target), "
                "sales pipeline for Q4, a decision to cut contractor headcount by 20%, "
                "engineering velocity concerns, a blocker on enterprise customer onboarding, "
                "and action items for each VP. Include specific numbers and named customers. "
                "Format as 'Name: text' lines."
            ),
        },
    ],
    "academic_research": [
        {
            "slug": "lab_meeting",
            "prompt": (
                "Write a realistic ML research lab meeting transcript (25-35 turns) where "
                "PhD student Zara presents preliminary results from her work on few-shot "
                "named entity recognition. Participants: Zara, advisor Prof. Huang, "
                "labmates Omar and Lily. Topics: Zara's method (prompt-tuning + in-context "
                "learning), results on CoNLL-2003 (F1 82.3 vs SOTA 93.5), comparison to "
                "prior work (SetFit, ICL-NER), weakness on low-resource languages, Prof. "
                "Huang's suggestion to add cross-lingual transfer experiments, discussion "
                "of the ACL submission deadline, and next steps. "
                "Format as 'Name: text' lines."
            ),
        },
        {
            "slug": "paper_review",
            "prompt": (
                "Write a realistic research discussion (20-30 turns) between two NLP "
                "researchers, Aiden and Priya, reading and critiquing a new paper on "
                "long-context reasoning. They discuss: the paper's main claim (their model "
                "outperforms GPT-4 on 128k context tasks), methodology concerns (evaluation "
                "set contamination risk), benchmark selection (SCROLLS vs HELMET), the "
                "ablation study quality, what the paper proves vs claims, and whether to "
                "cite it in their own upcoming submission. "
                "Format as 'Aiden: text' and 'Priya: text' lines."
            ),
        },
    ],
    "financial": [
        {
            "slug": "earnings_call",
            "prompt": (
                "Write a realistic earnings call transcript (25-35 turns) for fictional "
                "company CloudVault Inc (ticker: CVLT), a B2B SaaS company. Participants: "
                "CEO Jennifer Holt, CFO Robert Tanaka, and analysts from Morgan Stanley and "
                "Goldman Sachs. Topics: Q3 results (ARR $340M up 28% YoY, net revenue "
                "retention 118%, operating loss narrowed to -$12M), guidance raised for FY "
                "($1.28B-$1.31B ARR), strength in enterprise segment, headwinds in SMB, "
                "a strategic acquisition announced (DataSync for $85M), and questions about "
                "path to profitability. Format as 'Name/Role: text' lines."
            ),
        },
    ],
    "customer_support": [
        {
            "slug": "billing_dispute",
            "prompt": (
                "Write a realistic customer support chat conversation (20-30 turns) between "
                "support agent Taylor and customer Morgan, who was double-charged for a "
                "subscription. Topics: account verification, identifying the duplicate "
                "charge ($49.99 on Nov 3 and Nov 5), Morgan's frustration and threat to "
                "cancel, Taylor investigating and confirming the error, issuing a refund "
                "(3-5 business days), offering a goodwill 10% discount on next month, "
                "and Morgan's eventual satisfaction. Include realistic support workflow. "
                "Format as 'Taylor: text' and 'Morgan: text' lines."
            ),
        },
        {
            "slug": "technical_onboarding",
            "prompt": (
                "Write a realistic technical support conversation (20-30 turns) between "
                "support engineer Sam and a new enterprise customer IT admin, Jamie, "
                "setting up SSO integration for their company's 200-person Slack workspace. "
                "Topics: SAML vs OAuth choice, IdP configuration steps (Okta), testing "
                "the connection (certificate mismatch error and fix), setting up group "
                "sync, a question about guest account handling, and confirming successful "
                "login for 3 test users. Format as 'Sam: text' and 'Jamie: text' lines."
            ),
        },
    ],
    "news_events": [
        {
            "slug": "climate_summit",
            "prompt": (
                "Write a realistic news article-style transcript (20-30 turns, formatted as "
                "reporter/anchor dialogue) covering a major climate summit where 40 nations "
                "agreed to new emissions targets. Include: the key agreement (30% reduction "
                "by 2035), holdout nations and their stated reasons, reactions from NGOs and "
                "industry, a specific controversial clause about carbon credits, a quote "
                "from the UN Secretary-General, and a dissenting statement from a fossil "
                "fuel industry coalition. Present as anchor interviewing field correspondent. "
                "Format as 'Anchor: text' and 'Reporter: text' lines."
            ),
        },
        {
            "slug": "tech_antitrust",
            "prompt": (
                "Write a realistic news discussion transcript (20-30 turns) between a "
                "news anchor and a tech policy analyst discussing a major antitrust ruling "
                "against a fictional search company, SearchCorp. Topics: the DOJ ruling "
                "(forced divestiture of advertising business), the company's appeal plans, "
                "market reaction (stock down 18%), what precedent it sets, reactions from "
                "competitors and advertisers, a statement from SearchCorp CEO, and "
                "expected timeline. Format as 'Anchor: text' and 'Analyst: text' lines."
            ),
        },
    ],
    "education_tutoring": [
        {
            "slug": "calculus_tutoring",
            "prompt": (
                "Write a realistic tutoring session transcript (25-35 turns) between "
                "tutor Dr. Martinez and high school student Eli (17), struggling with "
                "related rates in calculus. Topics: diagnosing Eli's confusion (implicit "
                "differentiation), working through a ladder-sliding-down-wall problem step "
                "by step, Eli making an error with the chain rule and being guided to "
                "self-correct, practicing a second problem (expanding balloon), discussing "
                "exam strategy, and assigning practice problems for next session. "
                "Format as 'Dr. Martinez: text' and 'Eli: text' lines."
            ),
        },
        {
            "slug": "history_seminar",
            "prompt": (
                "Write a realistic college seminar discussion (25-35 turns) between "
                "professor Dr. Osei and 3 students (Mia, Carlos, Yuki) about the causes "
                "of WWI. Topics: debating structural vs contingent causes, Mia defending "
                "Fischer thesis, Carlos pushing back with AJP Taylor, Yuki raising the "
                "alliance system, Dr. Osei introducing the 'sleepwalkers' historiography, "
                "connecting to current international relations, and assignment of a "
                "comparative essay due in 2 weeks. "
                "Format as 'Name: text' lines."
            ),
        },
    ],
}


# ---------------------------------------------------------------------------
# Generation logic
# ---------------------------------------------------------------------------

_GENERATION_SYSTEM = (
    "You are a skilled writer generating realistic synthetic conversation transcripts "
    "for use in knowledge graph extraction research. Write natural, domain-appropriate "
    "dialogue with specific details (names, numbers, dates, technical terms). "
    "Do not add any header or preamble — output only the conversation turns."
)


async def generate_transcript(scenario: dict, config: dict) -> str:
    """Generate a single transcript from a scenario prompt."""
    from engram.extractor import _call_llm
    full_prompt = _GENERATION_SYSTEM + "\n\n" + scenario["prompt"]
    return await _call_llm(full_prompt, config)


async def generate_domain(
    domain: str,
    scenarios: list[dict],
    output_dir: Path,
    config: dict,
    verbose: bool = True,
) -> list[Path]:
    """Generate all transcripts for a domain."""
    domain_dir = output_dir / domain
    domain_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for scenario in scenarios:
        slug = scenario["slug"]
        out_path = domain_dir / f"{slug}.md"

        if out_path.exists():
            if verbose:
                print(f"  [skip] {domain}/{slug}.md already exists")
            written.append(out_path)
            continue

        if verbose:
            print(f"  [gen]  {domain}/{slug}.md ...")

        t0 = time.perf_counter()
        try:
            text = await generate_transcript(scenario, config)
            header = f"# {domain} / {slug}\n\n"
            out_path.write_text(header + text + "\n")
            elapsed = time.perf_counter() - t0
            if verbose:
                print(f"         done in {elapsed:.1f}s ({len(text.split())} words)")
            written.append(out_path)
        except Exception as e:
            if verbose:
                print(f"  [err]  {domain}/{slug}.md: {e}")

    return written


async def run_generation(
    domains: list[str],
    output_dir: Path,
    config: dict,
    verbose: bool = True,
) -> dict[str, list[Path]]:
    results: dict[str, list[Path]] = {}
    for domain in domains:
        scenarios = DOMAIN_SCENARIOS.get(domain, [])
        if not scenarios:
            if verbose:
                print(f"[warn] no scenarios defined for domain '{domain}'")
            continue
        if verbose:
            print(f"\n[{domain}] generating {len(scenarios)} transcript(s)...")
        paths = await generate_domain(domain, scenarios, output_dir, config, verbose)
        results[domain] = paths
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="benchmarks/transcripts",
        help="Output directory (default: benchmarks/transcripts)",
    )
    parser.add_argument(
        "--domains", nargs="+",
        choices=list(DOMAIN_SCENARIOS.keys()) + ["all"],
        default=["all"],
        help="Domains to generate (default: all)",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    domains = list(DOMAIN_SCENARIOS.keys()) if "all" in args.domains else args.domains
    output_dir = Path(args.output)

    config = load_config()
    asyncio.run(run_generation(domains, output_dir, config, verbose=not args.quiet))


if __name__ == "__main__":
    main()
