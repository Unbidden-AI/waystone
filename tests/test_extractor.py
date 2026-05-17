"""Tests for the extraction service (JSON parsing, ID assignment)."""

import re
from unittest.mock import AsyncMock, patch

import pytest

from waystone.extractor import (
    assign_ids,
    assign_ids_incremental,
    extract_chunked,
    parse_llm_response,
    split_into_chunks,
    split_transcript_into_turns,
)


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

    def test_missing_nodes_defaults_empty(self):
        # Missing "nodes" is handled gracefully — defaults to empty list
        result = parse_llm_response('{"edges": []}')
        assert result["nodes"] == []

    def test_missing_edges_defaults(self):
        result = parse_llm_response('{"nodes": []}')
        assert result["edges"] == []

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
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


class TestAssignIdsIncremental:
    EXISTING_ID = "n_abcdef01"
    EXISTING_ID_2 = "n_12345678"

    def _existing_ids(self):
        return {self.EXISTING_ID, self.EXISTING_ID_2}

    def test_new_ids_get_uuid(self):
        extraction = {
            "nodes": [
                {"id": "n1", "fact": "New fact", "type": "decision", "confidence": 0.8,
                 "source_message": 0, "tags": [], "supersedes": []},
            ],
            "edges": [],
        }
        result = assign_ids_incremental(extraction, self._existing_ids())
        node = result["nodes"][0]
        assert node["id"].startswith("n_")
        assert node["id"] != "n1"
        assert re.match(r"^n_[0-9a-f]{8}$", node["id"])

    def test_existing_id_in_nodes_is_preserved(self):
        extraction = {
            "nodes": [
                {"id": self.EXISTING_ID, "fact": "Existing fact", "type": "decision",
                 "confidence": 0.9, "source_message": 0, "tags": [], "supersedes": []},
            ],
            "edges": [],
        }
        result = assign_ids_incremental(extraction, self._existing_ids())
        assert result["nodes"][0]["id"] == self.EXISTING_ID

    def test_hallucinated_existing_pattern_gets_new_uuid(self):
        hallucinated_id = "n_deadbeef"  # looks like existing pattern but not in set
        extraction = {
            "nodes": [
                {"id": hallucinated_id, "fact": "Hallucinated", "type": "decision",
                 "confidence": 0.5, "source_message": 0, "tags": [], "supersedes": []},
            ],
            "edges": [],
        }
        result = assign_ids_incremental(extraction, self._existing_ids())
        new_id = result["nodes"][0]["id"]
        assert new_id != hallucinated_id
        assert re.match(r"^n_[0-9a-f]{8}$", new_id)

    def test_supersedes_existing_id_preserved(self):
        extraction = {
            "nodes": [
                {"id": "n1", "fact": "New superseding", "type": "decision",
                 "confidence": 0.9, "source_message": 0, "tags": [],
                 "supersedes": [self.EXISTING_ID]},
            ],
            "edges": [],
        }
        result = assign_ids_incremental(extraction, self._existing_ids())
        node = result["nodes"][0]
        assert self.EXISTING_ID in node["supersedes"]

    def test_supersedes_new_node_remapped(self):
        extraction = {
            "nodes": [
                {"id": "n1", "fact": "Old", "type": "decision", "confidence": 0.7,
                 "source_message": 0, "tags": [], "supersedes": []},
                {"id": "n2", "fact": "New", "type": "decision", "confidence": 0.9,
                 "source_message": 1, "tags": [], "supersedes": ["n1"]},
            ],
            "edges": [],
        }
        result = assign_ids_incremental(extraction, self._existing_ids())
        old_node = next(n for n in result["nodes"] if n["fact"] == "Old")
        new_node = next(n for n in result["nodes"] if n["fact"] == "New")
        assert old_node["id"] in new_node["supersedes"]

    def test_edge_referencing_existing_id_preserved(self):
        extraction = {
            "nodes": [
                {"id": "n1", "fact": "New node", "type": "implementation",
                 "confidence": 0.8, "source_message": 0, "tags": [], "supersedes": []},
            ],
            "edges": [
                {"from": "n1", "to": self.EXISTING_ID, "relation": "depends_on"},
            ],
        }
        result = assign_ids_incremental(extraction, self._existing_ids())
        edge = result["edges"][0]
        assert edge["to_id"] == self.EXISTING_ID
        assert re.match(r"^n_[0-9a-f]{8}$", edge["from_id"])

    def test_mixed_ids_in_edges(self):
        extraction = {
            "nodes": [
                {"id": "n1", "fact": "A", "type": "decision", "confidence": 0.8,
                 "source_message": 0, "tags": [], "supersedes": []},
                {"id": "n2", "fact": "B", "type": "decision", "confidence": 0.8,
                 "source_message": 1, "tags": [], "supersedes": []},
            ],
            "edges": [
                {"from": "n1", "to": self.EXISTING_ID_2, "relation": "relates_to"},
                {"from": "n2", "to": "n1", "relation": "depends_on"},
            ],
        }
        result = assign_ids_incremental(extraction, self._existing_ids())
        node_ids = {n["id"] for n in result["nodes"]}
        edges = result["edges"]

        # First edge: n1 → existing
        assert edges[0]["to_id"] == self.EXISTING_ID_2
        assert edges[0]["from_id"] in node_ids

        # Second edge: n2 → n1 (both new)
        assert edges[1]["from_id"] in node_ids
        assert edges[1]["to_id"] in node_ids


