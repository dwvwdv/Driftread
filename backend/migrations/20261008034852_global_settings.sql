-- Shared, typed application settings. Runtime secrets remain in deployment env.
CREATE TABLE IF NOT EXISTS driftread.app_settings (
 key text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_.-]{0,79}$'),
 value jsonb NOT NULL CHECK (jsonb_typeof(value) = 'object'),
 version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
 updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE driftread.app_settings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.app_settings FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON driftread.app_settings TO service_role;

CREATE OR REPLACE FUNCTION driftread.bump_app_setting_version()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.key IS DISTINCT FROM OLD.key THEN RAISE EXCEPTION 'setting key is immutable'; END IF;
 NEW.version:=OLD.version+1;
 NEW.updated_at:=clock_timestamp();
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS app_settings_version ON driftread.app_settings;
CREATE TRIGGER app_settings_version BEFORE UPDATE ON driftread.app_settings
 FOR EACH ROW EXECUTE FUNCTION driftread.bump_app_setting_version();
REVOKE ALL ON FUNCTION driftread.bump_app_setting_version() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.bump_app_setting_version() TO service_role;

CREATE OR REPLACE FUNCTION driftread.save_app_setting(p_key text,p_value jsonb,p_expected_version bigint,p_seeds jsonb DEFAULT '[]'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE saved jsonb; seed jsonb;
BEGIN
 IF p_key IS NULL OR p_key !~ '^[a-z][a-z0-9_.-]{0,79}$'
 OR jsonb_typeof(p_value) IS DISTINCT FROM 'object' OR octet_length(p_value::text)>1048576
 OR p_expected_version IS NULL OR p_expected_version<0
 OR jsonb_typeof(p_seeds) IS DISTINCT FROM 'array' OR jsonb_array_length(p_seeds)>200 THEN
  RAISE EXCEPTION 'invalid settings parameters';
 END IF;
 -- Validation before any mutation; the API also applies SSRF and typed registry
 -- checks. Only trusted backend callers may invoke this transaction.
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_seeds) s WHERE
  jsonb_typeof(s) IS DISTINCT FROM 'object' OR coalesce(s->>'host','') !~ '^[a-z0-9][a-z0-9.-]*\.[a-z0-9.-]+$'
  OR length(s->>'host')>253 OR coalesce(s->>'url','') !~ '^https?://' OR length(s->>'url')>2048) THEN
  RAISE EXCEPTION 'invalid settings seeds';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('driftread:settings-save',0));
 IF p_expected_version=0 THEN
  INSERT INTO driftread.app_settings AS s(key,value) VALUES(p_key,p_value)
   ON CONFLICT(key) DO NOTHING RETURNING to_jsonb(s) INTO saved;
 ELSE
  UPDATE driftread.app_settings AS s SET value=p_value
   WHERE s.key=p_key AND s.version=p_expected_version RETURNING to_jsonb(s) INTO saved;
 END IF;
 IF saved IS NULL THEN RETURN NULL; END IF;
 FOR seed IN SELECT value FROM jsonb_array_elements(p_seeds) LOOP
  -- Preserve existing state, including host-level rejection/robots decisions.
  -- Do not fabricate referral evidence or retag organic targets as seeds.
  IF NOT EXISTS(SELECT 1 FROM driftread.discovery_targets t WHERE t.host=seed->>'host') THEN
   INSERT INTO driftread.discovery_targets(url,host,source)
    VALUES(seed->>'url',seed->>'host','seed') ON CONFLICT(url) DO NOTHING;
  END IF;
 END LOOP;
 RETURN saved;
END $$;
REVOKE ALL ON FUNCTION driftread.save_app_setting(text,jsonb,bigint,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.save_app_setting(text,jsonb,bigint,jsonb) TO service_role;

INSERT INTO driftread.app_settings(key,value) VALUES('discovery.profiles', $defaults${"profiles": [{"id": "tech", "name": "科技", "language": "zh-tw", "category": "technology", "enabled": true, "quota": 1, "seed_urls": ["https://technews.tw/", "https://www.inside.com.tw/"]}, {"id": "business", "name": "商業", "language": "zh-tw", "category": "business", "enabled": true, "quota": 1, "seed_urls": ["https://www.managertoday.com.tw/"]}, {"id": "science", "name": "科學", "language": "zh-tw", "category": "science", "enabled": true, "quota": 1, "seed_urls": ["https://pansci.asia/"]}, {"id": "society", "name": "社會", "language": "zh-tw", "category": "society", "enabled": true, "quota": 1, "seed_urls": ["https://www.twreporter.org/"]}, {"id": "culture", "name": "文化", "language": "zh-tw", "category": "culture", "enabled": true, "quota": 1, "seed_urls": ["https://www.openbook.org.tw/", "https://storystudio.tw/"]}, {"id": "environment", "name": "環境", "language": "zh-tw", "category": "environment", "enabled": true, "quota": 1, "seed_urls": ["https://e-info.org.tw/"]}, {"id": "life", "name": "生活", "language": "zh-tw", "category": "life", "enabled": true, "quota": 1, "seed_urls": ["https://icook.tw/"]}]}$defaults$::jsonb) ON CONFLICT(key) DO NOTHING;
NOTIFY pgrst,'reload schema';
