-- Commit the cache with every successful batch. Later batch/metadata failures
-- must not hide already-durable articles behind a stale catalog count.
CREATE OR REPLACE FUNCTION driftread.reconcile_feed_article_count(p_feed_id uuid)
 RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE total integer;
BEGIN
 PERFORM 1 FROM driftread.feeds WHERE id=p_feed_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'feed not found'; END IF;
 SELECT count(*) INTO total FROM driftread.articles WHERE feed_id=p_feed_id;
 UPDATE driftread.feeds SET article_count=total WHERE id=p_feed_id AND article_count IS DISTINCT FROM total;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION driftread.reconcile_feed_article_count(uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.reconcile_feed_article_count(uuid) TO service_role;

CREATE OR REPLACE FUNCTION driftread.ingest_article_batch(p_feed_id uuid, p_articles jsonb)
RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE touched integer;
BEGIN
 IF jsonb_typeof(p_articles) IS DISTINCT FROM 'array' OR jsonb_array_length(p_articles)>200 THEN
  RAISE EXCEPTION 'article batch must be an array of at most 200 rows';
 END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_articles) r WHERE coalesce(r->>'content_hash','') !~ '^[0-9a-f]{64}$'
  OR coalesce(r->>'origin','rss') NOT IN ('rss','discover_import','historical_import')
  OR length(coalesce(r->>'backfill_reason',''))>100) THEN
  RAISE EXCEPTION 'invalid article hash or ingestion context';
 END IF;
 -- Lock only during the bounded SQL batch, never across external I/O.
 PERFORM 1 FROM driftread.feeds WHERE id=p_feed_id FOR UPDATE;
 INSERT INTO driftread.articles AS a(feed_id,title,url,summary,content,author,published_at,content_hash,content_revision_at,backfill,backfill_reason)
 SELECT p_feed_id,r.title,r.url,r.summary,r.content,r.author,r.published_at,r.content_hash,now(),
  coalesce(r.backfill,false) OR coalesce(r.published_at<now()-interval '7 days',false),
  CASE WHEN coalesce(r.backfill,false) THEN coalesce(r.backfill_reason,'historical_import')
       WHEN r.published_at<now()-interval '7 days' THEN 'old_publication' END
 FROM jsonb_to_recordset(p_articles) AS r(title text,url text,summary text,content text,author text,published_at timestamptz,content_hash text,backfill boolean,backfill_reason text)
 ORDER BY r.url
 ON CONFLICT(feed_id,url) DO UPDATE SET
 title=excluded.title,summary=excluded.summary,author=excluded.author,published_at=excluded.published_at,
 content=CASE WHEN a.content_hash=excluded.content_hash AND a.content_compacted_at IS NOT NULL THEN a.content ELSE excluded.content END,
 discovery_extracted_hash=CASE WHEN a.content_hash=excluded.content_hash THEN a.discovery_extracted_hash ELSE NULL END,
 discovery_extracted_at=CASE WHEN a.content_hash=excluded.content_hash THEN a.discovery_extracted_at ELSE NULL END,
 content_compacted_at=CASE WHEN a.content_hash=excluded.content_hash THEN a.content_compacted_at ELSE NULL END,
 discovery_retry_at=CASE WHEN a.content_hash=excluded.content_hash THEN a.discovery_retry_at ELSE NULL END,
 content_revision_at=CASE WHEN a.content_hash=excluded.content_hash THEN a.content_revision_at ELSE now() END,
 content_hash=excluded.content_hash
 WHERE a.content_hash IS DISTINCT FROM excluded.content_hash
 OR (a.title,a.summary,a.author,a.published_at) IS DISTINCT FROM (excluded.title,excluded.summary,excluded.author,excluded.published_at);
 GET DIAGNOSTICS touched=ROW_COUNT;
 INSERT INTO driftread.article_discoveries(article_id,feed_id,canonical_url,origin,source_url,observed_hash,backfill,backfill_reason)
 SELECT a.id,p_feed_id,a.canonical_url,coalesce(r->>'origin','rss'),f.url,r->>'content_hash',a.backfill,a.backfill_reason
 FROM jsonb_array_elements(p_articles) r JOIN driftread.articles a ON a.feed_id=p_feed_id AND a.url=r->>'url'
 JOIN driftread.feeds f ON f.id=p_feed_id
 ON CONFLICT(article_id,origin,source_url) DO UPDATE SET
 last_seen_at=now(),observed_hash=excluded.observed_hash;
 PERFORM driftread.reconcile_feed_article_count(p_feed_id);
 RETURN touched;
END $$;
REVOKE ALL ON FUNCTION driftread.ingest_article_batch(uuid,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.ingest_article_batch(uuid,jsonb) TO service_role;

NOTIFY pgrst, 'reload schema';
