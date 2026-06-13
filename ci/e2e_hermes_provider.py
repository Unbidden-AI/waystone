#!/usr/bin/env python3
"""E2E: prove the Waystone memory provider is compatible with REAL hermes-agent.

Validated 2026-06-13 against hermes-agent 0.16.0 (PyPI package name: `hermes-agent`).
Unlike the unit tests (which use a stub base class when Hermes is absent), this binds
the provider to Hermes's actual `agent.memory_provider.MemoryProvider` ABC and exercises
it — catching any drift in Hermes's base class between versions.

Prereqs on the runner (run from the repo root so `hermes_plugin` imports):
    pip install hermes-agent waystone
    # extraction LLM key for the seeded graph is NOT needed (retrieval only)

Usage:  python ci/e2e_hermes_provider.py        # exit 0 = pass, 1 = fail
"""

import json
import os
import sys
import tempfile
from pathlib import Path

SECRET = "NOUS-FLINT-9920"


def main() -> int:
    try:
        from agent.memory_provider import MemoryProvider  # real Hermes ABC
    except ImportError:
        print("SKIP: hermes-agent not installed (pip install hermes-agent)")
        return 0  # not a failure on a box without Hermes

    work = Path(tempfile.mkdtemp())
    db = work / "projects" / "hdemo" / "context.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    from waystone.store import GraphStore
    s = GraphStore(db)
    s.add_node({"id": "n1", "fact": f"The Hermes signing secret is {SECRET}",
                "type": "decision", "confidence": 1.0,
                "tags": ["hermes", "secret", "signing"],
                "created_at": "2026-06-01T00:00:00Z", "supersedes": []})
    s.close()
    (work / "config.yaml").write_text(f"projects_dir: {work / 'projects'}\n")
    os.environ["WAYSTONE_CONFIG"] = str(work / "config.yaml")
    os.environ["WAYSTONE_PROJECT"] = "hdemo"

    import hermes_plugin
    # The provider MUST have bound to the real ABC, not the stub.
    if hermes_plugin._get_base() is not MemoryProvider:
        print("FAIL: provider did not bind to real hermes MemoryProvider", file=sys.stderr)
        return 1
    if not issubclass(hermes_plugin.WaystoneMemoryProvider, MemoryProvider):
        print("FAIL: WaystoneMemoryProvider is not a MemoryProvider subclass", file=sys.stderr)
        return 1

    p = hermes_plugin.WaystoneMemoryProvider()  # instantiate → no missing abstract methods
    p.initialize("e2e", hermes_home=str(work / ".hermes"))
    if not p.is_available():
        print("FAIL: provider not available against seeded graph", file=sys.stderr)
        return 1
    if SECRET not in p.prefetch("what is the hermes signing secret"):
        print("FAIL: prefetch did not inject the seeded secret", file=sys.stderr)
        return 1
    if SECRET not in json.loads(p._handle_query({"query": "signing secret"}))["context"]:
        print("FAIL: waystone_query tool did not return the secret", file=sys.stderr)
        return 1

    print("PASS: Waystone provider is compatible with real hermes-agent and injects "
          "context (ABC bind + instantiate + is_available + prefetch + query tool)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
