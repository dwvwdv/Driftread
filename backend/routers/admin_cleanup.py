from fastapi import APIRouter, Depends, Query
from supabase import Client

from database import get_client
from routers.admin import require_api_key
from services import cleanup

router = APIRouter(prefix='/admin', tags=['admin'], dependencies=[Depends(require_api_key)])


@router.get('/cleanup/items')
def list_cleanup_items(
    kind: cleanup.Kind = 'feeds', rule: cleanup.Rule = 'all', state: cleanup.State = 'active',
    query: str = Query('', max_length=200), limit: int = Query(50, ge=1, le=50),
    offset: int = Query(0, ge=0), db: Client = Depends(get_client),
):
    return cleanup.list_items(db, kind, rule, state, query, limit, offset)


@router.post('/cleanup/batch')
def cleanup_batch(body: cleanup.CleanupBatch, db: Client = Depends(get_client)):
    return cleanup.apply_batch(db, body)


@router.get('/discovery/pending-targets')
def pending_targets(limit: int = Query(50, ge=1, le=50), offset: int = Query(0, ge=0),
                    db: Client = Depends(get_client)):
    return cleanup.list_pending_targets(db, limit, offset)


@router.post('/discovery/targets/remove')
def remove_targets(body: cleanup.RemoveTargets, db: Client = Depends(get_client)):
    return cleanup.remove_targets(db, body.ids)
