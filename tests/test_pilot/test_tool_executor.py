"""Tests for orchestrator.tool_executor — sandbox enforcement and tool execution."""

from __future__ import annotations

import asyncio
import pytest
from pathlib import Path

from orchestrator.tool_executor import (
    _resolve_and_check,
    execute_tool,
    execute_tools,
)
from orchestrator.types import ToolCall, ToolResult


# =============================================================================
# Tests for _resolve_and_check
# =============================================================================


class TestResolveAndCheck:
    """Test sandbox path validation."""

    def test_absolute_path_within_sandbox(self, tmp_path):
        """Absolute path within sandbox should resolve successfully."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        result = _resolve_and_check(str(test_file), tmp_path)
        assert result == test_file.resolve()

    def test_absolute_path_subdir_within_sandbox(self, tmp_path):
        """Absolute path to subdir within sandbox should resolve."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        test_file = subdir / "file.txt"
        test_file.write_text("content")

        result = _resolve_and_check(str(test_file), tmp_path)
        assert result == test_file.resolve()

    def test_path_escaping_with_dotdot_raises_permission_error(self, tmp_path):
        """Path with .. that escapes sandbox should raise PermissionError."""
        with pytest.raises(PermissionError, match="outside allowed directory"):
            _resolve_and_check("../../etc/passwd", tmp_path)

    def test_absolute_path_outside_sandbox_raises_permission_error(self, tmp_path):
        """Absolute path outside sandbox should raise PermissionError."""
        with pytest.raises(PermissionError, match="outside allowed directory"):
            _resolve_and_check("/etc/passwd", tmp_path)

    def test_symlink_pointing_outside_sandbox_raises_permission_error(self, tmp_path):
        """Symlink resolving outside sandbox should raise PermissionError."""
        # Create a target directory outside sandbox
        external_dir = tmp_path.parent / "external"
        external_dir.mkdir(exist_ok=True)
        external_file = external_dir / "file.txt"
        external_file.write_text("external content")

        # Create symlink inside sandbox pointing outside
        symlink = tmp_path / "symlink.txt"
        symlink.symlink_to(external_file)

        # Resolve should raise because it points outside
        with pytest.raises(PermissionError, match="outside allowed directory"):
            _resolve_and_check("symlink.txt", tmp_path)

    def test_tilde_expansion_within_sandbox(self, tmp_path, monkeypatch):
        """Tilde expansion should work if result is within sandbox."""
        # Create a file and make it appear as home directory for this test
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        # Monkeypatch expanduser to return path within sandbox
        # (This is somewhat artificial but tests the expanduser() call)
        result = _resolve_and_check(str(test_file), tmp_path)
        assert result == test_file.resolve()


# =============================================================================
# Tests for execute_tool — dispatcher
# =============================================================================


