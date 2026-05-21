"""Tests for pilot/cli.py."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from pilot.cli import main, _setup_logging, _load_cfg, _repl, _headless


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def cli_runner():
    """Return a Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_config():
    """Return a minimal config dict."""
    return {
        "pilot": {
            "context": {},
            "system_prompt": {},
            "llm": {},
            "tools": {"enabled": []},
            "retry": {},
        },
    }


@pytest.fixture
def mock_db_path(tmp_path):
    """Return a temporary database path."""
    db_file = tmp_path / "test.db"
    db_file.touch()
    return db_file


# ===========================================================================
# Tests: _setup_logging()
# ===========================================================================


def test_setup_logging_verbose():
    """Test _setup_logging() sets DEBUG level when verbose=True."""
    import logging

    with patch("logging.basicConfig") as mock_basic:
        _setup_logging(verbose=True)
        mock_basic.assert_called_once()
        call_kwargs = mock_basic.call_args[1]
        assert call_kwargs["level"] == logging.DEBUG


def test_setup_logging_non_verbose():
    """Test _setup_logging() sets WARNING level when verbose=False."""
    import logging

    with patch("logging.basicConfig") as mock_basic:
        _setup_logging(verbose=False)
        mock_basic.assert_called_once()
        call_kwargs = mock_basic.call_args[1]
        assert call_kwargs["level"] == logging.WARNING


# ===========================================================================
# Tests: _load_cfg()
# ===========================================================================


def test_load_cfg_success():
    """Test _load_cfg() loads config successfully."""
    with patch("pilot.cli.load_config") as mock_load:
        mock_load.return_value = {"test": "config"}

        result = _load_cfg(None)

        assert result == {"test": "config"}
        mock_load.assert_called_once_with(None)


def test_load_cfg_file_not_found(cli_runner):
    """Test _load_cfg() exits with error when config not found."""
    with patch("pilot.cli.load_config") as mock_load:
        mock_load.side_effect = FileNotFoundError("config.yaml not found")

        with pytest.raises(SystemExit) as exc_info:
            _load_cfg(None)

        assert exc_info.value.code == 1


# ===========================================================================
# Tests: REPL functionality
# ===========================================================================


@pytest.mark.asyncio
async def test_repl_quit_command():
    """Test /quit command exits the REPL."""
    conversation = AsyncMock()

    with patch("pilot.cli.click.prompt") as mock_prompt:
        mock_prompt.side_effect = ["/quit"]
        with patch("pilot.cli.click.echo"):
            await _repl(conversation, stream=False)

    # Verify prompt was called (REPL started)
    mock_prompt.assert_called()


@pytest.mark.asyncio
async def test_repl_exit_command():
    """Test /exit command also exits the REPL."""
    conversation = AsyncMock()

    with patch("pilot.cli.click.prompt") as mock_prompt:
        mock_prompt.side_effect = ["/exit"]
        with patch("pilot.cli.click.echo"):
            await _repl(conversation, stream=False)

    mock_prompt.assert_called()


@pytest.mark.asyncio
async def test_repl_q_command():
    """Test /q command (short form) exits the REPL."""
    conversation = AsyncMock()

    with patch("pilot.cli.click.prompt") as mock_prompt:
        mock_prompt.side_effect = ["/q"]
        with patch("pilot.cli.click.echo"):
            await _repl(conversation, stream=False)

    mock_prompt.assert_called()


@pytest.mark.asyncio
async def test_repl_reset_command():
    """Test /reset command clears history."""
    conversation = MagicMock()
    conversation.reset = MagicMock()

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo"):
        mock_prompt.side_effect = ["/reset", "/quit"]
        await _repl(conversation, stream=False)

    conversation.reset.assert_called_once()


@pytest.mark.asyncio
async def test_repl_stats_command():
    """Test /stats command prints stats."""
    conversation = MagicMock()
    conversation.stats = MagicMock(return_value={
        "history_depth": 5,
        "total_tokens": 500,
    })

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo") as mock_echo:
        mock_prompt.side_effect = ["/stats", "/quit"]
        await _repl(conversation, stream=False)

    conversation.stats.assert_called_once()
    # Verify echo was called with stats
    echo_calls = [call for call in mock_echo.call_args_list]
    assert len(echo_calls) > 0


