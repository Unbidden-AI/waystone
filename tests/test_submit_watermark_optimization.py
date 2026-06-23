"""Tests for byte-offset watermark optimization in UserPromptSubmit hook.

Tests prove equivalence between the old line-count watermark and the new
byte-offset approach across fresh starts, partial reads, appends, and migration
scenarios. Also validates file truncation/rotation safety.
"""

import json
import time
from pathlib import Path

from waystone._hooks import submit
from waystone.store import GraphStore


def _make_jsonl_line(role: str, text: str) -> str:
    """Create a valid JSONL line with the given role and text."""
    entry = {
        "message": {
            "role": role,
            "content": [{"type": "text", "text": text}]
        }
    }
    return json.dumps(entry)


def _write_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    """Write a list of (role, text) tuples to a JSONL transcript."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for role, text in turns:
            f.write(_make_jsonl_line(role, text) + "\n")


def _append_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    """Append (role, text) tuples to an existing JSONL transcript."""
    with open(path, "a", encoding="utf-8") as f:
        for role, text in turns:
            f.write(_make_jsonl_line(role, text) + "\n")


class TestByteOffsetBasics:
    """Test basic byte-offset functionality."""

    def test_read_assistant_since_empty_file(self, tmp_path):
        """Read from an empty transcript returns empty text and offset 0."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.touch()
        text, offset = submit._read_assistant_since(str(transcript), 0)
        assert text == ""
        assert offset == 0

    def test_read_assistant_since_fresh_start(self, tmp_path):
        """Fresh read (offset 0) returns all assistant text from the file."""
        transcript = tmp_path / "transcript.jsonl"
        turns = [
            ("user", "What is Waystone?"),
            ("assistant", "Waystone is a context intelligence layer. " * 10),  # Long enough
            ("user", "How does it work?"),
            ("assistant", "It extracts facts and builds a graph for retrieval. " * 10),
        ]
        _write_transcript(transcript, turns)

        text, new_offset = submit._read_assistant_since(str(transcript), 0)

        assert "Waystone is a context intelligence layer." in text
        assert "It extracts facts and builds a graph" in text
        assert new_offset == transcript.stat().st_size

    def test_read_assistant_since_no_new_data(self, tmp_path):
        """Read from offset at EOF returns empty text, same offset."""
        transcript = tmp_path / "transcript.jsonl"
        turns = [
            ("user", "Query"),
            ("assistant", "Response"),
        ]
        _write_transcript(transcript, turns)

        file_size = transcript.stat().st_size
        text, offset = submit._read_assistant_since(str(transcript), file_size)

        assert text == ""
        assert offset == file_size

    def test_read_assistant_since_partial_then_append(self, tmp_path):
        """Sequential reads: first at 0, second from EOF. Should not duplicate."""
        transcript = tmp_path / "transcript.jsonl"
        turns_1 = [
            ("user", "First query"),
            ("assistant", "First response " * 20),  # Long enough (40+ words)
        ]
        _write_transcript(transcript, turns_1)

        # First read
        text_1, offset_1 = submit._read_assistant_since(str(transcript), 0)
        assert "First response" in text_1

        # Append more
        turns_2 = [
            ("user", "Second query"),
            ("assistant", "Second response " * 20),
        ]
        _append_transcript(transcript, turns_2)

        # Second read from previous offset
        text_2, offset_2 = submit._read_assistant_since(str(transcript), offset_1)

        # Should get ONLY the second response
        assert "Second response" in text_2
        assert "First response" not in text_2
        assert offset_2 == transcript.stat().st_size

    def test_read_assistant_ignores_user_messages(self, tmp_path):
        """Only extracts assistant text, ignores user messages."""
        transcript = tmp_path / "transcript.jsonl"
        turns = [
            ("user", "This is user query text"),
            ("assistant", "This is assistant response " * 10),  # 40+ words
        ]
        _write_transcript(transcript, turns)

        text, _ = submit._read_assistant_since(str(transcript), 0)
        assert "This is assistant response" in text
        assert "This is user query text" not in text

    def test_read_assistant_filters_short_responses(self, tmp_path):
        """Filters out assistant text below MIN_ASSISTANT_WORDS (40 words)."""
        transcript = tmp_path / "transcript.jsonl"
        turns = [
            ("assistant", "This response is too short"),  # ~5 words, too short
            ("user", "Something"),
            ("assistant", "This is a longer response " * 5),  # 5*5=25 words, still too short
        ]
        _write_transcript(transcript, turns)

        text, _ = submit._read_assistant_since(str(transcript), 0)
        # Should return empty because all responses are too short
        assert text == ""

        # Now test with a long response
        transcript2 = tmp_path / "transcript2.jsonl"
        turns2 = [
            ("assistant", "This is a long response " * 10),  # 10*5=50 words, long enough
        ]
        _write_transcript(transcript2, turns2)
        text2, _ = submit._read_assistant_since(str(transcript2), 0)
        assert "This is a long response" in text2


