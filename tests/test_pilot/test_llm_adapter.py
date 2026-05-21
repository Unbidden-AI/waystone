"""Tests for pilot.llm_adapter — LLM call handling and tool schemas."""

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from pilot.llm_adapter import (
    _extract_retry_after,
    _parse_tool_calls,
    _resolve_api_key,
    build_tool_schemas,
    estimate_tokens,
)
from pilot.types import Message, ToolCall

# Prevent errors from missing tiktoken during tests
_TIKTOKEN_UNAVAILABLE = True


# ==============================================================================
# estimate_tokens tests
# ==============================================================================


class TestEstimateTokens:
    """Test token estimation with various inputs."""

    def test_non_empty_string_returns_positive_int(self):
        """estimate_tokens returns int > 0 for non-empty strings."""
        result = estimate_tokens("hello world")
        assert isinstance(result, int)
        assert result > 0

    def test_empty_string_returns_one(self):
        """estimate_tokens returns 1 for empty string."""
        result = estimate_tokens("")
        assert result == 1

    def test_single_char_returns_positive_int(self):
        """estimate_tokens returns int > 0 for single character."""
        result = estimate_tokens("a")
        assert isinstance(result, int)
        assert result > 0

    def test_long_text_increases_token_count(self):
        """estimate_tokens returns higher count for longer text."""
        short = estimate_tokens("hello")
        long = estimate_tokens("hello world this is a longer sentence with more words")
        assert long > short

    @patch("pilot.llm_adapter._get_encoding")
    def test_fallback_when_tiktoken_unavailable(self, mock_get_encoding):
        """estimate_tokens falls back to len//4 when tiktoken unavailable."""
        mock_get_encoding.return_value = False
        result = estimate_tokens("hello world")
        # Fallback is len(text) // 4, but with max(1, ...) so minimum 1
        assert isinstance(result, int)
        assert result > 0

    @patch("pilot.llm_adapter._get_encoding")
    def test_fallback_when_tiktoken_encode_raises(self, mock_get_encoding):
        """estimate_tokens falls back when tiktoken.encode raises."""
        mock_encoding = MagicMock()
        mock_encoding.encode.side_effect = Exception("tiktoken error")
        mock_get_encoding.return_value = mock_encoding
        result = estimate_tokens("hello world")
        assert isinstance(result, int)
        assert result > 0


# ==============================================================================
# build_tool_schemas tests
# ==============================================================================


class TestBuildToolSchemas:
    """Test tool schema building."""

    def test_returns_list(self):
        """build_tool_schemas returns a list."""
        result = build_tool_schemas([])
        assert isinstance(result, list)

    def test_empty_tools_returns_empty_list(self):
        """build_tool_schemas returns empty list for empty input."""
        result = build_tool_schemas([])
        assert result == []

    def test_known_tool_bash(self):
        """build_tool_schemas includes bash tool schema."""
        result = build_tool_schemas(["bash"])
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "bash"
        assert "command" in result[0]["function"]["parameters"]["properties"]

    def test_known_tool_read_file(self):
        """build_tool_schemas includes read_file tool schema."""
        result = build_tool_schemas(["read_file"])
        assert len(result) == 1
        assert result[0]["function"]["name"] == "read_file"
        assert "path" in result[0]["function"]["parameters"]["properties"]

    def test_known_tool_write_file(self):
        """build_tool_schemas includes write_file tool schema."""
        result = build_tool_schemas(["write_file"])
        assert len(result) == 1
        assert result[0]["function"]["name"] == "write_file"
        assert "path" in result[0]["function"]["parameters"]["properties"]
        assert "content" in result[0]["function"]["parameters"]["properties"]

    def test_known_tool_glob(self):
        """build_tool_schemas includes glob tool schema."""
        result = build_tool_schemas(["glob"])
        assert len(result) == 1
        assert result[0]["function"]["name"] == "glob"
        assert "pattern" in result[0]["function"]["parameters"]["properties"]

    def test_known_tool_grep(self):
        """build_tool_schemas includes grep tool schema."""
        result = build_tool_schemas(["grep"])
        assert len(result) == 1
        assert result[0]["function"]["name"] == "grep"
        assert "pattern" in result[0]["function"]["parameters"]["properties"]

    def test_multiple_tools(self):
        """build_tool_schemas returns all requested tools."""
        result = build_tool_schemas(["bash", "read_file", "grep"])
        assert len(result) == 3
        names = [s["function"]["name"] for s in result]
        assert set(names) == {"bash", "read_file", "grep"}

    @patch("pilot.llm_adapter.log")
    def test_unknown_tool_skipped(self, mock_log):
        """build_tool_schemas skips unknown tool names and logs warning."""
        result = build_tool_schemas(["unknown_tool"])
        assert result == []
        mock_log.warning.assert_called_once()
        assert "Unknown tool" in str(mock_log.warning.call_args)

    @patch("pilot.llm_adapter.log")
    def test_mixed_known_and_unknown_tools(self, mock_log):
        """build_tool_schemas includes known tools and skips unknown."""
        result = build_tool_schemas(["bash", "invalid_tool", "read_file"])
        assert len(result) == 2
        names = [s["function"]["name"] for s in result]
        assert set(names) == {"bash", "read_file"}
        mock_log.warning.assert_called_once()


