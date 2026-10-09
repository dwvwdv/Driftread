-- Durable, service-only jobs. No external network or AI jobs are introduced.
CREATE TABLE IF NOT EXISTS driftread.background_job_kinds (
    kind text PRIMARY KEY CHECK (length(kind) BETWEEN 1 AND 100),
    max_concurrency integer NOT NULL DEFAULT 1 CHECK (max_concurrency BETWEEN 1 AND 32)
);
INSERT INTO driftread.background_job_kinds(kind)
VALUES ('refresh'),('discovery'),('retention') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS driftread.background_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL REFERENCES driftread.background_job_kinds(kind),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    singleton_key text,
    priority integer NOT NULL DEFAULT 0 CHECK (priority BETWEEN -100 AND 100),
    timeout_seconds integer NOT NULL DEFAULT 1800 CHECK (timeout_seconds BETWEEN 1 AND 86400),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','dead')),
    available_at timestamptz NOT NULL DEFAULT now(),
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 20),
    repeat_seconds integer NOT NULL DEFAULT 0 CHECK (repeat_seconds BETWEEN 0 AND 604800),
    worker_id uuid,
    lease_token uuid,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    error text,
    CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536),
    CHECK (singleton_key IS NULL OR length(singleton_key) BETWEEN 1 AND 200),
    CHECK ((status='running') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS background_jobs_singleton_idx
ON driftread.background_jobs(kind,singleton_key) WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS background_jobs_due_idx
ON driftread.background_jobs(kind,priority DESC,available_at,created_at,id) WHERE status='queued';
CREATE INDEX IF NOT EXISTS background_jobs_expired_idx
ON driftread.background_jobs(lease_expires_at,id) WHERE status='running';
ALTER TABLE driftread.background_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.background_job_kinds ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.background_jobs,driftread.background_job_kinds FROM PUBLIC,anon,authenticated;
GRANT ALL ON driftread.background_jobs,driftread.background_job_kinds TO service_role;

CREATE OR REPLACE FUNCTION driftread.enqueue_background_job(
    p_kind text, p_payload jsonb DEFAULT '{}', p_singleton_key text DEFAULT NULL,
    p_available_at timestamptz DEFAULT now(), p_max_attempts integer DEFAULT 3,
    p_repeat_seconds integer DEFAULT NULL, p_priority integer DEFAULT NULL,
    p_timeout_seconds integer DEFAULT NULL
) RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,driftread AS $$
DECLARE v_id uuid;
BEGIN
    INSERT INTO driftread.background_jobs(kind,payload,singleton_key,available_at,max_attempts,repeat_seconds,priority,timeout_seconds)
    VALUES(p_kind,p_payload,p_singleton_key,p_available_at,p_max_attempts,
        coalesce(p_repeat_seconds,0),coalesce(p_priority,0),coalesce(p_timeout_seconds,1800))
    ON CONFLICT(kind,singleton_key) WHERE status IN ('queued','running')
    DO UPDATE SET
        repeat_seconds=coalesce(p_repeat_seconds,background_jobs.repeat_seconds),
        priority=coalesce(p_priority,background_jobs.priority),
        timeout_seconds=coalesce(p_timeout_seconds,background_jobs.timeout_seconds)
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION driftread.claim_background_job(
    p_kind text,p_worker_id uuid,p_lease_seconds integer DEFAULT 120
) RETURNS SETOF driftread.background_jobs LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog,driftread AS $$
DECLARE v_capacity integer; v_id uuid;
BEGIN
    IF p_worker_id IS NULL OR p_lease_seconds NOT BETWEEN 30 AND 3600 THEN
        RAISE EXCEPTION 'Invalid job lease' USING ERRCODE='22023';
    END IF;
    -- Serialize capacity decisions for one kind across independent RPC transactions.
    SELECT max_concurrency INTO v_capacity FROM driftread.background_job_kinds WHERE kind=p_kind FOR NO KEY UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Unknown job kind' USING ERRCODE='22023'; END IF;
    IF (SELECT count(*) FROM driftread.background_jobs WHERE kind=p_kind AND status='running')>=v_capacity THEN RETURN; END IF;
    SELECT id INTO v_id FROM driftread.background_jobs
    WHERE kind=p_kind AND status='queued' AND available_at<=clock_timestamp()
    ORDER BY priority DESC,available_at,created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED;
    IF NOT FOUND THEN RETURN; END IF;
    RETURN QUERY UPDATE driftread.background_jobs
    SET status='running',attempts=attempts+1,worker_id=p_worker_id,lease_token=gen_random_uuid(),
        lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds)
    WHERE id=v_id RETURNING *;
END $$;

CREATE OR REPLACE FUNCTION driftread.renew_background_job(
    p_id uuid,p_token uuid,p_lease_seconds integer DEFAULT 120
) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,driftread AS $$
BEGIN
    IF p_lease_seconds NOT BETWEEN 30 AND 3600 THEN RAISE EXCEPTION 'Invalid job lease' USING ERRCODE='22023'; END IF;
    UPDATE driftread.background_jobs
    SET lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds)
    WHERE id=p_id AND status='running' AND lease_token=p_token AND lease_expires_at>clock_timestamp();
    RETURN FOUND;
