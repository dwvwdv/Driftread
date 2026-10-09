-- Run after the final migration's notification arrives on another connection.
DO $$
BEGIN
 IF to_regclass('driftread.article_publications') IS NULL
    OR to_regclass('driftread.event_objects') IS NULL
    OR to_regclass('driftread.user_heat_snapshots') IS NULL
    OR to_regprocedure('driftread.personal_sync_snapshot(uuid,bigint,uuid[])') IS NULL
    OR to_regprocedure('driftread.personal_publication_search(uuid,text,integer)') IS NULL
    OR to_regprocedure('driftread.replay_personal_article_state(uuid,uuid,text,boolean)') IS NULL THEN
   RAISE EXCEPTION 'Schema reload arrived before all release objects were committed';
 END IF;
 IF NOT has_function_privilege('service_role',
     'driftread.replay_personal_article_state(uuid,uuid,text,boolean)', 'EXECUTE')
    OR has_function_privilege('anon',
     'driftread.replay_personal_article_state(uuid,uuid,text,boolean)', 'EXECUTE') THEN
   RAISE EXCEPTION 'Schema reload arrived before the final RPC grants were committed';
 END IF;
END $$;