# ==============================================================================
# _resolve_api_key tests
# ==============================================================================


class TestResolveApiKey:
    """Test API key resolution from config and environment."""

    def test_api_key_env_priority_custom_env(self):
        """_resolve_api_key uses api_key_env if provided and set."""
        with patch.dict(os.environ, {"CUSTOM_API_KEY": "custom_value"}, clear=True):
            cfg = {"api_key_env": "CUSTOM_API_KEY"}
            result = _resolve_api_key(cfg)
            assert result == "custom_value"

    def test_api_key_env_priority_missing_env(self):
        """_resolve_api_key skips unset api_key_env and tries standard vars."""
        with patch.dict(os.environ, {"CTX_API_KEY": "ctx_value"}, clear=True):
            cfg = {"api_key_env": "MISSING_VAR"}
            result = _resolve_api_key(cfg)
            assert result == "ctx_value"

    def test_ctx_api_key_second_priority(self):
        """_resolve_api_key tries CTX_API_KEY when no api_key_env."""
        with patch.dict(os.environ, {"CTX_API_KEY": "ctx_value"}, clear=True):
            cfg = {}
            result = _resolve_api_key(cfg)
            assert result == "ctx_value"

    def test_openai_api_key_third_priority(self):
        """_resolve_api_key tries OPENAI_API_KEY when CTX_API_KEY unset."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "openai_value"}, clear=True):
            cfg = {}
            result = _resolve_api_key(cfg)
            assert result == "openai_value"

    def test_anthropic_api_key_fourth_priority(self):
        """_resolve_api_key tries ANTHROPIC_API_KEY as last resort."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "anthropic_value"}, clear=True):
            cfg = {}
            result = _resolve_api_key(cfg)
            assert result == "anthropic_value"

    def test_priority_order_ctx_over_openai(self):
        """_resolve_api_key prefers CTX_API_KEY over OPENAI_API_KEY."""
        with patch.dict(
            os.environ,
            {"CTX_API_KEY": "ctx_value", "OPENAI_API_KEY": "openai_value"},
            clear=True,
        ):
            cfg = {}
            result = _resolve_api_key(cfg)
            assert result == "ctx_value"

    def test_priority_order_openai_over_anthropic(self):
        """_resolve_api_key prefers OPENAI_API_KEY over ANTHROPIC_API_KEY."""
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "openai_value", "ANTHROPIC_API_KEY": "anthropic_value"},
            clear=True,
        ):
            cfg = {}
            result = _resolve_api_key(cfg)
            assert result == "openai_value"

    def test_no_keys_returns_none(self):
        """_resolve_api_key returns None when no keys are set."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = {}
            result = _resolve_api_key(cfg)
            assert result is None

    def test_empty_api_key_env_falls_through(self):
        """_resolve_api_key treats empty api_key_env as not set."""
        with patch.dict(os.environ, {"CTX_API_KEY": "ctx_value"}, clear=True):
            cfg = {"api_key_env": ""}
            result = _resolve_api_key(cfg)
            assert result == "ctx_value"


# ==============================================================================
# _extract_retry_after tests
# ==============================================================================


class TestExtractRetryAfter:
    """Test Retry-After header extraction from exceptions."""

    def test_returns_float_from_response_headers(self):
        """_extract_retry_after returns float from response.headers Retry-After."""
        exc = Exception()
        mock_response = MagicMock()
        mock_response.headers = {"Retry-After": "5.5"}
        exc.response = mock_response
        result = _extract_retry_after(exc)
        assert result == 5.5
        assert isinstance(result, float)

    def test_returns_float_from_retry_after_lowercase(self):
        """_extract_retry_after handles lowercase retry-after header."""
        exc = Exception()
        mock_response = MagicMock()
        mock_response.headers = {"retry-after": "3.0"}
        exc.response = mock_response
        result = _extract_retry_after(exc)
        assert result == 3.0

    def test_returns_none_when_no_header(self):
        """_extract_retry_after returns None when Retry-After header missing."""
        exc = Exception()
        mock_response = MagicMock()
        mock_response.headers = {}
        exc.response = mock_response
        result = _extract_retry_after(exc)
        assert result is None

    def test_returns_none_when_no_response(self):
        """_extract_retry_after returns None when no response attribute."""
        exc = Exception()
        result = _extract_retry_after(exc)
        assert result is None

    def test_checks_llm_provider_exception_attribute(self):
        """_extract_retry_after checks llm_provider_exception attribute."""
        exc = Exception()
        mock_response = MagicMock()
        mock_response.headers = {"Retry-After": "2.0"}
        exc.llm_provider_exception = mock_response
        result = _extract_retry_after(exc)
        assert result == 2.0

    def test_checks_underscore_response_attribute(self):
        """_extract_retry_after checks _response attribute."""
        exc = Exception()
        mock_response = MagicMock()
        mock_response.headers = {"Retry-After": "4.5"}
        exc._response = mock_response
        result = _extract_retry_after(exc)
        assert result == 4.5

    def test_returns_none_for_invalid_float(self):
        """_extract_retry_after returns None for non-numeric Retry-After."""
        exc = Exception()
        mock_response = MagicMock()
        mock_response.headers = {"Retry-After": "not_a_number"}
        exc.response = mock_response
        result = _extract_retry_after(exc)
        assert result is None

    def test_response_without_headers_attribute(self):
        """_extract_retry_after handles response without headers attribute."""
        exc = Exception()
        exc.response = "not_a_response_object"  # No headers attribute
        result = _extract_retry_after(exc)
        assert result is None

    def test_integer_retry_after_converted_to_float(self):
        """_extract_retry_after converts integer Retry-After to float."""
        exc = Exception()
        mock_response = MagicMock()
        mock_response.headers = {"Retry-After": "10"}
        exc.response = mock_response
        result = _extract_retry_after(exc)
        assert result == 10.0


# ==============================================================================
# _parse_tool_calls tests
# ==============================================================================


class TestParseToolCalls:
    """Test tool call parsing from LLM response messages."""

    def test_returns_none_when_no_tool_calls(self):
        """_parse_tool_calls returns None when message has no tool_calls."""
        msg = MagicMock(spec=[])
        result = _parse_tool_calls(msg)
        assert result is None

    def test_returns_none_when_tool_calls_empty(self):
        """_parse_tool_calls returns None when tool_calls list is empty."""
        msg = MagicMock()
        msg.tool_calls = []
        result = _parse_tool_calls(msg)
        assert result is None

    def test_parses_single_tool_call(self):
        """_parse_tool_calls parses a single tool call."""
        msg = MagicMock()
        tc = MagicMock()
        tc.id = "call_123"
        tc.function.name = "bash"
        tc.function.arguments = '{"command": "ls -la"}'
        msg.tool_calls = [tc]
        result = _parse_tool_calls(msg)
        assert result is not None
        assert len(result) == 1
        assert result[0].id == "call_123"
        assert result[0].name == "bash"
        assert result[0].args == {"command": "ls -la"}

    def test_parses_multiple_tool_calls(self):
        """_parse_tool_calls parses multiple tool calls."""
        msg = MagicMock()
        tc1 = MagicMock()
        tc1.id = "call_1"
        tc1.function.name = "bash"
        tc1.function.arguments = '{"command": "echo hello"}'
        tc2 = MagicMock()
        tc2.id = "call_2"
        tc2.function.name = "read_file"
        tc2.function.arguments = '{"path": "/tmp/file.txt"}'
        msg.tool_calls = [tc1, tc2]
        result = _parse_tool_calls(msg)
        assert result is not None
        assert len(result) == 2
        assert result[0].name == "bash"
        assert result[1].name == "read_file"

    def test_handles_dict_arguments(self):
        """_parse_tool_calls handles arguments already as dict."""
        msg = MagicMock()
        tc = MagicMock()
        tc.id = "call_123"
        tc.function.name = "bash"
        tc.function.arguments = {"command": "ls -la"}
        msg.tool_calls = [tc]
        result = _parse_tool_calls(msg)
        assert result is not None
        assert result[0].args == {"command": "ls -la"}

    def test_handles_empty_arguments(self):
        """_parse_tool_calls handles None/empty arguments."""
        msg = MagicMock()
        tc = MagicMock()
        tc.id = "call_123"
        tc.function.name = "bash"
        tc.function.arguments = None
        msg.tool_calls = [tc]
        result = _parse_tool_calls(msg)
        assert result is not None
        assert result[0].args == {}

    @patch("pilot.llm_adapter.log")
    def test_skips_malformed_json_arguments(self, mock_log):
        """_parse_tool_calls skips tool calls with malformed JSON."""
        msg = MagicMock()
        tc = MagicMock()
        tc.id = "call_123"
        tc.function.name = "bash"
        tc.function.arguments = '{"command": invalid json}'
        msg.tool_calls = [tc]
        result = _parse_tool_calls(msg)
        assert result is None
        mock_log.warning.assert_called_once()

    @patch("pilot.llm_adapter.log")
    def test_skips_missing_function_attribute(self, mock_log):
        """_parse_tool_calls skips tool calls without function attribute."""
        msg = MagicMock()
        tc = MagicMock(spec=[])  # No attributes
        msg.tool_calls = [tc]
        result = _parse_tool_calls(msg)
        assert result is None
        mock_log.warning.assert_called_once()

    @patch("pilot.llm_adapter.log")
    def test_partial_parse_with_mixed_valid_invalid(self, mock_log):
        """_parse_tool_calls parses valid calls and skips invalid ones."""
        msg = MagicMock()
        tc1 = MagicMock()
        tc1.id = "call_1"
        tc1.function.name = "bash"
        tc1.function.arguments = '{"command": "ls"}'
        tc2 = MagicMock()
        tc2.id = "call_2"
        tc2.function.name = "read_file"
        tc2.function.arguments = 'invalid json'
        msg.tool_calls = [tc1, tc2]
        result = _parse_tool_calls(msg)
        assert result is not None
        assert len(result) == 1
        assert result[0].name == "bash"
        assert mock_log.warning.called


# ==============================================================================
# call_llm async tests
# ==============================================================================


@pytest.mark.asyncio
class TestCallLLM:
    """Test the main call_llm async function."""

    async def test_successful_call_returns_text(self):
        """call_llm returns (text, None, 'stop') on successful text response."""
        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {"max_retries": 2, "transient_codes": [500]},
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.content = "Hi there!"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = {"total_tokens": 10}

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response
            text, tool_calls, finish_reason = await call_llm(messages, system, cfg)

        assert text == "Hi there!"
        assert tool_calls is None
        assert finish_reason == "stop"

    async def test_tool_call_response_returns_tool_calls(self):
        """call_llm returns (None, [ToolCall(...)], 'tool_calls') on tool response."""
        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {"max_retries": 2, "transient_codes": [500]},
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Use a tool")]

        mock_tc = MagicMock()
        mock_tc.id = "call_123"
        mock_tc.function.name = "bash"
        mock_tc.function.arguments = '{"command": "ls"}'

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "tool_calls"
        mock_response.choices[0].message.content = None
        mock_response.choices[0].message.tool_calls = [mock_tc]
        mock_response.usage = {"total_tokens": 10}

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response
            text, tool_calls, finish_reason = await call_llm(messages, system, cfg, tools=["bash"])

        assert text is None
        assert tool_calls is not None
        assert len(tool_calls) == 1
        assert tool_calls[0].name == "bash"
        assert finish_reason == "tool_calls"

    async def test_authentication_error_raises_immediately(self):
        """call_llm raises AuthenticationError immediately without retry."""
        from litellm.exceptions import AuthenticationError

        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {"max_retries": 4, "transient_codes": [500]},
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        auth_error = AuthenticationError(
            message="Invalid API key",
            llm_provider="openai",
            model="test/model",
        )
        auth_error.status_code = 401

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.side_effect = auth_error
            with pytest.raises(AuthenticationError):
                await call_llm(messages, system, cfg)

        # Should only be called once (no retry)
        assert mock_acompletion.call_count == 1

    async def test_internal_server_error_500_retries_then_raises(self):
        """call_llm retries InternalServerError with code 500 then raises."""
        from litellm.exceptions import InternalServerError

        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {
                "max_retries": 2,
                "transient_codes": [500],
                "backoff_seconds": [0.001, 0.001],
            },
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        error_500 = InternalServerError(
            message="Server error",
            llm_provider="openai",
            model="test/model",
        )
        error_500.status_code = 500

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.side_effect = error_500
            with patch("pilot.llm_adapter.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(InternalServerError):
                    await call_llm(messages, system, cfg)

        # Should retry: attempt 0, 1, 2 = 3 total calls
        assert mock_acompletion.call_count == 3

    async def test_internal_server_error_400_raises_immediately(self):
        """call_llm raises InternalServerError with non-transient code immediately."""
        from litellm.exceptions import InternalServerError

        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {
                "max_retries": 4,
                "transient_codes": [500, 502, 503, 504],
            },
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        error_400 = InternalServerError(
            message="Bad request",
            llm_provider="openai",
            model="test/model",
        )
        error_400.status_code = 400

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.side_effect = error_400
            with pytest.raises(InternalServerError):
                await call_llm(messages, system, cfg)

        # Should only be called once (no retry for non-transient code)
        assert mock_acompletion.call_count == 1

    async def test_rate_limit_error_retries_with_backoff(self):
        """call_llm retries RateLimitError with exponential backoff."""
        from litellm.exceptions import RateLimitError

        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {
                "max_retries": 2,
                "backoff_seconds": [0.001, 0.001],
            },
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        rate_limit_error = RateLimitError(
            message="Too many requests",
            llm_provider="openai",
            model="test/model",
        )

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.content = "Success"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = {"total_tokens": 10}

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            # Fail twice, then succeed
            mock_acompletion.side_effect = [rate_limit_error, rate_limit_error, mock_response]
            with patch("pilot.llm_adapter.asyncio.sleep", new_callable=AsyncMock):
                text, tool_calls, finish_reason = await call_llm(messages, system, cfg)

        assert text == "Success"
        assert tool_calls is None
        assert finish_reason == "stop"
        assert mock_acompletion.call_count == 3

    async def test_rate_limit_error_uses_retry_after_header(self):
        """call_llm uses Retry-After from RateLimitError if present."""
        from litellm.exceptions import RateLimitError

        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {
                "max_retries": 2,
                "backoff_seconds": [1.0, 2.0],
            },
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        rate_limit_error = RateLimitError(
            message="Too many requests",
            llm_provider="openai",
            model="test/model",
        )
        mock_response = MagicMock()
        mock_response.headers = {"Retry-After": "0.001"}
        rate_limit_error.response = mock_response

        mock_llm_response = MagicMock()
        mock_llm_response.choices = [MagicMock()]
        mock_llm_response.choices[0].finish_reason = "stop"
        mock_llm_response.choices[0].message.content = "Success"
        mock_llm_response.choices[0].message.tool_calls = None
        mock_llm_response.usage = {"total_tokens": 10}

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.side_effect = [rate_limit_error, mock_llm_response]
            with patch("pilot.llm_adapter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                text, tool_calls, finish_reason = await call_llm(messages, system, cfg)

        assert text == "Success"
        # Verify sleep was called with Retry-After value (0.001) not backoff
        mock_sleep.assert_called_once_with(0.001)

    async def test_timeout_error_retries_then_raises(self):
        """call_llm retries Timeout error then raises."""
        from litellm.exceptions import Timeout

        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {
                "max_retries": 2,
                "backoff_seconds": [0.001, 0.001],
            },
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        timeout_error = Timeout(
            message="Request timeout",
            model="test/model",
            llm_provider="openai",
        )

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.side_effect = timeout_error
            with patch("pilot.llm_adapter.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(Timeout):
                    await call_llm(messages, system, cfg)

        # Should retry: attempt 0, 1, 2 = 3 total calls
        assert mock_acompletion.call_count == 3

    async def test_api_key_resolution_in_call(self):
        """call_llm resolves API key and passes to litellm.acompletion."""
        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "api_key_env": "TEST_API_KEY",
            "_retry": {"max_retries": 0},
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.content = "Response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = {"total_tokens": 10}

        with patch.dict(os.environ, {"TEST_API_KEY": "secret_key"}):
            with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
                mock_acompletion.return_value = mock_response
                await call_llm(messages, system, cfg)

        # Check that api_key was passed
        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["api_key"] == "secret_key"

    async def test_builds_tool_schemas_when_tools_provided(self):
        """call_llm builds tool schemas when tools parameter provided."""
        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {"max_retries": 0},
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Use tools")]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.content = "Response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = {"total_tokens": 10}

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response
            await call_llm(messages, system, cfg, tools=["bash", "read_file"])

        # Check that tools were passed to acompletion
        call_kwargs = mock_acompletion.call_args[1]
        assert "tools" in call_kwargs
        assert len(call_kwargs["tools"]) == 2
        assert call_kwargs["tool_choice"] == "auto"

    async def test_no_tools_when_tools_none(self):
        """call_llm doesn't pass tools when tools parameter is None."""
        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {"max_retries": 0},
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.content = "Response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = {"total_tokens": 10}

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response
            await call_llm(messages, system, cfg, tools=None)

        # Check that tools were not passed
        call_kwargs = mock_acompletion.call_args[1]
        assert "tools" not in call_kwargs

    async def test_default_config_values(self):
        """call_llm uses sensible defaults when config keys missing."""
        from pilot.llm_adapter import call_llm

        cfg = {}  # Minimal config
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.content = "Response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = {"total_tokens": 10}

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response
            await call_llm(messages, system, cfg)

        call_kwargs = mock_acompletion.call_args[1]
        # Check defaults
        assert call_kwargs["model"] == "anthropic/claude-sonnet-4-6"
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_tokens"] == 4096

    async def test_finish_reason_none_defaults_to_stop(self):
        """call_llm defaults finish_reason to 'stop' when None."""
        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {"max_retries": 0},
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = None
        mock_response.choices[0].message.content = "Response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = {"total_tokens": 10}

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response
            text, tool_calls, finish_reason = await call_llm(messages, system, cfg)

        assert finish_reason == "stop"

    async def test_empty_content_treated_as_none(self):
        """call_llm treats empty content string as None."""
        from pilot.llm_adapter import call_llm

        cfg = {
            "model": "test/model",
            "_retry": {"max_retries": 0},
        }
        system = "You are helpful."
        messages = [Message(role="user", content="Hello")]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.content = ""
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = {"total_tokens": 10}

        with patch("pilot.llm_adapter.litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response
            text, tool_calls, finish_reason = await call_llm(messages, system, cfg)

        assert text is None