END $$;

CREATE OR REPLACE FUNCTION driftread.finish_background_job(
    p_id uuid,p_token uuid,p_succeeded boolean,p_error text DEFAULT NULL
) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,driftread AS $$
DECLARE v_job driftread.background_jobs; v_terminal boolean;
BEGIN
    SELECT * INTO v_job FROM driftread.background_jobs WHERE id=p_id AND status='running'
        AND lease_token=p_token AND lease_expires_at>clock_timestamp() FOR UPDATE;
    IF NOT FOUND THEN RETURN false; END IF;
    v_terminal:=p_succeeded OR v_job.attempts>=v_job.max_attempts;
    UPDATE driftread.background_jobs SET
        status=CASE WHEN p_succeeded THEN 'succeeded' WHEN v_terminal THEN 'dead' ELSE 'queued' END,
        available_at=clock_timestamp()+make_interval(secs=>LEAST(3600,15*power(2,v_job.attempts-1)::integer)),
        finished_at=CASE WHEN v_terminal THEN clock_timestamp() ELSE NULL END,
        worker_id=NULL,lease_token=NULL,lease_expires_at=NULL,error=left(p_error,200)
    WHERE id=p_id;
    -- Success/dead-letter and the next periodic occurrence commit together.
    IF v_terminal AND v_job.repeat_seconds>0 THEN
        PERFORM driftread.enqueue_background_job(v_job.kind,v_job.payload,v_job.singleton_key,
            clock_timestamp()+make_interval(secs=>v_job.repeat_seconds),v_job.max_attempts,v_job.repeat_seconds,v_job.priority,v_job.timeout_seconds);
    END IF;
    RETURN true;
END $$;

CREATE OR REPLACE FUNCTION driftread.reconcile_background_jobs(p_limit integer DEFAULT 100)
RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,driftread AS $$
DECLARE v_job driftread.background_jobs; v_count integer:=0; v_terminal boolean;
BEGIN
    IF p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'Invalid sweep limit' USING ERRCODE='22023'; END IF;
    FOR v_job IN SELECT * FROM driftread.background_jobs WHERE status='running'
        AND lease_expires_at<=clock_timestamp() ORDER BY lease_expires_at,id
        LIMIT p_limit FOR UPDATE SKIP LOCKED LOOP
        v_terminal:=v_job.attempts>=v_job.max_attempts;
        UPDATE driftread.background_jobs SET status=CASE WHEN v_terminal THEN 'dead' ELSE 'queued' END,
            available_at=clock_timestamp()+make_interval(secs=>LEAST(3600,15*power(2,v_job.attempts-1)::integer)),
            finished_at=CASE WHEN v_terminal THEN clock_timestamp() ELSE NULL END,
            worker_id=NULL,lease_token=NULL,lease_expires_at=NULL,error='LeaseExpired' WHERE id=v_job.id;
        IF v_terminal AND v_job.repeat_seconds>0 THEN
            PERFORM driftread.enqueue_background_job(v_job.kind,v_job.payload,v_job.singleton_key,
                clock_timestamp()+make_interval(secs=>v_job.repeat_seconds),v_job.max_attempts,v_job.repeat_seconds,v_job.priority,v_job.timeout_seconds);
        END IF;
        v_count:=v_count+1;
    END LOOP;
    RETURN v_count;
END $$;

REVOKE ALL ON FUNCTION driftread.enqueue_background_job(text,jsonb,text,timestamptz,integer,integer,integer,integer),
 driftread.claim_background_job(text,uuid,integer),driftread.renew_background_job(uuid,uuid,integer),
 driftread.finish_background_job(uuid,uuid,boolean,text),driftread.reconcile_background_jobs(integer)
FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.enqueue_background_job(text,jsonb,text,timestamptz,integer,integer,integer,integer),
 driftread.claim_background_job(text,uuid,integer),driftread.renew_background_job(uuid,uuid,integer),
 driftread.finish_background_job(uuid,uuid,boolean,text),driftread.reconcile_background_jobs(integer)
TO service_role;

NOTIFY pgrst,'reload schema';

CREATE OR REPLACE FUNCTION driftread.background_queue_status()
RETURNS jsonb LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('queued',count(*) FILTER(WHERE status='queued'),
  'running',count(*) FILTER(WHERE status='running'),'dead',count(*) FILTER(WHERE status='dead'),
  'succeeded',count(*) FILTER(WHERE status='succeeded'),
  'next_available_at',min(available_at) FILTER(WHERE status='queued'))
 FROM driftread.background_jobs
$$;
REVOKE ALL ON FUNCTION driftread.background_queue_status() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.background_queue_status() TO service_role;
NOTIFY pgrst,'reload schema';
