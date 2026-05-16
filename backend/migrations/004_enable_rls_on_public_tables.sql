-- Enable Row Level Security on public-facing tables.
-- feeds and articles are world-readable (anyone can browse the RSS catalog),
-- but writes are gated to service_role (the backend connects with service_role
-- key and performs its own admin authorization via X-API-Key).

ALTER TABLE feeds    ENABLE ROW LEVEL SECURITY;
ALTER TABLE articles ENABLE ROW LEVEL SECURITY;

-- Public read access (anon + authenticated). Archived feeds are still readable;
-- application-level filters in the API exclude them where appropriate.
CREATE POLICY feeds_public_read ON feeds
  FOR SELECT TO anon, authenticated USING (TRUE);

CREATE POLICY articles_public_read ON articles
  FOR SELECT TO anon, authenticated USING (TRUE);

-- No INSERT/UPDATE/DELETE policies for anon/authenticated:
-- service_role bypasses RLS, so backend admin endpoints continue to work;
-- direct anon/authenticated writes are rejected by default.