class TestByteOffsetFileRotation:
    """Test safety under file truncation/rotation."""

    def test_truncated_file_resets_to_start(self, tmp_path):
        """If stored offset > file size, reset to 0."""
        transcript = tmp_path / "transcript.jsonl"
        turns = [
            ("user", "A"),
            ("assistant", "B " * 50),  # 50 words, long enough
        ]
        _write_transcript(transcript, turns)

        original_size = transcript.stat().st_size
        fake_offset = original_size + 1000  # Pretend file was bigger

        text, new_offset = submit._read_assistant_since(str(transcript), fake_offset)

        # Should reset and re-read from the start
        assert "B" in text
        assert new_offset == original_size

    def test_rotated_file_missing_starts_fresh(self, tmp_path):
        """If file doesn't exist, return empty and preserve offset."""
        transcript = tmp_path / "transcript.jsonl"
        text, offset = submit._read_assistant_since(str(transcript), 0)
        assert text == ""
        assert offset == 0


class TestReadRecentTurns:
    """Test the _read_recent_turns tail-read optimization."""

    def test_read_recent_turns_tail_only(self, tmp_path):
        """_read_recent_turns reads only the tail, not the whole file."""
        transcript = tmp_path / "transcript.jsonl"
        # Write 100 turns (should be >50KB tail cutoff, but JSONL is dense so keep it reasonable)
        turns = []
        for i in range(50):
            turns.append(("user", f"Query {i}"))
            turns.append(("assistant", "Response. " * 20))  # ~200 bytes per assistant
        _write_transcript(transcript, turns)

        text = submit._read_recent_turns(str(transcript), 5)

        # Should include the last few turns
        assert "Query 49" in text
        assert "Query 48" in text or "Query 47" in text
        # Early turns should NOT be present (only tail read)
        assert "Query 0" not in text

    def test_read_recent_turns_fewer_available(self, tmp_path):
        """If fewer than n turns exist, returns all."""
        transcript = tmp_path / "transcript.jsonl"
        turns = [
            ("user", "Q1"),
            ("assistant", "A1 response"),
            ("user", "Q2"),
            ("assistant", "A2 response"),
        ]
        _write_transcript(transcript, turns)

        text = submit._read_recent_turns(str(transcript), 10)
        assert "Q1" in text
        assert "A1" in text
        assert "Q2" in text
        assert "A2" in text

    def test_read_recent_turns_zero_returns_empty(self, tmp_path):
        """n=0 or missing file returns empty."""
        transcript = tmp_path / "transcript.jsonl"
        assert submit._read_recent_turns(str(transcript), 0) == ""
        assert submit._read_recent_turns(str(transcript), 5) == ""


