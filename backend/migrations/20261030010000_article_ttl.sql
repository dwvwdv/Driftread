-- Optional whole-row TTL. This is independent from body compaction and caps;
-- installation never enables deletion. See docs/CRAWLER_OPERATIONS.md.
INSERT INTO driftread.app_settings(key,value)
 VALUES('article.ttl','{"enabled":false,"days":14}'::jsonb) ON CONFLICT(key) DO NOTHING;
INSERT INTO driftread.background_job_kinds(kind) VALUES('article_ttl') ON CONFLICT DO NOTHING;
ALTER TABLE driftread.worker_runs DROP CONSTRAINT IF EXISTS worker_runs_kind_check;
ALTER TABLE driftread.worker_runs ADD CONSTRAINT worker_runs_kind_check
 CHECK(kind IN ('refresh','discovery','retention','source_review','storage','article_cap','article_ttl'));

CREATE INDEX IF NOT EXISTS articles_ttl_timeline_idx
 ON driftread.articles(timeline_at,feed_id,id);

-- Dated entries can be excluded by age at admission. For undated/future-dated
-- entries the original discovery time is gone after deletion, so remember a
-- bounded set of hashes instead. One private row per source, at most 1000
-- hashes, never permanent per-article tombstones. Older overflow may reappear.
CREATE TABLE IF NOT EXISTS driftread.article_ttl_reingest_guard(
 feed_id uuid PRIMARY KEY REFERENCES driftread.feeds(id) ON DELETE CASCADE,
 url_hashes text[] NOT NULL DEFAULT '{}' CHECK(cardinality(url_hashes)<=1000)
);
ALTER TABLE driftread.article_ttl_reingest_guard ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.article_ttl_reingest_guard FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON driftread.article_ttl_reingest_guard TO service_role;

-- Preserve administrator tombstones and cap watermarks. Existing IDs always
-- receive their updates, including protected articles older than the TTL.
-- The TTL admission policy/cache are ignored while its switch is off. Unlike
-- cap watermarks there is no persistent date watermark to reset when widening
-- the TTL; genuinely old publications arriving today are excluded while on.
CREATE OR REPLACE FUNCTION driftread.guard_removed_article()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE mark timestamptz; policy jsonb; days integer;
BEGIN
 IF EXISTS(SELECT 1 FROM driftread.article_cleanup_tombstones WHERE feed_id=NEW.feed_id AND url=NEW.url) THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM driftread.articles a WHERE a.feed_id=NEW.feed_id AND a.url=NEW.url) THEN RETURN NEW; END IF;
 SELECT f.article_cap_evicted_through INTO mark FROM driftread.feeds f WHERE f.id=NEW.feed_id;
 IF mark IS NOT NULL AND NEW.timeline_at<=mark THEN RETURN NULL; END IF;
 SELECT value INTO policy FROM driftread.app_settings WHERE key='article.ttl';
 IF policy->'enabled'='true'::jsonb AND jsonb_typeof(policy->'days')='number'
  AND (policy->>'days') ~ '^[0-9]{1,4}$' THEN
  days:=(policy->>'days')::integer;
  IF days BETWEEN 1 AND 3650 THEN
   IF NEW.timeline_at<now()-make_interval(days=>days) THEN RETURN NULL; END IF;
   IF EXISTS(SELECT 1 FROM driftread.article_ttl_reingest_guard g
    WHERE g.feed_id=NEW.feed_id AND md5(NEW.url)=ANY(g.url_hashes)) THEN RETURN NULL; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION driftread.guard_removed_article() FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION driftread.article_ttl_candidates(
 p_feed_id uuid,p_cutoff timestamptz,p_budget integer)
