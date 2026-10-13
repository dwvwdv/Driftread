DO $$
DECLARE r jsonb; t uuid; receipt uuid;
BEGIN
 r:=driftread.reserve_ai_request('sql-fixture','source_review','jev-1.13.0','input','policy','config',0.001,0.1,0.5,1);
 IF r->>'action'<>'send' THEN RAISE EXCEPTION 'reservation failed'; END IF;
 receipt:=(r->>'receipt_id')::uuid; t:=(r->>'token')::uuid;
 IF NOT driftread.receive_ai_response(receipt,t,'{"answers":{}}',200,10) THEN RAISE EXCEPTION 'raw response failed'; END IF;
 IF NOT driftread.finish_ai_request(receipt,t,'completed','{"decision":"accept"}',NULL,'{"input_tokens":1}',0.000000042,'jev-1.13.0') THEN RAISE EXCEPTION 'completion failed'; END IF;
 r:=driftread.reserve_ai_request('sql-fixture','source_review','jev-1.13.0','input','policy','config',0.001,0,0,0);
 IF r->>'action'<>'cached' THEN RAISE EXCEPTION 'completed result must be cached even with spending disabled'; END IF;
END $$;
DO $$
DECLARE r jsonb; receipt uuid; t uuid;
BEGIN
 r:=driftread.reserve_ai_request('sql-transient','source_review','jev-1.13.0','input','policy','config',0.001,0.1,0.5,1);
 receipt:=(r->>'receipt_id')::uuid; t:=(r->>'token')::uuid;
 PERFORM driftread.receive_ai_response(receipt,t,'rate limited',429,1);
 PERFORM driftread.finish_ai_request(receipt,t,'failed',NULL,'provider_http_retryable');
 r:=driftread.reserve_ai_request('sql-transient','source_review','jev-1.13.0','input','policy','config',0.001,0.1,0.5,1);
 IF r->>'action'<>'send' OR (r->>'attempts')::integer<>2 THEN RAISE EXCEPTION 'transient second attempt missing'; END IF;
 t:=(r->>'token')::uuid;
 PERFORM driftread.receive_ai_response(receipt,t,'overloaded',503,1);
 PERFORM driftread.finish_ai_request(receipt,t,'failed',NULL,'provider_http_retryable');
 r:=driftread.reserve_ai_request('sql-transient','source_review','jev-1.13.0','input','policy','config',0.001,0.1,0.5,1);
 IF r->>'action'<>'cached' THEN RAISE EXCEPTION 'transient retries must stop after two sends'; END IF;
END $$;
