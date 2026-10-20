"""Preview historical review holds; apply only individually selected preview tokens.

Run with backend dependencies installed. DATABASE_URL must point to the intended
DB. No external fetches or provider calls. Default invocation is read-only.
"""
import argparse
from collections import Counter
import json
import os
from uuid import UUID

import psycopg2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', nargs=3, metavar=('REVIEW_ID', 'POLICY_VERSION', 'CANDIDATE_VERSION'))
    args = parser.parse_args()
    with psycopg2.connect(os.environ['DATABASE_URL'], options='-c statement_timeout=15000') as conn:
        with conn.cursor() as cur:
            if args.apply:
                review_id, policy, candidate = args.apply
                cur.execute('SELECT driftread.reconcile_source_review(%s,%s,%s,false)',
                            (str(UUID(review_id)), int(policy), int(candidate)))
                print(json.dumps(cur.fetchone()[0], ensure_ascii=False))
                return
            cur.execute("""SELECT r.id,c.review_version FROM driftread.source_reviews r
                JOIN driftread.discovery_candidates c ON c.id=r.candidate_id
                WHERE r.status='awaiting_human' ORDER BY r.id""")
            rows = cur.fetchall()
        conn.rollback()
        counts = Counter()
        previews = []
        for row in rows:
            with conn.cursor() as cur:
                cur.execute("SELECT version FROM driftread.app_settings WHERE key='source.review'")
                policy_version = cur.fetchone()[0]
                cur.execute('SELECT driftread.reconcile_source_review(%s,%s,%s,true)',
                            (row[0], policy_version, row[1]))
                preview = cur.fetchone()[0]
                counts[preview.get('reason', preview.get('error', 'unknown'))] += 1
                previews.append(preview)
            conn.rollback()
        print(json.dumps({'counts': dict(counts), 'reviews': previews}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
