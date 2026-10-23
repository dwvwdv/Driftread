BEGIN;
DO $$
DECLARE f uuid; g uuid; h uuid; owner uuid; fact uuid; report jsonb; marked uuid; curated uuid; reader uuid;
 kept_mark timestamptz; failed boolean;
BEGIN
 SELECT id INTO owner FROM auth.users LIMIT 1;
 -- f: 130 extracted rows, override cap 100. Row i was published i hours ago.
 INSERT INTO driftread.feeds(title,url,article_cap) VALUES('Cap F','https://cap-f.invalid/rss',100) RETURNING id INTO f;
 INSERT INTO driftread.articles(feed_id,title,url,content,content_hash,published_at,discovery_extracted_at,discovery_extracted_hash)
 SELECT f,'F '||i,'https://cap-f.invalid/'||i,'body',repeat('a',64),now()-make_interval(hours=>i),now(),repeat('a',64)
 FROM generate_series(1,130) i;
 PERFORM driftread.reconcile_feed_article_count(f);
 -- g: 160 rows never mined for links, override cap 100 (hard ceiling 150).
 INSERT INTO driftread.feeds(title,url,article_cap) VALUES('Cap G','https://cap-g.invalid/rss',100) RETURNING id INTO g;
 INSERT INTO driftread.articles(feed_id,title,url,content,content_hash,published_at)
 SELECT g,'G '||i,'https://cap-g.invalid/'||i,'body',repeat('b',64),now()-make_interval(hours=>i)
 FROM generate_series(1,160) i;
 PERFORM driftread.reconcile_feed_article_count(g);
 -- h: below the global default; never touched.
 INSERT INTO driftread.feeds(title,url) VALUES('Cap H','https://cap-h.invalid/rss') RETURNING id INTO h;
 INSERT INTO driftread.articles(feed_id,title,url,published_at)
 SELECT h,'H '||i,'https://cap-h.invalid/'||i,now()-make_interval(days=>i) FROM generate_series(1,120) i;
 PERFORM driftread.reconcile_feed_article_count(h);

 -- Protection: a bookmark and a curated link on the two oldest rows of f,
 -- a read receipt (not protected) on the third oldest.
 SELECT id INTO marked FROM driftread.articles WHERE feed_id=f AND url='https://cap-f.invalid/130';
 SELECT id INTO curated FROM driftread.articles WHERE feed_id=f AND url='https://cap-f.invalid/129';
 SELECT id INTO reader FROM driftread.articles WHERE feed_id=f AND url='https://cap-f.invalid/128';
 INSERT INTO driftread.user_bookmarks(user_id,article_id,bookmark_type) VALUES(owner,marked,'read_later');
 INSERT INTO driftread.event_objects(kind,title) VALUES('fact','Curated') RETURNING id INTO fact;
 INSERT INTO driftread.fact_articles(fact_id,article_id) VALUES(fact,curated);
 INSERT INTO driftread.user_article_reads(user_id,article_id) VALUES(owner,reader);

 -- Preview counts f (128 unprotected - 100) and g (rows beyond 150) only.
 report:=driftread.enforce_article_cap(1000,500,true,true);
 ASSERT report->>'dry_run'='true';
 ASSERT (report->>'feeds_over_cap')::int=2, 'over-cap feeds: '||report;
 ASSERT (report->>'excess')::int=90, 'excess should be the cached upper bound: '||report;
 ASSERT (report->>'eligible')::int=38, 'preview eligible: '||report;
 ASSERT (report->>'deleted')::int=0;
 ASSERT (SELECT count(*) FROM driftread.articles WHERE feed_id IN (f,g))=290, 'preview mutated rows';

 -- A batch budget takes the oldest rows of the largest excess first (g).
 report:=driftread.enforce_article_cap(1000,5,false,true);
 ASSERT (report->>'deleted')::int=5, 'budget: '||report;
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=g AND url IN
  ('https://cap-g.invalid/160','https://cap-g.invalid/156')), 'oldest rows go first';
 ASSERT EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=g AND url='https://cap-g.invalid/155');

 report:=driftread.enforce_article_cap(1000,500,false,true);
 ASSERT (report->>'deleted')::int=33, 'apply: '||report;
 ASSERT (report->>'skipped_feeds')::int=0;
 ASSERT (SELECT count(*) FROM driftread.articles WHERE feed_id=f)=102, 'f keeps 100 + 2 protected';
 ASSERT (SELECT article_count FROM driftread.feeds WHERE id=f)=102, 'count cache stale';
 ASSERT EXISTS(SELECT 1 FROM driftread.articles WHERE id=marked), 'bookmark evicted';
 ASSERT EXISTS(SELECT 1 FROM driftread.articles WHERE id=curated), 'curated article evicted';
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.articles WHERE id=reader), 'read receipts are not protected';
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.user_article_reads WHERE article_id=reader);
 ASSERT EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=f AND url='https://cap-f.invalid/100');
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=f AND url='https://cap-f.invalid/101');
 ASSERT (SELECT count(*) FROM driftread.articles WHERE feed_id=g)=150, 'unextracted rows survive to 1.5x';
 ASSERT (SELECT count(*) FROM driftread.articles WHERE feed_id=h)=120, 'below default cap touched';
 ASSERT (SELECT article_cap_evicted_through FROM driftread.feeds WHERE id=h) IS NULL;
 kept_mark:=(SELECT article_cap_evicted_through FROM driftread.feeds WHERE id=f);
 ASSERT kept_mark=(SELECT timeline_at FROM driftread.articles WHERE feed_id=f AND url='https://cap-f.invalid/100')-interval '1 hour',
  'watermark is the newest evicted row: '||kept_mark;

 -- Re-running is a no-op; without extraction protection g drops to 100.
 report:=driftread.enforce_article_cap(1000,500,false,true);
 ASSERT (report->>'deleted')::int=0, 'second run: '||report;
 report:=driftread.enforce_article_cap(1000,500,false,false);
 ASSERT (report->>'deleted')::int=50, 'extraction protection off: '||report;
 ASSERT (SELECT count(*) FROM driftread.articles WHERE feed_id=g)=100;
 -- The global default applies to sources without an override (h has no
 -- extraction state, so protection is off for this run).
 report:=driftread.enforce_article_cap(100,500,false,false);
 ASSERT (SELECT count(*) FROM driftread.articles WHERE feed_id=h)=100, 'default cap: '||report;
 -- Harvest never visits archived sources, so their unextracted rows get no buffer.
 INSERT INTO driftread.feeds(title,url,article_cap,archived_at) VALUES('Cap J','https://cap-j.invalid/rss',100,now()) RETURNING id INTO h;
 INSERT INTO driftread.articles(feed_id,title,url,content_hash,published_at)
 SELECT h,'J '||i,'https://cap-j.invalid/'||i,repeat('f',64),now()-make_interval(hours=>i) FROM generate_series(1,120) i;
 PERFORM driftread.reconcile_feed_article_count(h);
 report:=driftread.enforce_article_cap(1000,500,false,true);
 ASSERT (report->>'deleted')::int=20, 'archived source kept its unextracted buffer: '||report;

 -- The watermark stops a still-listed evicted item from coming back, while
 -- protected rows keep updating and newer or undated items still arrive.
 PERFORM driftread.ingest_article_batch(f,jsonb_build_array(
  jsonb_build_object('title','Back','url','https://cap-f.invalid/120','published_at',now()-interval '120 hours','content_hash',repeat('c',64)),
  jsonb_build_object('title','Renamed','url','https://cap-f.invalid/130','published_at',now()-interval '130 hours','content_hash',repeat('a',64)),
  jsonb_build_object('title','Fresh','url','https://cap-f.invalid/new','published_at',now(),'content_hash',repeat('d',64)),
  jsonb_build_object('title','Undated','url','https://cap-f.invalid/undated','content_hash',repeat('e',64))));
 ASSERT NOT EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=f AND url='https://cap-f.invalid/120'), 'evicted item resurrected';
 ASSERT (SELECT title FROM driftread.articles WHERE id=marked)='Renamed', 'protected row lost its update';
 ASSERT EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=f AND url='https://cap-f.invalid/new');
 ASSERT EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=f AND url='https://cap-f.invalid/undated');
 ASSERT (SELECT article_count FROM driftread.feeds WHERE id=f)=104;
 -- A source that never evicted accepts old items as before.
 INSERT INTO driftread.feeds(title,url) VALUES('Cap I','https://cap-i.invalid/rss') RETURNING id INTO h;
 INSERT INTO driftread.articles(feed_id,title,url,published_at) VALUES(h,'Old','https://cap-i.invalid/old',now()-interval '10 years');
 ASSERT EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=h AND url='https://cap-i.invalid/old'), 'guard without watermark';

 BEGIN
  PERFORM driftread.enforce_article_cap(99,500,true,true); failed:=false;
 EXCEPTION WHEN raise_exception THEN failed:=true; END;
 ASSERT failed, 'cap below 100 accepted';
 BEGIN
  PERFORM driftread.enforce_article_cap(1000,1001,true,true); failed:=false;
 EXCEPTION WHEN raise_exception THEN failed:=true; END;
 ASSERT failed, 'batch above 1000 accepted';
 BEGIN
  UPDATE driftread.feeds SET article_cap=99 WHERE id=f; failed:=false;
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 ASSERT failed, 'override below 100 accepted';
END $$;
ROLLBACK;
