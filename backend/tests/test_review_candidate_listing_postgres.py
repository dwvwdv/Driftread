"""Real SQL filters, latest-review ordering and manual-resolution fencing."""
from pathlib import Path

import pytest

from tests.test_postgres_lifecycle import connection, pg_database, scalar
from tests.test_source_review_postgres import candidate


def test_candidate_review_listing_sql(pg_database):
    with connection(pg_database, 'service_role') as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / 'sql/test_review_candidate_listing.sql').read_text())


@pytest.mark.parametrize('mutation', ['requested', 'ready', 'stale', 'no_result', 'json_null_result', 'candidate_version', 'newer_review'])
def test_manual_resolution_rejects_unfinished_or_stale_selection(pg_database, mutation):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        review_id = scalar(conn, """INSERT INTO driftread.source_reviews(candidate_id,status,engine,model,
          policy_version,policy,candidate_version,result)
          VALUES(%s,'awaiting_human','external_agent','test',1,'{}',1,'{"decision":"accept"}') RETURNING id""", (cid,))
        if mutation in {'requested', 'ready', 'stale'}:
            scalar(conn, 'UPDATE driftread.source_reviews SET status=%s WHERE id=%s RETURNING id', (mutation, review_id))
        elif mutation == 'no_result':
            scalar(conn, 'UPDATE driftread.source_reviews SET result=NULL WHERE id=%s RETURNING id', (review_id,))
        elif mutation == 'json_null_result':
            scalar(conn, "UPDATE driftread.source_reviews SET result='null'::jsonb WHERE id=%s RETURNING id", (review_id,))
        elif mutation == 'candidate_version':
            scalar(conn, 'UPDATE driftread.source_reviews SET candidate_version=0 WHERE id=%s RETURNING id', (review_id,))
        else:
            scalar(conn, """INSERT INTO driftread.source_reviews(candidate_id,status,engine,model,
              policy_version,policy,candidate_version,created_at)
              VALUES(%s,'rejected','external_agent','test',1,'{}',1,clock_timestamp()+interval '1 second') RETURNING id""", (cid,))
        result = scalar(conn, "SELECT driftread.resolve_source_review(%s,'accept')", (review_id,))
        assert result == {'error': 'conflict'}
        assert scalar(conn, 'SELECT status FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == 'pending'
        assert scalar(conn, 'SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s', (cid,)) is None


@pytest.mark.parametrize('decision,expected', [('accept', 'imported'), ('reject', 'rejected'), ('defer', 'held')])
def test_current_completed_review_can_be_resolved(pg_database, decision, expected):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        review_id = scalar(conn, """INSERT INTO driftread.source_reviews(candidate_id,status,engine,model,
          policy_version,policy,candidate_version,result)
          VALUES(%s,'awaiting_human','external_agent','test',1,'{}',1,'{"decision":"accept"}') RETURNING id""", (cid,))
        result = scalar(conn, 'SELECT driftread.resolve_source_review(%s,%s)', (review_id, decision))
        assert 'error' not in result
        assert scalar(conn, 'SELECT status FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == expected
