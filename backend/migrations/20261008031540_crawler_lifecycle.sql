-- Discovery material has a lifecycle independent of reader article identity.
ALTER TABLE driftread.articles
  ADD COLUMN IF NOT EXISTS content_hash text,
  ADD COLUMN IF NOT EXISTS discovery_extracted_hash text,
  ADD COLUMN IF NOT EXISTS discovery_extracted_at timestamptz,
  ADD COLUMN IF NOT EXISTS content_compacted_at timestamptz,
  ADD COLUMN IF NOT EXISTS discovery_retry_at timestamptz;
CREATE INDEX IF NOT EXISTS articles_discovery_pending_idx
 ON driftread.articles(feed_id, fetched_at, id)
 WHERE discovery_extracted_at IS NULL OR discovery_extracted_hash IS DISTINCT FROM content_hash;
CREATE INDEX IF NOT EXISTS articles_compaction_idx
 ON driftread.articles(fetched_at, feed_id, id)
 WHERE content_compacted_at IS NULL AND content IS NOT NULL;

CREATE OR REPLACE FUNCTION driftread.ingest_article_batch(p_feed_id uuid, p_articles jsonb)
RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE touched integer;
BEGIN
 IF jsonb_typeof(p_articles) IS DISTINCT FROM 'array' OR jsonb_array_length(p_articles)>200 THEN
  RAISE EXCEPTION 'article batch must be an array of at most 200 rows';
 END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_articles) r WHERE coalesce(r->>'content_hash','') !~ '^[0-9a-f]{64}$') THEN
  RAISE EXCEPTION 'content_hash must be a SHA256 digest';
 END IF;
 INSERT INTO driftread.articles AS a(feed_id,title,url,summary,content,author,published_at,content_hash)
 SELECT p_feed_id,r.title,r.url,r.summary,r.content,r.author,r.published_at,r.content_hash
 FROM jsonb_to_recordset(p_articles) AS r(title text,url text,summary text,content text,author text,published_at timestamptz,content_hash text)
 ORDER BY r.url
 ON CONFLICT(feed_id,url) DO UPDATE SET
 title=excluded.title,summary=excluded.summary,author=excluded.author,published_at=excluded.published_at,
 content=CASE WHEN a.content_hash=excluded.content_hash AND a.content_compacted_at IS NOT NULL THEN a.content ELSE excluded.content END,
 discovery_extracted_hash=CASE WHEN a.content_hash=excluded.content_hash THEN a.discovery_extracted_hash ELSE NULL END,
 discovery_extracted_at=CASE WHEN a.content_hash=excluded.content_hash THEN a.discovery_extracted_at ELSE NULL END,
 content_compacted_at=CASE WHEN a.content_hash=excluded.content_hash THEN a.content_compacted_at ELSE NULL END,
 discovery_retry_at=CASE WHEN a.content_hash=excluded.content_hash THEN a.discovery_retry_at ELSE NULL END,
 content_hash=excluded.content_hash
 WHERE a.content_hash IS DISTINCT FROM excluded.content_hash
 OR (a.title,a.summary,a.author,a.published_at) IS DISTINCT FROM (excluded.title,excluded.summary,excluded.author,excluded.published_at);
 GET DIAGNOSTICS touched=ROW_COUNT;
 RETURN touched;
END $$;

-- Legacy bodies travel as POST JSON, never as unbounded URL filters. A source
-- revision that won the race must not acquire a hash for an older snapshot.
CREATE OR REPLACE FUNCTION driftread.initialize_article_source_hash(p_feed_id uuid,p_url text,p_content text,p_summary text,p_hash text)
RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE touched integer;
BEGIN
 IF p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid content hash'; END IF;
 UPDATE driftread.articles SET content_hash=p_hash
 WHERE feed_id=p_feed_id AND url=p_url AND content_hash IS NULL
 AND content IS NOT DISTINCT FROM p_content AND summary IS NOT DISTINCT FROM p_summary;
 GET DIAGNOSTICS touched=ROW_COUNT;
 RETURN touched;
END $$;