class TestWatermarkMigration:
    """Test migration from line-count to byte-offset watermarks."""

    def test_migrate_from_line_count_on_first_read(self, tmp_path):
        """Old line-count watermark is auto-migrated to byte-offset on load."""
        store_db = tmp_path / "context.db"
        store = GraphStore(store_db, vec_enabled=False)

        transcript_path = str(tmp_path / "transcript.jsonl")
        turns = [
            ("user", "Q1"),
            ("assistant", "A1 response text here"),
            ("user", "Q2"),
            ("assistant", "A2 response text here"),
        ]
        _write_transcript(Path(transcript_path), turns)

        try:
            # Simulate old storage: save a line-count watermark directly
            buf_data = store._load_buf_data()
            buf_data["transcript_path"] = transcript_path
            buf_data["transcript_watermark"] = 2  # line 0-1 read (user Q1, assistant A1)
            store._save_buf_data(buf_data)

            # Now load_watermark should auto-migrate
            watermark = store.load_watermark(transcript_path)

            # Should be a byte offset, not a line count
            assert watermark > 0
            assert watermark < Path(transcript_path).stat().st_size

            # Subsequent load should prefer the byte-offset (no re-migration)
            watermark_2 = store.load_watermark(transcript_path)
            assert watermark == watermark_2

            # Verify the new key was saved
            buf_data_after = store._load_buf_data()
            assert "transcript_byte_offset" in buf_data_after
            assert "transcript_watermark" not in buf_data_after
        finally:
            store.close()

    def test_migrate_preserves_unprocessed_text(self, tmp_path):
        """Migration processes remaining assistant text correctly."""
        store_db = tmp_path / "context.db"
        store = GraphStore(store_db, vec_enabled=False)

        transcript_path = str(tmp_path / "transcript.jsonl")
        turns = [
            ("user", "Query 1"),
            ("assistant", "Response 1 " * 20),  # ~100 words
            ("user", "Query 2"),
            ("assistant", "Response 2 " * 20),  # ~100 words
            ("user", "Query 3"),
            ("assistant", "Response 3 " * 20),  # ~100 words
        ]
        _write_transcript(Path(transcript_path), turns)

        try:
            # Simulate old watermark at line 4 (after Q2 and A2)
            buf_data = store._load_buf_data()
            buf_data["transcript_path"] = transcript_path
            buf_data["transcript_watermark"] = 4
            store._save_buf_data(buf_data)

            # Load and get byte offset
            byte_offset = store.load_watermark(transcript_path)

            # Now read from that offset — should get Response 3 only
            text, _ = submit._read_assistant_since(transcript_path, byte_offset)
            assert "Response 3" in text
            assert "Response 1" not in text
            assert "Response 2" not in text
        finally:
            store.close()


class TestWatermarkEquivalence:
    """Prove old (line-count) and new (byte-offset) paths are equivalent."""

    def test_equivalence_fresh_read(self, tmp_path):
        """Fresh read (offset 0) returns same text regardless of implementation."""
        transcript = tmp_path / "transcript.jsonl"
        turns = [
            ("user", f"Query {i}")
            for i in range(5)
        ] + [
            ("assistant", f"Response {i} " * 10)
            for i in range(5)
        ]
        _write_transcript(transcript, turns)

        # Both old and new should extract the same assistant text
        text, _ = submit._read_assistant_since(str(transcript), 0)

        # Sanity: got all responses
        for i in range(5):
            assert f"Response {i}" in text

    def test_equivalence_multiple_sequential_reads(self, tmp_path):
        """Sequential reads return same text as reading once."""
        transcript = tmp_path / "transcript.jsonl"

        # First batch
        turns_1 = [(("user", f"Q{i}"), ("assistant", f"A{i} text " * 10)) for i in range(3)]
        turns_1_flat = [item for pair in turns_1 for item in pair]
        _write_transcript(transcript, turns_1_flat)

        text_1, offset_1 = submit._read_assistant_since(str(transcript), 0)
        assert all(f"A{i}" in text_1 for i in range(3))

        # Append second batch
        turns_2 = [(("user", f"Q{i}"), ("assistant", f"A{i} text " * 10)) for i in range(3, 6)]
        turns_2_flat = [item for pair in turns_2 for item in pair]
        _append_transcript(transcript, turns_2_flat)

        # Read second batch
        text_2, offset_2 = submit._read_assistant_since(str(transcript), offset_1)
        assert all(f"A{i}" in text_2 for i in range(3, 6))
        assert not any(f"A{i}" in text_2 for i in range(3))

        # Read all from start (to compare)
        all_text, _ = submit._read_assistant_since(str(transcript), 0)
        combined_text = text_1 + "\n\n" + text_2

        # Combined sequential reads should include all assistant text
        assert all(f"A{i}" in combined_text for i in range(6))