@pytest.mark.asyncio
async def test_repl_help_command():
    """Test /help command prints help text."""
    conversation = MagicMock()

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo") as mock_echo:
        mock_prompt.side_effect = ["/help", "/quit"]
        await _repl(conversation, stream=False)

    # Verify echo was called with help text
    help_calls = [
        call for call in mock_echo.call_args_list
        if call[0] and "Commands:" in str(call[0][0])
    ]
    assert len(help_calls) > 0


@pytest.mark.asyncio
async def test_repl_unknown_command():
    """Test unknown /command prints error message."""
    conversation = MagicMock()

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo") as mock_echo:
        mock_prompt.side_effect = ["/unknown", "/quit"]
        await _repl(conversation, stream=False)

    # Verify echo was called with unknown command message
    unknown_calls = [
        call for call in mock_echo.call_args_list
        if call[0] and "Unknown command:" in str(call[0][0])
    ]
    assert len(unknown_calls) > 0


@pytest.mark.asyncio
async def test_repl_empty_input_skipped():
    """Test empty input is skipped (no LLM call)."""
    conversation = MagicMock()
    conversation.chat = AsyncMock()

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo"):
        mock_prompt.side_effect = ["", "  ", "/quit"]
        await _repl(conversation, stream=False)

    # chat() should never be called for empty inputs
    conversation.chat.assert_not_called()


@pytest.mark.asyncio
async def test_repl_normal_message_calls_chat():
    """Test normal message calls conversation.chat()."""
    conversation = MagicMock()
    conversation.chat = AsyncMock(return_value="Assistant response")

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo"):
        mock_prompt.side_effect = ["Hello", "/quit"]
        await _repl(conversation, stream=False)

    conversation.chat.assert_called_once_with("Hello")


@pytest.mark.asyncio
async def test_repl_stream_true_uses_chat_stream():
    """Test --stream flag uses chat_stream()."""
    conversation = MagicMock()

    async def mock_stream(msg):
        yield "chunk1"
        yield "chunk2"

    conversation.chat_stream = MagicMock(side_effect=mock_stream)

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo"):
        mock_prompt.side_effect = ["Hello", "/quit"]
        await _repl(conversation, stream=True)

    conversation.chat_stream.assert_called_once_with("Hello")


@pytest.mark.asyncio
async def test_repl_stream_false_uses_chat():
    """Test --no-stream flag uses chat()."""
    conversation = MagicMock()
    conversation.chat = AsyncMock(return_value="Full response")
    conversation.chat_stream = AsyncMock()

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo"):
        mock_prompt.side_effect = ["Hello", "/quit"]
        await _repl(conversation, stream=False)

    conversation.chat.assert_called_once_with("Hello")
    conversation.chat_stream.assert_not_called()


@pytest.mark.asyncio
async def test_repl_exception_during_chat_prints_error():
    """Test exception during chat is caught and error is printed."""
    conversation = MagicMock()
    conversation.chat = AsyncMock(side_effect=RuntimeError("LLM error"))

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo") as mock_echo:
        mock_prompt.side_effect = ["Test", "/quit"]
        await _repl(conversation, stream=False)

    # Verify error message was printed
    error_calls = [
        call for call in mock_echo.call_args_list
        if call[0] and "[Error:" in str(call[0][0])
    ]
    assert len(error_calls) > 0


@pytest.mark.asyncio
async def test_repl_eof_exits():
    """Test EOF (Ctrl-D) exits the REPL gracefully."""
    conversation = MagicMock()

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo"):
        mock_prompt.side_effect = EOFError()
        await _repl(conversation, stream=False)

    # Should exit without error
    mock_prompt.assert_called()


@pytest.mark.asyncio
async def test_repl_keyboard_interrupt_exits():
    """Test KeyboardInterrupt (Ctrl-C) exits the REPL gracefully."""
    conversation = MagicMock()

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo"):
        mock_prompt.side_effect = KeyboardInterrupt()
        await _repl(conversation, stream=False)

    mock_prompt.assert_called()


