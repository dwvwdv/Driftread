"""Durable PostgreSQL queue adapter. RPC failures never fall back to local work.

Each RPC is one database transaction. Producers changing domain data should call
enqueue_background_job from their SQL transaction rather than two HTTP calls.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

LEASE_SECONDS = 120
RENEW_SECONDS = 30
POLL_SECONDS = 5


@dataclass(frozen=True)
class Job:
    id: str
    token: str
    kind: str
    attempts: int
    payload: dict
    timeout_seconds: int = 1800


class JobQueue:
    def __init__(self, db):
        self.db = db

    async def _rpc(self, name: str, params: dict):
        result = await asyncio.to_thread(lambda: self.db.rpc(name, params).execute())
        return result.data

    async def enqueue(self, kind: str, interval: int) -> str:
        return await self._rpc("enqueue_background_job", {
            "p_kind": kind, "p_singleton_key": "scheduler", "p_repeat_seconds": interval,
            "p_timeout_seconds": {"refresh": 900, "discovery": 1800, "retention": 900}[kind],
        })

    async def claim(self, kind: str, worker_id: str) -> Job | None:
        rows = await self._rpc("claim_background_job", {
            "p_kind": kind, "p_worker_id": worker_id, "p_lease_seconds": LEASE_SECONDS,
        })
        if not rows:
            return None
        row = rows[0]
        return Job(str(row["id"]), str(row["lease_token"]), row["kind"],
                   row["attempts"], row["payload"], row["timeout_seconds"])

    async def renew(self, job: Job) -> bool:
        return await self._rpc("renew_background_job", {
            "p_id": job.id, "p_token": job.token, "p_lease_seconds": LEASE_SECONDS,
        }) is True

    async def finish(self, job: Job, succeeded: bool, error: str | None = None) -> bool:
        return await self._rpc("finish_background_job", {
            "p_id": job.id, "p_token": job.token, "p_succeeded": succeeded,
            "p_error": error,
        }) is True

    async def reconcile(self) -> int:
        return await self._rpc("reconcile_background_jobs", {"p_limit": 100})
