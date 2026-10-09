-- Isolated database only: verify the actual RPC payload, not an API allowlist.
BEGIN;
DO $$
DECLARE
 alice uuid:=gen_random_uuid(); bob uuid:=gen_random_uuid();
 feed uuid:=gen_random_uuid(); article uuid:=gen_random_uuid();
 body text:='bodyneedle ' || repeat('article text ',150000);
 payload jsonb; result jsonb;
BEGIN
 INSERT INTO auth.users(id) VALUES(alice),(bob);
 INSERT INTO driftread.feeds(id,title,url) VALUES(feed,'Source title','https://excerpt.invalid/rss');
 INSERT INTO driftread.articles(id,feed_id,title,url,summary,content,published_at)
 VALUES(article,feed,'Excerpt title','https://excerpt.invalid/article','Visible summary',body,now());
 INSERT INTO driftread.user_feeds(user_id,feed_id,custom_title) VALUES(alice,feed,'Personal title');

 SELECT to_jsonb(a) INTO payload FROM driftread.list_personal_publications(alice) a WHERE a.id=article;
 IF payload IS NULL OR payload ? 'content' OR payload ? 'search_vector' OR length(payload::text)>2000 THEN
  RAISE EXCEPTION 'personal excerpt RPC transferred large body or search vector';
 END IF;
 IF payload->>'summary'<>'Visible summary' OR payload->>'feed_title'<>'Personal title'
  OR payload->>'current_revision_id' IS NULL OR payload->>'fulltext_allowed'<>'true' THEN
  RAISE EXCEPTION 'excerpt projection lost public metadata';
 END IF;
 SELECT to_jsonb(a) INTO payload FROM driftread.personal_publication_search(alice,'bodyneedle') a WHERE a.id=article;
 IF payload IS NULL OR payload ? 'content' OR payload ? 'search_vector' OR length(payload::text)>2000 THEN
  RAISE EXCEPTION 'permitted fulltext search failed or transferred body/vector';
 END IF;
 IF payload->>'feed_title'<>'Personal title' THEN
  RAISE EXCEPTION 'personal search lost owner custom title';
 END IF;
 IF EXISTS(SELECT 1 FROM driftread.list_personal_publications(bob)) OR
  EXISTS(SELECT 1 FROM driftread.personal_publication_search(bob,'bodyneedle')) THEN
  RAISE EXCEPTION 'excerpt/search crossed subscription owners';
 END IF;
 IF (SELECT content FROM driftread.article_publications WHERE id=article) IS DISTINCT FROM body THEN
  RAISE EXCEPTION 'single-article reading lost permitted body';
 END IF;
 result:=driftread.personal_sync_snapshot(alice);
 IF result->'items'->0 ? 'content' OR result->'items'->0 ? 'search_vector' THEN
  RAISE EXCEPTION 'sync replacement snapshot exposed body/vector';
 END IF;

 UPDATE driftread.feeds SET fulltext_policy='summary_only' WHERE id=feed;
 IF EXISTS(SELECT 1 FROM driftread.personal_publication_search(alice,'bodyneedle')) OR
  NOT EXISTS(SELECT 1 FROM driftread.personal_publication_search(alice,'Visible') WHERE id=article) OR
  (SELECT content FROM driftread.article_publications WHERE id=article) IS NOT NULL THEN
  RAISE EXCEPTION 'summary-only search or single-article rights failed';
 END IF;
 UPDATE driftread.user_feeds SET muted_at=now() WHERE user_id=alice AND feed_id=feed;
 IF EXISTS(SELECT 1 FROM driftread.list_personal_publications(alice)) OR
  EXISTS(SELECT 1 FROM driftread.personal_publication_search(alice,'Visible')) THEN
  RAISE EXCEPTION 'muted subscription still exposed excerpts';
 END IF;
 IF has_function_privilege('anon','driftread.list_personal_publications(uuid,timestamptz,timestamptz,boolean,int)','EXECUTE') OR
  has_function_privilege('authenticated','driftread.personal_publication_search(uuid,text,int)','EXECUTE') OR
  NOT has_function_privilege('service_role','driftread.personal_publication_search(uuid,text,int)','EXECUTE') THEN
  RAISE EXCEPTION 'excerpt RPC grants changed after recreation';
 END IF;
END;
$$;
ROLLBACK;
