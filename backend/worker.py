"""Standalone background scheduler.

Runs as its own container from the same image as the API (see the `worker`
service in docker-compose.yml). Keeping it out of the API process means its work
doesn't compete with request handling and the API can be scaled to multiple
replicas without every replica re-fetching the same feeds.

Three scheduler loops share one event loop and one stop signal:

- **refresh** polls the due queue so imported feeds keep getting new articles;
- **discovery** mines the article corpus for outbound links, probes the resulting
  hosts for feeds, and fills the review queue;
- **retention**, opt-in only, compacts a bounded batch of eligible old bodies.

Network waits yield to other loops and a separate heartbeat task. Synchronous
ingestion database calls and bounded HTML parsing can still briefly block this
shared event loop; the heartbeat does not claim isolation from those bursts.

Deliberately does not run migrations — the API container does that on startup,
and the worker waits for it via compose's `depends_on: service_healthy`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from dataclasses import asdict

from database import get_client
from services.discovery import run_cycle
from services.discovery_config import discovery_enabled
from services.discovery_config import tick_seconds as discovery_tick_seconds
from services.feed_refresh import refresh_due, refresh_enabled, summarize, tick_seconds
from services.operations import OperationsRecorder
from services.job_queue import JobQueue, POLL_SECONDS, RENEW_SECONDS
from services.retention import (
    retention_enabled, scheduled_compaction, tick_seconds as retention_tick_seconds,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("driftread.worker")
SHUTDOWN_SECONDS = 20


class LeaseLost(RuntimeError):
    """The action must not acknowledge or renew a job it no longer owns."""


async def _leased_action(queue, job, action, db):
    task = asyncio.create_task(action(db))

    async def renew():
        while True:
            await asyncio.sleep(RENEW_SECONDS)
            try:
                renewed = await queue.renew(job)
            except Exception:
                # A failed renew leaves ownership uncertain. Do not immediately
                # retry work that a cancelled database thread may still finish.
                raise LeaseLost() from None
            if not renewed:
                raise LeaseLost()

    renewal = asyncio.create_task(renew())
    try:
        ready, _ = await asyncio.wait({task, renewal}, timeout=job.timeout_seconds,
                                    return_when=asyncio.FIRST_COMPLETED)
        if not ready:
            raise asyncio.TimeoutError()
        if renewal in ready:
            # Includes a database outage: continue only while ownership is known.
            await renewal
        return await task
    finally:
        task.cancel()
        renewal.cancel()
        await asyncio.gather(task, renewal, return_exceptions=True)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - non-Unix platforms
            signal.signal(sig, lambda *_: stop.set())


async def _run_loop(stop: asyncio.Event, kind: str, interval: int, action, summary_of) -> None:
    db = get_client()
    recorder = OperationsRecorder(db, kind)
    queue = JobQueue(db)
    done = asyncio.Event()
    pulse = asyncio.create_task(recorder.pulse(done))
    logger.info("%s worker started (tick=%ds)", kind, interval)
    try:
        while not stop.is_set():
            try:
                await queue.reconcile()
                if stop.is_set():
                    break
                await queue.enqueue(kind, interval)
                if stop.is_set():
                    break
                job = await queue.claim(kind, recorder.worker_id)
            except Exception:
                logger.warning("%s queue unavailable; no work started", kind)
                job = None
            if job is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass
                continue
            if stop.is_set():
                # The claim can commit concurrently with SIGTERM. Do not start
                # new work; let its lease expire and the sweep return it safely.
                break
            run_id = await recorder.begin()
            try:
                result = await _leased_action(queue, job, action, db)
                summary = summary_of(result)
                if kind == "refresh":
                    failed = summary.get("failed", 0)
                    errors = []
                elif kind == "discovery":
                    failed = sum(summary.get(stage, {}).get("failed", 0)
                                 for stage in ("directory", "harvest", "probe"))
                    errors = summary.get("errors", [])
                else:  # Retention RPC errors raise; successful bounded writes have no stages.
                    failed = 0
                    errors = []
                await recorder.finish(run_id, "partial" if failed or errors else "succeeded",
                                      summary, "; ".join(errors) or None)
                # Partial results already contain domain-level retry scheduling.
                # Retrying the entire cycle here would count failures twice.
                if not await queue.finish(job, True):
                    logger.warning("%s job acknowledgement rejected", kind)
                logger.info("%s cycle: %s", kind, summary)
            except asyncio.CancelledError:
                await recorder.finish(run_id, "cancelled")
                raise
            except LeaseLost:
                await recorder.finish(run_id, "cancelled", error="LeaseLost")
                logger.warning("%s job lease lost; acknowledgement skipped", kind)
            except Exception as exc:
                await recorder.finish(run_id, "failed", error=type(exc).__name__)
                # Persist only the exception class, never external URLs/secrets.
                logger.error("%s cycle failed (%s)", kind, type(exc).__name__)
                try:
                    await queue.finish(job, False, type(exc).__name__)
                except Exception:
                    logger.warning("%s job acknowledgement unavailable", kind)
            try:
                await asyncio.wait_for(stop.wait(), timeout=min(interval, POLL_SECONDS))
            except asyncio.TimeoutError:
                pass
    finally:
        done.set()
        try:
            await asyncio.wait_for(pulse, timeout=5)
        except asyncio.TimeoutError:
            logger.warning("%s final heartbeat timed out", kind)
        logger.info("%s worker stopped", kind)


async def run_forever(stop: asyncio.Event | None = None) -> None:
    """Poll due feeds while persisting cycle outcomes and an independent heartbeat."""
    await _run_loop(stop or asyncio.Event(), "refresh", tick_seconds(), refresh_due, summarize)


async def run_discovery_forever(stop: asyncio.Event | None = None) -> None:
    """Discovery is independent of refresh, including its ledger and heartbeat."""
    await _run_loop(stop or asyncio.Event(), "discovery", discovery_tick_seconds(), run_cycle, asdict)


async def run_retention_forever(stop: asyncio.Event | None = None) -> None:
    """Opt-in bounded compaction uses its own timer and ledger, like refresh."""
    await _run_loop(
        stop or asyncio.Event(), "retention", retention_tick_seconds(),
        scheduled_compaction, lambda result: result.model_dump() if result else {},
    )


async def main() -> int:
    if not refresh_enabled() and not discovery_enabled() and not retention_enabled():
        logger.info(
            "Refresh, discovery and article retention are disabled — "
            "worker exiting without polling"
        )
        return 0

    stop = asyncio.Event()
    _install_signal_handlers(stop)

    # get_client() is called inside each loop, not here, so the fully-disabled
    # path above still constructs no Supabase client — which is why a deployment
    # with both schedulers off doesn't need SUPABASE_* set at all.
    loops = []
    if refresh_enabled():
        loops.append(run_forever(stop))
    if discovery_enabled():
        loops.append(run_discovery_forever(stop))
    if retention_enabled():
        loops.append(run_retention_forever(stop))

    # return_exceptions=True is load-bearing: a bare gather propagates the first
    # exception and leaves the sibling task running and unawaited — an orphaned
    # loop inside a process that thinks it is dying.
    tasks = [asyncio.create_task(loop) for loop in loops]
    def stop_on_failure(task):
        if not task.cancelled() and task.exception() is not None:
            stop.set()
    for task in tasks:
        task.add_done_callback(stop_on_failure)
    settled_task = asyncio.gather(*tasks, return_exceptions=True)
    stopping = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({settled_task, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if stopping.done() and not settled_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(settled_task), SHUTDOWN_SECONDS)
            except asyncio.TimeoutError:
                logger.warning("Worker drain timed out; unfinished leases left for recovery")
                for task in tasks:
                    task.cancel()
        settled = await settled_task
    finally:
        stopping.cancel()
        await asyncio.gather(stopping, return_exceptions=True)
    failures = [r for r in settled if isinstance(r, BaseException)
                and not isinstance(r, asyncio.CancelledError)]
    for exc in failures:
        logger.error("Worker loop terminated", exc_info=exc)

    # Non-zero when a loop died, so compose's `restart: on-failure` recovers it.
    # A failed *cycle* never reaches here — each loop absorbs those itself.
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
