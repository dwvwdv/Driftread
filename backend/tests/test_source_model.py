from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4, UUID
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from models import FeedCreate, SourceMetadataUpdate
from services.source_health import participant_key, summarize_source_health
from services.source_visibility import require_readable_source
NOW=datetime(2026,10,9,tzinfo=timezone.utc)
class Query:
    """Explicit fake rejects unsupported builders."""
    def __init__(self,db,name): self.db,self.name,self.filters,self.operation=db,name,[],None
    def select(self,*a,**kw): return self
    def update(self,data,**kw): self.operation=data; return self
    def eq(self,field,value): self.filters.append((field,value)); return self
    def is_(self,*a): return self
    def order(self,*a,**kw): return self
    def range(self,*a): return self
    def maybe_single(self): return self
    def execute(self):
        self.db.calls.append((self.name,self.filters,self.operation))
        if self.name=='feeds':
            data=[r for r in self.db.rows if all(r.get(f)==v for f,v in self.filters)]
            if self.operation:
                for r in data: r.update(self.operation)
            return SimpleNamespace(data=(data[0] if data else None) if self.db.single else data,count=len(data))
        return SimpleNamespace(data=[],count=0)
class DB:
    def __init__(self,rows=(),single=False): self.rows,self.single,self.calls,self.rpcs=list(rows),single,[],[]
    def table(self,name): return Query(self,name)
    def rpc(self,name,args): self.rpcs.append((name,args)); return Query(self,name)
def source(**updates):
    return {'id':str(uuid4()),'title':'fixture','url':'https://example.test/rss','created_at':NOW,'updated_at':NOW,'participation_mode':'normal','last_ok_at':NOW.isoformat(),'fetch_interval_minutes':60,**updates}
def test_source_metadata_validation_and_explicit_clear():
    assert FeedCreate(title='x',url='https://x').participation_mode=='normal'
    assert SourceMetadataUpdate(signal_group='  Publisher  ').signal_group=='publisher'
    assert SourceMetadataUpdate(signal_group=' ').model_dump(exclude_unset=True)=={'signal_group':None}
    assert SourceMetadataUpdate().model_dump(exclude_unset=True)=={}
    for invalid in [{'participation_mode':'public'},{'first_party':None},{'fulltext_policy':None},{'signal_group':'x'*101}]:
        with pytest.raises(ValidationError): SourceMetadataUpdate(**invalid)
def test_completeness_includes_missing_sources_and_deduplicates_participants():
    a=source(signal_group='Publisher');b=source(signal_group='publisher',last_ok_at=(NOW-timedelta(hours=3)).isoformat());c=source(last_ok_at=None,participation_mode='signal_only')
    result=summarize_source_health([a,b,c,source(participation_mode='private'),source(archived_at=NOW)],NOW)
    assert result['complete'] is False and result['source_count']==3 and result['participant_count']==2
    assert result['behind_source_count']==2 and result['sources'][1]['lag_seconds']==7200
    assert participant_key(a)==participant_key(b)
    assert summarize_source_health([],NOW)['complete'] is False
    assert summarize_source_health([a],NOW)['complete'] is True
@pytest.mark.parametrize('role',['signal_only','private'])
def test_hidden_sources_return_no_readable_metadata(role):
    row=source(participation_mode=role);db=DB([row],single=True)
    with pytest.raises(HTTPException) as exc: require_readable_source(db,row['id'])
    assert exc.value.status_code==404
    assert db.calls[0][1]==[('id',row['id']),('participation_mode','normal')]
@pytest.mark.asyncio
async def test_catalog_filter_precedes_count_and_page():
    from routers.feeds import list_feeds
    db=DB([source(),source(participation_mode='private'),source(participation_mode='signal_only')])
    result=await list_feeds(page=1,page_size=20,search=None,db=db)
    assert result.total==1 and len(result.items)==1