CREATE OR REPLACE FUNCTION driftread.pending_article_discovery(p_feed_id uuid,p_limit integer DEFAULT 20)
RETURNS TABLE(id uuid,url text,title text,content text,summary text,author text,published_at timestamptz,content_hash text)
LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT a.id,a.url,a.title,a.content,a.summary,a.author,a.published_at,a.content_hash
 FROM driftread.articles a WHERE a.feed_id=p_feed_id
 AND (a.discovery_retry_at IS NULL OR a.discovery_retry_at<=now())
 AND (a.discovery_extracted_at IS NULL OR a.discovery_extracted_hash IS DISTINCT FROM a.content_hash)
 ORDER BY a.fetched_at,a.id LIMIT least(greatest(p_limit,1),200)
$$;

-- First release compacts only full content. Never delete article identities.
CREATE OR REPLACE FUNCTION driftread.compact_article_content(p_retention_days integer DEFAULT 30,p_limit integer DEFAULT 200,p_dry_run boolean DEFAULT true)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE item record; affected integer:=0; eligible integer:=0; bytes bigint:=0; locked_id uuid;
BEGIN
 IF p_retention_days IS NULL OR p_retention_days<7 OR p_retention_days>3650 OR p_limit IS NULL OR p_limit<1 OR p_limit>1000 OR p_dry_run IS NULL THEN
  RAISE EXCEPTION 'invalid retention parameters';
 END IF;
 -- Serialize maintenance only; user-facing writes remain independently scoped.
 IF NOT p_dry_run AND NOT pg_try_advisory_xact_lock(hashtextextended('driftread:content-compaction',0)) THEN
  RETURN jsonb_build_object('dry_run',false,'eligible',0,'compacted',0,'content_bytes',0);
 END IF;
 FOR item IN SELECT a.id,a.feed_id FROM driftread.articles a
  WHERE a.fetched_at<now()-make_interval(days=>p_retention_days)
   AND nullif(a.content,'') IS NOT NULL AND a.content_compacted_at IS NULL
   AND a.content_hash IS NOT NULL AND a.discovery_extracted_hash=a.content_hash AND a.discovery_extracted_at IS NOT NULL
   AND NOT EXISTS(SELECT 1 FROM driftread.user_feeds s WHERE s.feed_id=a.feed_id)
   AND NOT EXISTS(SELECT 1 FROM driftread.user_bookmarks b WHERE b.article_id=a.id)
   AND NOT EXISTS(SELECT 1 FROM driftread.user_article_reads r WHERE r.article_id=a.id)
  ORDER BY a.feed_id,a.id LIMIT p_limit
 LOOP
  IF NOT p_dry_run THEN
   -- FK inserts take KEY SHARE on these parents. Lock then re-check using a
   -- fresh statement snapshot, so previously committed interactions win.
   locked_id:=NULL;
   SELECT f.id INTO locked_id FROM driftread.feeds f WHERE f.id=item.feed_id FOR UPDATE SKIP LOCKED;
   IF locked_id IS NULL THEN CONTINUE; END IF;
   locked_id:=NULL;
   SELECT a.id INTO locked_id FROM driftread.articles a WHERE a.id=item.id FOR UPDATE SKIP LOCKED;
   IF locked_id IS NULL THEN CONTINUE; END IF;
  END IF;
  SELECT octet_length(a.content) INTO affected FROM driftread.articles a
   WHERE a.id=item.id AND nullif(a.content,'') IS NOT NULL AND a.content_compacted_at IS NULL
    AND a.fetched_at<now()-make_interval(days=>p_retention_days)
    AND a.content_hash IS NOT NULL AND a.discovery_extracted_hash=a.content_hash AND a.discovery_extracted_at IS NOT NULL
    AND NOT EXISTS(SELECT 1 FROM driftread.user_feeds s WHERE s.feed_id=a.feed_id)
    AND NOT EXISTS(SELECT 1 FROM driftread.user_bookmarks b WHERE b.article_id=a.id)
    AND NOT EXISTS(SELECT 1 FROM driftread.user_article_reads r WHERE r.article_id=a.id);
  IF affected IS NULL THEN CONTINUE; END IF;
  eligible:=eligible+1; bytes:=bytes+affected;
  IF NOT p_dry_run THEN
   UPDATE driftread.articles SET content=NULL,content_compacted_at=now() WHERE id=item.id;
  END IF;
 END LOOP;
 RETURN jsonb_build_object('dry_run',p_dry_run,'eligible',eligible,'compacted',CASE WHEN p_dry_run THEN 0 ELSE eligible END,'content_bytes',bytes);
END $$;