RETURNS uuid[] LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT coalesce(array_agg(r.id ORDER BY r.timeline_at,r.id),'{}') FROM (
  SELECT a.id,a.timeline_at FROM driftread.articles a
  WHERE a.feed_id=p_feed_id AND a.timeline_at<p_cutoff AND a.staged_at IS NULL
   AND NOT EXISTS(SELECT 1 FROM driftread.user_bookmarks b WHERE b.article_id=a.id)
   AND NOT EXISTS(SELECT 1 FROM driftread.fact_articles fa WHERE fa.article_id=a.id)
  ORDER BY a.timeline_at,a.id LIMIT p_budget
 ) r
$$;
REVOKE ALL ON FUNCTION driftread.article_ttl_candidates(uuid,timestamptz,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.article_ttl_candidates(uuid,timestamptz,integer) TO service_role;

-- Same feed-first, nonblocking lock order as ingestion/cap. FK KEY SHARE locks
-- from in-flight bookmarks/curation make the row lock skip that article. A new
-- statement snapshot after locking rechecks protection before permanent delete.
-- Read receipts and subscriptions do not protect, matching article_cap.
CREATE OR REPLACE FUNCTION driftread.enforce_article_ttl(
 p_days integer DEFAULT 14,p_limit integer DEFAULT 500,p_dry_run boolean DEFAULT true)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record; cutoff timestamptz; ids uuid[]; locked uuid[]; removed integer;
 new_hashes text[]; old_hashes text[]; cache text[]; affected bigint; delay numeric;
 expired bigint:=0; protected bigint:=0; eligible integer:=0; deleted integer:=0;
 reads_affected bigint:=0; skipped integer:=0; deletion_delay_seconds numeric:=0;
BEGIN
 IF p_days IS NULL OR p_days NOT BETWEEN 1 AND 3650 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000
  OR p_dry_run IS NULL THEN RAISE EXCEPTION 'invalid article TTL parameters'; END IF;
 cutoff:=now()-make_interval(days=>p_days);
 IF NOT p_dry_run AND NOT pg_try_advisory_xact_lock(hashtextextended('driftread:article-ttl',0)) THEN
  RETURN jsonb_build_object('dry_run',false,'expired',0,'protected',0,'eligible',0,'deleted',0,
   'reads_affected',0,'skipped_feeds',0,'deletion_delay_seconds',0);
 END IF;
 SELECT count(*),count(*) FILTER(WHERE a.staged_at IS NOT NULL
  OR EXISTS(SELECT 1 FROM driftread.user_bookmarks b WHERE b.article_id=a.id)
  OR EXISTS(SELECT 1 FROM driftread.fact_articles fa WHERE fa.article_id=a.id))
 INTO expired,protected FROM driftread.articles a WHERE a.timeline_at<cutoff;
 FOR source IN
  SELECT a.feed_id AS id,min(a.timeline_at) AS oldest FROM driftread.articles a
  WHERE a.timeline_at<cutoff AND a.staged_at IS NULL
   AND NOT EXISTS(SELECT 1 FROM driftread.user_bookmarks b WHERE b.article_id=a.id)
   AND NOT EXISTS(SELECT 1 FROM driftread.fact_articles fa WHERE fa.article_id=a.id)
  GROUP BY a.feed_id ORDER BY oldest,a.feed_id
 LOOP
  EXIT WHEN eligible>=p_limit;
  ids:=driftread.article_ttl_candidates(source.id,cutoff,p_limit-eligible);
  CONTINUE WHEN cardinality(ids)=0;
  IF NOT p_dry_run THEN
   PERFORM 1 FROM driftread.feeds f WHERE f.id=source.id FOR UPDATE SKIP LOCKED;
   IF NOT FOUND THEN skipped:=skipped+1; CONTINUE; END IF;
   ids:=driftread.article_ttl_candidates(source.id,cutoff,p_limit-eligible);
   CONTINUE WHEN cardinality(ids)=0;
  END IF;
  eligible:=eligible+cardinality(ids);
  IF p_dry_run THEN
   SELECT count(*) INTO affected FROM driftread.user_article_reads r WHERE r.article_id=ANY(ids);
   reads_affected:=reads_affected+affected;
   CONTINUE;
  END IF;
  SELECT coalesce(array_agg(l.id),'{}') INTO locked FROM (
   SELECT a.id FROM driftread.articles a WHERE a.id=ANY(ids) ORDER BY a.id FOR UPDATE SKIP LOCKED) l;
  WITH candidates AS MATERIALIZED (
   SELECT a.id,(SELECT count(*) FROM driftread.user_article_reads r WHERE r.article_id=a.id) AS read_count
   FROM driftread.articles a WHERE a.id=ANY(locked) AND a.timeline_at<cutoff AND a.staged_at IS NULL
    AND NOT EXISTS(SELECT 1 FROM driftread.user_bookmarks b WHERE b.article_id=a.id)
    AND NOT EXISTS(SELECT 1 FROM driftread.fact_articles fa WHERE fa.article_id=a.id)
  ), gone AS (
   DELETE FROM driftread.articles a USING candidates c WHERE a.id=c.id
   RETURNING a.id,a.url,a.timeline_at,a.published_at,a.discovered_at,c.read_count)
  SELECT count(*),coalesce(sum(read_count),0),coalesce(sum(extract(epoch FROM cutoff-timeline_at)),0),
   coalesce(array_agg(md5(url) ORDER BY timeline_at DESC,id DESC)
    FILTER(WHERE published_at IS NULL OR published_at>discovered_at),'{}')
  INTO removed,affected,delay,new_hashes FROM gone;
  CONTINUE WHEN removed=0;
  deleted:=deleted+removed; reads_affected:=reads_affected+affected;
  deletion_delay_seconds:=deletion_delay_seconds+delay;
  IF cardinality(new_hashes)>0 THEN
   SELECT url_hashes INTO old_hashes FROM driftread.article_ttl_reingest_guard WHERE feed_id=source.id;
   SELECT coalesce(array_agg(k.hash ORDER BY k.pos),'{}') INTO cache FROM (
    SELECT hash,min(pos) AS pos FROM unnest(new_hashes||coalesce(old_hashes,'{}')) WITH ORDINALITY h(hash,pos)
    GROUP BY hash ORDER BY min(pos) LIMIT 1000) k;
   INSERT INTO driftread.article_ttl_reingest_guard(feed_id,url_hashes) VALUES(source.id,cache)
    ON CONFLICT(feed_id) DO UPDATE SET url_hashes=excluded.url_hashes;
  END IF;
  PERFORM driftread.reconcile_feed_article_count(source.id);
 END LOOP;
 RETURN jsonb_build_object('dry_run',p_dry_run,'expired',expired,'protected',protected,
  'eligible',eligible,'deleted',deleted,'reads_affected',reads_affected,'skipped_feeds',skipped,
  'deletion_delay_seconds',deletion_delay_seconds);
END $$;
REVOKE ALL ON FUNCTION driftread.enforce_article_ttl(integer,integer,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.enforce_article_ttl(integer,integer,boolean) TO service_role;

-- Worker-run aggregates only; a read never starts a cleanup job. Delay is the
-- weighted mean lateness beyond each run's configured TTL, not article age.
CREATE OR REPLACE FUNCTION driftread.article_ttl_activity(p_since timestamptz)
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('deleted',coalesce(sum((summary->>'deleted')::bigint),0),
  'average_delay_seconds',sum((summary->>'deletion_delay_seconds')::numeric)
   /nullif(sum((summary->>'deleted')::bigint),0))
 FROM driftread.worker_runs WHERE kind='article_ttl' AND status='succeeded' AND finished_at>=p_since
$$;
REVOKE ALL ON FUNCTION driftread.article_ttl_activity(timestamptz) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.article_ttl_activity(timestamptz) TO service_role;

NOTIFY pgrst, 'reload schema';
