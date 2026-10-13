"""Reusable durable paid-request execution; no provider SDK hidden retries.

An unknown HTTP outcome keeps its full reservation. One bounded automatic retry
may send once more; after two unknown sends the request pauses for operations review.
This pause is independent of the content's optional human approval setting.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import time
from typing import Awaitable, Callable

_DB_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-receipts")
DB_TIMEOUT = 20


class RuntimeBlocked(Exception):
    def __init__(self, code: str, receipt_id: str | None = None):
        self.code, self.receipt_id = code, receipt_id
        super().__init__(code)  # never expose provider content, credentials or exception text


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RequestIdentity:
    capability: str
    model: str
    input_hash: str
    policy_hash: str
    config_hash: str

    @property
    def key(self):
        return digest(self.__dict__)


@dataclass(frozen=True)
class ProviderResponse:
    raw: str
    status: int = 200
    latency_ms: int = 0


@dataclass(frozen=True)
class ParsedResponse:
    result: dict
    usage: dict
    actual_usd: Decimal
    model: str


async def _db_call(operation):
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(loop.run_in_executor(_DB_POOL, operation), DB_TIMEOUT)


async def _rpc(db, name, params):
    return await _db_call(lambda: db.rpc(name, params).execute().data)


async def execute_paid(db, identity: RequestIdentity, *, reserved_usd: Decimal,
                       daily_budget_usd: Decimal, send: Callable[[], Awaitable[ProviderResponse]],
                       parse: Callable[[str], ParsedResponse], return_metadata: bool = False,
                       _retry_allowed: bool = True) -> dict:
    """Reserve atomically, commit raw response, then parse and commit typed result.

    Database failure fails closed. No outbound request happens before reservation.
    Input identity must exclude leases, timestamps and secrets.
    """
    reservation = await _rpc(db, "reserve_ai_request", {
        "p_logical_key": identity.key, "p_capability": identity.capability,
        "p_model": identity.model, "p_input_hash": identity.input_hash,
        "p_policy_hash": identity.policy_hash, "p_config_hash": identity.config_hash,
        "p_reserved_usd": str(reserved_usd),
        "p_minute_usd": str(min(daily_budget_usd, Decimal("0.1"))),
        "p_hour_usd": str(min(daily_budget_usd, Decimal("0.5"))),
        "p_day_usd": str(daily_budget_usd),
    })
    if reservation["action"] == "blocked":
        raise RuntimeBlocked(reservation["code"], reservation.get("receipt_id"))
    if reservation["action"] == "cached":
        receipt = reservation["receipt"]
        receipt_id, token = str(receipt["id"]), str(receipt["active_token"])
        reservation["attempts"] = receipt.get("attempts", 1)
        if receipt["status"] == "completed":
            if receipt["result"] is None:
                raise RuntimeBlocked("receipt_cache_expired", receipt_id)
            return {"result": receipt["result"], "receipt_id": receipt_id} if return_metadata else receipt["result"]
        if receipt["status"] == "failed":
            raise RuntimeBlocked(receipt.get("error_code") or "provider_failed", receipt_id)
        if receipt["status"] != "received":
            raise RuntimeBlocked("receipt_state_invalid", receipt_id)
        rows = await _db_call(lambda: db.table("ai_request_attempts").select("raw_response,http_status").eq("token", token).limit(1).execute().data)
        if not rows or rows[0]["raw_response"] is None:
            raise RuntimeBlocked("receipt_response_missing", receipt_id)
        response = ProviderResponse(rows[0]["raw_response"], rows[0]["http_status"])
    else:
        receipt_id, token = str(reservation["receipt_id"]), str(reservation["token"])
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(send(), 35)
        except BaseException as exc:
            unknown_stored = False
            # Cancellation might happen after payment. Persist uncertainty when possible.
            try:
                unknown_stored = await asyncio.shield(_rpc(db, "finish_ai_request", {
                    "p_id": receipt_id, "p_token": token, "p_status": "unknown", "p_error_code": "provider_outcome_unknown",
                }))
            except Exception:
                pass  # pending lease expires into durable unknown; never mark complete
            if isinstance(exc, asyncio.CancelledError):
                raise
            if not isinstance(exc, Exception):
                raise
            if unknown_stored is not True:
                raise RuntimeBlocked("request_fenced", receipt_id) from None
            if _retry_allowed and reservation.get("attempts", 1) < 2:
                await asyncio.sleep(0.5)
                return await execute_paid(db, identity, reserved_usd=reserved_usd,
                    daily_budget_usd=daily_budget_usd, send=send, parse=parse,
                    return_metadata=return_metadata, _retry_allowed=False)
            code = "request_unknown_paused" if reservation.get("attempts", 0) >= 2 else "provider_outcome_unknown"
            raise RuntimeBlocked(code, receipt_id) from None
        latency = response.latency_ms or round((time.monotonic() - started) * 1000)
        stored = await _rpc(db, "receive_ai_response", {
            "p_id": receipt_id, "p_token": token, "p_raw": response.raw,
            "p_http_status": response.status, "p_latency_ms": latency,
        })
        if stored is not True:
            raise RuntimeBlocked("request_fenced", receipt_id)
    if response.status != 200:
        retryable = response.status == 429 or 500 <= response.status <= 599
        code = "provider_http_retryable" if retryable else "provider_http_error"
        finished = await _rpc(db, "finish_ai_request", {"p_id": receipt_id, "p_token": token,
                   "p_status": "failed", "p_error_code": code})
        if finished is not True:
            raise RuntimeBlocked("request_fenced", receipt_id)
        if retryable and _retry_allowed and reservation.get("attempts", 1) < 2:
            await asyncio.sleep(0.5)
            return await execute_paid(db, identity, reserved_usd=reserved_usd,
                daily_budget_usd=daily_budget_usd, send=send, parse=parse,
                    return_metadata=return_metadata, _retry_allowed=False)
        raise RuntimeBlocked(code, receipt_id)
    try:
        parsed = parse(response.raw)
    except Exception:
        finished = await _rpc(db, "finish_ai_request", {"p_id": receipt_id, "p_token": token,
                   "p_status": "failed", "p_error_code": "provider_invalid_response"})
        if finished is not True:
            raise RuntimeBlocked("request_fenced", receipt_id) from None
        raise RuntimeBlocked("provider_invalid_response", receipt_id) from None
    finished = await _rpc(db, "finish_ai_request", {
        "p_id": receipt_id, "p_token": token, "p_status": "completed", "p_result": parsed.result,
        "p_usage": parsed.usage, "p_actual_usd": str(parsed.actual_usd), "p_actual_model": parsed.model,
    })
    if finished is not True:
        raise RuntimeBlocked("request_fenced", receipt_id)
    return {"result": parsed.result, "receipt_id": receipt_id} if return_metadata else parsed.result


async def prune_receipts(db):
    """Call from bounded housekeeping; cache retention does not include unknowns."""
    return await _rpc(db, "prune_ai_receipts", {"p_limit": 100})


async def ai_usage_summary(db, limit=50):
    """Admin-only billing/unknown summary without payloads or input content."""
    return await _rpc(db, "ai_usage_summary", {"p_limit": max(1, min(int(limit), 100))})
