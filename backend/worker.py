"""Standalone background scheduler.

Runs as its own container from the same image as the API (see the `worker`
service in docker-compose.yml). Keeping it out of the API process means its work
doesn't compete with request handling and the API can be scaled to multiple
replicas without every replica re-fetching the same feeds.

Two independent loops share one event loop and one stop signal:

- **refresh** polls the due queue so imported feeds keep getting new articles;
- **discovery** mines the article corpus for outbound links, probes the resulting
  hosts for feeds, and fills the review queue.

They coexist safely because every wait in both is `await asyncio.sleep` — a slow
discovery cycle never delays a refresh tick. The one shared-loop hazard is the
CPU-bound HTML parsing in link_harvest, which is bounded there (SoupStrainer, a
512 KiB slice per article, a yield between articles).

Deliberately does not run migrations — the API container does that on startup,
and the worker waits for it via compose's `depends_on: service_healthy`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from database import get_client
from services.discovery import run_cycle
from services.discovery_config import discovery_enabled
from services.discovery_config import tick_seconds as discovery_tick_seconds
from services.feed_refresh import refresh_due, refresh_enabled, summarize, tick_seconds

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


async def run_forever(stop: asyncio.Event | None = None) -> None:
    """Poll the due queue until signalled to stop."""
    stop = stop or asyncio.Event()
    interval = tick_seconds()
    db = get_client()
    logger.info("Feed refresh worker started (tick=%ds)", interval)

    while not stop.is_set():
        try:
            results = await refresh_due(db)
            if results:
                logger.info("Refresh cycle: %s", summarize(results))
            else:
                logger.debug("Refresh cycle: no feeds due")
        except Exception:
            # A cycle must never kill the process: restarting the container on a
            # transient Supabase blip would just loop, and compose's restart
            # policy gives no backoff. Log and wait for the next tick.
            logger.exception("Refresh cycle failed")

        # wait_for on the stop event rather than plain sleep, so SIGTERM takes
        # effect immediately instead of after up to a full tick.
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Feed refresh worker stopped")


async def run_discovery_forever(stop: asyncio.Event | None = None) -> None:
    """Run a discovery cycle on a timer until signalled to stop."""
    stop = stop or asyncio.Event()
    interval = discovery_tick_seconds()
    db = get_client()
    logger.info("Discovery worker started (tick=%ds)", interval)

    while not stop.is_set():
        try:
            summary = await run_cycle(db)
            logger.info(
                "Discovery cycle: directory=%s harvest=%s probe=%s "
                "auto_promoted=%d imported=%d",
                summary.directory, summary.harvest, summary.probe,
                summary.auto_promoted, summary.imported,
            )
        except Exception:
            # Same reasoning as the refresh loop: a cycle must never kill the
            # process, because compose's restart policy gives no backoff.
            logger.exception("Discovery cycle failed")

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Discovery worker stopped")


async def main() -> int:
    if not refresh_enabled() and not discovery_enabled():
        logger.info(
            "FEED_REFRESH_ENABLED and FEED_DISCOVERY_ENABLED are both false — "
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
