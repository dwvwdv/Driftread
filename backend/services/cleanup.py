"""Bounded administrator housekeeping shared by REST and management MCP."""
import logging
import os
from typing import Literal
from uuid import UUID

import psycopg2
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

Kind = Literal['feeds', 'articles']
Rule = Literal['all', 'category', 'host', 'title', 'url', 'content']
State = Literal['active', 'staged', 'all']


class CleanupBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Kind
    ids: list[UUID] = Field(min_length=1, max_length=50)
    action: Literal['stage', 'restore', 'delete']
    dry_run: bool = True
    confirm_delete: bool = False

    @model_validator(mode='after')
    def deletion_is_explicit(self):
        if self.action == 'delete' and not self.dry_run and not self.confirm_delete:
            raise ValueError('Deleting requires confirm_delete=true')
        return self


class RemoveTargets(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ids: list[UUID] = Field(min_length=1, max_length=50)


def list_items(db, kind: Kind, rule: Rule = 'all', state: State = 'active',
               query: str = '', limit: int = 50, offset: int = 0) -> dict:
    return db.rpc('list_cleanup_items', {
        'p_kind': kind, 'p_rule': rule, 'p_state': state, 'p_query': query,
        'p_limit': limit, 'p_offset': offset,
    }).execute().data


def apply_batch(db, body: CleanupBatch) -> dict:
    if body.action == 'delete' and not body.dry_run:
        return _delete_batch(body)
    return db.rpc('apply_cleanup_batch', {
        'p_kind': body.kind, 'p_ids': list(dict.fromkeys(map(str, body.ids))),
        'p_action': body.action, 'p_dry_run': body.dry_run,
        'p_confirm_delete': body.confirm_delete,
    }).execute().data


def _delete_batch(body: CleanupBatch) -> dict:
    # A feed can cascade to thousands of articles/revisions. Keep the same RPC
    # and atomic transaction, without PostgREST's short request timeout.
    dsn = os.getenv('DATABASE_URL', '').strip()
    if not dsn:
        raise HTTPException(503, '永久刪除需要設定資料庫連線')
    conn = None
    try:
        conn = psycopg2.connect(dsn, connect_timeout=5)
        with conn:
            with conn.cursor() as cur:
                # Match the Data API role instead of using migration privileges.
                cur.execute('SET LOCAL ROLE service_role')
                cur.execute("SET LOCAL statement_timeout = '45s'")
                cur.execute("SET LOCAL lock_timeout = '3s'")
                cur.execute(
                    'SELECT driftread.apply_cleanup_batch(%s,%s::uuid[],%s,%s,%s)',
                    (body.kind, list(dict.fromkeys(map(str, body.ids))),
                     body.action, body.dry_run, body.confirm_delete),
                )
                result = cur.fetchone()[0]
        return result
    except psycopg2.Error as exc:
        # SQLSTATE is safe to log; exception text can contain connection secrets.
        logger.error('Cleanup deletion database operation failed (sqlstate=%s)', exc.pgcode)
        if exc.pgcode in ('57014', '55P03', '40P01'):
            raise HTTPException(503, '批量刪除逾時或資料使用中，請減少每批數量後重試') from None
        raise HTTPException(503, '批量刪除暫時無法完成，請稍後重試') from None
    finally:
        if conn is not None:
            conn.close()


def list_pending_targets(db, limit: int = 50, offset: int = 0) -> dict:
    result = (db.table('discovery_targets').select('*', count='exact')
              .eq('status', 'pending').order('referring_feed_count', desc=True)
              .order('next_probe_at').order('id').range(offset, offset + limit - 1).execute())
    return {'items': result.data or [], 'total': result.count or 0, 'limit': limit, 'offset': offset}


def remove_targets(db, ids: list[UUID]) -> dict:
    return db.rpc('remove_discovery_targets', {
        'p_ids': list(dict.fromkeys(map(str, ids))),
    }).execute().data
