"""
Generate synthetic LOCOMO-format conversations for benchmark development.

Produces JSON compatible with LocomoDataset so you can run the harness
against generated data without touching the held-out test set (conv-44+).

Usage:
    # 3 conversations, quick dev iteration
    python -m benchmarks.locomo.generate_conversations \
        --n-conversations 3 \
        --output benchmarks/locomo/data/synthetic_v1.json

    # Larger set, more sessions
    python -m benchmarks.locomo.generate_conversations \
        --n-conversations 10 \
        --sessions-per-conv 8 \
        --turns-per-session 12 \
        --output benchmarks/locomo/data/synthetic_v2.json

    # Then run the harness against it
    python -m benchmarks.locomo.harness \
        --dataset benchmarks/locomo/data/synthetic_v1.json \
        --configs engram_default full_context \
        --limit 1
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import os

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import openai

# ---------------------------------------------------------------------------
# Persona pool — varied backgrounds ensure diverse conversational content
# ---------------------------------------------------------------------------

PERSONA_PAIRS = [
    {
        "a": {"name": "Marcus", "age": 34, "job": "high school history teacher", "city": "Columbus, Ohio"},
        "b": {"name": "Priya", "age": 32, "job": "pediatric nurse", "city": "Columbus, Ohio"},
        "relationship": "married couple, together 6 years",
    },
    {
        "a": {"name": "Elena", "age": 28, "job": "UX designer at a fintech startup", "city": "Austin, Texas"},
        "b": {"name": "Theo", "age": 31, "job": "freelance musician and music teacher", "city": "Austin, Texas"},
        "relationship": "close friends who met in college",
    },
    {
        "a": {"name": "David", "age": 45, "job": "civil engineer", "city": "Portland, Oregon"},
        "b": {"name": "Sandra", "age": 43, "job": "elementary school principal", "city": "Portland, Oregon"},
        "relationship": "siblings who talk weekly",
    },
    {
        "a": {"name": "Kenji", "age": 26, "job": "graduate student in biochemistry", "city": "Boston, Massachusetts"},
        "b": {"name": "Amara", "age": 27, "job": "social worker", "city": "Cambridge, Massachusetts"},
        "relationship": "roommates who became best friends",
    },
    {
        "a": {"name": "Rosa", "age": 52, "job": "restaurant owner", "city": "Miami, Florida"},
        "b": {"name": "Luis", "age": 50, "job": "retired firefighter, now real estate agent", "city": "Miami, Florida"},
        "relationship": "married 22 years",
    },
    {
        "a": {"name": "Sophie", "age": 38, "job": "data analyst", "city": "Denver, Colorado"},
        "b": {"name": "Jordan", "age": 36, "job": "physical therapist", "city": "Denver, Colorado"},
        "relationship": "couple who have been dating for 2 years",
    },
    {
        "a": {"name": "Owen", "age": 29, "job": "software engineer at a gaming company", "city": "Seattle, Washington"},
        "b": {"name": "Naomi", "age": 30, "job": "veterinarian", "city": "Bellevue, Washington"},
        "relationship": "best friends since childhood",
    },
    {
        "a": {"name": "Helena", "age": 41, "job": "family law attorney", "city": "Chicago, Illinois"},
        "b": {"name": "Ben", "age": 44, "job": "chef and food blogger", "city": "Chicago, Illinois"},
        "relationship": "married couple, together 12 years",
    },
]


def _random_start_date() -> datetime:
    """Random start date in the past 2 years."""
    days_back = random.randint(180, 730)
    return datetime.now() - timedelta(days=days_back)


def _format_date(dt: datetime) -> str:
    """Format date like the LOCOMO dataset: '1:56 pm on 8 May, 2023'."""
    hour = dt.hour
    ampm = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    minute = dt.minute
    return f"{hour12}:{minute:02d} {ampm} on {dt.day} {dt.strftime('%B')}, {dt.year}"


# ---------------------------------------------------------------------------
# LLM generation helpers
# ---------------------------------------------------------------------------

def _parse_json(text: str):
    """Parse JSON from LLM output, stripping markdown code fences if present."""
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first line (```json or ```) and last line (```)
        inner = lines[1:] if lines[-1].strip() == "```" else lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()
    return json.loads(text)


def _make_client() -> tuple[openai.OpenAI, str]:
    """Build an OpenAI-compatible client from config.yaml, return (client, model)."""
    from engram.config import load_config
    cfg = load_config()
    llm_cfg = cfg.get("llm", {})
    base_url = llm_cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta/openai")
    model = llm_cfg.get("model", "gemini-2.5-flash")
    api_key_env = llm_cfg.get("api_key_env", "GEMINI_API_KEY")
    api_key = os.environ.get(api_key_env, "")
    client = openai.OpenAI(base_url=base_url, api_key=api_key)
    return client, model


def _generate(client: openai.OpenAI, model: str, prompt: str, max_tokens: int = 4096) -> str:
    """Single LLM call, returns text."""
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def _generate_sessions(
    client: openai.OpenAI,
    model: str,
    persona_pair: dict,
    n_sessions: int,
    turns_per_session: int,
    start_date: datetime,
) -> list[dict]:
    """
    Generate all sessions for a conversation.
    Returns list of {"date": datetime, "turns": [{"speaker", "text"}, ...]}.
    """
    pa = persona_pair["a"]
    pb = persona_pair["b"]
    relationship = persona_pair["relationship"]

    # Build a brief arc to ensure continuity across sessions
    arc_prompt = f"""You are writing a realistic ongoing conversation between two people.

