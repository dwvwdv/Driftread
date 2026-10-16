-- Browser retries refer to the review the administrator actually selected.
-- Keep the scheduler's existing request RPC and its unpaid retry semantics.
CREATE OR REPLACE FUNCTION driftread.retry_source_review(
 p_candidate_id uuid,p_expected_review_id uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE c driftread.discovery_candidates; r driftread.source_reviews;
BEGIN
 -- Match request/claim/submit ordering: policy, then candidate, then review.
 -- Taking this before the candidate also avoids a queued policy writer causing
 -- the nested request's shared policy lock to wait behind a candidate waiter.
 PERFORM 1 FROM driftread.app_settings WHERE key='source.review' FOR SHARE;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=p_candidate_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
 SELECT * INTO r FROM driftread.source_reviews WHERE candidate_id=c.id
  ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE;
 IF NOT FOUND OR r.id IS DISTINCT FROM p_expected_review_id
  OR r.status NOT IN('rejected','deferred','failed','stale','overridden') THEN
  RETURN jsonb_build_object('error','conflict');
 END IF;
 -- The nested request retains the same candidate lock while enforcing policy,
 -- manual overrides and source identity, then creates the explicit retry key.
 RETURN driftread.request_source_review(c.id,true);
END $$;
REVOKE ALL ON FUNCTION driftread.retry_source_review(uuid,uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.retry_source_review(uuid,uuid) TO service_role;

NOTIFY pgrst, 'reload schema';
