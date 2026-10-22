-- Per-source article cap. Unlike content compaction this deletes whole article
-- rows, so the protected set and the re-ingestion watermark live in one place.
-- See docs/CRAWLER_OPERATIONS.md「每來源文章上限」.
--
-- * article_cap: per-source override; NULL uses the global article.cap setting.
-- * article_cap_evicted_through: the newest timeline_at evicted so far. One value
--   per source instead of a tombstone per URL, so it never grows. An item that
--   is no newer than an evicted one would be evicted again on the next run.
ALTER TABLE driftread.feeds
 ADD COLUMN IF NOT EXISTS article_cap integer,
 ADD COLUMN IF NOT EXISTS article_cap_evicted_through timestamptz;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint
  WHERE conrelid='driftread.feeds'::regclass AND conname='feeds_article_cap_check') THEN
  ALTER TABLE driftread.feeds ADD CONSTRAINT feeds_article_cap_check
   CHECK(article_cap IS NULL OR article_cap BETWEEN 100 AND 100000);
 END IF;
END $$;

INSERT INTO driftread.background_job_kinds(kind) VALUES('article_cap') ON CONFLICT DO NOTHING;
ALTER TABLE driftread.worker_runs DROP CONSTRAINT IF EXISTS worker_runs_kind_check;
ALTER TABLE driftread.worker_runs ADD CONSTRAINT worker_runs_kind_check
 CHECK(kind IN ('refresh','discovery','retention','source_review','storage','article_cap'));

-- Same trigger as administrator deletes. A row that still exists (bookmarked,
-- curated or kept for extraction) keeps receiving upserts; only a new insert
-- at or below the watermark is dropped. timeline_at is recomputed here the same
-- way prepare_article_revision does, because trigger order is by name.
CREATE OR REPLACE FUNCTION driftread.guard_removed_article()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE mark timestamptz; seen timestamptz;
BEGIN
 IF EXISTS(SELECT 1 FROM driftread.article_cleanup_tombstones WHERE feed_id=NEW.feed_id AND url=NEW.url) THEN RETURN NULL; END IF;
 SELECT f.article_cap_evicted_through INTO mark FROM driftread.feeds f WHERE f.id=NEW.feed_id;
 IF mark IS NOT NULL THEN
  seen:=coalesce(NEW.discovered_at,NEW.fetched_at,now());
  IF least(coalesce(NEW.published_at,seen),seen)<=mark
   AND NOT EXISTS(SELECT 1 FROM driftread.articles a WHERE a.feed_id=NEW.feed_id AND a.url=NEW.url) THEN
   RETURN NULL;
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION driftread.guard_removed_article() FROM PUBLIC,anon,authenticated;

-- Keeps the newest cap unprotected rows per source. Bookmarks (favorite and
-- read_later) and manual event links are never deleted and do not count toward
-- the cap; read receipts are not protected and cascade with the row. With
-- p_keep_unextracted, rows whose current version has not been mined for links
-- survive up to 1.5x the cap. Oldest by timeline_at first, which clamps future
-- publication dates to the first time the row was seen.
CREATE OR REPLACE FUNCTION driftread.enforce_article_cap(
 p_default_cap integer DEFAULT 1000,p_limit integer DEFAULT 500,
 p_dry_run boolean DEFAULT true,p_keep_unextracted boolean DEFAULT true)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record; budget integer; ids uuid[]; locked uuid[]; mark timestamptz; removed integer;
 over_cap integer:=0; excess bigint:=0; eligible integer:=0; deleted integer:=0; skipped integer:=0;
