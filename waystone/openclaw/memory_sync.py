"""MEMORY.md <-> Waystone graph synchronisation for the OpenClaw skill.

Three operations:
  bootstrap(store, cfg)       — render graph nodes → write Waystone section to MEMORY.md
  seed_from_memory_md(cfg)    — parse existing MEMORY.md → extract → seed graph
  write_back(store, cfg)      — refresh the Waystone section after new extraction

All file writes are atomic (fcntl.flock + NamedTemporaryFile + rename) to
prevent data loss if OpenClaw's native dreaming runs concurrently.

MEMORY.md layout after bootstrap/write-back:
    <user's own content unchanged>
    <!-- waystone:start -->
    ## Waystone Context
    <rendered nodes by type>
    <!-- waystone:end -->

The waystone block is bookended by HTML comments so we can reliably find and
replace it without touching the user's handwritten content.
"""

from __future__ import annotations

import fcntl
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from waystone.store import GraphStore

log = logging.getLogger(__name__)

# Sentinel comments that wrap our injected block
_START = "<!-- waystone:start -->"
_END = "<!-- waystone:end -->"

# Node types rendered, in display order
_TYPE_ORDER = [
    "decision",
    "transition",
    "constraint",
    "implementation",
    "lesson_learned",
    "preference",
    "question",
    "resolved",
]

