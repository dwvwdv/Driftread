-- Explicit administrator selections only; similarity evidence never deletes.
ALTER TABLE driftread.articles ADD COLUMN IF NOT EXISTS staged_at timestamptz;
CREATE INDEX IF NOT EXISTS articles_staged_idx ON driftread.articles(staged_at,id) WHERE staged_at IS NOT NULL;
CREATE TABLE IF NOT EXISTS driftread.article_cleanup_tombstones (
 feed_id uuid NOT NULL REFERENCES driftread.feeds(id) ON DELETE CASCADE,
 url text NOT NULL, deleted_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(feed_id,url)
);
ALTER TABLE driftread.article_cleanup_tombstones ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.article_cleanup_tombstones FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,DELETE ON driftread.article_cleanup_tombstones TO service_role;

CREATE OR REPLACE FUNCTION driftread.guard_removed_article()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM driftread.article_cleanup_tombstones WHERE feed_id=NEW.feed_id AND url=NEW.url) THEN RETURN NULL; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION driftread.guard_removed_article() FROM PUBLIC,anon,authenticated;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='articles_cleanup_guard' AND tgrelid='driftread.articles'::regclass) THEN
  CREATE TRIGGER articles_cleanup_guard BEFORE INSERT ON driftread.articles FOR EACH ROW EXECUTE FUNCTION driftread.guard_removed_article();
 END IF;
END $$;

