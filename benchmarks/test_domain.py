"""
Quick test: run Waystone extraction on generated transcripts for a given domain profile.

Usage:
    python -m benchmarks.test_domain --domain episodic_personal
    python -m benchmarks.test_domain --domain medical_clinical --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))

from waystone.extractor import extract_turn, ExtractionBuffer
from waystone.retriever import bfs_collect, score_by_relevance, extract_keywords
from waystone.store import GraphStore
from waystone.config import load_config, get_domain_profile


async def ingest_transcript(
    text: str,
    config: dict,
    profile,
    verbose: bool = False,
) -> tuple[dict, GraphStore]:
    """Ingest a transcript text using buffered extraction. Returns (type_counts, store)."""
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    db_path = tempfile.mktemp(suffix=".db")
    store = GraphStore(db_path)
    buffer = ExtractionBuffer()
    type_counts: dict[str, int] = {}
    all_nodes: list[dict] = []

    async def flush(flush_text: str) -> None:
        kw = extract_keywords(flush_text)
        ctx: list[dict] = []
        if kw:
            entry = store.get_nodes_by_tags(kw)
            if entry:
                entry = score_by_relevance(entry, kw)
                ctx = bfs_collect(store, entry, 2)[:20]
        extraction = await extract_turn(flush_text, ctx, config, profile)
        nodes = extraction.get("nodes", [])
        edges = extraction.get("edges", [])
        store.merge_extraction(nodes, edges)
        all_nodes.extend(nodes)
        for n in nodes:
            t = n.get("type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1
        if verbose:
            print(f"    flushed {len(nodes)} nodes, {len(edges)} edges")

    for line in lines:
        if buffer.add(line):
            await flush(buffer.flush())

    remaining = buffer.flush()
    if remaining.strip():
        await flush(remaining)

    return type_counts, store, all_nodes


async def run_test(domain: str, transcript_dir: Path, verbose: bool) -> None:
    config = load_config()
    config = {**config, "domain": {"name": domain}}
    profile = get_domain_profile(config)

    files = sorted(transcript_dir.glob("*.md"))
    if not files:
        print(f"No transcripts found in {transcript_dir}")
        return

    print(f"Domain: {domain}")
    print(f"Profile node types: {', '.join(profile.node_types.keys())}")
    print(f"Profile edge relations: {', '.join(profile.edge_relations.keys())}")
    print()

    total_types: dict[str, int] = {}

    for f in files:
        print(f"--- {f.name} ---")
        text = f.read_text()
        type_counts, store, nodes = await ingest_transcript(text, config, profile, verbose)

        total = sum(type_counts.values())
        print(f"  Total nodes: {total}")
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"    {t}: {c}")
            total_types[t] = total_types.get(t, 0) + c

        print("  Sample nodes:")
        for n in nodes[:5]:
            print(f"    [{n.get('type')}] {str(n.get('fact', ''))[:80]}")
            if n.get("tags"):
                print(f"      tags: {n['tags'][:6]}")

        store.close()
        Path(store.db_path if hasattr(store, 'db_path') else '').unlink(missing_ok=True)
        print()

    print("=== Aggregate across all transcripts ===")
    grand_total = sum(total_types.values())
    for t, c in sorted(total_types.items(), key=lambda x: -x[1]):
        pct = c / grand_total * 100 if grand_total else 0
        print(f"  {t}: {c} ({pct:.0f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True, help="Domain profile name")
    parser.add_argument(
        "--transcripts", default=None,
        help="Transcript directory (default: benchmarks/transcripts/<domain>)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    transcript_dir = Path(args.transcripts or f"benchmarks/transcripts/{args.domain}")
    if not transcript_dir.exists():
        print(f"Transcript directory not found: {transcript_dir}")
        sys.exit(1)

    asyncio.run(run_test(args.domain, transcript_dir, args.verbose))


if __name__ == "__main__":
    main()
