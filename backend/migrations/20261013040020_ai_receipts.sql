-- Private paid-request receipts. Budgets include unknown outcomes conservatively.
CREATE TABLE IF NOT EXISTS driftread.ai_request_receipts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), logical_key text NOT NULL UNIQUE,
 capability text NOT NULL, model text NOT NULL, input_hash text NOT NULL,
 policy_hash text NOT NULL, config_hash text NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','received','completed','failed','unknown')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 2),
 active_token uuid, lease_expires_at timestamptz, paused boolean NOT NULL DEFAULT false,
 result jsonb, error_code text, actual_model text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS driftread.ai_request_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), receipt_id uuid NOT NULL REFERENCES driftread.ai_request_receipts(id) ON DELETE CASCADE,
 attempt integer NOT NULL, token uuid NOT NULL UNIQUE,
 status text NOT NULL CHECK(status IN ('pending','received','completed','failed','unknown')),
 reserved_usd numeric(16,10) NOT NULL CHECK(reserved_usd>0),
 charged_usd numeric(16,10) NOT NULL CHECK(charged_usd>=0),
 raw_response text CHECK(octet_length(raw_response)<=65536), http_status integer,
 usage jsonb, latency_ms integer, error_code text,
 started_at timestamptz NOT NULL DEFAULT now(), received_at timestamptz,
 UNIQUE(receipt_id,attempt)
);
ALTER TABLE driftread.ai_request_receipts ADD COLUMN IF NOT EXISTS retry_parent_key text;
CREATE INDEX IF NOT EXISTS ai_receipts_retry_parent ON driftread.ai_request_receipts(retry_parent_key,created_at);
CREATE TABLE IF NOT EXISTS driftread.ai_request_retry_bindings (
 retry_key uuid PRIMARY KEY, logical_key text NOT NULL,
 receipt_id uuid NOT NULL REFERENCES driftread.ai_request_receipts(id) ON DELETE CASCADE
);
ALTER TABLE driftread.ai_request_retry_bindings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.ai_request_retry_bindings FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,DELETE ON driftread.ai_request_retry_bindings TO service_role;
CREATE INDEX IF NOT EXISTS ai_receipts_capability_time ON driftread.ai_request_receipts(capability,created_at);
CREATE INDEX IF NOT EXISTS ai_attempts_time ON driftread.ai_request_attempts(started_at);
ALTER TABLE driftread.ai_request_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.ai_request_attempts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.ai_request_receipts,driftread.ai_request_attempts FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON driftread.ai_request_receipts,driftread.ai_request_attempts TO service_role;