Person A: {pa['name']}, {pa['age']}, {pa['job']}, lives in {pa['city']}
Person B: {pb['name']}, {pb['age']}, {pb['job']}, lives in {pb['city']}
Relationship: {relationship}

Generate a brief story arc for {n_sessions} separate conversation sessions spanning several months.
Each session should reference or build on something from previous sessions (shared events, ongoing
situations, mentioned plans, updates on problems). Include concrete details: names of places,
people they mention, dates of events, specific decisions made.

Format as JSON:
{{
  "arc_summary": "2-3 sentences describing the overall arc",
  "sessions": [
    {{"theme": "main topic", "key_facts": ["specific detail 1", "specific detail 2", "..."]}},
    ...
  ]
}}

Return only the JSON, no other text."""

    arc_json = _generate(client, model, arc_prompt, max_tokens=2048)
    try:
        arc = _parse_json(arc_json)
    except json.JSONDecodeError:
        # Fallback: generic arc
        arc = {
            "sessions": [{"theme": f"session {i+1}", "key_facts": []} for i in range(n_sessions)]
        }

    sessions = []
    current_date = start_date
    prior_context = ""

    for i, session_info in enumerate(arc.get("sessions", [])[:n_sessions]):
        # Advance date: 1–6 weeks between sessions
        if i > 0:
            gap_days = random.randint(7, 42)
            current_date = current_date + timedelta(days=gap_days)

        # Random time of day
        current_date = current_date.replace(
            hour=random.randint(8, 22),
            minute=random.randint(0, 59),
        )

        key_facts = "\n".join(f"- {f}" for f in session_info.get("key_facts", []))
        theme = session_info.get("theme", "casual catch-up")

        session_prompt = f"""Write a realistic, casual text conversation between {pa['name']} and {pb['name']}.

Background:
- {pa['name']}: {pa['age']}, {pa['job']}, {pa['city']}
- {pb['name']}: {pb['age']}, {pb['job']}, {pb['city']}
- Relationship: {relationship}

This is session {i+1} of {n_sessions}. Topic/theme: {theme}
Key facts to weave in naturally:
{key_facts if key_facts else "- casual everyday conversation"}

{f"Prior conversation context:{chr(10)}{prior_context}" if prior_context else ""}

Rules:
- Write exactly {turns_per_session} turns, alternating speakers (starting with {pa['name']})
- Keep it natural and conversational — short messages, typos ok, abbreviations fine
- Include specific details: dates, names, places, numbers, decisions
- Some messages can reference past events from prior sessions

