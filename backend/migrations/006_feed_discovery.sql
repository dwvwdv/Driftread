-- Autonomous (proactive) feed discovery.
--
-- Until now "discovery" was purely reactive: a user pasted a URL into
-- POST /api/discover and services/feed_discovery.py::discover_feeds() probed it.
-- Nothing let the platform find new sources on its own, which is why the feed
-- catalog shipped empty. This migration adds the state the discovery loop needs.
--
-- Four moving parts:
--   1. a harvest cursor on `feeds`, mirroring 005's next_fetch_at pattern, so
--      "which feeds are due to be mined for outbound links" is the same shape of
--      due query as the refresh queue — no join, one partial index;
--   2. `discovery_targets`, the crawl frontier, plus `discovery_target_referrers`,
--      the ledger that makes "how many DISTINCT existing feeds link here" exact
--      and idempotent under re-harvesting;
--   3. `discovery_candidates`, the review queue of feed URLs the probe actually
--      fetched and parsed;
--   4. `discovery_sources`, the admin-maintained list of directory / index pages
--      to mine (awesome-lists, OPML directories).
--
-- All four new tables are back-office crawl state. Unlike feeds / articles in
-- migration 004 they get RLS with **no** anon/authenticated policies: only the
-- service_role backend can read or write them, so even a leaked anon key can't
-- enumerate the frontier (who-links-to-whom is scraping-sensitive data).
--
-- Everything here is idempotent: IF NOT EXISTS, CREATE OR REPLACE FUNCTION, and
-- DO-guards around CREATE TRIGGER (which has no IF NOT EXISTS). migrate.py
-- applies the file in one transaction and a raised migration aborts API startup
-- (main.py's lifespan calls run_migrations before serving), so re-running this
-- against a hand-patched database must never error.

-- ═════════════════════════════════════════════ 1. harvest cursor on feeds

ALTER TABLE feeds
  ADD COLUMN IF NOT EXISTS last_harvested_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS next_harvest_at   TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Drives services/link_harvest.py::select_due_harvest_feeds, which always
-- filters archived_at IS NULL — same reasoning as feeds_next_fetch_at_idx in
-- 005: keep archived feeds out of the index entirely, not just out of the
-- result set.
CREATE INDEX IF NOT EXISTS feeds_next_harvest_at_idx
  ON feeds (next_harvest_at)
  WHERE archived_at IS NULL;

-- DEFAULT NOW() makes every existing feed immediately harvest-due. That is
-- intended and safe: FEED_DISCOVERY_HARVEST_BATCH_SIZE (10) bounds each cycle,
-- and mining articles.content makes zero network requests.

-- ═════════════════════════════════════════════ 2. the crawl frontier

CREATE TABLE IF NOT EXISTS discovery_targets (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- UNIQUE on url, not host: an OPML directory can contribute several feed URLs
  -- on one host. The "probe a host only once" rule applies only to link mining
  -- (article_link / blogroll) and lives in Python — see link_harvest.HostIndex —
  -- so it can be relaxed per source without touching the schema.
  url                  TEXT NOT NULL UNIQUE,
  -- Normalized by services/link_harvest.py::normalize_host: lowercase, no
  -- userinfo, no port, leading "www." stripped.
  host                 TEXT NOT NULL,
  source               TEXT NOT NULL DEFAULT 'article_link'
                         CHECK (source IN ('article_link', 'blogroll', 'seed',
                                           'directory', 'opml')),
  -- One representative referrer for display; the full set lives in
  -- discovery_target_referrers. SET NULL so deleting a feed doesn't delete
  -- frontier history.
  source_feed_id       UUID REFERENCES feeds(id) ON DELETE SET NULL,
  -- Denormalized COUNT of discovery_target_referrers, maintained by the trigger
  -- below. The primary quality signal.
  referring_feed_count INT NOT NULL DEFAULT 0,
  status               TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'done', 'blocked',
                                           'exhausted', 'rejected')),
  --   pending   : due (or waiting on next_probe_at) — the only status probed
  --   done      : probed, reachable; feeds_found may be 0 (site has no feed)
  --   blocked   : robots.txt disallow, or host matched the denylist
  --   exhausted : unreachable FEED_DISCOVERY_PROBE_MAX_ATTEMPTS times
  --   rejected  : an admin blocked this host; never re-queued, ever
  attempts             INT NOT NULL DEFAULT 0,
  feeds_found          INT NOT NULL DEFAULT 0,
  next_probe_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_probe_at        TIMESTAMPTZ,
  last_failure_reason  TEXT,                  -- truncated to 500 chars in Python
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Matches services/discovery_probe.py::select_due_targets exactly:
-- status = 'pending', next_probe_at <= now(),
-- ORDER BY referring_feed_count DESC, next_probe_at.
-- Partial on 'pending' so terminal rows (the long-term majority) leave the
-- index — the same trick as feeds_next_fetch_at_idx. The leading column is the
-- quality signal, so the crawler always spends its budget on the
-- best-evidenced hosts first.
CREATE INDEX IF NOT EXISTS discovery_targets_due_idx
  ON discovery_targets (referring_feed_count DESC, next_probe_at)
  WHERE status = 'pending';

