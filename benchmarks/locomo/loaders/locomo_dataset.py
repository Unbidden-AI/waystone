"""
LOCOMO dataset loader.

Dataset: snap-research/locomo (locomo10.json)
Structure: 10 conversations × ~27 sessions × ~22 turns/session, ~199 QA pairs/conv

Usage:
    from benchmarks.locomo.loaders.locomo_dataset import LocomoDataset

    ds = LocomoDataset("/path/to/locomo10.json")
    for conv in ds.iter_conversations():
        for session in conv.sessions:
            for turn in session.turns:
                ...
        for qa in conv.qa_pairs:
            ...
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# QA category labels from the LOCOMO paper (verified against locomo10.json)
# Cat 5 = adversarial: questions have `adversarial_answer` not `answer`.
# The official evaluation protocol EXCLUDES cat 5 from accuracy scoring —
# use --categories 1 2 3 4 when running the harness.
QA_CATEGORY_LABELS = {
    1: "single_hop",       # direct single-session recall
    2: "temporal",         # date / time ordering questions
    3: "open_domain",      # inference / reasoning over stored facts
    4: "multi_hop",        # cross-session synthesis
    5: "adversarial",      # adversarial — uses adversarial_answer field, not answer
}


@dataclass
class Turn:
    speaker: str
    dia_id: str      # e.g. "D1:3" — session D1, utterance 3
    text: str


@dataclass
class Session:
    session_id: str  # e.g. "session_1"
    datetime_str: str | None  # e.g. "1:56 pm on 8 May, 2023"
    turns: list[Turn]

    def as_transcript(self, speaker_a: str, speaker_b: str) -> str:
        """Return a plain-text transcript suitable for LLM extraction."""
        lines = []
        if self.datetime_str:
            lines.append(f"[{self.datetime_str}]")
        for t in self.turns:
            lines.append(f"{t.speaker}: {t.text}")
        return "\n".join(lines)


@dataclass
class QAPair:
    question: str
    answer: str
    evidence: list[str]   # dia_id references, e.g. ["D1:3", "D2:7"]
    category: int         # 1–5
    category_label: str   # human-readable

    @property
    def relevant_session_ids(self) -> list[str]:
        """Extract session numbers referenced in evidence dia_ids."""
        sessions = set()
        for ref in self.evidence:
            # dia_id format: "D<session>:<utterance>"
            if ":" in ref and ref.startswith("D"):
                try:
                    sess_num = ref.split(":")[0][1:]
                    sessions.add(f"session_{sess_num}")
                except (IndexError, ValueError):
                    pass
        return sorted(sessions)


@dataclass
class Conversation:
    sample_id: str          # e.g. "conv-26"
    speaker_a: str
    speaker_b: str
    sessions: list[Session]
    qa_pairs: list[QAPair]

    @property
    def total_turns(self) -> int:
        return sum(len(s.turns) for s in self.sessions)

    def iter_qa_by_category(self, category: int) -> Iterator[QAPair]:
        for qa in self.qa_pairs:
            if qa.category == category:
                yield qa


class LocomoDataset:
    """Loader for the LOCOMO benchmark dataset (locomo10.json)."""

    def __init__(self, json_path: str | Path):
        self.json_path = Path(json_path)
        if not self.json_path.exists():
            raise FileNotFoundError(
                f"LOCOMO dataset not found: {self.json_path}\n"
                "Download from: https://github.com/snap-research/locomo"
            )
        with open(self.json_path) as f:
            self._raw = json.load(f)

    def __len__(self) -> int:
        return len(self._raw)

    def iter_conversations(
        self,
        limit: int | None = None,
        sample_ids: list[str] | None = None,
    ) -> Iterator[Conversation]:
        """
        Iterate over conversations.

        Args:
            limit: Stop after this many conversations (for rapid dev iteration).
            sample_ids: Only yield conversations with these sample_ids.
        """
        count = 0
        for raw_conv in self._raw:
            if sample_ids and raw_conv.get("sample_id") not in sample_ids:
                continue
            if limit is not None and count >= limit:
                break
            yield self._parse_conversation(raw_conv)
            count += 1

    def iter_qa_pairs(
        self,
        limit: int | None = None,
        categories: list[int] | None = None,
    ) -> Iterator[tuple[Conversation, QAPair]]:
        """
        Flat iterator over all (conversation, qa_pair) tuples.

        Args:
            limit: Stop after this many QA pairs total.
            categories: Only yield QA pairs with these category IDs.
        """
        count = 0
        for conv in self.iter_conversations():
            for qa in conv.qa_pairs:
                if categories and qa.category not in categories:
                    continue
                if limit is not None and count >= limit:
                    return
                yield conv, qa
                count += 1

    def stats(self) -> dict:
        """Return dataset statistics."""
        convs = list(self.iter_conversations())
        total_qa = sum(len(c.qa_pairs) for c in convs)
        total_sessions = sum(len(c.sessions) for c in convs)
        total_turns = sum(c.total_turns for c in convs)
        from collections import Counter
        cat_counts = Counter(
            qa.category for c in convs for qa in c.qa_pairs
        )
        return {
            "conversations": len(convs),
            "total_qa_pairs": total_qa,
            "total_sessions": total_sessions,
            "total_turns": total_turns,
            "qa_by_category": {
                QA_CATEGORY_LABELS.get(k, str(k)): v
                for k, v in sorted(cat_counts.items())
            },
        }

    # ------------------------------------------------------------------
    # Private parsing helpers
    # ------------------------------------------------------------------

    def _parse_conversation(self, raw: dict) -> Conversation:
        conv_data = raw["conversation"]
        speaker_a = conv_data.get("speaker_a", "Speaker A")
        speaker_b = conv_data.get("speaker_b", "Speaker B")

        sessions = self._parse_sessions(conv_data)
        qa_pairs = self._parse_qa(raw.get("qa", []))

        return Conversation(
            sample_id=raw.get("sample_id", ""),
            speaker_a=speaker_a,
            speaker_b=speaker_b,
            sessions=sessions,
            qa_pairs=qa_pairs,
        )

    def _parse_sessions(self, conv_data: dict) -> list[Session]:
        sessions = []
        # Collect all session_N keys (not _date_time variants)
        session_keys = sorted(
            [k for k in conv_data if k.startswith("session_") and not k.endswith("_date_time")],
            key=lambda k: int(k.split("_")[1]),
        )
        for sk in session_keys:
            raw_turns = conv_data[sk]
            if not isinstance(raw_turns, list):
                continue
            datetime_str = conv_data.get(f"{sk}_date_time")
            turns = [
                Turn(
                    speaker=t.get("speaker", ""),
                    dia_id=t.get("dia_id", ""),
                    text=t.get("text", ""),
                )
                for t in raw_turns
                if isinstance(t, dict)
            ]
            sessions.append(Session(
                session_id=sk,
                datetime_str=datetime_str,
                turns=turns,
            ))
        return sessions

    def _parse_qa(self, raw_qa: list) -> list[QAPair]:
        pairs = []
        for q in raw_qa:
            if not isinstance(q, dict):
                continue
            cat = q.get("category", 0)
            # Cat 5 (adversarial) uses `adversarial_answer`, not `answer`
            answer = q.get("answer") or q.get("adversarial_answer", "")
            pairs.append(QAPair(
                question=q.get("question", ""),
                answer=answer,
                evidence=q.get("evidence", []),
                category=cat,
                category_label=QA_CATEGORY_LABELS.get(cat, f"cat_{cat}"),
            ))
        return pairs
