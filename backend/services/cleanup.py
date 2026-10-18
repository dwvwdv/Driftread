"""Bounded administrator housekeeping shared by REST and management MCP."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    return db.rpc('apply_cleanup_batch', {
        'p_kind': body.kind, 'p_ids': list(dict.fromkeys(map(str, body.ids))),
        'p_action': body.action, 'p_dry_run': body.dry_run,
        'p_confirm_delete': body.confirm_delete,
    }).execute().data


def list_pending_targets(db, limit: int = 50, offset: int = 0) -> dict:
    result = (db.table('discovery_targets').select('*', count='exact')
              .eq('status', 'pending').order('referring_feed_count', desc=True)
              .order('next_probe_at').order('id').range(offset, offset + limit - 1).execute())
    return {'items': result.data or [], 'total': result.count or 0, 'limit': limit, 'offset': offset}


def remove_targets(db, ids: list[UUID]) -> dict:
    return db.rpc('remove_discovery_targets', {
        'p_ids': list(dict.fromkeys(map(str, ids))),
    }).execute().data