@pytest.mark.asyncio
async def test_repl_stats_formats_floats():
    """Test /stats command formats float values with 1 decimal place."""
    conversation = MagicMock()
    conversation.stats = MagicMock(return_value={
        "total_tokens": 500,
        "idle_seconds": 123.456,
    })

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo") as mock_echo:
        mock_prompt.side_effect = ["/stats", "/quit"]
        await _repl(conversation, stream=False)

    # Verify float values were formatted
    echo_calls = [str(call[0][0]) if call[0] else "" for call in mock_echo.call_args_list]
    echo_str = "\n".join(echo_calls)
    # Should contain formatted float or idle_seconds reference
    assert "123.5" in echo_str or "123.456" in echo_str or "idle_seconds" in echo_str


# ===========================================================================
# Tests: Click command
# ===========================================================================


def test_main_project_not_found(cli_runner, tmp_path):
    """Test main() exits with error when project database doesn't exist."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db:

        mock_load_cfg.return_value = {}
        # Database doesn't exist
        mock_get_db.return_value = tmp_path / "nonexistent.db"

        result = cli_runner.invoke(main, ["my_project"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()


def test_main_verbose_flag_enables_debug_logging(cli_runner, mock_db_path):
    """Test -v/--verbose flag enables debug logging."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli._setup_logging") as mock_setup_log, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation"), \
         patch("pilot.cli.asyncio.run") as mock_async_run:

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=None)

        result = cli_runner.invoke(main, ["my_project", "-v"])

        mock_setup_log.assert_called_once_with(True)


def test_main_no_verbose_flag(cli_runner, mock_db_path):
    """Test without -v flag, logging is not set to debug."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli._setup_logging") as mock_setup_log, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation"), \
         patch("pilot.cli.asyncio.run") as mock_async_run:

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=None)

        result = cli_runner.invoke(main, ["my_project"])

        mock_setup_log.assert_called_once_with(False)


def test_main_stream_flag(cli_runner, mock_db_path):
    """Test --stream flag is passed to _repl."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation"), \
         patch("pilot.cli._repl") as mock_repl, \
         patch("pilot.cli.asyncio.run") as mock_async_run:

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store = MagicMock()
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=mock_store)
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=None)

        result = cli_runner.invoke(main, ["my_project", "--stream"])

        # asyncio.run should be called with _repl coroutine
        mock_async_run.assert_called_once()


def test_main_no_stream_flag(cli_runner, mock_db_path):
    """Test --no-stream flag is passed to _repl."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation"), \
         patch("pilot.cli._repl") as mock_repl, \
         patch("pilot.cli.asyncio.run") as mock_async_run:

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store = MagicMock()
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=mock_store)
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=None)

        result = cli_runner.invoke(main, ["my_project", "--no-stream"])

        mock_async_run.assert_called_once()


def test_main_config_path_option(cli_runner, mock_db_path, tmp_path):
    """Test --config option passes config file path."""
    config_file = tmp_path / "custom_config.yaml"
    config_file.write_text("test: config")

    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation"), \
         patch("pilot.cli.asyncio.run"):

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store = MagicMock()
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=mock_store)
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=None)

        result = cli_runner.invoke(
            main,
            ["my_project", "--config", str(config_file)]
        )

        mock_load_cfg.assert_called_once_with(str(config_file))


def test_main_creates_conversation(cli_runner, mock_db_path):
    """Test main() creates Conversation instance."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation") as mock_conv_cls, \
         patch("pilot.cli.asyncio.run"):

        mock_load_cfg.return_value = {"pilot": {}}
        mock_get_db.return_value = mock_db_path
        mock_store = MagicMock()
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=mock_store)
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=None)

        result = cli_runner.invoke(main, ["my_project"])

        mock_conv_cls.assert_called_once()
        # Verify Conversation was called with project name
        call_kwargs = mock_conv_cls.call_args[1]
        assert call_kwargs["project_name"] == "my_project"


