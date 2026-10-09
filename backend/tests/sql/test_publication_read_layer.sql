-- Isolated database only; all fixture data rolls back.
BEGIN;
DO $$
DECLARE
 normal uuid:=gen_random_uuid(); hidden uuid:=gen_random_uuid(); signal uuid:=gen_random_uuid();
 alice uuid:=gen_random_uuid(); bob uuid:=gen_random_uuid();
 article uuid:=gen_random_uuid(); hidden_article uuid:=gen_random_uuid(); signal_article uuid:=gen_random_uuid();
 old_article uuid:=gen_random_uuid(); fresh_article uuid:=gen_random_uuid(); n bigint;
BEGIN
 INSERT INTO auth.users(id) VALUES(alice),(bob);
 INSERT INTO driftread.feeds(id,title,url,participation_mode) VALUES
 (normal,'normal source','https://normal.invalid/rss','normal'),
 (hidden,'private source','https://hidden.invalid/rss','private'),
 (signal,'signal source','https://signal.invalid/rss','signal_only');
 INSERT INTO driftread.articles(id,feed_id,title,url,summary,content,published_at,fetched_at,backfill) VALUES
 (article,normal,'common title','https://normal.invalid/a','allowedsummary','<p>forbiddenbodyword common</p>','2026-10-08 09:00Z','2026-10-08 10:00Z',false),
 (old_article,normal,'old common','https://normal.invalid/old','oldsummary',NULL,'2026-10-07 09:00Z','2026-10-08 10:00Z',false),
 (fresh_article,normal,'backfill common','https://normal.invalid/new','backfilled',NULL,'2026-10-08 09:30Z','2026-10-08 10:00Z',true),
 (hidden_article,hidden,'private common','https://hidden.invalid/a','secret',NULL,NULL,'2026-10-08 10:00Z',false),
 (signal_article,signal,'signal common','https://signal.invalid/a','signal',NULL,NULL,'2026-10-08 10:00Z',false);
 INSERT INTO driftread.user_feeds(user_id,feed_id,custom_title) VALUES
 (alice,normal,'Alice custom'),(alice,hidden,NULL),(alice,signal,NULL),(bob,normal,'Bob custom');
 INSERT INTO driftread.user_article_reads(user_id,article_id) VALUES(alice,article);
 INSERT INTO driftread.user_bookmarks(user_id,article_id,bookmark_type) VALUES
 (alice,article,'favorite'),(alice,hidden_article,'favorite'),(bob,old_article,'favorite');
 IF (SELECT count(*) FROM driftread.article_publications WHERE feed_id IN(normal,hidden,signal))<>3 THEN
 RAISE EXCEPTION 'non-reading roles exposed in publication'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.article_publications WHERE id=article AND content IS NOT NULL AND fulltext_allowed) THEN
 RAISE EXCEPTION 'rss reader content missing'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.search_publications('forbiddenbodyword') WHERE id=article) THEN
 RAISE EXCEPTION 'rss body not indexed'; END IF;
 UPDATE driftread.articles SET summary='<p>allowedsummary ' || repeat('excerpt ',100) || 'lateforbiddenword</p>' WHERE id=article;
 UPDATE driftread.feeds SET fulltext_policy='summary_only' WHERE id=normal;
 IF EXISTS(SELECT 1 FROM driftread.article_publications WHERE id=article AND (content IS NOT NULL OR fulltext_allowed)) THEN
 RAISE EXCEPTION 'policy update did not immediately suppress body'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.article_publications WHERE id=article AND (length(summary)>500 OR summary LIKE '%lateforbiddenword%' OR summary LIKE '%<p>%')) THEN
 RAISE EXCEPTION 'summary-only policy exposed full RSS description'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.search_publications('lateforbiddenword') WHERE id=article) THEN
 RAISE EXCEPTION 'RSS full-description tail leaked through search'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.search_publications('forbiddenbodyword') WHERE id=article) THEN
 RAISE EXCEPTION 'forbidden content leaked through search match'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.search_publications('allowedsummary') WHERE id=article AND snippet LIKE '%allowedsummary%') THEN
 RAISE EXCEPTION 'summary search unavailable'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.search_publications('common') WHERE id=article AND snippet LIKE '%forbiddenbodyword%') THEN
 RAISE EXCEPTION 'forbidden content leaked through fallback snippet'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.search_publications('common') WHERE feed_id IN(hidden,signal)) THEN
 RAISE EXCEPTION 'non-reading source searchable'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.list_feed_publications(normal,alice) WHERE id=fresh_article AND backfill AND discovered_at IS NOT NULL AND current_revision_id IS NOT NULL) OR
 NOT EXISTS(SELECT 1 FROM driftread.list_reading_publications(alice) WHERE id=fresh_article AND backfill AND current_revision_id IS NOT NULL) OR
 NOT EXISTS(SELECT 1 FROM driftread.search_publications('backfill') WHERE id=fresh_article AND backfill AND current_revision_id IS NOT NULL) THEN
 RAISE EXCEPTION 'publication metadata dropped by article RPC'; END IF;
 IF (SELECT count(*) FROM driftread.list_feed_publications(normal,alice))<>3 OR
 (SELECT count(*) FROM driftread.list_feed_publications(hidden,alice))<>0 THEN
 RAISE EXCEPTION 'feed listing source visibility wrong'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.list_feed_publications(normal,alice) WHERE id=article AND is_read AND is_bookmarked) OR
 EXISTS(SELECT 1 FROM driftread.list_feed_publications(normal,bob) WHERE id=article AND (is_read OR is_bookmarked)) THEN
 RAISE EXCEPTION 'read/bookmark state crossed users'; END IF;
 IF (SELECT count(*) FROM driftread.list_reading_publications(alice))<>3 OR
 (SELECT count(*) FROM driftread.list_reading_publications(alice,NULL,true))<>2 THEN
 RAISE EXCEPTION 'stream visibility/unread mismatch'; END IF;
 IF (SELECT sum(unread_count) FROM driftread.reading_stream_unread_counts(alice))<>2 OR
 (SELECT sum(unread_count) FROM driftread.reading_stream_unread_counts(bob))<>3 THEN
 RAISE EXCEPTION 'unread counts leak or count hidden roles'; END IF;
 IF (SELECT count(*) FROM driftread.list_bookmark_publications(alice,'favorite'))<>1 OR
 NOT EXISTS(SELECT 1 FROM driftread.list_bookmark_publications(bob,'favorite') WHERE id=old_article) THEN
 RAISE EXCEPTION 'bookmark projection isolation failed'; END IF;
 IF (SELECT count(*) FROM driftread.list_personal_publications(alice,'2026-10-08 00:00Z','2026-10-09 00:00Z',true))<>1 OR
 NOT EXISTS(SELECT 1 FROM driftread.list_personal_publications(alice) a WHERE id=article AND feed_title='Alice custom' AND NOT (to_jsonb(a) ? 'content')) THEN
 RAISE EXCEPTION 'personal period/backfill/custom-title/rights contract failed'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.list_personal_publications(bob) WHERE feed_title='Alice custom') THEN
 RAISE EXCEPTION 'personal title crossed user'; END IF;
 IF (SELECT id FROM driftread.list_feed_publications(normal,NULL,NULL,NULL,1))<>fresh_article THEN
 RAISE EXCEPTION 'timeline ordering mismatch'; END IF;
 IF (SELECT id FROM driftread.list_feed_publications(normal,NULL,'2026-10-08 09:30Z',fresh_article,1))<>article THEN
 RAISE EXCEPTION 'timeline keyset repeats/skips'; END IF;
 SELECT marked INTO n FROM driftread.mark_reading_stream_read(alice);
 IF n<>2 OR EXISTS(SELECT 1 FROM driftread.user_article_reads WHERE user_id=alice AND article_id IN(hidden_article,signal_article)) THEN
 RAISE EXCEPTION 'mark-all touched non-reading sources'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.search_articles('forbiddenbodyword') WHERE id=article) OR
 EXISTS(SELECT 1 FROM driftread.list_feed_articles(hidden,alice)) OR
 EXISTS(SELECT 1 FROM driftread.list_reading_stream(alice) WHERE feed_id=hidden) THEN
 RAISE EXCEPTION 'legacy reader wrapper bypassed publication'; END IF;
 IF has_table_privilege('anon','driftread.articles','SELECT') OR
 has_table_privilege('authenticated','driftread.articles','SELECT') OR
 has_table_privilege('anon','driftread.article_publications','SELECT') OR
 has_function_privilege('authenticated','driftread.list_bookmark_publications(uuid,text)','EXECUTE') THEN
 RAISE EXCEPTION 'raw/publication/internal RPC accessible through Data API'; END IF;
 IF NOT has_table_privilege('service_role','driftread.article_publications','SELECT') THEN
 RAISE EXCEPTION 'backend publication grant missing'; END IF;
END;
$$;
ROLLBACK;