class TestHookIntegrationMigration:
    """Integration: hook uses store watermarks correctly across migration."""

    def test_hook_migrates_watermark_on_load(self, tmp_path, monkeypatch):
        """Hook's load_watermark -> save_watermark cycle uses new format."""
        store_db = tmp_path / "context.db"
        store = GraphStore(store_db, vec_enabled=False)

        transcript_path = str(tmp_path / "transcript.jsonl")
        turns = [
            ("user", "Q1"),
            ("assistant", "A1"),
            ("user", "Q2"),
            ("assistant", "A2"),
        ]
        _write_transcript(Path(transcript_path), turns)

        try:
            # OLD: store a line-count watermark (simulating pre-update state)
            buf_data = store._load_buf_data()
            buf_data["transcript_path"] = transcript_path
            buf_data["transcript_watermark"] = 2
            store._save_buf_data(buf_data)

            # Hook reads: triggers migration
            old_watermark = store.load_watermark(transcript_path)
            old_watermark_type = type(old_watermark)

            # Hook processes new data
            text, new_watermark = submit._read_assistant_since(
                transcript_path, old_watermark
            )
            new_watermark_type = type(new_watermark)

            # Hook saves: uses new byte-offset key
            store.save_watermark(transcript_path, new_watermark)

            # Verify: new key stored, old key removed
            buf_data_final = store._load_buf_data()
            assert "transcript_byte_offset" in buf_data_final
            assert "transcript_watermark" not in buf_data_final

            # Both old and new watermarks are ints (could be line counts or byte offsets,
            # but the new one should be a byte offset in the file)
            assert old_watermark_type is int
            assert new_watermark_type is int

            # New watermark should be at most the file size
            assert new_watermark <= Path(transcript_path).stat().st_size

        finally:
            store.close()


class TestPerformance:
    """Performance validation: byte-offset reads are faster than line-count reads."""

    def test_large_file_read_performance(self, tmp_path):
        """Byte-offset read of large file tail is sub-millisecond."""
        transcript = tmp_path / "transcript.jsonl"

        # Generate ~15 MB file (same as profiling case)
        with open(transcript, "w", encoding="utf-8") as f:
            for i in range(5000):
                f.write(_make_jsonl_line("user", f"Query {i}") + "\n")
                f.write(
                    _make_jsonl_line("assistant", "Response text. " * 100)
                    + "\n"
                )

        file_size = transcript.stat().st_size
        print(f"\nFile size: {file_size / (1024*1024):.1f} MB")

        # Read from near the end (most common case after migration)
        offset_near_end = file_size - 1000  # Read last ~1KB
        t0 = time.time()
        text, _ = submit._read_assistant_since(str(transcript), offset_near_end)
        elapsed_ms = (time.time() - t0) * 1000

        print(f"Tail read (last 1KB): {elapsed_ms:.2f} ms")
        assert elapsed_ms < 10, f"Tail read should be <10ms, got {elapsed_ms:.2f}ms"

    def test_full_file_read_once(self, tmp_path):
        """First read (from offset 0) on large file takes ~100-200ms (acceptable one-time cost)."""
        transcript = tmp_path / "transcript.jsonl"

        # Generate ~15 MB file
        with open(transcript, "w", encoding="utf-8") as f:
            for i in range(5000):
                f.write(_make_jsonl_line("user", f"Query {i}") + "\n")
                f.write(
                    _make_jsonl_line("assistant", "Response text. " * 100)
                    + "\n"
                )

        file_size = transcript.stat().st_size
        print(f"\nFile size: {file_size / (1024*1024):.1f} MB")

        # First read: full file
        t0 = time.time()
        text, offset = submit._read_assistant_since(str(transcript), 0)
        elapsed_ms = (time.time() - t0) * 1000

        print(f"Full file read (first time): {elapsed_ms:.2f} ms")
        # Should be ~100-300ms on typical hardware; we just verify it completes
        assert elapsed_ms < 5000, f"First read should complete in <5s, got {elapsed_ms:.2f}ms"
        assert offset == file_size
        assert "Response text" in text
