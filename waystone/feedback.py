"""Interactive feedback labeling for Waystone fact extraction outputs.

Provides:
  - Interactive review loop: shows unrated nodes one by one, accepts u/d/s/q keypresses
  - Batch export to JSONL for LoRA fine-tuning (conversation → fact pairs with quality signal)
  - Auto-labeling via LLM-as-judge to rate unrated nodes

JSONL export format (one JSON object per line):

  {
    "messages": [
      {"role": "user", "content": "<source transcript snippet or context>"},
      {"role": "assistant", "content": "<extracted fact>"}
    ],
    "label": 1 | -1,
    "metadata": {
      "node_id": "...", "type": "...", "confidence": 0.9,
      "tags": [...], "comment": "...", "rated_at": "..."
    }
  }
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import GraphStore


# ---------------------------------------------------------------------------
# Interactive review loop
# ---------------------------------------------------------------------------

def _read_char() -> str:
    """Read a single keypress without requiring Enter. Falls back to line input."""
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        # Non-TTY fallback (piped input, Windows)
        line = sys.stdin.readline().strip()
        return line[0].lower() if line else "q"


def review_loop(store: "GraphStore", limit: int = 50) -> dict:
    """Interactively label unrated nodes in the store.

    Keypresses:
      u / 1  → thumbs up  (+1)
      d / 0  → thumbs down (-1)
      s      → skip (no rating stored)
      q      → quit

    Returns a summary dict with counts.
    """
    nodes = store.get_unrated_nodes(limit=limit)
    if not nodes:
        print("No unrated nodes found.")
        return {"rated": 0, "skipped": 0, "quit": False}

    total = len(nodes)
    rated = 0
    skipped = 0

    print(f"\n{'='*60}")
    print(f"  Waystone Feedback — {total} unrated node(s)")
    print("  [u]p  [d]own  [s]kip  [q]uit")
    print(f"{'='*60}\n")

    for i, node in enumerate(nodes, 1):
        tags = ", ".join(node.get("tags") or [])
        conf = node.get("confidence", 0)
        ntype = node.get("type", "?")
        fact = node.get("fact", "")
        source = node.get("source_transcript", "")

        print(f"[{i}/{total}]  {ntype}  (conf={conf:.2f})  src={source}")
        if tags:
            print(f"  tags: {tags}")
        print(f"  {fact}")
        print()
        print("  > ", end="", flush=True)

        ch = _read_char().lower()
        print(ch)  # echo the keypress
        print()

        if ch in ("u", "1"):
            store.add_feedback(node["id"], rating=1)
            rated += 1
        elif ch in ("d", "0"):
            store.add_feedback(node["id"], rating=-1)
            rated += 1
        elif ch == "s":
            skipped += 1
        elif ch == "q":
            print("Quit.")
            return {"rated": rated, "skipped": skipped, "quit": True}
        else:
            print(f"  (unknown key {ch!r} — skipping)")
            skipped += 1

    print(f"\nDone. Rated {rated}, skipped {skipped}.")
    return {"rated": rated, "skipped": skipped, "quit": False}


# ---------------------------------------------------------------------------
# Single-node rating (non-interactive)
# ---------------------------------------------------------------------------

def rate_node(store: "GraphStore", node_id: str, rating: int, comment: str = "") -> bool:
    """Rate a single node by ID. Returns True if the node exists and was rated."""
    node = store.get_node(node_id)
    if node is None:
        return False
    return store.add_feedback(node_id, rating=rating, comment=comment)


# ---------------------------------------------------------------------------
# JSONL export for LoRA fine-tuning
# ---------------------------------------------------------------------------

def export_jsonl(store: "GraphStore", output_path: Path, rating: int | None = None) -> int:
    """Export rated nodes to JSONL format suitable for supervised fine-tuning.

    Args:
        store: Open GraphStore
        output_path: Where to write the .jsonl file
        rating: If 1 → export only thumbs-up; -1 → only thumbs-down; None → all rated

    Returns:
        Number of records written.
    """
    rated_nodes = store.get_rated_nodes(rating=rating)
    if not rated_nodes:
        return 0

    count = 0
    with output_path.open("w", encoding="utf-8") as fh:
        for node in rated_nodes:
            fb = store.get_feedback(node["id"])
            if fb is None:
                continue

            # Build a short context string from source + tags
            source = node.get("source_transcript", "")
            tags = node.get("tags") or []
            context_parts = []
            if source:
                context_parts.append(f"Source: {source}")
            if tags:
                context_parts.append(f"Context tags: {', '.join(tags)}")
            user_content = "\n".join(context_parts) if context_parts else "Extract facts from the conversation."

            record = {
                "messages": [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": node["fact"]},
                ],
                "label": fb["rating"],
                "metadata": {
                    "node_id": node["id"],
                    "type": node.get("type", ""),
                    "confidence": node.get("confidence", 0),
                    "tags": tags,
                    "comment": fb.get("comment", ""),
                    "rated_at": fb.get("created_at", ""),
                },
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1

    return count


# ---------------------------------------------------------------------------
# Auto-labeling via LLM-as-judge
# ---------------------------------------------------------------------------

def _call_llm_judge(prompt: str, llm_config: dict) -> str:
    """Make an LLM API call and return the raw response content."""
    try:
        import httpx
    except ImportError:
        raise ImportError("httpx is required for LLM calls; install with: pip install httpx")

    headers = {}
    api_key_env = llm_config.get("api_key_env")
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ValueError(f"API key env var '{api_key_env}' is not set")
        headers["Authorization"] = f"Bearer {api_key}"
    elif llm_config.get("api_key"):
        headers["Authorization"] = f"Bearer {llm_config['api_key']}"
    else:
        api_key = os.environ.get("CTX_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    timeout = llm_config.get("timeout", 30.0)

    request_body = {
        "model": llm_config.get("model", "gpt-4"),
        "messages": [
            {
                "role": "system",
                "content": "You are a fact quality evaluator. Respond with ONLY a single integer: 1 or -1",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 5,
    }

    url = f"{llm_config.get('base_url', 'http://localhost:1234/v1')}/chat/completions"

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=request_body, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        raise RuntimeError(f"LLM call failed: {e}")


def _extract_rating(response_text: str) -> int | None:
    """Extract the first integer token that is 1 or -1 from the response."""
    # Look for standalone 1 or -1 surrounded by word boundaries or whitespace
    match = re.search(r'(?:^|\s|[^\w-])(-?1)(?:\s|[^\w-]|$)', response_text)
    if match:
        token = match.group(1)
        if token in ("1", "-1"):
            return int(token)
    return None


def _read_source_context(source_transcript: str, transcripts_dir: Path) -> str | None:
    """Try to read source file context. Returns None if not a file or file not found."""
    if source_transcript in ("assistant", "live"):
        return None

    try:
        source_path = transcripts_dir / source_transcript
        if not source_path.exists():
            return None

        text = source_path.read_text(encoding="utf-8", errors="replace")
        # Truncate to 3000 chars if too large
        if len(text) > 3000:
            text = text[:3000]
        return text
    except Exception:
        return None


def auto_label(
    store: "GraphStore",
    project_name: str,
    transcripts_dir: Path,
    llm_config: dict,
    limit: int = 200,
    dry_run: bool = False,
) -> dict:
    """Use LLM-as-judge to automatically rate unrated nodes in the database.

    Args:
        store: Open GraphStore
        project_name: Project name (used for logging)
        transcripts_dir: Path to directory containing transcript files
        llm_config: LLM configuration dict (base_url, model, api_key, etc.)
        limit: Max nodes to rate per run (default 200)
        dry_run: If True, don't write ratings to DB; just print what would happen

    Returns:
        Dict with keys: rated, skipped, thumbs_up, thumbs_down
    """
    unrated = store.get_unrated_nodes(limit=limit)
    if not unrated:
        return {"rated": 0, "skipped": 0, "thumbs_up": 0, "thumbs_down": 0}

    len(unrated)
    rated = 0
    skipped = 0
    thumbs_up = 0
    thumbs_down = 0

    for i, node in enumerate(unrated, 1):
        node_id = node["id"]
        fact = node.get("fact", "")
        node_type = node.get("type", "?")
        source = node.get("source_transcript", "")
        tags = node.get("tags") or []

        try:
            # Try to read source context
            source_text = None
            if source and source not in ("assistant", "live"):
                source_text = _read_source_context(source, transcripts_dir)

            # Build judge prompt
            if source_text:
                prompt = f"""You are evaluating whether a fact was accurately extracted from a document.

