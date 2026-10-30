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
RUN_KINDS = ("refresh", "discovery", "retention", "source_review", "storage", "article_cap", "article_ttl")


def scheduler_config() -> dict:
    """What this worker process actually parsed from its own environment.

    The API container's environment can differ from the worker's, so the
    admin reads this from heartbeats instead of the API's os.environ. Values
    are booleans/integers only, never secrets. See docs/CRAWLER_OPERATIONS.md
    「Worker 監控」.
    """
    # Lazy imports: these modules are heavier and some import this one's peers.
    from services import retention
    from services.discovery_config import discovery_enabled
    from services.feed_refresh import refresh_enabled

    enabled = retention.retention_enabled()
    return {
        "refresh_enabled": refresh_enabled(),
        "discovery_deployment_enabled": discovery_enabled(),
        "retention": {
            "enabled": enabled,
            # The scheduler never previews: when enabled it compacts a batch.
            "mode": "apply" if enabled else "disabled",
            "days": retention.retention_days(),
            "batch_size": retention.batch_size(),
            "interval_minutes": retention.tick_seconds() // 60,
        },
    }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OperationsRecorder:
    def __init__(self, db, kind: str, config: dict | None = None):
        self.db = db
        self.kind = kind
        self.config = config
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
        if self.config is not None:
            row["config"] = self.config
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
    # The recent window is dominated by frequent refresh runs; a daily
    # retention or storage run needs its own lookup to stay visible.
    latest_runs = dict.fromkeys(RUN_KINDS)
    for row in db.rpc("latest_worker_runs", {"p_kinds": list(RUN_KINDS)}).execute().data or []:
        latest_runs[row["kind"]] = row
    for run in [*runs, *(r for r in latest_runs.values() if r)]:
        worker = by_worker.get(run["worker_id"])
        if run["status"] == "running" and (
            not worker or worker["stale"] or worker["status"] == "stopped"
        ):
            run["status"] = "interrupted"
    # Newest heartbeat that carries a config; an older worker image reports none.
    reporter = next((w for w in workers if w.get("config")), None)
    worker_config = None if reporter is None else {
        "worker_id": reporter["worker_id"], "hostname": reporter.get("hostname"),
        "reported_at": reporter["heartbeat_at"], "stale": reporter["stale"],
        "status": reporter["status"], "config": reporter["config"],
    }
    alerts = db.table("worker_alerts").select("*").order(
        "created_at", desc=True
    ).limit(limit).execute().data or []
    queue = db.rpc("background_queue_status", {}).execute().data
    ttl_activity = db.rpc("article_ttl_activity", {
        "p_since": (now - timedelta(hours=24)).isoformat(),
    }).execute().data
    jobs = db.table("background_jobs").select(
        "id,kind,status,attempts,max_attempts,priority,timeout_seconds,available_at,lease_expires_at,created_at,finished_at,error"
    ).order("created_at", desc=True).limit(limit).execute().data or []
    return {"alerts": alerts, "queue": queue, "recent_jobs": jobs,
            "observed_at": now.isoformat(), "stale_after_seconds": STALE_SECONDS,
            "retention_days": RETENTION_DAYS, "workers": workers, "recent_runs": runs,
            "latest_runs": latest_runs, "worker_config": worker_config,
            "article_ttl_24h": ttl_activity,
            "recent_failures": sum(r["status"] in {"failed", "partial", "interrupted"}
                                   for r in runs)}
