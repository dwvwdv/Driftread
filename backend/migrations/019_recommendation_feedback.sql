-- TODO.md P1「推薦回饋持久化」：目前「猜你喜歡」的喜歡／跳過只存在瀏覽器 localStorage
-- （frontend/src/app/services/recommendation.ts），換裝置或清除瀏覽器資料就會整個消失，
-- 且 routers/recommendations.py 完全不知道使用者過去的回饋，每次都要靠前端把 liked／disliked
-- 兩個 query string 帶上限 50 筆重新送一次。這裡補上伺服器端的回饋表，讓已登入使用者的回饋
-- 跨裝置生效，且推薦排序可以直接查表而不必依賴呼叫端老實回傳歷史。
--
-- 一個使用者對一個 Feed 只保留「目前的最新立場」（PRIMARY KEY (user_id, feed_id)，upsert
-- 覆蓋），不是逐筆事件記錄：喜歡完又按不喜歡，只需要知道現在的立場是不喜歡，不需要完整歷史，
-- 同 user_feeds／user_preferences 既有的單列狀態模式。
--
-- feedback_type 三種：
--   liked    — 正向訊號，弱於訂閱。
--   disliked — 明確負向訊號，長期排除且降權同類 category／tag（feed-detail 頁的「不喜歡」）。
--   skipped  — 「猜你喜歡」卡片式介面的「跳過」。TODO.md 明確要求這個訊號只做「短期降權」，
--              不等同 disliked 的明確不喜歡；短期窗口交給 routers/recommendations.py 讀取
--              時依 created_at 判斷是否還在有效期，過期的 skipped 列不再影響排序（但仍保留
--              在表裡，不需要額外的過期清除流程）。
CREATE TABLE IF NOT EXISTS driftread.user_feed_feedback (
  user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  feed_id       UUID NOT NULL REFERENCES driftread.feeds(id) ON DELETE CASCADE,
  feedback_type TEXT NOT NULL CHECK (feedback_type IN ('liked', 'disliked', 'skipped')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, feed_id)
);

-- GET /me/feed-feedback 的唯一查詢形狀是「這個使用者的全部回饋」；scoring 端讀取時同樣是
-- 整批依 user_id 撈出後在應用層依 feedback_type／created_at 分流，不需要另外的
-- (user_id, feedback_type) 複合 index。
CREATE INDEX IF NOT EXISTS user_feed_feedback_user_idx
  ON driftread.user_feed_feedback (user_id);

ALTER TABLE driftread.user_feed_feedback ENABLE ROW LEVEL SECURITY;

-- 同 migration 010 對其他 user_* 表的最終版寫法：init-plan 化的 auth.uid()，並排除
-- Supabase 匿名登入 session（`is_anonymous` claim）——這張表的資料只對「真的登入」的使用者
-- 有意義，訪客不該留下可被 RLS 允許讀寫的回饋列。
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
     WHERE schemaname = 'driftread' AND tablename = 'user_feed_feedback'
       AND policyname = 'user_feed_feedback_owner'
  ) THEN
    CREATE POLICY user_feed_feedback_owner ON driftread.user_feed_feedback
      FOR ALL TO authenticated
      USING (
        user_id = (SELECT auth.uid())
        AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
      )
      WITH CHECK (
        user_id = (SELECT auth.uid())
        AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
      );
  END IF;
END $$;

-- migration 010 REVOKE ALL 了 driftread schema 的預設權限給往後新建的表，所以這張新表要
-- 自己重新 GRANT 給 authenticated，同其他 user_* 表的既有作法（RLS 先擋列，這裡只開表級門）。
GRANT SELECT, INSERT, UPDATE, DELETE ON driftread.user_feed_feedback TO authenticated;
GRANT ALL ON driftread.user_feed_feedback TO service_role;
