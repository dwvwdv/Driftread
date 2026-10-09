-- Durable invalidations and bounded authoritative offline cache. A single row
-- serializes sequence assignment until COMMIT: unlike bigserial, a cursor can
-- never skip an earlier sequence belonging to an uncommitted transaction.
CREATE TABLE IF NOT EXISTS driftread.sync_clock (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    sequence bigint NOT NULL DEFAULT 0
);
INSERT INTO driftread.sync_clock(singleton) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS driftread.sync_changes (
    sequence bigint PRIMARY KEY,
    user_id uuid,
    entity text NOT NULL,
    operation text NOT NULL CHECK(operation IN ('INSERT','UPDATE','DELETE')),
    changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sync_changes_user_idx ON driftread.sync_changes(user_id,sequence);
ALTER TABLE driftread.sync_clock ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.sync_changes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.sync_clock, driftread.sync_changes FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON driftread.sync_clock,driftread.sync_changes TO service_role;

CREATE OR REPLACE FUNCTION driftread.record_sync_change() RETURNS trigger
-- Trigger executes only from mutations already authorized by the source
-- table's RLS. It has no callable API or payload and must write a private ledger.
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE seq bigint; owner uuid;
BEGIN
    IF TG_OP='UPDATE' AND OLD IS NOT DISTINCT FROM NEW THEN RETURN NEW; END IF;
    UPDATE driftread.sync_clock SET sequence=sequence+1 WHERE singleton RETURNING sequence INTO seq;
    IF TG_TABLE_NAME LIKE 'user_%' THEN
        IF TG_OP='DELETE' THEN owner:=(to_jsonb(OLD)->>'user_id')::uuid;
        ELSE owner:=(to_jsonb(NEW)->>'user_id')::uuid; END IF;
    END IF;
    INSERT INTO driftread.sync_changes(sequence,user_id,entity,operation)
    VALUES(seq,owner,TG_TABLE_NAME,TG_OP);
    -- Bound the invalidation ledger; no article bodies or user payloads persist.
    IF seq % 1000 = 0 THEN
        DELETE FROM driftread.sync_changes WHERE sequence <= seq-50000;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION driftread.record_sync_change() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.record_sync_change() TO service_role;
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['articles','feeds','user_feeds',
                                'user_article_reads','user_bookmarks'] LOOP
        IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='sync_'||name AND
                      tgrelid=('driftread.'||name)::regclass) THEN
            EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON driftread.%I '
                           'FOR EACH ROW EXECUTE FUNCTION driftread.record_sync_change()', 'sync_'||name,name);
        END IF;
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION driftread.personal_sync_snapshot(p_user_id uuid,p_since bigint DEFAULT NULL,p_pending_ids uuid[] DEFAULT ARRAY[]::uuid[])
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE highwater bigint; reset boolean; changed boolean; items jsonb; authorized jsonb;
BEGIN
    -- Lock before observing the publication projection. Writers of subscription,
    -- rights, overrides and article state hold the same lock until commit.
    SELECT sequence INTO highwater FROM driftread.sync_clock WHERE singleton FOR UPDATE;
    reset := p_since IS NULL OR p_since < greatest(0,highwater-50000) OR p_since > highwater;
    changed := reset OR EXISTS(SELECT 1 FROM driftread.sync_changes c
        WHERE c.sequence>p_since AND (c.user_id IS NULL OR c.user_id=p_user_id));
    IF changed THEN
        SELECT coalesce(jsonb_agg(to_jsonb(a) - 'search_vector' - 'content' || jsonb_build_object(
            'is_read',EXISTS(SELECT 1 FROM driftread.user_article_reads r WHERE r.user_id=p_user_id AND r.article_id=a.id),
            'bookmark_types',coalesce((SELECT jsonb_agg(b.bookmark_type ORDER BY b.bookmark_type)
               FROM driftread.user_bookmarks b WHERE b.user_id=p_user_id AND b.article_id=a.id),'[]'::jsonb)
        ) ORDER BY a.timeline_at DESC,a.id DESC),'[]'::jsonb) INTO items
        FROM driftread.list_personal_publications(p_user_id,NULL,NULL,false,100) a;
    END IF;
    SELECT coalesce(jsonb_agg(a.id),'[]'::jsonb) INTO authorized
    FROM driftread.article_publications a JOIN driftread.user_feeds s
      ON s.feed_id=a.feed_id AND s.user_id=p_user_id AND s.muted_at IS NULL
    WHERE a.id=ANY(p_pending_ids);
    RETURN jsonb_build_object('authorized_article_ids',authorized,'sequence',highwater,'reset',reset,'changed',changed,'items',items,
                             'cache_limit',100);
END $$;
REVOKE ALL ON FUNCTION driftread.personal_sync_snapshot(uuid,bigint,uuid[]) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.personal_sync_snapshot(uuid,bigint,uuid[]) TO service_role;

