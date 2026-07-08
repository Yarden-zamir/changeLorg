from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from changelorg.models import GenerationError
from changelorg.service import generate_changes
from changelorg.timeutils import parse_window


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass
class RefreshStatus:
    running: bool = False
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_change_count: int = 0
    last_errors: list[GenerationError] = field(default_factory=list)
    last_message: str = "not started"

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "last_started_at": self.last_started_at.isoformat() if self.last_started_at else None,
            "last_finished_at": self.last_finished_at.isoformat() if self.last_finished_at else None,
            "last_change_count": self.last_change_count,
            "last_errors": [error.model_dump(mode="json") for error in self.last_errors],
            "last_message": self.last_message,
        }


class HourlyRefreshLoop:
    def __init__(self, interval_seconds: int, window: str, initial_delay_seconds: int = 5) -> None:
        self.interval_seconds = interval_seconds
        self.window = window
        self.initial_delay_seconds = initial_delay_seconds
        self.status = RefreshStatus()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> HourlyRefreshLoop:
        return cls(
            interval_seconds=_env_int("CHANGELORG_REFRESH_INTERVAL_SECONDS", 3600),
            window=os.environ.get("CHANGELORG_REFRESH_WINDOW", "30d"),
            initial_delay_seconds=_env_int("CHANGELORG_REFRESH_INITIAL_DELAY_SECONDS", 5),
        )

    @staticmethod
    def enabled_from_env() -> bool:
        return _env_bool("CHANGELORG_AUTO_REFRESH", True)

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def refresh_once(self) -> None:
        if self._lock.locked():
            self.status.last_message = "skipped overlapping refresh"
            return
        async with self._lock:
            self.status.running = True
            self.status.last_started_at = datetime.now(timezone.utc)
            try:
                window = parse_window(since=self.window)
                result = await asyncio.to_thread(generate_changes, window, None, 500)
            except Exception as exc:
                self.status.last_change_count = 0
                self.status.last_errors = []
                self.status.last_message = f"refresh failed: {exc}"
            else:
                self.status.last_change_count = len(result.changes)
                self.status.last_errors = result.errors
                self.status.last_message = "refresh completed" if not result.errors else "refresh completed with source errors"
            finally:
                self.status.running = False
                self.status.last_finished_at = datetime.now(timezone.utc)

    async def _run_forever(self) -> None:
        await asyncio.sleep(self.initial_delay_seconds)
        while True:
            await self.refresh_once()
            await asyncio.sleep(self.interval_seconds)