Format as JSON array (no other text):
[
  {{"speaker": "{pa['name']}", "text": "..."}},
  {{"speaker": "{pb['name']}", "text": "..."}},
  ...
]"""

        turns_json = _generate(client, model, session_prompt, max_tokens=2048)
        try:
            turns_raw = _parse_json(turns_json)
            if not isinstance(turns_raw, list):
                raise ValueError("not a list")
            turns = [
                {"speaker": t.get("speaker", pa["name"]), "text": t.get("text", "")}
                for t in turns_raw
                if isinstance(t, dict) and t.get("text")
            ]
        except (json.JSONDecodeError, ValueError):
            # Fallback: minimal turns
            turns = [
                {"speaker": pa["name"] if j % 2 == 0 else pb["name"], "text": f"[session {i+1} turn {j+1}]"}
                for j in range(turns_per_session)
            ]

        # Build context summary for next session
        prior_context = f"Session {i+1} ({theme}): " + " | ".join(
            f"{t['speaker']}: {t['text'][:60]}" for t in turns[:4]
        )

        sessions.append({"date": current_date, "turns": turns})

    return sessions


def _build_full_transcript(
    sessions: list[dict],
    speaker_a: str,
    speaker_b: str,
) -> str:
    """Build a readable transcript for QA generation."""
    lines = []
    for i, session in enumerate(sessions):
        lines.append(f"\n[Session {i+1} — {_format_date(session['date'])}]")
        for j, turn in enumerate(session["turns"]):
            dia_id = f"D{i+1}:{j}"
            lines.append(f"({dia_id}) {turn['speaker']}: {turn['text']}")
    return "\n".join(lines)


def _generate_qa_pairs(
    client: openai.OpenAI,
    model: str,
    transcript: str,
    speaker_a: str,
    speaker_b: str,
    n_qa: int,
) -> list[dict]:
    """
    Generate QA pairs covering all 5 LOCOMO categories.
    Returns list of {"question", "answer", "evidence": ["D1:3", ...], "category": int}.
    """
    per_category = max(1, n_qa // 5)
    extra = n_qa - (per_category * 5)

    category_descriptions = {
        1: f"temporal — about WHEN something happened (specific dates, sequences, how long ago). Answer is usually a date or time reference found in the transcript.",
        2: f"explicit_memory — about specific facts explicitly stated (names, places, numbers, decisions). Answer is a direct fact from the transcript.",
        3: f"adversarial — asks about something that did NOT happen or was NOT discussed, OR asks about something ambiguous/uncertain. Answer should be 'not mentioned', 'unknown', or a clarification of what was actually said.",
        4: f"multi_session — requires combining info from multiple sessions to answer (e.g. 'Did they ever follow up on X?', 'What changed between their first and last discussion of Y?'). Evidence spans 2+ sessions.",
        5: f"open_domain — general knowledge question about a topic mentioned in the conversation (not answered by the transcript itself, but prompted by it). Answer is world knowledge.",
    }

    qa_pairs = []

    for cat_id, cat_desc in category_descriptions.items():
        count = per_category + (1 if cat_id <= extra else 0)

        prompt = f"""You are creating a question-answer benchmark from this conversation transcript.

TRANSCRIPT:
{transcript}

Generate {count} question-answer pair(s) of category: {cat_desc}