CREATE TABLE IF NOT EXISTS driftread.worker_heartbeats(
 worker_id text PRIMARY KEY,hostname text NOT NULL,process_id integer NOT NULL,
 started_at timestamptz NOT NULL,heartbeat_at timestamptz NOT NULL,
 status text NOT NULL CHECK(status IN ('running','stopped')),active_runs jsonb NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS driftread.worker_runs(
 id uuid PRIMARY KEY,worker_id text NOT NULL,kind text NOT NULL CHECK(kind IN ('refresh','discovery','retention')),
 status text NOT NULL CHECK(status IN ('running','succeeded','partial','failed','cancelled')),
 started_at timestamptz NOT NULL,finished_at timestamptz,summary jsonb NOT NULL DEFAULT '{}',error text);
CREATE INDEX IF NOT EXISTS worker_runs_started_idx ON driftread.worker_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS worker_runs_retention_idx ON driftread.worker_runs(finished_at) WHERE finished_at IS NOT NULL;
ALTER TABLE driftread.worker_heartbeats ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.worker_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.worker_heartbeats,driftread.worker_runs FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON driftread.worker_heartbeats,driftread.worker_runs TO service_role;

CREATE OR REPLACE FUNCTION driftread.prune_worker_operations(p_before timestamptz)
RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE deleted integer; remaining integer; h_deleted integer;
BEGIN
 IF p_before IS NULL OR p_before>now()-interval '1 day' THEN RAISE EXCEPTION 'invalid prune cutoff'; END IF;
 WITH expired AS(SELECT r.id FROM driftread.worker_runs r
  WHERE (r.finished_at<p_before) OR (r.started_at<p_before AND r.status='running' AND NOT EXISTS(
   SELECT 1 FROM driftread.worker_heartbeats h WHERE h.worker_id=r.worker_id AND h.status='running' AND h.heartbeat_at>now()-interval '90 seconds'))
  ORDER BY r.started_at LIMIT 1000 FOR UPDATE SKIP LOCKED)
 DELETE FROM driftread.worker_runs r USING expired e WHERE r.id=e.id;
 GET DIAGNOSTICS deleted=ROW_COUNT;
 remaining:=1000-deleted;
 WITH expired AS(SELECT h.worker_id FROM driftread.worker_heartbeats h WHERE h.heartbeat_at<p_before
  ORDER BY h.heartbeat_at LIMIT remaining FOR UPDATE SKIP LOCKED)
 DELETE FROM driftread.worker_heartbeats h USING expired e WHERE h.worker_id=e.worker_id;
 GET DIAGNOSTICS h_deleted=ROW_COUNT;
 RETURN deleted+h_deleted;
END $$;
REVOKE ALL ON FUNCTION driftread.ingest_article_batch(uuid,jsonb),driftread.pending_article_discovery(uuid,integer),driftread.compact_article_content(integer,integer,boolean),driftread.prune_worker_operations(timestamptz) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.ingest_article_batch(uuid,jsonb),driftread.pending_article_discovery(uuid,integer),driftread.compact_article_content(integer,integer,boolean),driftread.prune_worker_operations(timestamptz) TO service_role;
NOTIFY pgrst,'reload schema';
CREATE OR REPLACE FUNCTION driftread.article_storage_stats()
RETURNS jsonb LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('article_count',count(*),
 'full_content_count',count(*) FILTER(WHERE nullif(content,'') IS NOT NULL),
 'summary_only_count',count(*) FILTER(WHERE nullif(content,'') IS NULL AND nullif(summary,'') IS NOT NULL),
 'pending_discovery_count',count(*) FILTER(WHERE discovery_extracted_at IS NULL OR discovery_extracted_hash IS DISTINCT FROM content_hash),
 'compacted_count',count(*) FILTER(WHERE content_compacted_at IS NOT NULL),
 'table_bytes',pg_table_size('driftread.articles'::regclass),
 'index_bytes',pg_indexes_size('driftread.articles'::regclass),
 'total_bytes',pg_total_relation_size('driftread.articles'::regclass),
 'database_bytes',pg_database_size(current_database())) FROM driftread.articles
$$;
REVOKE ALL ON FUNCTION driftread.article_storage_stats() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.article_storage_stats() TO service_role;
REVOKE ALL ON FUNCTION driftread.initialize_article_source_hash(uuid,text,text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.initialize_article_source_hash(uuid,text,text,text,text) TO service_role;
NOTIFY pgrst,'reload schema';