@pytest.mark.asyncio
async def test_source_patch_preserves_omitted_metadata():
    from routers.admin import update_source_metadata
    row=source(first_party=True,signal_group='publisher');db=DB([row])
    await update_source_metadata(feed_id=UUID(row['id']),body=SourceMetadataUpdate(participation_mode='signal_only'),db=db)
    assert row['first_party'] is True and row['signal_group']=='publisher' and row['participation_mode']=='signal_only'
@pytest.mark.asyncio
async def test_failure_records_attempt_but_not_success():
    from services.feed_refresh import refresh_one
    db=DB();row=source()
    with patch('services.feed_refresh.validate_fetch_url',new=AsyncMock(side_effect=ValueError('fetch failed'))): result=await refresh_one(db,row)
    assert result.status=='failed' and [a['p_ok'] for _,a in db.rpcs]==[False]
@pytest.mark.asyncio
async def test_304_advances_ok_without_article_writes():
    from services.feed_refresh import refresh_one
    db=DB();row=source();fetched=SimpleNamespace(not_modified=True,etag='e',last_modified=None)
    with patch('services.feed_refresh.validate_fetch_url',new=AsyncMock(return_value=row['url'])),patch('services.feed_refresh.fetch_and_parse_conditional',new=AsyncMock(return_value=fetched)): result=await refresh_one(db,row)
    assert result.status=='not_modified' and [a['p_ok'] for _,a in db.rpcs]==[False,True]
    assert not any(n=='articles' for n,_,_ in db.calls)

@pytest.mark.asyncio
async def test_success_record_survives_article_ingestion_failure():
    from services.feed_refresh import refresh_one
    db=DB();row=source();fetched=SimpleNamespace(not_modified=False,parsed=SimpleNamespace(articles=[]))
    with patch('services.feed_refresh.validate_fetch_url',new=AsyncMock(return_value=row['url'])),patch('services.feed_refresh.fetch_and_parse_conditional',new=AsyncMock(return_value=fetched)),patch('services.feed_refresh.upsert_articles',side_effect=RuntimeError('business write failed')):
        with pytest.raises(RuntimeError): await refresh_one(db,row)
    assert [a['p_ok'] for _,a in db.rpcs]==[False,True]

@pytest.mark.asyncio
@pytest.mark.parametrize('role',['signal_only','private'])
async def test_hidden_source_cannot_be_subscribed_or_liked(role):
    from auth import AuthUser
    from models import FeedFeedbackCreate
    from routers.me import subscribe,set_feed_feedback
    row=source(participation_mode=role);db=DB([row],single=True)
    user=AuthUser(user_id=str(uuid4()),email='reader@example.test')
    with pytest.raises(HTTPException): await subscribe(feed_id=UUID(row['id']),user=user,db=db)
    with pytest.raises(HTTPException): await set_feed_feedback(feed_id=UUID(row['id']),body=FeedFeedbackCreate(feedback_type='liked'),user=user,db=db)
    assert all(name=='feeds' for name,_,_ in db.calls)

@pytest.mark.asyncio
async def test_admin_legacy_reimport_preserves_source_metadata():
    from models import ImportFeedsRequest
    from routers.admin import import_feeds
    row=source(participation_mode='private',first_party=True,signal_group='publisher',fulltext_policy='summary_only')
    class ImportDB:
        def table(self,name): assert name=='feeds'; return self
        def upsert(self,data,**kwargs): row.update(data); return self
        def execute(self): return SimpleNamespace(data=[row])
    db=ImportDB()
    await import_feeds(body=ImportFeedsRequest(feeds=[FeedCreate(title='new title',url=row['url'])]),db=db)
    assert row['participation_mode']=='private' and row['first_party'] is True
    assert row['signal_group']=='publisher' and row['fulltext_policy']=='summary_only'
    await import_feeds(body=ImportFeedsRequest(feeds=[FeedCreate(title='new title',url=row['url'],participation_mode='normal',fulltext_policy='rss')]),db=db)
    assert row['participation_mode']=='normal' and row['fulltext_policy']=='rss'
