-- Homepage attempts have their own cadence, independent of article backlog ticks.
ALTER TABLE driftread.feeds
    ADD COLUMN IF NOT EXISTS last_blogroll_attempt_at timestamptz;

CREATE OR REPLACE FUNCTION driftread.claim_blogroll_attempt(
    p_feed_id uuid,
    p_interval_hours integer
) RETURNS boolean
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    claimed integer;
BEGIN
    IF p_interval_hours IS NULL OR p_interval_hours < 1 THEN
        RAISE EXCEPTION 'blogroll interval must be positive';
    END IF;
    UPDATE driftread.feeds
    SET last_blogroll_attempt_at = now()
    WHERE id = p_feed_id
      AND (last_blogroll_attempt_at IS NULL
           OR last_blogroll_attempt_at <= now() - make_interval(hours => p_interval_hours));
    GET DIAGNOSTICS claimed = ROW_COUNT;
    RETURN claimed = 1;
END;
$$;

REVOKE ALL ON FUNCTION driftread.claim_blogroll_attempt(uuid, integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.claim_blogroll_attempt(uuid, integer)
    TO service_role;

COMMENT ON COLUMN driftread.feeds.last_blogroll_attempt_at IS
    'Durable homepage attempt time; advanced before network work, including failed attempts.';
COMMENT ON FUNCTION driftread.claim_blogroll_attempt(uuid, integer) IS
    'Atomically reserve a homepage attempt at the configured harvest interval; service workers only.';

NOTIFY pgrst, 'reload schema';