CREATE OR REPLACE FUNCTION driftread.reserve_ai_request(
 p_logical_key text,p_capability text,p_model text,p_input_hash text,p_policy_hash text,p_config_hash text,
 p_reserved_usd numeric,p_minute_usd numeric,p_hour_usd numeric,p_day_usd numeric
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=driftread,pg_temp AS $$
DECLARE r driftread.ai_request_receipts; t uuid; v_now timestamptz:=clock_timestamp(); v_used numeric;
BEGIN
 IF p_reserved_usd IS NULL OR p_reserved_usd<=0 OR p_day_usd IS NULL OR p_day_usd<0 OR
    p_hour_usd IS NULL OR p_hour_usd<0 OR p_minute_usd IS NULL OR p_minute_usd<0 THEN
  RAISE EXCEPTION 'invalid_budget';
 END IF;
 -- All capabilities share the deployment's provider budget; a capability cannot evade it.
 PERFORM pg_advisory_xact_lock(73692411);
 -- Automatic work reuses the newest manually-created generation too.
 SELECT * INTO r FROM driftread.ai_request_receipts
 WHERE logical_key=p_logical_key OR retry_parent_key=p_logical_key
 ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE;
 IF FOUND THEN
  IF r.status IN ('received','completed','failed') AND NOT (r.status='failed' AND r.error_code='provider_http_retryable' AND r.attempts<2) THEN RETURN jsonb_build_object('action','cached','receipt',to_jsonb(r)); END IF;
  IF r.status='pending' AND r.lease_expires_at>v_now THEN RETURN jsonb_build_object('action','blocked','code','request_in_progress','receipt_id',r.id); END IF;
  IF r.status='pending' THEN
   UPDATE driftread.ai_request_attempts SET status='unknown',error_code='lease_expired' WHERE token=r.active_token AND status='pending';
   UPDATE driftread.ai_request_receipts SET status='unknown',error_code='lease_expired',paused=(attempts>=2),updated_at=v_now WHERE id=r.id RETURNING * INTO r;
  END IF;
  IF r.paused OR r.attempts>=2 THEN
   RETURN jsonb_build_object('action','blocked','code','request_unknown_paused','receipt_id',r.id);
  END IF;
 END IF;
 -- Count reservations and settled costs in rolling windows, not reset-boundary counters.
 SELECT coalesce(sum(charged_usd) FILTER(WHERE started_at>v_now-interval '1 minute'),0) INTO v_used FROM driftread.ai_request_attempts WHERE started_at>v_now-interval '1 day';
 IF v_used+p_reserved_usd>p_minute_usd THEN RETURN jsonb_build_object('action','blocked','code','budget_exceeded'); END IF;
 SELECT coalesce(sum(charged_usd) FILTER(WHERE started_at>v_now-interval '1 hour'),0) INTO v_used FROM driftread.ai_request_attempts WHERE started_at>v_now-interval '1 day';
 IF v_used+p_reserved_usd>p_hour_usd THEN RETURN jsonb_build_object('action','blocked','code','budget_exceeded'); END IF;
 SELECT coalesce(sum(charged_usd),0) INTO v_used FROM driftread.ai_request_attempts WHERE started_at>v_now-interval '1 day';
 IF v_used+p_reserved_usd>p_day_usd THEN RETURN jsonb_build_object('action','blocked','code','budget_exceeded'); END IF;
 IF r.id IS NULL THEN
  INSERT INTO driftread.ai_request_receipts(logical_key,capability,model,input_hash,policy_hash,config_hash,created_at)
  VALUES(p_logical_key,p_capability,p_model,p_input_hash,p_policy_hash,p_config_hash,v_now) RETURNING * INTO r;
 END IF;
 t:=gen_random_uuid();
 UPDATE driftread.ai_request_receipts SET status='pending',attempts=attempts+1,active_token=t,lease_expires_at=v_now+interval '120 seconds',updated_at=v_now,error_code=NULL WHERE id=r.id RETURNING * INTO r;
 INSERT INTO driftread.ai_request_attempts(receipt_id,attempt,token,status,reserved_usd,charged_usd,started_at)
 VALUES(r.id,r.attempts,t,'pending',p_reserved_usd,p_reserved_usd,v_now);
 RETURN jsonb_build_object('action','send','receipt_id',r.id,'token',t,'attempts',r.attempts);
END $$;

-- An explicit admin retry may renew only a known terminal provider failure
-- or a completed result whose cached payload has been pruned.
-- The durable task retry key deduplicates queue retries; unknowns retain the
-- original receipt's two-send bound. Keep all earlier attempts/costs for billing.
CREATE OR REPLACE FUNCTION driftread.reserve_ai_retry(
 p_logical_key text,p_capability text,p_model text,p_input_hash text,p_policy_hash text,p_config_hash text,
 p_reserved_usd numeric,p_minute_usd numeric,p_hour_usd numeric,p_day_usd numeric,p_retry_key uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=driftread,pg_temp AS $$
DECLARE r driftread.ai_request_receipts; v_key text; outcome jsonb; v_receipt_id uuid;
BEGIN
 IF p_retry_key IS NULL THEN RAISE EXCEPTION 'invalid_retry_key'; END IF;
 PERFORM pg_advisory_xact_lock(73692411);
 v_key:=p_logical_key||':retry:'||p_retry_key::text;
 SELECT ar.* INTO r FROM driftread.ai_request_retry_bindings b JOIN driftread.ai_request_receipts ar ON ar.id=b.receipt_id
  WHERE b.retry_key=p_retry_key AND b.logical_key=p_logical_key FOR UPDATE OF ar;
 IF FOUND THEN v_key:=r.logical_key;
 ELSE
  SELECT * INTO r FROM driftread.ai_request_receipts WHERE logical_key=v_key FOR UPDATE;
 END IF;
 IF NOT FOUND THEN
  SELECT * INTO r FROM driftread.ai_request_receipts
   WHERE logical_key=p_logical_key OR retry_parent_key=p_logical_key
   ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE;
  IF NOT FOUND THEN v_key:=p_logical_key;
  ELSIF (r.status='failed' AND (r.error_code IN('provider_http_error','provider_invalid_response')
   OR (r.error_code='provider_http_retryable' AND r.attempts>=2)))
   -- A completed result pruned after 90 days is only repurchased on explicit retry.
   OR (r.status='completed' AND r.result IS NULL) THEN
   -- v_key remains the new explicit generation.
   NULL;
  ELSE v_key:=r.logical_key;
  END IF;
 END IF;
 outcome:=driftread.reserve_ai_request(v_key,p_capability,p_model,p_input_hash,p_policy_hash,p_config_hash,
  p_reserved_usd,p_minute_usd,p_hour_usd,p_day_usd);
 IF outcome->>'action'='send' AND v_key<>p_logical_key THEN
  UPDATE driftread.ai_request_receipts SET retry_parent_key=p_logical_key
   WHERE id=(outcome->>'receipt_id')::uuid;
 END IF;
 v_receipt_id:=coalesce(outcome->>'receipt_id',outcome->'receipt'->>'id')::uuid;
 IF v_receipt_id IS NOT NULL THEN
  INSERT INTO driftread.ai_request_retry_bindings(retry_key,logical_key,receipt_id)
   VALUES(p_retry_key,p_logical_key,v_receipt_id) ON CONFLICT(retry_key) DO NOTHING;
 END IF;
 RETURN outcome;
END $$;
REVOKE ALL ON FUNCTION driftread.reserve_ai_retry(text,text,text,text,text,text,numeric,numeric,numeric,numeric,uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.reserve_ai_retry(text,text,text,text,text,text,numeric,numeric,numeric,numeric,uuid) TO service_role;

-- Raw bytes are committed before Python attempts JSON parsing. No untrusted response text in errors.
CREATE OR REPLACE FUNCTION driftread.receive_ai_response(p_id uuid,p_token uuid,p_raw text,p_http_status integer,p_latency_ms integer)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=driftread,pg_temp AS $$
BEGIN
 UPDATE driftread.ai_request_receipts SET status='received',updated_at=now() WHERE id=p_id AND active_token=p_token AND status='pending';
 IF NOT FOUND THEN RETURN false; END IF;
 UPDATE driftread.ai_request_attempts SET status='received',raw_response=p_raw,http_status=p_http_status,latency_ms=p_latency_ms,received_at=now() WHERE receipt_id=p_id AND token=p_token;
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION driftread.finish_ai_request(p_id uuid,p_token uuid,p_status text,p_result jsonb DEFAULT NULL,p_error_code text DEFAULT NULL,p_usage jsonb DEFAULT NULL,p_actual_usd numeric DEFAULT NULL,p_actual_model text DEFAULT NULL)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=driftread,pg_temp AS $$
DECLARE v_attempt integer;
BEGIN
 IF p_status NOT IN ('completed','failed','unknown') THEN RAISE EXCEPTION 'invalid_status'; END IF;
 IF p_actual_usd IS NOT NULL AND p_actual_usd<0 THEN RAISE EXCEPTION 'invalid_cost'; END IF;
 UPDATE driftread.ai_request_receipts SET status=p_status,result=p_result,error_code=p_error_code,actual_model=p_actual_model,
 paused=(p_status='unknown' AND attempts>=2),lease_expires_at=NULL,updated_at=now()
 WHERE id=p_id AND active_token=p_token AND status IN ('pending','received') RETURNING attempts INTO v_attempt;
 IF NOT FOUND THEN RETURN false; END IF;
 UPDATE driftread.ai_request_attempts SET status=p_status,error_code=p_error_code,usage=p_usage,
 charged_usd=coalesce(p_actual_usd,charged_usd) WHERE receipt_id=p_id AND token=p_token;
 RETURN true;
END $$;

-- Bounded payload retention: raw responses 30d; typed result cache 90d.
-- Compact logical identity is retained so expiration never silently repurchases a response.
CREATE OR REPLACE FUNCTION driftread.prune_ai_receipts(p_limit integer DEFAULT 100)
RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path=driftread,pg_temp AS $$
DECLARE n integer;
BEGIN
 UPDATE driftread.ai_request_attempts SET raw_response=NULL WHERE id IN
 (SELECT id FROM driftread.ai_request_attempts WHERE raw_response IS NOT NULL AND started_at<now()-interval '30 days' ORDER BY started_at LIMIT greatest(1,least(p_limit,1000)));
 UPDATE driftread.ai_request_receipts SET result=NULL WHERE id IN
 (SELECT id FROM driftread.ai_request_receipts WHERE result IS NOT NULL AND status IN ('completed','failed') AND updated_at<now()-interval '90 days' ORDER BY updated_at LIMIT greatest(1,least(p_limit,1000)));
 GET DIAGNOSTICS n=ROW_COUNT; RETURN n;
END $$;
REVOKE ALL ON FUNCTION driftread.reserve_ai_request(text,text,text,text,text,text,numeric,numeric,numeric,numeric),driftread.receive_ai_response(uuid,uuid,text,integer,integer),driftread.finish_ai_request(uuid,uuid,text,jsonb,text,jsonb,numeric,text),driftread.prune_ai_receipts(integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.reserve_ai_request(text,text,text,text,text,text,numeric,numeric,numeric,numeric),driftread.receive_ai_response(uuid,uuid,text,integer,integer),driftread.finish_ai_request(uuid,uuid,text,jsonb,text,jsonb,numeric,text),driftread.prune_ai_receipts(integer) TO service_role;
CREATE OR REPLACE FUNCTION driftread.ai_usage_summary(p_limit integer DEFAULT 50)
RETURNS jsonb LANGUAGE sql SECURITY INVOKER SET search_path=driftread,pg_temp AS $$
 SELECT jsonb_build_object(
  'last_24h', (SELECT jsonb_build_object('sends',count(*),'charged_usd',coalesce(sum(charged_usd),0),'reserved_usd',coalesce(sum(reserved_usd),0)) FROM ai_request_attempts WHERE started_at>now()-interval '1 day'),
  'statuses', (SELECT coalesce(jsonb_object_agg(status,n),'{}') FROM (SELECT status,count(*) n FROM ai_request_receipts GROUP BY status) counts),
  'unknown_paused', (SELECT count(*) FROM ai_request_receipts WHERE status='unknown' AND paused),
  'recent_requests', (SELECT coalesce(jsonb_agg(to_jsonb(rows)),'[]') FROM
   (SELECT id,capability,model,actual_model,status,attempts,paused,error_code,created_at,updated_at FROM ai_request_receipts ORDER BY created_at DESC LIMIT greatest(1,least(p_limit,100))) rows)
 )
$$;
REVOKE ALL ON FUNCTION driftread.ai_usage_summary(integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.ai_usage_summary(integer) TO service_role;
NOTIFY pgrst, 'reload schema';
