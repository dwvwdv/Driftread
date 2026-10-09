-- Real RPC semantics, including expressions unsafe for a raw-vector prefilter.
BEGIN;
DO $$
DECLARE
 owner uuid:=gen_random_uuid(); other uuid:=gen_random_uuid();
 source uuid:=gen_random_uuid(); hidden uuid:=gen_random_uuid();
 article uuid:=gen_random_uuid(); stronger uuid:=gen_random_uuid();
 phrase uuid:=gen_random_uuid(); clipped uuid:=gen_random_uuid();
 query text; actual uuid[]; expected uuid[];
BEGIN
 INSERT INTO auth.users(id) VALUES(owner),(other);
 INSERT INTO driftread.feeds(id,title,url,fulltext_policy) VALUES
  (source,'Search source','https://search-candidates.invalid/rss','summary_only');
 INSERT INTO driftread.feeds(id,title,url,participation_mode) VALUES
  (hidden,'Hidden source','https://search-candidates.invalid/hidden','private');
 INSERT INTO driftread.user_feeds(user_id,feed_id,custom_title) VALUES
  (owner,source,'Owner title'),(owner,hidden,'Hidden title');
 INSERT INTO driftread.articles(id,feed_id,title,url,summary,author,content,timeline_at) VALUES
  (article,source,'Heading','https://search-candidates.invalid/one','visible','authorword',
   'secret '||repeat('visible ',100),now()),
  (stronger,source,'Heading','https://search-candidates.invalid/two','visible visible another',
   'authorword','bodyonly',now()-interval '1 day'),
  (phrase,source,'Heading','https://search-candidates.invalid/phrase',
   repeat(' ',493)||'visible trailing','authorword','secret',now()-interval '2 days'),
  (clipped,source,'Heading','https://search-candidates.invalid/clipped',
   repeat(' ',493)||'visibletrailing','authorword','secret',now()-interval '3 days'),
  (gen_random_uuid(),hidden,'visible','https://search-candidates.invalid/hidden-post',
   'visible','authorword','secret',now());

 -- Each query must preserve the original policy-aware result AND rank order.
 FOREACH query IN ARRAY ARRAY['visible','visible OR another','visible -secret',
                             '-secret','"visible authorword"','bodyonly','secret'] LOOP
  SELECT array_agg(a.id ORDER BY ts_rank_cd(a.search_vector,websearch_to_tsquery('simple',query)) DESC,
                                a.timeline_at DESC,a.id DESC) INTO expected
   FROM driftread.article_publications a JOIN driftread.user_feeds s ON s.feed_id=a.feed_id
   WHERE s.user_id=owner AND s.muted_at IS NULL
    AND a.search_vector @@ websearch_to_tsquery('simple',query);
  SELECT array_agg(r.id) INTO actual FROM driftread.personal_publication_search(owner,query,100) r;
  IF actual IS DISTINCT FROM expected THEN
   RAISE EXCEPTION 'candidate search changed policy matching/rank for %: % != %',query,actual,expected;
  END IF;
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM driftread.personal_publication_search(owner,'visible') WHERE id=clipped)
  OR (SELECT search_vector @@ websearch_to_tsquery('simple','visible')
      FROM driftread.articles WHERE id=clipped) THEN
  RAISE EXCEPTION 'fixture did not preserve the summary-clipped lexeme';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.personal_publication_search(owner,'"visible authorword"') WHERE id=phrase)
  OR (SELECT search_vector @@ websearch_to_tsquery('simple','"visible authorword"')
      FROM driftread.articles WHERE id=phrase) THEN
  RAISE EXCEPTION 'fixture did not cover phrase-position fallback';
 END IF;
 IF (SELECT id FROM driftread.personal_publication_search(owner,'visible',1))<>stronger THEN
  RAISE EXCEPTION 'forbidden body changed personal search ranking';
 END IF;
 IF EXISTS(SELECT 1 FROM driftread.personal_publication_search(other,'visible')) THEN
  RAISE EXCEPTION 'search crossed subscription owner';
 END IF;
 IF EXISTS(SELECT 1 FROM driftread.personal_publication_search(owner,'visible') r
           WHERE to_jsonb(r) ? 'content' OR to_jsonb(r) ? 'search_vector' OR r.feed_title<>'Owner title') THEN
  RAISE EXCEPTION 'search metadata contract changed';
 END IF;
 UPDATE driftread.user_feeds SET muted_at=now() WHERE user_id=owner AND feed_id=source;
 IF EXISTS(SELECT 1 FROM driftread.personal_publication_search(owner,'visible'))
  OR EXISTS(SELECT 1 FROM driftread.personal_publication_search(owner,'-secret')) THEN
  RAISE EXCEPTION 'candidate or fallback branch bypassed mute';
 END IF;
END $$;
ROLLBACK;