def test_main_uses_graph_store_context_manager(cli_runner, mock_db_path):
    """Test main() creates GraphStore and calls close() in finally block."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation"), \
         patch("pilot.cli.asyncio.run"):

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store

        result = cli_runner.invoke(main, ["my_project"])

        # Verify GraphStore was instantiated and close() was called
        mock_store_cls.assert_called_once_with(mock_db_path)
        mock_store.close.assert_called_once()


def test_main_help_text(cli_runner):
    """Test main() command has proper help text."""
    result = cli_runner.invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "PROJECT" in result.output
    assert "pilot" in result.output.lower()


# ===========================================================================
# Tests: Integration scenarios
# ===========================================================================


@pytest.mark.asyncio
async def test_repl_multiple_commands_in_sequence():
    """Test REPL handles multiple commands in sequence."""
    conversation = MagicMock()
    conversation.stats = MagicMock(return_value={"history_depth": 2})
    conversation.chat = AsyncMock(return_value="Response")
    conversation.reset = MagicMock()

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo"):
        mock_prompt.side_effect = [
            "/help",
            "Hello",
            "/stats",
            "/reset",
            "/quit",
        ]
        await _repl(conversation, stream=False)

    conversation.chat.assert_called_once_with("Hello")
    conversation.reset.assert_called_once()
    conversation.stats.assert_called_once()


@pytest.mark.asyncio
async def test_repl_handles_chat_with_special_characters():
    """Test REPL handles user input with special characters."""
    conversation = MagicMock()
    conversation.chat = AsyncMock(return_value="Response")

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo"):
        special_msg = "Test message with @#$%^&*() characters!"
        mock_prompt.side_effect = [special_msg, "/quit"]
        await _repl(conversation, stream=False)

    conversation.chat.assert_called_once_with(special_msg)


@pytest.mark.asyncio
async def test_repl_case_insensitive_commands():
    """Test slash commands are case-insensitive."""
    conversation = MagicMock()
    conversation.reset = MagicMock()
    conversation.stats = MagicMock(return_value={})

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo"):
        mock_prompt.side_effect = ["/RESET", "/STATS", "/QUIT"]
        await _repl(conversation, stream=False)

    conversation.reset.assert_called_once()
    conversation.stats.assert_called_once()


@pytest.mark.asyncio
async def test_repl_whitespace_handling():
    """Test REPL strips whitespace from commands."""
    conversation = MagicMock()
    conversation.reset = MagicMock()

    with patch("pilot.cli.click.prompt") as mock_prompt, \
         patch("pilot.cli.click.echo"):
        mock_prompt.side_effect = ["  /reset  ", "  /quit  "]
        await _repl(conversation, stream=False)

    conversation.reset.assert_called_once()


# ===========================================================================
# Tests: headless mode (_headless + --print flag)
# ===========================================================================


@pytest.mark.asyncio
async def test_headless_calls_chat_once():
    """_headless() calls conversation.chat() exactly once with the prompt."""
    conversation = MagicMock()
    conversation.chat = AsyncMock(return_value="The answer is 42.")

    with patch("pilot.cli.click.echo") as mock_echo:
        await _headless(conversation, "what is the answer?", fmt="text")

    conversation.chat.assert_called_once_with("what is the answer?")
    mock_echo.assert_called_once_with("The answer is 42.")


@pytest.mark.asyncio
async def test_headless_text_format_prints_reply():
    """_headless() with fmt='text' prints the raw reply string."""
    conversation = MagicMock()
    conversation.chat = AsyncMock(return_value="hello world")

    with patch("pilot.cli.click.echo") as mock_echo:
        await _headless(conversation, "say hi", fmt="text")

    mock_echo.assert_called_once_with("hello world")


@pytest.mark.asyncio
async def test_headless_json_format_wraps_reply():
    """_headless() with fmt='json' prints {\"reply\": ...} JSON."""
    import json
    conversation = MagicMock()
    conversation.chat = AsyncMock(return_value="structured answer")

    with patch("pilot.cli.click.echo") as mock_echo:
        await _headless(conversation, "query", fmt="json")

    printed = mock_echo.call_args[0][0]
    parsed = json.loads(printed)
    assert parsed == {"reply": "structured answer"}


@pytest.mark.asyncio
async def test_headless_json_format_handles_special_chars():
    """_headless() JSON output correctly escapes special characters."""
    import json
    conversation = MagicMock()
    reply = 'Line 1\nLine 2 with "quotes" and \\backslash'
    conversation.chat = AsyncMock(return_value=reply)

    with patch("pilot.cli.click.echo") as mock_echo:
        await _headless(conversation, "query", fmt="json")

    printed = mock_echo.call_args[0][0]
    parsed = json.loads(printed)
    assert parsed["reply"] == reply


def test_main_print_flag_headless_mode(cli_runner, mock_db_path):
    """--print flag invokes _headless() instead of _repl()."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation") as mock_conv_cls, \
         patch("pilot.cli.asyncio.run") as mock_async_run, \
         patch("pilot.cli._headless") as mock_headless, \
         patch("pilot.cli._repl") as mock_repl:

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_conv_cls.return_value = MagicMock()

        result = cli_runner.invoke(main, ["my_project", "--print", "summarise decisions"])

        assert result.exit_code == 0
        # asyncio.run must be called (wraps _headless coroutine)
        mock_async_run.assert_called_once()
        # _repl must NOT be called
        mock_repl.assert_not_called()


def test_main_print_flag_empty_prompt_exits(cli_runner, mock_db_path):
    """--print '' (empty prompt) exits with code 1."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation") as mock_conv_cls:

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store_cls.return_value = MagicMock()
        mock_conv_cls.return_value = MagicMock()

        result = cli_runner.invoke(main, ["my_project", "--print", ""])

        assert result.exit_code == 1
        assert "empty" in result.output.lower()


