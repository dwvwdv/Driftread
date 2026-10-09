DO $$
DECLARE
 alias_id uuid := gen_random_uuid(); source_id uuid := gen_random_uuid(); target_id uuid := gen_random_uuid();
 target_alias uuid := gen_random_uuid(); peer uuid := gen_random_uuid(); peer2 uuid := gen_random_uuid(); peer3 uuid := gen_random_uuid();
 result jsonb;
BEGIN
 INSERT INTO driftread.event_objects(id,kind,title) VALUES
 (alias_id,'story','Old source alias'),(source_id,'story','Source'),(target_id,'story','Target'),
 (target_alias,'story','Old target alias'),(peer,'story','Peer'),(peer2,'fact','Peer 2'),(peer3,'story','Peer 3');
 INSERT INTO driftread.event_relations(left_id,right_id,relation) VALUES
 (least(alias_id,peer),greatest(alias_id,peer),'SAME_STORY'),
 (least(source_id,peer),greatest(source_id,peer),'SAME_STORY'),
 (least(source_id,target_id),greatest(source_id,target_id),'SAME_STORY'),
 (least(source_id,peer2),greatest(source_id,peer2),'REACTION'),
 (least(target_id,peer),greatest(target_id,peer),'SAME_STORY'),
 (least(target_id,peer3),greatest(target_id,peer3),'UNRELATED');
 PERFORM driftread.merge_manual_stories(alias_id,source_id,1,1);
 PERFORM driftread.merge_manual_stories(target_alias,target_id,1,1);
 -- Reproduce alias edges that earlier code left in a pre-upgrade database.
 INSERT INTO driftread.event_relations(left_id,right_id,relation) VALUES
 (least(alias_id,peer2),greatest(alias_id,peer2),'REACTION'),
 (least(target_alias,peer3),greatest(target_alias,peer3),'UNRELATED');
 result := driftread.merge_manual_stories(source_id,target_id,2,2);
 IF (result->>'version')::int<>3 THEN RAISE EXCEPTION 'target version did not advance once'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.event_relations WHERE left_id IN(alias_id,source_id,target_alias)
           OR right_id IN(alias_id,source_id,target_alias)) THEN
   RAISE EXCEPTION 'Relation still names a merged source or alias';
 END IF;
 IF (SELECT count(*) FROM driftread.event_relations WHERE left_id=target_id OR right_id=target_id)<>3 THEN
   RAISE EXCEPTION 'External relations lost, self relation survived, or duplicates remained';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.event_relations WHERE left_id=least(target_id,peer2)
               AND right_id=greatest(target_id,peer2) AND relation='REACTION') THEN
   RAISE EXCEPTION 'Source relation label or canonical endpoint order lost';
 END IF;
 IF jsonb_array_length(driftread.read_manual_event(alias_id,true)->'relations')<>3
    OR jsonb_array_length(driftread.read_manual_event(target_alias,true)->'relations')<>3 THEN
   RAISE EXCEPTION 'Alias reads did not expose the preserved target relations';
 END IF;
 -- Future writes through an old URL must also resolve the surviving endpoint.
 PERFORM driftread.mutate_manual_event(peer,1,NULL,NULL,alias_id,'ROUNDUP');
 IF NOT EXISTS(SELECT 1 FROM driftread.event_relations WHERE left_id=least(target_id,peer)
               AND right_id=greatest(target_id,peer) AND relation='ROUNDUP') THEN
   RAISE EXCEPTION 'PATCH recreated an edge to an old alias';
 END IF;
 BEGIN
   PERFORM driftread.mutate_manual_event(target_id,3,NULL,NULL,alias_id,'SAME_STORY');
   RAISE EXCEPTION 'Alias equivalent self relation accepted';
 EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
END $$;

DO $$
DECLARE
 source_id uuid := gen_random_uuid(); target_id uuid := gen_random_uuid(); peer uuid := gen_random_uuid(); member_fact uuid := gen_random_uuid();
BEGIN
 INSERT INTO driftread.event_objects(id,kind,title) VALUES
 (source_id,'story','Conflicting source'),(target_id,'story','Conflicting target'),
 (peer,'story','Conflict peer'),(member_fact,'fact','Preserve exclusion');
 INSERT INTO driftread.story_facts(story_id,fact_id,excluded) VALUES(source_id,member_fact,true);
 INSERT INTO driftread.event_relations(left_id,right_id,relation) VALUES
 (least(source_id,peer),greatest(source_id,peer),'FOLLOW_UP'),
 (least(target_id,peer),greatest(target_id,peer),'UNRELATED');
 BEGIN
   PERFORM driftread.merge_manual_stories(source_id,target_id,1,1);
   RAISE EXCEPTION 'Conflicting authored relation silently overwritten';
 EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
 IF EXISTS(SELECT 1 FROM driftread.event_objects WHERE id IN(source_id,target_id)
           AND (merged_into IS NOT NULL OR version<>1))
    OR EXISTS(SELECT 1 FROM driftread.story_facts WHERE story_id=target_id)
    OR NOT EXISTS(SELECT 1 FROM driftread.story_facts WHERE story_id=source_id AND fact_id=member_fact AND excluded)
    OR (SELECT count(*) FROM driftread.event_relations WHERE left_id IN(source_id,target_id)
        OR right_id IN(source_id,target_id))<>2 THEN
   RAISE EXCEPTION 'Rejected merge partially changed aliases, membership, versions, or relations';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.event_relations WHERE left_id=least(source_id,peer)
               AND right_id=greatest(source_id,peer) AND relation='FOLLOW_UP')
    OR NOT EXISTS(SELECT 1 FROM driftread.event_relations WHERE left_id=least(target_id,peer)
                  AND right_id=greatest(target_id,peer) AND relation='UNRELATED') THEN
   RAISE EXCEPTION 'Rejected merge did not retain both conflicting declarations';
 END IF;
END $$;
