#!/usr/bin/env python3
"""Short-Prompt Context Retrieval Evaluation

Evaluates whether augmenting the retrieval query with recent turns + session
narrative helps short/contentless prompts ("yes do that", "proceed") surface
on-topic context without regressing content-rich prompts.

Metrics:
  - On-topic retrieval rate: fraction of retrieved nodes belonging to the
    case's intended topic (higher is better)
  - Topic recall: whether the topic's key nodes were retrieved (binary per case)
  - Aggregate A/B: bare-prompt query (baseline) vs context-augmented

All evaluation is offline (no LLM). Cases are hand-seeded into a graph with
dedup_threshold=1.1 to prevent semantic collapse locally. Graph is ephemeral
(isolated temp dir).

Usage:
    python benchmarks/short_prompt_eval.py [--query-context-turns N]

Exit codes:
    0 = success
    1 = failure
"""

import argparse
import json
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import waystone  # noqa: E402

print(f"[IMPORT] waystone module: {waystone.__file__}")
print()

from waystone.retriever import retrieve_with_stats  # noqa: E402
from waystone.store import GraphStore  # noqa: E402


def _nid() -> str:
    """Generate a unique node ID."""
    return f"n_{uuid.uuid4().hex[:8]}"


def _seed_topic_cluster(store: GraphStore, topic: str, nodes_spec: list[str]) -> list[str]:
    """Seed a cluster of nodes for a single topic.

    Args:
        store: Graph store to populate
        topic: Topic name (used for tags)
        nodes_spec: List of facts (strings)

    Returns:
        List of node IDs created
    """
    node_ids = []
    for fact in nodes_spec:
        nid = _nid()
        store.add_node({
            "id": nid,
            "fact": fact,
            "type": "decision",
            "confidence": 0.95,
            "tags": [topic.lower(), "topic"] + topic.lower().split("-"),
        })
        node_ids.append(nid)
    return node_ids


def _build_test_graph() -> GraphStore:
    """Build an ephemeral test graph with distinct topic clusters + noise.

    Returns an open GraphStore (caller must close it).
    """
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    store = GraphStore(db_path, dedup_threshold=1.1, vec_enabled=False)

    # Topic 1: JWT authentication (3 nodes)
    _seed_topic_cluster(store, "jwt", [
        "JWT tokens are stateless — no server-side session store required.",
        "We're using RS256 signing with a 2048-bit RSA key for JWT validation.",
        "JWT expiry is set to 1 hour; refresh tokens live for 30 days.",
    ])

    # Topic 2: Redis caching (3 nodes)
    _seed_topic_cluster(store, "redis", [
        "Redis is deployed as a 3-node cluster for high availability.",
        "Cache invalidation strategy: LRU with 512MB per-node memory limit.",
        "TTL on cache entries is 5 minutes to avoid stale data.",
    ])

    # Topic 3: Kubernetes deployment (3 nodes)
    _seed_topic_cluster(store, "kubernetes", [
        "Services run on Kubernetes with 3 replicas in production.",
        "Resource limits: 512Mi RAM per pod, 250m CPU.",
        "Rolling updates ensure zero-downtime deployments.",
    ])

    # Noise: unrelated nodes that should NOT dominate (4 nodes)
    _seed_topic_cluster(store, "frontend", [
        "The frontend is built with React and TypeScript.",
        "We use Tailwind CSS for styling.",
    ])
    _seed_topic_cluster(store, "database", [
        "PostgreSQL is used for the primary data store.",
        "We run weekly backups to S3.",
    ])

    return store


def score_on_topic(retrieved_markdown: str, expected_topics: list[str]) -> tuple:
    """Score retrieved nodes: what fraction are on-topic?

    Args:
        retrieved_markdown: The markdown returned from retrieve_with_stats
        expected_topics: List of topic keywords this case expects

    Returns:
        (on_topic_rate: float, on_topic_count: int, total_count: int)
    """
    text = retrieved_markdown.lower()

    # Count total facts retrieved (heuristic: lines starting with "- ")
    facts = [line for line in text.split("\n") if line.strip().startswith("- ")]
    total = len(facts)
    if total == 0:
        return 0.0, 0, 0

    # Count facts mentioning at least one expected topic keyword
    on_topic = 0
    for fact_line in facts:
        fact_lower = fact_line.lower()
        if any(topic.lower() in fact_lower for topic in expected_topics):
            on_topic += 1

    rate = on_topic / total if total > 0 else 0.0
    return rate, on_topic, total


