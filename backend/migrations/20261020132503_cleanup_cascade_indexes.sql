-- Feed cleanup cascades/SET NULL must locate children by their FK, including
-- article user relations whose composite primary key starts with user_id.
CREATE INDEX IF NOT EXISTS discovery_targets_source_feed_idx
 ON driftread.discovery_targets(source_feed_id);
CREATE INDEX IF NOT EXISTS discovery_candidates_feed_idx
 ON driftread.discovery_candidates(feed_id);
CREATE INDEX IF NOT EXISTS article_discoveries_feed_idx
 ON driftread.article_discoveries(feed_id);
CREATE INDEX IF NOT EXISTS user_feeds_feed_idx
 ON driftread.user_feeds(feed_id);
CREATE INDEX IF NOT EXISTS user_article_reads_article_idx
 ON driftread.user_article_reads(article_id);
CREATE INDEX IF NOT EXISTS user_bookmarks_article_idx
 ON driftread.user_bookmarks(article_id);
CREATE INDEX IF NOT EXISTS user_feed_feedback_feed_idx
 ON driftread.user_feed_feedback(feed_id);

NOTIFY pgrst, 'reload schema';
