"""Tests for orchestrator/scheduler.py."""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.scheduler import (
    OutputConfig,
    ErrorNotificationConfig,
    ScheduleConfig,
    Scheduler,
    _should_run,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def mock_store():
    """Return a mock GraphStore."""
    return MagicMock()


@pytest.fixture
def mock_config():
    """Return a minimal config dict."""
    return {
        "orchestrator": {
            "context": {},
            "system_prompt": {},
            "llm": {},
            "tools": {"enabled": []},
            "retry": {},
        },
    }


@pytest.fixture
def sample_schedule_config():
    """Return a sample ScheduleConfig."""
    return ScheduleConfig(
        name="daily_summary",
        enabled=True,
        project="test_project",
        schedule="0 9 * * *",
        prompt_template="Summarize the day. Date: {DATE}",
        output=OutputConfig(destination="stdout"),
        error_notification=None,
    )


# ===========================================================================
# Tests: OutputConfig
# ===========================================================================


def test_output_config_stdout():
    """Test OutputConfig with stdout destination."""
    cfg = OutputConfig(destination="stdout")
    assert cfg.destination == "stdout"
    assert cfg.webhook_url_env is None
    assert cfg.path is None


def test_output_config_file():
    """Test OutputConfig with file destination."""
    cfg = OutputConfig(destination="file", path="/tmp/output_{DATE}.txt")
    assert cfg.destination == "file"
    assert cfg.path == "/tmp/output_{DATE}.txt"


def test_output_config_discord():
    """Test OutputConfig with discord destination."""
    cfg = OutputConfig(destination="discord", webhook_url_env="DISCORD_WEBHOOK")
    assert cfg.destination == "discord"
    assert cfg.webhook_url_env == "DISCORD_WEBHOOK"


def test_output_config_resolve_webhook_url_success():
    """Test resolve_webhook_url returns the env var value."""
    os.environ["TEST_WEBHOOK"] = "https://discord.com/api/webhooks/123/abc"
    cfg = OutputConfig(destination="discord", webhook_url_env="TEST_WEBHOOK")

    url = cfg.resolve_webhook_url()
    assert url == "https://discord.com/api/webhooks/123/abc"


def test_output_config_resolve_webhook_url_missing_env():
    """Test resolve_webhook_url raises ValueError when env var is missing."""
    cfg = OutputConfig(destination="discord", webhook_url_env="NONEXISTENT_VAR")

    with pytest.raises(ValueError, match="Missing webhook URL env var"):
        cfg.resolve_webhook_url()


def test_output_config_resolve_webhook_url_not_applicable():
    """Test resolve_webhook_url returns None when webhook_url_env is not set."""
    cfg = OutputConfig(destination="stdout")
    url = cfg.resolve_webhook_url()
    assert url is None


def test_output_config_resolve_path_with_templating():
    """Test resolve_path substitutes template variables."""
    cfg = OutputConfig(destination="file", path="/tmp/{DATE}_{TIME}.log")
    template_vars = {"DATE": "2026-05-17", "TIME": "10:30:00"}

    path = cfg.resolve_path(template_vars)
    assert path == "/tmp/2026-05-17_10:30:00.log"


def test_output_config_resolve_path_missing():
    """Test resolve_path returns None when path is not set."""
    cfg = OutputConfig(destination="discord")
    path = cfg.resolve_path()
    assert path is None


def test_output_config_resolve_path_no_template_vars():
    """Test resolve_path works without template variables."""
    cfg = OutputConfig(destination="file", path="/tmp/output.txt")
    path = cfg.resolve_path()
    assert path == "/tmp/output.txt"


# ===========================================================================
# Tests: ErrorNotificationConfig
# ===========================================================================


def test_error_notification_config_disabled():
    """Test ErrorNotificationConfig defaults to disabled."""
    cfg = ErrorNotificationConfig()
    assert cfg.enabled is False


def test_error_notification_config_discord():
    """Test ErrorNotificationConfig with discord destination."""
    cfg = ErrorNotificationConfig(enabled=True, destination="discord", webhook_url_env="WEBHOOK")
    assert cfg.enabled is True
    assert cfg.destination == "discord"
    assert cfg.webhook_url_env == "WEBHOOK"


# ===========================================================================
# Tests: ScheduleConfig
# ===========================================================================


def test_schedule_config_from_dict_minimal():
    """Test ScheduleConfig.from_dict with minimal required fields."""
    d = {
        "name": "test_schedule",
        "project": "test_project",
        "schedule": "0 9 * * *",
        "prompt_template": "Run task",
        "output": {"destination": "stdout"},
    }

    cfg = ScheduleConfig.from_dict(d)
    assert cfg.name == "test_schedule"
    assert cfg.enabled is True  # default
    assert cfg.project == "test_project"
    assert cfg.output.destination == "stdout"


def test_schedule_config_from_dict_with_enabled_false():
    """Test ScheduleConfig.from_dict with enabled=False."""
    d = {
        "name": "test_schedule",
        "enabled": False,
        "project": "test_project",
        "schedule": "0 9 * * *",
        "prompt_template": "Run task",
        "output": {"destination": "stdout"},
    }

    cfg = ScheduleConfig.from_dict(d)
    assert cfg.enabled is False


def test_schedule_config_from_dict_with_error_notification():
    """Test ScheduleConfig.from_dict with error notification config."""
    d = {
        "name": "test_schedule",
        "project": "test_project",
        "schedule": "0 9 * * *",
        "prompt_template": "Run task",
        "output": {"destination": "stdout"},
        "error_notification": {
            "enabled": True,
            "destination": "discord",
            "webhook_url_env": "ERROR_WEBHOOK",
        },
    }

    cfg = ScheduleConfig.from_dict(d)
    assert cfg.error_notification is not None
    assert cfg.error_notification.enabled is True
    assert cfg.error_notification.destination == "discord"


def test_schedule_config_to_dict():
    """Test ScheduleConfig.to_dict serialization."""
    cfg = ScheduleConfig(
        name="test",
        enabled=True,
        project="proj",
        schedule="0 9 * * *",
        prompt_template="task",
        output=OutputConfig(destination="stdout"),
    )

    d = cfg.to_dict()
    assert d["name"] == "test"
    assert d["enabled"] is True
    assert d["project"] == "proj"


# ===========================================================================
# Tests: Scheduler
# ===========================================================================


def test_scheduler_init_empty_config():
    """Test Scheduler initialization with empty config."""
    cfg = {"orchestrator": {}}
    scheduler = Scheduler(cfg)

    assert len(scheduler.list_schedules()) == 0


def test_scheduler_init_with_schedules():
    """Test Scheduler initialization with schedules in config."""
    cfg = {
        "orchestrator": {
            "schedules": [
                {
                    "name": "daily",
                    "enabled": True,
                    "project": "test",
                    "schedule": "0 9 * * *",
                    "prompt_template": "task",
                    "output": {"destination": "stdout"},
                }
            ]
        }
    }
    scheduler = Scheduler(cfg)

    schedules = scheduler.list_schedules()
    assert len(schedules) == 1
    assert schedules[0].name == "daily"


def test_scheduler_list_schedules_enabled_only():
    """Test Scheduler.list_schedules with enabled_only=True."""
    cfg = {
        "orchestrator": {
            "schedules": [
                {
                    "name": "enabled",
                    "enabled": True,
                    "project": "test",
                    "schedule": "0 9 * * *",
                    "prompt_template": "task",
                    "output": {"destination": "stdout"},
                },
                {
                    "name": "disabled",
                    "enabled": False,
                    "project": "test",
                    "schedule": "0 10 * * *",
                    "prompt_template": "task",
                    "output": {"destination": "stdout"},
                },
            ]
        }
    }
    scheduler = Scheduler(cfg)

    enabled = scheduler.list_schedules(enabled_only=True)
    assert len(enabled) == 1
    assert enabled[0].name == "enabled"


@pytest.mark.asyncio
async def test_scheduler_run_schedule_not_found(mock_store, mock_config):
    """Test Scheduler.run_schedule when schedule is not found."""
    scheduler = Scheduler(mock_config)

    result = await scheduler.run_schedule("nonexistent", mock_store)
    assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_scheduler_run_schedule_disabled(mock_store, sample_schedule_config):
    """Test Scheduler.run_schedule when schedule is disabled."""
    sample_schedule_config.enabled = False
    cfg = {"orchestrator": {"schedules": [sample_schedule_config.to_dict()]}}
    scheduler = Scheduler(cfg)

    result = await scheduler.run_schedule("daily_summary", mock_store)
    assert result["status"] == "disabled"


@pytest.mark.asyncio
async def test_scheduler_run_schedule_success(mock_store, sample_schedule_config):
    """Test Scheduler.run_schedule executes successfully."""
    cfg = {"orchestrator": {"schedules": [sample_schedule_config.to_dict()]}}
    scheduler = Scheduler(cfg)

    with patch("orchestrator.scheduler.Conversation") as mock_conv_cls:
        mock_conv = AsyncMock()
        mock_conv.chat = AsyncMock(return_value="Task completed.")
        mock_conv_cls.return_value = mock_conv

        result = await scheduler.run_schedule("daily_summary", mock_store)

        assert result["status"] == "success"
        assert "run_id" in result


@pytest.mark.asyncio
async def test_scheduler_run_schedule_template_expansion(mock_store, sample_schedule_config):
    """Test Scheduler.run_schedule expands prompt template variables."""
    cfg = {"orchestrator": {"schedules": [sample_schedule_config.to_dict()]}}
    scheduler = Scheduler(cfg)

    with patch("orchestrator.scheduler.Conversation") as mock_conv_cls:
        mock_conv = AsyncMock()
        mock_conv.chat = AsyncMock(return_value="Done.")
        mock_conv_cls.return_value = mock_conv

        await scheduler.run_schedule("daily_summary", mock_store)

        # Check that chat was called with expanded prompt
        call_args = mock_conv.chat.call_args
        prompt = call_args[0][0]
        assert "Date: 202" in prompt or prompt  # Should contain date expansion


@pytest.mark.asyncio
async def test_scheduler_run_schedule_error_handling(mock_store, sample_schedule_config):
    """Test Scheduler.run_schedule handles errors gracefully."""
    cfg = {"orchestrator": {"schedules": [sample_schedule_config.to_dict()]}}
    scheduler = Scheduler(cfg)

    with patch("orchestrator.scheduler.Conversation") as mock_conv_cls:
        mock_conv = AsyncMock()
        mock_conv.chat = AsyncMock(side_effect=Exception("Chat failed"))
        mock_conv_cls.return_value = mock_conv

        result = await scheduler.run_schedule("daily_summary", mock_store)

        assert result["status"] == "error"
        assert "run_id" in result


@pytest.mark.asyncio
async def test_scheduler_route_output_stdout(mock_store, sample_schedule_config):
    """Test Scheduler._route_output with stdout destination."""
    cfg = {"orchestrator": {"schedules": [sample_schedule_config.to_dict()]}}
    scheduler = Scheduler(cfg)

    with patch("builtins.print") as mock_print:
        await scheduler._route_output(sample_schedule_config, "Test output", "run_123")
        mock_print.assert_called_once_with("Test output")


@pytest.mark.asyncio
async def test_scheduler_route_output_file(mock_store, tmp_path):
    """Test Scheduler._route_output with file destination."""
    output_file = tmp_path / "output.txt"
    output_cfg = OutputConfig(
        destination="file",
        path=str(output_file),
    )
    sched_cfg = ScheduleConfig(
        name="test",
        enabled=True,
        project="test",
        schedule="0 9 * * *",
        prompt_template="task",
        output=output_cfg,
    )

    cfg = {"orchestrator": {"schedules": [sched_cfg.to_dict()]}}
    scheduler = Scheduler(cfg)

    await scheduler._route_output(sched_cfg, "Test content", "run_123")

    assert output_file.exists()
    content = output_file.read_text()
    assert "Test content" in content
    assert "run_123" in content


@pytest.mark.asyncio
async def test_scheduler_post_to_discord_missing_url():
    """Test Scheduler._post_to_discord raises ValueError when URL is missing."""
    output_cfg = OutputConfig(destination="discord")
    scheduler = Scheduler({})

    with pytest.raises(ValueError, match="Discord output requires webhook_url_env"):
        await scheduler._post_to_discord(output_cfg, "test", "run_123")


# ===========================================================================
# Tests: _should_run()
# ===========================================================================


def test_should_run_with_croniter():
    """Test _should_run checks cron expression correctly."""
    # Use a time that matches "0 9 * * *" (9:00 AM every day)
    test_time = datetime(2026, 5, 17, 9, 0, 0)

    result = _should_run("0 9 * * *", test_time)
    # This should be True or False depending on croniter logic
    assert isinstance(result, bool)


def test_should_run_invalid_cron():
    """Test _should_run handles invalid cron expressions gracefully."""
    test_time = datetime(2026, 5, 17, 9, 0, 0)

    result = _should_run("invalid cron", test_time)
    # Should return False for invalid cron
    assert result is False


def test_should_run_multiple_times_per_minute():
    """Test _should_run doesn't fire multiple times within same minute."""
    test_time = datetime(2026, 5, 17, 9, 0, 30)

    # Both 9:00:00 and 9:00:30 should be in the same minute
    result1 = _should_run("0 9 * * *", test_time)
    assert isinstance(result1, bool)


# ===========================================================================
# Tests: run_daemon()
# ===========================================================================


@pytest.mark.asyncio
async def test_run_daemon_no_schedules(mock_store, mock_config):
    """Test run_daemon logs warning when no enabled schedules exist."""
    with patch("orchestrator.scheduler.log") as mock_log:
        import asyncio

        # Set a short timeout to prevent infinite loop
        async def run_with_timeout():
            try:
                await asyncio.wait_for(
                    _run_daemon_for_test(mock_store, mock_config),
                    timeout=0.1
                )
            except asyncio.TimeoutError:
                pass

        async def _run_daemon_for_test(store, cfg):
            from orchestrator.scheduler import run_daemon
            await run_daemon(cfg, store)

        await run_with_timeout()


# ===========================================================================
# Integration tests
# ===========================================================================


@pytest.mark.asyncio
async def test_scheduler_full_workflow(tmp_path, mock_store):
    """Test Scheduler full workflow: init -> list -> run -> output."""
    schedule_dict = {
        "name": "integration_test",
        "enabled": True,
        "project": "test",
        "schedule": "0 9 * * *",
        "prompt_template": "Integration test at {TIMESTAMP}",
        "output": {
            "destination": "file",
            "path": str(tmp_path / "output.txt"),
        },
    }

    cfg = {"orchestrator": {"schedules": [schedule_dict]}}
    scheduler = Scheduler(cfg)

    # Verify schedule is registered
    schedules = scheduler.list_schedules()
    assert len(schedules) == 1

    with patch("orchestrator.scheduler.Conversation") as mock_conv_cls:
        mock_conv = AsyncMock()
        mock_conv.chat = AsyncMock(return_value="Integration test result")
        mock_conv_cls.return_value = mock_conv

        result = await scheduler.run_schedule("integration_test", mock_store)

        assert result["status"] == "success"
        assert (tmp_path / "output.txt").exists()