BEGIN
 IF p_default_cap IS NULL OR p_default_cap NOT BETWEEN 100 AND 100000 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000
  OR p_dry_run IS NULL OR p_keep_unextracted IS NULL THEN
  RAISE EXCEPTION 'invalid article cap parameters';
 END IF;
 IF NOT p_dry_run AND NOT pg_try_advisory_xact_lock(hashtextextended('driftread:article-cap',0)) THEN
  RETURN jsonb_build_object('dry_run',false,'feeds_over_cap',0,'excess',0,'eligible',0,'deleted',0,'skipped_feeds',0);
 END IF;
 FOR source IN
  SELECT f.id,coalesce(f.article_cap,p_default_cap) AS cap,f.article_count
  FROM driftread.feeds f WHERE f.article_count>coalesce(f.article_cap,p_default_cap)
  ORDER BY f.article_count-coalesce(f.article_cap,p_default_cap) DESC,f.id
 LOOP
  -- article_count includes protected rows, so excess is an upper bound.
  over_cap:=over_cap+1; excess:=excess+source.article_count-source.cap;
  budget:=p_limit-eligible;
  CONTINUE WHEN budget<=0;
  IF NOT p_dry_run THEN
   -- ingest_article_batch locks the feed row first too; a source being
   -- refreshed right now is skipped until the next run instead of waited on.
   PERFORM 1 FROM driftread.feeds f WHERE f.id=source.id FOR UPDATE SKIP LOCKED;
   IF NOT FOUND THEN skipped:=skipped+1; CONTINUE; END IF;
  END IF;
  SELECT coalesce(array_agg(r.id ORDER BY r.timeline_at,r.id),'{}') INTO ids FROM (
   SELECT k.id,k.timeline_at FROM (
    SELECT a.id,a.timeline_at,
     a.discovery_extracted_at IS NOT NULL AND a.discovery_extracted_hash IS NOT DISTINCT FROM a.content_hash AS extracted,
     row_number() OVER (ORDER BY a.timeline_at DESC,a.id DESC) AS rn
    FROM driftread.articles a WHERE a.feed_id=source.id
     AND NOT EXISTS(SELECT 1 FROM driftread.user_bookmarks b WHERE b.article_id=a.id)
     AND NOT EXISTS(SELECT 1 FROM driftread.fact_articles fa WHERE fa.article_id=a.id)
   ) k WHERE k.rn>source.cap AND (NOT p_keep_unextracted OR k.extracted OR k.rn>source.cap+source.cap/2)
   ORDER BY k.timeline_at,k.id LIMIT budget
  ) r;
  CONTINUE WHEN cardinality(ids)=0;
  eligible:=eligible+cardinality(ids);
  CONTINUE WHEN p_dry_run;
  -- FK inserts (bookmark, read, curation) hold KEY SHARE until commit. Lock
  -- first, then re-check in a new statement whose snapshot sees anything that
  -- committed meanwhile; a reference that wins the race keeps the article.
  SELECT coalesce(array_agg(l.id),'{}') INTO locked FROM (
   SELECT a.id FROM driftread.articles a WHERE a.id=ANY(ids) ORDER BY a.id FOR UPDATE SKIP LOCKED) l;
  WITH gone AS (
   DELETE FROM driftread.articles a WHERE a.id=ANY(locked)
    AND NOT EXISTS(SELECT 1 FROM driftread.user_bookmarks b WHERE b.article_id=a.id)
    AND NOT EXISTS(SELECT 1 FROM driftread.fact_articles fa WHERE fa.article_id=a.id)
   RETURNING a.timeline_at)
  SELECT count(*),max(timeline_at) INTO removed,mark FROM gone;
  CONTINUE WHEN removed=0;
  deleted:=deleted+removed;
  UPDATE driftread.feeds SET article_cap_evicted_through=greatest(article_cap_evicted_through,mark)
   WHERE id=source.id AND article_cap_evicted_through IS DISTINCT FROM greatest(article_cap_evicted_through,mark);
  PERFORM driftread.reconcile_feed_article_count(source.id);
 END LOOP;
 RETURN jsonb_build_object('dry_run',p_dry_run,'feeds_over_cap',over_cap,'excess',excess,
  'eligible',eligible,'deleted',deleted,'skipped_feeds',skipped);
END $$;
REVOKE ALL ON FUNCTION driftread.enforce_article_cap(integer,integer,boolean,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.enforce_article_cap(integer,integer,boolean,boolean) TO service_role;

NOTIFY pgrst, 'reload schema';
