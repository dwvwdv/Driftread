# Driftread Browser Extension

一鍵將任何網站的 RSS / Atom feed 加入你的 Driftread 資料庫。

## 安裝（開發版）

1. 打開 Chrome / Edge / Brave，前往 `chrome://extensions/`
2. 啟用右上角「開發人員模式」
3. 點「載入未封裝項目」，選擇本資料夾 (`extension/`)
4. 圖示出現在工具列後，點右鍵 → 「選項」設定：
   - **API URL**：你的 Driftread 後端，例如 `https://driftread-api.workers.dev/api`
   - **Admin API Key**：與後端 `ADMIN_API_KEY` 環境變數相同的值

## 使用

- 瀏覽到任意有 RSS 的網站 → 圖示徽章會顯示偵測到的 feed 數量
- 點圖示 → 看到所有偵測到的 feed → 點「加入 Driftread」一鍵匯入

## 缺少圖示

`icons/` 目錄留給你放入：
- `icon-16.png` (16×16)
- `icon-32.png` (32×32)
- `icon-128.png` (128×128)

沒有圖示也可以載入，Chrome 會用預設灰底。