Source document excerpt:
---
{source_text}
---

Extracted fact: "{fact}"
Node type: {node_type}

Is this extraction accurate and useful?
- Respond 1 if: the fact is accurate, specific, non-trivial, and worth remembering
- Respond -1 if: the fact is vague, inaccurate, hallucinated, redundant, or too generic

Respond with ONLY a single integer: 1 or -1"""
            else:
                tags_str = ", ".join(tags) if tags else "(none)"
                prompt = f"""You are evaluating whether an extracted fact is worth remembering.

Extracted fact: "{fact}"
Node type: {node_type}
Tags: {tags_str}

Is this a good fact to keep in a knowledge graph?
- Respond 1 if: specific, actionable, non-trivial, would be useful for a future coding session
- Respond -1 if: vague ("the system works"), trivial ("the user greeted the assistant"), or generic boilerplate

Respond with ONLY a single integer: 1 or -1"""

            # Call LLM
            response = _call_llm_judge(prompt, llm_config)
            rating = _extract_rating(response)

            if rating is None:
                # Unparseable response
                skipped += 1
            else:
                # Write rating (unless dry-run)
                if not dry_run:
                    store.add_feedback(node_id, rating=rating, comment="auto-labeled")

                rated += 1
                if rating == 1:
                    thumbs_up += 1
                else:
                    thumbs_down += 1

        except Exception:
            # Per-node error: skip this node, continue to next
            skipped += 1

        # Progress indicator every 10 nodes
        if i % 10 == 0:
            print(".", end="", flush=True)

        # Rate limit: small delay between LLM calls
        time.sleep(0.1)

    print()  # newline after progress dots
    return {"rated": rated, "skipped": skipped, "thumbs_up": thumbs_up, "thumbs_down": thumbs_down}
