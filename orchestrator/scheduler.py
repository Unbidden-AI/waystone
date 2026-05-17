"""Scheduled runs — cron-driven headless orchestrator execution.

Status: EXPERIMENTAL in v1. Single-machine only. For HA / distributed scheduling,
use systemd timers or cron on one designated machine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from waystone.store import GraphStore

from .conversation import Conversation

log = logging.getLogger(__name__)


@dataclass
class OutputConfig:
    """Output routing configuration for a scheduled run."""

    destination: str  # "discord", "file", or "stdout"
    webhook_url_env: str | None = None  # Discord webhook URL env var
    path: str | None = None  # File output path (with date/time templating)

    def resolve_webhook_url(self) -> str | None:
        """Resolve webhook URL from environment variable.

        Returns
        -------
        str | None
            The webhook URL, or None if not found or not applicable.

        Raises
        ------
        ValueError
            If webhook_url_env is set but the env var is missing.
        """
        if not self.webhook_url_env:
            return None
        url = os.getenv(self.webhook_url_env)
        if not url:
            raise ValueError(f"Missing webhook URL env var: {self.webhook_url_env}")
        return url

    def resolve_path(self, template_vars: dict[str, str] | None = None) -> str | None:
        """Resolve output file path with templating.

        Supports substitutions: {DATE}, {TIME}, {TIMESTAMP}, {PROJECT_NAME}

        Parameters
        ----------
        template_vars : dict[str, str] | None
            Variables to substitute in the path template.

        Returns
        -------
        str | None
            The resolved path, or None if not applicable.
        """
        if not self.path:
            return None
        return self.path.format(**(template_vars or {}))


@dataclass
class ErrorNotificationConfig:
    """Error notification routing."""

    enabled: bool = False
    destination: str | None = None  # "discord" or "file"
    webhook_url_env: str | None = None


@dataclass
class ScheduleConfig:
    """Configuration for one scheduled run."""

    name: str  # unique identifier
    enabled: bool
    project: str  # project name
    schedule: str  # cron expression (e.g., "0 9 * * *" for 9am daily)
    prompt_template: str  # user-provided prompt with {DATE}, {TIME}, {TIMESTAMP} placeholders
    output: OutputConfig
    error_notification: ErrorNotificationConfig | None = None

    @classmethod
    def from_dict(cls, d: dict) -> ScheduleConfig:
        """Construct from a dict (e.g., from YAML config)."""
        output_dict = d.get("output", {})
        output = OutputConfig(
            destination=output_dict.get("destination", "stdout"),
            webhook_url_env=output_dict.get("webhook_url_env"),
            path=output_dict.get("path"),
        )

        err_dict = d.get("error_notification", {})
        error_notification = (
            ErrorNotificationConfig(
                enabled=err_dict.get("enabled", False),
                destination=err_dict.get("destination"),
                webhook_url_env=err_dict.get("webhook_url_env"),
            )
            if err_dict
            else None
        )

        return cls(
            name=d["name"],
            enabled=d.get("enabled", True),
            project=d["project"],
            schedule=d["schedule"],
            prompt_template=d["prompt_template"],
            output=output,
            error_notification=error_notification,
        )

    def to_dict(self) -> dict:
        """Convert to dict for serialization."""
        return asdict(self)


class Scheduler:
    """Manages scheduled runs of the orchestrator.

    EXPERIMENTAL in v1. Intended for single-machine deployment.
    For HA / distributed scheduling, use system cron or systemd timers.
    """

    def __init__(self, cfg: dict):
        """
        Parameters
        ----------
        cfg : dict
            Full config dict (output of load_config()).
        """
        self._cfg = cfg
        self._schedules: dict[str, ScheduleConfig] = {}

        # Load schedules from config (if present)
        schedules_list = cfg.get("orchestrator", {}).get("schedules", [])
        for sched_dict in schedules_list:
            try:
                sched = ScheduleConfig.from_dict(sched_dict)
                self._schedules[sched.name] = sched
            except Exception as e:
                log.error("Failed to load schedule %r: %s", sched_dict.get("name"), e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_schedules(self, enabled_only: bool = False) -> list[ScheduleConfig]:
        """Return all configured schedules.

        Parameters
        ----------
        enabled_only : bool
            If True, only return enabled schedules.

        Returns
        -------
        list[ScheduleConfig]
            List of schedules.
        """
        schedules = list(self._schedules.values())
        if enabled_only:
            schedules = [s for s in schedules if s.enabled]
        return schedules

    async def run_schedule(
        self,
        schedule_name: str,
        store: GraphStore,
        project_root: str | None = None,
    ) -> dict[str, Any]:
        """Execute a single scheduled run.

        Parameters
        ----------
        schedule_name : str
            Name of the schedule to run.
        store : GraphStore
            Open graph store for the project.
        project_root : str | None
            Base directory for project (for resolving relative paths).

        Returns
        -------
        dict[str, Any]
            Result dict with keys: status, message, run_id (if success).
            status: "success", "not_found", "disabled", or "error"
        """
        matching = [s for s in self._schedules.values() if s.name == schedule_name]

        if not matching:
            return {
                "status": "not_found",
                "message": f"Schedule {schedule_name!r} not found",
            }

        sched = matching[0]

        if not sched.enabled:
            return {
                "status": "disabled",
                "message": f"Schedule {schedule_name!r} is disabled",
            }

        run_id = f"{schedule_name}_{int(datetime.now(timezone.utc).timestamp())}"
        log.info("Running schedule %r [%s]", schedule_name, run_id)

        try:
            # Instantiate conversation and run the prompt
            conv = Conversation(
                cfg=self._cfg,
                store=store,
                project_name=sched.project,
                project_root=project_root,
            )

            # Expand prompt template with current time vars
            now = datetime.now(timezone.utc)
            template_vars = {
                "DATE": now.strftime("%Y-%m-%d"),
                "TIME": now.strftime("%H:%M:%S"),
                "TIMESTAMP": now.isoformat(),
                "PROJECT_NAME": sched.project,
            }
            prompt = sched.prompt_template.format(**template_vars)

            # Run the orchestrator
            reply = await conv.chat(prompt)

            # Route output
            await self._route_output(sched, reply, run_id)

            return {
                "status": "success",
                "run_id": run_id,
                "message": f"Schedule {schedule_name!r} completed successfully",
            }

        except Exception as e:
            error_msg = str(e)
            log.exception("Schedule %r failed: %s", schedule_name, error_msg)

            # Route error notification if configured
            if sched.error_notification and sched.error_notification.enabled:
                await self._route_error(sched, error_msg, run_id)

            return {
                "status": "error",
                "run_id": run_id,
                "message": f"Schedule {schedule_name!r} failed: {error_msg}",
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _route_output(self, sched: ScheduleConfig, output: str, run_id: str) -> None:
        """Route the run output to the configured destination.

        Parameters
        ----------
        sched : ScheduleConfig
            The schedule that ran.
        output : str
            The output text.
        run_id : str
            Unique run identifier.

        Raises
        ------
        Exception
            If routing fails (output will be logged and the exception re-raised).
        """
        if sched.output.destination == "discord":
            await self._post_to_discord(sched.output, output, run_id)
        elif sched.output.destination == "file":
            await self._write_to_file(sched.output, output, run_id)
        elif sched.output.destination == "stdout":
            print(output)
        else:
            log.warning("Unknown output destination: %s", sched.output.destination)

    async def _post_to_discord(self, output_cfg: OutputConfig, text: str, run_id: str) -> None:
        """Post output to Discord via webhook.

        Truncates to 1900 chars to fit Discord embed limit.

        Parameters
        ----------
        output_cfg : OutputConfig
            Output configuration.
        text : str
            Output text.
        run_id : str
            Run identifier.

        Raises
        ------
        ValueError
            If webhook URL is missing.
        Exception
            If HTTP POST fails.
        """
        url = output_cfg.resolve_webhook_url()
        if not url:
            raise ValueError("Discord output requires webhook_url_env")

        # Truncate to Discord limits
        truncated = text[: 1900 - len(f"\n\n_[{run_id}]_")]
        payload = {
            "content": truncated + f"\n\n_[{run_id}]_",
        }

        # Use httpx or requests if available; for now, assume user has httpx
        try:
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=10)
                resp.raise_for_status()
                log.info("Posted to Discord [%s]", run_id)
        except ImportError:
            log.warning("httpx not available; Discord output skipped")
        except Exception as e:
            log.error("Failed to post to Discord: %s", e)
            raise

    async def _write_to_file(self, output_cfg: OutputConfig, text: str, run_id: str) -> None:
        """Write output to a file.

        Supports date/time templating in the path.

        Parameters
        ----------
        output_cfg : OutputConfig
            Output configuration.
        text : str
            Output text.
        run_id : str
            Run identifier.

        Raises
        ------
        ValueError
            If path is missing.
        """
        now = datetime.now(timezone.utc)
        template_vars = {
            "DATE": now.strftime("%Y-%m-%d"),
            "TIME": now.strftime("%H:%M:%S"),
            "TIMESTAMP": now.isoformat(),
        }
        path = output_cfg.resolve_path(template_vars)
        if not path:
            raise ValueError("File output requires path")

        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + f"\n\n[Run: {run_id}]", encoding="utf-8")
        log.info("Wrote output to file: %s [%s]", p, run_id)

    async def _route_error(
        self,
        sched: ScheduleConfig,
        error_msg: str,
        run_id: str,
    ) -> None:
        """Route error notification to the configured destination.

        Parameters
        ----------
        sched : ScheduleConfig
            The schedule that failed.
        error_msg : str
            Error message.
        run_id : str
            Run identifier.
        """
        if not sched.error_notification:
            return

        err_cfg = sched.error_notification
        if not err_cfg.enabled:
            return

        error_text = f"Schedule {sched.name} failed:\n{error_msg}"

        if err_cfg.destination == "discord":
            try:
                output_cfg = OutputConfig(
                    destination="discord",
                    webhook_url_env=err_cfg.webhook_url_env,
                )
                await self._post_to_discord(output_cfg, error_text, run_id)
            except Exception as e:
                log.error("Failed to post error notification to Discord: %s", e)
        elif err_cfg.destination == "file":
            log.error("File-based error notification not yet implemented")
        else:
            log.warning("Unknown error notification destination: %s", err_cfg.destination)


async def run_daemon(cfg: dict, store: GraphStore, project_root: str | None = None) -> None:
    """Run the scheduler daemon.

    EXPERIMENTAL. Runs indefinitely, checking cron schedule every 60 seconds.
    For production use, recommend system cron or systemd timer.

    Parameters
    ----------
    cfg : dict
        Full config dict.
    store : GraphStore
        Open graph store.
    project_root : str | None
        Base directory for projects.
    """
    scheduler = Scheduler(cfg)
    schedules = scheduler.list_schedules(enabled_only=True)

    if not schedules:
        log.warning("Daemon started but no enabled schedules found")
        return

    log.info("Scheduler daemon starting with %d enabled schedule(s)", len(schedules))

    try:
        while True:
            await asyncio.sleep(60)
            now = datetime.now(timezone.utc)

            for sched in schedules:
                if _should_run(sched.schedule, now):
                    log.info("Cron check: schedule %r should fire", sched.name)
                    result = await scheduler.run_schedule(sched.name, store, project_root)
                    log.info("Schedule %r result: %s", sched.name, result)

    except KeyboardInterrupt:
        log.info("Scheduler daemon shutting down")


def _should_run(cron_expr: str, now: datetime) -> bool:
    """Check if a cron expression should fire at the given time.

    NOTE: This is a simplified check. For production use, recommend system cron
    or systemd timer.

    Parameters
    ----------
    cron_expr : str
        Cron expression (e.g., "0 9 * * *").
    now : datetime
        Current time (UTC).

    Returns
    -------
    bool
        True if the cron expression matches the current minute.
    """
    try:
        from croniter import croniter

        cron = croniter(cron_expr, now)
        # Get the last firing time
        last_fire = cron.get_prev(datetime)
        # If it fired in the last 60-120 seconds, assume we should run
        # (to avoid running multiple times per minute)
        elapsed = (now - last_fire).total_seconds()
        return elapsed < 120
    except Exception as e:
        log.warning("Failed to parse cron %r: %s", cron_expr, e)
        return False
