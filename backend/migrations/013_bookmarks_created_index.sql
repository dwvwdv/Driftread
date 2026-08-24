-- GET /me/bookmarks filters on (user_id, bookmark_type) and orders by
-- created_at DESC. The existing user_bookmarks_user_type_idx only covers the
-- two equality filters, so Postgres still needs a separate sort step for the
-- ORDER BY. This composite index lets the whole query be satisfied by a
-- single index scan, same reasoning as migration 012 for user_article_reads.
CREATE INDEX IF NOT EXISTS user_bookmarks_user_type_created_idx
  ON driftread.user_bookmarks (user_id, bookmark_type, created_at DESC);
