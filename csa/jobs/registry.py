"""Scheduled job definitions and metadata registry (LG10)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

# Job function signature:
# - If per_client is True: (conn, client_id, clock, [batch_size]) -> dict[str, Any]
# - If per_client is False: (conn, clock, [batch_size]) -> dict[str, Any]
JobFn = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Job:
    """Definition of a scheduled background job."""

    name: str
    every: timedelta
    fn: JobFn
    timeout: float = 30.0
    per_client: bool = True
    batch_size: int = 100


class JobRegistry:
    """Registry holding all configured scheduled jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def register(self, job: Job) -> None:
        if job.name in self._jobs:
            raise ValueError(f"job already registered: {job.name}")
        self._jobs[job.name] = job

    def get(self, name: str) -> Job | None:
        return self._jobs.get(name)

    def all_jobs(self) -> list[Job]:
        return list(self._jobs.values())