_TYPE_LABELS = {
    "decision": "Decisions",
    "transition": "Transitions",
    "constraint": "Constraints",
    "implementation": "Implementation",
    "lesson_learned": "Lessons Learned",
    "preference": "Preferences",
    "question": "Open Questions",
    "resolved": "Resolved",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def bootstrap(store: "GraphStore", cfg: dict) -> bool:
    """Render graph nodes → MEMORY.md Waystone section.

    Called on session start. If the graph has nodes, writes them to the
    ``## Waystone Context`` section of MEMORY.md (creating the section if it
    doesn't exist). If the graph is empty, does nothing.

    Returns True if MEMORY.md was updated, False otherwise.
    """
    try:
        stats = store.get_stats()
        if stats.get("node_count", 0) == 0:
            return False

        top_k = cfg.get("context_top_k", 15)
        nodes = _get_top_nodes(store, top_k)
        if not nodes:
            return False

        section_header = cfg.get("memory_md_section", "## Waystone Context")
        rendered = _render_nodes(nodes, section_header)
        memory_path = _get_memory_md_path(cfg)
        _inject_section(memory_path, rendered, cfg)
        log.info("waystone: bootstrapped MEMORY.md with %d nodes", len(nodes))
        return True
    except Exception as e:
        log.warning("waystone: bootstrap failed: %s", e)
        return False


def seed_from_memory_md(cfg: dict) -> int:
    """Parse existing MEMORY.md → extract facts → seed graph.

    Called on session start when the Waystone graph is empty but MEMORY.md
    exists (e.g. first-time install, or user migrating from native OpenClaw
    memory to Waystone).

    Returns the number of nodes extracted (0 if nothing was done).
    """
    import asyncio
    from .errors import LLMExtractionError, DBInitError

    memory_path = _get_memory_md_path(cfg)
    if not memory_path.exists():
        return 0

    try:
        text = memory_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        log.warning("waystone: could not read MEMORY.md: %s", e)
        return 0

    if not text:
        return 0

    # Strip the waystone block itself to avoid circular extraction
    text = _strip_waystone_block(text)
    if not text.strip():
        return 0

    waystone_cfg = cfg.get("_waystone", {})
    db_path = _get_db_path(cfg)

    try:
        from waystone.store import GraphStore
        store = GraphStore(db_path)
    except Exception as e:
        raise DBInitError(f"Could not open graph store at {db_path}: {e}") from e

    try:
        from waystone.extractor import extract
        result = asyncio.run(extract(text, waystone_cfg, store=store, source_transcript="memory_md_seed"))
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        if nodes:
            store.merge_extraction(nodes, edges)
        log.info("waystone: seeded %d nodes from MEMORY.md", len(nodes))
        return len(nodes)
    except Exception as e:
        raise LLMExtractionError(f"Extraction from MEMORY.md failed: {e}") from e
    finally:
        store.close()


def write_back(store: "GraphStore", cfg: dict) -> bool:
    """Refresh the Waystone section of MEMORY.md from the current graph.

    Called after successful extraction. Re-renders the top-K nodes and
    replaces the existing waystone block in MEMORY.md.

    Returns True if MEMORY.md was updated, False on failure.
    """
    try:
        top_k = cfg.get("context_top_k", 15)
        nodes = _get_top_nodes(store, top_k)
        section_header = cfg.get("memory_md_section", "## Waystone Context")
        rendered = _render_nodes(nodes, section_header)
        memory_path = _get_memory_md_path(cfg)
        _inject_section(memory_path, rendered, cfg)
        log.info("waystone: write-back complete (%d nodes)", len(nodes))
        return True
    except Exception as e:
        log.warning("waystone: write-back failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _get_top_nodes(store: "GraphStore", top_k: int) -> list[dict]:
    """Return top-K active nodes sorted by type priority then confidence desc."""
    try:
        all_nodes = store.get_all_nodes()
        # Filter to active nodes only
        all_nodes = [n for n in all_nodes if n.get("is_active", True)]
        # Sort by type priority then confidence descending
        type_priority = {t: i for i, t in enumerate(_TYPE_ORDER)}
        all_nodes.sort(
            key=lambda n: (
                type_priority.get(n.get("type", ""), len(_TYPE_ORDER)),
                -(n.get("confidence") or 0.5),
            )
        )
        return all_nodes[:top_k]
    except Exception as e:
        log.warning("waystone: could not fetch nodes: %s", e)
        return []


def _render_nodes(nodes: list[dict], section_header: str) -> str:
    """Render a list of nodes as a markdown section grouped by type."""
    if not nodes:
        return f"{section_header}\n\n_No context yet._"

    # Group by type
    groups: dict[str, list[dict]] = {}
    for node in nodes:
        t = node.get("type", "implementation")
        groups.setdefault(t, []).append(node)

    lines = [section_header, ""]
    for type_key in _TYPE_ORDER:
        if type_key not in groups:
            continue
        label = _TYPE_LABELS.get(type_key, type_key.replace("_", " ").title())
        lines.append(f"**{label}**")
        for node in groups[type_key]:
            fact = node.get("fact", "").strip()
            if fact:
                lines.append(f"- {fact}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _inject_section(memory_path: Path, content: str, cfg: dict) -> None:
    """Replace or append the Waystone block in MEMORY.md atomically."""
    from .errors import MemoryMDCorruptError

    # Ensure parent dir exists
    memory_path.parent.mkdir(parents=True, exist_ok=True)

    block = f"{_START}\n{content}\n{_END}"

    # Read existing content (empty string if file doesn't exist)
    existing = ""
    if memory_path.exists():
        try:
            existing = memory_path.read_text(encoding="utf-8")
        except Exception as e:
            raise MemoryMDCorruptError(f"Could not read {memory_path}: {e}") from e

    # Replace existing block or append
    if _START in existing and _END in existing:
        start_idx = existing.index(_START)
        end_idx = existing.index(_END) + len(_END)
        new_content = existing[:start_idx] + block + existing[end_idx:]
    else:
        separator = "\n\n" if existing and not existing.endswith("\n\n") else ""
        new_content = existing + separator + block + "\n"

    # Enforce max size: trim from oldest (top of our block) if over cap
    max_bytes = cfg.get("memory_md_max_bytes", 4096)
    if len(new_content.encode("utf-8")) > max_bytes:
        new_content = _trim_to_cap(new_content, max_bytes)

    _atomic_write(memory_path, new_content)


def _trim_to_cap(content: str, max_bytes: int) -> str:
    """Trim the Waystone section to fit within max_bytes.

    Removes facts from the bottom of the Waystone block (oldest/lowest-priority
    are rendered last). Preserves all user content outside the block.
    """
    if _START not in content or _END not in content:
        # No waystone block to trim — return as-is (user content untouched)
        return content

    start_idx = content.index(_START)
    end_idx = content.index(_END) + len(_END)
    pre = content[:start_idx]
    block = content[start_idx:end_idx]
    post = content[end_idx:]

    # Trim lines from block until it fits
    block_lines = block.split("\n")
    while len((pre + "\n".join(block_lines) + post).encode("utf-8")) > max_bytes and len(block_lines) > 3:
        # Remove second-to-last line (last is _END sentinel)
        block_lines.pop(-2)

    return pre + "\n".join(block_lines) + post


def _strip_waystone_block(text: str) -> str:
    """Remove the Waystone-injected block from MEMORY.md text."""
    if _START not in text or _END not in text:
        return text
    start = text.index(_START)
    end = text.index(_END) + len(_END)
    return text[:start] + text[end:]


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically using flock + temp file + rename."""
    from .errors import MemoryMDCorruptError

    try:
        # Use a lock file alongside MEMORY.md to coordinate with OpenClaw
        lock_path = path.with_suffix(".engram_lock")
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                # Write to temp file in same dir (ensures same filesystem for rename)
                fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".engram_tmp_")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(content)
                    os.replace(tmp_path, path)
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        raise MemoryMDCorruptError(f"Atomic write to {path} failed: {e}") from e


def archive_overflow(cfg: dict) -> int:
    """Move content trimmed from MEMORY.md into MEMORY_ARCHIVE.md.

    When the Waystone block exceeds ``memory_md_max_bytes``, ``_trim_to_cap``
    silently drops the lowest-priority facts.  This function instead moves the
    trimmed content to a sidecar archive file so nothing is permanently lost.

    Reads MEMORY.md, re-renders from the graph with no cap to get the full
    set, and appends any facts not present in the current MEMORY.md to the
    archive file.  Safe to call after every write-back; it's a no-op when
    nothing was trimmed.

    Returns the number of lines appended to the archive.
    """
    memory_path = _get_memory_md_path(cfg)
    if not memory_path.exists():
        return 0

    archive_path = memory_path.with_name("MEMORY_ARCHIVE.md")
    max_bytes = cfg.get("memory_md_max_bytes", 4096)

    try:
        current = memory_path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning("waystone: archive_overflow could not read MEMORY.md: %s", e)
        return 0

    # Extract the current waystone block lines
    if _START not in current or _END not in current:
        return 0
    start_idx = current.index(_START)
    end_idx = current.index(_END) + len(_END)
    current_block = current[start_idx:end_idx]
    current_lines = set(current_block.splitlines())

    # Re-render without cap to see what was trimmed
    try:
        from waystone.store import GraphStore
        db_path = _get_db_path(cfg)
        store = GraphStore(db_path)
        try:
            all_nodes = store.get_all_nodes()
            all_nodes = [n for n in all_nodes if n.get("is_active", True)]
        finally:
            store.close()
    except Exception as e:
        log.warning("waystone: archive_overflow could not open store: %s", e)
        return 0

    if not all_nodes:
        return 0

    section_header = cfg.get("memory_md_section", "## Waystone Context")
    full_rendered = _render_nodes(all_nodes, section_header)
    full_block = f"{_START}\n{full_rendered}\n{_END}"
    full_size = len((current[:start_idx] + full_block + current[end_idx:]).encode("utf-8"))

    if full_size <= max_bytes:
        return 0  # Nothing was trimmed

    # Find lines present in full render but absent from current (trimmed) block
    full_lines = full_block.splitlines()
    trimmed_lines = [ln for ln in full_lines if ln and ln not in current_lines and ln not in (_START, _END)]
    if not trimmed_lines:
        return 0

    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).isoformat()
        with open(archive_path, "a", encoding="utf-8") as f:
            f.write(f"\n<!-- archived {timestamp} -->\n")
            for ln in trimmed_lines:
                f.write(ln + "\n")
        log.info("waystone: archived %d trimmed line(s) to %s", len(trimmed_lines), archive_path)
        return len(trimmed_lines)
    except Exception as e:
        log.warning("waystone: archive_overflow write failed: %s", e)
        return 0


def _get_memory_md_path(cfg: dict) -> Path:
    from .config import get_memory_md_path
    return get_memory_md_path(cfg)


def _get_db_path(cfg: dict) -> Path:
    from .config import get_db_path
    return get_db_path(cfg)
