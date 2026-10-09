BEGIN;
DO $$
DECLARE feed uuid; article uuid; original_revision uuid; other_feed uuid; payload jsonb; result integer;
BEGIN
 INSERT INTO driftread.feeds(title,url) VALUES('revision fixture','https://revisions.example/rss') RETURNING id INTO feed;
 payload:=jsonb_build_array(jsonb_build_object('url','https://revisions.example/post','title','original','content','body','content_hash',repeat('a',64),'published_at',now()-interval '60 days','backfill',true,'backfill_reason','initial_fetch'));
 result:=driftread.ingest_article_batch(feed,payload);
 IF result<>1 THEN RAISE EXCEPTION 'initial ingest failed'; END IF;
 SELECT id,current_revision_id INTO article,original_revision FROM driftread.articles WHERE feed_id=feed;
 IF NOT EXISTS(SELECT 1 FROM driftread.articles WHERE id=article AND revision_number=1 AND backfill AND backfill_reason='initial_fetch' AND timeline_at=published_at) THEN
  RAISE EXCEPTION 'initial revision/time context missing';
 END IF;
 IF driftread.ingest_article_batch(feed,payload)<>0 OR (SELECT count(*) FROM driftread.article_revisions WHERE article_id=article)<>1 THEN
  RAISE EXCEPTION 'unchanged source adds versions';
 END IF;
 UPDATE driftread.articles SET content_revision_at=now()-interval '60 days',discovery_extracted_hash=content_hash,discovery_extracted_at=now() WHERE id=article;
 IF (driftread.compact_article_content(30,200,false)->>'compacted')::integer<>1 THEN
  RAISE EXCEPTION 'revision prevents compaction';
 END IF;
 result:=driftread.ingest_article_batch(feed,payload);
 IF result<>0 OR (SELECT content FROM driftread.articles WHERE id=article) IS NOT NULL THEN
  RAISE EXCEPTION 'unchanged compacted source rehydrates';
 END IF;
 payload:=jsonb_set(payload,'{0,title}','"corrected"');
 PERFORM driftread.ingest_article_batch(feed,payload);
 IF (SELECT count(*) FROM driftread.article_revisions WHERE article_id=article)<>2 OR NOT EXISTS(SELECT 1 FROM driftread.article_revisions WHERE id=original_revision AND title='original') THEN
  RAISE EXCEPTION 'metadata revision lost snapshot';
 END IF;
 IF (SELECT content FROM driftread.articles WHERE id=article) IS NOT NULL THEN RAISE EXCEPTION 'metadata edit rehydrates'; END IF;
 payload:=jsonb_set(payload,'{0,content_hash}',to_jsonb(repeat('b',64)));
 payload:=jsonb_set(payload,'{0,content}','"revised body"');
 PERFORM driftread.ingest_article_batch(feed,payload);
 IF NOT EXISTS(SELECT 1 FROM driftread.articles WHERE id=article AND revision_number=3 AND content='revised body' AND content_compacted_at IS NULL AND discovery_extracted_at IS NULL) THEN
  RAISE EXCEPTION 'new source hash not retained/requeued';
 END IF;
 INSERT INTO driftread.feeds(title,url) VALUES('second entrance','https://other-revisions.example/rss') RETURNING id INTO other_feed;
 PERFORM driftread.ingest_article_batch(other_feed,jsonb_set(payload,'{0,title}','"other rendering"'));
 IF (SELECT count(*) FROM driftread.article_discoveries WHERE canonical_url='https://revisions.example/post')<>2 OR (SELECT title FROM driftread.articles WHERE id=article)<>'corrected' THEN
  RAISE EXCEPTION 'multiple source provenance lost or renderer overwritten';
 END IF;
 payload:=jsonb_set(payload,'{0,origin}','"discover_import"');
 PERFORM driftread.ingest_article_batch(feed,payload);
 IF (SELECT count(*) FROM driftread.article_discoveries WHERE article_id=article)<>2 THEN RAISE EXCEPTION 'multiple origin missing'; END IF;
 BEGIN
  UPDATE driftread.article_revisions SET title='tampered' WHERE id=original_revision;
  RAISE EXCEPTION 'immutable revision updated';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM<>'article revisions are immutable' THEN RAISE; END IF;
 END;
 -- First discovery cannot become realtime by refreshing the same URL.
 UPDATE driftread.articles SET discovered_at=now()+interval '1 day',backfill=false,backfill_reason=NULL WHERE id=article;
 IF NOT EXISTS(SELECT 1 FROM driftread.articles WHERE id=article AND discovered_at<=now() AND backfill AND backfill_reason='initial_fetch') THEN RAISE EXCEPTION 'historical context overwritten'; END IF;
 PERFORM driftread.ingest_article_batch(feed,jsonb_build_array(jsonb_build_object('url','https://revisions.example/future','title','future','content_hash',repeat('c',64),'published_at',now()+interval '1 day')));
 IF EXISTS(SELECT 1 FROM driftread.articles WHERE feed_id=feed AND timeline_at>discovered_at) THEN RAISE EXCEPTION 'future source date poisons timeline'; END IF;
 PERFORM driftread.ingest_article_batch(feed,jsonb_build_array(jsonb_build_object('url','https://revisions.example/undated','title','undated','content_hash',repeat('d',64))));
 IF NOT EXISTS(SELECT 1 FROM driftread.articles WHERE url='https://revisions.example/undated' AND timeline_at=discovered_at AND NOT backfill) THEN RAISE EXCEPTION 'undated source missing fallback'; END IF;
END $$;
ROLLBACK;
