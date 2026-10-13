"""Source review orchestration; all decision mutations are fenced SQL transactions.

Packets contain bounded, untrusted RSS snippets, never public preview articles.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from rss_parser import parse_feed, _plain_text
from services import robots
from services.discovery_candidates import get_candidate, sanitize_text, sanitize_http_url
from services.discovery_config import discovery_enabled, host_delay_seconds
from services.feed_discovery import (
    DiscoveryError, MAX_FEED_BYTES, fetch_with_cap, ssrf_safe_client, user_agent,
)
from services.link_harvest import is_denied_host, normalize_host
from services.review_models import ReviewSubmission
from services.settings import get_setting
from services.source_identity import is_comment_feed

logger = logging.getLogger(__name__)


class ReviewError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _rpc(db, name: str, params: dict) -> dict | None:
    result = db.rpc(name, params).execute()
    data = getattr(result, 'data', None)
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict) and data.get('error'):
        code = data['error']
        raise ReviewError(404 if code == 'not_found' else 503 if code == 'disabled' else 409,
                          {'not_found': 'Review or candidate not found', 'disabled': 'Source review is disabled',
                           'manual_decision': 'A manual decision already controls this candidate',
                           'lease': 'Review lease or snapshot is no longer valid',
                           'already_submitted': 'A different result was already submitted'}.get(code, 'Review conflict'))
    return data


def _summary(row: dict, candidate: dict | None = None) -> dict:
    return {key: value for key, value in row.items() if key not in {'snapshot', 'lease_token', 'policy', 'lease_expires_at'}} | {
        'manual_override': (candidate or {}).get('manual_override'),
    }


def list_reviews(db, candidate_id=None, limit=50) -> list[dict]:
    query = db.table('source_reviews').select('*')
    if candidate_id is not None:
        query = query.eq('candidate_id', str(candidate_id))
    rows = query.order('created_at', desc=True).limit(max(1, min(limit, 100))).execute().data or []
    candidates = {}
    for row in rows:
        cid = row['candidate_id']
        if cid not in candidates:
            candidates[cid] = get_candidate(db, cid)
    return [_summary(row, candidates[row['candidate_id']]) for row in rows]


def request_review(db, candidate_id, *, retry=False) -> dict:
    candidate = get_candidate(db, str(candidate_id))
    if candidate and is_comment_feed(candidate['feed_url']):
        raise ReviewError(409, 'Comment feeds are excluded from article discovery')
    row = _rpc(db, 'request_source_review', {'p_candidate_id': str(candidate_id), 'p_retry': retry})
    if not row:
        raise ReviewError(409, 'Review could not be requested')
    return _summary(row, candidate)


def _packet(row: dict, candidate: dict | None, *, expose_lease: bool = False) -> dict:
    packet = _summary(row, candidate)
    snapshot = row.get('snapshot') or {}
    expiry = row.get('snapshot_expires_at')
    if expiry and datetime.fromisoformat(expiry.replace('Z', '+00:00')) <= datetime.now(timezone.utc):
        snapshot = {}
    packet.update({'policy': row['policy'], 'rubric_hash': _rubric_hash(row['policy']), 'source': snapshot.get('source', {}),
                   'samples': snapshot.get('samples', []), 'snapshot_expired': not bool(snapshot)})
    if expose_lease:
        packet.update(lease_token=row.get('lease_token'), lease_expires_at=row.get('lease_expires_at'))
    return packet


def claim_review(db, reviewer_id='external_agent') -> dict | None:
    if reviewer_id not in {'external_agent', 'jev'}:
        raise ReviewError(403, 'Unknown review engine')
    row = _rpc(db, 'claim_source_review', {'p_reviewer_id': reviewer_id})
    return _packet(row, get_candidate(db, row['candidate_id']), expose_lease=True) if row else None


def get_review_packet(db, review_id, *, reviewer_id=None) -> dict:
    result = db.table('source_reviews').select('*').eq('id', str(review_id)).maybe_single().execute()
    row = getattr(result, 'data', None) if result is not None else None
    if not row:
        raise ReviewError(404, 'Review not found')
    if reviewer_id is not None and (row['engine'] != reviewer_id or row.get('reviewer_id') not in {None, reviewer_id}):
        raise ReviewError(403, 'Review belongs to another engine')
    # Read-only packet retrieval never issues a lease; only claim returns tokens.
    return _packet(row, get_candidate(db, row['candidate_id']))


def _rubric_hash(policy: dict) -> str:
    rubric = {key: policy.get(key) for key in ('policy_text', 'min_confidence', 'min_quality', 'max_spam_probability', 'sample_size')}
    rubric['min_relevance'] = 0.5
    return hashlib.sha256(json.dumps(rubric, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def submit_review(db, review_id, body, reviewer_id='external_agent', *, analysis_metadata=None) -> dict:
    if reviewer_id not in {'external_agent', 'jev'}:
        raise ReviewError(403, 'Unknown review engine')
    try:
        submission = body if isinstance(body, ReviewSubmission) else ReviewSubmission.model_validate(body)
    except ValidationError as exc:
        raise ReviewError(422, 'Invalid review submission') from exc
    payload = submission.model_dump(mode='json', exclude={'lease_token', 'snapshot_hash'})
    payload['rationale'] = sanitize_text(payload['rationale'], 2000)
    payload['category'] = sanitize_text(payload['category'], 100)
    payload['tags'] = [text for item in payload['tags'] if (text := sanitize_text(item, 100))]
    payload['reason_codes'] = [text for item in payload['reason_codes'] if (text := sanitize_text(item, 100))]
    # Machine DTOs cannot submit this field. Only trusted server adapters add
    # receipt identifiers; arbitrary provider payloads and secrets are excluded.
    policy_result = db.table('source_reviews').select('policy').eq('id', str(review_id)).maybe_single().execute()
    policy_row = getattr(policy_result, 'data', None) if policy_result else None
    if not policy_row:
        raise ReviewError(404, 'Review not found')
    metadata = {'rubric_hash': _rubric_hash(policy_row['policy'])}
    for key, value in (analysis_metadata or {}).items():
        if key == 'receipt_id':
            try:
                metadata[key] = str(UUID(value))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ReviewError(422, 'Invalid server analysis metadata') from exc
        elif key in {'provider_prompt_hash', 'provider_input_hash'}:
            if not isinstance(value, str) or len(value) != 64 or any(ch not in '0123456789abcdef' for ch in value):
                raise ReviewError(422, 'Invalid server analysis metadata')
            metadata[key] = value
        elif key == 'adapter_version':
            if not isinstance(value, str) or len(value) > 64:
                raise ReviewError(422, 'Invalid server analysis metadata')
            metadata[key] = sanitize_text(value, 64)
    payload['analysis_metadata'] = metadata
    row = _rpc(db, 'submit_source_review', {
        'p_review_id': str(review_id), 'p_reviewer_id': reviewer_id,
        'p_token': str(submission.lease_token), 'p_hash': submission.snapshot_hash, 'p_result': payload,
    })
    if not row:
        raise ReviewError(409, 'Review could not be submitted')
    return _summary(row, get_candidate(db, row['candidate_id']))


def resolve_review(db, review_id, decision: Literal['accept', 'reject', 'defer']) -> dict:
    if decision not in {'accept', 'reject', 'defer'}:
        raise ReviewError(422, 'Invalid resolution')
    row = _rpc(db, 'resolve_source_review', {'p_review_id': str(review_id), 'p_decision': decision})
    if not row:
        raise ReviewError(409, 'Review could not be resolved')
    return _summary(row, get_candidate(db, row['candidate_id']))


def _crawl_database_allowed(db, candidate: dict, host: str, review: dict) -> bool:
    """Recheck operator decisions before every HTTP hop, including redirects."""
    setting = get_setting(db, 'source.review')
    if not setting.value['enabled'] or setting.version != review['policy_version']:
        return False
    current = get_candidate(db, candidate['id'])
    if (not current or current['status'] != 'pending' or current.get('manual_override')
            or current['review_version'] != review['candidate_version']):
        return False
    if _rpc(db, 'source_review_candidate_blocked', {'p_candidate_id': str(candidate['id'])}):
        return False
    blocked = db.table('discovery_targets').select('id').eq('host', host).in_(
        'status', ['rejected', 'blocked']).limit(1).execute().data
    if blocked:
        return False
    row = db.table('source_reviews').select('status,lease_token,lease_expires_at').eq(
        'id', review['id']).maybe_single().execute()
    task = getattr(row, 'data', None) if row else None
    return bool(task and task['status'] == 'preparing' and task['lease_token'] == review['lease_token']
                and datetime.fromisoformat(task['lease_expires_at'].replace('Z', '+00:00')) > datetime.now(timezone.utc))


async def _fetch_snapshot(candidate: dict, sample_size: int, *, db=None, review=None) -> dict:
    ua = user_agent()

    async def gate(url: str) -> bool:
        host = normalize_host(url)
        if not host or is_denied_host(host):
            return False
        if db is not None and not await asyncio.to_thread(_crawl_database_allowed, db, candidate, host, review):
            return False
        decision = await robots.check(url, ua)
        if not decision.allowed:
            return False
        await asyncio.sleep(min(robots.MAX_CRAWL_DELAY_SECONDS,
                                max(host_delay_seconds(), decision.crawl_delay or 0)))
        return True

    async with ssrf_safe_client(follow_redirects=False, timeout=15, headers={'User-Agent': ua}) as client:
        text, _ctype = await fetch_with_cap(client, candidate['feed_url'], MAX_FEED_BYTES, allow_url=gate)
    parsed = parse_feed(text)
    samples = []
    for index, article in enumerate(parsed.articles[:sample_size]):
        raw = article.content or article.summary or ''
        snippet = sanitize_text(_plain_text(raw), 2000) or ''
        samples.append({'evidence_id': f'rss:{index}', 'title': sanitize_text(article.title, 200) or '',
                        'url': sanitize_http_url(article.url), 'text': snippet,
                        'author': sanitize_text(article.author, 200),
                        'published_at': article.published_at.isoformat() if article.published_at else None})
    packet = {'source': {'candidate_id': str(candidate['id']), 'feed_url': candidate['feed_url'],
                       'title': sanitize_text(parsed.title, 200), 'description': sanitize_text(parsed.description, 1000),
                       'website_url': sanitize_http_url(parsed.website_url), 'language': sanitize_text(parsed.language, 35),
                       'source_host': candidate.get('source_host'),
                       'referring_feed_count': candidate.get('referring_feed_count', 0)}, 'samples': samples}
    while len(json.dumps(packet, ensure_ascii=False).encode()) > 90000 and packet['samples']:
        packet['samples'].pop()
    return packet


async def prepare_due_reviews(db, limit=10) -> dict:
    limit = max(1, min(limit, 10))
    await asyncio.to_thread(_rpc, db, 'prune_source_reviews', {})
    settings = await asyncio.to_thread(get_setting, db, 'source.review')
    counts = {'requested': 0, 'prepared': 0, 'failed': 0, 'submitted': 0}
    if not settings.value['enabled'] or not discovery_enabled():
        return counts
    requested = await asyncio.to_thread(_rpc, db, 'request_due_source_reviews', {'p_limit': limit})
    counts['requested'] = requested['requested'] if requested else 0
    evaluated = 0
    for _ in range(limit):
        row = await asyncio.to_thread(_rpc, db, 'claim_source_review', {'p_reviewer_id': 'preparer', 'p_preparing': True})
        if not row:
            break
        candidate = await asyncio.to_thread(get_candidate, db, row['candidate_id'])
        try:
            snapshot = await asyncio.wait_for(_fetch_snapshot(candidate, row['policy']['sample_size'], db=db, review=row), 90)
            digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            ready = await asyncio.to_thread(_rpc, db, 'prepare_source_review', {
                'p_review_id': row['id'], 'p_token': row['lease_token'], 'p_snapshot': snapshot, 'p_hash': digest,
            })
            counts['prepared'] += 1
        except Exception as exc:
            logger.warning('Source review preparation failed: %s', type(exc).__name__)
            await asyncio.to_thread(_rpc, db, 'prepare_source_review', {
                'p_review_id': row['id'], 'p_token': row['lease_token'], 'p_snapshot': None,
                'p_hash': None, 'p_error': 'sample_fetch_failed',
            })
            counts['failed'] += 1
            continue
        # External tasks remain ready for a separately authenticated agent.
        if ready and ready['status'] == 'ready' and ready['engine'] == 'jev':
            if await _run_jev_review(db, ready['id'], counts):
                evaluated += 1
    # Ready Jev tasks survive process interruption between preparation and call.
    for _ in range(max(0, limit - evaluated)):
        if not await _run_jev_review(db, None, counts):
            break
    return counts


async def _run_jev_review(db, review_id, counts) -> bool:
    from services.review_provider import evaluate_jev
    from services.settings import load_source_review_settings
    row = await asyncio.to_thread(_rpc, db, 'claim_source_review', {
        'p_reviewer_id': 'jev', 'p_review_id': review_id,
    })
    if not row:
        return False
    packet = _packet(row, await asyncio.to_thread(get_candidate, db, row['candidate_id']), expose_lease=True)
    settings = await asyncio.to_thread(load_source_review_settings, db)
    try:
        result = await evaluate_jev(db, packet, settings)
        metadata = result.pop('_analysis_metadata', {})
        await asyncio.to_thread(submit_review, db, row['id'], {
            **result, 'lease_token': row['lease_token'], 'snapshot_hash': row['snapshot_hash'],
        }, 'jev', analysis_metadata=metadata)
        counts['submitted'] += 1
    except Exception as exc:
        logger.warning('Source review provider failed: %s', type(exc).__name__)
        # Fence the failure too; no raw provider exception or secret is persisted.
        await asyncio.to_thread(_rpc, db, 'fail_source_review', {
            'p_review_id': row['id'], 'p_token': row['lease_token'],
            'p_error': getattr(exc, 'code', 'provider_failed'),
        })
        counts['failed'] += 1
    return True
