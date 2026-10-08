DO $$
DECLARE
 u uuid:=gen_random_uuid(); f uuid:=gen_random_uuid(); mirror uuid:=gen_random_uuid(); priv uuid:=gen_random_uuid(); signal uuid:=gen_random_uuid(); lag uuid:=gen_random_uuid();
 a uuid:=gen_random_uuid(); a2 uuid:=gen_random_uuid(); ap uuid:=gen_random_uuid(); ab uuid:=gen_random_uuid(); asig uuid:=gen_random_uuid();
 fact uuid:=gen_random_uuid(); fact2 uuid:=gen_random_uuid(); story uuid:=gen_random_uuid(); story2 uuid:=gen_random_uuid(); story3 uuid:=gen_random_uuid();
 payload jsonb; result jsonb; count_value int; v bigint;
BEGIN
 INSERT INTO auth.users(id) VALUES(u);
 INSERT INTO driftread.feeds(id,title,url,signal_group,last_ok_at,participation_mode) VALUES
 (f,'Normal','https://normal.example/rss','mirror-group',now(),'normal'),
 (mirror,'Mirror','https://mirror.example/rss','mirror-group',now(),'normal'),
 (priv,'Private','https://private.example/rss',NULL,now(),'private'),
 (signal,'Signal','https://signal.example/rss',NULL,now(),'signal_only'),
 (lag,'Behind','https://lag.example/rss',NULL,NULL,'normal');
 INSERT INTO driftread.user_feeds(user_id,feed_id) VALUES(u,f),(u,mirror),(u,priv),(u,lag);
 INSERT INTO driftread.articles(id,feed_id,title,url,published_at,backfill) VALUES
 (a,f,'Article','https://shared.example/a#section',now()-interval '1 hour',false),
 (a2,mirror,'Mirror','https://shared.example/a',now()-interval '2 hour',false),
 (ap,priv,'Private secret','https://shared.example/a',now(),false),
 (asig,signal,'Signal evidence','https://shared.example/a',now()-interval '3 hour',false),
 (ab,f,'Imported old article','https://shared.example/old',now()-interval '1 hour',true);
 INSERT INTO driftread.event_objects(id,kind,title) VALUES
 (fact,'fact','Manual fact'),(fact2,'fact','Private fact'),
 (story,'story','First story'),(story2,'story','Second story'),(story3,'story','Third story');
 result:=driftread.mutate_manual_event(fact,1,NULL,jsonb_build_array(jsonb_build_object('id',a),jsonb_build_object('id',a2)));
 IF (result->>'version')::int<>2 THEN RAISE EXCEPTION 'membership version'; END IF;
 BEGIN
  PERFORM driftread.mutate_manual_event(fact,1,'stale',NULL);
  RAISE EXCEPTION 'stale version accepted';
 EXCEPTION WHEN serialization_failure THEN NULL; END;
 PERFORM driftread.mutate_manual_event(fact,2,NULL,jsonb_build_array(jsonb_build_object('id',a2,'excluded',true)));
 PERFORM driftread.mutate_manual_event(fact,3,'Renamed',jsonb_build_array(jsonb_build_object('id',a)));
 IF NOT(SELECT excluded FROM driftread.fact_articles WHERE fact_id=fact AND article_id=a2) THEN RAISE EXCEPTION 'exclusion vanished'; END IF;
 BEGIN
  PERFORM driftread.mutate_manual_event(fact,4,NULL,jsonb_build_array(jsonb_build_object('id',gen_random_uuid())));
  RAISE EXCEPTION 'bad member accepted';
 EXCEPTION WHEN foreign_key_violation THEN NULL; END;
 IF (SELECT version FROM driftread.event_objects WHERE id=fact)<>4 THEN RAISE EXCEPTION 'failed write consumed version'; END IF;
 PERFORM driftread.mutate_manual_event(story,1,NULL,jsonb_build_array(jsonb_build_object('id',fact)));
 PERFORM driftread.mutate_manual_event(story2,1,NULL,jsonb_build_array(jsonb_build_object('id',fact,'excluded',true)));
 result:=driftread.merge_manual_stories(story,story2,2,2);
 IF NOT(SELECT excluded FROM driftread.story_facts WHERE story_id=story2 AND fact_id=fact) THEN RAISE EXCEPTION 'merge erased exclusion'; END IF;
 PERFORM driftread.merge_manual_stories(story2,story3,3,1);
 IF (SELECT merged_into FROM driftread.event_objects WHERE id=story)<>story3 THEN RAISE EXCEPTION 'alias not flattened'; END IF;
 IF driftread.read_manual_event(story,true)->>'id'<>story3::text THEN RAISE EXCEPTION 'alias resolution'; END IF;
 BEGIN
  PERFORM driftread.merge_manual_stories(story3,story,2,3);
  RAISE EXCEPTION 'cycle accepted';
 EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
 PERFORM driftread.mutate_manual_event(fact2,1,NULL,jsonb_build_array(jsonb_build_object('id',ap)));
 IF driftread.read_manual_event(fact2,false) IS NOT NULL THEN RAISE EXCEPTION 'private fact disclosed'; END IF;
 IF jsonb_array_length(driftread.read_manual_event(fact,false)->'articles')<>1 THEN RAISE EXCEPTION 'exclusion public filter'; END IF;
 payload:=driftread.personal_heat_snapshot(u,now(),NULL,false,500);
 IF jsonb_array_length(payload->'sources')<>4 THEN RAISE EXCEPTION 'cohort privacy or missing lag source'; END IF;
 IF jsonb_array_length(payload->'candidates')<>2 THEN RAISE EXCEPTION 'display private/signal/backfill leak'; END IF;
 -- Same mirror group dedup, plus signal-only evidence, no private contributor.
 IF jsonb_array_length(payload->'evidence')<>2 THEN RAISE EXCEPTION 'participant dedup'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(payload->'evidence') e WHERE e->>'participant_key'='feed:'||priv::text) THEN RAISE EXCEPTION 'private heat leak'; END IF;
 INSERT INTO driftread.user_feed_feedback(user_id,feed_id,feedback_type) VALUES(u,f,'disliked');
 payload:=driftread.personal_heat_snapshot(u,now(),f,false,500);
 IF jsonb_array_length(payload->'candidates')<>0 THEN RAISE EXCEPTION 'dislike bypass'; END IF;
 INSERT INTO driftread.user_article_reads(user_id,article_id) VALUES(u,a2);
 payload:=driftread.personal_heat_snapshot(u,now(),mirror,true,500);
 IF jsonb_array_length(payload->'candidates')<>0 THEN RAISE EXCEPTION 'unread scope bypass'; END IF;
 IF has_function_privilege('authenticated','driftread.personal_heat_snapshot(uuid,timestamptz,uuid,boolean,int)','EXECUTE') THEN RAISE EXCEPTION 'heat RPC exposed'; END IF;
 IF has_function_privilege('anon','driftread.read_manual_event(uuid,boolean)','EXECUTE') THEN RAISE EXCEPTION 'admin flag RPC exposed'; END IF;
END $$;