-- link_harvest.build_host_index pages through every host to build its in-memory
-- dedupe set; the seed endpoint looks up single hosts.
CREATE INDEX IF NOT EXISTS discovery_targets_host_idx
  ON discovery_targets (host);

-- Admin list/stats filter by status.
CREATE INDEX IF NOT EXISTS discovery_targets_status_idx
  ON discovery_targets (status);

-- The distinct-referrer ledger. A plain counter column would double-count every
-- re-harvest of the same feed; a row per (target, feed) makes the count a
-- property of the data instead of of the write path.
CREATE TABLE IF NOT EXISTS discovery_target_referrers (
  target_id     UUID NOT NULL REFERENCES discovery_targets(id) ON DELETE CASCADE,
  feed_id       UUID NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (target_id, feed_id)
);

CREATE INDEX IF NOT EXISTS discovery_target_referrers_feed_idx
  ON discovery_target_referrers (feed_id);

-- ═════════════════════════════════════════════ 3. candidate review queue

CREATE TABLE IF NOT EXISTS discovery_candidates (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- SET NULL, not CASCADE: purging old frontier rows must not destroy review
  -- history (especially rejections).
  target_id            UUID REFERENCES discovery_targets(id) ON DELETE SET NULL,
  -- UNIQUE is the "never re-propose a rejected candidate" mechanism. The write
  -- path in services/discovery_candidates.py::record_candidates therefore
  -- INSERTs only URLs it has confirmed absent, and never upserts over status.
  feed_url             TEXT NOT NULL UNIQUE,
  title                TEXT,          -- untrusted third-party text; sanitized in Python
  website_url          TEXT,          -- untrusted; http/https enforced in Python
  source_host          TEXT,          -- denormalized from the target, for the review UI
  referring_feed_count INT NOT NULL DEFAULT 0,  -- kept in step by the trigger while pending
  status               TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'approved', 'rejected', 'imported')),
  --   approved is a real intermediate state: approval is recorded first, then a
  --   sweep promotes it into `feeds`. If the feeds write fails the approval
  --   isn't lost and the next cycle retries it.
  feed_id              UUID REFERENCES feeds(id) ON DELETE SET NULL,
  -- The category/tags the reviewer chose, stored with the approval rather than
  -- held in the request. Without these, a promotion that failed and got retried
  -- by promote_approved() next cycle would import the feed uncategorised and
  -- silently discard what the admin actually picked.
  approved_category    TEXT,
  approved_tags        TEXT[] NOT NULL DEFAULT '{}',
  review_note          TEXT,
  discovered_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  reviewed_at          TIMESTAMPTZ,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The review queue query: status = 'pending', ordered best-evidence-first.
CREATE INDEX IF NOT EXISTS discovery_candidates_review_idx
  ON discovery_candidates (referring_feed_count DESC, discovered_at DESC)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS discovery_candidates_status_idx
  ON discovery_candidates (status);

CREATE INDEX IF NOT EXISTS discovery_candidates_target_idx
  ON discovery_candidates (target_id);

-- ═════════════════════════════════════════════ 4. directory / index sources

