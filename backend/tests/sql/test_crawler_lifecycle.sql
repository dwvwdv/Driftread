-- Run in an isolated PostgreSQL database after the migrations. Rolls back data.
BEGIN;
DO $$
DECLARE f uuid:=gen_random_uuid(); f_sub uuid:=gen_random_uuid(); a uuid; b uuid; r uuid; s uuid; pending uuid; stale uuid;
 h text:=repeat('a',64); h2 text:=repeat('b',64); result jsonb; touched int;
BEGIN
 INSERT INTO driftread.feeds(id,title,url) VALUES(f,'fixture','https://fixture.invalid/rss'),(f_sub,'protected','https://protected.invalid/rss');
 INSERT INTO driftread.articles(feed_id,title,url,content,summary) VALUES(f,'legacy','https://fixture.invalid/legacy',repeat('body',3000),NULL);
 touched:=driftread.initialize_article_source_hash(f,'https://fixture.invalid/legacy','stale',NULL,h);
 IF touched<>0 THEN RAISE EXCEPTION 'stale legacy snapshot acquired hash'; END IF;
 touched:=driftread.initialize_article_source_hash(f,'https://fixture.invalid/legacy',repeat('body',3000),NULL,h);
 IF touched<>1 THEN RAISE EXCEPTION 'legacy hash initialization failed'; END IF;
 touched:=driftread.initialize_article_source_hash(f,'https://fixture.invalid/legacy',repeat('body',3000),NULL,h2);
 IF touched<>0 THEN RAISE EXCEPTION 'initialized legacy hash overwritten'; END IF;
 touched:=driftread.ingest_article_batch(f,jsonb_build_array(jsonb_build_object('title','original','url','https://fixture.invalid/article','summary','brief','content','<p>uniqueoldword</p>','content_hash',h)));
 IF touched<>1 THEN RAISE EXCEPTION 'first ingest must insert'; END IF;
 SELECT id INTO a FROM driftread.articles WHERE feed_id=f AND url='https://fixture.invalid/article';
 touched:=driftread.ingest_article_batch(f,jsonb_build_array(jsonb_build_object('title','original','url','https://fixture.invalid/article','summary','brief','content','<p>uniqueoldword</p>','content_hash',h)));
 IF touched<>0 THEN RAISE EXCEPTION 'identical ingest should not update'; END IF;
 UPDATE driftread.articles SET fetched_at=now()-interval '40 days',content_revision_at=now()-interval '40 days',discovery_extracted_hash=h,discovery_extracted_at=now() WHERE id=a;
 INSERT INTO driftread.articles(feed_id,title,url,content,content_hash,discovery_extracted_hash,discovery_extracted_at,fetched_at)
 VALUES(f,'bookmark','https://fixture.invalid/b','body',h,h,now(),now()-interval '40 days') RETURNING id INTO b;
 INSERT INTO driftread.articles(feed_id,title,url,content,content_hash,discovery_extracted_hash,discovery_extracted_at,fetched_at)
 VALUES(f,'read','https://fixture.invalid/r','body',h,h,now(),now()-interval '40 days') RETURNING id INTO r;
 INSERT INTO driftread.articles(feed_id,title,url,content,content_hash,discovery_extracted_hash,discovery_extracted_at,fetched_at)
 VALUES(f_sub,'subscribed','https://fixture.invalid/s','body',h,h,now(),now()-interval '40 days') RETURNING id INTO s;
 INSERT INTO driftread.articles(feed_id,title,url,content,content_hash,fetched_at)
 VALUES(f,'pending','https://fixture.invalid/p','body',h,now()-interval '40 days') RETURNING id INTO pending;
 INSERT INTO driftread.articles(feed_id,title,url,content,content_hash,discovery_extracted_hash,discovery_extracted_at,fetched_at)
 VALUES(f,'stale','https://fixture.invalid/stale','body',h2,h,now(),now()-interval '40 days') RETURNING id INTO stale;
 -- The runner creates a real auth user fixture before this block.
 INSERT INTO driftread.user_bookmarks(user_id,article_id,bookmark_type) SELECT id,b,'read_later' FROM auth.users LIMIT 1;
 INSERT INTO driftread.user_article_reads(user_id,article_id) SELECT id,r FROM auth.users LIMIT 1;
 INSERT INTO driftread.user_feeds(user_id,feed_id) SELECT id,f_sub FROM auth.users LIMIT 1;
 result:=driftread.compact_article_content(30,200,true);
 IF (result->>'eligible')::int<>1 OR (result->>'compacted')::int<>0 THEN RAISE EXCEPTION 'preview must identify only safe article: %',result; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.articles WHERE id=a AND content IS NOT NULL) THEN RAISE EXCEPTION 'preview mutated body'; END IF;
 result:=driftread.compact_article_content(30,200,false);
 IF (result->>'compacted')::int<>1 THEN RAISE EXCEPTION 'apply must compact only safe article: %',result; END IF;
 IF EXISTS(SELECT 1 FROM driftread.articles WHERE id=a AND content IS NOT NULL) THEN RAISE EXCEPTION 'body not compacted'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.articles WHERE id=a AND search_vector @@ plainto_tsquery('simple','uniqueoldword')) THEN RAISE EXCEPTION 'search index retained compacted body'; END IF;
 IF (SELECT count(*) FROM driftread.articles WHERE id IN(b,r,s,pending,stale) AND content IS NOT NULL)<>5 THEN RAISE EXCEPTION 'protected or unextracted bodies lost'; END IF;
 result:=driftread.compact_article_content(30,200,false);
 IF (result->>'compacted')::int<>0 THEN RAISE EXCEPTION 'not idempotent'; END IF;
 touched:=driftread.ingest_article_batch(f,jsonb_build_array(jsonb_build_object('title','renamed','url','https://fixture.invalid/article','summary','brief','content','<p>uniqueoldword</p>','content_hash',h)));
 IF touched<>1 OR EXISTS(SELECT 1 FROM driftread.articles WHERE id=a AND content IS NOT NULL) THEN RAISE EXCEPTION 'metadata update rehydrated body'; END IF;
 touched:=driftread.ingest_article_batch(f,jsonb_build_array(jsonb_build_object('title','renamed','url','https://fixture.invalid/article','summary','brief','content','new revision','content_hash',h2)));
 IF NOT EXISTS(SELECT 1 FROM driftread.articles WHERE id=a AND content='new revision' AND content_compacted_at IS NULL AND discovery_extracted_at IS NULL) THEN RAISE EXCEPTION 'revision state did not reset'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.pending_article_discovery(f,200) WHERE id=a) THEN RAISE EXCEPTION 'new revision missing discovery work'; END IF;
 UPDATE driftread.articles SET discovery_extracted_at=now(),discovery_extracted_hash=h2 WHERE id=a;
 result:=driftread.compact_article_content(30,200,false);
 IF (result->>'compacted')::int<>0 OR NOT EXISTS(SELECT 1 FROM driftread.articles WHERE id=a AND content='new revision' AND content_revision_at>=now()-interval '1 minute' AND fetched_at<now()-interval '30 days') THEN RAISE EXCEPTION 'new source revision compacted by old fetched date'; END IF;
 UPDATE driftread.articles SET discovery_retry_at=now()+interval '1 day' WHERE id=pending;
 IF EXISTS(SELECT 1 FROM driftread.pending_article_discovery(f,200) WHERE id=pending) THEN RAISE EXCEPTION 'deferred article blocks queue'; END IF;
 IF has_function_privilege('anon','driftread.ingest_article_batch(uuid,jsonb)','EXECUTE') OR has_function_privilege('authenticated','driftread.compact_article_content(integer,integer,boolean)','EXECUTE') THEN RAISE EXCEPTION 'privileged RPC exposed'; END IF;
 IF has_table_privilege('anon','driftread.worker_runs','SELECT') THEN RAISE EXCEPTION 'private telemetry exposed'; END IF;
 IF has_function_privilege('anon','driftread.initialize_article_source_hash(uuid,text,text,text,text)','EXECUTE') THEN RAISE EXCEPTION 'legacy hash RPC exposed'; END IF;
 result:=driftread.article_storage_stats();
 IF (result->>'article_count')::int<>7 OR (result->>'full_content_count')::int<>7 OR (result->>'compacted_count')::int<>0 THEN RAISE EXCEPTION 'storage snapshot inconsistent: %',result; END IF;
 INSERT INTO driftread.worker_runs(id,worker_id,kind,status,started_at,finished_at)
 SELECT gen_random_uuid(),'expired','refresh','succeeded',now()-interval '40 days',now()-interval '40 days' FROM generate_series(1,1005);
 touched:=driftread.prune_worker_operations(now()-interval '30 days');
 IF touched<>1000 OR (SELECT count(*) FROM driftread.worker_runs WHERE worker_id='expired')<>5 THEN RAISE EXCEPTION 'telemetry prune not bounded'; END IF;
 BEGIN
  PERFORM driftread.compact_article_content(0,200,false);
  RAISE EXCEPTION 'invalid retention accepted';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM='invalid retention accepted' THEN RAISE; END IF;
 END;
END $$;
ROLLBACK;
