"""
Ablation configurations for the LOCOMO benchmark.

Each config maps to a named retrieval strategy preset. These drive the ablation
study section of the arXiv paper — showing which components of Engram's
strategy pipeline contribute to accuracy and token efficiency.

Config names mirror the STRATEGY_PRESETS in run_benchmark.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AblationConfig:
    name: str
    description: str
    # Retrieval strategy flags
    superseded_pruning: bool = True
    confidence_threshold: float | None = None   # None = disabled
    recency_decay: bool = True
    top_k: int | None = None                    # None = no limit
    token_budget: int | None = None             # None = no limit
    # Retrieval mode
    use_bfs: bool = True                        # False = flat vector/keyword only
    # For baseline comparisons
    full_context: bool = False                  # Inject raw transcript instead
    # Domain profile for extraction
    domain: str = "episodic_personal"           # Profile name from domain_profiles.py


# ---------------------------------------------------------------------------
# Named configurations used in the paper
# ---------------------------------------------------------------------------

ABLATION_CONFIGS: dict[str, AblationConfig] = {
    # ---- Baselines ----
    "full_context": AblationConfig(
        name="full_context",
        description="Entire conversation transcript injected (no memory system). "
                    "Upper bound on context; lower bound on token efficiency.",
        full_context=True,
        use_bfs=False,
    ),
    "no_memory": AblationConfig(
        name="no_memory",
        description="No context injected at all. Tests LLM's parametric knowledge only.",
        full_context=False,
        use_bfs=False,
        superseded_pruning=False,
        recency_decay=False,
    ),

    # ---- Engram ablations (pipeline component removal) ----
    "engram_all_off": AblationConfig(
        name="engram_all_off",
        description="Engram retrieval with ALL strategy pipeline components disabled. "
                    "Raw BFS graph traversal output.",
        superseded_pruning=False,
        confidence_threshold=None,
        recency_decay=False,
        top_k=None,
        token_budget=None,
    ),
    "engram_superseded_only": AblationConfig(
        name="engram_superseded_only",
        description="Only superseded_pruning enabled. Isolates contribution of "
                    "contradiction resolution.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=False,
        top_k=None,
        token_budget=None,
    ),
    "engram_no_superseded": AblationConfig(
        name="engram_no_superseded",
        description="All pipeline components EXCEPT superseded_pruning. Shows cost of "
                    "removing contradiction resolution.",
        superseded_pruning=False,
        confidence_threshold=0.6,
        recency_decay=True,
        top_k=50,
        token_budget=2000,
    ),
    "engram_default": AblationConfig(
        name="engram_default",
        description="Default Engram pipeline: superseded_pruning + recency_decay + "
                    "relevance scoring. No hard token budget.",
        superseded_pruning=True,
        confidence_threshold=None,
        recency_decay=True,
        top_k=50,
        token_budget=None,
    ),
    "engram_filtered": AblationConfig(
        name="engram_filtered",
        description="Default + confidence threshold 0.6. Filters low-confidence nodes.",
        superseded_pruning=True,
        confidence_threshold=0.6,
        recency_decay=True,
        top_k=50,
        token_budget=None,
    ),
    "engram_tight": AblationConfig(
        name="engram_tight",
        description="All pipeline components + aggressive 750-token budget. "
                    "Maximizes token efficiency at potential accuracy cost.",
        superseded_pruning=True,
        confidence_threshold=0.6,
        recency_decay=True,
        top_k=20,
        token_budget=750,
    ),
}


# Configs to run in a standard benchmark sweep (ordered for the paper table)
PAPER_CONFIGS = [
    "no_memory",
    "full_context",
    "engram_all_off",
    "engram_superseded_only",
    "engram_no_superseded",
    "engram_default",
    "engram_filtered",
    "engram_tight",
]
