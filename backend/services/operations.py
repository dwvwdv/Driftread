"""Private, best-effort worker ledger. Database I/O never blocks the event loop."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 30
STALE_SECONDS = 90
RETENTION_DAYS = 30
PRUNE_SECONDS = 3600


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OperationsRecorder:
    def __init__(self, db, kind: str):
        self.db = db
        self.kind = kind
        self.worker_id = str(uuid4())
        self.started_at = utcnow().isoformat()
        self.active_run: str | None = None
        self._last_prune: datetime | None = None

    async def _write(self, action) -> bool:
        try:
            await asyncio.to_thread(action)
            return True
        except Exception:
            # Missing migration or a temporary database outage must not disable
            # feed fetching. Failed telemetry is visible in worker logs.
            logger.exception("Worker operations write failed (%s)", self.kind)
            return False

    async def heartbeat(self, status: str = "running") -> None:
        row = {
            "worker_id": self.worker_id,
            "hostname": socket.gethostname(),
            "process_id": os.getpid(),
            "started_at": self.started_at,
            "heartbeat_at": utcnow().isoformat(),
            "status": status,
            "active_runs": {self.kind: self.active_run} if self.active_run else {},
        }
        await self._write(lambda: self.db.table("worker_heartbeats").upsert(
            row, on_conflict="worker_id"
        ).execute())

    async def pulse(self, done: asyncio.Event) -> None:
        """Runs separately from cycles, including while a cycle awaits network I/O."""
        while not done.is_set():
            await self.heartbeat()
            now = utcnow()
            if self._last_prune is None or (now - self._last_prune).total_seconds() >= PRUNE_SECONDS:
                self._last_prune = now
                await self._write(lambda: self.db.rpc("prune_worker_operations", {
                    "p_before": (now - timedelta(days=RETENTION_DAYS)).isoformat()
                }).execute())
            try:
                await asyncio.wait_for(done.wait(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                pass
        await self.heartbeat("stopped")

    async def begin(self) -> str:
        run_id = str(uuid4())
        self.active_run = run_id
        await self._write(lambda: self.db.table("worker_runs").insert({
            "id": run_id, "worker_id": self.worker_id, "kind": self.kind,
            "status": "running", "started_at": utcnow().isoformat(), "summary": {},
        }).execute())
        await self.heartbeat()
        return run_id

    async def finish(self, run_id: str, status: str, summary: dict | None = None,
                     error: str | None = None) -> None:
        await self._write(lambda: self.db.table("worker_runs").update({
            "status": status, "finished_at": utcnow().isoformat(),
            "summary": summary or {},
            # Store the exception type/stage, never URLs, credentials or bodies.
            "error": error[:200] if error else None,
        }).eq("id", run_id).execute())
        self.active_run = None
        await self.heartbeat()


def _age_seconds(value: str, now: datetime) -> float:
    return max(0, (now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds())


def operations_status(db, limit: int = 20) -> dict:
    now = utcnow()
    workers = db.table("worker_heartbeats").select("*").gte(
        "heartbeat_at", (now - timedelta(days=RETENTION_DAYS)).isoformat()
    ).order("heartbeat_at", desc=True).limit(200).execute().data or []
    runs = db.table("worker_runs").select("*").order(
        "started_at", desc=True
    ).limit(limit).execute().data or []
    by_worker = {}
    for worker in workers:
        worker["age_seconds"] = round(_age_seconds(worker["heartbeat_at"], now), 1)
        worker["stale"] = worker["age_seconds"] > STALE_SECONDS
        by_worker[worker["worker_id"]] = worker
    for run in runs:
        worker = by_worker.get(run["worker_id"])
        if run["status"] == "running" and (
            not worker or worker["stale"] or worker["status"] == "stopped"
        ):
            run["status"] = "interrupted"
    return {"observed_at": now.isoformat(), "stale_after_seconds": STALE_SECONDS,
            "retention_days": RETENTION_DAYS, "workers": workers, "recent_runs": runs,
            "recent_failures": sum(r["status"] in {"failed", "partial", "interrupted"}
                                   for r in runs)}
