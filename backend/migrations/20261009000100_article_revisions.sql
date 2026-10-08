-- Source-specific reader IDs remain stable. Exact URLs (minus fragments) group
-- provenance; this intentionally does not guess syndication or strip queries.
ALTER TABLE driftread.articles
 ADD COLUMN IF NOT EXISTS canonical_url text,
 ADD COLUMN IF NOT EXISTS discovered_at timestamptz,
 ADD COLUMN IF NOT EXISTS timeline_at timestamptz,
 ADD COLUMN IF NOT EXISTS backfill boolean NOT NULL DEFAULT false,
 ADD COLUMN IF NOT EXISTS backfill_reason text,
 ADD COLUMN IF NOT EXISTS revision_number bigint NOT NULL DEFAULT 0,
 ADD COLUMN IF NOT EXISTS current_revision_id uuid;
UPDATE driftread.articles SET canonical_url=split_part(url,'#',1),
 discovered_at=fetched_at, timeline_at=least(coalesce(published_at,fetched_at),fetched_at),
 backfill=true,backfill_reason='legacy_import'
 WHERE discovered_at IS NULL;
ALTER TABLE driftread.articles ALTER COLUMN discovered_at SET NOT NULL,
 ALTER COLUMN discovered_at SET DEFAULT now(), ALTER COLUMN timeline_at SET NOT NULL,
 ALTER COLUMN timeline_at SET DEFAULT now();
CREATE INDEX IF NOT EXISTS articles_canonical_url_idx ON driftread.articles(canonical_url);
CREATE INDEX IF NOT EXISTS articles_timeline_idx ON driftread.articles(feed_id,timeline_at DESC,id DESC);

-- Keep historical metadata and source digests, not permanent copies of HTML.
-- Retention still releases bodies from articles; history cannot rehydrate them.
CREATE TABLE IF NOT EXISTS driftread.article_revisions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 article_id uuid NOT NULL REFERENCES driftread.articles(id) ON DELETE CASCADE,
 revision_number bigint NOT NULL,
 content_hash text,
 title text NOT NULL, summary text, author text, published_at timestamptz,
 recorded_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(article_id,revision_number)
);
CREATE TABLE IF NOT EXISTS driftread.article_discoveries (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 article_id uuid NOT NULL REFERENCES driftread.articles(id) ON DELETE CASCADE,
 feed_id uuid NOT NULL REFERENCES driftread.feeds(id) ON DELETE CASCADE,
 canonical_url text NOT NULL,
 origin text NOT NULL CHECK(origin IN ('rss','discover_import','historical_import')),
 source_url text NOT NULL,
 observed_hash text,
 first_seen_at timestamptz NOT NULL DEFAULT now(),
 last_seen_at timestamptz NOT NULL DEFAULT now(),
 backfill boolean NOT NULL DEFAULT false, backfill_reason text,
 UNIQUE(article_id,origin,source_url)
);
CREATE INDEX IF NOT EXISTS article_discoveries_canonical_idx
 ON driftread.article_discoveries(canonical_url,feed_id);
ALTER TABLE driftread.article_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.article_discoveries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.article_revisions,driftread.article_discoveries FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT ON driftread.article_revisions TO service_role;
GRANT SELECT,INSERT,UPDATE,DELETE ON driftread.article_discoveries TO service_role;

CREATE OR REPLACE FUNCTION driftread.prepare_article_revision()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  NEW.canonical_url:=split_part(NEW.url,'#',1);
  NEW.discovered_at:=coalesce(NEW.discovered_at,NEW.fetched_at,now());
  NEW.timeline_at:=least(coalesce(NEW.published_at,NEW.discovered_at),NEW.discovered_at);
  NEW.revision_number:=1; NEW.current_revision_id:=gen_random_uuid();
 ELSIF (NEW.title,NEW.summary,NEW.author,NEW.published_at,NEW.content_hash)
  IS DISTINCT FROM (OLD.title,OLD.summary,OLD.author,OLD.published_at,OLD.content_hash) THEN
  NEW.revision_number:=OLD.revision_number+1; NEW.current_revision_id:=gen_random_uuid();
  NEW.timeline_at:=least(coalesce(NEW.published_at,OLD.discovered_at),OLD.discovered_at);
 END IF;
 IF TG_OP='UPDATE' THEN
  NEW.discovered_at:=OLD.discovered_at;
  NEW.backfill:=OLD.backfill; NEW.backfill_reason:=OLD.backfill_reason;
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION driftread.record_article_revision()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='INSERT' OR NEW.current_revision_id IS DISTINCT FROM OLD.current_revision_id THEN
  INSERT INTO driftread.article_revisions(id,article_id,revision_number,content_hash,title,summary,author,published_at)
  VALUES(NEW.current_revision_id,NEW.id,NEW.revision_number,NEW.content_hash,NEW.title,NEW.summary,NEW.author,NEW.published_at);
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION driftread.reject_article_revision_update()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'article revisions are immutable'; END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='article_revision_prepare' AND tgrelid='driftread.articles'::regclass) THEN
  CREATE TRIGGER article_revision_prepare BEFORE INSERT OR UPDATE ON driftread.articles
   FOR EACH ROW EXECUTE FUNCTION driftread.prepare_article_revision();
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='article_revision_record' AND tgrelid='driftread.articles'::regclass) THEN
  CREATE TRIGGER article_revision_record AFTER INSERT OR UPDATE ON driftread.articles
   FOR EACH ROW EXECUTE FUNCTION driftread.record_article_revision();
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='article_revision_immutable' AND tgrelid='driftread.article_revisions'::regclass) THEN
  CREATE TRIGGER article_revision_immutable BEFORE UPDATE ON driftread.article_revisions
   FOR EACH ROW EXECUTE FUNCTION driftread.reject_article_revision_update();
 END IF;
END $$;
-- Baseline existing snapshots without claiming to reconstruct lost revisions.
UPDATE driftread.articles SET revision_number=1,current_revision_id=gen_random_uuid()
 WHERE current_revision_id IS NULL;
INSERT INTO driftread.article_discoveries(article_id,feed_id,canonical_url,origin,source_url,observed_hash,first_seen_at,last_seen_at,backfill,backfill_reason)
 SELECT a.id,a.feed_id,a.canonical_url,'rss',f.url,a.content_hash,a.discovered_at,a.discovered_at,a.backfill,a.backfill_reason
 FROM driftread.articles a JOIN driftread.feeds f ON f.id=a.feed_id
 ON CONFLICT(article_id,origin,source_url) DO NOTHING;

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
 RETURN touched;
END $$;
REVOKE ALL ON FUNCTION driftread.prepare_article_revision(),driftread.record_article_revision(),driftread.reject_article_revision_update(),driftread.ingest_article_batch(uuid,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.prepare_article_revision(),driftread.record_article_revision(),driftread.reject_article_revision_update(),driftread.ingest_article_batch(uuid,jsonb) TO service_role;
