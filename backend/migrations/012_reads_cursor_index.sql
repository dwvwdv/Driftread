-- GET /me/reads used to select every user_article_reads row for the caller
-- unbounded. A heavy reader can accumulate thousands of rows, so the route
-- now paginates by (read_at, article_id) keyset instead of returning
-- everything in one response. The existing user_article_reads_user_idx only
-- covers the `user_id` equality filter; this composite index lets the
-- ORDER BY read_at DESC, article_id DESC plus keyset filter be satisfied by
-- a single index scan instead of an index scan plus a separate sort.
CREATE INDEX IF NOT EXISTS user_article_reads_user_read_idx
  ON driftread.user_article_reads (user_id, read_at DESC, article_id DESC);
