-- Feed 完整文章列表：GET /feeds/{feed_id}/articles 從 offset 分頁改成 keyset（cursor）分頁，
-- 並在呼叫者已登入時一併帶出每篇文章的已讀／收藏狀態（TODO.md「Feed 完整文章列表」四項）。
--
-- 為什麼要換成 DB function 而不是繼續在 routers/articles.py 用 PostgREST query builder：
-- 舊版只用 `.order("published_at", desc=True)` 排序，`published_at` 為 NULL 的文章會落進
-- Postgres `DESC` 預設的 `NULLS FIRST`，卡在最前面；而且是 offset（`.range()`）分頁，新文章
-- 抓進來、往前插隊時，翻頁會重複或漏掉文章——同 migration 015 替 list_reading_stream 解決過的
-- 問題。這裡直接沿用同一套排序鍵與 cursor 形狀：COALESCE(published_at, fetched_at) DESC,
-- id DESC，未解析出發佈日期的文章退回抓取時間，也讓 cursor 比較不必特別處理 NULL。
--
-- 這個 function 服務公開端點（feed 詳情頁任何人都能看文章列表），所以 p_user_id 是可選的：
-- 帶 NULL 時兩個 LEFT JOIN 的 `x.user_id = p_user_id` 條件永遠不成立，is_read／is_bookmarked
-- 自然全部是 false，不需要另外的分支處理未登入情境。
--
-- 索引沿用 migration 015 的 `articles_feed_id_sort_at_idx`
-- （feed_id, COALESCE(published_at, fetched_at) DESC, id DESC）——與這裡的排序鍵完全一致，
-- 不需要新增索引。
CREATE OR REPLACE FUNCTION driftread.list_feed_articles(
  p_feed_id         uuid,
  p_user_id         uuid DEFAULT NULL,
  p_cursor_sort_at  timestamptz DEFAULT NULL,
  p_cursor_id       uuid DEFAULT NULL,
  p_limit           int DEFAULT 20
)
RETURNS TABLE(
  id            uuid,
  feed_id       uuid,
  title         text,
  url           text,
  summary       text,
  author        text,
  published_at  timestamptz,
  fetched_at    timestamptz,
  is_read       boolean,
  is_bookmarked boolean
)
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
  SELECT
    a.id, a.feed_id, a.title, a.url, a.summary, a.author,
    a.published_at, a.fetched_at,
    (r.article_id IS NOT NULL) AS is_read,
    (b.article_id IS NOT NULL) AS is_bookmarked
  FROM driftread.articles a
  LEFT JOIN driftread.user_article_reads r
    ON r.article_id = a.id AND r.user_id = p_user_id
  LEFT JOIN driftread.user_bookmarks b
    ON b.article_id = a.id AND b.user_id = p_user_id AND b.bookmark_type = 'favorite'
  WHERE a.feed_id = p_feed_id
    AND (
      p_cursor_sort_at IS NULL
      OR COALESCE(a.published_at, a.fetched_at) < p_cursor_sort_at
      OR (COALESCE(a.published_at, a.fetched_at) = p_cursor_sort_at AND a.id < p_cursor_id)
    )
  ORDER BY COALESCE(a.published_at, a.fetched_at) DESC, a.id DESC
  -- Same defense-in-depth clamp as list_reading_stream / sample_feed_candidates.
  LIMIT LEAST(GREATEST(p_limit, 1), 100)
$$;

REVOKE ALL ON FUNCTION driftread.list_feed_articles(uuid, uuid, timestamptz, uuid, int)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.list_feed_articles(uuid, uuid, timestamptz, uuid, int)
  TO service_role;
