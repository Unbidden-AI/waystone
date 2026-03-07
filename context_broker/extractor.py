"""LLM-based extraction service for Context Broker."""

import json
import re
import uuid

import httpx

from .prompts import build_extraction_prompt


async def extract(transcript_text: str, config: dict) -> dict:
    """Extract structured facts from a transcript using an LLM.

    Calls an OpenAI-compatible chat/completions endpoint and parses
    the JSON response into nodes and edges with proper IDs.

    Returns:
        dict with "nodes" (list[dict]) and "edges" (list[dict])
    """
    llm_cfg = config["llm"]
    prompt = build_extraction_prompt(transcript_text)

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{llm_cfg['base_url']}/chat/completions",
            json={
                "model": llm_cfg["model"],
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a context extraction engine. Return only valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": llm_cfg.get("temperature", 0.1),
                "max_tokens": llm_cfg.get("max_tokens", 4096),
            },
        )
        response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    extraction = parse_llm_response(content)
    return assign_ids(extraction)


def parse_llm_response(content: str) -> dict:
    """Parse LLM response text into structured extraction data.

    Handles both raw JSON and markdown-fenced JSON blocks.
    """
    # Strip markdown code fences if present
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", content.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse LLM response as JSON: {e}\nContent: {content[:500]}")

    if "nodes" not in result:
        raise ValueError("LLM response missing 'nodes' key")
    if "edges" not in result:
        result["edges"] = []

    return result


def assign_ids(extraction: dict) -> dict:
    """Assign proper UUIDs to nodes and remap edge references.

    The LLM returns short IDs like "n1", "n2". This replaces them
    with "n_<uuid-short>" and updates all edge from/to references.
    """
    id_map = {}
    nodes = []

    for node in extraction["nodes"]:
        old_id = node["id"]
        new_id = f"n_{uuid.uuid4().hex[:8]}"
        id_map[old_id] = new_id

        nodes.append({
            "id": new_id,
            "fact": node["fact"],
            "type": node["type"],
            "confidence": node.get("confidence", 0.5),
            "source_message_index": node.get("source_message"),
            "tags": node.get("tags", []),
            "supersedes": [id_map.get(s, s) for s in node.get("supersedes", [])],
        })

    edges = []
    for edge in extraction.get("edges", []):
        from_id = id_map.get(edge["from"], edge["from"])
        to_id = id_map.get(edge["to"], edge["to"])
        edges.append({
            "from_id": from_id,
            "to_id": to_id,
            "relation": edge["relation"],
        })

    return {"nodes": nodes, "edges": edges}
