# Driftread Browser Extension

偵測網站 RSS／Atom，並閱讀個人最近文章摘要、離線排隊已讀／收藏／稍後讀操作。

## 安裝與連線

1. 在 Chrome／Edge／Brave 的 `chrome://extensions/` 開啟開發人員模式，載入未封裝的 `extension/`。
2. 開啟擴充選項，輸入 API URL（例如 `https://driftread.example.com/api`）與目前登入永久使用者的 Supabase access token。
3. 授予該 API 網域權限；API 必須 HTTPS，本機 localhost／127.0.0.1 開發除外。
4. 按「連線」，取得初始快取；token 到期後重新連線。不要填 Admin API Key 或 service_role key。

選項連線會移除舊版同步儲存的 admin credentials。token 僅留在 session storage；關閉 browser session 後需重新連線。斷線會清除本機個人快取。帳號切換及延遲連線回應受到 connectionId／generation 保護。popup 與 options 的個人快取、queue、連線及清除操作都由 background service worker 的單一佇列序列化：在佇列內確認 connectionId，等待 storage 寫入完成才處理下一項，避免 popup 延遲寫入在斷線後恢復私人資料。連線 intent 保存在 session storage，service worker 在等待候選回應時重啟仍可完成連線；較新的候選會取消舊候選的延遲快取 commit。只接受本擴充 popup／options 頁面要求，content script 無法變更私人儲存。儲存失敗會顯示錯誤，不會回報連線或離線操作已成功。

browser 重啟後先開 popup 不會刪除待同步操作；未連線或摘要超過 24 小時時不顯示私人文章，保留 queue 等待同帳號重新連線並同步。

「連線」會先用候選 API／token 取得初始 snapshot，成功後才替換目前連線；錯誤 token、拒絕權限或網路失敗保留原連線、快取與待同步操作。同一 API／帳號重新連線保留 pending queue，接著在 popup 按「同步」確認權限並重放；成功切換帳號或 API 則使用新帳號的空 queue。可按「中斷連線」主動清除保留資料。

## 使用

- 網頁偵測到 feed 後，在 popup 點「加入 Driftread」透過個人 API 匯入。
- popup 的「同步」載入最近 100 篇摘要，不保存正文；快取有效期 24 小時。
- 已讀／收藏／稍後讀操作先在本機 coalesce 成期望狀態；重新連線按同步時先確認權限，再用原帳號 token 重放，最後更新快照。
- 網路失敗保留待同步操作；401／403 清除失效帳號，已失權／刪除文章的操作會剔除，404／409 不會阻止其餘同步。

同步使用有界 replacement snapshot，不提供完整歷史增量下載；撤權在下次同步或快取過期後反映。詳細 trade-off 與 MCP／daily 出口見 [個人閱讀出口](../docs/CONSUMPTION_SURFACES.md)。

## 驗證

`node --test extension/tests/*.test.js` 執行 cache、queue、帳號切換、options lifecycle、可見回饋、撤權與重放測試。獨立 popup／options／background VM 共用實際 message listener 與 storage，驗證延遲 pull、重放確認、離線按鈕寫入與斷線／切帳號競跑，以及 service worker 重啟、儲存拒絕及 sender 限制。Chrome 實際 UI／載入流程需在瀏覽器驗證；Node 不驗證 browser permission 對話框本身。

`icons/` 可放 icon-16.png、icon-32.png、icon-128.png；缺少時 Chrome 使用預設圖示。
