"""API process observes durable incidents; no outbound notifications."""
from __future__ import annotations
import asyncio
import logging
from database import get_client

logger = logging.getLogger(__name__)
WATCHDOG_SECONDS = 30


async def prune_review_payloads(db) -> None:
    """Bounded, local housekeeping continues when review/crawling is disabled."""
    # Keep sweeps independent: one unavailable RPC must not suppress the other.
    for name, params in (("prune_source_reviews", {}), ("prune_ai_receipts", {"p_limit": 100})):
        try:
            await asyncio.to_thread(lambda: db.rpc(name, params).execute())
        except Exception as exc:
            logger.warning('Review payload cleanup unavailable (%s)', type(exc).__name__)


async def run_watchdog(stop: asyncio.Event) -> None:
    # Delay the first bounded sweep so API startup/health never depends on it.
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), WATCHDOG_SECONDS)
            break
        except asyncio.TimeoutError:
            pass
        try:
            result = await asyncio.to_thread(
                lambda: get_client().rpc('watchdog_worker_operations', {}).execute().data
            )
            if any(result.get(key, 0) for key in ('new_alerts', 'recovered', 'interrupted', 'reconciled')):
                logger.warning('Worker watchdog: new_alerts=%d recovered=%d interrupted=%d reconciled=%d',
                               result['new_alerts'], result['recovered'], result['interrupted'], result['reconciled'])
        except Exception as exc:
            logger.warning('Worker watchdog unavailable (%s)', type(exc).__name__)
        if not stop.is_set():
            try:
                await prune_review_payloads(get_client())
            except Exception as exc:
                logger.warning('Review housekeeping unavailable (%s)', type(exc).__name__)
