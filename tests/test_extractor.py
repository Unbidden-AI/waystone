"""Tests for the extraction service (JSON parsing, ID assignment)."""

import pytest

from context_broker.extractor import assign_ids, parse_llm_response


class TestParseLLMResponse:
    def test_parse_clean_json(self):
        content = '{"nodes": [{"id": "n1", "fact": "test", "type": "decision", "confidence": 0.9, "source_message": 0, "supersedes": [], "tags": ["api"]}], "edges": []}'
        result = parse_llm_response(content)
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["fact"] == "test"

    def test_parse_fenced_json(self):
        content = '```json\n{"nodes": [{"id": "n1", "fact": "test", "type": "decision", "confidence": 0.9, "source_message": 0, "supersedes": [], "tags": []}], "edges": []}\n```'
        result = parse_llm_response(content)
        assert len(result["nodes"]) == 1

    def test_parse_bare_fences(self):
        content = '```\n{"nodes": [], "edges": []}\n```'
        result = parse_llm_response(content)
        assert result["nodes"] == []

    def test_missing_nodes_raises(self):
        with pytest.raises(ValueError, match="missing 'nodes'"):
            parse_llm_response('{"edges": []}')

    def test_missing_edges_defaults(self):
        result = parse_llm_response('{"nodes": []}')
        assert result["edges"] == []

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Failed to parse"):
            parse_llm_response("this is not json at all")


class TestAssignIds:
    def test_ids_are_remapped(self):
        extraction = {
            "nodes": [
                {"id": "n1", "fact": "Fact A", "type": "decision", "confidence": 0.8, "source_message": 0, "tags": ["api"], "supersedes": []},
                {"id": "n2", "fact": "Fact B", "type": "implementation", "confidence": 0.9, "source_message": 1, "tags": ["db"], "supersedes": []},
            ],
            "edges": [
                {"from": "n1", "to": "n2", "relation": "depends_on"},
            ],
        }
        result = assign_ids(extraction)

        # Nodes should have new IDs starting with n_
        assert len(result["nodes"]) == 2
        for node in result["nodes"]:
            assert node["id"].startswith("n_")
            assert len(node["id"]) == 10  # "n_" + 8 hex chars

        # Edges should reference the new IDs
        edge = result["edges"][0]
        node_ids = {n["id"] for n in result["nodes"]}
        assert edge["from_id"] in node_ids
        assert edge["to_id"] in node_ids

    def test_supersedes_remapped(self):
        extraction = {
            "nodes": [
                {"id": "n1", "fact": "Old", "type": "decision", "confidence": 0.8, "source_message": 0, "tags": [], "supersedes": []},
                {"id": "n2", "fact": "New", "type": "decision", "confidence": 0.9, "source_message": 1, "tags": [], "supersedes": ["n1"]},
            ],
            "edges": [],
        }
        result = assign_ids(extraction)
        new_node = next(n for n in result["nodes"] if n["fact"] == "New")
        old_node = next(n for n in result["nodes"] if n["fact"] == "Old")
        assert old_node["id"] in new_node["supersedes"]

    def test_preserves_fields(self):
        extraction = {
            "nodes": [
                {"id": "n1", "fact": "Test", "type": "constraint", "confidence": 0.7, "source_message": 5, "tags": ["perf", "api"], "supersedes": []},
            ],
            "edges": [],
        }
        result = assign_ids(extraction)
        node = result["nodes"][0]
        assert node["fact"] == "Test"
        assert node["type"] == "constraint"
        assert node["confidence"] == 0.7
        assert node["source_message_index"] == 5
        assert node["tags"] == ["perf", "api"]