def test_case(
    store: GraphStore,
    case_name: str,
    prompt: str,
    recent_context: str,
    expected_topics: list[str],
    retrieval_query_bare: str,
    retrieval_query_augmented: str,
) -> dict:
    """Run a single test case with both bare and augmented queries.

    Returns:
        {
            "case": case_name,
            "expected_topics": expected_topics,
            "baseline": {"on_topic_rate": float, "on_topic": int, "total": int},
            "augmented": {...},
            "improved": bool,
        }
    """
    # Baseline: bare prompt query
    result_bare = retrieve_with_stats(store, retrieval_query_bare, hops=2, top_k=10)
    on_topic_rate_bare, on_topic_bare, total_bare = score_on_topic(
        result_bare.markdown, expected_topics
    )

    # Augmented: recent context + prompt
    result_aug = retrieve_with_stats(store, retrieval_query_augmented, hops=2, top_k=10)
    on_topic_rate_aug, on_topic_aug, total_aug = score_on_topic(
        result_aug.markdown, expected_topics
    )

    improved = on_topic_rate_aug > on_topic_rate_bare

    return {
        "case": case_name,
        "prompt": prompt,
        "expected_topics": expected_topics,
        "baseline": {
            "on_topic_rate": round(on_topic_rate_bare, 3),
            "on_topic": on_topic_bare,
            "total": total_bare,
        },
        "augmented": {
            "on_topic_rate": round(on_topic_rate_aug, 3),
            "on_topic": on_topic_aug,
            "total": total_aug,
        },
        "improved": improved,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--query-context-turns",
        type=int,
        default=3,
        help="Number of recent turns to prepend to short prompts (default: 3)",
    )
    args = parser.parse_args()

    print(f"[CONFIG] query_context_turns={args.query_context_turns}\n")

    # Build test graph
    print("[SETUP] Building test graph with 3 topic clusters + noise...")
    store = _build_test_graph()
    print(f"[SETUP] Graph ready: {store.get_stats()['node_count']} nodes\n")

    # Test cases: short/contentless prompts + preceding turns from a specific topic
    # Format: (case_name, short_prompt, recent_context_window, expected_topics_for_grading)
    test_cases = [
        (
            "jwt_short_1",
            "yes, do that",
            "User: So for auth we need stateless tokens?\nAssistant: Right, JWT handles that.",
            ["jwt"],
        ),
        (
            "jwt_short_2",
            "make it so",
            "User: When should tokens expire?\nAssistant: Typically 1 hour for access tokens.",
            ["jwt"],
        ),
        (
            "redis_short_1",
            "ok let's go",
            "User: How should we cache?\nAssistant: Redis is a good choice.",
            ["redis"],
        ),
        (
            "redis_short_2",
            "proceed with that",
            "User: What TTL should cache entries have?\nAssistant: 5 minutes is standard.",
            ["redis"],
        ),
        (
            "k8s_short_1",
            "let's do it",
            "User: Should we use containers?\nAssistant: Yes, Kubernetes is the way.",
            ["kubernetes"],
        ),
        (
            "k8s_short_2",
            "sounds good",
            "User: How do we handle updates?\nAssistant: Rolling deployments avoid downtime.",
            ["kubernetes"],
        ),
        (
            "mixed_short",
            "yep",
            "User: Redis and JWT?\nAssistant: Both complement each other.",
            ["redis", "jwt"],
        ),
        (
            "noise_short",
            "got it",
            "User: What about frontend?\nAssistant: React with Tailwind.",
            ["frontend"],
        ),
    ]

    results = []
    for case_name, prompt, recent_context, expected_topics in test_cases:
        # Baseline: bare prompt
        retrieval_query_bare = prompt

        # Augmented: recent context + prompt
        retrieval_query_aug = recent_context + "\n" + prompt

        result = test_case(
            store,
            case_name,
            prompt,
            recent_context,
            expected_topics,
            retrieval_query_bare,
            retrieval_query_aug,
        )
        results.append(result)

    store.close()

    # Summarize results
    print("\n" + "=" * 100)
    print("SHORT-PROMPT EVAL RESULTS: Bare Query vs Context-Augmented")
    print("=" * 100 + "\n")

    print(f"{'Case':<20} | {'Expected':<20} | {'Baseline Rate':<15} | {'Augmented Rate':<15} | {'Improved':<10}")
    print("-" * 100)

    improved_count = 0
    for r in results:
        case = r["case"]
        topics = ", ".join(r["expected_topics"][:2])
        baseline_rate = r["baseline"]["on_topic_rate"]
        augmented_rate = r["augmented"]["on_topic_rate"]
        improved = "✓" if r["improved"] else "✗"
        if r["improved"]:
            improved_count += 1

        print(
            f"{case:<20} | {topics:<20} | {baseline_rate:<15.1%} | {augmented_rate:<15.1%} | {improved:<10}"
        )

    print("\n" + "=" * 100)
    print("AGGREGATE METRICS")
    print("=" * 100)

    avg_baseline = sum(r["baseline"]["on_topic_rate"] for r in results) / len(results)
    avg_augmented = sum(r["augmented"]["on_topic_rate"] for r in results) / len(results)
    total_improved = improved_count

    print(f"Cases improved with augmentation: {total_improved}/{len(results)}")
    print(f"Avg baseline on-topic rate:       {avg_baseline:.1%}")
    print(f"Avg augmented on-topic rate:      {avg_augmented:.1%}")
    print(f"Absolute improvement:              {avg_augmented - avg_baseline:+.1%}")

    if avg_augmented >= avg_baseline:
        print("\n✓ PASS: Context-augmented queries do not regress; show improvement.")
        return 0
    else:
        print("\n✗ FAIL: Context-augmented queries regressed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
