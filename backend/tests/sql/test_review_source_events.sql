-- Successful imports may only report completion, without an earlier attempt.
DO $$
DECLARE
 source_id uuid;
BEGIN
 SELECT id INTO source_id FROM driftread.import_readable_source(
   '{"title":"Successful import","url":"https://review-success-only.example/rss"}');
 PERFORM driftread.record_source_fetch(source_id,'2026-10-09T01:00:00Z',true);
 PERFORM driftread.import_readable_source(
   '{"title":"Do not overwrite","url":"https://review-success-only.example/rss"}');
 PERFORM driftread.record_source_fetch(source_id,'2026-10-09T02:00:00Z',true);
 IF (SELECT last_fetch_at FROM driftread.feeds WHERE id=source_id) <> '2026-10-09T02:00:00Z'::timestamptz THEN
   RAISE EXCEPTION 'Repeated successful import did not advance last fetch';
 END IF;
 PERFORM driftread.record_source_fetch(source_id,'2026-10-09T02:00:00Z',true);
 PERFORM driftread.record_source_fetch(source_id,'2026-10-08T03:00:00Z',true);
 IF (SELECT last_fetch_at FROM driftread.feeds WHERE id=source_id) <> '2026-10-09T02:00:00Z'::timestamptz
    OR (SELECT last_ok_at FROM driftread.feeds WHERE id=source_id) <> '2026-10-09T02:00:00Z'::timestamptz THEN
   RAISE EXCEPTION 'Repeated or late older success changed the latest evidence';
 END IF;
 PERFORM driftread.record_source_fetch(source_id,'2026-10-09T03:00:00Z',false);
 PERFORM driftread.record_source_fetch(source_id,'2026-10-09T02:30:00Z',true);
 IF (SELECT last_fetch_at FROM driftread.feeds WHERE id=source_id) <> '2026-10-09T03:00:00Z'::timestamptz
    OR (SELECT last_ok_at FROM driftread.feeds WHERE id=source_id) <> '2026-10-09T02:30:00Z'::timestamptz THEN
   RAISE EXCEPTION 'Completion after a newer attempt broke monotonic timestamps';
 END IF;
END $$;

-- Archiving stops discovery; it does not withdraw an existing publication.
DO $$
DECLARE
 normal_id uuid := gen_random_uuid(); private_id uuid := gen_random_uuid(); signal_id uuid := gen_random_uuid();
 readable_id uuid := gen_random_uuid(); private_article uuid := gen_random_uuid(); signal_article uuid := gen_random_uuid(); excluded_id uuid := gen_random_uuid();
 fact_id uuid := gen_random_uuid(); story_id uuid := gen_random_uuid(); payload jsonb;
BEGIN
 INSERT INTO driftread.feeds(id,title,url,participation_mode,archived_at) VALUES
 (normal_id,'Archived normal','https://review-events.example/normal','normal',now()),
 (private_id,'Private secret','https://review-events.example/private','private',now()),
 (signal_id,'Signal secret','https://review-events.example/signal','signal_only',now());
 INSERT INTO driftread.articles(id,feed_id,title,url) VALUES
 (readable_id,normal_id,'Still readable','https://review-events.example/readable'),
 (excluded_id,normal_id,'Explicitly excluded','https://review-events.example/excluded'),
 (private_article,private_id,'Private article secret','https://review-events.example/private-article'),
 (signal_article,signal_id,'Signal article secret','https://review-events.example/signal-article');
 INSERT INTO driftread.event_objects(id,kind,title) VALUES
 (fact_id,'fact','Existing fact'),(story_id,'story','Existing story');
 INSERT INTO driftread.fact_articles(fact_id,article_id,excluded) VALUES
 (fact_id,readable_id,false),(fact_id,excluded_id,true),
 (fact_id,private_article,false),(fact_id,signal_article,false);
 INSERT INTO driftread.story_facts(story_id,fact_id) VALUES(story_id,fact_id);

 IF NOT EXISTS(SELECT 1 FROM driftread.article_publications WHERE id=readable_id) THEN
   RAISE EXCEPTION 'Fixture article must remain readable through publication';
 END IF;
 payload := driftread.read_manual_event(fact_id,false);
 IF payload IS NULL OR jsonb_array_length(payload->'articles')<>1
    OR payload->'articles'->0->>'article_id'<>readable_id::text THEN
   RAISE EXCEPTION 'Fact lost archived normal publication or disclosed hidden member';
 END IF;
 payload := driftread.read_manual_event(story_id,false);
 IF payload IS NULL OR jsonb_array_length(payload->'articles')<>1
    OR payload->'articles'->0->>'article_id'<>readable_id::text THEN
   RAISE EXCEPTION 'Story must follow the same publication and exclusion rules';
 END IF;
 IF jsonb_array_length(driftread.read_manual_event(fact_id,true)->'articles')<>4 THEN
   RAISE EXCEPTION 'Administrative evidence must retain hidden and excluded members';
 END IF;
 UPDATE driftread.feeds SET participation_mode='private' WHERE id=normal_id;
 IF driftread.read_manual_event(fact_id,false) IS NOT NULL
    OR driftread.read_manual_event(story_id,false) IS NOT NULL THEN
   RAISE EXCEPTION 'Current source rights must immediately withdraw public events';
 END IF;
END $$;