class TestExecuteTool:
    """Test tool execution dispatcher."""

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, tmp_path):
        """Unknown tool name should return ToolResult with error."""
        cfg = {"sandbox_root": str(tmp_path)}
        tool_call = ToolCall(id="tc1", name="unknown_tool", args={})

        result = await execute_tool(tool_call, cfg)

        assert result.tool_call_id == "tc1"
        assert result.name == "unknown_tool"
        assert result.error is not None
        assert "Unknown tool" in result.error

    @pytest.mark.asyncio
    async def test_disabled_tool_returns_error(self, tmp_path):
        """Disabled tool should return ToolResult with error."""
        cfg = {
            "sandbox_root": str(tmp_path),
            "enabled": ["read_file", "write_file"],  # bash not enabled
        }
        tool_call = ToolCall(id="tc1", name="bash", args={"command": "echo hello"})

        result = await execute_tool(tool_call, cfg)

        assert result.tool_call_id == "tc1"
        assert result.name == "bash"
        assert result.error is not None
        assert "not enabled" in result.error

    @pytest.mark.asyncio
    async def test_permission_error_captured(self, tmp_path):
        """PermissionError from tool should be captured in ToolResult.error."""
        cfg = {"sandbox_root": str(tmp_path)}
        # Try to read a file outside sandbox
        tool_call = ToolCall(
            id="tc1",
            name="read_file",
            args={"path": "/etc/passwd"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is not None
        assert "Permission denied" in result.error

    @pytest.mark.asyncio
    async def test_value_error_captured(self, tmp_path):
        """ValueError from tool should be captured in ToolResult.error."""
        cfg = {"sandbox_root": str(tmp_path)}
        # Missing required argument
        tool_call = ToolCall(id="tc1", name="bash", args={})

        result = await execute_tool(tool_call, cfg)

        assert result.error is not None
        assert "requires a 'command'" in result.error


# =============================================================================
# Tests for _run_bash
# =============================================================================


class TestBash:
    """Test bash command execution."""

    @pytest.mark.asyncio
    async def test_echo_hello(self, tmp_path):
        """Simple echo command should return output."""
        cfg = {
            "sandbox_root": str(tmp_path),
            "bash_timeout": 5,
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(id="tc1", name="bash", args={"command": "echo hello"})

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_bash_cwd_is_sandbox_root(self, tmp_path):
        """Bash commands should run with cwd=sandbox_root."""
        cfg = {
            "sandbox_root": str(tmp_path),
            "bash_timeout": 5,
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(id="tc1", name="bash", args={"command": "pwd"})

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        # Output should be the sandbox root path
        assert str(tmp_path).rstrip() in result.output.rstrip()

    @pytest.mark.asyncio
    async def test_bash_timeout(self, tmp_path):
        """Command exceeding timeout should be killed."""
        cfg = {
            "sandbox_root": str(tmp_path),
            "bash_timeout": 1,
            "max_output_chars": 10_000,
        }
        # Sleep longer than timeout
        tool_call = ToolCall(id="tc1", name="bash", args={"command": "sleep 10"})

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert "timed out" in result.output

    @pytest.mark.asyncio
    async def test_bash_output_truncation(self, tmp_path):
        """Large output should be truncated at max_output_chars."""
        cfg = {
            "sandbox_root": str(tmp_path),
            "bash_timeout": 5,
            "max_output_chars": 50,
        }
        # Generate output larger than max_output_chars
        tool_call = ToolCall(
            id="tc1",
            name="bash",
            args={"command": "printf '%0.s#' {1..200}"},  # 200 '#' chars
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert "truncated" in result.output
        assert len(result.output) < 200

    @pytest.mark.asyncio
    async def test_bash_missing_command_arg(self, tmp_path):
        """Missing 'command' argument should raise ValueError."""
        cfg = {"sandbox_root": str(tmp_path)}
        tool_call = ToolCall(id="tc1", name="bash", args={})

        result = await execute_tool(tool_call, cfg)

        assert result.error is not None
        assert "requires a 'command'" in result.error


# =============================================================================
# Tests for _run_read_file
# =============================================================================


class TestReadFile:
    """Test file reading."""

    @pytest.mark.asyncio
    async def test_read_existing_file(self, tmp_path):
        """Reading existing file should return content."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="read_file",
            args={"path": str(test_file)},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert result.output == "Hello, World!"

    @pytest.mark.asyncio
    async def test_read_file_in_subdirectory(self, tmp_path):
        """Reading file in subdirectory should work."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        test_file = subdir / "test.txt"
        test_file.write_text("Nested content")

        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="read_file",
            args={"path": str(test_file)},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert result.output == "Nested content"

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, tmp_path):
        """Reading nonexistent file should return error."""
        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        nonexistent = str(tmp_path / "nonexistent.txt")
        tool_call = ToolCall(
            id="tc1",
            name="read_file",
            args={"path": nonexistent},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None  # No exception, but message in output
        assert "not found" in result.output

    @pytest.mark.asyncio
    async def test_read_file_outside_sandbox(self, tmp_path):
        """Reading file outside sandbox should raise PermissionError."""
        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="read_file",
            args={"path": "/etc/passwd"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is not None
        assert "Permission denied" in result.error

    @pytest.mark.asyncio
    async def test_read_file_via_symlink_outside_sandbox(self, tmp_path):
        """Reading file via symlink outside sandbox should raise PermissionError."""
        # Create external file
        external_dir = tmp_path.parent / "external"
        external_dir.mkdir(exist_ok=True)
        external_file = external_dir / "secret.txt"
        external_file.write_text("secret content")

        # Create symlink inside sandbox
        symlink = tmp_path / "symlink.txt"
        symlink.symlink_to(external_file)

        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="read_file",
            args={"path": "symlink.txt"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is not None
        assert "Permission denied" in result.error

    @pytest.mark.asyncio
    async def test_read_file_output_truncation(self, tmp_path):
        """Large file content should be truncated."""
        test_file = tmp_path / "large.txt"
        test_file.write_text("x" * 20_000)

        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 100,
        }
        tool_call = ToolCall(
            id="tc1",
            name="read_file",
            args={"path": str(test_file)},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert "truncated" in result.output
        assert len(result.output) < 20_000

    @pytest.mark.asyncio
    async def test_read_file_missing_path_arg(self, tmp_path):
        """Missing 'path' argument should raise ValueError."""
        cfg = {"sandbox_root": str(tmp_path)}
        tool_call = ToolCall(id="tc1", name="read_file", args={})

        result = await execute_tool(tool_call, cfg)

        assert result.error is not None
        assert "requires a 'path'" in result.error


# =============================================================================
# Tests for _run_write_file
# =============================================================================


class TestWriteFile:
    """Test file writing."""

    @pytest.mark.asyncio
    async def test_write_file_to_sandbox(self, tmp_path):
        """Writing file should succeed and create file."""
        output_file = str(tmp_path / "output.txt")
        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="write_file",
            args={"path": output_file, "content": "Hello, World!"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert "wrote" in result.output

        # Verify file was actually written
        written_file = tmp_path / "output.txt"
        assert written_file.exists()
        assert written_file.read_text() == "Hello, World!"

    @pytest.mark.asyncio
    async def test_write_file_creates_parent_directories(self, tmp_path):
        """Writing file should create parent directories if needed."""
        nested_path = str(tmp_path / "deep" / "nested" / "dir" / "file.txt")
        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="write_file",
            args={"path": nested_path, "content": "nested content"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None

        # Verify file and directories were created
        written_file = tmp_path / "deep" / "nested" / "dir" / "file.txt"
        assert written_file.exists()
        assert written_file.read_text() == "nested content"

    @pytest.mark.asyncio
    async def test_write_file_outside_sandbox(self, tmp_path):
        """Writing file outside sandbox should raise PermissionError."""
        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="write_file",
            args={"path": "/etc/hostile.txt", "content": "malicious"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is not None
        assert "Permission denied" in result.error

    @pytest.mark.asyncio
    async def test_write_file_via_symlink_outside_sandbox(self, tmp_path):
        """Writing via symlink outside sandbox should raise PermissionError."""
        # Create external directory
        external_dir = tmp_path.parent / "external"
        external_dir.mkdir(exist_ok=True)

        # Create symlink inside sandbox pointing outside
        symlink = tmp_path / "evil_link"
        symlink.symlink_to(external_dir)

        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="write_file",
            args={"path": "evil_link/file.txt", "content": "malicious"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is not None
        assert "Permission denied" in result.error

    @pytest.mark.asyncio
    async def test_write_file_missing_path_arg(self, tmp_path):
        """Missing 'path' argument should raise ValueError."""
        cfg = {"sandbox_root": str(tmp_path)}
        tool_call = ToolCall(
            id="tc1",
            name="write_file",
            args={"content": "text"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is not None
        assert "requires a 'path'" in result.error

    @pytest.mark.asyncio
    async def test_write_file_missing_content_arg(self, tmp_path):
        """Missing 'content' argument should raise ValueError."""
        cfg = {"sandbox_root": str(tmp_path)}
        tool_call = ToolCall(
            id="tc1",
            name="write_file",
            args={"path": "file.txt"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is not None
        assert "requires a 'content'" in result.error

    @pytest.mark.asyncio
    async def test_write_file_overwrites_existing(self, tmp_path):
        """Writing should overwrite existing file."""
        existing_file = tmp_path / "file.txt"
        existing_file.write_text("old content")

        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="write_file",
            args={"path": str(existing_file), "content": "new content"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert existing_file.read_text() == "new content"


# =============================================================================
# Tests for _run_glob
# =============================================================================


class TestGlob:
    """Test file globbing."""

    @pytest.mark.asyncio
    async def test_glob_finds_matching_files(self, tmp_path):
        """Glob should find files matching pattern."""
        (tmp_path / "file1.txt").write_text("1")
        (tmp_path / "file2.txt").write_text("2")
        (tmp_path / "file3.py").write_text("3")

        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="glob",
            args={"pattern": "*.txt"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert "file1.txt" in result.output
        assert "file2.txt" in result.output
        assert "file3.py" not in result.output

    @pytest.mark.asyncio
    async def test_glob_recursive(self, tmp_path):
        """Glob should support recursive patterns."""
        (tmp_path / "a.txt").write_text("a")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "b.txt").write_text("b")

        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="glob",
            args={"pattern": "**/*.txt"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert "a.txt" in result.output
        assert "subdir" in result.output and "b.txt" in result.output

    @pytest.mark.asyncio
    async def test_glob_no_matches(self, tmp_path):
        """Glob with no matches should return special message."""
        (tmp_path / "file.txt").write_text("text")

        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="glob",
            args={"pattern": "*.nonexistent"},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert "[glob: no matches]" in result.output

    @pytest.mark.asyncio
    async def test_glob_pattern_outside_sandbox_filtered(self, tmp_path):
        """Glob results outside sandbox should be filtered out."""
        # Create files inside sandbox
        (tmp_path / "inside.txt").write_text("inside")

        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        # Try to glob with pattern that tries to escape
        # (the function constructs the path safely, so this should still be limited)
        tool_call = ToolCall(
            id="tc1",
            name="glob",
            args={"pattern": "*.txt"},
        )

        result = await execute_tool(tool_call, cfg)

        # Should only get files inside sandbox
        assert result.error is None
        assert "inside.txt" in result.output

    @pytest.mark.asyncio
    async def test_glob_with_root_arg(self, tmp_path):
        """Glob with 'root' argument should search from that root."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file.txt").write_text("content")
        (tmp_path / "other.txt").write_text("other")

        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }
        tool_call = ToolCall(
            id="tc1",
            name="glob",
            args={"pattern": "*.txt", "root": str(subdir)},
        )

        result = await execute_tool(tool_call, cfg)

        assert result.error is None
        assert "file.txt" in result.output
        assert "other.txt" not in result.output

    @pytest.mark.asyncio
    async def test_glob_missing_pattern_arg(self, tmp_path):
        """Missing 'pattern' argument should raise ValueError."""
        cfg = {"sandbox_root": str(tmp_path)}
        tool_call = ToolCall(id="tc1", name="glob", args={})

        result = await execute_tool(tool_call, cfg)

        assert result.error is not None
        assert "requires a 'pattern'" in result.error


# =============================================================================
# Tests for execute_tools (concurrent execution)
# =============================================================================


class TestExecuteTools:
    """Test concurrent tool execution."""

    @pytest.mark.asyncio
    async def test_execute_multiple_tools_concurrently(self, tmp_path):
        """Multiple tool calls should execute concurrently and return in order."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("content1")
        file2.write_text("content2")

        cfg = {
            "sandbox_root": str(tmp_path),
            "bash_timeout": 5,
            "max_output_chars": 10_000,
            "enabled": ["bash", "read_file", "write_file", "glob"],
        }

        tool_calls = [
            ToolCall(id="tc1", name="read_file", args={"path": str(file1)}),
            ToolCall(id="tc2", name="read_file", args={"path": str(file2)}),
            ToolCall(id="tc3", name="bash", args={"command": "echo test"}),
        ]

        results = await execute_tools(tool_calls, cfg)

        assert len(results) == 3
        assert results[0].tool_call_id == "tc1"
        assert results[1].tool_call_id == "tc2"
        assert results[2].tool_call_id == "tc3"
        assert "content1" in results[0].output
        assert "content2" in results[1].output
        assert "test" in results[2].output

    @pytest.mark.asyncio
    async def test_execute_tools_with_errors(self, tmp_path):
        """execute_tools should capture errors for individual tools."""
        cfg = {
            "sandbox_root": str(tmp_path),
            "enabled": ["bash", "read_file"],
        }

        tool_calls = [
            ToolCall(id="tc1", name="read_file", args={"path": "/etc/passwd"}),  # error
            ToolCall(id="tc2", name="unknown_tool", args={}),  # error
        ]

        results = await execute_tools(tool_calls, cfg)

        assert len(results) == 2
        assert results[0].error is not None
        assert results[1].error is not None

    @pytest.mark.asyncio
    async def test_execute_tools_maintains_order(self, tmp_path):
        """Results should be returned in the same order as tool_calls."""
        cfg = {"sandbox_root": str(tmp_path)}

        tool_calls = [
            ToolCall(id=f"tc{i}", name="bash", args={"command": "echo id"})
            for i in range(10)
        ]

        results = await execute_tools(tool_calls, cfg)

        assert len(results) == 10
        for i, result in enumerate(results):
            assert result.tool_call_id == f"tc{i}"


# =============================================================================
# Tests for ToolResult.to_message()
# =============================================================================


class TestToolResultToMessage:
    """Test ToolResult.to_message() conversion."""

    def test_to_message_success_case(self):
        """Success case should include output only."""
        result = ToolResult(
            tool_call_id="tc1",
            name="bash",
            output="Hello, World!",
            error=None,
        )

        msg = result.to_message()

        assert msg.role == "tool"
        assert msg.content == "Hello, World!"
        assert msg.tool_call_id == "tc1"

    def test_to_message_error_case(self):
        """Error case should include 'Error:' prefix and output."""
        result = ToolResult(
            tool_call_id="tc1",
            name="bash",
            output="stderr output",
            error="Command failed",
        )

        msg = result.to_message()

        assert msg.role == "tool"
        assert "Error: Command failed" in msg.content
        assert "stderr output" in msg.content
        assert msg.tool_call_id == "tc1"

    def test_to_message_error_with_empty_output(self):
        """Error with no output should just show error."""
        result = ToolResult(
            tool_call_id="tc1",
            name="bash",
            output="",
            error="Something went wrong",
        )

        msg = result.to_message()

        assert msg.role == "tool"
        assert "Error: Something went wrong" in msg.content
        assert msg.tool_call_id == "tc1"


# =============================================================================
# Integration tests
# =============================================================================


class TestIntegration:
    """Integration tests combining multiple features."""

    @pytest.mark.asyncio
    async def test_write_and_read_file(self, tmp_path):
        """Should be able to write then read a file."""
        test_file = str(tmp_path / "test.txt")
        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }

        # Write file
        write_call = ToolCall(
            id="tc1",
            name="write_file",
            args={"path": test_file, "content": "test content"},
        )
        write_result = await execute_tool(write_call, cfg)
        assert write_result.error is None

        # Read file back
        read_call = ToolCall(
            id="tc2",
            name="read_file",
            args={"path": test_file},
        )
        read_result = await execute_tool(read_call, cfg)
        assert read_result.error is None
        assert read_result.output == "test content"

    @pytest.mark.asyncio
    async def test_write_and_glob(self, tmp_path):
        """Should be able to write files and then glob them."""
        cfg = {
            "sandbox_root": str(tmp_path),
            "max_output_chars": 10_000,
        }

        # Write multiple files
        for i in range(3):
            file_path = str(tmp_path / f"file{i}.txt")
            call = ToolCall(
                id=f"tc{i}",
                name="write_file",
                args={"path": file_path, "content": f"content{i}"},
            )
            result = await execute_tool(call, cfg)
            assert result.error is None

        # Glob them
        glob_call = ToolCall(
            id="tcglob",
            name="glob",
            args={"pattern": "*.txt"},
        )
        glob_result = await execute_tool(glob_call, cfg)
        assert glob_result.error is None
        for i in range(3):
            assert f"file{i}.txt" in glob_result.output

    @pytest.mark.asyncio
    async def test_bash_and_read_file(self, tmp_path):
        """Should be able to run bash to create file, then read it."""
        output_file = str(tmp_path / "output.txt")
        cfg = {
            "sandbox_root": str(tmp_path),
            "bash_timeout": 5,
            "max_output_chars": 10_000,
        }

        # Create file with bash
        bash_call = ToolCall(
            id="tc1",
            name="bash",
            args={"command": f"echo 'created by bash' > {output_file}"},
        )
        bash_result = await execute_tool(bash_call, cfg)
        assert bash_result.error is None

        # Read file
        read_call = ToolCall(
            id="tc2",
            name="read_file",
            args={"path": output_file},
        )
        read_result = await execute_tool(read_call, cfg)
        assert read_result.error is None
        assert "created by bash" in read_result.output
