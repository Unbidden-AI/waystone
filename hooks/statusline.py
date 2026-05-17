#!/usr/bin/env python3
"""Claude Code status line script with Waystone retrieval metrics.

Displays standard session info (model, context %, cost) plus CB metrics:
  - While buffering: turn count until first extraction
  - Once active: nodes retrieved, tokens injected as % of context window,
    tokens saved (in graph but not injected), and retrieval latency

Configure in ~/.claude/settings.json:
  {
    "statusLine": {
      "type": "command",
      "command": "python /path/to/hooks/statusline.py"
    }
  }

Or run the installer:
  python hooks/install.py
"""

import json
import sys
import time
from pathlib import Path

STATE_DIR = Path.home() / ".waystone"
STATE_MAX_AGE_SECS = 300  # Don't show stale CB state after 5 min


def main():
    try:
        session = json.loads(sys.stdin.read())
    except Exception:
        session = {}

    ctx = session.get("context_window", {}) or {}
    model = (session.get("model") or {}).get("display_name", "")
    cost = (session.get("cost") or {})
    total_cost = cost.get("total_cost_usd") or 0
    used_pct = ctx.get("used_percentage") or 0
    ctx_size = ctx.get("context_window_size") or 0

    session_id = session.get("session_id", "")
    cb = _load_cb_state(session_id)

    parts = []

    if model:
        parts.append(model)

    if used_pct:
        parts.append(f"ws {_pct_bar(used_pct)} {used_pct:.0f}%")

    if total_cost:
        parts.append(f"${total_cost:.4f}")

    cb_str = _format_cb(cb, ctx_size)
    if cb_str:
        parts.append(cb_str)

    print(" │ ".join(parts))


def _load_cb_state(session_id: str = "") -> dict:
    try:
        if session_id:
            p = STATE_DIR / "state" / f"{session_id}.json"
        else:
            p = STATE_DIR / "state.json"
        if not p.exists():
            return {}
        age = time.time() - p.stat().st_mtime
        if age > STATE_MAX_AGE_SECS:
            return {}
        return json.loads(p.read_text())
    except Exception:
        return {}


def _format_cb(state: dict, ctx_size: int) -> str:
    status = state.get("status")
    project = state.get("project", "?")
    extracting = state.get("extracting", False)
    extract_started_at = state.get("extract_started_at")

    # Extraction suffix shown whenever a background worker is running
    if extracting and extract_started_at:
        elapsed_s = int(time.time() - extract_started_at)
        extract_str = f" ⟳{elapsed_s}s"
    else:
        extract_str = ""

    if status in ("no_graph", "empty"):
        return f"WS({project}): building graph…{extract_str}"
    if status == "buffering":
        turns = state.get("buffered_turns", "?")
        return f"WS({project}): buffering ({turns} turns){extract_str}"
    if status == "paused":
        nodes_total = state.get("nodes_total", 0)
        if nodes_total:
            return f"WS({project}): paused ({nodes_total} nodes)"
        return f"WS({project}): paused"
    if status == "error":
        err = state.get("error", "")
        return f"WS({project}): error{(' — ' + err[:40]) if err else ''}"
    if status != "ok":
        return ""

    nodes_ret = state.get("nodes_retrieved", 0)
    nodes_total = state.get("nodes_total", 0)
    tokens_inj = state.get("tokens_injected", 0)
    tokens_filt = state.get("tokens_filtered", 0)
    elapsed = state.get("elapsed_ms", 0)

    if nodes_ret == 0:
        return f"WS({project}): no match{extract_str}"

    if ctx_size and tokens_inj:
        inj_pct = tokens_inj / ctx_size * 100
        inj_str = f"~{tokens_inj}tok ({inj_pct:.1f}% ctx)"
    else:
        inj_str = f"~{tokens_inj}tok"

    if tokens_filt > 50:
        if ctx_size:
            saved_pct = tokens_filt / ctx_size * 100
            saved_str = f" saved {tokens_filt}tok ({saved_pct:.1f}%)"
        else:
            saved_str = f" saved {tokens_filt}tok"
    else:
        saved_str = ""

    return (
        f"WS({project}): {nodes_ret}/{nodes_total} nodes"
        f" {inj_str}{saved_str}"
        f" [{elapsed}ms]{extract_str}"
    )


def _pct_bar(pct: float, width: int = 8) -> str:
    filled = round(pct / 100 * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


if __name__ == "__main__":
    main()
