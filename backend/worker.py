"""Standalone feed-refresh scheduler.

Runs as its own container from the same image as the API (see the `worker`
service in docker-compose.yml), polling the due queue on a timer. Keeping it out
of the API process means refreshes don't compete with request handling and the
API can be scaled to multiple replicas without every replica re-fetching the
same feeds.

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


async def main() -> int:
    if not refresh_enabled():
        logger.info("FEED_REFRESH_ENABLED is false — worker exiting without polling")
        return 0

    stop = asyncio.Event()
    _install_signal_handlers(stop)
    await run_forever(stop)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