Rules:
- Questions must be answerable using the transcript (except category 3 adversarial, where the answer is that something wasn't mentioned, and category 5 open_domain, where the answer is general knowledge)
- Answers must be SHORT and specific (1–2 sentences max, often just a name, date, or brief fact)
- Evidence must be exact dia_ids from the transcript in format D<session>:<utterance_index> (e.g. "D2:4")
- For category 3: the question asks about something NOT in the transcript, answer is "not mentioned" or similar
- For category 5: question asks general knowledge about a topic that came up, answer doesn't need evidence

Return as JSON array (no other text):
[
  {{
    "question": "...",
    "answer": "...",
    "evidence": ["D1:3", "D2:7"],
    "category": {cat_id}
  }}
]"""

        result = _generate(client, model, prompt, max_tokens=2048)
        try:
            pairs = _parse_json(result)
            if not isinstance(pairs, list):
                raise ValueError
            for p in pairs:
                if isinstance(p, dict) and p.get("question") and p.get("answer"):
                    qa_pairs.append({
                        "question": p["question"],
                        "answer": str(p["answer"]),
                        "evidence": p.get("evidence", []),
                        "category": cat_id,
                    })
        except (json.JSONDecodeError, ValueError):
            # Fallback: minimal QA
            qa_pairs.append({
                "question": f"[cat {cat_id} question placeholder]",
                "answer": "placeholder",
                "evidence": [],
                "category": cat_id,
            })

    return qa_pairs[:n_qa]


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------

def generate_conversation(
    client: openai.OpenAI,
    model: str,
    conv_id: int,
    persona_pair: dict,
    n_sessions: int,
    turns_per_session: int,
    n_qa: int,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Generate one synthetic conversation in LOCOMO JSON format.
    Returns a dict compatible with LocomoDataset._parse_conversation.
    """
    pa = persona_pair["a"]
    pb = persona_pair["b"]
    sample_id = f"synth-{conv_id:03d}"

    if verbose:
        print(f"  Generating {sample_id}: {pa['name']} & {pb['name']} ({persona_pair['relationship']})")
        print(f"    {n_sessions} sessions, {turns_per_session} turns/session, {n_qa} QA pairs")

    start_date = _random_start_date()
    sessions_data = _generate_sessions(
        client=client,
        model=model,
        persona_pair=persona_pair,
        n_sessions=n_sessions,
        turns_per_session=turns_per_session,
        start_date=start_date,
    )

    if verbose:
        print(f"    Sessions generated ({len(sessions_data)})")

    # Build the LOCOMO-format conversation dict
    conversation: dict[str, Any] = {
        "speaker_a": pa["name"],
        "speaker_b": pb["name"],
    }

    for i, session in enumerate(sessions_data):
        sess_key = f"session_{i+1}"
        conversation[f"{sess_key}_date_time"] = _format_date(session["date"])
        turns = []
        for j, turn in enumerate(session["turns"]):
            turns.append({
                "speaker": turn["speaker"],
                "dia_id": f"D{i+1}:{j}",
                "text": turn["text"],
            })
        conversation[sess_key] = turns

    # Generate QA pairs
    transcript = _build_full_transcript(sessions_data, pa["name"], pb["name"])
    qa_pairs = _generate_qa_pairs(
        client=client,
        model=model,
        transcript=transcript,
        speaker_a=pa["name"],
        speaker_b=pb["name"],
        n_qa=n_qa,
    )

    if verbose:
        from collections import Counter
        cat_counts = Counter(q["category"] for q in qa_pairs)
        print(f"    QA pairs: {len(qa_pairs)} total — {dict(sorted(cat_counts.items()))}")

    return {
        "sample_id": sample_id,
        "conversation": conversation,
        "qa": qa_pairs,
    }


def generate_dataset(
    n_conversations: int,
    sessions_per_conv: int,
    turns_per_session: int,
    qa_per_conv: int,
    output_path: str,
    verbose: bool = True,
) -> list[dict]:
    """Generate a full synthetic LOCOMO-format dataset."""
    client, model = _make_client()

    if verbose:
        print(f"Generating {n_conversations} synthetic conversations")
        print(f"  {sessions_per_conv} sessions/conv, {turns_per_session} turns/session, {qa_per_conv} QA/conv")
        print(f"  Model: {model}")
        print()

    # Shuffle persona pairs so repeated runs vary
    personas = PERSONA_PAIRS.copy()
    random.shuffle(personas)
    # Cycle if we need more conversations than personas
    extended = (personas * ((n_conversations // len(personas)) + 1))[:n_conversations]

    dataset = []
    t0 = time.perf_counter()

    for i in range(n_conversations):
        conv_t0 = time.perf_counter()
        try:
            conv = generate_conversation(
                client=client,
                model=model,
                conv_id=i + 1,
                persona_pair=extended[i],
                n_sessions=sessions_per_conv,
                turns_per_session=turns_per_session,
                n_qa=qa_per_conv,
                verbose=verbose,
            )
            dataset.append(conv)
            elapsed = time.perf_counter() - conv_t0
            if verbose:
                print(f"    Done in {elapsed:.1f}s")
                print()
        except Exception as e:
            print(f"  [error] conv {i+1} failed: {e}")
            if verbose:
                import traceback
                traceback.print_exc()

    total_elapsed = time.perf_counter() - t0

    # Write output
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(dataset, f, indent=2)

    if verbose:
        total_qa = sum(len(c["qa"]) for c in dataset)
        total_sessions = sum(
            sum(1 for k in c["conversation"] if k.startswith("session_") and not k.endswith("_date_time"))
            for c in dataset
        )
        total_turns = sum(
            sum(len(c["conversation"][k]) for k in c["conversation"] if k.startswith("session_") and not k.endswith("_date_time"))
            for c in dataset
        )
        print(f"Dataset written to {output_path}")
        print(f"  {len(dataset)} conversations, {total_sessions} sessions, {total_turns} turns, {total_qa} QA pairs")
        print(f"  Total time: {total_elapsed:.1f}s")

    return dataset


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic LOCOMO-format conversations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--n-conversations", type=int, default=3,
        help="Number of conversations to generate (default: 3)",
    )
    parser.add_argument(
        "--sessions-per-conv", type=int, default=6,
        help="Sessions per conversation (default: 6). LOCOMO has ~27 but 6 is fast for dev.",
    )
    parser.add_argument(
        "--turns-per-session", type=int, default=10,
        help="Turns per session (default: 10)",
    )
    parser.add_argument(
        "--qa-per-conv", type=int, default=25,
        help="QA pairs per conversation (default: 25, spread across 5 categories)",
    )
    parser.add_argument(
        "--output", default="benchmarks/locomo/data/synthetic.json",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress output",
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    generate_dataset(
        n_conversations=args.n_conversations,
        sessions_per_conv=args.sessions_per_conv,
        turns_per_session=args.turns_per_session,
        qa_per_conv=args.qa_per_conv,
        output_path=args.output,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