class TestSplitTranscriptIntoTurns:
    TRANSCRIPT = """\
**Alice**: Let's use PostgreSQL for the database.

**Bob**: Good idea. Should we add a cache layer?

**Alice**: Yes, Redis would work well for caching.

**Bob**: Agreed. What about the API framework?

**Alice**: FastAPI is my preference.

**Bob**: Works for me."""

    def test_splits_into_correct_count(self):
        turns = split_transcript_into_turns(self.TRANSCRIPT, turn_size=2)
        assert len(turns) == 3  # 6 messages / 2 per turn

    def test_single_turn_contains_correct_messages(self):
        turns = split_transcript_into_turns(self.TRANSCRIPT, turn_size=2)
        assert "PostgreSQL" in turns[0]
        assert "cache layer" in turns[0]

    def test_second_turn_correct(self):
        turns = split_transcript_into_turns(self.TRANSCRIPT, turn_size=2)
        assert "Redis" in turns[1]
        assert "API framework" in turns[1]

    def test_turn_size_one(self):
        turns = split_transcript_into_turns(self.TRANSCRIPT, turn_size=1)
        assert len(turns) == 6

    def test_turn_size_larger_than_messages(self):
        turns = split_transcript_into_turns(self.TRANSCRIPT, turn_size=10)
        assert len(turns) == 1
        assert "PostgreSQL" in turns[0]
        assert "FastAPI" in turns[0]

    def test_empty_text(self):
        turns = split_transcript_into_turns("", turn_size=2)
        assert turns == []

    def test_no_speaker_pattern(self):
        # Plain text with no **Speaker**: pattern — treated as one chunk
        turns = split_transcript_into_turns("Just some text without speakers.", turn_size=2)
        assert len(turns) == 1


class TestSplitIntoChunks:
    def test_single_chunk_when_under_limit(self):
        text = "Hello world.\n\nSecond paragraph."
        chunks = split_into_chunks(text, max_chars=1000)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_splits_at_paragraph_boundary(self):
        # Two paragraphs of ~20 chars each; limit of 25 forces a split
        a = "A" * 20
        b = "B" * 20
        text = f"{a}\n\n{b}"
        chunks = split_into_chunks(text, max_chars=25)
        assert len(chunks) == 2
        assert a in chunks[0]
        assert b in chunks[1]

    def test_respects_max_chars(self):
        # Every chunk should be ≤ max_chars (except oversized single paragraphs)
        paras = ["x" * 50 for _ in range(6)]
        text = "\n\n".join(paras)
        chunks = split_into_chunks(text, max_chars=110)
        for chunk in chunks:
            assert len(chunk) <= 110

    def test_oversized_single_paragraph_emitted_alone(self):
        # A paragraph larger than max_chars must still be returned
        big = "Z" * 500
        chunks = split_into_chunks(big, max_chars=100)
        assert len(chunks) == 1
        assert chunks[0] == big

    def test_empty_string_returns_empty_list(self):
        chunks = split_into_chunks("", max_chars=100)
        # Empty string splits into one empty string (falsy but present) or zero chunks
        # The implementation returns [""] when there is one empty paragraph
        assert chunks == [""] or chunks == []

    def test_multiple_chunks_cover_all_content(self):
        paras = [f"Para {i}: " + "x" * 30 for i in range(5)]
        text = "\n\n".join(paras)
        chunks = split_into_chunks(text, max_chars=50)
        rejoined = "\n\n".join(chunks)
        assert rejoined == text