CREATE OR REPLACE FUNCTION driftread.list_cleanup_items(
 p_kind text,p_rule text DEFAULT 'all',p_state text DEFAULT 'active',p_query text DEFAULT '',
 p_limit integer DEFAULT 50,p_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 IF p_kind NOT IN ('feeds','articles') OR p_rule NOT IN ('all','category','host','title','url','content')
  OR p_state NOT IN ('active','staged','all') OR p_limit NOT BETWEEN 1 AND 50 OR p_offset<0 OR length(p_query)>200 THEN
  RAISE EXCEPTION 'invalid housekeeping query';
 END IF;
 WITH inventory AS (
  SELECT f.id,f.title,f.url,NULL::uuid AS feed_id,NULL::text AS feed_title,f.category,
   f.archived_at AS staged_at,f.article_count,
   CASE p_rule WHEN 'category' THEN nullif(btrim(f.category),'')
    WHEN 'host' THEN lower(substring(coalesce(f.website_url,f.url) FROM '^https?://([^/:]+)'))
    WHEN 'title' THEN nullif(lower(regexp_replace(btrim(f.title),'\s+',' ','g')),'')
    WHEN 'all' THEN f.id::text END AS group_key
  FROM driftread.feeds f WHERE p_kind='feeds'
  UNION ALL
  SELECT a.id,a.title,a.url,a.feed_id,f.title,f.category,a.staged_at,1,
   CASE p_rule WHEN 'category' THEN nullif(btrim(f.category),'')
    WHEN 'url' THEN nullif(a.canonical_url,'')
    WHEN 'title' THEN CASE WHEN length(btrim(a.title))>=8 THEN lower(regexp_replace(btrim(a.title),'\s+',' ','g')) END
    WHEN 'content' THEN CASE WHEN length(coalesce(a.content,''))>=100 THEN a.content_hash END
    WHEN 'all' THEN a.id::text END
  FROM driftread.articles a JOIN driftread.feeds f ON f.id=a.feed_id WHERE p_kind='articles'
 ), grouped AS (
  SELECT *,count(*) OVER (PARTITION BY group_key) AS group_size FROM inventory
  WHERE group_key IS NOT NULL AND (p_state='all' OR (p_state='staged')=(staged_at IS NOT NULL))
 ), filtered AS (
  SELECT * FROM grouped WHERE (p_rule='all' OR group_size>1)
   AND (p_query='' OR strpos(lower(concat_ws(' ',title,url,feed_title,category,group_key)),lower(p_query))>0)
 ), page AS (
  SELECT * FROM filtered ORDER BY group_size DESC,group_key,id LIMIT p_limit OFFSET p_offset
 ) SELECT jsonb_build_object('items',coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY group_size DESC,group_key,id) FROM page p),'[]'::jsonb),
  'total',(SELECT count(*) FROM filtered),'limit',p_limit,'offset',p_offset) INTO result;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION driftread.list_cleanup_items(text,text,text,text,integer,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.list_cleanup_items(text,text,text,text,integer,integer) TO service_role;

CREATE OR REPLACE FUNCTION driftread.apply_cleanup_batch(
 p_kind text,p_ids uuid[],p_action text,p_dry_run boolean DEFAULT true,p_confirm_delete boolean DEFAULT false
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE item uuid; source uuid; staged timestamptz; impact jsonb; results jsonb:='[]'; found_row boolean;
BEGIN
 IF p_kind NOT IN ('feeds','articles') OR p_action NOT IN ('stage','restore','delete')
  OR coalesce(cardinality(p_ids),0) NOT BETWEEN 1 AND 50 OR array_position(p_ids,NULL) IS NOT NULL
  OR p_dry_run IS NULL OR p_confirm_delete IS NULL THEN RAISE EXCEPTION 'invalid housekeeping batch'; END IF;
 IF p_action='delete' AND NOT p_dry_run AND NOT p_confirm_delete THEN RAISE EXCEPTION 'explicit delete confirmation required'; END IF;
 -- Feed-before-article order matches ingestion; sorting avoids batch lock inversions.
 IF p_kind='feeds' THEN
  PERFORM 1 FROM driftread.feeds WHERE id=ANY(p_ids) ORDER BY id FOR UPDATE;
 ELSE
  PERFORM 1 FROM driftread.feeds WHERE id IN (SELECT feed_id FROM driftread.articles WHERE id=ANY(p_ids)) ORDER BY id FOR UPDATE;
 END IF;
 FOR item IN SELECT DISTINCT unnest(p_ids) ORDER BY 1 LOOP
  IF p_kind='feeds' THEN
   SELECT archived_at,id INTO staged,source FROM driftread.feeds WHERE id=item FOR UPDATE;
  ELSE
   SELECT staged_at,feed_id INTO staged,source FROM driftread.articles WHERE id=item FOR UPDATE;
  END IF;
  found_row:=FOUND;
  IF NOT found_row THEN
   results:=results||jsonb_build_array(jsonb_build_object('id',item,'ok',false,'error','not_found')); CONTINUE;
  END IF;
  IF p_action='delete' AND staged IS NULL THEN
   results:=results||jsonb_build_array(jsonb_build_object('id',item,'ok',false,'error','stage_first')); CONTINUE;
  END IF;
  SELECT jsonb_build_object('articles',count(*),
   'bookmarks',(SELECT count(*) FROM driftread.user_bookmarks b JOIN driftread.articles a ON a.id=b.article_id
     WHERE (p_kind='feeds' AND a.feed_id=item) OR (p_kind='articles' AND a.id=item)),
   'reads',(SELECT count(*) FROM driftread.user_article_reads r JOIN driftread.articles a ON a.id=r.article_id
     WHERE (p_kind='feeds' AND a.feed_id=item) OR (p_kind='articles' AND a.id=item)),
   'subscriptions',CASE WHEN p_kind='feeds' THEN (SELECT count(*) FROM driftread.user_feeds WHERE feed_id=item) ELSE 0 END)
  INTO impact FROM driftread.articles a WHERE (p_kind='feeds' AND a.feed_id=item) OR (p_kind='articles' AND a.id=item);
  IF NOT p_dry_run THEN
   IF p_kind='feeds' THEN
    IF p_action='delete' THEN DELETE FROM driftread.feeds WHERE id=item;
    ELSIF p_action='stage' THEN UPDATE driftread.feeds SET archived_at=coalesce(archived_at,now()) WHERE id=item;
    ELSE UPDATE driftread.feeds SET archived_at=NULL,next_fetch_at=now(),
      admitted_at=CASE WHEN last_failure_reason LIKE 'Excluded before admission%' THEN coalesce(admitted_at,now()) ELSE admitted_at END
      WHERE id=item; END IF;
   ELSE
    IF p_action='delete' THEN
     INSERT INTO driftread.article_cleanup_tombstones(feed_id,url) SELECT feed_id,url FROM driftread.articles WHERE id=item ON CONFLICT DO NOTHING;
     DELETE FROM driftread.articles WHERE id=item;
     PERFORM driftread.reconcile_feed_article_count(source);
    ELSE UPDATE driftread.articles SET staged_at=CASE WHEN p_action='stage' THEN coalesce(staged_at,now()) END WHERE id=item; END IF;
   END IF;
  END IF;
  results:=results||jsonb_build_array(jsonb_build_object('id',item,'ok',true,'impact',impact));
 END LOOP;
 RETURN jsonb_build_object('items',results,'dry_run',p_dry_run,'action',p_action);
END $$;
REVOKE ALL ON FUNCTION driftread.apply_cleanup_batch(text,uuid[],text,boolean,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.apply_cleanup_batch(text,uuid[],text,boolean,boolean) TO service_role;

ALTER TABLE driftread.discovery_targets DROP CONSTRAINT IF EXISTS discovery_targets_status_check;
ALTER TABLE driftread.discovery_targets ADD CONSTRAINT discovery_targets_status_check CHECK(status IN ('pending','done','blocked','exhausted','rejected','removed'));

CREATE OR REPLACE FUNCTION driftread.remove_discovery_targets(p_ids uuid[])
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE item uuid; current_status text; results jsonb:='[]';
BEGIN
 IF coalesce(cardinality(p_ids),0) NOT BETWEEN 1 AND 50 OR array_position(p_ids,NULL) IS NOT NULL THEN RAISE EXCEPTION 'invalid target batch'; END IF;
 PERFORM 1 FROM driftread.discovery_targets WHERE id=ANY(p_ids) ORDER BY id FOR UPDATE;
 FOR item IN SELECT DISTINCT unnest(p_ids) ORDER BY 1 LOOP
  SELECT status INTO current_status FROM driftread.discovery_targets WHERE id=item;
  IF current_status IN ('pending','removed') THEN
   UPDATE driftread.discovery_targets SET status='removed' WHERE id=item AND status='pending';
   results:=results||jsonb_build_array(jsonb_build_object('id',item,'ok',true));
  ELSE results:=results||jsonb_build_array(jsonb_build_object('id',item,'ok',false,'error',CASE WHEN current_status IS NULL THEN 'not_found' ELSE 'target_completed' END)); END IF;
 END LOOP;
 RETURN jsonb_build_object('items',results);
END $$;
REVOKE ALL ON FUNCTION driftread.remove_discovery_targets(uuid[]) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.remove_discovery_targets(uuid[]) TO service_role;

CREATE OR REPLACE FUNCTION driftread.guard_removed_target()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_NAME='discovery_targets' THEN
  -- An explicit administrator host block may strengthen cancellation. Workers
  -- only write pending/done/blocked/exhausted and cannot undo this marker.
  IF OLD.status='removed' AND NEW.status<>'rejected' THEN RETURN NULL; END IF;
 ELSE
  IF NEW.target_id IS NOT NULL THEN
   PERFORM 1 FROM driftread.discovery_targets WHERE id=NEW.target_id AND status NOT IN ('removed','rejected') FOR SHARE;
   IF NOT FOUND THEN RETURN NULL; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION driftread.guard_removed_target() FROM PUBLIC,anon,authenticated;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='targets_removed_guard' AND tgrelid='driftread.discovery_targets'::regclass) THEN
  CREATE TRIGGER targets_removed_guard BEFORE UPDATE ON driftread.discovery_targets FOR EACH ROW EXECUTE FUNCTION driftread.guard_removed_target();
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='candidates_removed_guard' AND tgrelid='driftread.discovery_candidates'::regclass) THEN
  CREATE TRIGGER candidates_removed_guard BEFORE INSERT ON driftread.discovery_candidates FOR EACH ROW EXECUTE FUNCTION driftread.guard_removed_target();
 END IF;
END $$;

-- All reading surfaces share this projection; staging preserves IDs and user data.
CREATE OR REPLACE VIEW driftread.article_publications WITH (security_invoker=true) AS
SELECT a.id, a.feed_id, a.title, a.url, display.summary,
 CASE WHEN f.fulltext_policy='rss' THEN a.content ELSE NULL::text END AS content,
 a.author, a.published_at, a.fetched_at, a.content_compacted_at,
 a.timeline_at, a.discovered_at, a.backfill, a.backfill_reason, a.current_revision_id,
 f.title AS feed_title, f.language AS feed_language, f.archived_at AS feed_archived_at,
 (f.fulltext_policy='rss') AS fulltext_allowed,
 CASE WHEN f.fulltext_policy='rss' THEN a.search_vector
 ELSE to_tsvector('simple', driftread.bounded_search_text(
 coalesce(a.title,'') || ' ' || coalesce(display.summary,'') || ' ' || coalesce(a.author,''))) END AS search_vector
FROM driftread.articles a JOIN driftread.feeds f ON f.id=a.feed_id
CROSS JOIN LATERAL (SELECT CASE WHEN f.fulltext_policy='rss' THEN a.summary
 ELSE left(driftread.strip_html_for_search(driftread.bounded_search_text(a.summary)),500) END AS summary) display
WHERE f.participation_mode='normal' AND a.staged_at IS NULL;
REVOKE ALL ON driftread.article_publications FROM PUBLIC,anon,authenticated;
GRANT SELECT ON driftread.article_publications TO service_role;

NOTIFY pgrst, 'reload schema';