-- Match/rank with the rights-aware vector, but return metadata only. Recreate
-- this unmerged RPC so replay also corrects its former publication-row return.
DROP FUNCTION IF EXISTS driftread.personal_publication_search(uuid,text,int);
CREATE FUNCTION driftread.personal_publication_search(p_user_id uuid,p_query text,p_limit int DEFAULT 20)
RETURNS TABLE(id uuid,feed_id uuid,title text,url text,summary text,author text,
 published_at timestamptz,fetched_at timestamptz,content_compacted_at timestamptz,
 timeline_at timestamptz,discovered_at timestamptz,backfill boolean,backfill_reason text,
 current_revision_id uuid,feed_title text,feed_language text,feed_archived_at timestamptz,
 fulltext_allowed boolean)
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path=pg_catalog AS $$
 SELECT a.id,a.feed_id,a.title,a.url,a.summary,a.author,a.published_at,a.fetched_at,
 a.content_compacted_at,a.timeline_at,a.discovered_at,a.backfill,a.backfill_reason,a.current_revision_id,
 coalesce(s.custom_title,a.feed_title),a.feed_language,a.feed_archived_at,a.fulltext_allowed
 FROM driftread.article_publications a
 JOIN driftread.user_feeds s ON s.feed_id=a.feed_id AND s.user_id=p_user_id AND s.muted_at IS NULL
 WHERE a.search_vector @@ websearch_to_tsquery('simple',p_query)
 ORDER BY ts_rank_cd(a.search_vector,websearch_to_tsquery('simple',p_query)) DESC,a.timeline_at DESC,a.id DESC
 LIMIT least(greatest(p_limit,1),100)
$$;
REVOKE ALL ON FUNCTION driftread.personal_publication_search(uuid,text,int) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.personal_publication_search(uuid,text,int) TO service_role;

-- Offline intent must still be authorized when it is written, not just during
-- an earlier sync preflight. SHARE locks prevent role/mute/unsubscribe changes
-- from committing between authorization and the idempotent state mutation.
DROP FUNCTION IF EXISTS driftread.replay_personal_article_state(uuid,uuid,text,boolean);
CREATE FUNCTION driftread.replay_personal_article_state(
 p_user_id uuid,p_article_id uuid,p_kind text,p_enabled boolean
) RETURNS text LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE source_feed uuid;
BEGIN
 IF p_kind NOT IN ('read','favorite','read_later') OR p_kind IS NULL OR p_enabled IS NULL THEN
  RAISE EXCEPTION 'Invalid offline operation' USING ERRCODE='22023';
 END IF;
 SELECT feed_id INTO source_feed FROM driftread.articles WHERE id=p_article_id;
 IF NOT FOUND THEN RETURN 'unavailable'; END IF;
 PERFORM 1 FROM driftread.feeds WHERE id=source_feed AND participation_mode='normal' FOR SHARE;
 IF NOT FOUND THEN RETURN 'unavailable'; END IF;
 PERFORM 1 FROM driftread.user_feeds
  WHERE user_id=p_user_id AND feed_id=source_feed AND muted_at IS NULL FOR SHARE;
 IF NOT FOUND THEN RETURN 'unavailable'; END IF;
 PERFORM 1 FROM driftread.articles a JOIN driftread.article_publications p ON p.id=a.id
  WHERE a.id=p_article_id AND a.feed_id=source_feed FOR SHARE OF a;
 IF NOT FOUND THEN RETURN 'unavailable'; END IF;
 IF p_kind='read' THEN
  IF p_enabled THEN
   INSERT INTO driftread.user_article_reads(user_id,article_id) VALUES(p_user_id,p_article_id)
    ON CONFLICT(user_id,article_id) DO NOTHING;
  ELSE
   DELETE FROM driftread.user_article_reads WHERE user_id=p_user_id AND article_id=p_article_id;
  END IF;
 ELSIF p_enabled THEN
  INSERT INTO driftread.user_bookmarks(user_id,article_id,bookmark_type) VALUES(p_user_id,p_article_id,p_kind)
   ON CONFLICT(user_id,article_id,bookmark_type) DO NOTHING;
 ELSE
  DELETE FROM driftread.user_bookmarks
   WHERE user_id=p_user_id AND article_id=p_article_id AND bookmark_type=p_kind;
 END IF;
 RETURN 'applied';
EXCEPTION WHEN lock_not_available OR deadlock_detected THEN
 -- The exception subtransaction rolls back state AND its sync invalidation,
 -- releasing authorization locks. A busy batch must not lose offline intent.
 RETURN 'retry';
END $$;
REVOKE ALL ON FUNCTION driftread.replay_personal_article_state(uuid,uuid,text,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.replay_personal_article_state(uuid,uuid,text,boolean) TO service_role;
