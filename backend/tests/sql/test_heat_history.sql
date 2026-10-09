BEGIN;
DO $$
DECLARE u uuid; other_user uuid; f uuid; signal uuid; a uuid; sid uuid;
 snap jsonb; history jsonb; original_sources jsonb; stamp timestamptz; ev jsonb;
BEGIN
 INSERT INTO auth.users VALUES(gen_random_uuid()) RETURNING id INTO u;
 INSERT INTO auth.users VALUES(gen_random_uuid()) RETURNING id INTO other_user;
 INSERT INTO driftread.feeds(title,url,last_ok_at,fetch_interval_minutes)
 VALUES('History','https://history.invalid/rss',now(),60) RETURNING id INTO f;
 INSERT INTO driftread.feeds(title,url,participation_mode,last_ok_at)
 VALUES('Late signal','https://history-signal.invalid/rss','signal_only',NULL) RETURNING id INTO signal;
 INSERT INTO driftread.user_feeds(user_id,feed_id) VALUES(u,f);
 INSERT INTO driftread.articles(feed_id,title,url,summary,content,published_at,fetched_at)
 VALUES(f,'Do not persist this text','https://history.invalid/post','<b>summary</b>','private full body',now()-interval '24 hours',now()) RETURNING id INTO a;
 stamp:=clock_timestamp();
 snap:=driftread.capture_personal_heat(u,stamp,NULL,false,500);
 sid:=(snap->>'snapshot_id')::uuid;
 SELECT sources INTO original_sources FROM driftread.user_heat_snapshots WHERE id=sid;
 IF jsonb_array_length(snap->'candidates')<>1 THEN RAISE EXCEPTION 'capture not connected'; END IF;
 IF (SELECT candidates::text||evidence::text FROM driftread.user_heat_snapshots WHERE id=sid) LIKE '%persist this text%'
  OR (SELECT candidates::text||evidence::text FROM driftread.user_heat_snapshots WHERE id=sid) LIKE '%private full body%'
 THEN RAISE EXCEPTION 'stored article text'; END IF;
 -- An article discovered after the snapshot repairs heat by source publication
 -- time, without becoming a new historical candidate or replacing old health.
 INSERT INTO driftread.articles(feed_id,title,url,published_at,fetched_at) VALUES
 (signal,'Late','https://history.invalid/post',stamp-interval '12 hours',stamp+interval '1 minute');
 INSERT INTO driftread.articles(feed_id,title,url,published_at,fetched_at,backfill) VALUES
 (signal,'Backfill','https://history.invalid/post#backfill',stamp-interval '1 hour',stamp+interval '1 minute',true),
 (signal,'Future','https://history.invalid/post#future',stamp+interval '1 day',stamp+interval '1 minute',false),
 (signal,'Unknown','https://history.invalid/post#unknown',NULL,stamp+interval '1 minute',false);
 UPDATE driftread.feeds SET last_ok_at=now() WHERE id=signal;
 IF (driftread.repair_personal_heat_history(other_user,20)->>'repaired_count')::int<>0 THEN RAISE EXCEPTION 'repair crossed owner'; END IF;
 IF driftread.read_personal_heat_history(other_user,sid) IS NOT NULL THEN RAISE EXCEPTION 'read crossed owner'; END IF;
 IF (driftread.repair_personal_heat_history(u,1)->>'repaired_count')::int<>1 THEN RAISE EXCEPTION 'repair missing'; END IF;
 history:=driftread.read_personal_heat_history(u,sid);
 IF history->'sources'<>original_sources OR history->>'repaired_at' IS NULL THEN RAISE EXCEPTION 'past health replaced'; END IF;
 IF jsonb_array_length(history->'evidence')<>2 OR jsonb_array_length(history->'candidates')<>1 THEN RAISE EXCEPTION 'late evidence not repaired'; END IF;
 SELECT e INTO ev FROM jsonb_array_elements(history->'evidence') e WHERE e->>'feed_id'=signal::text;
 IF (ev->>'source_time')::timestamptz<>stamp-interval '12 hours' THEN RAISE EXCEPTION 'fetch/future/backfill created heat'; END IF;
 -- Idempotent repair, and revocation is projected again when reading history.
 PERFORM driftread.repair_personal_heat_history(u,1);
 IF jsonb_array_length(driftread.read_personal_heat_history(u,sid)->'evidence')<>2 THEN RAISE EXCEPTION 'repair duplicates'; END IF;
 UPDATE driftread.feeds SET participation_mode='private' WHERE id=signal;
 IF jsonb_array_length(driftread.read_personal_heat_history(u,sid)->'evidence')<>1 THEN RAISE EXCEPTION 'private evidence leak'; END IF;
 UPDATE driftread.feeds SET fulltext_policy='summary_only' WHERE id=f;
 history:=driftread.read_personal_heat_history(u,sid);
 IF history->'candidates'->0->>'summary'<>'summary' OR history->'candidates'->0 ? 'content' THEN RAISE EXCEPTION 'current rights projection bypassed'; END IF;
 UPDATE driftread.user_feeds SET muted_at=now() WHERE user_id=u AND feed_id=f;
 IF jsonb_array_length(driftread.read_personal_heat_history(u,sid)->'candidates')<>0 THEN RAISE EXCEPTION 'mute bypass'; END IF;
 UPDATE driftread.user_feeds SET muted_at=NULL WHERE user_id=u AND feed_id=f;
 INSERT INTO driftread.user_feed_feedback(user_id,feed_id,feedback_type) VALUES(u,f,'disliked');
 IF jsonb_array_length(driftread.read_personal_heat_history(u,sid)->'candidates')<>0 THEN RAISE EXCEPTION 'feedback bypass'; END IF;
 DELETE FROM driftread.user_feed_feedback WHERE user_id=u AND feed_id=f;
 UPDATE driftread.feeds SET participation_mode='private' WHERE id=f;
 IF jsonb_array_length(driftread.read_personal_heat_history(u,sid)->'candidates')<>0 THEN RAISE EXCEPTION 'private publication leak'; END IF;
 IF has_table_privilege('authenticated','driftread.user_heat_snapshots','SELECT')
  OR has_function_privilege('authenticated','driftread.read_personal_heat_history(uuid,uuid)','EXECUTE')
  OR has_function_privilege('anon','driftread.repair_personal_heat_history(uuid,int)','EXECUTE') THEN RAISE EXCEPTION 'raw history exposed'; END IF;
END $$;
ROLLBACK;
