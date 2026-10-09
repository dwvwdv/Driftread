-- Human-authored evidence only. No inferred relation, embedding or AI grouping.
CREATE TABLE IF NOT EXISTS driftread.event_objects (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 kind text NOT NULL CHECK(kind IN ('fact','story')),
 title text NOT NULL CHECK(length(btrim(title)) BETWEEN 1 AND 200),
 version bigint NOT NULL DEFAULT 1,
 merged_into uuid REFERENCES driftread.event_objects(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(merged_into IS NULL OR merged_into <> id)
);
CREATE TABLE IF NOT EXISTS driftread.fact_articles (
 fact_id uuid NOT NULL REFERENCES driftread.event_objects(id) ON DELETE CASCADE,
 article_id uuid NOT NULL REFERENCES driftread.articles(id) ON DELETE CASCADE,
 excluded boolean NOT NULL DEFAULT false,
 PRIMARY KEY(fact_id, article_id)
);
CREATE TABLE IF NOT EXISTS driftread.story_facts (
 story_id uuid NOT NULL REFERENCES driftread.event_objects(id) ON DELETE CASCADE,
 fact_id uuid NOT NULL REFERENCES driftread.event_objects(id) ON DELETE CASCADE,
 excluded boolean NOT NULL DEFAULT false,
 PRIMARY KEY(story_id, fact_id), CHECK(story_id<>fact_id)
);
CREATE TABLE IF NOT EXISTS driftread.event_relations (
 left_id uuid NOT NULL REFERENCES driftread.event_objects(id) ON DELETE CASCADE,
 right_id uuid NOT NULL REFERENCES driftread.event_objects(id) ON DELETE CASCADE,
 relation text NOT NULL CHECK(relation IN ('SAME_OCCURRENCE','ROUNDUP','SAME_EVENT','SAME_STORY','FOLLOW_UP','REACTION','CONTEXT','SAME_TOPIC','UNRELATED')),
 PRIMARY KEY(left_id,right_id), CHECK(left_id<right_id)
);
CREATE INDEX IF NOT EXISTS fact_articles_article_idx ON driftread.fact_articles(article_id);
CREATE INDEX IF NOT EXISTS story_facts_fact_idx ON driftread.story_facts(fact_id);
ALTER TABLE driftread.event_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.fact_articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.story_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.event_relations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.event_objects,driftread.fact_articles,driftread.story_facts,driftread.event_relations FROM anon,authenticated;
GRANT ALL ON driftread.event_objects,driftread.fact_articles,driftread.story_facts,driftread.event_relations TO service_role;

CREATE OR REPLACE FUNCTION driftread.mutate_manual_event(
 p_id uuid, p_expected_version bigint, p_title text, p_members jsonb DEFAULT NULL,
 p_relation_target uuid DEFAULT NULL, p_relation text DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE e driftread.event_objects; m jsonb; target_kind text; relation_target_id uuid;
BEGIN
 IF p_relation_target IS NOT NULL THEN
   -- Relation writers and merges share this lock before any event row lock.
   PERFORM pg_advisory_xact_lock(hashtextextended('driftread.manual_event_relations',0));
 END IF;
 SELECT * INTO e FROM driftread.event_objects WHERE id=p_id FOR UPDATE;
 IF NOT FOUND OR e.merged_into IS NOT NULL THEN RAISE EXCEPTION 'event not editable' USING ERRCODE='P0002'; END IF;
 IF e.version<>p_expected_version THEN RAISE EXCEPTION 'version conflict' USING ERRCODE='40001'; END IF;
 IF p_members IS NOT NULL THEN
   IF jsonb_typeof(p_members)<>'array' OR jsonb_array_length(p_members)>200 THEN
     RAISE EXCEPTION 'invalid members' USING ERRCODE='22023';
   END IF;
   -- PATCH upserts explicit include/exclude decisions. Omitting a member
   -- never deletes a permanent exclusion, and there is no automated writer.
   FOR m IN SELECT value FROM jsonb_array_elements(p_members) LOOP
     IF e.kind='fact' THEN
       INSERT INTO driftread.fact_articles(fact_id,article_id,excluded)
       VALUES(e.id,(m->>'id')::uuid,coalesce((m->>'excluded')::boolean,false))
       ON CONFLICT(fact_id,article_id) DO UPDATE SET excluded=excluded.excluded;
     ELSE
       SELECT kind INTO target_kind FROM driftread.event_objects WHERE id=(m->>'id')::uuid AND merged_into IS NULL;
       IF target_kind IS DISTINCT FROM 'fact' THEN RAISE EXCEPTION 'story members must be facts' USING ERRCODE='22023'; END IF;
       INSERT INTO driftread.story_facts(story_id,fact_id,excluded)
       VALUES(e.id,(m->>'id')::uuid,coalesce((m->>'excluded')::boolean,false))
       ON CONFLICT(story_id,fact_id) DO UPDATE SET excluded=excluded.excluded;
     END IF;
   END LOOP;
 END IF;
 IF p_relation_target IS NOT NULL THEN
   SELECT coalesce(merged_into,id) INTO relation_target_id FROM driftread.event_objects WHERE id=p_relation_target;
   IF relation_target_id IS NULL THEN RAISE EXCEPTION 'invalid relation target' USING ERRCODE='22023'; END IF;
   IF relation_target_id=p_id THEN RAISE EXCEPTION 'self relation' USING ERRCODE='22023'; END IF;
   INSERT INTO driftread.event_relations(left_id,right_id,relation)
   VALUES(least(p_id,relation_target_id),greatest(p_id,relation_target_id),p_relation)
   ON CONFLICT(left_id,right_id) DO UPDATE SET relation=excluded.relation;
 END IF;
 UPDATE driftread.event_objects SET title=coalesce(p_title,title),version=version+1,updated_at=now()
 WHERE id=e.id RETURNING * INTO e;
 RETURN to_jsonb(e);
END $$;

CREATE OR REPLACE FUNCTION driftread.merge_manual_stories(
 p_source uuid,p_target uuid,p_source_version bigint,p_target_version bigint
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE s driftread.event_objects; t driftread.event_objects; merged_ids uuid[]; relations jsonb;
BEGIN
 IF p_source=p_target THEN RAISE EXCEPTION 'self merge' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('driftread.manual_event_relations',0));
 -- Fixed lock order prevents A→B vs B→A deadlocks. Merged rows cannot be
 -- merged again; flatten older aliases so resolution is always one hop.
 PERFORM id FROM driftread.event_objects WHERE id IN(p_source,p_target) ORDER BY id FOR UPDATE;
 SELECT * INTO s FROM driftread.event_objects WHERE id=p_source;
 SELECT * INTO t FROM driftread.event_objects WHERE id=p_target;
 IF s.id IS NULL OR t.id IS NULL OR s.kind<>'story' OR t.kind<>'story' OR s.merged_into IS NOT NULL OR t.merged_into IS NOT NULL THEN
   RAISE EXCEPTION 'invalid merge' USING ERRCODE='22023';
 END IF;
 IF s.version<>p_source_version OR t.version<>p_target_version THEN RAISE EXCEPTION 'version conflict' USING ERRCODE='40001'; END IF;
 -- Include both sets of older aliases; all their edges now name the target.
 SELECT array_agg(id) INTO merged_ids FROM driftread.event_objects
 WHERE id IN(p_source,p_target) OR merged_into IN(p_source,p_target);
 WITH removed AS (
   DELETE FROM driftread.event_relations WHERE left_id=ANY(merged_ids) OR right_id=ANY(merged_ids)
   RETURNING left_id,right_id,relation
 ), mapped AS (
   SELECT CASE WHEN left_id=ANY(merged_ids) THEN p_target ELSE left_id END l,
          CASE WHEN right_id=ANY(merged_ids) THEN p_target ELSE right_id END r,relation
   FROM removed
 )
 SELECT coalesce(jsonb_agg(jsonb_build_object('left_id',least(l,r),'right_id',greatest(l,r),'relation',relation)),'[]'::jsonb)
 INTO relations FROM mapped WHERE l<>r;
 -- A pair has one authored relation: identical duplicates collapse, while
 -- conflicting declarations require an explicit decision, never silent loss.
 IF EXISTS(SELECT 1 FROM jsonb_to_recordset(relations) AS x(left_id uuid,right_id uuid,relation text)
           GROUP BY left_id,right_id HAVING count(DISTINCT relation)>1) THEN
   RAISE EXCEPTION 'conflicting relations; resolve before merging' USING ERRCODE='22023';
 END IF;
 INSERT INTO driftread.event_relations(left_id,right_id,relation)
 SELECT DISTINCT left_id,right_id,relation
 FROM jsonb_to_recordset(relations) AS x(left_id uuid,right_id uuid,relation text);
 INSERT INTO driftread.story_facts(story_id,fact_id,excluded)
 SELECT p_target,fact_id,excluded FROM driftread.story_facts WHERE story_id=p_source
 ON CONFLICT(story_id,fact_id) DO UPDATE SET excluded=driftread.story_facts.excluded OR excluded.excluded;
 UPDATE driftread.event_objects SET merged_into=p_target,version=version+1,updated_at=now() WHERE id=p_source OR merged_into=p_source;
 UPDATE driftread.event_objects SET version=version+1,updated_at=now() WHERE id=p_target RETURNING * INTO t;
 RETURN to_jsonb(t);
END $$;

CREATE OR REPLACE FUNCTION driftread.read_manual_event(p_id uuid,p_admin boolean DEFAULT false)
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 WITH resolved AS (
  SELECT coalesce(merged_into,id) id FROM driftread.event_objects WHERE id=p_id
 ), e AS (
  SELECT o.* FROM driftread.event_objects o JOIN resolved r ON o.id=r.id
 ), memberships AS (
  SELECT fa.article_id,fa.excluded,fa.fact_id FROM e JOIN driftread.fact_articles fa ON e.id=fa.fact_id WHERE e.kind='fact'
  UNION ALL
  SELECT fa.article_id,sf.excluded OR fa.excluded,fa.fact_id FROM e JOIN driftread.story_facts sf ON e.id=sf.story_id
  JOIN driftread.fact_articles fa ON fa.fact_id=sf.fact_id WHERE e.kind='story'
 ), visible AS (
  SELECT DISTINCT m.article_id,m.excluded,m.fact_id,a.title,a.url,a.feed_id
  FROM memberships m JOIN driftread.articles a ON a.id=m.article_id
  WHERE p_admin
  UNION ALL
  -- Known archived normal articles remain readable, just as in the reader.
  -- Public membership cannot widen the shared publication's source rights.
  SELECT DISTINCT m.article_id,m.excluded,m.fact_id,a.title,a.url,a.feed_id
  FROM memberships m JOIN driftread.article_publications a ON a.id=m.article_id
  WHERE NOT p_admin AND NOT m.excluded
 )
 SELECT to_jsonb(e) || jsonb_build_object('requested_id',p_id,'articles',coalesce((SELECT jsonb_agg(to_jsonb(v)) FROM visible v),'[]'::jsonb),
 'relations',coalesce((SELECT jsonb_agg(jsonb_build_object('left_id',r.left_id,'right_id',r.right_id,'relation',r.relation))
 FROM driftread.event_relations r WHERE p_admin AND (r.left_id=e.id OR r.right_id=e.id)),'[]'::jsonb),
 'members', CASE WHEN p_admin THEN CASE WHEN e.kind='fact' THEN
 (SELECT coalesce(jsonb_agg(jsonb_build_object('id',article_id,'excluded',excluded)),'[]'::jsonb) FROM driftread.fact_articles WHERE fact_id=e.id)
 ELSE (SELECT coalesce(jsonb_agg(jsonb_build_object('id',fact_id,'excluded',excluded)),'[]'::jsonb) FROM driftread.story_facts WHERE story_id=e.id) END ELSE NULL END)
 FROM e WHERE p_admin OR EXISTS(SELECT 1 FROM visible)
$$;
REVOKE ALL ON FUNCTION driftread.mutate_manual_event(uuid,bigint,text,jsonb,uuid,text),driftread.merge_manual_stories(uuid,uuid,bigint,bigint),driftread.read_manual_event(uuid,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.mutate_manual_event(uuid,bigint,text,jsonb,uuid,text),driftread.merge_manual_stories(uuid,uuid,bigint,bigint),driftread.read_manual_event(uuid,boolean) TO service_role;