def test_main_print_stdin_reads_stdin(cli_runner, mock_db_path):
    """--print - reads the prompt from stdin."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation") as mock_conv_cls, \
         patch("pilot.cli.asyncio.run") as mock_async_run:

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store_cls.return_value = MagicMock()
        mock_conv_cls.return_value = MagicMock()

        result = cli_runner.invoke(
            main,
            ["my_project", "--print", "-"],
            input="stdin prompt text\n",
        )

        assert result.exit_code == 0
        mock_async_run.assert_called_once()


def test_main_print_stdin_empty_exits(cli_runner, mock_db_path):
    """--print - with empty stdin exits with code 1."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation") as mock_conv_cls:

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store_cls.return_value = MagicMock()
        mock_conv_cls.return_value = MagicMock()

        result = cli_runner.invoke(
            main,
            ["my_project", "--print", "-"],
            input="",
        )

        assert result.exit_code == 1
        assert "empty" in result.output.lower()


def test_main_print_json_format(cli_runner, mock_db_path):
    """--print with --format json passes fmt='json' to _headless."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation") as mock_conv_cls, \
         patch("pilot.cli.asyncio.run") as mock_async_run:

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store_cls.return_value = MagicMock()
        mock_conv_cls.return_value = MagicMock()

        result = cli_runner.invoke(
            main,
            ["my_project", "--print", "list nodes", "--format", "json"],
        )

        assert result.exit_code == 0
        mock_async_run.assert_called_once()


def test_main_without_print_flag_calls_repl(cli_runner, mock_db_path):
    """Without --print, main() invokes the REPL (not _headless)."""
    with patch("pilot.cli.load_config") as mock_load_cfg, \
         patch("pilot.cli.get_db_path") as mock_get_db, \
         patch("pilot.cli.GraphStore") as mock_store_cls, \
         patch("pilot.cli.Conversation") as mock_conv_cls, \
         patch("pilot.cli.asyncio.run") as mock_async_run, \
         patch("pilot.cli._headless") as mock_headless:

        mock_load_cfg.return_value = {}
        mock_get_db.return_value = mock_db_path
        mock_store_cls.return_value = MagicMock()
        mock_conv_cls.return_value = MagicMock()

        result = cli_runner.invoke(main, ["my_project"])

        assert result.exit_code == 0
        mock_async_run.assert_called_once()
        mock_headless.assert_not_called()


def test_main_help_includes_print_option(cli_runner):
    """--help output includes --print option documentation."""
    result = cli_runner.invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "--print" in result.output
    assert "headless" in result.output.lower()
