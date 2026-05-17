"""Domain profile bootstrapper for Waystone.

Automatically derives a DomainProfile from sample documents by running a
two-pass schema discovery:

  1. **Discovery pass** — for each sample document, ask the LLM: "given this
     content, what node types and edge relations make sense?"
  2. **Synthesis pass** — aggregate all candidate types across samples and ask
     the LLM to consolidate into a clean, non-redundant schema.

Usage (Python):
    from waystone.domain_bootstrapper import bootstrap_domain
    from waystone.config import load_config

    config = load_config()
    samples = [open("sample1.txt").read(), open("sample2.txt").read()]
    profile = await bootstrap_domain("medical", samples, config)
    print(profile)

Usage (CLI via `waystone`):
    waystone bootstrap-domain --name medical --samples s1.txt s2.txt s3.txt
    waystone bootstrap-domain --name legal --samples *.txt --output legal_profile.py
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from .domain_profiles import DomainProfile


# ---------------------------------------------------------------------------
# Discovery prompt (one per sample document)
# ---------------------------------------------------------------------------

_DISCOVERY_PROMPT = """\
You are designing a knowledge graph schema. Given the sample document below,
propose the ideal node types and edge relations for extracting structured,
reusable knowledge from similar documents in this domain.

Think about:
- What are the distinct *kinds* of information worth remembering long-term?
- What *relationships* connect those kinds?

Guidelines:
- Propose 5-10 node types. Each should be meaningfully distinct.
- Propose 3-7 edge relations. Each should represent a different structural link.
- Use snake_case for names.
- Write descriptions that guide extraction (tell the LLM *what to capture*,
  not just a dictionary definition).

Return ONLY valid JSON — no markdown fences, no explanation:
{{
  "domain_hint": "<one sentence: what kind of content is this?>",
  "node_types": [
    {{"name": "example_type", "description": "what this captures and when to use it"}},
    ...
  ],
  "edge_relations": [
    {{"name": "example_rel", "description": "what structural link this represents"}},
    ...
  ]
}}

Document:
---
{document}
---"""


# ---------------------------------------------------------------------------
# Synthesis prompt (one call across all samples)
# ---------------------------------------------------------------------------

_SYNTHESIS_PROMPT = """\
You are finalizing a knowledge graph schema for a new domain. Multiple sample
documents have been analyzed and each produced candidate node types and edge
relations. Synthesize them into a single coherent, non-redundant schema.

Domain name: {domain_name}
Domain hints from samples:
{domain_hints}

Candidate node types collected from {n_samples} samples:
{candidate_node_types}

Candidate edge relations collected from {n_samples} samples:
{candidate_edge_relations}

Instructions:
- Merge synonymous types (e.g. "diagnosis" + "medical_diagnosis" → "diagnosis").
- Drop types that appeared once and don't generalize to the domain.
- Keep 6-10 node types and 3-6 edge relations.
- Write descriptions that tell an extraction LLM what to capture and when —
  be specific to the domain.
- Add a node_types_note with 1-3 sentences of cross-cutting extraction guidance
  unique to this domain (e.g. how to handle updates, what to always tag, etc.)
  Leave it empty string "" if there is nothing important to say.