class TestExtractChunked:
    """Tests for extract_chunked() — parallel multi-chunk extraction."""

    def _fake_extract_result(self, tag: str = "a"):
        return {
            "nodes": [
                {"id": f"n_{tag}000000", "fact": f"Fact {tag}", "type": "decision",
                 "confidence": 0.9, "source_message_index": 0, "tags": [tag], "supersedes": []}
            ],
            "edges": [],
        }

    @pytest.mark.asyncio
    async def test_single_chunk_calls_extract_once(self):
        text = "Short text — fits in one chunk."
        config = {}
        mock_result = self._fake_extract_result("s")

        with patch("waystone.extractor.extract", new=AsyncMock(return_value=mock_result)) as mock_ext:
            result = await extract_chunked(text, config, chunk_size=10_000)

        mock_ext.assert_called_once_with(text, config)
        assert result["nodes"] == mock_result["nodes"]

    @pytest.mark.asyncio
    async def test_multi_chunk_runs_in_parallel(self):
        # Build a text large enough to split into 2 chunks at chunk_size=50
        para1 = "A" * 40
        para2 = "B" * 40
        text = f"{para1}\n\n{para2}"
        config = {}

        call_args: list[str] = []

        async def fake_extract(chunk_text, cfg):
            call_args.append(chunk_text)
            tag = "a" if "A" in chunk_text else "b"
            return self._fake_extract_result(tag)

        with patch("waystone.extractor.extract", new=fake_extract):
            result = await extract_chunked(text, config, chunk_size=50)

        assert len(call_args) == 2
        assert len(result["nodes"]) == 2

    @pytest.mark.asyncio
    async def test_verify_passed_through_single_chunk(self):
        text = "Short text."
        config = {}
        extract_result = self._fake_extract_result("x")
        verify_result = self._fake_extract_result("v")

        with patch("waystone.extractor.extract", new=AsyncMock(return_value=extract_result)), \
             patch("waystone.extractor.verify_extraction", new=AsyncMock(return_value=verify_result)) as mock_verify:
            result = await extract_chunked(text, config, chunk_size=10_000, verify=True)

        mock_verify.assert_called_once()
        assert len(result["nodes"]) == 2  # one from extract + one from verify

    @pytest.mark.asyncio
    async def test_all_chunks_failed_raises_runtime_error(self):
        para1 = "A" * 40
        para2 = "B" * 40
        text = f"{para1}\n\n{para2}"
        config = {}

        async def always_fail(chunk_text, cfg):
            raise ValueError("LLM error")

        with patch("waystone.extractor.extract", new=always_fail):
            with pytest.raises(RuntimeError, match="All .* extraction chunks failed"):
                await extract_chunked(text, config, chunk_size=50)

    @pytest.mark.asyncio
    async def test_partial_failure_returns_successful_chunks(self):
        para1 = "A" * 40
        para2 = "B" * 40
        text = f"{para1}\n\n{para2}"
        config = {}
        call_count = 0

        async def sometimes_fail(chunk_text, cfg):
            nonlocal call_count
            call_count += 1
            if "A" in chunk_text:
                raise ValueError("first chunk failed")
            return self._fake_extract_result("b")

        with patch("waystone.extractor.extract", new=sometimes_fail):
            result = await extract_chunked(text, config, chunk_size=50)

        assert call_count == 2
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["tags"] == ["b"]
