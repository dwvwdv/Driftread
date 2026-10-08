from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import psycopg2

from tests.test_postgres_lifecycle import pg_database, connection, scalar


def test_manual_membership_aliases_roles_and_heat(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute(
                (Path(__file__).parent / "sql/test_manual_events_heat.sql").read_text()
            )


def test_manual_edit_same_version_allows_only_one_winner(pg_database):
    with connection(pg_database) as conn:
        event = scalar(
            conn,
            "INSERT INTO driftread.event_objects(kind,title) VALUES('fact','Race') RETURNING id",
        )
        conn.commit()

    def edit(title):
        try:
            with connection(pg_database) as conn:
                value = scalar(
                    conn,
                    "SELECT driftread.mutate_manual_event(%s,1,%s,NULL)",
                    (event, title),
                )
                conn.commit()
                return value["version"]
        except psycopg2.errors.SerializationFailure:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(edit, ["One", "Two"]))
    assert sorted(outcomes, key=str) == [2, "conflict"]
