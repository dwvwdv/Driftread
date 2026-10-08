BEGIN;
INSERT INTO driftread.feeds(title,url,category,language,participation_mode) VALUES
 ('sourcemodel public','https://source-model.test/normal','visible','en','normal'),
 ('sourcemodel private','https://source-model.test/private','hidden','secret','private'),
 ('sourcemodel signal','https://source-model.test/signal','signals','signal','signal_only');
DO $$ DECLARE source uuid; BEGIN
 SELECT id INTO source FROM driftread.feeds WHERE url='https://source-model.test/normal';
 PERFORM driftread.record_source_fetch(source,'2026-10-09T01:00:00Z',false);
 PERFORM driftread.record_source_fetch(source,'2026-10-09T01:01:00Z',true);
 PERFORM driftread.record_source_fetch(source,'2026-10-08T01:01:00Z',true);
 IF (SELECT last_ok_at FROM driftread.feeds WHERE id=source)<>'2026-10-09T01:01:00Z'::timestamptz THEN RAISE EXCEPTION 'success went backwards'; END IF;
 PERFORM driftread.record_source_fetch(source,'2026-10-09T02:00:00Z',false);
 IF (SELECT last_ok_at FROM driftread.feeds WHERE id=source)<>'2026-10-09T01:01:00Z'::timestamptz THEN RAISE EXCEPTION 'failure advanced success'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.import_readable_source('{"title":"overwrite","url":"https://source-model.test/private"}')) THEN RAISE EXCEPTION 'private URL exposed'; END IF;
 IF (SELECT title FROM driftread.feeds WHERE url='https://source-model.test/private')<>'sourcemodel private' THEN RAISE EXCEPTION 'private metadata overwritten'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.search_feeds('sourcemodel') WHERE url<>'https://source-model.test/normal') THEN RAISE EXCEPTION 'search disclosed private source'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.sample_feed_candidates(NULL,NULL,'unfiltered',250) WHERE participation_mode<>'normal') THEN RAISE EXCEPTION 'recommendations exposed signal/private'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.list_feed_categories() WHERE category IN('hidden','signals')) THEN RAISE EXCEPTION 'categories disclosed private metadata'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.list_feed_languages() WHERE language IN('secret','signal')) THEN RAISE EXCEPTION 'languages disclosed private metadata'; END IF;
 IF has_function_privilege('anon','driftread.import_readable_source(jsonb)','EXECUTE') OR has_function_privilege('authenticated','driftread.record_source_fetch(uuid,timestamptz,boolean)','EXECUTE') THEN RAISE EXCEPTION 'source write RPC exposed'; END IF;
END $$;
SET LOCAL ROLE anon;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM driftread.feeds WHERE url IN('https://source-model.test/private','https://source-model.test/signal')) THEN RAISE EXCEPTION 'RLS exposes hidden roles'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.feeds WHERE url='https://source-model.test/normal') THEN RAISE EXCEPTION 'normal source lost public read'; END IF;
END $$;
RESET ROLE;
ROLLBACK;
