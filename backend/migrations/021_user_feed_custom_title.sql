-- TODO.md P2「資料夾與來源控制」：支援每個來源的使用者自訂名稱。訂閱關係本身
-- （driftread.user_feeds）已經是「這個使用者與這個 feed」的唯一列，自訂名稱是這段關係的
-- 屬性、不是 feed 本身的屬性——故意不寫回 feeds.title，那是所有訂閱者共用的目錄名稱，
-- 一個人改了顯示名稱不該影響其他訂閱者或未訂閱者看到的名稱。
--
-- NULL 代表「沒有自訂名稱，顯示 feed 原本的 title」，不是空字串——區分「使用者刻意清除
-- 自訂名稱」與「自訂名稱本身是空字串」沒有意義，兩者行為應該一致，由呼叫端
-- （routers/me.py）把空白字串一併正規化成 NULL 再寫入，這裡的 CHECK 只擋長度。
ALTER TABLE driftread.user_feeds
  ADD COLUMN IF NOT EXISTS custom_title TEXT
    CHECK (custom_title IS NULL OR char_length(custom_title) <= 200);
