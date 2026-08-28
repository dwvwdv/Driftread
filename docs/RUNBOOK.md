# 部署與回滾 Runbook

給要實際操作 `docker-compose.yml` 這份部署的人看的操作手冊，記錄「照什麼順序做」。
背景與技術細節（RLS 決策、schema 設計理由）在 `docs/FEATURES.md` 第 5 節與
`docs/SECURITY.md`；這份文件只管操作順序。

## 前提

- 三個服務（`api`、`worker`、`frontend`）都是同一組 image 建構出來的兩個 repo
  （`driftread-api`、`driftread-frontend`），由 `.github/workflows/{backend,frontend}.yml`
  推到 GHCR。
- **每個 image 都同時打上 `:latest` 與 `:sha-<commit sha>` 兩個 tag**（見下方「回滾」）。
- `api` 在 `main.py` 的 lifespan 裡開機時自動跑 `migrate.py::run_migrations()` 與
  `backfill.py::run_backfills()`，兩者都用同一把 PostgreSQL advisory lock
  （`migrate.py::acquire_migration_lock`）序列化，並各自把「跑過了嗎」記在
  `driftread._migrations`——多個 `api` replica 同時開機、或 `worker`／`api` 前後開機，
  不會重複套用或互相競跑。migration／backfill 因此**不需要**手動照順序一個個跑，
  `docker compose up -d` 開 `api` 就會自動處理，`worker` 的 `depends_on: api: condition:
  service_healthy` 保證它一定晚於 migration 完成才啟動。
- `frontend` 的 Supabase 設定（`SUPABASE_URL` / `SUPABASE_ANON_KEY`）是容器啟動時由
  `frontend/docker-entrypoint.d` 寫進 `env.js`，不是 build time 編進 JS bundle——換帳號、
  換 project 只需要改 `.env` 再 `docker compose up -d frontend`，不必重新 build image。

## 一般部署（沒有動 DB schema／RLS／Supabase Dashboard 設定）

1. `git pull`（或改 `docker-compose.yml` 指到想要的 tag，見下方「回滾」）。
2. `docker compose pull`
3. `docker compose up -d`
   - `api` 開機時自動跑新 migration（若有）與健康檢查；`worker`／`frontend` 都
     `depends_on: api: condition: service_healthy`，會自動等 `api` 準備好才起。
4. `docker compose logs -f api` 確認 `Applying migration: ...` / `Applied: ...`
   （若這次沒有新 migration 則不會出現）且沒有例外往外拋、健康檢查轉綠。

日常沒有 schema 變動時，這四步就是全部流程。

## 會動到 Supabase Dashboard 設定的部署（schema exposure／grant／RLS）

`backend/migrations/*.sql` 涵蓋得到的部分（建表、RLS policy、function 權限）都會在
`api` 開機時自動套用。**Supabase Dashboard 的 Data API「Exposed Schemas」清單不是**——
沒有程式碼路徑能改這項設定，純粹是 Dashboard 手動步驟（Settings → API → Exposed schemas）。

目前的狀態：`driftread` schema 的 RLS policy／grant 已經由 migration 010 / 011 就緒
（見 `docs/FEATURES.md` 第 5 節），但「Exposed Schemas 是否已加入 `driftread`」是
`TODO.md` Phase 0 仍列為待確認的一項——這件事沒辦法從程式碼驗證，只能到 Dashboard 親自看。
**在把這份 compose 部署指到一個新的 Supabase project 之前，先確認這項設定**，否則
`backend/database.py::get_client()` 的 scoped client（固定 `schema="driftread"`）對
PostgREST 的每一次查詢都會是空的 exposed-schema 錯誤，而不是慢慢才發現的資料缺失。

正確順序（新增一個會擴大 exposed schema 或改變 grant 的 migration 時）：

1. **先**確認／調整 Supabase Dashboard 的 Exposed Schemas 與必要的 `anon`／`authenticated`
   grant——這步永遠手動、永遠先做，因為新 migration 套用後如果 API 還沒被允許看到
   新的 schema／表，任何依賴它的請求都會直接壞掉，而不是優雅降級。
