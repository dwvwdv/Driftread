from pathlib import Path

from tests.test_postgres_lifecycle import pg_database, connection, scalar


def test_capture_repair_owner_scope_frozen_health_and_revocation(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / "sql/test_heat_history.sql").read_text())


def test_history_retention_and_expected_relation_names(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute("""DO $$ DECLARE u uuid; first_id uuid; second_id uuid; i int;
            BEGIN
             INSERT INTO auth.users VALUES(gen_random_uuid()) RETURNING id INTO u;
             FOR i IN 1..103 LOOP
              PERFORM driftread.capture_personal_heat(u,now()+i*interval '1 microsecond');
             END LOOP;
             IF (SELECT count(*) FROM driftread.user_heat_snapshots WHERE user_id=u)<>100
              THEN RAISE EXCEPTION 'unbounded retention'; END IF;
             IF (driftread.repair_personal_heat_history(u,999)->>'repaired_count')::int<>20
              THEN RAISE EXCEPTION 'unbounded repair'; END IF;
             INSERT INTO driftread.event_objects(kind,title) VALUES('fact','One') RETURNING id INTO first_id;
             INSERT INTO driftread.event_objects(kind,title) VALUES('fact','Two') RETURNING id INTO second_id;
             PERFORM driftread.mutate_manual_event(first_id,1,NULL,NULL,second_id,'SAME_OCCURRENCE');
             PERFORM driftread.mutate_manual_event(first_id,2,NULL,NULL,second_id,'ROUNDUP');
             IF NOT EXISTS(SELECT 1 FROM driftread.event_relations WHERE relation='ROUNDUP')
              THEN RAISE EXCEPTION 'expected taxonomy missing'; END IF;
            END $$;""")
        assert scalar(conn, "SELECT count(*) FROM driftread.user_heat_snapshots") == 100
