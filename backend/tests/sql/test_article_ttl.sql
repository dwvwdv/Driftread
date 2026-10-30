BEGIN;
DO $$
DECLARE f uuid; owner uuid; other_owner uuid; fact uuid; marked uuid; later uuid; curated uuid;
 staged uuid; reader uuid; report jsonb; failed boolean; old_id uuid; fresh_id uuid;
 boundary_id uuid; undated_id uuid; future_id uuid; zone_id uuid; sync_before bigint;
BEGIN
 ASSERT (SELECT value->'enabled' FROM driftread.app_settings WHERE key='article.ttl')='false'::jsonb,
  'TTL must install disabled';
 SELECT id INTO owner FROM auth.users LIMIT 1;
 INSERT INTO auth.users VALUES(gen_random_uuid()) RETURNING id INTO other_owner;
 INSERT INTO driftread.feeds(title,url) VALUES('TTL fixture','https://ttl-fixture.invalid/rss') RETURNING id INTO f;
 INSERT INTO driftread.user_feeds(user_id,feed_id) VALUES(owner,f);
 INSERT INTO driftread.articles(feed_id,title,url,published_at)
 VALUES(f,'fresh','https://ttl-fixture.invalid/fresh',now()-interval '13 days') RETURNING id INTO fresh_id;
 INSERT INTO driftread.articles(feed_id,title,url,published_at)
 VALUES(f,'boundary','https://ttl-fixture.invalid/boundary',now()-interval '14 days') RETURNING id INTO boundary_id;
 INSERT INTO driftread.articles(feed_id,title,url,published_at)
 VALUES(f,'reader','https://ttl-fixture.invalid/reader',now()-interval '14 days 1 second') RETURNING id INTO reader;
 INSERT INTO driftread.articles(feed_id,title,url,published_at,fetched_at)
 VALUES(f,'late discovery','https://ttl-fixture.invalid/old',now()-interval '30 days',now()) RETURNING id INTO old_id;
 INSERT INTO driftread.articles(feed_id,title,url,discovered_at)
 VALUES(f,'undated','https://ttl-fixture.invalid/undated',now()-interval '20 days') RETURNING id INTO undated_id;
 INSERT INTO driftread.articles(feed_id,title,url,published_at,discovered_at)
 VALUES(f,'future','https://ttl-fixture.invalid/future',now()+interval '5 days',now()-interval '20 days') RETURNING id INTO future_id;
 INSERT INTO driftread.articles(feed_id,title,url,published_at)
 VALUES(f,'zone','https://ttl-fixture.invalid/zone',
  ((now()-interval '16 days') AT TIME ZONE 'Asia/Taipei') AT TIME ZONE 'Asia/Taipei') RETURNING id INTO zone_id;
 INSERT INTO driftread.articles(feed_id,title,url,published_at)
 VALUES(f,'bookmark','https://ttl-fixture.invalid/bookmark',now()-interval '30 days') RETURNING id INTO marked;
 INSERT INTO driftread.articles(feed_id,title,url,published_at)
 VALUES(f,'later','https://ttl-fixture.invalid/later',now()-interval '30 days') RETURNING id INTO later;
 INSERT INTO driftread.articles(feed_id,title,url,published_at)
 VALUES(f,'curated','https://ttl-fixture.invalid/curated',now()-interval '30 days') RETURNING id INTO curated;
 INSERT INTO driftread.articles(feed_id,title,url,published_at,staged_at)
 VALUES(f,'staged','https://ttl-fixture.invalid/staged',now()-interval '30 days',now()) RETURNING id INTO staged;
 INSERT INTO driftread.user_bookmarks(user_id,article_id,bookmark_type)
 VALUES(owner,marked,'favorite'),(other_owner,later,'read_later');
 INSERT INTO driftread.event_objects(kind,title) VALUES('fact','TTL curated') RETURNING id INTO fact;
 INSERT INTO driftread.fact_articles(fact_id,article_id) VALUES(fact,curated);
 INSERT INTO driftread.user_article_reads(user_id,article_id) VALUES(owner,reader);
 INSERT INTO driftread.article_discoveries(article_id,feed_id,canonical_url,origin,source_url)
 VALUES(old_id,f,'https://ttl-fixture.invalid/old','rss','https://ttl-fixture.invalid/rss');
 PERFORM driftread.reconcile_feed_article_count(f);

 report:=driftread.enforce_article_ttl(14,500,true);
 ASSERT (report->>'expired')::int=9, 'strict date eligibility: '||report;
 ASSERT (report->>'protected')::int=4, 'all users/curation/staging protected: '||report;
 ASSERT (report->>'eligible')::int=5 AND (report->>'deleted')::int=0, 'preview: '||report;
 ASSERT (report->>'reads_affected')::int=1, 'preview read receipt impact: '||report;
 ASSERT (report->>'deletion_delay_seconds')::numeric=0;
 ASSERT (SELECT count(*) FROM driftread.articles WHERE feed_id=f)=11, 'preview changed rows';
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.article_ttl_reingest_guard WHERE feed_id=f), 'preview changed cache';

 SELECT sequence INTO sync_before FROM driftread.sync_clock WHERE singleton;
 report:=driftread.enforce_article_ttl(14,2,false);
 ASSERT (report->>'eligible')::int=2 AND (report->>'deleted')::int=2, 'bounded first batch: '||report;
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.articles WHERE id=old_id), 'old publication newly fetched must expire';
 ASSERT (report->>'deletion_delay_seconds')::numeric>0;
 report:=driftread.enforce_article_ttl(14,500,false);
 ASSERT (report->>'deleted')::int=3 AND (report->>'reads_affected')::int=1, 'remaining batch: '||report;
 ASSERT (SELECT article_count FROM driftread.feeds WHERE id=f)=6, 'count cache';
 ASSERT EXISTS(SELECT 1 FROM driftread.articles WHERE id=boundary_id), 'cutoff equality deleted';
 ASSERT EXISTS(SELECT 1 FROM driftread.articles WHERE id=fresh_id), 'fresh deleted';
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.user_article_reads WHERE article_id=reader), 'reads not cascaded';
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.article_revisions WHERE article_id=old_id), 'revision not cascaded';
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.article_discoveries WHERE article_id=old_id), 'discovery not cascaded';
 ASSERT (SELECT count(*) FROM driftread.sync_changes WHERE sequence>sync_before AND entity='articles' AND operation='DELETE')=5,
  'article deletions did not invalidate sync snapshots';
 ASSERT (SELECT count(*) FROM driftread.sync_changes WHERE sequence>sync_before AND entity='user_article_reads' AND operation='DELETE')=1,
  'read cascade did not invalidate user sync snapshot';
 ASSERT (SELECT cardinality(url_hashes) FROM driftread.article_ttl_reingest_guard WHERE feed_id=f)=2,
  'only undated/future-dated identities cached';
 report:=driftread.enforce_article_ttl(14,500,false);
 ASSERT (report->>'deleted')::int=0, 'repeat must not delete protected/boundary';

 -- While enabled, dated old inserts are excluded without creating a tombstone.
 -- Protected existing IDs still update. No-date/future-date deletions cannot
 -- reset their discovery time on the next refresh thanks to the bounded cache.
 UPDATE driftread.app_settings SET value='{"enabled":true,"days":14}' WHERE key='article.ttl';
 PERFORM driftread.ingest_article_batch(f,jsonb_build_array(
  jsonb_build_object('title','old again','url','https://ttl-fixture.invalid/old','published_at',now()-interval '30 days','content_hash',repeat('a',64)),
  jsonb_build_object('title','late old backfill','url','https://ttl-fixture.invalid/never-seen','published_at',now()-interval '30 days','content_hash',repeat('a',64)),
  jsonb_build_object('title','undated again','url','https://ttl-fixture.invalid/undated','content_hash',repeat('a',64)),
  jsonb_build_object('title','future again','url','https://ttl-fixture.invalid/future','published_at',now()+interval '5 days','content_hash',repeat('a',64)),
  jsonb_build_object('title','updated bookmark','url','https://ttl-fixture.invalid/bookmark','published_at',now()-interval '30 days','content_hash',repeat('a',64)),
  jsonb_build_object('title','new no-date','url','https://ttl-fixture.invalid/new-undated','content_hash',repeat('a',64))));
 ASSERT (SELECT article_count FROM driftread.feeds WHERE id=f)=7, 'admission guarded incorrectly';
 ASSERT (SELECT title FROM driftread.articles WHERE id=marked)='updated bookmark', 'existing protected update lost';
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=f AND url IN
  ('https://ttl-fixture.invalid/old','https://ttl-fixture.invalid/never-seen','https://ttl-fixture.invalid/undated','https://ttl-fixture.invalid/future')),
  'deleted/late old items re-ingested';
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.article_cleanup_tombstones WHERE feed_id=f), 'TTL made permanent per-URL tombstones';

 -- Widening admits dated history now in range; turning off ignores cache too.
 UPDATE driftread.app_settings SET value='{"enabled":true,"days":60}' WHERE key='article.ttl';
 INSERT INTO driftread.articles(feed_id,title,url,published_at)
 VALUES(f,'history allowed','https://ttl-fixture.invalid/old',now()-interval '30 days');
 ASSERT EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=f AND url='https://ttl-fixture.invalid/old'), 'widened TTL kept date watermark';
 UPDATE driftread.app_settings SET value='{"enabled":false,"days":14}' WHERE key='article.ttl';
 INSERT INTO driftread.articles(feed_id,title,url)
 VALUES(f,'undated allowed','https://ttl-fixture.invalid/undated');
 ASSERT EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=f AND url='https://ttl-fixture.invalid/undated'), 'disabled cache still blocked';

 BEGIN
  PERFORM driftread.enforce_article_ttl(0,500,true); failed:=false;
 EXCEPTION WHEN raise_exception THEN failed:=true; END;
 ASSERT failed, 'days=0 accepted';
 BEGIN
  PERFORM driftread.enforce_article_ttl(3651,500,true); failed:=false;
 EXCEPTION WHEN raise_exception THEN failed:=true; END;
 ASSERT failed, 'days=3651 accepted';
 BEGIN
  PERFORM driftread.enforce_article_ttl(14,1001,true); failed:=false;
 EXCEPTION WHEN raise_exception THEN failed:=true; END;
 ASSERT failed, 'batch=1001 accepted';
 BEGIN
  PERFORM driftread.enforce_article_ttl(14,500,NULL); failed:=false;
 EXCEPTION WHEN raise_exception THEN failed:=true; END;
 ASSERT failed, 'NULL preview flag accepted';
END $$;
ROLLBACK;