Return ONLY valid JSON:
{{
  "node_types": [
    {{"name": "...", "description": "..."}},
    ...
  ],
  "edge_relations": [
    {{"name": "...", "description": "..."}},
    ...
  ],
  "node_types_note": "..."
}}"""


# ---------------------------------------------------------------------------
# Core bootstrap logic
# ---------------------------------------------------------------------------

@dataclass
class BootstrapResult:
    """Full result of a domain bootstrap run, including intermediate data."""
    profile: DomainProfile
    domain_hints: list[str]
    raw_candidates: list[dict]  # one per sample: {node_types: [...], edge_relations: [...]}
    synthesis_raw: dict         # raw JSON from synthesis LLM call


async def bootstrap_domain(
    domain_name: str,
    sample_documents: list[str],
    config: dict,
    *,
    verbose: bool = False,
) -> BootstrapResult:
    """Derive a DomainProfile from sample documents.

    Args:
        domain_name: Short snake_case name for the new domain (e.g. "medical").
        sample_documents: List of raw text samples representative of the domain.
        config: Waystone config dict (must have llm settings).
        verbose: Print progress to stdout.

    Returns:
        BootstrapResult with .profile being the derived DomainProfile.
    """
    if not sample_documents:
        raise ValueError("At least one sample document is required")

    # --- Pass 1: discovery (parallel across all samples) ---
    if verbose:
        print(f"[bootstrap] Discovery pass: {len(sample_documents)} samples...")

    discovery_tasks = [
        _discovery_pass(doc, config, idx, verbose)
        for idx, doc in enumerate(sample_documents)
    ]
    raw_candidates = await asyncio.gather(*discovery_tasks)
    raw_candidates = [r for r in raw_candidates if r is not None]

    if not raw_candidates:
        raise RuntimeError("All discovery passes failed — check LLM config and sample content")

    # --- Aggregate candidates ---
    domain_hints = [c.get("domain_hint", "") for c in raw_candidates if c.get("domain_hint")]

    all_node_types: list[dict] = []
    all_edge_relations: list[dict] = []
    for candidate in raw_candidates:
        all_node_types.extend(candidate.get("node_types", []))
        all_edge_relations.extend(candidate.get("edge_relations", []))

    if verbose:
        unique_node_names = {t["name"] for t in all_node_types if "name" in t}
        unique_edge_names = {r["name"] for r in all_edge_relations if "name" in r}
        print(f"[bootstrap] Collected {len(all_node_types)} node type proposals "
              f"({len(unique_node_names)} unique names)")
        print(f"[bootstrap] Collected {len(all_edge_relations)} edge relation proposals "
              f"({len(unique_edge_names)} unique names)")

    # --- Pass 2: synthesis ---
    if verbose:
        print("[bootstrap] Synthesis pass...")

    synthesis_raw = await _synthesis_pass(
        domain_name=domain_name,
        domain_hints=domain_hints,
        all_node_types=all_node_types,
        all_edge_relations=all_edge_relations,
        n_samples=len(raw_candidates),
        config=config,
    )

    # --- Build DomainProfile ---
    node_types = {
        t["name"]: t["description"]
        for t in synthesis_raw.get("node_types", [])
        if t.get("name") and t.get("description")
    }
    edge_relations = {
        r["name"]: r["description"]
        for r in synthesis_raw.get("edge_relations", [])
        if r.get("name") and r.get("description")
    }

    if not node_types:
        raise RuntimeError("Synthesis produced no node types — check LLM response")

    profile = DomainProfile(
        name=domain_name,
        node_types=node_types,
        edge_relations=edge_relations,
        node_types_note=synthesis_raw.get("node_types_note", ""),
    )

    if verbose:
        print(f"[bootstrap] Done — {len(node_types)} node types, "
              f"{len(edge_relations)} edge relations")

    return BootstrapResult(
        profile=profile,
        domain_hints=domain_hints,
        raw_candidates=list(raw_candidates),
        synthesis_raw=synthesis_raw,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _discovery_pass(
    document: str,
    config: dict,
    idx: int,
    verbose: bool,
) -> dict | None:
    """Run one discovery pass for a single sample document."""
    # Truncate very long documents to ~4000 words so we don't blow token budget
    words = document.split()
    if len(words) > 4000:
        document = " ".join(words[:4000]) + "\n[... truncated ...]"

    prompt = _DISCOVERY_PROMPT.format(document=document)
    try:
        from .extractor import _call_llm
        content = await _call_llm(prompt, config)
        result = _parse_json(content)
        if verbose:
            nt = len(result.get("node_types", []))
            er = len(result.get("edge_relations", []))
            print(f"[bootstrap]   sample {idx + 1}: {nt} node types, {er} edge relations — "
                  f"{result.get('domain_hint', '?')!r}")
        return result
    except Exception as e:
        if verbose:
            print(f"[bootstrap]   sample {idx + 1}: discovery failed — {e}")
        return None


async def _synthesis_pass(
    domain_name: str,
    domain_hints: list[str],
    all_node_types: list[dict],
    all_edge_relations: list[dict],
    n_samples: int,
    config: dict,
) -> dict:
    """Run the synthesis pass to consolidate candidate types into a final schema."""
    hints_text = "\n".join(f"  - {h}" for h in domain_hints) or "  (none)"
    node_types_text = _format_candidates(all_node_types)
    edge_relations_text = _format_candidates(all_edge_relations)

    prompt = _SYNTHESIS_PROMPT.format(
        domain_name=domain_name,
        domain_hints=hints_text,
        n_samples=n_samples,
        candidate_node_types=node_types_text,
        candidate_edge_relations=edge_relations_text,
    )

    from .extractor import _call_llm
    content = await _call_llm(prompt, config)
    return _parse_json(content)


def _format_candidates(candidates: list[dict]) -> str:
    """Format a list of {name, description} candidates for the synthesis prompt."""
    if not candidates:
        return "  (none)"
    # Count frequency of each name for context
    from collections import Counter
    freq: Counter = Counter(c.get("name", "") for c in candidates)
    # Group by name, collect distinct descriptions
    seen: dict[str, list[str]] = {}
    for c in candidates:
        name = c.get("name", "")
        desc = c.get("description", "")
        if name:
            seen.setdefault(name, [])
            if desc and desc not in seen[name]:
                seen[name].append(desc)
    lines = []
    for name, descs in sorted(seen.items(), key=lambda kv: -freq[kv[0]]):
        count = freq[name]
        first_desc = descs[0] if descs else ""
        lines.append(f"  - {name} (×{count}): {first_desc}")
        for extra in descs[1:3]:  # show up to 2 additional descriptions
            lines.append(f"      alt: {extra}")
    return "\n".join(lines)


def _parse_json(content: str) -> dict:
    """Parse JSON from LLM response, stripping think tags and code fences."""
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Find first balanced { block
        start = cleaned.find("{")
        if start != -1:
            depth = 0
            in_str = False
            esc = False
            for i, ch in enumerate(cleaned[start:], start):
                if esc:
                    esc = False
                    continue
                if ch == "\\" and in_str:
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return json.loads(cleaned[start:i + 1])
        raise ValueError(f"Could not parse JSON from LLM response: {content[:300]}")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def profile_to_python(profile: DomainProfile, var_name: str | None = None) -> str:
    """Render a DomainProfile as Python source code for embedding in domain_profiles.py.

    Args:
        profile: The DomainProfile to render.
        var_name: Variable name (default: uppercased domain name, e.g. MEDICAL).

    Returns:
        A Python source string ready to paste into domain_profiles.py.
    """
    if var_name is None:
        var_name = profile.name.upper().replace("-", "_")

    lines = [f'{var_name} = DomainProfile(']
    lines.append(f'    name="{profile.name}",')
    lines.append('    node_types={')
    for name, desc in profile.node_types.items():
        escaped = desc.replace('"', '\\"')
        lines.append(f'        "{name}": (')
        # Wrap long descriptions
        for chunk in _wrap_text(escaped, width=72):
            lines.append(f'            "{chunk}"')
        lines.append('        ),')
    lines.append('    },')
    lines.append('    edge_relations={')
    for name, desc in profile.edge_relations.items():
        escaped = desc.replace('"', '\\"')
        lines.append(f'        "{name}": "{escaped}",')
    lines.append('    },')
    note = profile.node_types_note.replace('"', '\\"')
    if note:
        lines.append(f'    node_types_note=(')
        for chunk in _wrap_text(note, width=72):
            lines.append(f'        "{chunk}"')
        lines.append('    ),')
    else:
        lines.append('    node_types_note="",')
    lines.append(')')
    return "\n".join(lines)


def profile_to_yaml(profile: DomainProfile) -> str:
    """Render a DomainProfile as a YAML config snippet."""
    lines = [f"domain:"]
    lines.append(f"  name: {profile.name}")
    lines.append(f"  # Node types: {', '.join(profile.node_types)}")
    lines.append(f"  # Edge relations: {', '.join(profile.edge_relations)}")
    return "\n".join(lines)


def _wrap_text(text: str, width: int = 72) -> list[str]:
    """Split text into chunks of at most `width` chars, breaking on spaces.

    Each non-final chunk keeps a trailing space so adjacent string literals
    concatenate correctly when emitted as Python source.
    """
    if len(text) <= width:
        return [text]
    chunks = []
    while len(text) > width:
        split_at = text.rfind(" ", 0, width)
        if split_at == -1:
            split_at = width
            chunks.append(text[:split_at])
            text = text[split_at:]
        else:
            # Include the space in the current chunk so concatenation is seamless
            chunks.append(text[:split_at + 1])
            text = text[split_at + 1:]
    if text:
        chunks.append(text)
    return chunks
