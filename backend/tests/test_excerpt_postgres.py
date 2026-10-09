"""Excerpt RPCs omit large bodies before the service-role network boundary."""
from pathlib import Path

from tests.test_postgres_lifecycle import pg_database, connection


def test_excerpt_payload_fulltext_match_rights_and_owner_isolation(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / 'sql/test_excerpt_projection.sql').read_text())


def test_excerpt_migration_replay_preserves_contract_and_grants(pg_database):
    migrations = Path(__file__).parent.parent / 'migrations'
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            # Reproduce the former unmerged return contracts before replay:
            # CREATE OR REPLACE alone cannot change a function's return type.
            cur.execute("""
                DROP FUNCTION driftread.list_personal_publications(uuid,timestamptz,timestamptz,boolean,int);
                CREATE FUNCTION driftread.list_personal_publications(
                    p_user_id uuid,p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL,
                    p_exclude_backfill boolean DEFAULT false,p_limit int DEFAULT 100)
                RETURNS SETOF driftread.article_publications LANGUAGE sql AS
                $$ SELECT * FROM driftread.article_publications WHERE false $$;
                DROP FUNCTION driftread.personal_publication_search(uuid,text,int);
                CREATE FUNCTION driftread.personal_publication_search(
                    p_user_id uuid,p_query text,p_limit int DEFAULT 20)
                RETURNS SETOF driftread.article_publications LANGUAGE sql AS
                $$ SELECT * FROM driftread.article_publications WHERE false $$;
            """)
            for _ in range(2):
                for filename in ('20261009000300_publication_read_layer.sql',
                                 '20261009000700_incremental_sync.sql'):
                    cur.execute((migrations / filename).read_text())
        conn.commit()
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / 'sql/test_excerpt_projection.sql').read_text())
