"""
LongMemEval dataset loader.

Dataset: xiaowu0162/longmemeval-cleaned (HuggingFace)

Three splits:
    longmemeval_oracle     — only oracle (answer-bearing) sessions per question (~3 sessions)
    longmemeval_s_cleaned  — full haystack, ~53 sessions per question (single assistant history)
    longmemeval_m_cleaned  — full haystack, multi-user variant (large file, may need streaming)

Usage:
    from benchmarks.longmemeval.loaders.longmemeval_dataset import LongMemEvalDataset

    ds = LongMemEvalDataset("/path/to/longmemeval_s_cleaned.json")
    for sample in ds.iter_samples():
        # sample is a locomo-compatible Conversation with 1 QA pair
        for session in sample.sessions:
            ...
        qa = sample.qa_pairs[0]
        print(qa.question, qa.answer)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# Reuse LOCOMO dataclasses — LME samples are converted to the same types
# so we can feed them directly into locomo's ingestion pipeline.
from benchmarks.locomo.loaders.locomo_dataset import (
    Turn,
    Session,
    QAPair,
    Conversation,
)

# LongMemEval question type → integer category (for filtering / reporting)
# Mirrors LOCOMO's category system:
#   1 = single-session recall
#   2 = temporal reasoning
#   3 = multi-session / open-domain
#   4 = knowledge update (Waystone's key differentiator)
LME_QUESTION_TYPES = {
    "single-session-user":        1,
    "single-session-assistant":   1,
    "single-session-preference":  1,
    "temporal-reasoning":         2,
    "multi-session":              3,
    "knowledge-update":           4,
    # absent-info lives in the M split; map to 5 for future use
    "absent-information":         5,
}


class LongMemEvalDataset:
    """
    Loader for LongMemEval (cleaned) JSON files.

    Each JSON file is a list of 500 samples.  Each sample is converted to a
    Conversation with exactly one QAPair, so the LOCOMO ingestion pipeline
    and harness can consume it unchanged.
    """

    def __init__(self, json_path: str | Path):
        self.json_path = Path(json_path)
        if not self.json_path.exists():
            raise FileNotFoundError(
                f"LongMemEval data not found: {self.json_path}\n"
                "Download with:\n"
                "  python -m benchmarks.longmemeval.loaders.longmemeval_dataset --download"
            )
        with open(self.json_path) as f:
            self._raw: list[dict] = json.load(f)

    def __len__(self) -> int:
        return len(self._raw)

    def iter_samples(
        self,
        limit: int | None = None,
        question_types: list[str] | None = None,
        sample_ids: list[str] | None = None,
    ) -> Iterator[Conversation]:
        """
        Yield each LME sample as a LOCOMO-compatible Conversation.

        Args:
            limit: Stop after this many samples.
            question_types: Filter to these question_type values
                            (e.g. ['knowledge-update', 'temporal-reasoning']).
            sample_ids: Filter to these question_id values.
        """
        count = 0
        for raw in self._raw:
            qid = raw.get("question_id", "")
            qtype = raw.get("question_type", "")

            if sample_ids and qid not in sample_ids:
                continue
            if question_types and qtype not in question_types:
                continue
            if limit is not None and count >= limit:
                break

            yield self._parse_sample(raw)
            count += 1

    def stats(self) -> dict:
        """Return dataset statistics."""
        from collections import Counter
        type_counts: Counter = Counter()
        total_sessions = 0
        total_turns = 0
        for raw in self._raw:
            type_counts[raw.get("question_type", "unknown")] += 1
            sessions = raw.get("haystack_sessions", [])
            total_sessions += len(sessions)
            for sess in sessions:
                total_turns += len(sess) if isinstance(sess, list) else 0

        return {
            "samples": len(self._raw),
            "total_sessions": total_sessions,
            "avg_sessions_per_sample": round(total_sessions / max(len(self._raw), 1), 1),
            "total_turns": total_turns,
            "by_question_type": dict(sorted(type_counts.items())),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_sample(self, raw: dict) -> Conversation:
        question_id = raw.get("question_id", "lme_unknown")
        question_type = raw.get("question_type", "unknown")
        question = raw.get("question", "")
        answer = raw.get("answer", "")
        question_date = raw.get("question_date")
        haystack_dates = raw.get("haystack_dates", [])
        haystack_session_ids = raw.get("haystack_session_ids", [])
        raw_sessions = raw.get("haystack_sessions", [])
        answer_session_ids = raw.get("answer_session_ids", [])

        sessions: list[Session] = []
        for i, raw_sess in enumerate(raw_sessions):
            sess_id = (
                haystack_session_ids[i]
                if i < len(haystack_session_ids)
                else f"sess_{i}"
            )
            date_str = (
                haystack_dates[i] if i < len(haystack_dates) else None
            )
            turns = self._parse_turns(raw_sess, sess_idx=i)
            sessions.append(Session(
                session_id=sess_id,
                datetime_str=date_str,
                turns=turns,
            ))

        # LME question type → integer category
        cat_int = LME_QUESTION_TYPES.get(question_type, 0)

        qa = QAPair(
            question=question,
            answer=answer,
            evidence=answer_session_ids,
            category=cat_int,
            category_label=question_type,
        )

        return Conversation(
            sample_id=question_id,
            speaker_a="User",
            speaker_b="Assistant",
            sessions=sessions,
            qa_pairs=[qa],
        )

    def _parse_turns(self, raw_sess: list, sess_idx: int) -> list[Turn]:
        turns = []
        for j, raw_turn in enumerate(raw_sess):
            if not isinstance(raw_turn, dict):
                continue
            role = raw_turn.get("role", "user")
            content = raw_turn.get("content", "")
            speaker = "User" if role == "user" else "Assistant"
            # Synthetic dia_id that mirrors LOCOMO's "D<session>:<turn>" format
            dia_id = f"D{sess_idx + 1}:{j + 1}"
            turns.append(Turn(speaker=speaker, dia_id=dia_id, text=content))
        return turns


# ---------------------------------------------------------------------------
# Download helper (CLI)
# ---------------------------------------------------------------------------

def _download_splits(data_dir: Path, splits: list[str]) -> None:
    """Download specified LME splits from HuggingFace Hub."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise ImportError(
            "huggingface_hub is required for download. "
            "Install with: pip install huggingface-hub"
        )
    data_dir.mkdir(parents=True, exist_ok=True)
    for split in splits:
        filename = f"{split}.json"
        dest = data_dir / filename
        if dest.exists():
            print(f"  Already downloaded: {dest}")
            continue
        print(f"  Downloading {filename} ...")
        path = hf_hub_download(
            repo_id="xiaowu0162/longmemeval-cleaned",
            filename=filename,
            repo_type="dataset",
            local_dir=str(data_dir),
        )
        print(f"  → {path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Download LongMemEval data")
    parser.add_argument(
        "--data-dir",
        default="benchmarks/longmemeval/data",
        help="Directory to save JSON files",
    )
    parser.add_argument(
        "--splits",
        nargs="+",
        default=["longmemeval_oracle", "longmemeval_s_cleaned"],
        choices=[
            "longmemeval_oracle",
            "longmemeval_s_cleaned",
            "longmemeval_m_cleaned",
        ],
        help="Which splits to download (oracle and s_cleaned recommended; m_cleaned is very large)",
    )
    args = parser.parse_args()
    _download_splits(Path(args.data_dir), args.splits)
    print("Done.")
