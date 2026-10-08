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
from services.retention import (
    retention_enabled, scheduled_compaction, tick_seconds as retention_tick_seconds,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("driftread.worker")


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
    done = asyncio.Event()
    pulse = asyncio.create_task(recorder.pulse(done))
    logger.info("%s worker started (tick=%ds)", kind, interval)
    try:
        while not stop.is_set():
            run_id = await recorder.begin()
            try:
                result = await action(db)
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
                logger.info("%s cycle: %s", kind, summary)
            except asyncio.CancelledError:
                await recorder.finish(run_id, "cancelled")
                raise
            except Exception as exc:
                await recorder.finish(run_id, "failed", error=type(exc).__name__)
                logger.exception("%s cycle failed", kind)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    finally:
        done.set()
        await pulse
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
    settled = await asyncio.gather(*loops, return_exceptions=True)
    failures = [r for r in settled if isinstance(r, BaseException)]
    for exc in failures:
        logger.error("Worker loop terminated", exc_info=exc)

    # Non-zero when a loop died, so compose's `restart: on-failure` recovers it.
    # A failed *cycle* never reaches here — each loop absorbs those itself.
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