2. **再** deploy 帶著新 migration 的 `api` image（走上面「一般部署」四步）。
3. 部署後跑一次 Supabase Dashboard 的 Advisors 頁（Database → Advisors）與一次
   「新舊 backend 部署順序」的手動走查——`TODO.md` Phase 0 最後一項——確認沒有非預期的
   公開存取或缺 grant。

**相容性 view 的收尾**：migration 010 把 `_migrations` 從 `public` 搬進 `driftread` schema
時，保留了一個 `public._migrations` 的 `security_invoker` view 當作相容橋樑，避免舊版
（改用 `driftread` schema 之前的）backend 重啟時把它當空 table 誤建一份新的 ledger。
**這個 view 目前仍然保留著**（`TODO.md` Phase 0 明確記著這項尚未執行）。要移除它，前提是
「所有仍在跑的 backend 版本都已經是讀 `driftread._migrations` 的版本」——確認方式是看
`docker compose ps` / 部署紀錄，確定沒有任何舊版 image 還在跑，才手動下
`DROP VIEW public._migrations;`。順序反了（view 移除在舊版本淘汰之前）會讓還沒升級完的
舊版 backend 在下次重啟時把 ledger 誤判成空的，重新跑一次它以為沒跑過的舊 migration。

## 回滾

Image 只有兩種 tag：`latest`（永遠指向最後一次成功的 push）與
`sha-<40 字元 commit sha>`（每次 push 到 `main`/`master` 都會多一個，永久保留、不會被覆寫）。
`docker-compose.yml` 目前寫死 `:latest`，代表**沒有額外動作的情況下，回滾等於「把
壞掉的版本重新推一次」**——不是真的回到舊 image。要回滾到指定 commit：

1. 找出想回滾到的 commit sha（`git log` 或 GitHub Actions 該次 workflow run 的 commit）。
   `api`／`worker` 用同一個 image，只需要一個 sha；`frontend` 是另一個 repo 的 image，
   通常和 backend 同一個 sha 一起改，但兩邊各自最後一次改動的 commit 不一定相同，
   混用 sha 時留意這點。
2. 改 `docker-compose.yml` 裡 `api`、`worker`、`frontend` 三個 `image:` 欄位，把
   `:latest` 換成 `:sha-<sha>`，執行 `docker compose up -d`。
   回滾完之後要嘛把三個欄位改回 `:latest`，要嘛就放著——下一次正常部署會照 CI 推的
   `:latest` 覆寫回去。
3. **回滾只回滾應用程式碼，不會回滾資料庫 schema。** migration 是只進不退的——沒有
   `down` migration 機制（見 `backend/migrations/*.sql` 只有正向 SQL）。如果要回滾的目標
   commit 早於某個已經在生產環境套用過的 migration，**先確認**那個 migration 是否為
   純加法（新增 table／column／index，不刪東西）——純加法的話舊版程式碼通常能安全地
   忽略新欄位而正常運作；如果 migration 動過既有欄位的型別、刪過欄位，或改過 RLS
   policy，回滾應用程式碼前得先手動評估資料庫端要不要一起處理，這件事沒有自動化，
   純加法以外的 migration 回滾必須逐一個案判斷。
4. 回滾後同樣跑一次「一般部署」第 4 步的健康檢查與 log 確認。

## 環境變數檢查

新增／移除／修改環境變數的三處同步規則見 `CLAUDE.md`
（`.env.example` / `docker-compose.yml` / `scripts/gen_env.py`）。`scripts/gen_env.py`
本身只會檢查「必要變數是否為空」並補上有預設值的變數，不會幫你判斷該填什麼——`SUPABASE_URL`
／`SUPABASE_KEY`／`SUPABASE_ANON_KEY`／`DATABASE_URL`／`SUPABASE_JWT_SECRET` 五個一定要
手動從 Supabase Dashboard 填。