CREATE TABLE IF NOT EXISTS discovery_sources (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  url                 TEXT NOT NULL UNIQUE,
  --   links_page : HTML page; every external <a href> becomes a frontier host
  --   opml       : OPML/XML; every outline/@xmlUrl becomes a feed-URL target
  kind                TEXT NOT NULL DEFAULT 'links_page'
                        CHECK (kind IN ('links_page', 'opml')),
  label               TEXT,
  enabled             BOOLEAN NOT NULL DEFAULT TRUE,
  interval_hours      INT NOT NULL DEFAULT 168,
  next_harvest_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_harvested_at   TIMESTAMPTZ,
  attempts            INT NOT NULL DEFAULT 0,
  last_failure_reason TEXT,
  targets_created     INT NOT NULL DEFAULT 0,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Matches services/directory_sources.py::select_due_sources.
CREATE INDEX IF NOT EXISTS discovery_sources_due_idx
  ON discovery_sources (next_harvest_at)
  WHERE enabled;

-- ═════════════════════════════════════════════ 5. referrer count maintenance

-- Recomputes (never increments) the denormalized count. That is what makes
-- re-harvesting the same feed a no-op: the ledger's PRIMARY KEY absorbs the
-- duplicate and the count is an absolute COUNT(*), so the signal can't inflate.
-- Also refreshes *pending* candidates so the auto-promote threshold reacts to
-- evidence that accumulated after the candidate was first discovered; already
-- reviewed rows are deliberately frozen.
CREATE OR REPLACE FUNCTION discovery_sync_referrer_count()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
  affected UUID := COALESCE(NEW.target_id, OLD.target_id);
  total    INT;
BEGIN
  SELECT COUNT(*) INTO total
    FROM discovery_target_referrers
   WHERE target_id = affected;

  UPDATE discovery_targets
     SET referring_feed_count = total
   WHERE id = affected
     AND referring_feed_count <> total;

  UPDATE discovery_candidates
     SET referring_feed_count = total
   WHERE target_id = affected
     AND status = 'pending'
     AND referring_feed_count <> total;

  RETURN NULL;  -- AFTER trigger: the return value is ignored
END;
$$;

-- Row-level on purpose: a statement-level trigger would need a transition
-- table, and a transition-table name can't be shared between the INSERT and
-- DELETE triggers without duplicating the function. One harvest batch inserts
-- at most FEED_DISCOVERY_HARVEST_MAX_LINKS_PER_FEED (200) referrer rows, so
-- this is at most 200 index-only counts of a handful of rows each.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgname = 'discovery_target_referrers_sync_count'
       AND tgrelid = 'discovery_target_referrers'::regclass
  ) THEN
    CREATE TRIGGER discovery_target_referrers_sync_count
      AFTER INSERT OR DELETE ON discovery_target_referrers
      FOR EACH ROW EXECUTE FUNCTION discovery_sync_referrer_count();
  END IF;
END $$;

-- ═════════════════════════════════════════════ 6. updated_at (reuses 001's fn)

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgname = 'discovery_targets_updated_at'
       AND tgrelid = 'discovery_targets'::regclass
  ) THEN
    CREATE TRIGGER discovery_targets_updated_at
      BEFORE UPDATE ON discovery_targets
      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgname = 'discovery_candidates_updated_at'
       AND tgrelid = 'discovery_candidates'::regclass
  ) THEN
    CREATE TRIGGER discovery_candidates_updated_at
      BEFORE UPDATE ON discovery_candidates
      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgname = 'discovery_sources_updated_at'
       AND tgrelid = 'discovery_sources'::regclass
  ) THEN
    CREATE TRIGGER discovery_sources_updated_at
      BEFORE UPDATE ON discovery_sources
      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
END $$;

-- ═════════════════════════════════════════════ 7. RLS: service_role only

ALTER TABLE discovery_targets          ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_target_referrers ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_candidates       ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_sources          ENABLE ROW LEVEL SECURITY;

-- Deliberately NO policies at all — not even SELECT. With RLS on and zero
-- policies, anon and authenticated see zero rows and can write nothing, while
-- service_role (which the backend uses, see database.py) bypasses RLS
-- entirely. Contrast migration 004, where feeds/articles get an explicit
-- public-read policy because they ARE the public catalog. If a future migration
-- adds any policy here, it must not be granted to anon.
