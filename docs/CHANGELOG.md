# 變更紀錄（PR 逐筆）

本檔以 PR 為單位記錄 Driftread 的變更，**依合併時間由舊到新排序，新條目一律加在檔案最下方**。
「當前狀態」請看 [FEATURES.md](FEATURES.md)；安全加固細節看 [SECURITY.md](SECURITY.md)。

> 註：`master` 是本專案的預設分支（CI 同時接受 `main` / `master`）。

> **本檔只保留最近兩個十位段的階段（目前為階段三十一起）**；更早的階段依十位段原文歸檔在 [changelog-archive/](changelog-archive/)。
> 跨入新的十位段時（例如寫下第一個「階段五十一」），把最舊的那一段（屆時為三十一～四十）整段搬到
> `changelog-archive/stages-NN-NN.md` 並更新下方索引——**只搬不改**，歸檔內容就是當時的紀錄（相對連結補上 `../`）。
> 彙整某段期間的變更時若跨過本檔最舊的階段，要連同對應的歸檔檔一起讀。
> 撰寫規則（一個 PR 一個條目、review 修復併入同一條目）見 [AGENTS.md](../AGENTS.md) 的「Changelog 維護」。

## 歸檔索引

- [階段二十一～三十（訂閱與閱讀、部署 runbook、個人化隔離，2026-08-18 ~ 09-03）](changelog-archive/stages-21-30.md)
- [階段十一～二十（PR #25–#34 與 schema 隔離、runtime config，2026-07-30 ~ 08-17）](changelog-archive/stages-11-20.md)
- [階段一～十（PR #1–#24，2026-05-07 ~ 07-30）](changelog-archive/stages-01-10.md)

---

## 階段三十一：`ReadingStreamService` 補齊 pending-write 與並發 GET 的 reconciliation（2026-09-07）

- **背景**：PR #43 code review 時就記錄在 TODO.md 的已知缺口——`ReadingStreamService`
  只有 `_itemsGeneration`／`_countsGeneration` 兩個「較新請求蓋掉較舊請求」的計數器，沒有
  `SubscriptionService` 那套「這個 id 有 pending 寫入或已確認的較新寫入，GET 回來的舊快照不能
  贏」的 ticketing。當時故意留給後續 PR，因為都是窄視窗、只影響畫面顯示到下次 reload／filter
  變更為止，不影響伺服器端資料正確性。
- **改動**：`frontend/src/app/services/reading-stream.ts` 新增單一共用的 `version` 單調序號，
  每次 `beginFetch()`（GET 前）與每次寫入 commit 都各自抽一個 ticket，同
  `SubscriptionService` 的 `beginFetch`/`confirmWrite`/`asOf` 設計：
  1. `load()`/`loadMore()` 回應套用前先 `reconcileItems()`：id 若仍在 `_pending`（該篇文章自己
     的寫入還沒結束）就沿用回應前的舊值，而不是被可能落後的伺服器快照蓋掉；否則若
     `_confirmedReadAt` 記錄的確認時間點晚於這個 GET 的 `asOf`，改採確認值——GET 有可能是在
     那次確認送出「之前」就已經發出的。
  2. 未讀數改成「最後一次接受的 GET 快照（baseline）＋尚未確認被該快照涵蓋的本地 delta 清單」，
     `recomputeCounts()` 統一算出顯示值。GET 回應一律套用、更新 baseline 並把 ticket 早於等於
     這次 `asOf` 的 delta 修剪掉，不再整批丟棄——舊版的 `_countsGeneration` 在偵測到「這次回應
     比某個本地寫入舊」時會整包丟棄，包含第一次 `loadCounts()` 都還沒回來過的情況；那種情況下
     即使丟棄也照樣把 `countsLoaded` 設成 `true`，之後沒有任何機制重試，未讀數就永遠卡在
     clamp 過後的錯誤猜測值。
  3. `markAllReadInView` 現在也把批次的目標 id 一併登記進 `_pending`（並在 settle 後清除），
     `markRead`/`markUnread` 既有的 `isPending` guard 因此也會排除掉正在被批次處理的文章，
     反之亦然——避免同一篇文章被批次與單篇操作各自套用一次 optimistic count delta，重複計算
     同一次伺服器端變更。`markAllReadInScope` 的成功回呼比照排除仍 pending 的文章，不去覆蓋它
     正在進行中的單篇寫入。
- **測試**：`reading-stream.spec.ts` 新增四個案例，各自對應上面修掉的情境——`markRead` 在
  `loadCounts()` 第一次回來前提交，斷言最終仍會拿到真正的 baseline 而不是卡住；`load()` 在
  同一篇文章有 pending 寫入時保留樂觀值；`load()` 在寫入已確認、但 GET 是在確認之前發出時改採
  確認值；`markAllReadInView` 排除掉正有 `markUnread` pending 的文章。既有測試不需要改動斷言即
  可通過（唯一調整：移除已被新機制取代的 `_countsGeneration` 相關內部欄位，行為對外不變）。
- **本 sandbox 的已知限制**：`npm ci` 被 `registry.npmjs.org` 的 network egress allowlist 擋下
  （一個 `@angular/cli` 的間接依賴 `zod-to-json-schema`），與先前多個 PR 遇到的限制相同，無法在
  本機實際跑 `vitest`／production build。已用系統 `tsc`（`--ignoreConfig --noResolve`）過新增
  改動，並用專案的 `.prettierrc.json` 設定跑過 `prettier --check`；邏輯正確性以逐步手動追蹤三個
  新增測試案例的 ticket／delta 數值驗證，交給 CI 的 `frontend.yml` 實際跑過 `npm test` 驗證。

## 階段三十二：JWT 驗證改為支援 Supabase JWKS（ES256／RS256），保留 HS256 相容（2026-09-09）

- **背景**：TODO.md「Auth 與安全」批次最後兩個未完成項目——`backend/auth.py` 從一開始就只
  接受 HS256 shared secret（`SUPABASE_JWT_SECRET`）。Supabase 目前預設把新專案的 JWT signing
  key 改成非對稱（ES256，也支援 RS256），HS256 secret 仍可用但屬於逐步淘汰的舊路徑；只認
  HS256 意味著已經（或將要）輪替到新式 signing key 的專案會拿到完全驗證不了的 token。
- **改動**：`auth._verify_token` 從固定用一把密鑰改成先讀 token header 的 `alg`，再決定走哪條
  固定路徑——`alg=HS256` 一律用 `SUPABASE_JWT_SECRET`；`alg` 為 `ES256`／`RS256` 一律改用
  `jwt.PyJWKClient` 向 JWKS 端點（預設 `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`，可用
  新增的 `SUPABASE_JWKS_URL` 覆寫）取得對應 `kid` 的公鑰驗證；其餘 `alg` 一律 401。兩條路徑各自
  固定死對應的驗證方式與金鑰來源，不會因為 token header 而互相借用——不會重開「alg 混淆」
  （攻擊者把 header 的 `alg` 換成 HS256、拿公鑰當 HMAC secret 偽造簽章）這類洞。
  Signing key rotation／cache refresh 兩項直接交給 `PyJWKClient` 自己的機制，不另外手寫快取：
  `cache_keys=True` 快取取回的 JWKS 文件 `lifespan`（300）秒；快取裡找不到的 `kid` 會觸發一次
  無條件重新抓取，所以剛輪替的新 signing key 第一個帶新 `kid` 的 token 就能驗證，不必等快取
  過期。`requirements.txt` 的 `pyjwt` 依賴加上 `[crypto]` extra（帶入 `cryptography`）——原本
  只用 HS256 時不需要它，PyJWT 的非對稱演算法沒有這個套件會直接說「不支援該演算法」。
- **相容性**：沒有新增必填環境變數。`SUPABASE_URL` 本來就必填，JWKS 端點預設由它推導；
  `SUPABASE_JWKS_URL` 是選填覆寫，只有自架或非標準網域才需要。尚未輪替 signing key 的專案
  送來的 token 仍是 `alg=HS256`，行為與改動前完全相同。
- **測試**：`backend/tests/test_auth.py` 新增 `TestJwksVerification`——ES256／RS256 token 經
  JWKS 驗證成功、匿名使用者仍被拒、不在 JWKS 裡的金鑰（含刻意選一個不存在的 `kid`）與缺
  `kid` 的 token 都要 401、輪替情境（換一把新 `kid` 簽的 token，只在快取沒有該 `kid` 時觸發
  「恰好一次」重新抓取，不是每次都打 JWKS 端點）、JWKS URL 推導與覆寫、`SUPABASE_URL` 未設定
  時的非對稱驗證回退成 500。既有 HS256 案例不變，額外補了不支援的 `alg`（`none`）必須 401。
- **本 sandbox 的已知限制**：`pip`／`uv` 的 PyPI index 被 network egress allowlist 擋下，
  與先前多輪修法相同的既有限制，無法直接 `pip install -r requirements.txt` 跑專案的
  `pytest`。改用 `python3 -m py_compile` 驗證語法、`ruff check` 過 lint；額外用一個獨立
  venv（`uv venv` + 從 uv 的本地 wheel cache 離線裝 `cryptography`／`cffi`，並沿用系統既有的
  `PyJWT` 套件目錄）搭配一個只實作 `HTTPException`／`status`／`Header` 三個名字的最小 fastapi
  替身模組，直接呼叫 `auth._verify_token()`（不經過完整的 FastAPI app）逐一手動重現上面測試
  案例涵蓋的每個情境並斷言結果——HS256 correct／wrong secret、匿名拒絕、`alg=none` 拒絕、
  ES256／RS256 成功、輪替時恰好一次重抓、錯誤金鑰拒絕、缺 `kid` 拒絕、JWKS URL 推導與覆寫、
  缺 `SUPABASE_URL` 時 500，全部通過。這只驗證了 `auth.py` 本身的邏輯，FastAPI route 與
  依賴注入的接線交給 CI 的 `backend.yml` 實際跑 `pytest`。
- 對應文件更新：`TODO.md`（「JWT 驗證由只接受 HS256...」與「支援 signing key rotation 與 JWKS
  cache refresh」兩項打勾）、`docs/SECURITY.md`（新增 #31）、`README.md` 與 `.env.example`
  （新增 `SUPABASE_JWKS_URL` 說明）。
- 對應文件更新：`TODO.md`（「ReadingStreamService 補齊 pending-write...」打勾並補上機制說明）。
- **PR review 修正第一輪（Codex，3 個 P2，均證實為真）**：
  1. **未讀數的 ticket 修剪條件用錯了比較對象**：`loadCounts()` 接受一個新 baseline 時，原本用
     「delta 的 commit ticket 是否 ≤ 這次 GET 的 `asOf`」決定要不要修剪掉這筆 delta——但
     commit ticket 只記錄「client 端何時做出樂觀猜測」，不代表伺服器當時已經處理完那次寫入。
     一個仍在 pending（尚未拿到伺服器回應）的寫入，即使它的 ticket 早於某次 GET 的 `asOf`，
     那次 GET 抓到的也可能是寫入生效「之前」的伺服器狀態——照舊邏輯會把這筆 delta 誤判成
     「已經反映在快照裡」而修剪掉，實際上該篇文章的寫入之後才確認成功，未讀數會憑空少算一次
     直到下次真正刷新。修法：把 delta 儲存結構從「ticket 陣列」改成「依 article id 存淨值的
     Map」（`_countDeltasByArticle`，同一篇文章的多次調整直接互相抵銷，不留兩筆），
     `recomputeCounts()` 判斷「這筆 delta 現在還要不要疊加」的依據改成同 `reconcileItems()`
     一致的規則：這篇文章若仍是 `_pending`，無論 ticket 為何一律疊加（寫入還沒有結果，任何
     baseline 都不能假設已經反映它）；只有寫入已確認（`_confirmedReadAt` 有記錄）時，才拿
     那次「確認」的 ticket 去跟 baseline 的 `asOf` 比。
  2. **「較舊請求被較新請求蓋掉」只認已經套用的 baseline，沒認已經發出的請求**：`loadCounts()`
     的 error handler 原本用「這次失敗的 `asOf` 是否早於目前 baseline 的 `asOf`」判斷要不要
     忽略這個失敗——但如果兩個 `loadCounts()` 同時在飛、較舊的那個先失敗、較新的那個都還沒回來
     （baseline 還沒被任何一個更新過），這個條件就抓不到「其實已經有更新的嘗試在路上」，較舊
     那次的失敗會被誤判成整個操作失敗，把 `countsLoading` 提早關掉、`onError` 提早觸發，即使
     真正較新的請求隨後成功。修法：新增 `_countsLatestIssuedAsOf`，在每次呼叫的當下（而非拿到
     回應時）就更新——error handler 改成跟這個「最新已發出」的 ticket 比，不是跟「最新已套用」
     的 baseline 比；success handler 也只在自己就是最新已發出的那次時才清 `countsLoading`。
  3. **`load()`/`loadMore()` 用來 reconcile 的 `previous` 陣列在請求「發出當下」就截取了**：
     若 GET 發出時文章還是未讀、`markRead()` 是在 GET 已經送出、回應尚未回來之間才啟動並樂觀
     翻成已讀，`reconcileItems()` 看到這篇文章仍是 `_pending`，會拿舊的（GET 發出當下截取的）
     `previous` 快照蓋回去——那個快照本身就還沒反映這次樂觀更新，所以會把已讀又蓋回未讀；
     等到 POST 真的成功，成功回呼只呼叫 `confirmReadState()` 記錄確認結果，並不會重新
     `patchItem()` 那一列，錯誤的未讀狀態會一直留到下一次 `load()`/`loadMore()` 才自動修正。
     修法：`reconcileItems()` 不再吃外部傳入的 `previous` 參數，改成在方法內部呼又時當下讀
     `this._items()`——這樣拿到的一定是回應抵達那一刻的最新狀態，包含回應抵達前才啟動的樂觀
     更新。
  - **測試**：`reading-stream.spec.ts` 新增三個案例，各自對應上面三點——`loadCounts()`
    在寫入仍 pending 時，即使 delta 的 commit ticket 早於某次 GET 的 `asOf`，未讀數仍要保留該
    delta（若照原本邏輯執行會斷言失敗，證實這是真的迴歸測試而非重複既有覆蓋）；`loadCounts()`
    在較舊請求先失敗、較新請求還沒回來時忽略那次失敗且不提早關閉 `countsLoading`；
    `load()` 在 GET 發出「之後」才啟動的 markRead optimistic 更新，回應抵達時仍要保留已讀狀態
    （既有的「pending 保留樂觀值」案例其實沒踩到這個 bug——那個案例裡寫入是在 `load()` 呼叫
    「之前」就啟動的，`previous` 截取當下已經反映過樂觀值，不足以證偽舊邏輯，故補這個更精確的
    案例）。既有測試全數維持原斷言可通過。
  - 本輪的網路限制與驗證方式同上——`npm ci` 仍被擋下，改用 `tsc --noResolve` 與
    `prettier --check` 驗證這兩個檔案，邏輯正確性以手動逐步追蹤新增與既有測試案例的
    ticket／delta 數值運算確認，實際 `npm test` 交給 PR #56 的 CI 跑過（`Build` job 綠燈）。
- **PR review 修正第二輪（Codex，2 個 P2，1 修 1 記錄為已知限制）**：
  1. **修**：`markAllReadInView` 的批次部分失敗時，原本先跑失敗批次的 rollback（呼叫
     `commitCountDelta` 觸發 `recomputeCounts()`），才跑成功批次的 `confirmReadState()`。
     成功批次的文章在 `_pending` 已於函式最前面被整批清掉、但還沒被
     `confirmReadState()` 標記確認的這段空窗期裡，若剛好被失敗批次的 rollback
     觸發一次 `recomputeCounts()`，會被誤判成「未 pending 且未確認、ticket 又早於等於
     baseline」而排除在外——`confirmReadState()` 本身不會觸發 recompute，所以這筆遺漏
     直到下次別的地方觸發 recompute 前都不會自己修正，未讀數／badge 會少扣那個成功批次
     的量。修法：把「確認成功批次」的迴圈移到「處理失敗批次 rollback」之前，讓成功批次的
     delta 在任何 rollback 觸發的 recompute 發生前，就已經進入「已確認且 ticket 新於
     baseline」的可疊加狀態。新增 `reading-stream.spec.ts` 案例（620 篇、前 500 成功
     後 120 失敗、baseline 未讀數設一個不會被 clamp 蓋掉差異的大數字），照舊順序執行會
     斷言失敗（顯示未讀數完全沒扣），驗證這是真的迴歸測試。
  2. **記錄為已知限制，未修**：同一篇文章身上，一個仍 pending 的單篇 markRead/markUnread
     與一個涵蓋它的 `markAllReadInScope` 各自獨立送出去，client 端無法從兩個回應誰先抵達
     推斷兩者在伺服器端真正的 commit 順序——任何用回應抵達順序或呼叫 `confirmReadState()`
     先後當決勝規則的修法，都只是把現有的不確定性換一個方向，並不是真的解掉它，需要 API
     額外提供列版本／時間戳之類的排序依據才能穩妥解決。這是窄視窗（兩個獨立寫入要短時間內
     命中同一篇文章）、不影響伺服器端資料正確性、只影響 UI 顯示到下次 reload 為止的既有已知
     限制類型（同本階段開頭「背景」段所述 PR #43 遺留缺口的精神），記錄在 TODO.md
     的「技術與可靠性優化」小節，留待有更明確的排序依據時再處理，不在本 PR 內強行猜一個
     決勝規則。
- **PR review 修正第三輪（Codex，2 個 P2，1 修 1 記錄為已知限制）**：
  1. **修**：`loadCounts()` 成功回呼原本只跟「最後一次被接受的 baseline」比較 ticket 決定
     要不要丟棄這次回應——但如果一個較新（ticket 較大）的 `loadCounts()` 呼叫最終是以
     **失敗**收場（例如 `markAllReadInScope` 寫入成功後觸發的刷新剛好打不到後端），
     baseline 從來沒被那次較新的呼叫推進過，一個更舊、還在飛的請求解析回來時就會通過
     這個比較被誤判成「還沒被蓋過」而被接受，用寫入前的舊數字覆蓋 badge——即使呼叫端才剛被
     `onError` 告知這次刷新失敗。修法：把成功回呼的丟棄條件從「比 baseline 的 ticket 舊」
     改成「比最後一次**發出**的 ticket（`_countsLatestIssuedAsOf`，失敗回呼已經在用同一個）
     舊」——只要曾經發出過更新的請求，不管那個更新的請求最後是成功還是失敗，比它舊的回應
     一律視為作廢，不再有機可乘；順便讓「什麼時候該清 `countsLoading`」的邏輯跟著簡化成
     「接受回應就清」，因為現在能被接受的回應必然就是最後一次發出的那個。新增
     `reading-stream.spec.ts` 案例：較新請求先失敗、較舊請求帶著假數字之後才回來，斷言
     未讀數與 `countsLoaded` 都維持初始狀態（照舊邏輯執行會斷言失敗，證實是真的迴歸測試）。
  2. **記錄為已知限制，未修**：`_countDeltasByArticle` 對「仍是 `_pending` 的文章」一律疊加
     delta、完全不比較 ticket，這本來就是上一輪修 bug 時的刻意選擇，Codex 這輪指出它的
     鏡像代價——若某篇文章的寫入其實已經在伺服器端 commit、只是自己的回應還沒送達
     client，一個「之後才發出、卻先抵達」且已經反映這次寫入的 `loadCounts()` GET 會讓這篇
     文章的 delta 被多算一次。改成比照已確認寫入去比較 ticket，會直接讓上一輪才修掉的原始
     bug（pending 寫入被 baseline 誤判成「已反映」而整個丟棄）復發——兩個方向的 bug 無法只靠
     client 端的 ticket 排序同時解掉，性質與根因和上面「`markAllReadInScope` 與 pending
     markRead/markUnread」那項完全一樣。在 `recomputeCounts()` 的方法註解與 `TODO.md`
     都記錄了這個取捨與原因，不強行對調方向。
  - 本輪驗證方式同前兩輪：`npm ci` 仍被 network egress allowlist 擋下，改用
    `tsc --noResolve` 與 `prettier --check` 驗證改動，邏輯以手動追蹤新增測試案例的
    ticket 數值運算確認，實際跑測試交給 PR #56 的 CI。

- **PR review 修正第四輪（Codex，2 個 P2，全修）**：
  1. **`_countDeltas` 改成不合併、每寫入一筆 entry**：原本以 article id 為 key 合併淨值——
     一篇文章連續兩次寫入會被相消成一個數字。問題情境：文章原本已讀，`markUnread` 成功確認
     （`+1`），接著發出 `loadCounts()`；GET 還沒回來前，同一篇文章又被 `markRead`（`-1`）。
     合併淨值 `+1-1=0`，整條記錄直接被刪掉——但這個「0」抹掉的是兩筆完全不同性質的資訊：
     舊的 `+1` 已經確認成功、只是還不確定目前 baseline 有沒有反映它；新的 `-1` 則是還沒有
     任何伺服器回應的樂觀猜測，兩者需要各自獨立判斷。等 GET 回來，剛好只反映了
     `markUnread`（還沒反映 `markRead`），因為記錄已經被刪掉，未讀數少了「還有一個 pending
     寫入尚未疊加」的資訊；`markRead` 之後真的成功時，也沒有任何機制再把未讀數修正回來，
     卡住直到下次完整刷新。修法：`_countDeltas` 從 `Map<articleId, {feedId, amount}>` 改成
     一個陣列，每次 `commitCountDelta()` 都新增一筆獨立 entry
     `{articleId, feedId, amount, pending, confirmedAt}`，回傳該 entry 的參照；寫入成功時
     呼叫新的 `confirmCountDelta(entry, ticket)` 原地標記確認（`ticket` 直接沿用
     `confirmReadState()` 回傳的同一個，兩者代表同一個事件）；失敗時呼叫新的
     `removeCountDelta(entry)` 整條移除，不再用推入相反數字相消的方式處理 rollback。
     `recomputeCounts()`／`loadCounts()` 的 GC 迴圈都改成逐筆判斷
     `entry.pending || entry.confirmedAt > baselineAsOf`，不再用 `isPending(articleId)`
     查全域 `_pending` set——這帶來一個附帶好處：上一輪「批次成功要在失敗 rollback 觸發的
     recompute 之前先確認」那個順序限制，現在因為 entry 自己的 `pending` 欄位與共用的
     `_pending` set 已經脫鉤，兩個迴圈用哪個順序執行都不影響正確性，`markAllReadInView`
     的註解一併更新說明這點（順序仍保留，只是原因換了）。
  2. **`reconcileItems()` 的 pending 分支只該合併已讀狀態**：文章有 pending 寫入時，原本是
     整個回傳 `previous` 裡的舊物件（`return prior`），這連同已讀狀態一起把該篇文章可能被
     feed 重新抓取（`upsert_articles()`）更新過的標題／摘要／作者／發布時間等欄位也一併蓋回
     舊快取值，直到下一次沒有寫入競爭的 reload 才會修正。修法：改成
     `{ ...item, is_read: prior.is_read, read_at: prior.read_at }`，只從舊物件合併這兩個
     真正在競爭中的欄位，同已確認分支原本就有的寫法一致。
  - **測試**：`reading-stream.spec.ts` 新增兩案例——一篇文章先 `markUnread` 確認、
    `loadCounts()` 發出後又 `markRead`（仍 pending）：斷言整個過程與最終未讀數都正確
    （照舊的合併邏輯執行，GET 回來後與 `markRead` 成功後都會停在錯誤值，證實是真的迴歸
    測試）；`load()` 在文章有 pending 讀取切換時，回應帶回的新標題不會被舊快取蓋掉。
  - 本輪驗證方式同前——`npm ci` 仍被擋下，改用 `tsc --noResolve`／`prettier --check`；
    這輪額外用一個獨立的最小重現檔案（不含 rxjs／Angular import）搭配完整型別解析的
    `tsc` 驗證了 `(typeof this._countDeltas)[number]` 與 `new Map(...)` 這類寫法本身沒有
    型別錯誤——本檔案內的 `--noResolve` 檢查在這幾行報的兩個型別錯誤經確認是
    `--noResolve` 本身破壞 tuple 型別推導的假警報，不是真的問題。

- **PR review 修正第五輪（Codex，1 個 P2，效能，已修）**：`markAllReadInView` 樂觀套用階段
  對每個 target 各呼叫一次 `commitCountDelta()`，而 `recomputeCounts()` 的成本是 O(目前
  未平倉 entry 數)——對同一批 target 逐篇呼叫，等於把「送出 HTTP 請求之前」這段準備工作做成
  O(n²)；一次全部標已讀命中的文章數大時（例如單一多產來源一次數百篇）這段純本地運算會明顯
  變慢，且發生在任何網路請求送出之前。失敗批次的 rollback 迴圈原本也是逐篇呼叫
  `removeCountDelta()`，有同樣的問題（雖然單一批次上限是 `MARK_ALL_BATCH_SIZE=500`，量體
  較小但邏輯一樣不划算）。修法：新增 `pushCountDelta()`（只建立 entry、不觸發 recompute）
  取代樂觀套用迴圈裡的 `commitCountDelta()`，迴圈結束後才呼叫一次 `recomputeCounts()`；
  新增 `removeCountDeltas()`（批次移除＋只 recompute 一次）取代失敗 rollback 迴圈裡逐篇呼叫
  的 `removeCountDelta()`（該函式本身也改成呼叫 `removeCountDeltas([entry])`，避免重複邏輯）。
  單篇 `markRead`/`markUnread` 沿用的 `commitCountDelta()`/`removeCountDelta()`（單次呼叫即
  recompute）不受影響，因為那本來就只呼叫一次。未新增測試：這是可觀察行為不變、只有內部
  呼叫次數／時機改變的效能修正，既有的大批次（620 篇）正確性測試已經覆蓋修改後的邏輯算出
  同樣正確的結果；要斷言「recompute 只呼叫一次」需要暴露 signal `.set()` 呼叫次數這類內部
  實作細節，不值得為此新增測試耦合，故只以人工推演確認前後行為一致、複雜度改善。
## 階段三十二：PostgREST／database 例外的一致 API error mapping（2026-09-08）

TODO.md「技術與可靠性優化」批次的最後一項：`backend/database.py::get_client()` 是全專案唯一的
Supabase client 建構點，任何 route 呼叫 `.execute()` 時，一旦 postgrest-py 拋出 `APIError`
（unique constraint、check constraint、not-null、RLS 拒絕……），因為沒有任何 handler 接住，
會直接落到 Starlette 的預設行為——沒有 JSON body 的裸 500，前端拿不到任何可用資訊，也無法區分
「資料衝突」跟「真的壞掉了」。目前已知會走到寫入路徑的是 `admin_discovery.py`／
`discovery_candidates.py`／`link_harvest.py` 對 `discovery_targets`／`discovery_candidates`
的 `.insert()`（前兩者的先查後寫在併發下仍有 race window），但這是一致性修法，不是只補這幾個
call site。

- **`backend/errors.py`（新）**：`map_postgrest_error(exc) -> (status_code, body)`。用一份
  SQLSTATE（`23505`／`23503`／`23502`／`23514`／`22P02`／`42501`）＋PostgREST 自己的
  `PGRST116`（`.single()` 零筆或多筆）對照表，分別映射到 409／409／400／400／400／403／404；
  對照不到的一律回通用 `{"detail": "Internal server error"}` 的 500，不把 postgrest-py 的
  `message`／`details`（可能含表名、欄位名、原始 constraint 名稱）洩漏給呼叫端——`code` 用
  `getattr(exc, "code", None)` 讀取而非直接存取屬性，同 `routers/feeds.py` 既有對
  postgrest-py 版本差異的防禦寫法。
- **`backend/main.py`**：`@app.exception_handler(APIError)` 註冊上述映射；映射到 500（代表
  對照表沒認得的錯誤碼）的情況才寫 server-side error log（帶真正的 `code`／`message`），
  409／400／403／404 屬於正常的請求結果，不當成需要留意的操作問題來記。
- **測試**：新增 `tests/test_errors.py`，涵蓋每個對照碼、未知碼、缺 `code`、以及完全沒有
  `code` 屬性的物件（防禦寫法本身）；`tests/test_feeds.py` 新增一個透過真正的 `client` fixture
  打 `GET /api/feeds/{id}`、讓 mock 的 `.execute()` 拋出 `APIError(code="23505")` 的整合測試，
  斷言拿到的是映射後的 409 而不是未接住的例外——證明 handler 真的被 FastAPI 註冊上，不只是
  `map_postgrest_error()` 本身邏輯正確。
- **本 sandbox 的已知限制**：`pip install -r requirements.txt` 被 PyPI 的 network egress
  allowlist 擋下，與先前多個 PR 遇到的限制相同，無法在本機安裝 `postgrest`／`fastapi` 實際跑
  `pytest`。`postgrest.exceptions.APIError` 的建構子簽名（接受一個 dict，讀出
  `message`／`code`／`hint`／`details` 四個屬性，`.get()` 帶預設值故缺鍵不會噴例外）已透過
  postgrest-py 官方文件（readthedocs `api/exceptions.html`）核對過；已用
  `python3 -m py_compile` 與 `ruff check` 過新增／改動的 backend 檔案，交給 CI 的
  `backend.yml` 實際跑過 `pytest` 驗證。
- 對應文件更新：`TODO.md`（「PostgREST／database 例外...API error mapping」項目打勾並補上
  機制說明）。
- **PR review 修正（Codex，兩個 P2）**：
  1. 500 的 log 只記 `code`／`message`，沒有 `details`／`hint`，也沒有帶 traceback——這個
     handler 本來就是取代 Starlette 預設會印出完整 traceback 的行為，只記兩個欄位等於讓真正
     需要調查的未知錯誤反而少了診斷資訊。`main.py` 補上 `hint`／`details`、`request.method`／
     `request.url.path`，並加上 `exc_info=exc` 保留 traceback。
  2. `PGRST116` 不是只代表「零筆」，PostgREST 對 `.single()`／`maybe_single()` 這個 code 同時
     覆蓋零筆跟多筆兩種情況（`maybe_single()` 只吃掉零筆的例外，多筆仍會 raise）——原本無條件
     映射成 404 是錯的，`routers/admin_discovery.py::seed_targets` 的
     `.eq("host", host).maybe_single()` 就是個真的會踩到的案例：migration 006 說明
     `discovery_targets` 對 `host`沒有 unique constraint（只 unique 在 `url`，因為一個 OPML
     目錄可以在同一個 host 貢獻多筆 feed），一個熱門 host 累積多筆是預期中的正常狀態,
     多筆同 host 時把它報成「找不到」還吞掉 log，會讓這類資料狀態異常變得無法被發現。
     修法：`errors.py` 改成讀 `details` 欄位裡的 `"Results contain N rows"`（PostgREST 在零筆
     跟多筆時 `message` 相同，只有 `details` 的筆數不同），`N == 0` 才映射 404，其他情況
     （含 `details` 無法解析或缺漏）一律落回一般 500，交給上面補強的 log 記下來。
     `tests/test_errors.py` 新增零筆／多筆／`details` 無法解析／`details` 缺漏四個案例。
- **PR review 修正第二輪（Codex，P2）**：第一輪的 `_ROWS_IN_DETAILS` 只認得舊版 PostgREST 的
  `"Results contain N rows, application/vnd.pgrst.object+json requires 1 row"`——較新版本
  （`message` 也從「JSON object requested, multiple (or no) rows returned」換成「Cannot
  coerce the result to a single JSON object」）改成單數的 `"The result contains N rows"`，
  原本的規則式（大小寫、`Results`／`result`、`contain`／`contains` 都是精確比對）在這個版本上
  完全不會 match，等於每個零筆的 PGRST116 都會落回一般 500，而不是原本要的 404——跟這個 PR
  想修的問題方向正好相反。修法：`_ROWS_IN_DETAILS` 改成
  `r"results?\s+contains?\s+(\d+)\s+rows?"`（`re.IGNORECASE`），同時吃兩種版本的措辭，不釘死
  在其中一種。`tests/test_errors.py` 的零筆／多筆案例都改成 `@pytest.mark.parametrize`，兩種
  措辭各測一次。
- **PR review 修正第三輪（Codex，P2）**：`42501`（insufficient_privilege）原本映射成 403，
  隱含「這次請求的呼叫者沒有權限」——但 `database.py::get_client()` 是全專案唯一的 client
  建構點，永遠用 `SUPABASE_KEY`（依 TODO.md Phase 0，必須是 service_role key），完全繞過
  RLS，也沒有任何 per-request／per-user 的身分。這個架構下 `42501`唯一可能的成因是
  service_role key 本身或它的 schema／function grant（migration 010）設定錯誤——是部署層級
  的錯誤設定，不是某次請求真的被拒絕；映射成 403 不只講錯故事，還讓它跳過
  `status_code >= 500` 才會走的完整診斷 log，變成一次完全沒有留下痕跡的資料庫權限失效。
  修法：把 `42501` 從 `_STATUS_BY_CODE` 移除，讓它落回一般 500（連同上面已經補好的
  `hint`／`details`／traceback log）。程式碼註解與 `tests/test_errors.py` 同步更新；等
  TODO.md「一般使用者路徑改用 user JWT scoped client」那項真的做了，`42501` 才會重新變成
  一個有意義的 per-request 403，到時要把這個排除規則拿掉。
- **PR review 修正第四輪（Codex，P2）**：`errors.py` 把 `23505`（unique_violation）全域映射成
  409 之後，出現一個沒預料到的跨層副作用——`frontend/src/app/services/admin.ts::report()` 是
  所有 admin API 呼叫共用的錯誤處理，原本把「任何 409」都當成「`approveCandidate` 核准了一個
  已被拒絕的候選」，顯示對應提示。這個假設在這個 PR 之前是對的，因為在此之前只有
  `approve` 端點自己用 `HTTPException(409, ...)` 明確丟過 409；但現在
  `seedTargets()`（`POST /admin/discovery/targets`）這類完全不相關的寫入，一旦與
  `discovery_targets.url` 的 unique constraint 競爭，也會經過新的全域 handler 變成 409，
  卻被 `report()` 誤判成「候選已被拒絕」，讓操作者看到完全對不上的提示。
  修法：`report()` 的 `case 409` 改成只在 `context === '核准失敗'`（`approveCandidate()`
  自己的 context 字串）時才顯示候選專屬訊息，其他 context 一律走既有的
  `${context}：${apiMessage(...)}` 通用衝突訊息；同步更新 `AdminService` 頂部說明 409
  語意的註解。新增 `frontend/src/app/services/admin.spec.ts`（這個服務先前完全沒有測試，
  也是本專案第一個用 `HttpTestingController` 直接測 HttpClient-based service 的案例）：
  `approveCandidate()` 收到 409 時顯示候選訊息、`seedTargets()` 收到 409 時顯示通用衝突訊息
  兩個案例。
- **CI 修正**：這是這個 PR 第一次改到 `frontend/`，第一次真的觸發 `frontend.yml`——結果
  `Build`（跑 `npm test`／Vitest）失敗：`it('...', (done) => { ...; done(); })` 這種
  Jasmine 風格的非同步寫法在這個專案的 Vitest 底下不成立，`done` 收到的是一個
  `TestContext` 物件，不是可呼叫的 callback（`TS2349: This expression is not callable`）。
  修法：拿掉 `done`，改成同步斷言——`HttpTestingController.flush()` 本來就是同一個
  call stack 內同步送達 subscriber，`subscribe({ error: () => {} })` 之後接著呼叫
  `.flush()`，再直接斷言 `toastCalls`，不需要任何 async/await 或 done。
- **CI 修正第二輪**：上面那次推上去之後還是紅——這次是真的執行到請求了，但
  `httpMock.expectOne('/api/admin/discovery/candidates/c1/approve')`（字串形式的
  `expectOne` 是對 `req.url` 做精確字串比對）找不到相符的請求：這個測試環境下
  `HttpClient` 會把相對路徑解析成絕對網址（`http://localhost:8000/api/...`）才送進
  mock backend，不是保持原本的相對路徑。第一個測試的 `expectOne` 因此直接 throw，連帶讓
  `afterEach` 的 `httpMock.verify()` 抓到一個沒被 flush 掉的請求，而第二個測試的
  `beforeEach` 又因為第一個測試中途失敗、沒能讓 TestBed 正常收尾而撞上
  「Cannot configure the test module when the test module has already been
  instantiated」——三個錯誤其實是同一個根因級聯出來的。修法：`expectOne` 改用 predicate
  （`(req) => req.url.endsWith(...)`）比對路徑尾端，不管前面解析出的是相對還是絕對網址；
  `beforeEach` 補上 `TestBed.resetTestingModule()`（`discover.spec.ts` 既有的寫法），
  每個測試都從乾淨的 TestBed 開始，不互相依賴前一個測試有沒有正常收尾。
- **PR review 修正第五輪（Codex，P2）**：`TODO.md` 這一項的完成說明從第一版之後就沒再更新，
  還寫著「RLS 拒絕映射到 403」，但第三輪已經把 `42501` 從映射表移除、改落回通用 500；也完全
  沒提到 `22P02`（invalid_text_representation）跟 `PGRST116` 零筆／多筆的區分。修法：改寫
  說明文字對齊 `errors.py` 最終版的實際行為，避免之後的維護者照著這段過期說明去猜 API
  contract。

## 階段三十三：推薦回饋持久化——喜歡／不喜歡／跳過跨裝置、分層評分權重與推薦理由（2026-09-10）

TODO.md「建議開發批次」第 6 批：猜你喜歡的喜歡／不喜歡／跳過過去完全只存在瀏覽器
localStorage（`RecommendationService`），換裝置或清掉瀏覽器資料就整個消失，`routers/
recommendations.py` 也完全不知道使用者過去的回饋，只能靠呼叫端把 `liked`／`disliked`
（各上限 50 筆）老實帶回來。

- **`backend/migrations/019_recommendation_feedback.sql`（新）**：`driftread.
  user_feed_feedback`，`PRIMARY KEY (user_id, feed_id)`——一個使用者對一個 feed 只留最新
  立場（`feedback_type`：`liked` / `disliked` / `skipped`），不是逐筆事件記錄，同
  `user_feeds`／`user_preferences` 既有的單列狀態模式。RLS owner policy 沿用 migration 010
  最終版寫法（init-plan 化 `auth.uid()` ＋排除 Supabase 匿名登入 session）；migration 010
  已把 `driftread` schema 新表的預設權限 REVOKE 給 `authenticated`，這裡重新明確 GRANT。
- **`backend/routers/me.py`**：`GET`／`PUT`／`DELETE /me/feed-feedback{,/​{feed_id}}`——
  `PUT` upsert（`on_conflict="user_id,feed_id"`），每次都重寫 `created_at`（upsert 只在
  INSERT 時套用欄位 DEFAULT，若不手動更新，改變立場不會更新時間戳，`skipped` 的短期衰減
  就會用到第一次而非最新一次的時間）。
- **`backend/routers/recommendations.py`**：評分從三個等權 flat set（category/tag/language
  membership）改寫成 `Signals` dataclass——依訊號來源分層加權（`_WEIGHT_SUBSCRIBED`／
  `_WEIGHT_LIKED`／`_WEIGHT_BOOKMARKED`／`_WEIGHT_DISLIKED`／`_WEIGHT_SKIPPED`，強度依
  TODO.md「訂閱為強正向；喜歡為正向；收藏為中度正向」排序，外加不喜歡／跳過的負向權重）；
  `_load_signals()` 一次查齊訂閱、`user_preferences`、`user_feed_feedback`、收藏文章所屬來源
  四種來源，`_SKIP_DECAY`（14 天）內的 `skipped` 才排除與降權，過期則完全不影響排序（不是
  永久排除）；`_reason()` 依訂閱／喜歡／收藏優先序，解釋候選命中的 category 或 tag，回應
  model 改成 `RecommendedFeed { feed, reason }`。新增 `skipped` query 參數（獨立於
  `disliked`），供匿名呼叫端沿用既有的純排除語意——沒有伺服器端時間戳可供衰減。順手清掉
  `models.py` 定義了但從未被任何程式碼引用的 `RecommendationRequest`。
- **`frontend/src/app/services/recommendation.ts`**：`liked`／`disliked`／`skipped` 三個
  互斥的本地立場（喜歡一個 feed 會同時把它從不喜歡／跳過裡移除，反之亦然，鏡射伺服器端
  一個 feed 只留一筆的模型）；新增 `skip()`，與 `dislike()`分開——猜你喜歡卡片的「跳過」
  過去誤用 `dislike()`，讓一個輕量的「先不看」被記成 feed 詳情頁「不喜歡」按鈕那種明確負向
  訊號。已登入時三個動作都額外呼叫 `MeService.setFeedFeedback()` 盡力持久化（fire-and-
  forget，失敗不影響本地狀態或拋出）。**刻意不**在登入時把伺服器回饋合併進本地
  localStorage：這三個 key 是瀏覽器層級、不分帳號的，合併等於讓一個帳號的喜好留在瀏覽器裡
  影響下一個登入的帳號或匿名瀏覽——推薦排序本來就不依賴前端本地狀態（`_load_signals` 每次
  重查資料庫），拿掉自動合併不影響跨裝置的推薦品質，只是本地 `isLiked`／`isDisliked` 這類
  UI 提示在其他裝置上不會立刻反映，可接受的落差。
- **`frontend/src/app/components/recommendations`**：`skip()` 改呼叫
  `RecommendationService.skip()`；卡片新增推薦理由（`item.reason`，`null` 時不顯示）；
  回應型別從 `Feed[]` 改為 `RecommendedFeed[]`。
- **測試**：`backend/tests/test_me_feed_feedback.py`（新）涵蓋三個端點；
  `test_me_isolation.py` 補三個跨使用者隔離案例；`test_recommendations.py` 的
  `TestScoreCandidates` 改用 `Signals`，新增 `TestReason` 與已登入者 liked／disliked／
  skipped（含過期衰減）／bookmarked 訊號端到端案例，既有案例改讀新的 `row["feed"][...]`
  回應形狀。`frontend/src/app/services/recommendation.spec.ts`（新，這個 service 先前完全
  沒有測試）涵蓋互斥立場、已登入才持久化、持久化失敗不影響本地狀態、`getRecommendations()`
  的 50 筆上限與「最近的在後」排序。`recommendations.spec.ts` 同步改用 `RecommendedFeed`。
- **本 sandbox 的已知限制**：`pip`／`npm` 的 PyPI／npm registry index 皆被 network egress
  allowlist 擋下，與先前多輪修法相同的既有限制，無法在本機安裝依賴跑真正的
  `pytest`／`vitest`。後端新增的純邏輯（`Signals`／`_score`／`_reason`／skip 衰減時間運算）
  已用一組獨立的 stub 模組（替換 `fastapi`／`supabase`／`auth`／`database`／`models`／
  `rate_limit`，只留 stdlib 依賴）直接 import 並執行實際的 `routers/recommendations.py`
  逐案例驗證；`python3 -m py_compile` 與 `ruff check` 過所有新增／改動的 backend 檔案；
  frontend 用系統 `tsc --ignoreConfig --noResolve` 驗證語法，實際 `npm test`／production
  build 交給 CI 的 `frontend.yml`／`backend.yml` 執行。
- 對應文件更新：`TODO.md`（「推薦回饋持久化」全數打勾並記錄與原規格的兩處刻意偏離：
  不額外複製 `subscribed`／`unsubscribed` 進 `user_feed_feedback`、不做匿名登入自動合併；
  「建議開發批次」第 6 項打勾）、`docs/FEATURES.md`（第 1 節功能總覽、第 2 節推薦邏輯改寫
  為分層權重表、第 3 節新增三個 `/me/feed-feedback*` 端點、第 5 節新增資料表與索引）。
- **PR review 修正（Codex，1 個 P1，2 個 P2，均證實為真）**：
  1. **P1**：`test_final_order_reflects_score_not_quota_origin` 沒有跟著新的分層權重調整——
     `_WEIGHT_LIKED` 的 category +2、tag +1，讓案例裡「category 命中」的 preferred（+2）跟
     「兩個 tag 命中」的 exploratory（1+1=+2）同分，Python `sort()` 的 stable 特性讓同分時
     preferred 留在前面，斷言因此必然失敗。修法：exploratory 改成三個 tag 命中（+3），
     確保跟 preferred 的 +2 有明確差距，同時驗證過即使套用新權重仍然通過。
  2. **P2**：`RecommendationService.getRecommendations()` 先前不論登入與否都把本地
     `liked`／`disliked`／`skipped` 三個陣列當 query string 送出，原意是「已登入者的伺服器端
     回饋跟本地陣列並存也無妨」——但兩個實際後果都是真的洞：(a) 已登入者的 `liked` 訊號會被
     算兩次（一次來自 `_load_signals` 讀到的 persisted 列，一次來自這裡的 query param 重新
     觸發 `add_liked()`），把 `_WEIGHT_LIKED` 的 +2／+1 悄悄疊成 +4／+2；(b) `skipped` 陣列
     完全沒有時間戳可供前端自行判斷是否過期，只要還留在 localStorage 就會永遠隨每次請求送出，
     讓伺服器端 `_SKIP_DECAY`（14 天）刻意設計的「過期後不再排除」失效——query param 路徑
     不管新舊一律無條件加進 `excluded`。修法：`getRecommendations()` 只在**未登入**時才附上
     這三個 query param；已登入者完全信任 `_load_signals` 每次都重新查表這件事，不再有本地
     陣列可以干擾。
  3. **P2**：同一個 feed 上快速連續操作（例如先按喜歡、還沒等回應就按不喜歡）會各自送出一個
     獨立的 `PUT /me/feed-feedback/{feed_id}`，兩個請求之間沒有任何順序保證——如果先送出的
     那個晚到伺服器（不同 replica、網路抖動……），upsert 最終落地的值就會是較舊的操作，
     且不會有任何錯誤讓使用者或程式發現這筆資料其實跟本地狀態不一致，直到下次跨裝置推薦讀到
     錯的立場。修法：`RecommendationService` 用一個 `Map<feedId, Subscription>` 追蹤每個
     feed 目前唯一在途的 persist 請求，發出新請求前先 `unsubscribe()` 掉同一個 feed 舊的
     那個（`HttpClient` 的 unsubscribe 會真的取消底層請求），保證同一個 feed 永遠只有最多
     一個請求在飛，沒有兩個結果可以互相蓋過。
  - **測試**：`recommendation.spec.ts` 新增已登入時不附加 query param、以及同一 feed 連續
    操作會取消前一個請求兩個案例。

## 階段三十四：全文搜尋——文章與 Feed 分開搜尋，PostgreSQL tsvector + GIN index（2026-09-14）

TODO.md P2「全文搜尋」：過去唯一的關鍵字搜尋是 `GET /feeds?search=` 的
`ilike '%keyword%'`（無 index、大表整表掃描），而且完全沒有文章層級的搜尋——`articles`
表從未被搜尋觸及。

- **`backend/migrations/020_full_text_search.sql`（新）**：`articles`／`feeds` 各加一個
  `search_vector`（`GENERATED ALWAYS AS to_tsvector('simple', ...) STORED`）＋GIN index。
  語言設定刻意統一用 `simple`、不做逐語言 stemming：tsvector 的 lexeme 依建立時的
  regconfig 決定，查詢端 tsquery 必須用同一個 regconfig 才能命中，而 Driftread 的搜尋橫跨
  多個 feed／多種語言混在同一次結果裡，沒有單一查詢能同時對「每篇文件各自用不同 regconfig
  建的 tsvector」都選對 config；加上目前有語言偵測的 zh／ja／ko／th／vi 等，Postgres 內建
  本來就沒有對應斷詞字典。統一 `simple` 讓建索引與查詢兩端永遠一致，是「無法可靠斷詞時提供
  可預測 fallback」的落地方式，真正逐語言 stemming 留待之後有需要再做。新增
  `search_articles()`／`search_feeds()` 兩個 DB function：`websearch_to_tsquery` 比對
  `search_vector`，`ts_rank_cd` 排相關度，`(rank, sort_at/created_at, id)` 三欄 keyset
  分頁（同分退回既有日期／id 決勝規則）。三層 CTE 而非單層：`ranked` 只算 rank（GIN index
  篩過的列才算），`paged` 做 cursor 篩選＋排序＋LIMIT，最外層才對已經分頁過的那一頁（≤100
  列）呼叫 `ts_headline`——`ts_headline` 要重新掃過整段文字找命中片段，比 `ts_rank_cd` 貴
  得多，一個熱門關鍵字命中幾千篇文章時不該對每一篇都算一次。`SECURITY INVOKER`、EXECUTE
  只授權 `service_role`，同 `list_reading_stream`／`list_feed_articles` 的既有鎖法。
- **`backend/routers/search.py`（新）**：`GET /search/articles`、`GET /search/feeds`，
  分開端點、分開回應形狀（TODO.md 明列「Feed 名稱／描述搜尋與文章搜尋分開呈現」）。
  `q` 必填（1–200 字）、`language` 可選、cursor 分頁（`limit` 上限 100，同
  `GET /feeds/{feed_id}/articles` 的 400-on-malformed-cursor 慣例）。文章搜尋是公開端點，
  帶有效 token 時每筆回傳呼叫者自己的 `is_read`／`is_bookmarked`（同
  `list_feed_articles`）。
- **`backend/utils.py`**：新增 `encode_rank_cursor`／`decode_rank_cursor`——三欄
  `(rank, marker, id)` 版的 keyset cursor，`rank` 用 `repr()` 而非 `str()` 編碼以保證
  Postgres `real`（float4）經 JSON 往返成 Python `float64` 再送回時精確相等，cursor
  的 rank 比對才不會因為浮點數表示誤差錯過或重複結果。
- **前端**：`components/search`（新，`/search`）——文章／來源兩個分頁，各自獨立的
  cursor 分頁狀態與載入更多；`(query, language)` 相同時切換分頁或重新整理不重打 API。
  命中摘要片段固定走既有 `stripHtml()` 呈現成純文字（不特別高亮 `ts_headline` 加的
  `<b>` 標籤）——避免把 RSS 內容裡本來就可能存在的標籤與 `ts_headline` 自己加的標籤
  混在一起走 `[innerHTML]`，同現有 `stripHtml` 的既有防線一致。`services/search.ts`
  對應兩個端點。公開導覽列（含手機版抽屜選單）加上「搜尋」連結。
- **測試**：`backend/tests/test_search.py`（新，兩個端點的空/帶使用者、language 篩選、
  cursor 編碼/解碼、分頁下一頁存在與否）；`test_utils.py` 補 rank cursor 的編解碼、浮點
  數精確往返、格式錯誤拒絕案例。`frontend/src/app/services/search.spec.ts`（新，
  HttpTestingController 驗證查詢參數）；`components/search/search.spec.ts`（新，提交／
  分頁切換快取／language 變更重打／載入更多／stale response 被新搜尋蓋過／錯誤狀態）。
- 對應文件更新：`TODO.md`（「全文搜尋」四項全數打勾並記錄與規格的刻意偏離：language-aware
  config 統一走 `simple` 而非逐語言 stemming；建議開發批次第 7 項更新為全文搜尋已完成，
  資料夾管理仍未開始）、`docs/FEATURES.md`（第 1 節功能總覽、第 3 節新增 Search 端點、
  第 4 節新增 `/search` 路由、第 5 節新增 `search_vector` 欄位／`search_articles`／
  `search_feeds`／GIN index）。
- **本 sandbox 的已知限制**：與先前多輪修法相同，`pip`／`npm` 的 PyPI／npm registry
  index 皆被 network egress allowlist 擋下，無法在本機安裝依賴跑真正的
  `pytest`／`ng test`。已用 `python3 -m py_compile` 驗證所有新增／改動 backend 檔案的
  語法，`flake8` 驗證新增程式碼無 lint 問題（既有 `models.py` 一個 `Any` 未使用的既有
  問題與本次改動無關，未動它）；frontend 新增檔案已對照既有元件（`feed-detail.ts`、
  `bookmarks.spec.ts`、`feed-list.html` 的 `ngModel`／`ngSubmit` 慣例）逐行核對語法與
  慣例一致性，實際 `npm test`／production build 交給 CI 的 `backend.yml`／
  `frontend.yml` 執行。
- **PR review 修正（Codex，九輪，1 個 P1，18 個 P2，均證實為真）**：
  1. **P1**：`articles.content` 沒有欄位層級長度上限（只有抓取階段整個 feed 下載量的
     5 MiB 上限），而 Postgres 的 tsvector 序列化後有約 1 MiB 的大小限制——單篇超大文章
     會讓 `search_vector` 這個 generated column 的計算直接丟出
     `string is too long for tsvector`，新文章寫入失敗，或者這個 migration 替既有資料
     回填該欄位時整個 migration 失敗、擋住後端啟動。修法：新增
     `driftread.bounded_search_text(text)`（`IMMUTABLE` SQL function，`left(text, 100000)`），
     所有送進 `to_tsvector`／`ts_headline` 的文字都先經過它——100,000 字元就算全是
     4-byte UTF-8 字元也只有 400 KB，遠低於 1 MiB 上限，且沒有真實搜尋情境需要比這更
     後面的內文才能命中。
  2. **P2**：`search_articles` 的命中摘要片段原本不論比對命中的位置在哪，一律固定取
     `summary`（沒有摘要才退回 `content`）餵給 `ts_headline`——當一篇文章有非空
     `summary` 但查詢其實只命中 `content` 時，回傳的片段會是一段完全沒有標記到關鍵字的
     `summary` 開頭，與端點承諾的「命中摘要片段」不符。修法：分別檢查
     `summary`／`content` 各自的（同樣經過 `bounded_search_text` 界限的）tsvector
     是否命中查詢，取真正命中的那一個餵給 `ts_headline`；兩者都沒命中（純粹命中
     title／author）才退回原本的預設值——`ts_headline` 對沒有命中的文字不會報錯，只是
     顯示一段沒有標記的開頭摘錄。
  3. **P2**：`GET /search/articles`／`GET /search/feeds` 兩個公開端點原本沒有掛任何
     rate limit——與 `GET /recommendations`（同樣是公開、每次呼叫都有真實 DB 排序工作）
     先前的既有洞一樣：呼叫端可以不斷送出熱門關鍵字查詢，讓資料庫對每次查詢的所有命中
     排序、再對分頁後的結果算 `ts_headline`，造成可避免的 CPU 耗用。修法：兩個端點各自
     掛 `Depends(rate_limit("search_articles"))`／`Depends(rate_limit("search_feeds"))`，
     沿用 `/discover`／`/discover/import`／`/recommendations` 同一套「每個 client IP
     每端點 20 requests / 60 秒」預設值與獨立配額（bucket 用不同 `name`，互不影響）。
  4. **P2**：`decode_rank_cursor` 用 Python `float()` 解析 cursor 裡的 rank 部分，但
     `float()` 也會無錯誤地解析 `'nan'`／`'inf'`／`'-inf'` 這類字串——一個刻意構造、
     base64 格式正確但帶有非有限值的 cursor 會通過解碼，把 NaN／Inf 當成 RPC 的
     `real` 參數送進資料庫，依 JSON 序列化／PostgREST 處理方式不同，結果可能是跳出
     `ValueError` 判斷路徑回傳非預期的 500、或是產生不合理的分頁結果，而不是文件承諾的
     400。修法：解碼後多一道 `math.isfinite(rank)` 檢查，非有限值一律視同格式錯誤的
     cursor。
  5. **P2**（第二輪 review）：`articles.content` 刻意保留原始 HTML（供 reader 頁
     `[innerHTML]` 呈現用），`title`／`summary`／`author` 則已經是
     `rss_parser.py::_plain_text()` 產生的純文字——`search_vector` 這個 generated column
     卻把 `content` 原封不動串進 `to_tsvector`，讓 tag 名稱、屬性、class、連結網址這些
     呈現用的標記語法本身變成可搜尋詞彙：搜尋 `href` 或某個 CSS class 名稱會命中完全不
     相關的文章，重複出現的樣板標記也會稀釋 `ts_rank_cd` 的相關度排序。修法：新增
     `driftread.strip_html_for_search(text)`（`IMMUTABLE` SQL function，
     `regexp_replace(text, '<[^>]*>', ' ', 'g')`——每個標籤換成空白而不是直接砍掉，避免
     `"...句尾</p><p>下一句"` 少了空白黏成一個詞），`search_vector` 的 generated column
     與 `search_articles` 命中摘要片段的 `content` 分支都先過這道處理，不追求跟
     `_plain_text()` 完全一致的還原精確度——這裡只是搜尋索引前處理，不是要呈現給讀者看的
     文字。
  6. **P2**（第二輪 review）：`routers/articles.py::get_article`（`GET /articles/{id}`）
     原本用 `.select("*")` 查單篇文章，migration 020 替 `articles` 加上
     `search_vector` 後，這個萬用字元查詢會連帶把這個對長文章可能有數十到數百 KB 的
     generated tsvector 從 PostgREST 撈回並序列化，即使 `Article` 回應 model 從未使用它
     ——每次讀一篇文章都白白多傳一份幾乎跟全文一樣大的資料。修法：改成明確欄位清單
     `id,feed_id,title,url,summary,content,author,published_at,fetched_at`，不含
     `search_vector`。
  7. **P2**（第三輪 review）：`search_articles` 的 JOIN 只用 `f.id = a.feed_id` 取
     `feed_title`，從未檢查 `f.archived_at`——已封存來源的文章仍然完全可以透過這個新的
     公開搜尋端點被搜到／列出，與封存流程本身給操作者的承諾（`admin-feeds.ts` 封存
     確認對話框：「封存後這個來源不再出現在前台」）矛盾，也跟 `search_feeds` 早已排除
     已封存來源的既有行為不一致。修法：`ranked` CTE 的 `WHERE` 加上
     `f.archived_at IS NULL`，同 `search_feeds`／`GET /feeds` 既有行為。
  8. **P2**（第三輪 review）：`routers/feeds.py` 的 `list_feeds`（`GET /feeds`，一次最多
     100 筆）與 `get_feed`（`GET /feeds/{id}`）都用 `.select("*")`，migration 020 替
     `feeds` 加上 `search_vector` 後，這個最多索引 100,000 字元 description 的 generated
     tsvector 也會被撈回——`Feed` 回應 model 從未用到它，分頁列表的浪費隨頁面大小疊加。
     修法：改成同一份明確欄位清單 `_FEED_COLUMNS`（`Feed` model 的全部欄位，不含
     `search_vector`），兩處呼叫共用。`routers/admin.py`／
     `services/discovery_candidates.py` 還有其他 `feeds.select("*")` 既有用法，這次
     review 沒有指出（後台操作端點，不是高流量的公開路徑）——範圍留給之後真的需要時再
     處理，不在這個 PR 裡順手清掉。
  9. **P2**（第四輪 review）：第二輪加的 `strip_html_for_search()`（`<[^>]*>` → 空白）
     在屬性值裡出現字面 `>` 時會提早停在那個 `>`，不是標籤真正的收尾——例如
     `<a title="2 > 1" href="https://example.com">text</a>`，會把
     ` 1" href="https://example.com">` 這段原封不動留在索引／headline 文字裡，搜尋還是
     能命中看不見的屬性值或連結網址，等於沒真正解掉第二輪那個發現。修法：正規表示式改用
     跟 `frontend/src/app/shared/html.ts` 的 `ATTRS`／`TAG_RE` 同一套 quote-aware 邏輯
     （翻譯成 Postgres 的 regex 語法）——標籤的屬性部分改成
     `(?:[^>"']|"[^"]*"|'[^']*')*`：不是屬性值的字元逐一比對，遇到雙引號／單引號包起來的
     一整段（不論裡面有沒有 `>`）當成一個單位跳過，只有真的沒有配對引號的異常標籤才退回
     原本天真的 `[^>]*`。
  10. **P2**（第四輪 review）：`components/search/search.ts` 從未對
      `AuthService.session()` 做任何反應——`AuthService` 是非同步還原已登入 session
      的（同 `feed-detail.ts`／`reading-stream.ts`／`bookmarks.ts` 已經處理過的同一類
      問題），如果讀者在 session 還原完成前就送出搜尋，那次請求會是匿名的，
      `is_read`／`is_bookmarked` 全部回傳 false；`searchKey` 只看 query／language，不含
      使用者身分，session 還原後重新送出同一個查詢會被當成「已經載入過」直接跳過，讀者
      會一直卡在匿名結果上。修法：跟其餘元件同一套模式——建構子裡加一個
      `effect()` 追蹤 `auth.session()?.user?.id`，身分改變時清掉 `articleLoadedForKey`
      這個快取鍵；若當下就在文章分頁且有進行中的查詢就立刻重新載入，若在來源分頁則只
      invalidate，等切回文章分頁時 `loadActiveTab()` 既有的快取鍵比對自然會重新載入
      （來源搜尋本來就不帶使用者狀態，不需要在身分改變當下就重打）。
  11. **P2**（第五輪 review）：`strip_html_for_search()` 加了 quote-aware 標籤比對後，
      仍然只移除標籤本身，`<script>`／`<style>` 元素的「內容」（JS 程式碼、CSS
      selector／屬性值）沒有被當成標籤，原封不動變成一般可見文字留在索引裡——即使前端
      畫面上完全不會顯示這些內容。`rss_parser.py::_plain_text()` 早就用 `_DROP_WHOLE_RE`
      處理過同一個問題（先整個元素含內容砍掉，再處理一般標籤）。修法：
      `strip_html_for_search()` 改成兩段 `regexp_replace`——先用跟 `_DROP_WHOLE_RE` 對應
      的 pattern（quote-aware 屬性、大小寫不分、`\1` 反向參照比對收尾標籤）整個砍掉
      `<script>`／`<style>` 元素（標籤＋內容），再套用既有的一般標籤比對。
  12. **P2**（第五輪 review）：`feeds.description` 也不保證是純文字——
      `rss_parser.py::_text()`（channel 層級 description 用的就是它）只是回傳元素解碼後的
      文字內容，不像 `_plain_text()` 會處理成純文字；發佈者若在 `<description>` 裡跳脫
      HTML，XML unescape 之後就是貨真價實的 `<...>` 標記，這個 generated column 卻原封
      不動索引——搜尋 `href`／`class` 或某個網址一樣能命中一個描述根本沒顯示這些字的
      feed。修法：`feeds.search_vector` 的 generated column 與 `search_feeds` 的
      `ts_headline` 呼叫，`description` 都先過 `strip_html_for_search()`，跟
      `articles.content` 同一套處理與理由。
  13. **P2**（第五輪 review）：`components/search/search.html` 只在 `published_at` 存在時
      才顯示日期，但 model 上 `published_at` 是 nullable、`fetched_at` 永遠非空（元件自己
      的測試 fixture 也是這樣寫的），沒解析出發佈日期的文章因此完全不顯示日期，也跟後端
      `COALESCE(published_at, fetched_at)` 的排序邏輯不一致——結果少了「顯示日期」這個
      端點本來就承諾的欄位。修法：改成 `(row.article.published_at ?? row.article.fetched_at)`，
      一律顯示，退回抓取時間。
  - **測試**：`test_utils.py` 新增四種非有限值（`nan`／`inf`／`-inf`／`Infinity`）的
    拒絕案例；`test_articles.py`／`test_feeds.py` 各新增案例斷言對應端點的
    `.select(...)` 參數是明確欄位清單、不是 `"*"`；`components/search/search.spec.ts`
    新增三個案例（session 還原後重新載入文章結果、尚未送出查詢時 session 還原不觸發任何
    請求、身分改變當下人在來源分頁不立即重打但切回文章分頁會重打），做法同
    `feed-detail.spec.ts`／`my-feeds.spec.ts` 既有對 session 訊號的測法（`session.set(...)`
    後在同一個 fixture 上再呼叫一次 `detectChanges()` 讓元件自己的 `effect()` 真正跑一次
    ——`TestBed.flushEffects()` 是給注入來源為 `TestBed.inject()` 的 effect，不是給
    component fixture 的）。P2-9／11／12（SQL 正規表示式）與 P1（截斷）都是這個 sandbox
    無法連上真正 Postgres 執行的 SQL 邏輯，同本節前段記錄的既有限制，靠人工覆核；P2-13
    是純樣板改動，既有 spec 沒有對 DOM 渲染斷言的慣例（都是狀態／行為層級），跟隨這個
    檔案既有風格沒有另外補 DOM 測試；rate limit dependency 不影響既有測試——
    `conftest.py` 的 `_reset_rate_limits` 每個測試前都會清空命中紀錄，且每個測試案例
    只送一到兩次請求。
  14. **P2**（第六輪 review）：第五輪加的 `<(script|style)\b...` 這個 pattern 裡的
      `\b`，在 PostgreSQL 的 ARE（Advanced Regular Expression）語法裡是「backspace」
      這個字元跳脫（同 `\a`／`\f`／`\n`／`\r`／`\t` 那組 character-entry escape），**不是**
      Perl／PCRE 那種 word-boundary constraint——這是這兩種 regex 方言一個真實存在、
      容易誤踩的差異。結果是這個 pattern 幾乎永遠不會命中（因為真實輸入裡 "script" 或
      "style" 後面幾乎不會剛好接一個字面 backspace 字元），第五輪那個「先整個砍掉
      script／style 元素」的修法因此完全是 no-op，退回成只砍標籤、留下內容的舊行為，
      沒有真的解掉問題。修法：換成 Postgres 自己 regex 方言裡真正的 word-boundary
      constraint escape `\y`（對應 word-start／word-end 分別是 `\m`／`\M`），讓
      `<script>`／`<script ...>` 命中，但 `<scriptx>` 這種更長的標籤名稱不會被誤砍。
  - **測試**：這一項是 SQL 正規表示式本身的方言差異，這個 sandbox 無法連上真正
    Postgres 執行驗證，同本節前段記錄的既有限制，靠人工覆核（對照 PostgreSQL 官方文件
    的 constraint escape／character-entry escape 對照表逐字核對）。
  15. **P2**（第七輪 review）：`components/search/search.ts` 的 `searchKey` 用一個位元組
      分隔 `activeQuery`／`language` 兩段組成快取鍵，原意是想用一個「查詢文字與語言代碼都
      不會出現」的分隔字元，但寫進原始碼時該位元組是直接以字面 NUL byte（`\x00`）存在
      檔案裡，不是文字跳脫序列——結果 `git diff --numstat` 之類的工具把整個檔案判定成
      binary，正常的逐行 diff／merge 都失效。修法：改成 TypeScript 範本字面值裡的合法
      跳脫序列 `\u0000`（反斜線＋u0000 六個字元），執行期仍會被直譯成同一個 NUL
      字元、行為完全不變，但原始碼檔案本身變回純文字。
  - **測試**：純位元組層級的原始碼修正，行為不變（同一個 NUL 分隔字元，只是換成合法的
    文字跳脫序列表示），既有 `search.spec.ts` 的快取鍵相關案例（分頁切換快取、language
    變更重打）不需要跟著改。
  16. **P2**（第八輪 review）：`services/articles.py::upsert_articles()`（排程刷新每個
      feed 都會呼叫，每批最多 200 篇）原本用預設的 `returning="representation"`，
      migration 020 替 `articles` 加上 `search_vector` 後，這個回應會連同每篇文章可能
      數十到數百 KB 的 generated tsvector 一起序列化回傳，但呼叫端只用
      `len(result.data)` 算筆數，完全沒用到內容本身。修法：改成
      `returning="minimal"`（`Prefer: return=minimal`，完全不回傳列內容），筆數改用
      `len(chunk)` 直接算——同一批次內的 `(feed_id, url)` 已在呼叫前用 dict 去重過，
      upsert 在沒有 `ignore_duplicates` 的情況下一定是每筆要嘛新增要嘛更新，不會有
      「送出去但沒被回應提到」的列，所以 `len(chunk)` 跟原本 `len(result.data)`
      在數學上恆等，不是近似值。
  17. **P2**（第八輪 review）：`strip_html_for_search()` 只拆標籤，不處理標籤拆完後
      留在文字裡的 HTML entity（`&eacute;`／`&nbsp;` 之類）——escaped markup 常見這種
      情況：發佈者把自己的 HTML 原文用 `&amp;` 跳脫過一次才塞進 XML，XML parser 只解一次
      `&amp;`，裡面本來就是 entity 的部分（例如 `&eacute;`）解完後還是原封不動的文字。
      結果搜尋「Café」這個可見字命中不了索引裡的「Caf&eacute;」，「eacute」這個
      entity 名稱本身反而變成一個看不見卻能被搜到的詞。另外，把每個標籤都換成空白也會
      拆散行內標記中間的詞——`micro<em>soft</em>` 畫面上是一個字「microsoft」，索引
      卻變成兩個獨立詞「micro」「soft」，讀者搜畫面上看到的字反而找不到；CJK 文字中間
      被行內標籤（例如 `<a>`、`<em>`）包住幾個字時問題更明顯，因為中文本來就沒有空白
      斷詞，硬插一個空白會把一段連續文字切成兩截。修法：`strip_html_for_search()`
      改成區分「區塊標籤」（`p`／`li`／`div`／`h1`-`h6`／`br` 等，同
      `rss_parser.py::_BLOCK_TAGS`／`frontend/src/app/shared/html.ts::BLOCK_TAGS`
      原封不動照抄）換成空白，其餘所有標籤（含行內標記與註解）直接移除、不留分隔——
      跟這個專案既有的 `_plain_text()`／`stripHtml()` 同一套判斷依據；並在標籤全部
      拆完之後（避免把「示範用的逃脫標籤文字」不小心解回真標籤又被上一步吃掉）多一道
      只處理 XML 預定義的 `&amp;`／`&lt;`／`&gt;` 加上極常見的 `&nbsp;` 這四種明確、
      無歧義的 entity 解碼（`&amp;` 放最後解，避免雙重跳脫的 `&amp;lt;` 被連環解成
      `<`）。刻意不嘗試完整的 HTML 具名 entity 對照表（上千筆，`&eacute;` 這種）——
      在 SQL 裡手刻這個正是這個專案自己的 `frontend/src/app/shared/html.ts`
      的 `decodeEntities()` 已經寫下教訓、明確不要做的事（「每次 review 都會冒出另一種
      跟 Python `html.unescape()` 不一致的地方」）；真正完整的修法該放在 Python、在
      抓取階段做，同 `backend/backfill.py` 呼叫真的解析器的作法，不是在這裡養一張
      越補越大的 regex 表。
  - **測試**：第 16 項是 `services/articles.py`，`tests/test_articles_service.py`
    的 `_FakeTable.upsert` 補上 `returning` 參數並斷言等於 `"minimal"`，既有案例的
    行為不變（fake 本來 `result.data` 存的就是傳入的 chunk 本身，跟改用 `len(chunk)`
    在數學上等價）。第 17 項是 SQL 正規表示式，這個 sandbox 無法連上真正 Postgres
    執行驗證，同本節前段記錄的既有限制，靠人工逐字元核對（block-tag 清單對照
    `rss_parser.py::_BLOCK_TAGS` 逐一比對、quote-aware pattern 沿用第四輪已驗證過的
    escaping 方式、entity 解碼順序手動追蹤三個範例：`&amp;lt;`、`&lt;script&gt;`
    示範文字、一般 `&amp;`／`&nbsp;`）。
  18. **P2**（第九輪 review）：`strip_html_for_search()` 內部好幾道
      `regexp_replace`，但 `articles.content` 沒有欄位層級長度上限（理論上可接近
      抓取階段整個 feed 下載量的 5 MiB 上限）——`bounded_search_text()` 原本是等
      `strip_html_for_search()` 跑完、串接完 title／summary／author 之後才對「最終
      串接結果」做長度限制，這代表 regex 本身是對著未經界限的原始 HTML 掃描，掃描
      成本沒有上限。`search_articles` 的命中摘要片段那段 CASE 更是把
      `strip_html_for_search(content)` 對同一篇文章重複呼叫兩次（WHEN 判斷命中一次、
      THEN 分支再算一次），而這個端點單次查詢最多回傳 100 篇文章——等於單次公開搜尋
      請求最壞情況要對未界限的原始內文跑到 200 次多階段 regex 掃描，是可避免的
      DB CPU／延遲尖峰。修法：`bounded_search_text()` 改成在 `strip_html_for_search()`
      **之前**先跑（截斷是 O(1) 的 `left()`，先做完全不影響後面 regex 的正確性——
      stripping 的每一種取代都只會讓字串變短或不變，先界限原始輸入，出來的結果保證
      一樣有界限，不需要再包一層）；`search_articles` 額外把去 HTML 這道計算搬進
      `paged` 之後新增的第四層 CTE `enriched`（`SELECT *, strip_html_for_search(...)
      AS stripped_content FROM paged`），同一列只算一次、WHEN／THEN 兩處共用同一個
      欄位——刻意不放進 `ranked` 或 `paged` 本身，這兩層是在 LIMIT 篩選**之前**跑過
      所有命中的列，把貴的逐列文字處理放在那裡，就是這整個三（現在四）層 CTE 設計
      一開始想避免的事：只有真的會回傳給呼叫端的那一頁才該付這個成本。`search_feeds`
      的 `description` 只有單一候選欄位、單一呼叫點，沒有「重複算兩次」的問題，但
      一樣改成「先界限再處理」而不是「先處理再界限」。
  - **測試**：SQL 查詢結構調整，這個 sandbox 無法連上真正 Postgres 執行驗證，同本節
    前段記錄的既有限制，靠人工核對：CTE 執行順序（`enriched` 直接 `FROM paged`，
    `paged` 自己的 `ORDER BY ... LIMIT` 保證只有已經篩選過的那一頁會流進
    `enriched`）、`bounded_search_text` 與 `strip_html_for_search` 呼叫順序在六個
    呼叫點（articles／feeds 的 generated column、`search_articles` 的 WHEN／THEN／
    ELSE 三處、`search_feeds` 的 ts_headline）全部一致改成「先界限再去 HTML」。
  19. **P2**（第十輪 review）：第十八項把 `bounded_search_text()` 搬到
      `strip_html_for_search()` 之前執行後，帶出一個新的邊界案例——當一篇文章原始
      `content` 超過 100,000 字元、且截斷點剛好落在一個 `<script>`／`<style>` 元素
      中間時，截斷會把該元素的收尾標籤（`</script>`／`</style>`）一併切掉。第五、六輪
      加的「整個元素連內容砍掉」那道 `regexp_replace` 要求比對到 `</\1\s*>` 收尾標籤
      才會命中，收尾標籤被截斷後這道 pattern 就不會命中這個（截斷後）未閉合的元素，
      後面一般標籤那道 pass 只會砍掉殘留的開頭 `<script ...>` 標籤本身，把 JS／CSS
      內容原封不動當成一般可見文字留在索引裡——跟第五、六輪想解的問題（script／style
      內容不該被索引）本質相同，只是換了個從「截斷」帶出來的新誘因。修法：
      `<(script|style)...>.*?</\1\s*>` 的收尾比對改成
      `<(script|style)...>.*?(?:</\1\s*>|$)`——用 alternation 多接受「字串結尾」當成
      收尾點之一，`.*?` 是 lazy quantifier，仍然優先比對到真正的收尾標籤，只有真的
      遇不到收尾標籤（截斷造成）才會一路吃到字串結尾，把截斷後的殘缺元素整個砍掉而不是
      留下沒加保護的內容。
  20. **P2**（第十輪 review）：`services/feed_refresh.py::refresh_one()` 三處
      `db.table("feeds").update(...)` 呼叫（抓取失敗、304 not modified、成功更新這三條
      路徑）都沒有使用回應內容，但都沿用 postgrest-py `update()` 預設的
      `returning="representation"`——migration 020 替 `feeds` 加上 `search_vector`
      後，排程刷新（預設每批 50 個 feed）每次更新都會連同該 feed 可能高達數十 KB 的
      generated tsvector 一起序列化回傳，同第十六項 `services/articles.py::upsert_articles()`
      已經解掉的同一類浪費，只是這次是 feed 更新而非 article upsert，那次的修法沒有覆蓋
      到這裡。修法：三處都加上 `returning="minimal"`，行為與回傳值皆不變（呼叫端本來就
      只依賴 side effect，不讀取任何回應內容）。
  - **測試**：第 19 項是 SQL 正規表示式，這個 sandbox 無法連上真正 Postgres 執行驗證，
    同本節前段記錄的既有限制，靠人工核對 lazy quantifier 搭配 alternation 的比對順序
    （逐字元手動追蹤一個刻意截斷在 `<script>` 中間的範例字串）。第 20 項是
    `backend/tests/test_feed_refresh.py` 的 `_FakeTable`／`_FeedsProxy.update()`
    fake 補上 `returning` 參數（原本只接受單一 payload 位置參數，呼叫端傳
    `returning="minimal"` 關鍵字參數會直接拋 `TypeError`），三個既有 `update()` 呼叫點
    的既有測試案例（`db.feed_updates` 斷言）不需要跟著改——fake 記錄的仍然是同一個
    `payload`，`returning` 只是額外接受、不影響任何既有斷言。

## 階段三十四：偏好設定與推薦回饋的前端整合測試，並修掉訂閱回應晚到多吃一張卡的 race（2026-09-15）

TODO.md「Frontend 與 CI」最後一個未完成的測試項目：訂閱 CTA 與我的閱讀流早就補過，偏好設定
與推薦回饋當時寫「都還沒實作，測試無從補起」，但 PR #44（偏好設定 UI）與 PR #58（推薦回饋
持久化）之後這句話已經過期——功能都在，只是那條清單沒回頭補測試。

- **`frontend/src/app/components/preferences/preferences.spec.ts`**：在 PR #44 既有的載入／
  toggle／儲存成功失敗／stale generation 案例之外新增五個案例——(1) 送出的 payload 是 toggle
  後的選擇而非載入時的（`PUT /me/preferences` 整批覆寫兩個陣列，取消勾選的分類只有在 payload
  裡真的不見才會消失，先前的測試完全沒有斷言過送出去的內容）；(2) toggle chip 在按下儲存前
  不送任何請求——chip 純粹是本地意圖，沒有 per-toggle 請求需要 debounce 或 de-dup，唯一的 PUT
  發生在 `save()`，連點由 `[disabled]="saving()"` 擋住；(3) 儲存失敗不清掉選擇，重試送出同一
  份 payload（這裡沒有「伺服器端舊值」可以回滾，清掉只會讓使用者重選一次）；(4) 只有
  `GET /feeds/categories`／`GET /feeds/languages` 失敗時表單仍可用，不進 `error` 狀態——代價
  只有兩個 toast 與空 chip 清單，與「`GET /me/preferences` 失敗必須整個表單藏起來」的既有
  設計相對照；(5) 三個讀取全部 settle 前維持 `loading`，避免先閃出一個空選擇的表單，而那個
  表單的儲存按鈕會把畫面上的空選擇 PUT 出去。`setup()` 改成會記錄每次 `updatePreferences()`
  的 payload，並可覆寫兩個 catalog observable。
- **`frontend/src/app/components/recommendations/recommendations.spec.ts`**：新增
  `Recommendations feedback actions` 區塊。回饋持久化本身（`PUT /me/feed-feedback/{feed_id}`、
  未登入不送、失敗不回滾本地狀態、同一 feed 只保留一個在途請求）是 `RecommendationService`
  的責任，`services/recommendation.spec.ts` 已經逐項覆蓋，這裡刻意不重複；補的是元件自己的
  接線：喜歡／跳過各自回報哪個 feed id、卡片是否立刻前進（回饋是 best-effort，不該等回應）、
  `已喜歡 N 個` 讀的是 service 的 `liked()` 而不是元件私有計數，以及回饋與在途訂閱之間的互動。
  `SubscriptionService` 的 mock 改成把成功／失敗回呼留著由測試決定何時觸發，才有辦法測到
  「訂閱還在途中」這個視窗。
- **`frontend/src/app/components/recommendations/recommendations.ts`（真實 bug 修正）**：寫上面
  最後一個案例時發現的 race——`subscribe()` 的成功回呼無條件呼叫 `next()`，但訂閱在途期間只有
  訂閱 按鈕自己被 `isSubscribePending` 停用，跳過／喜歡 兩顆按鈕仍然可以點。讀者按下 訂閱 後
  不等回應直接按 跳過，牌堆會先前進一格，訂閱回應到達時再前進一格——`next()` 只進不退，中間
  那張推薦就這樣沒被看過就從這批消失（`loadMore()` 重抓一批也一樣，回應晚到會吃掉新牌堆的第
  一張）。修法最小：成功回呼只在「畫面上仍是當初按下訂閱的那張卡」（`this.current?.feed.id
  === feed.id`）時才 `next()`，`rec.like()` 這個比 skip 更強的正向訊號照樣記錄。對應測試三個：
  跳過之後訂閱成功不再前進、讀者沒動時訂閱成功照常前進、跳過之後訂閱失敗同樣不動牌堆。
- **PR review 修正（Codex，P2，證實為真）**：上面那個修法只比對 `feed.id`，漏了一種情況——
  訂閱還在途中時，讀者點了「再推薦一批」（`loadMore()`），新抓回來的牌堆剛好在同一位置又出現
  同一個 feed（該訂閱還沒 commit，伺服器沒有理由排除它），成功回呼比對 `feed.id` 會誤判成
  「還在原本那張卡」，把這張其實從未被看過的新卡片也吃掉。修法：改成連牌堆本身的陣列參照一起
  比對（`this.feeds() === deck`）——`loadMore()` 每次呼叫都會 `set()` 一個全新陣列，即使內容
  剛好重複，參照必然不同，藉此區分「同一張卡還沒換」與「牌堆已經整批換過，只是恰好重複」。
  新增一個案例：`loadMore()` 换出的新牌堆第一張恰好也是 feed-1 時，舊的訂閱回應到達不會吃掉它。
- **本 sandbox 的已知限制**：`registry.npmjs.org` 依舊被 network egress allowlist 擋下（這次
  連 metadata 都是 403，`npm ci` 卡在 `@angular/cli` 的間接依賴 `zod-to-json-schema`），
  `node_modules` 裝不起來，因此本機跑不了 `vitest`、`ng build`，連 `prettier --check` 都跑不
  起來（`npx prettier` 同樣 403）。已用系統 `tsc 6.0.2`（`--ignoreConfig --noResolve`）確認三
  個改動檔案沒有語法或型別結構問題（只剩因為 `--noResolve` 而必然出現的 TS2307／TS2304 模組
  與 global 缺失），並手動照專案 `.prettierrc`（printWidth 100、single quote）對齊格式、確認
  沒有超寬行。實際 `npm test`／production build 交給 CI 的 `frontend.yml` 驗證。
- 對應文件更新：`TODO.md`（「為訂閱 CTA、我的閱讀流、偏好設定與推薦回饋補前端整合測試」四項
  到齊後打勾並改寫過期的括號說明，另記錄上面那個 race 的修正）。

## 階段三十五：支援每個來源的使用者自訂名稱（2026-09-16）

TODO.md P2「資料夾與來源控制」的一項：訂閱清單裡的名稱一律來自 `feeds.title`，來源自己取的
名字不一定合用，而 `feeds.title` 是所有訂閱者與公開目錄共用的，改寫它會影響其他人。

- **`backend/migrations/021_user_feed_custom_title.sql`**：`driftread.user_feeds` 新增
  `custom_title TEXT`（nullable，`CHECK` 長度上限 200，同 `discovery_candidates.title` 的
  既有上限）。NULL 代表「沿用 feed 原本的 title」；空白字串一律在寫入前正規化成 NULL（見下），
  不讓「沒有自訂名稱」與「自訂名稱是空字串」變成兩種要分別處理的狀態。
- **`backend/models.py`**：新增 `SubscribedFeed`（`Feed` 加一個 `custom_title` 欄位，只用在
  `GET /me/feeds`——這是訂閱關係的屬性，不是 feed 本身的，故意不放進 `Feed` 本體污染其他讀取
  路徑）與 `SubscriptionUpdate`（`custom_title: str | None`，上限 200）。
- **`backend/routers/me.py`**：`list_subscriptions` 改回傳 `list[SubscribedFeed]`，一併
  select `user_feeds.custom_title`；新增 `PATCH /me/feeds/{feed_id}`
  （`update_subscription`）：先查訂閱是否存在（未訂閱回 404，同 `routers/admin.py` 既有的
  「先查存在再動作」寫法），空白／純空白字串在這裡（不是在 model）正規化成 NULL 再寫入。
- **前端**：`models.ts` 新增 `SubscribedFeed`；`MeService.listSubscriptions()` 回傳型別改為
  `SubscribedFeed[]`，新增 `updateSubscription()`。`components/my-feeds`：卡片標題改顯示
  `custom_title || title`，有自訂名稱時原本的目錄標題以「原名：」小字保留在旁，不會被完全
  蓋掉；新增「重新命名」原地編輯（`Enter` 送出、`Esc` 取消），儲存時裁剪前後空白、空白值清除
  自訂名稱，失敗不影響已顯示的清單並跳 toast。
- **測試**：`backend/tests/test_me.py` 四個案例（`GET /me/feeds` 回傳 `custom_title`、
  設定會裁剪空白、空白值清除為 NULL、未訂閱回 404 且不呼叫 `update`）；
  `backend/tests/test_me_isolation.py` 補一個跨使用者隔離案例（同其餘 `/me/*` 端點的既有
  寫法：兩個使用者各呼叫一次，斷言送進 `update().eq("user_id", ...)` 的值互不相同）；
  `frontend/src/app/components/my-feeds/my-feeds.spec.ts` 四個案例（成功設定並裁剪、空白值
  清除、失敗不動清單並跳一次 toast、取消編輯不送請求），既有的 `Feed`／`Feed[]` 測試 fixture
  與型別註記一併改成 `SubscribedFeed`／`SubscribedFeed[]`，配合 `MeService` 回傳型別的變更。
- **本 sandbox 的已知限制**（同 PR #58／#59 記錄的既有情況）：`pypi.org`／
  `registry.npmjs.org` 這次連 metadata 都被 network egress 政策擋下 403，backend 的
  `pip install`、frontend 的 `npm install` 都裝不起來，本機跑不了 `pytest`／`vitest`／
  `ng build`。backend 三個改動檔案已用 `python3 -m py_compile` 確認語法正確；frontend 四個
  改動檔案用系統 `tsc 6.0.2`（`--ignoreConfig --noResolve`）確認沒有語法或結構問題（只剩
  `--noResolve` 必然出現的模組/global 缺失噪音），並手動核對 `.prettierrc`
  （printWidth 100、single quote）與既有測試的 mock chain 寫法一致。實際
  `pytest`／`npm test`／production build 交給 CI 的 `backend.yml`／`frontend.yml` 驗證。
- 對應文件更新：`TODO.md`（「支援每個來源的使用者自訂名稱」打勾並記錄實作位置）、
  `docs/FEATURES.md`（功能總覽、`/me/feeds` API 表新增 `PATCH` 列、`user_feeds` 資料表列）。
- **PR review 後修正**（合併前）：自動 review 指出兩個 edge case——(1) 切換登入使用者時，
  重新命名編輯器的 `renamingId`／`renameValue` 沒有跟著重置，若元件維持掛載且兩人剛好訂閱
  同一個 feed，會讓後一位使用者的卡片一開就帶著前一位使用者尚未送出的自訂名稱；(2) 儲存中
  只有畫面上的按鈕被 `[disabled]` 擋住，輸入框的 `Enter`／`startRename` 本身沒有守門，同一次
  編輯可能被重複送出，較晚回來的回應可能把較新的編輯器狀態蓋掉。兩處都加上守門（`renaming()`
  guard、使用者 id 變動時清空編輯狀態），並在 `my-feeds.spec.ts` 補上對應案例。

## 階段三十六：支援來源靜音／暫停（2026-09-21）

TODO.md P2「資料夾與來源控制」的另一項：目前要讓一個訂閱的來源不再出現在「我的閱讀」，唯一
的辦法是整個取消訂閱，之後想找回來還得重新訂閱一次，會丟失既有的已讀狀態關聯（訂閱關係本身
被刪掉重建）。靜音讓讀者可以「先不看這個來源」而不必付出這個代價。

- **`backend/migrations/022_user_feed_mute.sql`**：`driftread.user_feeds` 新增
  `muted_at TIMESTAMPTZ`（nullable）。NULL 代表未靜音；非 NULL 記錄靜音當下的時間戳，同
  `feeds.archived_at`（migration 002）既有的「用時間戳而不是純布林」慣例，時間戳本身是免費
  多出來的資訊，比布林多記錄「何時」。`list_reading_stream`／`reading_stream_unread_counts`
  （皆 `CREATE OR REPLACE`，簽章不變、既有 grant 不必重下）加上 `uf.muted_at IS NULL`：已靜音
  的訂閱從「我的閱讀」的文章時間流、總未讀數與來源篩選清單中整個消失。`mark_reading_stream_read`
  刻意不改——靜音是可逆的，不該讓「這篇文章在靜音期間新增」變成「解除靜音後被追溯標記已讀」；
  `GET /me/feeds` 與取消訂閱都直接查 `user_feeds`，不受這兩個 function 的改動影響，靜音的來源
  仍照常列在「我的訂閱」。
- **`backend/models.py`**：`SubscribedFeed` 新增 `muted_at: datetime | None`；`SubscriptionUpdate`
  新增 `muted: bool | None`，與既有的 `custom_title` 各自獨立、預設都是 `None`。
- **`backend/routers/me.py`**：`list_subscriptions` 一併 select `muted_at`；`update_subscription`
  改用 `body.model_fields_set` 判斷請求 body 真的帶了哪個欄位，只更動那些欄位——避免「只想切換
  靜音」的請求因為 `custom_title` 預設是 `None` 而把已設定的自訂名稱誤清空，反之亦然；body 兩個
  欄位都沒帶則整個不呼叫 `update`。
- **前端**：`models.ts` 的 `SubscribedFeed` 新增 `muted_at`；`MeService.setMuted()` 獨立成自己的
  請求，刻意不跟 `updateSubscription` 共用一次 PATCH body，避免靜音時意外帶上記憶體裡當下的
  `custom_title` 值重新送一次。`components/my-feeds`：卡片顯示「已靜音」標籤與
  靜音／取消靜音按鈕，用 `mutingIds`（每個 feed id 各自的在途狀態）擋同一張卡片的重複點擊；
  未擋跨卡片操作，因為靜音沒有像重新命名編輯器那樣「同時只能開一個」的共用 UI 狀態需要保護。
- **測試**：`backend/tests/test_me.py` 新增 5 案例（`GET /me/feeds` 回傳 `muted_at`、靜音、
  取消靜音、只設定 `custom_title` 不動靜音狀態、空 body 整個不呼叫 `update`）；靜音的跨使用者
  隔離由既有的 `test_update_subscription_scoped_per_user` 涵蓋（走的是同一段
  `update().eq(user_id).eq(feed_id)` call path，與更新哪個欄位無關，另開一個案例不會增加涵蓋
  範圍）。`frontend/src/app/components/my-feeds/my-feeds.spec.ts` 新增 4 案例（靜音、取消靜音、
  同一張卡片在途時忽略第二次點擊、失敗不動狀態並跳一次 toast），既有 fixture 補上 `muted_at`。
- 對應文件更新：`TODO.md`（「支援來源靜音／暫停」打勾並記錄實作位置）、`docs/FEATURES.md`
  （功能總覽、`/me/feeds` API 表、`user_feeds` 資料表列、`list_reading_stream`／
  `reading_stream_unread_counts` 說明）。

## 階段三十七：擷取生命週期、中文來源與營運監控（PR #65，issue #63／#64，2026-10-08）

Review 修復：匯入保留累計文章數、空待抽取佇列跳過 HostIndex、seed 嚴格隔離一般名額、正文修訂重設保存期、舊留言／別名審核防繞過。PostgreSQL 完整 migration、SQL fixture 與多連線鎖競態納入 Backend CI。

後續 review 修復：未修改的預設設定可明確套用並入列種子；文章與首頁採集共用新 host 配額；首頁採集另記錄嘗試時間，避免文章 backlog 或失敗造成每個 tick 重抓。

補入共用 `app_settings` 表、型別設定註冊、版本衝突保護與後台全域設定頁；語言／分類探測方向取代硬編碼中文名額，儲存與新增種子在同一筆交易中完成。

文章寫入以正文／摘要 hash 跳過相同版本；逐版本抽取與正文保存解耦，失敗與截斷保留待處理狀態。新增預設關閉的正文縮減排程與預設 dry-run 的管理端點，保護訂閱、已讀、收藏／稍後讀，保留文章身份並同步全文搜尋。管理首頁新增私有 worker heartbeat／近期執行與文章資料量快照；補入多類中文種子、探測優先名額，以及留言 feed／斜線別名品質規則。部署與限制見 [CRAWLER_OPERATIONS.md](CRAWLER_OPERATIONS.md)。

## 階段三十八：專案規範改由 `AGENTS.md` 單一維護、changelog 依十位段歸檔、文件一致性檢查（PR #67，2026-10-08）

把無感記帳（Seamless Track）已經跑順的開發規範與文件紀錄方式移植過來。

- **`AGENTS.md` 成為 Claude Code 與 Codex 共用的唯一規範，`CLAUDE.md` 只剩一行 `@AGENTS.md`**：Codex 只讀 `AGENTS.md`（PR 的 Codex review 也是），
  Claude Code 有 `CLAUDE.md` 時預設只讀 `CLAUDE.md`——過去 Codex 完全看不到本專案的任何規則。原 `CLAUDE.md` 的內容全部搬進 `AGENTS.md`，另新增：
  - 「資料與外連說明」：backend 全程使用 `service_role`、使用者隔離實際靠應用層 `user_id` 條件；伺服器會主動對外連線——對外說明不可含糊帶過。
  - 「改 X 前先讀 Y」對照表：動到抓取、推薦、發現、正文保存、全域設定、schema、前端樣式、部署前分別要讀哪份文件的哪一段。
  - 快速開始、CI 一覽，以及 backend（DB 查詢、日誌、mock）、frontend（樣式、`[innerHTML]`、樂觀更新）規範的摘要，細節仍連回 `SECURITY.md`、`frontend/README.md`。
  - **migration 規則**：新檔改用 `YYYYMMDDHHMMSS_<name>.sql`、已合併的 migration 不得修改（ledger 只看檔名）、可重跑的存在性防護、`supabase/` 分層原稿與 010 逐字一致。
  - **Changelog 維護**：一個 PR 一個階段條目、review 修復併入同一條目、寫清楚根因與沒做什麼、同步更新 FEATURES／SECURITY／TODO。
  - **文件維護**：本檔與 `docs/` 不重複、現況文件 vs 歷史紀錄、註解引用文件時寫段落名稱而非章節編號。
  - **PR 語言規範**（繁體中文）與 **Code Review 原則**（finding 門檻、優先級、產品規模假設、review scope），依本專案調整：
    公開免認證端點與對第三方 URL 的外連面對不受信任的網路，外部可觸發的安全問題不適用「極端輸入不提」的放寬。
- **CHANGELOG 依十位段歸檔**：原本約 222 KB 的單檔只留最近兩個十位段（階段二十一起，約 135 KB），階段一～二十原文搬到
  `docs/changelog-archive/stages-01-10.md`、`stages-11-20.md`（只搬不改，相對連結補 `../`），搬移規則寫在本檔開頭。
  （Codex review：初版連二十一～三十也搬走，主檔實際只剩一個十位段，與「保留兩個十位段」的規則不符，已搬回。）
  檔頭原本的「尚未合併：#63／#64」已隨 PR #65 合併，改列為階段三十七並移到依時間排序的位置。
- **新增 `scripts/check_docs.py` 與 `.github/workflows/docs.yml`**：`CLAUDE.md` 只能是 `@AGENTS.md`、`AGENTS.md` 不超過 30 KiB
  （Codex 預設只讀前 32 KiB）、所有 Markdown 的相對連結都存在、`AGENTS.md` 以反引號提到的路徑都存在。
  workflow 不設路徑篩選（Codex review：只改名或刪除被引用的程式檔、沒動到 `.md` 的 PR 也會讓連結失效），每次 PR／push 都跑。
  Markdown 交給 CommonMark 解析器 `markdown-it-py`（版本釘在 `scripts/requirements-docs.txt`）而非手寫 regex：Codex review 連續指出圖片、帶標題、
  reference-style 連結與波浪號／縮排／縮排式程式碼區塊的漏判，根因是自己解析 Markdown，改用解析器一次涵蓋；程式碼區塊與行內程式碼裡的示範語法不檢查。
  另外檢查內嵌 HTML 的 `href`／`src`、拒絕逸出 repository 的相對路徑（`../../` 在 runner 上存在、在 GitHub 上是壞連結）、`.env.example` 這類 dotfile 也列入路徑檢查；`.markdown` 與大寫副檔名也掃描、略過 `//host` 協定相對網址、`docs/` 這類帶尾斜線的單層目錄也檢查（Codex review）。
- README 的文件表、CI 表（補上 `supabase/**` 觸發、`npm test`、docs workflow、sha tag）與開發規則改為指向 `AGENTS.md`；
  `SECURITY.md`、`RUNBOOK.md` 引用 `CLAUDE.md` 的地方改指 `AGENTS.md` 的「環境變數維護」。

沒有移植的部分：無感記帳的「每個 PR 疊代版本號」與 Play Store 公告流程——本專案以 `:sha-<commit>` tag 部署、沒有對外版號，不需要。

### 驗證

- `python3 scripts/check_docs.py` 通過；把 `CLAUDE.md` 改成多一行時確實失敗；臨時文件裡的壞圖片、帶標題、角括號、reference-style、清單延續段落與表格裡的壞連結都會被抓到，各種圍欄、縮排式程式碼區塊與行內程式碼裡的示範連結不會誤報；`AGENTS.md` 提到不存在的路徑時會失敗。
- 以逐行比對確認歸檔後的 CHANGELOG 與歸檔檔合起來涵蓋原檔每一行（只有被改寫的檔頭與「尚未合併」標題不同）。
- 未改動任何程式碼，backend／frontend workflow 不受影響。

## 階段三十九：依非 AI 依賴完成持久化採集、個人閱讀與消費出口（PR #81，2026-10-09）

issue #63 原 roadmap 同時涵蓋採集、內容身份、來源政策、個人閱讀與 AI。依使用者指定先排除所有 AI 功能，建立 #68–#80 共 13 個工作議題，以多個 sub-agent 分工，再依基礎依賴整合到單一 PR。父議題 checklist 與各工作議題互相連結；目前 connector 沒有寫入 GitHub 原生 sub-issue 關係的工具，未宣稱原生 parent relationship 已建立。

- 採集 cycle 改用 PostgreSQL durable queue，加入 priority、lease fencing、bounded retry／timeout、過期回收、週期 singleton 設定更新與下一輪原子入列。SIGTERM 停止接新工作、bounded drain；API watchdog 獨立監控失聯／恢復，管理頁可見佇列與私有告警。交付為 at-least-once，lease 不撤回已提交業務寫入或終止同步 thread，見 [BACKGROUND_JOBS.md](BACKGROUND_JOBS.md)。
- 保留 source-specific article id／訂閱／收藏，追加 metadata/hash revisions 與多入口 provenance，canonical URL 只移除 fragment。區分 source publication、first discovery、timeline 與不可洗成 realtime 的 backfill；初次抓取、歷史匯入與 legacy 資料不製造當前熱度／日報。沒有永久保存每版本全文，見 [CONTENT_MODEL.md](CONTENT_MODEL.md)。
- 来源 normal／signal_only／private、鏡像群、first_party、正文政策與 fetch/ok 時間加入管理表單。匯入冪等且不重設既有角色；來源健康包含沒有近期文章的來源。所有文章出口共用 publication，summary_only 即時限摘要，搜索不能由禁止正文命中／排名／出片段；raw articles 的匿名 Data API SELECT 已撤銷。cursor 綁定 user／endpoint／query，舊 cursor 須重載，見 [SOURCE_MODEL.md](SOURCE_MODEL.md) 與 [PUBLICATION.md](PUBLICATION.md)。
- 人工 Fact／Story 以版本 CAS 原子更新，永久排除、merge／alias、顯式 relation 不作自動語意擴張。個人熱度只按同 URL／人工 Story 的獨立來源與 source time 衰減，偏好主導且 lag 明示 incomplete；閱讀流提供時間／熱度選擇。本人熱度保存有界證據 snapshot，歷史修復補晚到證據但凍結當時來源健康，讀取重新套用目前權限／訂閱／回饋；不宣稱還原歷史偏好。已讀只提供有界弱訊號，探索維持 category 配額與可解釋理由，見 [NON_AI_INTELLIGENCE.md](NON_AI_INTELLIGENCE.md)。
- 確定性 daily digest 按 IANA timezone／DST 日界切分，私人 RSS 只匯出摘要／原文；官方 MCP SDK 同 FastAPI 程序提供五個唯讀工具，每請求驗 JWT。sync ledger 以交易序列鎖處理 commit race，新增 trigger 是固定 search_path、撤銷 API EXECUTE 的窄範圍 DEFINER 例外。離線擴充採摘要快取、session token、pinned connection generation、權限預檢與有界 idempotent queue，撤權／刪除後 replacement snapshot 清 cache；離線期間撤權須重連或 TTL 才生效，見 [CONSUMPTION_SURFACES.md](CONSUMPTION_SURFACES.md)。新增 extension CI 跑 account／cache lifecycle。

### 驗證與部署界限

- 最終 backend 全套含隔離 PostgreSQL 17、publication／queue／history／owner RLS／sync commit race、官方 MCP ClientSession：964 tests 通過。
- Frontend 33 files、289 tests 通過；production build 通過，initial bundle 506.28 kB 相對 500 kB warning budget 超出 6.28 kB，沒有調高預算掩蓋。
- Extension 13 Node lifecycle tests、`scripts/check_docs.py` 與 `git diff --check` 通過。
- 未驗證正式 Supabase、Docker image／compose 部署與實際 Chrome／瀏覽器流程；[RUNBOOK.md](RUNBOOK.md) 補先停舊 worker、API migrations healthy、再升級 worker／frontend 的順序。不新增環境變數或 exposed schema。
- 新增 migrations 的 baseline 升級與重跑通過；完整清空歷史 ledger 後重播所有舊 migration 仍遇到既有 006→010 public trigger dependency 問題，本次不改已合併 migration。升級須保留 ledger，不宣稱完整歷史 reset 已驗證。
- 不含模型呼叫、embedding、自動語意事件判斷、AI 摘要、模型路由／prompt／benchmark；MCP 為讀取協定。PR review 事件 hook 工具不可用，依既有偏好未建立替代輪詢。
- CI 首輪發現 `postgres` 的既有 default privileges 為新表授予 service_role ALL，單純 GRANT SELECT/INSERT 沒有移除既有 DELETE/UPDATE；本機不同 owner 原先未重現。新 revision migration 先顯式 REVOKE service_role ALL 再給 append-only 權限，真 PostgreSQL fixture 同步模擬 default grants，避免兩種 owner 環境的假通過。

### Review 修正

- 離線 preflight 與原已讀／收藏 POST 分屬兩次請求，撤權間隙可能永久寫入過期 intent。新增同交易授權的專用 replay RPC／API，以來源、本人訂閱、publication 鎖保護冪等狀態寫入；保留公開 reader 的原線上語義。批次抓取持有 sync_clock 再更新下一篇文章的正常競態可與 replay 形成反向鎖等待；真 PG 重現後加入 250ms lock timeout／exception rollback，以 503 明確重試且保留 queue，避免以 409 靜默丟掉 intent。
- 擴充 options 的訊息原本仍被 display:none 隱藏；改為每次 feedback 解開 hidden，保留 account generation guards，測試實際 HTML 初始隱藏及成功／API錯誤／權限拒絕／disconnect 可見性。
- 日報／RSS／MCP 的列表原 RPC 傳回正文／search_vector，事後才 excerpt，造成不必要的大 payload。列表與個人搜尋改 SQL metadata projection；1.95 MB 正文的回傳小於 2 KB，許可全文仍能搜尋、summary_only 禁止正文命中、單篇 reader 保留正文。未合併 RPC 以 DROP／重建處理 return type 改變與重跑。
- 人工事件公开支路直接使用 publication，archived normal 成員保留閱讀能力，private／signal-only／exclusions 仍隱藏；success-only 的重新匯入會以 GREATEST 同時推進 last_fetch_at，舊完成時間不能倒退觀測。
- 後續 review 發現候選連線尚未驗證就清掉舊快取，錯誤 token／網路失敗會遺失 pending。改為先以候選設定取得 snapshot，成功後才提交連線；失敗保留舊資料，同帳號／API 更新 token 保留 queue，切換帳號或 API 不帶入舊 queue。
- Story 合併原本僅搬 membership，原人工 relation 留在 alias 而消失。合併在同交易重掛來源及既有 aliases 的兩端關係，正規化端點、合併相同聲明並刪除等效自關係；不同聲明衝突拒絕整筆合併並回滾，讓操作者先明確處理。relation PATCH 與 merge 共用先於 row locks 的交易鎖，避免並發寫回 alias；這個鎖僅限小型人工管理操作。
- popup 曾把 browser 重啟後 session 帳號缺失當成切換帳號，先於 options 重連刪除 local queue；改為只有目前帳號確定存在且不同才清除。未連線與 TTL 過期不顯示私人摘要，但保留 pending 等待驗證／同步，補 popup → options 真 storage lifecycle 回歸。
- 原 schema reload 通知早於來源／publication／事件／熱度／sync DDL；本批最後 migration 的全部 DDL／grants 之後補 `NOTIFY pgrst, 'reload schema'`。真 PostgreSQL listener 驗證提交後才收到、rollback 無通知，且通知後可見新增物件及最終 RPC 權限；實際 migration runner 補執行最後檔及 ledger rerun 皆驗證。未宣稱正式 Supabase 的非同步 cache reload 已走查，部署驗收仍須呼叫新出口。
- RSS 查詢按校正後 timeline 排序，輸出卻優先使用原始 published_at，使錯誤的未來日期在外部 reader 長期置頂。`pubDate` 改用 timeline_at，只有舊資料缺少 timeline 時回退原始日期；補未來日期／UTC 轉換、順序、舊資料回退及無日期回歸。依使用者後續要求啟用每小時 review 檢查，本次有修正推送後保留排程供下一輪確認。
- 個人搜尋曾直接在 publication 的政策 CASE 向量上比對，無法使用原始 GIN 索引而掃描所有訂閱文章。一般正詞 AND／OR 改先用原始向量與精確摘要 expression 的 GIN 聯集做候選篩選，再以許可向量重驗命中與排名，保持本人／靜音隔離與純 metadata 回傳。摘要索引保留 500 字元裁切產生的詞，增加有界摘要索引的儲存／寫入成本；負詞／片語沿用許可向量查詢，避免禁止正文或詞位置差異讓許可結果漏掉。
- MCP 自訂 async 認證 wrapper 曾同步驗證 JWT，冷 JWKS 或金鑰輪替的 HTTPS 會阻塞 FastAPI 事件迴圈。改將同步驗證移至 threadpool，維持每次請求的永久帳號驗證、原錯誤回應與請求身份隔離，補等待驗證時其他 task 能進展的回歸。
- popup 同步寫入只在呼叫 storage 前檢查 connectionId，與 options 分頁的斷線／切帳號清除沒有共用順序；延遲寫入會復活舊帳號的摘要與 pending。連線與快取變更由擴充 service worker 單一佇列協調，佇列內確認 connectionId、等待已接受寫入完成再清除或替換；連線 intent 保存在 session 以跨 worker idle 重啟。message 限本擴充 popup／options 頁面，儲存錯誤安全回報，保留重啟後 queue 與候選連線契約。獨立三個 VM context 驗證延遲 pull／replay ack／離線按鈕對斷線及切帳號、過期 auth／cleanup、worker 重啟、儲存拒絕與 sender 限制。
- 本輪 extension 48 tests、文件檢查與 diff check 通過；backend／前端未更動，前一 head 的 backend 1000 tests（含真 PostgreSQL）、前端 289 tests 及 build 為歷史驗證。GitHub CI 結果另確認 PR 最新 commit 的 checks；正式 Supabase、Chrome 實際 lifecycle 與正式部署未執行。

## 階段四十：文章版本歷史加上每篇 10 版的上限（PR #93，issue #82，2026-10-12）

PR #81 新增的 `article_revisions` 會在文章的標題、摘要、作者、發布日期或正文 hash 變動時新增一列，但沒有任何保存期：只在刪除文章／來源時 cascade 清除，#64 的 retention 也只縮減 `articles.content`。摘要帶動態數字的 feed（例如 hnrss 的 `Points`／`# Comments`）或每次輸出都改 `pubDate` 的 generator，每次 refresh 都會新增一個版本。PR #81 之前這類變動是原地 UPDATE，現在則只增不減，等於在 #64 處理 DB 容量的同時又開了一條沒有上限的成長路徑。這是 PR #81 合併後 review 時發現的。正式環境（2026-10-12 唯讀查詢）：PR #81 的 migration 還沒套用，DB 269 MB；最近 24 小時至少 336 篇文章在首次抓取後內容又變動過。

- 新增 migration `20261012000000_article_revision_retention.sql`：`article_revisions` 加上 AFTER INSERT trigger，在寫入版本的同一 transaction 內刪掉同一篇文章 `revision_number <= 新版本 - 10` 的列。每篇保留最近 10 版（含 current），其他文章不受影響。
- 清理放在 trigger，不放在 worker 的 retention 週期：retention 預設關閉（`ARTICLE_RETENTION_ENABLED`），放在那裡的話，大多數部署仍會持續成長；trigger 也不需要額外排程或批次狀態。
- trigger 函式是窄範圍的 `SECURITY DEFINER`：service_role 對版本表仍只有 SELECT／INSERT（append-only 權限不變），函式回傳 trigger、沒有參數、撤銷所有角色的 EXECUTE。SECURITY.md 在同步 invalidation trigger 那段旁邊記下這個例外。
- 已套用 20261009 系列的資料庫，migration 會一次刪掉既有的超量舊版本；重跑時沒有可刪的列，trigger 也有存在性檢查。
- 這支 migration 成為新的最後一支，結尾同樣送出 `NOTIFY pgrst, 'reload schema'`。`tests/test_schema_reload_postgres.py` 原本寫死 `20261009000700_incremental_sync.sql` 為最後一支，改成檢查「目前最新的一支」都要帶 reload 通知；runner 測試改為只清掉最新一支的 ledger 後重跑。AGENTS.md 的 migration 規則補上這條慣例。

### 沒做什麼

- 保留數 10 是寫死的產品限制，沒有開放環境變數或全域設定。日後的分析只需要比對 current revision，需要更長歷史時再新增 migration 調整。這和 #82 原本寫的「可設定 N、由 retention 週期執行」不同，已同步更新 issue。
- 沒有減少版本的產生條件（例如忽略只有 `published_at` 變動的情況）：哪些變動算新版本屬於 #70 的語義，不在此 PR 範圍。
- `sync_changes` 會因內部欄位更新產生全域 invalidation，這是 PR #81 review 的非阻擋觀察，未處理。

### 驗證

- 新增 `backend/tests/test_article_revision_retention.py`（真 PostgreSQL，實際 migration chain）：同一篇經 `ingest_article_batch` 改 15 次摘要後只剩第 6～15 版、current 的摘要正確、同 feed 其他文章不受影響；anon／authenticated／service_role 都沒有清理函式的 EXECUTE，service_role 仍無法直接 DELETE；拿掉 trigger 後累積 14 版，套用 migration 後剩 10 版，重跑兩次結果相同。暫時移除新 migration 時第一項測試如預期失敗。
- 本機 PostgreSQL 16（CI 為 17）上，後端全套 1003 passed（含 `DRIFTREAD_TEST_DATABASE_URL` 的真 PostgreSQL 測試）；`scripts/check_docs.py` 通過。
- 沒有前端或擴充變更。正式 Supabase 套用與部署未驗證，跟 PR #81 一起由 #83 追蹤。

## 階段四十一：關閉 Driftread 自助註冊，後台移至 /manage 並加入 Telegram OTP（PR #94，2026-10-12）

原後台以共享 API Key 解鎖分頁，沒有帳號綁定與第二因素。依使用者指定，改為既有 Supabase 帳密登入 + 本人 Telegram OTP，限定唯一管理員 UUID，沿用 seamless_track 的 Vault 固定私人收件人，reference schema 未修改。

- 移除 Driftread 註冊切換與 `AuthService.signUp`；依使用者確認不關閉共用 Supabase Auth 的全域註冊。
- 前端 `/admin` 入口移除，後台改 `/manage`，登入 `/manage/login`。OTP 5 分鐘、重送 60 秒、最多錯 5 次後鎖 15 分鐘；管理 session 8 小時，綁帳號與 Supabase session，每次後台呼叫驗證、登出撤銷。API Key 不再提供給瀏覽器。
- owner row lock 原子消耗 OTP／建立 session，錯誤嘗試先提交再回錯誤。OTP bcrypt、管理 token SHA-256；新表 RLS 並撤銷所有 Data API roles（含 service_role）存取。既有 privileged DATABASE_URL 執行驗證，Vault 密鑰留在 SQL，pg_net 非同步送信並確認 Telegram 成功回應。
- 新增 `MANAGE_OWNER_USER_ID`，同步 `.env.example`、compose api、gen_env missing 檢查。部署者需填指定帳號 UUID；空白時拒絕管理登入。正式部署與驗收順序見 [RUNBOOK](RUNBOOK.md#後台-telegram-兩步驟驗證)。
- 既有 `/api/admin/*` 機器 API Key 接口保留供腳本／擴充使用；它仍是獨立授權，不經 OTP。帶管理登入 header 時不回退到 key。若要所有機器接口也強制 OTP，需另設整合認證；本次未變更該契約，也未旋轉既有 key。

### 驗證與部署界限

- 完整 backend：1026 tests 通過，包含隔離 PostgreSQL 17 的真 bcrypt、並發 OTP 一次消耗、重送冷卻、錯誤次數提交／鎖定、過期／換 session／撤銷與 Data API grants；migration runner 與新增檔重跑通過。
- Frontend：34 files、296 tests 通過；production build 通過，initial 507.91 kB 相對既有 500 kB warning budget 超出 7.91 kB，未修改預算。覆蓋無註冊入口、兩階段登入、錯碼與 cooldown、redirect、reload 還原、帳號／session 切換與登出。
- 文件檢查與 `git diff --check` 通過。唯讀查詢確認正式 Supabase 指定帳號為永久帳號且已有密碼、Vault 收件為私人 chat、pg_net／pgcrypto 既有設定；未部署正式 schema／API／frontend，未以本人帳密走查、未發送正式 Telegram 測試 OTP。真 PG 測試使用假的 Vault／pg_net 送信端，不能當成已驗證實際 Telegram 收件。
- 整合 master 已合併的 PR #93（文章版本保存上限），保留最新 migration reload 測試，真 PG 全套重跑通過；階段編號接續為四十一，依專案規則原文歸檔二十一～三十（僅相對連結調整）。
- Review 事件 hook 工具不可用；沒有建立替代輪詢排程。

### Review 過程中修掉的問題

- 原本只有同一組驗證碼錯滿 5 次才上鎖，`issue()` 每次發新碼都把 `attempts` 歸零：知道密碼的人每組只猜 4 次、等 60 秒重送，就能無限猜下去（本機真 PostgreSQL 重現：錯 12 次仍未鎖定，下一組照樣登入成功）。改為錯誤次數跨重送累計，只在上一輪鎖定期滿時歸零，驗證成功時清零。新增 `test_resend_does_not_reset_wrong_attempts`（錯 4 次 → 重送 → 再錯 1 次即鎖定）與 `test_success_and_expired_lock_reset_wrong_attempts`；原程式碼下兩項都會失敗。
- 已知取捨：錯誤次數沒有隨時間衰減，本人隔天再打錯也會延續計數，達 5 次仍會鎖 15 分鐘；成功登入一次即歸零，不另加衰減邏輯。

## 階段四十二：可替換來源收錄評審、管理 MCP 與可選人工覆核（PR #97，#96，2026-10-13）

原主動發現只提供人工候選審核及 referrer 數達標直接入庫，無法用內容樣本判斷來源價值。依使用者批准新增來源評審，作為 #84–#87 共用 AI 基礎的第一個用途，後續分類、事件與摘要可以沿用版本與付費請求語義。

- 在 `/manage/settings` 設定外部 agent／Jev 引擎、政策與門檻，人工覆核可關閉；新有效通過結果直接入庫，既有待人工結果不批次放行。人工 hold／reject／approve 與來源 metadata 始終優先。
- 專用 `/api/admin/mcp/` 使用獨立機器 key，提供 policy／request／claim／packet／submit；個人 MCP 保持原唯讀權限。官方 SDK protocol tests 驗證完整流程、strict inputs、結構化結果及無參數工具相容性。
- 候選樣本走有界 SSRF／robots／DB host-block gate，不公開 preview articles。以快照、policy／candidate version、lease token 和同交易入庫保護並發。原人工批准改為原子 RPC，不再把新核准留在半提交 approved；舊 approved recovery 保留。重複 URL 只連結，不覆寫人工整理來源。
- `source_review` queue 隨 discovery 主開關啟用，後台設定熱更新；啟用評審時阻止 referrer 自動入庫繞過。樣本／provider 工作有界退避，預算等待不發送付費請求，人工封鎖與逾期結果無法寫入。
- Jev 固定官方 endpoint／版本，以獨立品質、相關性與垃圾訊號評分，不生成文字理由。receipt 保存回應後才解析，並發安全預留滾動 minute/hour/day 預算；unknown／429／5xx 額外發送最多一次。分析結果連結 receipt、provider input／prompt／rubric hash 與 adapter version。
- 新增離線 development／holdout 比較工具；隨附資料是合成測試，不冒稱已校準的正式 gold set。快照／原始回應／快取有保存期，API watchdog 在停用評審後仍清理有限 payload。
- 新增 `SOURCE_REVIEW_MCP_KEY`、`JEV_API_KEY`，同步 `.env.example`／compose／gen_env；新增三支 service-only、RLS 零 policy、SECURITY INVOKER migration，最後一支送 schema reload。操作與回滾見 [來源評審](SOURCE_REVIEW.md) 與 [RUNBOOK](RUNBOOK.md#來源收錄評審升級96)。

### 驗證與範圍

- 本機 backend 1014 passed／130 skipped；前端 318 tests 通過，production build 通過並保留既有 initial bundle warning（507.91 kB／500 kB），沒有放寬預算。文件檢查、diff check、環境 key 產生冪等性及離線示例 CLI 通過。
- 程式 commit `a547647` 的 GitHub CI 全數通過：backend 1144 passed（含 PostgreSQL 17.11 的真 SQL／角色／交易／migration 重跑）、frontend 318 tests／production build、文件與兩個 Docker image build；PR workflow 未推送正式 image。
- 本機沒有 PostgreSQL 測試資料庫，真 SQL fixture／角色／交易／migration 重跑未在本機執行，交 GitHub CI 的 PostgreSQL 17 service 驗證。普通沙箱的 asyncio 跨執行緒喚醒限制，以 repo 外的 selector bounded polling runner 完成測試，不修改產品 async 邏輯。
- 正式 Supabase、Docker、實際瀏覽器／Jev key、付費 API 與中文評審品質未驗證；正式部署銜接 #83。未發送通知或使用正式資料庫。
- 本批只提供來源評審需要的 #84–#87 部分：文章／Story 模型 analysis publication 合成、其他模型能力／付費服務、分類／事件／推薦 gold sets 與 #88–#91 的 embedding／事件／生成摘要仍待後續實作。來源評審不取代個人推薦，不將外部 agent 的模型／prompt／成本冒稱已由本後台驗證。
- Review 事件 hook 工具不可用，沒有建立替代輪詢排程。

### CI 過程中修掉的問題

- 真 PostgreSQL fixture 發現收錄 INSERT 指定六欄但只有五個值，補上 `next_fetch_at=now()`，確保新收錄會進抓取排程。
- 政策熱更新測試的 helper 原本領取全域最舊準備任務，會誤取先建立的其他 review；改為指定 request 回傳 ID，並檢查 candidate ID，保留原本的過期任務重排與人工結果不自動放行 assertions。
- 人工保留／拒絕原本是無條件 PostgREST 更新，可能在模型已收錄後顯示拒絕成功。改為候選鎖與版本檢查的原子 RPC；已收錄時回 409，前端提示前往來源管理並重讀，不動既有 feed。補上真 PostgreSQL 兩種順序的鎖等待、rollback、版本與 alias 測試。
- Jev 認證錯誤或無效回應原本永久命中 failed receipt，修正 key 後也無法明確重試。新增 server-only 任務 retry key、已知終態失敗的新 generation 及 durable receipt binding；保留舊費用與回應，預算照常生效，背景重跑與 unknown 不解除原重試上限。
- Review 修正後本機 backend 1024 passed／142 skipped、前端 323 tests／production build 通過；最新 head PostgreSQL CI 結果由 PR 追蹤。

### 後續 review 修正

- 模型建議的分類與標籤原本原樣寫進新來源；開啟跳過人工覆核時，外部 agent 自創的分類（例如 `Technology` 對上既有 `科技`）會直接進公開目錄與推薦分類。新增 `source_review_known_category()`／`source_review_known_tags()`：分類必須已被某個來源或探測方向使用、標籤必須已被某個來源使用，否則捨棄。自動收錄與「覆核通過」都套用；人工「核准」時指定的分類不受限。沒有改為讓模型從清單中選擇，也沒有在 MCP 暴露分類清單（不在此 PR 範圍）。
- `request_due_source_reviews` 原本以 `request_source_review(cid,true)` 重新排入，連帶替每個排程任務產生 `paid_retry_key`，與「只有明確重試才有 retry key」的契約不符。`request_source_review` 新增 `p_paid_retry`（預設沿用 `p_retry`），排程傳 `false`；同一支未合併的 migration 內先 `DROP` 舊的兩參數版本，避免重載歧義。
- 結果快取 90 天後清空 `result` 但保留 identity，同輸入的明確重試會一直拿到 `receipt_cache_expired`。`reserve_ai_retry` 對「完成但 result 已清除」的 receipt 也建立新 generation；背景工作仍不靜默重新付費。候選頁提示改為說明重試會重新付費。
- 驗證：本機 PostgreSQL 16 執行 `test_source_review_postgres.py`、`test_ai_runtime_postgres.py` 共 51 passed；四個新測試在修正前皆失敗。PostgreSQL 17 交 CI。

## 階段四十三：每日文章容量歷史與平均成長率（PR #95，2026-10-13）

#64 的正文縮減與即時容量統計已由 PR #65 完成，但即時快照無法回答文章數與 DB 用量每天增加多少。本次補上獨立 `storage` queue 工作、每日持久化採樣與管理首頁歷史顯示。

- 任一既有 scheduler 啟用時，每小時檢查並保存 UTC 每天第一筆成功的 aggregate；交易 advisory lock 防止並行採樣／重試覆寫，同日已有紀錄不再掃描文章。保留最近 90 個 UTC 日，不補造停機期間的資料；沒有新增環境變數、不啟用正文縮減，全部 scheduler 關閉仍正常退出。
- `GET /api/admin/retention/history?days=30` 只讀已存紀錄，最多 90 天；兩筆採樣跨滿 24 小時才提供平均每日淨增量，按實際時間計算，保留負增量。後台顯示文章數、table／index／total／database bytes、採樣區間與最近 30 天表格；紀錄不足、沒有紀錄與載入失敗各自呈現，舊請求不得蓋掉重新整理的結果。
- 新表 RLS 零 policy、僅 service_role 存取，RPC 使用 INVOKER 與固定 search_path；新 migration 由 Supabase CLI 2.81.3 產生時間戳後移到既有 backend runner 目錄。既有 migration 不修改。
- 依使用者指示關閉 #68–#80，結案附註明確將正式部署驗收留在 #83；文件同步結案狀態。

### 驗證與部署界限

- PostgreSQL 17.10 的完整 backend：1188 tests 通過；新 SQL fixture 驗證每日去重、同日不重掃、90 天保存邊界、RLS／role grants、migration 重跑與兩個獨立連線的採樣競跑；最新 migration 的 schema reload 測試通過。
- Frontend：34 files、327 tests 通過，production build 成功。initial bundle 511.70 kB，超出既有 500 kB warning budget 11.70 kB，未修改預算；沒有 component style budget 警告。
- Chromium：以假的 Supabase／管理 session 與 API aggregate 走查 1280px／390px 的歷史表格、負增量與展開操作，無 JavaScript 錯誤；修正表格的 intrinsic width 撐大手機 grid，表格可水平捲動而整頁不溢出。這不表示已驗證正式認證或資料庫。
- 文件檢查與 `git diff --check` 通過；未部署正式 Supabase／Docker，正式跨日採樣與備份／dry-run 仍由 #83 追蹤。沒有修改既有 retention eligibility、刪文章 row、quota 預測或外部通知。
- Review 事件 hook 工具不可用，沒有建立替代輪詢排程。

### Review 過程中修掉的問題

- `storage` 已在 queue kinds 註冊，但舊 `worker_runs_kind_check` 仍只接受三種 scheduler，實際 INSERT 會被拒絕；best-effort recorder 吞掉錯誤，讓採樣的成功／失敗從 recent runs 消失。補上同支未合併 migration 的可重跑約束更新，保留原三種 kind、最新 master 的 source_review，並接受 storage，未知 kind 仍拒絕。真 PostgreSQL fixture 使用 service_role 驗證五種工作的 running → failed／succeeded 寫入與讀回；修正前會明確因 storage 的 CHECK violation 失敗。
- 整合已合併 PR #97：保留來源評審與 storage 的 timeout、loop、前端服務與型別，合併衝突已解；將本 PR 未合併的 migration 重命名為 `20261013115249_article_storage_history.sql`（由 CLI 產生），排在來源評審三支 migration 之後，避免後者重新設定 kind whitelist 時移除 storage。完整 migration chain 的 fixture 同時驗證 source_review 與 storage；本 PR 階段順延至四十三。

## 階段四十四：ChatGPT Web 來源評審 OAuth 與管理員授權（PR #101，#100，2026-10-14）

管理 MCP 原本只接受專用 X-API-Key，ChatGPT Web 新增連線介面無法提供自訂 header，選 OAuth／混合／無驗證皆不能直接接入。依使用者指示補窄權限 OAuth，沿用單管理員帳密＋Telegram 驗證，不更換既有服務 hosting 或共用 Supabase Auth 配置。

- OAuth discovery、public client DCR、S256 PKCE 與精確 callback／client／resource 綁定；public origin 只來自新 `SOURCE_REVIEW_OAUTH_BASE_URL`，同步 env 範本／compose api／gen_env。空白停用 OAuth，原專用機器 key 保留，拒絕混合憑證與普通 JWT。
- `/manage/mcp-authorize` 顯式同意／拒絕，登入回跳保留 opaque request；`/manage/mcp-connections` 顯示授權、期限、撤銷及歷史。前端檢查官方 ChatGPT callback、account switch／離頁在途回應、重複提交與清單 race；沒有把 key 或 OAuth token 放進前端設定。
- 五個 MCP 工具維持來源評審權限，`securitySchemes` 宣告來源評審與 offline_access。OAuth 不授權全域設定、人工覆寫或一般管理 REST；人工覆核開關沿用既有政策。
- 一次性 code、15 分鐘 access、30 天 grant、refresh 輪替與重放提交後撤銷；每次檢查目前管理員、Supabase session、帳號停用、resource／到期／撤銷。後台登出同步撤銷該登入 session grants，OTP session 自然到期不阻斷仍有效的 OAuth grant。
- 六張 OAuth 私有表保存憑證 hash／replay fencing 與持久 DCR 簽名金鑰，RLS 零 policy，撤銷所有 Data API roles（含 service_role），沿用 privileged DATABASE_URL。簽名金鑰須保留原值並隨資料庫備份；bearer 憑證仍只存 hash。新增可重跑 timestamp migration；本機 Supabase CLI 不可用，按既有 runner 命名規範建立，不改已合併 migration。
- nginx forwarding discovery，避免回傳 Angular HTML；no-referrer 與 X-Frame-Options DENY 保護同意頁。公開 DCR／authorize／token 有界 body／參數／限流與容量，有界清理保留必要的 replay digest。

### 驗證與界限

- 本機 backend（含 review 修復）1101 passed／183 skipped；OAuth HTTP 與官方 MCP SDK 的 key／OAuth、scope／metadata、錯誤與業務邊界測試通過。真 PostgreSQL 包含 code／refresh 競跑、重放撤銷、登出／同意競跑、角色／RLS、migration 重跑及 HTTP linking→工具→refresh→撤銷，交 CI PostgreSQL 17 驗證。
- 程式 commit `873bd46` 的 GitHub CI 全部成功：backend 1263 passed（含 PostgreSQL 17 的全部 SQL／角色／競跑與 HTTP linking fixture）、frontend 364 tests／build、docs 與兩個 Docker image build。PR workflow 未推送正式 image。
- 前端（含追加 review 修復）37 files／372 tests 通過，production build 通過；initial 511.81 kB／500 kB 保留既有 warning，不放寬預算。文件與 diff 檢查通過。普通沙箱執行緒喚醒限制以 repo 外 bounded selector runner 驗證，不更改產品 async 邏輯、不新增權限要求。
- 獨立 review 發現同意頁原本只接受 source:review，會拒絕正常 offline_access grant；改為 scope allowlist 並補測試。installed SDK 驗證 metadata、DCR、code／refresh request shape 相容。
- 真實 ChatGPT Web、本人帳密／Telegram、正式 Supabase、TLS／nginx／Docker 部署未驗收，交 #83。隔離 HTTP 測試的 browser JWT identity 與 MCP 業務資料使用假資料，不能冒稱已接上正式 Web。ChatGPT 客戶端自己的工具批准策略不由 OAuth 同意取代；MCP 不建立常駐 agent。
- 本批只提供官方 ChatGPT callbacks／public client／DCR，不實作任意 callback、CIMD URL 抓取、client_credentials 或一般用途 OAuth provider；其他 AI roadmap 範圍不變。Review 事件 hook 工具不可用，依使用者更新指示建立 PR #101 每小時檢查，持續至合併或關閉；無新進展時不通知。

### Review 過程中修掉的問題

- 接受 P1：公開 DCR 原先每次新增 client，匿名呼叫可耗盡 100 筆配額並阻斷合法連線至清理。改為 HMAC 簽名的公開 metadata，匿名註冊零 client 記錄，只有管理員同意才保存；待授權請求仍有 10 分鐘期限、容量上限與 IP 限流。
- 接受 P2：grant 清理後連帶移除閒置 client，會令 ChatGPT 快取的 client ID 無法重新授權。已同意 client 改為保留，token／grant 仍按原期限失效；原 client ID 相容。
- 新增 120 次匿名註冊後仍可同意／交換 token、閒置 61 天清理後同 ID 重新授權、簽名篡改與金鑰跨連線持久化、同意重放及最大 metadata 的 PostgreSQL 回歸測試。issued client ID 限 2,048 字元，避免索引大小限制；簽名 metadata 不加密且不包含 bearer 憑證。
- 接受追加 P2：前端比較完整 JWT 導致正常自動刷新被誤判為切換帳號，同意已消耗卻不回跳。改以穩定 user ID／auth session ID 與管理 token 隔離成功回應，舊 JWT 的延遲 401 不能清除新憑證；真帳號／登入／管理 session 切換仍拒收舊結果。
- 接受追加 P2：管理 token 過期／清除後，明確登出沒有撤銷 30 天 OAuth grant。改以已驗證 owner／Supabase session 撤銷該登入全部管理 sessions 與 grants，不依賴可選管理 token；沿用 consent 列鎖，其他登入與 owner 隔離，補空／已清理 token 的登出與同意競跑真 SQL 測試。
- 接受追加 P1：形狀正確的偽造 bearer 可在未認證前大量新建 DB 連線，佔滿共用 threadpool。加入進 DB 前每 IP 每分鐘 60 次驗證與每 process 4 個名額、獨立驗證執行緒池；超額立即 429／Retry-After，不排隊，取消請求仍待實際 worker 結束才釋放名額。補 IP 隔離、偽造 forwarded header、取消／過載及機器 key 不受驗證池阻塞的回歸。

## 階段四十五：清除重複來源並在收錄前以內容判斷鏡像與留言 feed（PR #99，2026-10-15）

正式 Supabase 用到 551 MB，超過 Free plan 的 500 MB，會被切成唯讀。唯讀查詢的結果是：跨 feed 的文章重複全部來自同站別名 feed（Ars Technica、TIME 各收了 `/feed`、`/feed/`、`/rss` 三個），同一 feed 內的重複全部來自留言 feed（truenas 一篇文章最多存成 70 列 `#comment-N`）。這幾個來源都在 2026-07-31～08-04 經由候選收錄進來，早於 10-08 加入的 URL 守門。另外 pub.dev 的套件發版通知占了 13,667 篇（33%）。

### 正式資料清理（直接在 Supabase 執行，不在 diff 內）

- 經使用者確認後，用單一 statement 封存 10 個 feed，並刪除它們的 18,435 篇文章：pub.dev、TIME 的 `/feed`、`/rss`（保留 `/feed/`）、Ars 的 `/feed/`、`/rss`（保留 `/feed`），以及 truenas、cyberinsider、iso20022、x402、gdpr.eu 的留言 feed。revisions 與 discoveries 透過 cascade 一起刪除。這些來源的訂閱、已讀、收藏都是 0。保留 feed 列當墓碑：直接刪除 feed 會讓 `discovery_candidates.feed_id` 變成 NULL，之後可能再被收錄。
- 對 `articles`、`article_revisions`、`article_discoveries` 跑 `VACUUM (FULL, ANALYZE)`。資料庫從 551 MB 降到 195 MB（articles 472 → 137 MB）。實際刪除的資料只有約 60 MB，**其餘約 290 MB 是 UPDATE 造成的 bloat**，所以空間仍會長回去。處理步驟寫進 [RUNBOOK](RUNBOOK.md#資料庫接近-supabase-容量上限)。
- 對應的 `discovery_candidates` 仍是 `imported`，沒有改。

### 入口守門

- 新增 `items_look_like_comments()` 與 `find_mirror_source()`（`services/source_identity.py`），以及 service-only RPC `find_mirror_sources`（migration `20261015040607_source_mirror_gate.sql`）。鏡像判斷看新 feed 項目的 canonical URL 和既有來源文章的**雙向**重疊（各 80%），分類 feed 才不會被當成主 feed 的鏡像；不以 host 合併。設計與套用範圍見 [SOURCE_MODEL.md](SOURCE_MODEL.md#收錄前的來源身分檢查)。
- `/discover/import`、`/me/import/opml`、`/admin/feeds/from-url` 原本完全沒有留言 feed 或別名檢查。現在建立來源前都會檢查：留言 feed 拒絕；鏡像來源在使用者路徑改為訂閱既有來源，在管理 API 回 409。前端沿用回傳 feed 的 id 標記訂閱，不需要修改。
- 候選收錄與管理 JSON 匯入不抓取，因此在排程抓取存入文章前檢查（直到來源存了 5 篇），命中就封存並記錄 `last_failure_reason`。已經有人訂閱的來源不封存。
- 查詢失敗時放行並記 warning，避免 worker 早於 migration 啟動時排程中斷。

### 沒做的事

- 沒有加入以標題判斷留言 feed（例如「Comments Feed」）：`is_comment_feed()` 的既有決策是不靠標題關鍵字；gdpr.eu 那種 0 篇文章的來源不會產生資料。
- 沒有做 `TODO.md` 的「重複來源管理畫面」與後台的批次清除功能；這次清理直接在資料庫執行。
- pub.dev 這類「不是文章」的來源沒有自動判斷規則，仍靠收錄評審。
- 沒有處理 UPDATE 造成的 bloat 本身（例如調整 autovacuum 或減少不必要的文章 UPDATE）；先用容量歷史觀察成長速度。

### 驗證

- 本機 PostgreSQL 16 的完整 backend：1205 passed、0 skipped。新增的真 PostgreSQL fixture 驗證鏡像、fragment 正規化、分類 feed 子集不命中、封存與 private 來源排除，以及 anon／authenticated 無法執行；單元測試涵蓋門檻、fail-open、首次抓取封存、已訂閱不封存，以及三條匯入路徑。PostgreSQL 17 交給 CI。
- 用正式資料唯讀驗證：拿每個啟用中來源最新 50 篇比對其他來源，清理後沒有任何重疊，現有來源不會被誤判。
- 未部署；正式環境套用 migration 後的實際攔截，要等下一次有人匯入別名或留言 feed 才看得到。

### Review 過程中修掉的問題

- **並行競跑**：同一批收錄的別名（正式資料裡 Ars 三個別名在同一秒建立）會在同一輪 `refresh_due()` 並行抓取，兩邊都在對方寫入前完成檢查而一起放行。改為檢查與寫入文章共用一把 process 內的 `asyncio.Lock`；新增的測試在拿掉鎖時會失敗。沒有改成資料庫鎖：文章寫入是多次 PostgREST 呼叫，無法放進同一個交易；跨 process 的情況寫進 SOURCE_MODEL 的限制。
- **門檻太寬**：兩個方向都以新 feed 的樣本數當分母，60% 門檻下，占主 feed 六成以上的分類 feed 會被對應到主 feed；反過來，匯入主 feed 時也可能被對應到既有的分類 feed，使用者會漏掉其他分類的文章。門檻提高到 80%；真鏡像在兩邊都正常抓取時接近 100%。
- **「最新文章」的定義**：原本用 `published_at DESC NULLS LAST`，最近沒有日期的文章會排在有舊日期的文章後面，內容相同的新別名因此被放行。改用專案既有的 `timeline_at` 排序與索引，並補真 PostgreSQL 測試。
- **證據不足就永遠不再檢查**：第一次抓取時項目少於門檻的來源會正常寫入並設定 `last_fetched_at`，之後就不再檢查。改為來源存滿 5 篇之前，每次排程抓取都重新檢查。

### 討論後不修

- **先訂閱就略過檢查**：候選收錄或 JSON 匯入建立的來源，若在第一次排程抓取前就被使用者訂閱，會略過檢查。新來源一建立就排定立即抓取，這段時間最多一個 tick（預設 5 分鐘）。要涵蓋這種情況，得在封存時把訂閱搬到鏡像來源，而這會批次改動使用者資料，代價和它防止的情況不成比例。
- 修正後本機 PostgreSQL 16 完整 backend：1208 passed、0 skipped。

### 第二輪 review

- **解除封存後立刻被重新封存**：被檢查擋下的來源沒有任何文章，原本「存了 5 篇前都再檢查」的條件會讓管理者解除封存後的下一次抓取又把它封存，SOURCE_MODEL 寫的復原流程實際不能用。改為新增 `feeds.admitted_at`：存滿 5 篇時寫入；管理者解除 `Excluded before admission` 這類封存時也寫入，視為人工覆核。migration 把已抓取過的既有來源回填為已收錄；worker 先於 migration 啟動時沿用原本行為，不寫不存在的欄位。
- **同一份 OPML 裡的別名**：兩個新網址都只建立 metadata、還沒有文章，目錄比對看不出相同，兩個都會被建立並訂閱，之後又因為有訂閱而略過排程檢查。匯入過程改為保留已處理 outline 的項目並互相比對，只訂閱第一個。
- **排程檢查沒有套用 URL 規則**：管理 JSON 匯入只建 metadata，完全依賴排程檢查；項目連結不是 `#comment-N` 的明確留言端點（`/comments/feed`、`?withcomments=1`）原本會被收錄。排程檢查改為先套 `is_comment_feed()`。
- **不修：既有來源的寫入也納入鎖**：新別名與既有來源同一輪到期時，新別名可能看不到既有來源本輪才抓到的項目。但鎖只保證互斥、不保證先後，新別名仍可能先拿到鎖，所以納入鎖解決不了這個問題，反而會讓整個 worker 的文章寫入全部序列化。這個落差與既有來源抓取間隔造成的落差相同，由 80% 門檻吸收。
- 修正後本機 PostgreSQL 16 完整 backend：1215 passed、0 skipped。

### 第三輪 review

- **小視窗 feed 會被提早標成已收錄**：檢查看的是這次抓到的項目，`admitted_at` 卻依累積存了幾篇來寫入。永遠只顯示最新 3 篇的別名，每次都因樣本不足無法判斷，累積到 5 篇後卻被標成已收錄，之後再也不檢查。改為只有某次抓取本身有 5 個以上項目（檢查真的判斷過）才寫入；更新測試，修正前會失敗。
- **不修：兩位使用者同時匯入同一站的不同別名**：需要兩個請求落在同一次外部抓取的幾秒內，屬於 AGENTS.md「Code Review 原則」列出的極端 timing；要涵蓋得把抓取後的檢查、建立、寫入、訂閱全部序列化。發生時只多一個重複來源，可用 RUNBOOK 清理。

### 協作規範

- PR #101 在 `AGENTS.md`「分支與 PR」加入的每小時 PR 檢查，是使用者給 Codex 的指示（Codex 沒有事件 hook）。依使用者要求標註為「僅 Codex 適用」；Claude Code 仍以事件 hook 訂閱為準。

### 與 master 整合

- 合併 PR #101 後，它的 `20261014010000_source_review_oauth.sql` 排在本 PR 原本的 migration 之後。比照階段四十三的作法，把本 PR 尚未合併的 migration 改名為 `20261015040607_source_mirror_gate.sql`，讓它排在所有已合併的 migration 之後，結尾的 schema reload 仍是最後一支。兩支 migration 動的是不同的表，沒有相依關係。PR #101 的條目佔用了階段四十四，本條目順延為階段四十五。

## 階段四十六：候選評審改為批次讀取，避免後台驗證連線超額（PR #102，2026-10-15）

候選頁原本在載入清單後，為每筆候選立即發出一次評審讀取；預設 20 筆就會同時進行 20 次管理驗證，各自建立 PostgreSQL 連線。正式 Supavisor 日誌在 2026-10-15 09:34:03 與 09:34:39 UTC 各記錄六次 `EMAXCONNSESSION`，session pool 上限為 15。每個失敗請求各自提示，而通知最多顯示三則，因此進頁時反覆看到三則「後台驗證服務暫時無法使用」。離線以原始前端類別重現了 20 個並行評審讀取與三則可見通知。

- 新增管理批次摘要 API，每次接受 1–50 個候選 UUID，以 PostgREST 關聯查詢各取最新一筆；沿用既有管理認證與摘要欄位過濾，不取得全域前 100 筆後再篩選，也不替每筆候選另查人工覆寫狀態。
- 前端改為清單一次、當頁評審一次；預設單頁從 21 個 HTTP 請求降為兩個。重載取消舊批次，並保留人工操作與舊回應的版本圍欄；批次失敗明確標示相關列為讀取失敗。
- 相同內容與等級的通知在顯示期間去重，螢幕閱讀器也只宣告一次；不延長通知停留時間，關閉或到期後可以再次提示。
- 不調高正式連線池、不修改全域 `DATABASE_URL`、不快取或略過管理認證；不新增 migration 或環境變數。全域 URL 仍供 migration 的跨交易 session advisory lock 使用。

### 驗證與部署界限

- 後端全套 1134 passed、186 skipped：本機未設定 `DRIFTREAD_TEST_DATABASE_URL`，真 PostgreSQL 測試交 CI。沙箱跨執行緒喚醒問題以 repo 外 selector bounded polling runner 完成測試，未改產品程式。批次 API 測試涵蓋管理認證只執行一次、1–50 個 UUID、真正 PostgREST client 產生的單次嵌入請求與摘要敏感欄位過濾；MockTransport 只驗證請求／回應契約，不冒稱已驗證真 PostgREST 執行。
- 前端 38 個測試檔、384 tests 通過，production build 通過；initial bundle 511.81 kB／500 kB 保留既有 warning。回歸測試涵蓋 20／50 筆僅一次批次、空頁、缺評審、失敗後恢復、換頁／操作取消舊回應，以及通知去重與關閉／到期後重新提示。獨立 review、文件檢查與 `git diff --check` 通過。
- 正式 Supabase 唯讀查詢確認關聯與 service_role 讀取權限齊全，並以等價 SQL 取得 20 個候選各自最新的評審。尚未部署或走查真實登入、PostgREST HTTP 與瀏覽器；部署時先升級 API，再升級 frontend。

## 階段四十七：OAuth 授權接受附加參數，修復 ChatGPT 連線中斷（PR #103，2026-10-15）

使用者已能完成 ChatGPT OAuth discovery，但進入 Driftread 授權時收到 `invalid_request`／`Unsupported request parameters`。授權端點的固定白名單會在驗證前拒絕任何額外參數；以帶附加提示的標準授權請求可重現，違反 RFC 6749 §3.1 對不識別參數應忽略的規定。使用者實際請求的附加參數名稱尚未取得，不能宣稱已重播正式瀏覽器請求。

- 授權端點忽略不識別的參數，仍精確驗證 client、官方 callback、resource、scope 與 S256 PKCE；提示不授權、不作為登入憑證、不反射至 callback、不額外抓取 URL。其他端點的輸入契約不變。
- 在解析前限制原始 query 為 8,192 bytes，保留最多 20 欄位及任何重複參數拒絕；不藉相容性修復開放無界輸入。
- 回歸測試涵蓋附加提示下錯誤的已知參數仍遭拒絕、重複附加鍵／過長 query／超量欄位；真 PostgreSQL 同意與 token 交換、HTTP linking→MCP→refresh→撤銷增加附加參數，驗證 resource／scope 與 callback 不被改寫。
- 不新增環境變數、migration 或 Supabase 設定；不修改正式部署。正式 ChatGPT Web 再次授權驗收仍由 #83 追蹤。

### 驗證與部署界限

- 本機 backend 全套 1133 passed／187 skipped；未設定本機 PostgreSQL，真 SQL 的附加參數與完整 HTTP linking 回歸交 CI PostgreSQL 17 驗證。修復前以同樣的標準授權欄位加一個 `audience` 提示，可重現相同錯誤。
- OAuth 目標測試 53 passed／36 skipped，獨立 review、文件檢查與 `git diff --check` 通過。沿用 repo 外 bounded selector runner 處理沙箱執行緒喚醒限制，不修改產品 async 行為。
- 未修改 frontend，不需要新增部署設定；正式環境需更新 API image 後，從 ChatGPT 重新開始授權。尚未代替使用者驗證正式瀏覽器／Telegram 流程。
- Review 事件 hook 工具不可用，依使用者與 AGENTS.md 的指示建立 PR #103 每小時檢查，直到合併或關閉；無新進展時不通知。

## 階段四十八：批次來源評審、待複審與可調連線有效期限（PR #104，2026-10-16）

首次 ChatGPT MCP 評審可用，但「查看任務」缺少唯讀入口、領取與提交只能逐筆；候選頁也無法集中處理等待人工覆核的結果。新增完整批次流程與待複審視圖，並依使用者補充在來源發現時提前排除留言 feed，減少無效抓取與評審成本。

- 管理 MCP 從五個擴為九個工具：唯讀候選／最新結果清單、批次申請、批次領取與批次提交。清單最多 50 筆，批次申請／提交 1–50 筆，領取預設 3、最多 5 筆；各筆保留獨立租約、快照與原有冪等政策。
- 新管理批次 REST 每批驗證一次；回報每筆成功或安全錯誤，部分失敗不回滾已成功項目。前端提供當頁全選、AI 申請／明確重試、人工批次覆核，保留失敗項目選取並阻擋舊回應覆蓋。既有人工作業核准／保留／拒絕限制同時兩個請求，避免再次耗盡管理驗證連線。
- 「待複審」依各候選最新 `awaiting_human` 評審在 SQL 端先過濾，再計數與分頁。候選仍用既有 status，不搬動歷史資料；覆核在候選鎖內驗證最新任務、結果、版本及狀態，防止過時選取收錄舊結果。
- alternate、直接 URL 與 redirect 發現路徑排除 `comments/feed/` 等明確留言端點；不占候選數，也不把政策排除當暫時錯誤重試。保留既有候選寫入／評審申請防線，不更動已收錄來源的泛用抓取政策。
- 獨立 subagent 實測正式政策與錯誤，未領取或修改正式任務。依其發現補上穩定 MCP 業務錯誤碼、工具操作規則、移除領取租約後額外候選 GET，並讓 ready 清單包含尚可重領的過期租約且排除舊政策／版本／耗盡任務。
- MCP 連線頁新增逐條編輯到期時間與永久有效。新 grant 仍預設 30 天；只有明確 null 才永久，access 仍最多 15 分鐘。修改時同步未使用 refresh 的期限，避免延長 grant 後連線仍被舊 token 期限中斷。永久授權仍可撤銷，並保留 owner、原 Supabase session、登出與 replay 檢查；過期／撤銷不復活。新增 `20261016050000_review_oauth_expiry.sql`，調整 grant／refresh nullable，保留 access 必須有期限的約束與既有私有表權限。
- Review 發現批次重試只傳候選 ID，可能取消 worker 已建立的新任務。新增 `20261016050100_review_retry_fencing.sql`，單筆與批次明確重試依選定 review ID，在政策→候選→任務鎖內驗證最新終態，衝突回 409，不取消新租約或多建付費重試 key。
- 新 migration `20261016033310_review_candidate_listing.sql` 加入索引與 service_role-only RPC／較嚴格覆核檢查；不新增環境變數、不變更權限範圍、不強制 MCP 付費重試。永久授權的回滾需先處理 null 歷史或停用 OAuth，詳見 RUNBOOK。既有有效分類／標籤詞彙查詢留為 P3 後續改善，未知建議仍依原政策捨棄。

### 驗證與部署界限

- 本機 PostgreSQL 17 跑完整 backend：1413 passed、0 skipped；frontend 38 files／439 tests 通過，production build 通過（既有 initial bundle warning：511.82 kB／500 kB）。文件檢查與 git diff --check 通過。測試需開啟本機 socket 執行權限；未修改產品 async 行為。
- 正式 MCP 舊版唯讀測試成功；新工具以官方 SDK、本機隔離 PostgreSQL 17 與單元測試驗證。真實 ChatGPT／瀏覽器、正式 Supabase／PostgREST、Telegram 與正式部署尚未驗收。
- 先更新 API 套用 migration，healthy 後更新 frontend／worker，再刷新 ChatGPT 工具列表；不直接部署或合併 master。
- GitHub review 事件 hook 工具不可用；依 AGENTS.md 既有 Codex 規範建立 PR #104 每小時追蹤，合併或關閉後停止，無新進展不通知。

## 階段四十九：來源管道診斷、AI 人工決策與近期收錄（PR #107、#105，2026-10-16）

#106 的零文章、stale 與 provider 設定失敗屬於不同問題。本次先用正式唯讀 SQL 核對：214 個來源的文章快取與實際關聯數全部一致，零文章已從較早快照的 100 個降至 40 個，其中 36 個封存、3 個啟用且歷史原因不明、1 個啟用且抓取失敗但錯誤文字空白；不能把封存與准入排除當成新抓取故障。時間點、至少十個來源證據及可重跑的唯讀 SQL 見 [調查紀錄](ISSUE_106.md)。

- 一般申請原本會沿用 stale／failed 任務，造成 MCP「申請成功」但未重新入列。現在在既有政策→候選→任務鎖內重建 stale／舊政策未完成任務，並允許舊 Jev 缺金鑰失敗轉交目前的 external_agent；以 `request_action` 區分新建、重排與沿用。保留舊任務、人工決定、租約隔離及 receipt，不建立普通恢復的 paid retry key；其他失敗與付費結果未知不自動重買。管理頁提供安全重新申請，`review_policy.queue` 以各 pending 候選最新任務提供積壓／缺金鑰訊號，沒有新增通知外送。
- `last_fetch_result` 記錄嘗試中、逾時、HTTP／解析錯誤、空 feed、缺網址、准入排除、寫入失敗與文章變化，含解析／批內重複／新增／總數。原始外連與 DB 例外不進新診斷；後台區分歷史原因未知及等待抓取，顯示下次時間。保存／計數失敗會退避，不接受未知精確計數為 0、不保存未完成抓取的新 validator。零文章排程與管理單筆更新重新下載 body；管理更新成功時重算文章快取，未對正式資料執行修復。
- 完整符合 WordPress 預設起始文章的樣本不滿足有效內容門檻，SQL 提交時再次確認實際證據，沒有提示的舊快照也適用；只剩示範內容時暫緩，不封鎖 host，保留人工核准。同標題真實文章與僅有摘要來源不一概當成垃圾。
- 新 migration `20261016122206_source_diagnostics_recovery.sql` 可重跑；新增函式為 INVOKER 且僅 service_role 可執行。先停舊 worker，由 API 套 migration、healthy 後更新 worker／frontend，再刷新 MCP 說明；不新增環境變數或 Dashboard 設定。

### 驗證與尚待驗收

- 本機 PostgreSQL 17 完整後端 1433 passed、0 skipped；前端 38 files／441 tests 通過，production build 通過（既有 initial bundle warning：511.82 kB／500 kB），文件一致性與 `git diff --check` 通過。新增 SQL 測試包含 migration 重跑、角色權限、並行重排、政策升版、人工優先、跨引擎恢復、預設內容與候選→評審→准入→刷新→真實文章入庫→公開詳情的整合流程；第三方 HTTP 回應使用替身，資料庫操作經測試轉接器執行真實 SQL。
- 正式 Supabase 僅執行唯讀查核，未更新 schema／來源／評審／receipt，也未觸發 Jev。正式網站網域未列入環境網路政策，正式 PostgREST／瀏覽器、三個歷史原因不明來源的重新抓取、Docker 部署及 ChatGPT 新版工具刷新均未驗證。#106 保持開啟追蹤；不批次刪除或合併歷史別名、不還原較早 100 個來源的歷史原因。
- GitHub review 事件 hook 工具不可用；建立 PR 後檢查現有 review，並依既有 Codex 專案規範設定每小時追蹤，合併或關閉後停止，無新進展不通知。

### PR #105：AI 人工決策與近期收錄

舊頁面把 AI 已完成評審的候選同時放上「核准／拒絕」與「覆核通過／不通過」，混淆是否認同 AI 與最後收錄來源兩件事。評審結果也混在來源小字中，不易看出 MCP 已經做了什麼。改以 AI 建議為卡片主體，再由人工同意或拒絕該判定，清楚顯示即將發生的處置。

- MCP／Jev、AI 收錄／不收錄／暫緩建議、理由、評分與時間分層呈現，理由完整保留、已知代碼中文化、未知代碼保留原文；以文字加色條辨識，不只靠顏色。設定區收合，僅供無 AI 決策／已保留來源的手動操作。
- 有效 AI 結果只顯示「同意決策／拒絕決策」。AI 建議不收錄時，同意會拒收、拒絕會改為收錄，寫入前確認實際後果。AI abstain 的不同意需明選人工收錄或不收錄，不能把拒絕 AI 一律當拒收來源。批次逐筆依各自 AI 結果計算，混合建議不硬套同一個 final decision。
- 新增管理單筆／批次 judgment API 與 `source_reviews.human_review`；AI `result` 不變，另存人工同意／不同意、原 AI 判定、最終處置與時間。新 SQL 在候選／評審鎖內驗證最新任務、候選版本、有效結果與人工覆寫；原 resolve 保留相容並推導同一人工紀錄，MCP 不取得人工決策權限。
- 待複審視圖涵蓋最新且版本一致、尚無人工決策的 AI 拒收與暫緩；不把 result=null 的服務錯誤混為 AI 判定。AI 建議收錄但被政策暫緩時，UI／SQL 均不允許直接同意收錄。失效或在途結果禁止重複手動繞過，失敗／暫緩且無 AI 決策保留人工備援；已保留節點仍可透過獨立「調整人工決定」改為收錄。
- 新 migration `20261016090000_review_human_judgment.sql` 僅擴充既有表與 service_role-only RPC，不修改已合併 migration，不新增環境變數，不回填歷史人工意圖、不重新評審或改動正式候選。
- 候選頁新增「近期收錄」獨立區塊與頁首跳轉，顯示最新 10 個目錄來源、時間、AI 自動／人工同意或推翻 AI／人工直接／排程收錄方式與引擎；可收合、單獨重載，成功收錄後只刷新一次。錯誤保留上次清單，不影響候選操作。
- 新 migration `20261016140000_recent_admissions.sql` 在候選真正新增 feed 的同一交易保存 service-only 首次收錄證據；重新發現／連結既有來源不覆寫，評審清理不丟失方式。管理唯讀清單最多 50 筆，以 `feeds.created_at` 表示進入目錄時間，不混用來源抓取檢查的 `feeds.admitted_at`。歷史及 JSON／URL／OPML 等其他匯入未記錄方式，標示「歷史／其他管道（未記錄）」，不以目前政策推測。

### 驗證與部署界限

- 整合主分支來源診斷後，本機 PostgreSQL 17 完整 backend：1519 passed、0 skipped；frontend 39 files／480 tests 通過，production build 通過（既有 initial bundle warning：511.85 kB／500 kB，元件樣式預算通過）。文件檢查與 git diff --check 通過。
- 實際 Chromium／Playwright 以本機假資料驗證桌機 1440px、手機 390px 與深／淺主題，無水平溢位或頁面錯誤；驗證拒絕 AI 拒收會提議收錄、取消不送寫入、abstain 明選與混合批次不帶硬編碼最終處置；近期收錄六種標記、頁內跳轉、收合不重查、讀取失敗保留舊清單與單獨重新整理均通過。模擬 409 走錯誤恢復，未對正式環境寫入，不能當作正式登入／API 已部署驗收。
- 獨立 subagent 檢查決策語義、批次、鎖、權限與回歸測試，未發現新增阻擋問題。部署先更新 API 套用 migration，再更新 frontend；來源評審 MCP 九個工具與權限範圍保持不變。
- GitHub review事件hook工具不可用；依AGENTS.md既有規範建立PR #105每小時追蹤，合併或關閉後停止，無新進展不通知。

## 階段五十：小批次 MCP 驗收與文章計數交易修復（PR #108，2026-10-17）

使用者要求對 #106 實際處理少量來源後確認。本次正式 MCP 僅提交三筆（收錄／拒收留言／暫緩各一），新收錄來源由 worker 成功入庫 10 篇且計數一致；兩筆舊 Jev 缺金鑰失敗普通申請成功轉 external_agent 並進入 ready，積壓 186→184，未建立 paid retry key。具體來源、時間點與界限見 [調查紀錄](ISSUE_106.md)。

- 正式查核發現 Notebookcheck News、NBC Sports 各少算一篇，兩者皆為 ingest_failed。只重算這兩筆衍生計數，不清除失敗、不改文章或訂閱；22:01（UTC+8）全目錄 442 個來源的快取與實際合計皆為 40,510，逐來源落差 0。
- 本機以舊 SQL 重現成功入庫後快取 0／實際 1。計數原本在 Python 完成整輪最後才寫回；分批提交後的失敗留下已入庫文章與舊計數。新 migration `20261017135504_atomic_feed_article_counts.sql` 在每批入庫交易內，以來源列鎖重算計數，保留原版本／provenance 原子性；Python 不再回寫先前讀取的 COUNT。鎖只涵蓋有限 SQL 批次，不跨外連、抽取或語言判定。
- 新 service_role-only／INVOKER 重算 RPC 供刷新（含 304）與 discover/import 修復歷史落差；未知計數禁止當 0。上輪入庫失敗時，已有文章也無條件下載，以免 validator 掩蓋未完成入庫。診斷與安全日誌記錄固定失敗階段，不保存原始例外。唯讀 audit 腳本另列所有非零／零計數落差及零文章來源診斷。
- 本機 PostgreSQL 18.4 完整 backend：1531 passed、0 skipped；新增真 SQL 回歸覆蓋部分批次失敗、metadata 失敗後恢復、鎖等待、重算、重複入庫不膨脹、rollback、權限與 migration 重跑。原版／修正版對照分別為 0／1 與 1／1。未修改 frontend；文件檢查與 git diff --check 通過。
- 未部署新 migration／API／worker，不直接合併 master。先停舊 worker，由 API 套 migration 並 healthy 後啟動同版 worker。舊正式 ingest_failed 無階段，尚無證據能定位每次底層例外；不把本機注入例外當作正式根因。其餘 184 筆缺金鑰任務、歷史留言／別名、零文章逐筆分類、正式網站與 API／瀏覽器驗收仍由 #106 追蹤。

- GitHub review 事件 hook 工具不可用；依 AGENTS.md 的既有 Codex 規範設定 PR #108 每小時追蹤，合併或關閉後停止，無新進展不通知。

## 階段五十一：後台批量清理、探索管道控制與 MCP（PR #109，2026-10-18）

來源及文章累積相同性質內容後，原後台只能逐筆封存來源，待探索前沿也只有封鎖整個網域，缺少集中整理與單一網址取消。新增兩個管理頁與共用 REST／MCP 操作，處理前先顯示可檢查的分組證據及影響範圍。

- `/manage/cleanup` 依來源分類、網域、標題、文章 canonical URL 或正文 hash 整理，支援搜尋、使用中／暫存篩選、當頁選取及每批最多 50 筆。來源暫存沿用封存，文章暫存保留原 ID、版本與使用者資料，統一 publication 隱藏並可還原。永久刪除必須先暫存及明確確認，前端先 dry-run 顯示文章／訂閱／收藏／已讀影響數；部分業務衝突逐筆回報，保留失敗選取。
- 新私有文章刪除標記與來源列鎖，避免文章被後續或在途 RSS ingestion 重新插回；文章刪除重算來源計數。來源刪除依既有 FK 清除文章及使用者關聯，不轉移／合併個人資料。
- `/manage/pending-sources` 顯示等待探索／重試的 URL、引用證據、發現方式、下次執行及失敗原因，支援單筆／批次移除。removed 標記以 URL 為界，不封鎖同 host 其他 URL、不因收割立即重新入列。worker 執行前重查，候選 INSERT 與取消共用列鎖，晚到更新不能復活；已送出的網路請求仍可能完成。
- 總覽與待探索頁新增共用的自動探索暫停／啟用面板，以 `app_settings.discovery.control` 與版本檢查即時控制 API／worker，不必重啟。保留佇列與既有 RSS 更新；進行中的探索通常在一秒輪詢內取消，已送出的網路／資料庫工作可能完成。取樣與 Jev 停止啟動新工作，在途付費結果仍可保存，但暫停期間 AI 收錄轉待人工；SQL 共享設定列鎖序列化暫停與自動准入。環境變數仍是部署上限，故預設關閉的部署不能由後台自行開啟。
- 管理 MCP 新增 `get_discovery_control`、`set_discovery_enabled`、`list_cleanup_items`、`apply_cleanup_batch`、`list_pending_discovery_targets`、`remove_pending_discovery_targets`。新增獨立 `catalog:manage` OAuth scope、明確同意頁及可選 `CATALOG_MANAGEMENT_MCP_KEY`；既有來源評審 key／grant 不擴權，新的管理 key 也不能執行來源評審。個人唯讀 MCP 保持原範圍。
- migrations `20261018033131_admin_cleanup.sql` 與 `20261018034910_discovery_control.sql` 由 Supabase CLI 建立檔名，再放入專案既有 migration runner；新表 RLS 零公開 policy，RPC 為 INVOKER 且僅授權 service_role。先停舊 worker，由 API 套兩支 migration，再更新 worker／frontend；沒有對正式資料執行清理或啟用額外爬取。操作、限制及部署見 [後台清理](ADMIN_CLEANUP.md)。
- 未加入語意模型、背景自動刪除或跨來源資料合併；分類／同網域只是整理證據，不代表低品質。來源暫存保留既有文章可讀，文章計數仍含暫存實體列。沒有解除 removed 的操作，未阻止管理員未來明確重新匯入已刪來源。

- Review 修正：初始 OAuth challenge 與省略 scope 的註冊預設同步要求 `catalog:manage`，明確指定的窄 scope 及舊 grant 不自動擴權。移除前在目標列鎖內檢查既有候選，已完成探測但尚未更新狀態時回報逐筆衝突；真 PostgreSQL 雙連線驗證候選先寫入與取消先鎖定兩種順序，避免假成功。

驗證：本地 PostgreSQL 17 完整 backend 1563 passed、frontend 42 files／492 tests 與 production build 通過；另驗證 service_role 真實寫入、migration 重跑、刪除 cascade、取消與在途 INSERT／文章 ingestion 的鎖競跑、官方 MCP SDK 的 key／OAuth 權限分隔、控制設定失敗即停止與並行版本衝突、暫停與自動准入／AI 結果的 SQL 鎖，以及前端舊回應與確認取消／失敗選取。自行檢查時補上 removed 後仍可明確封鎖網域的狀態轉換，避免前沿頁顯示封鎖成功卻無效；晚到候選同樣拒絕。文件與 diff 檢查通過。Build 保留既有 initial bundle 警告（511.85 kB／500 kB）；實際瀏覽器畫面未驗證（環境未安裝 Chromium），正式 Supabase／部署與 ChatGPT 重新授權未驗證。review 事件 hook 工具不可用，依 AGENTS.md 建立每小時追蹤，合併／關閉後停止，無新進展不通知。

## 階段五十二：Offbeat token 對齊設計系統 v1（PR #110，2026-10-20）

五個專案（Driftread、Lazyrhythm、WorldPane、LazyEmbed、無感記帳）各自沿用不同時期的 Offbeat 色值，這一輪以 lazyrhythm 的 Offbeat 設計系統（tokens v1）為唯一標準統一。Driftread 是最接近的一個，差在文字 token 只量了 `surface-base`，卡片內（raised）與標題欄（sunken）的小字掉到 AA 以下。

- `_tokens.scss`：深色 `--text-dim` 由舊 dim `#9aa0ad`（sunken 3.29:1）改為 `#b8bdc8`（6.63／5.34／4.58）；深色 `--accent`／`--info-ink` 由 frost1（sunken 4.31:1）改為提亮一階的 `#93c6d4`，frost1 仍是主按鈕與 checkbox 的填色。
- 深色 `--success-ink`／`--caution-ink`／`--special-ink` 不再直接用 Aurora 原色，改為各自提亮的 `*-ink-dark`；`--danger-ink` 由 `#e08b93` 改為 `#f4abb2`。淺色 `success-ink`、`special-ink` 修正為 `#4d683a`、`#755770`。
- 新增 `--border-control`（深 `#838ea6`／淺 `#76859c`，≥ 3:1），幽靈按鈕與 checkbox 外框改用它；`--border` 維持裝飾性的低對比。
- 新增 `--danger-fill-hover`（`#8f3a42`，6.39:1）：危險按鈕 hover 原本換回 aurora_red，snow2 字只剩 3.55:1。
- `frontend/README.md` 的「對比度」補上標準來源與三層底色原則。

- Review 修正：`--border-control` 只在 base／raised 上 ≥ 3:1，幽靈按鈕 hover 換到 sunken 底時外框掉到 2.6:1，hover 時外框改用 `--accent`（sunken 上 4.63:1）；後台 AI 結果區塊本身是 sunken 底，靜止的幽靈按鈕一樣掉到 2.6:1，因此幽靈按鈕改為自帶 `--surface-raised` 底，不再透明，外框對比不再取決於所在容器。後台網域群組的標題欄也是 sunken 底、裡面有 checkbox，問題的根因是設計系統的 `border-control`（`#838ea6`／`#76859c`）只保證 base／raised，因此把它各調一階為 `#919bb1`／`#707e95`，三層底色都 ≥ 3:1；這組值已回寫到 Offbeat 設計系統，五個專案同步更新。

沒做的事：元件結構、圓角（已是 0）、偏移陰影、字族與字級都已符合設計系統，不動；只改色值，不改語意 token 名稱。

驗證：`npm test`（42 files／492 tests）與 `npm run build` 通過；build 的 initial bundle 預算警告（512 kB／500 kB）在本 PR 之前就存在，與色值無關。未在瀏覽器實際目視比對。

## 階段五十三：來源評審與探索開關解耦（PR #112，2026-10-20）

#111 的根因是探索暫停同時阻擋評審排程、採樣與 AI metadata 准入，SQL 還將有效 accept 改為 awaiting_human，覆蓋 skip_human_review 的語義。新增 migration `20261020080000_review_independence.sql`，僅讓 referrer_auto 探索收錄遵守 discovery pause；AI 收錄與人工覆核政策獨立，保留有效樣本、品質／信心／相關性／垃圾門檻、租約、快照、候選版本、人工覆寫及冪等交易。

- worker 常駐監看 source.review，即使 FEED_DISCOVERY_ENABLED=false 也能準備既有候選及執行獨立評審；評審停用時不啟動新採樣或付費，skip 不啟動評審或探索。每 hop 仍驗證政策及來源安全防線，首次抓取沿用 refresh。其他部署排程全關時仍有評審監看及本地容量工作，需要可用 DB；不新增環境變數。
- MCP review_policy 的評審可用性改由 source.review 與引擎憑證決定，探索部署／暫停僅作獨立診斷；探索狀態讀取錯誤不阻止讀取評審政策。後台設定與探索面板說明同步更新。
- 新提交記錄 gate_reason。歷史不自動回填；reconcile_source_review 預設 dry-run，只有舊 policy skip=true／AI accept 與 migration ledger 提交時段等證據明確，且目前政策、候選版本、最新任務、無人工處置、有效快照與原始 analysis_results、品質門檻均通過，才允許逐筆修復。保留原結果，另記 reconciled_discovery_pause；重跑不重複收錄、不重送模型請求。預覽／逐筆 apply 腳本見 scripts/reconcile_source_review_holds.py。
- 2026-10-20 15:11–15:12（UTC+8）正式 Supabase 僅 SELECT 查核：source.review v5 enabled=true／skip=true，discovery.control v2 enabled=false。99 筆 awaiting_human 中，68 筆依舊 SQL 與 ledger 證據屬探索暫停，68 筆均通過目前修復資格預檢；其餘 31 筆為原 policy skip=false，政策版本較舊，不列入修復。沒有人工覆寫、候選變更或過期／缺失快照混入那 68 筆。此為升級前等價查詢預覽，部署後必須再以新 RPC 重新檢查；未更新正式 schema、評審或來源，未將 99 筆無差別放行。
- 本機 PostgreSQL 17.11 完整 backend：1610 passed、0 skipped；frontend 42 files／492 tests 與 production build 通過（既有 initial bundle warning：511.85 kB／500 kB）。覆蓋探索／skip 四格、部署開關與 review.enabled／skip 組合、MCP 官方 SDK 及 REST、品質與租約保護、service_role-only 權限、migration 重跑不改設定、真實舊 SQL 產生 hold 的 dry-run／逐筆 apply 與原始結果冪等、CLI 原因統計及保留人工覆核。文件檢查與 git diff --check 通過。
- 正式部署、Supabase 新 migration／RPC、實際瀏覽器與 Jev 付費請求未驗證；不自動變更正式開關或修復那 68 筆。部署先停舊 worker，由 API 套 migration 並 healthy 後啟動同版 worker／frontend。

- Docker 補充驗證：原 Dockerfile 的線上安裝受容器 DNS／proxy 解析限制而未成功；使用相同 Python 3.12 base、相同 requirements 與原始碼，在暫存 context 以預下載 wheel 離線建置測試 image。容器內 140 項評審／探索／MCP／API／歷史修復 PostgreSQL 測試通過；另兩次重新建立容器並重跑正式 migration runner，確認同一隔離測試 DB 的探索暫停、評審政策、版本與時間戳逐字保留。不是正式部署或正式資料庫重啟驗收。
- GitHub review 事件 hook 工具不可用；已建立 PR #112 每小時追蹤，合併／關閉後停止，無實質新進展不通知。初次查核無 review／未解決 thread，GitHub 自動 review 正在執行。

- Review 修正（P2）：README 與 .env.example 仍誤稱 FEED_REFRESH_ENABLED=false 會讓 worker 退出，RUNBOOK 每日容量段也保留三排程全關即退出的舊契約。同步改為只停 refresh，說明評審監看與容量採樣仍存取 DB；完全停用使用 docker compose stop worker，後續部署使用 --scale worker=0，並註明單次 stop 會被後續完整 up 重新啟動。Compose 與 gen_env 註解同步，沒有變更設定值或執行行為。文件檢查、diff 檢查與 gen_env Python 語法檢查通過；本次僅文件／註解，未重跑既有程式測試、未操作正式容器或資料。

- 主分支同步：納入已合併 PR #110 的 Offbeat token／控制項對比修正，完整保留其歷史條目與樣式；只解決雙方於 changelog 檔尾追加的衝突。#110 已使用階段五十二，因此本 PR 原條目順延為五十三，仍維持一個 PR 一個條目，不新增第二份修復紀錄。來源評審、歷史修復資格與正式開關均未變更。同步後前端 42 files／492 tests 與 production build 通過（initial bundle 512.45 kB／500 kB，延續主分支既有警告），文件與 diff 檢查通過；backend 與先前通過 1610 tests 的版本逐字相同，未重跑後端或 Docker 測試。

## 階段五十四：大量文章來源永久刪除逾時修復（PR #113，2026-10-20）

正式「Українська правда」的永久刪除在 REST 顯示 500，管理 MCP 也失敗。Supabase postgres logs 確認 `apply_cleanup_batch` 的來源 cascade 遇到 statement timeout；回滾量測刪除剩餘 4,990 篇與版本／探索關聯約 7.5 秒，逼近 PostgREST 預設短逾時。清單快取原顯示 5,990 篇，以 dry-run 實際計數作準。

- REST／MCP 共用 cleanup adapter 的正式刪除改透過既有 DATABASE_URL 呼叫同一 INVOKER RPC，SET LOCAL ROLE service_role 保持原權限。連線 5 秒、SQL 45 秒、等鎖 3 秒；逾時／死鎖或連線問題回安全的 503，失敗整批回滾並關閉連線。預覽、暫存、還原、明確 ID／確認與來源列鎖維持原契約。
- 新增 `20261020132503_cleanup_cascade_indexes.sql`（Supabase CLI 產生檔名），補七個來源／文章外鍵索引。保留舊 migration，不改 RLS／公開權限，不調高全域或 Data API 角色逾時。
- 已以 Supabase MCP、service_role 與原 RPC 成功永久刪除指定來源及剩餘 4,990 篇，零訂閱／收藏／已讀受影響；管理 MCP 搜尋為 0，資料庫確認來源、文章與來源探索證據均為 0。七個索引已直接套用正式資料庫；API 部署時 runner 可冪等補記專案 ledger。

驗證：本地 PostgreSQL 18.4 同步 PR #112 後全部 backend **1618 passed**；包含短逾時失敗／回滾後以新 adapter 成功、未暫存仍拒絕、service_role cascade／匿名拒絕、索引重跑、錯誤訊息不洩漏與連線收尾。文件檢查與 diff 檢查通過。正式 security／performance advisors 已讀取，本次七個外鍵未再列缺索引；其餘既有及其他 schema 的告警未擴大處理，參見 [外鍵索引診斷](https://supabase.com/docs/guides/database/database-linter?lint=0001_unindexed_foreign_keys)。未更動 frontend，未執行瀏覽器驗收；API image 尚未部署，須合併並更新 API 才啟用新 adapter。未加入背景刪除、改寫同步 ledger 或修復全站文章計數快取；極大量單批仍受 45 秒上限，逾時需縮小批次。GitHub review 事件 hook 工具不可用，依既有 Codex 規範另設每小時追蹤。

## 階段五十五：探索暫停涵蓋文章入庫，並停止 pubDate 重送的寫入放大（PR #115，issue #114，2026-10-21）

#114 的唯讀診斷指出：後台已暫停自動探索（`discovery.control.enabled=false`），`discovery_targets` 仍持續新增 `article_link`；`article_revisions` 24 小時內 1,704 筆只差 `published_at`，單一來源（Korea Times，15 分鐘間隔）就占 1,589 筆。本 PR 依 issue 建議先修 A、B，並順手處理同一支 SQL 函式裡的 C；D、E 的政策與容量治理需要使用者決定，留給後續。

**A：探索暫停沒有涵蓋文章入庫路徑（已修）**

- 根因成立：`upsert_articles()` 只看部署開關 `FEED_DISCOVERY_ENABLED`，沒看持久化的 `discovery.control`。worker 的 discovery cycle 有被 `can_start()`／`while_enabled()` 擋下，但 RSS refresh 與 discover/import 走的是文章入庫路徑，照常解析外連並經 `harvest_pending_articles()` 寫入 frontier。逐一檢查其他寫 `discovery_targets`／referrer／候選的入口（`link_harvest.harvest_one`、`directory_sources`、`discovery_probe.record_candidates`）都在 discovery cycle 內，已受暫停控制；唯一的例外是管理員手動 seed（`POST /admin/discovery/targets`），屬明確的人工操作，維持不擋。
- 修法：新增 `_discovery_active()`，部署開關與持久化開關都開才做外連解析與採集。每 200 篇一批讀一次開關（不逐篇查詢），已讀到啟用的那一批可以完成、下一批起生效；讀取失敗視同暫停並記 warning，不讓 refresh 失敗。文章本身照常保存，`FEED_REFRESH_ENABLED` 與 `source.review` 都不受影響。
- 副作用（已寫進 GLOBAL_SETTINGS／CRAWLER_OPERATIONS）：暫停期間入庫的版本留在待抽取佇列，恢復後由 backlog 補抽；但正文縮減要求「完整抽取過目前版本」，所以暫停越久，能縮減的正文越少。這和 `FEED_DISCOVERY_ENABLED=false` 的既有行為一致，本 PR 沒有改變縮減條件，列為待決策。

**B：純 `published_at` 變動造成寫入放大（已修）**

- 根因成立：`ingest_article_batch` 的 `ON CONFLICT ... WHERE` 與 `prepare_article_revision()` 都把 `published_at` 算進「有變動」。每次 restamp 都會 UPDATE 文章列（MVCC 舊列、`search_vector`／GIN 維護）、新增版本，並觸發 `sync_articles` → `sync_changes` 的全域失效（`user_id` 為 NULL，所有使用者的增量同步都會被判定有變更）。
- 新 migration `20261021100000_ingest_write_amplification.sql` 只替換 `ingest_article_batch`：正文 hash／標題／摘要／作者都沒變時，`published_at` 的改變（含變成 NULL）不寫入，保留第一次取得的值；原本 NULL、來源補上日期則寫入一次；其他欄位有真正變動時，`published_at` 一併採用來源當下的值。revision trigger 沒改，因為非入庫路徑（抽取標記、正文縮減）本來就不動 `published_at`。
- 代價：只改日期、其他完全不變的純日期更正不再反映。`timeline_at` 取 `least(published_at, discovered_at)`，往後挪的日期本來就不影響時間流排序；往前修正則會遺失。是否接受純日期更正列為待決策，要改只需新增一支 migration 調整 `CASE`。
- `feed_refresh` 的自適應間隔是用文章數差計算 `new_articles`，不看 `touched`，所以 Korea Times 的 15 分鐘間隔來自真實的新文章，本 PR 不影響排程。

**C：`article_discoveries` 每次 refresh 都更新（已修寫入面）**

- 同一支函式的 provenance upsert 改成：觀測 hash 改變立即更新；否則 `last_seen_at` 超過 6 小時才更新。查過 repo 內沒有程式或文件讀 `article_discoveries.last_seen_at`，`(article_id,origin,source_url)` 唯一性與多入口證據不變。15 分鐘間隔的來源寫入量約降為 1/24。

**沒做的事（D、E 與決策清單）**

- 沒有刪除任何文章、索引，也沒有改正文保存語意或縮減條件。沒有加每來源每日入庫上限、異常 revision-rate 偵測或自動封存；issue 的數據顯示近期新增主要是真實新聞（2,800 篇中 2,618 篇發布與入庫相差不到一天），限額屬產品政策。
- 沒有啟用 retention 排程，也沒動 `ARTICLE_RETENTION_ENABLED` 預設值；正式容器的實際值本 PR 無法確認。`VACUUM FULL` 不應成為例行任務的說明與每表 heap／TOAST／index 監控留給後續。
- 待使用者決定：是否接受純日期更正、每來源每日入庫預算與告警、正文保存天數與是否啟用定時 retention、探索暫停期間未抽取版本是否允許縮減（代價是那些正文的外連永遠不會被探勘）。

驗證：本地 PostgreSQL 16 跑完整 backend **1625 passed**（含真 PostgreSQL 測試）。新增 `tests/test_ingest_write_amplification_postgres.py`：先在移除新 migration 的情況下確認 4 個測試中 3 個失敗（重現根因），套用後全過；涵蓋連續 20 次 restamp 不改文章 ctid／版本數／`sync_changes`、標題／正文／摘要／作者變動仍建立版本並帶入新日期、NULL 日期補一次、provenance hash 立即更新與 `last_seen_at` 6 小時節流。`tests/test_articles_service.py` 新增持久化暫停仍入庫但不碰 frontier、開關讀取失敗不影響入庫、每批讀一次開關三個測試。文件檢查通過。**未驗證**：正式環境部署後的寫入量變化（需部署後 24–72 小時比對每小時新增文章、更新、版本、探索 target 與各表容量），未動 frontend。

## 階段五十六：#114 D／E 的容量與入庫觀測，以及 #92 PWA 行動閱讀出口（PR #116，issue #114／#92，2026-10-22）

PR #115 修好 A、B、C 後，#114 剩下的 D、E 需要先能「看見」才能決定政策：營運無法分辨 DB 成長是有效資料還是 VACUUM 已回收、可在表內重用但檔案沒縮小的空間；也看不到哪個來源一天入庫多少、哪個來源反覆產生版本；正式 `worker_runs` 沒有 retention 紀錄，無法確認 worker 容器的 `ARTICLE_RETENTION_ENABLED` 實際值。本 PR 只做唯讀觀測與後台顯示，不改任何產品政策。

**E：每表容量與 MVCC**

- 新 migration `20261022020000_storage_observability.sql` 在既有每日容量採樣的同一列保存 `tables` 快照（同一 advisory lock、UTC 每日去重、90 天保存）：`driftread` 總量前 25 大的表，加上固定的 articles／article_revisions／article_discoveries／sync_changes／discovery_targets／background_jobs，記錄 heap／TOAST／index bytes、live／dead tuples、累積 INSERT／UPDATE／DELETE／HOT、vacuum／autovacuum 時間與次數，以及資料庫 `stats_reset`。只讀 catalog 與統計，不掃表內容；部署當天已採樣的日子會補一次表快照，不重掃文章。
- `GET /admin/retention/tables` 回傳即時快照與相鄰兩日的區間增量。累積計數遇到 stats reset、單表重設或 crash recovery 而變小時，增量為 null（後台顯示「無法計算」），不顯示負數；容量變化保留負值。dead tuples 是估計值，不安裝 `pgstattuple`。

**D 與 B 的最後一點：來源入庫量與版本異常**

- `source_ingest_activity` 只走新增的 `article_revisions(recorded_at)` 索引的時間範圍，再以主鍵關聯文章，不全表掃描。新文章入庫時一定在同一交易記錄版本，所以新增篇數即使第 1 版被 10 版上限清掉仍是精確值；版本更新數在單篇一天超過 10 次時是下限。另外以前一版比較，區分正文變更與僅中繼資料變更，並列出舊文回填篇數，區分刊登時間與入庫時間。
- `GET /admin/operations/ingest` 列出最近 24 小時新增最多與版本更新最多的來源。標示門檻：新增 ≥ 500 篇；版本更新 ≥ 200 次；或至少 30 次且每篇平均 ≥ 3 次。只標示，不限額、不節流、不封存。

**retention 實際狀態**

- 每個 worker loop 的 heartbeat 帶上 worker 自己解析的排程設定（refresh／discovery 部署開關、retention 啟用、mode、天數、批次、間隔；只有布林與整數），`/admin/operations` 回傳最新一筆帶設定的 `worker_config` 與各 kind 最近一次 run（新增 `worker_runs(kind, started_at DESC)` 索引）。舊版 worker 不回報時顯示「狀態未知」，不當成已停用。API 容器的環境可能與 worker 不同，所以不從 API 讀。
- 後台管理首頁新增「來源入庫量與版本更新」「每表容量與 MVCC」面板（寬表在自己的捲動區內，不撐寬 390px 頁面），並在排程器狀態顯示正文縮減排程的實際設定與 retention／storage 最近一次執行。

**沒做的事**

- 沒有每來源每日入庫上限、異常來源節流或封存、retention 啟用或預設值變更，也沒有刪除資料或索引、改 `search_vector`；這些仍待使用者決定。沒有每小時寫入量的時間序列；部署前後 24–72 小時的比對要用這些面板人工觀察。
- 部署注意：`article_revisions(recorded_at)` 以一般 `CREATE INDEX` 建立，建立期間會短暫擋住文章更新，建議離峰部署（見 RUNBOOK）。
- review 修正（Codex P2）：原本超標來源數是從截斷後的兩份前 N 名推算，`limit` 較小時會少算；更嚴重的是只靠「每篇平均版本」超標的來源總更新數未必排得進前 N，會完全看不到。改為由 SQL 在截斷前對全部來源套用門檻，回傳 `flagged_total` 與最多 `limit` 筆的 `flagged` 清單，後台在警示中列出來源名稱；門檻仍只在 `ingest_activity.py` 定義，以參數傳入（migration 尚未合併，直接修改同一支）。SQL fixture 以 `limit=1` 確認不在前 N 名的來源仍被計入，並驗證門檻參數為 0／NULL 時拒絕。
- 既有文件落差：FEATURES 的索引清單列了 `articles(feed_id, fetched_at DESC)`，repo 實際沒有這個索引；本 PR 未修，新增篇數查詢改從 revisions 推導，不依賴它。

驗證：本地 PostgreSQL 16、Python 3.12 完整 backend **1646 passed／0 skipped**（含真 PostgreSQL）。新增 `tests/test_storage_observability_postgres.py` 與 `tests/sql/test_storage_observability.sql`，涵蓋 migration 重跑、快照格式與大小加總、同日補表快照且不重掃文章、CHECK 約束、新增／回填／metadata-only／正文變更計數、limit、24 小時視窗排除、參數驗證、anon／authenticated 無 EXECUTE、SECURITY INVOKER，並以 EXPLAIN 確認兩個新索引可被使用；`test_table_storage.py`、`test_ingest_activity.py`、`test_operations.py` 覆蓋區間增量、reset 處理、門檻判斷、端點認證與上下限、worker 設定回報。frontend 單獨驗證 43 files／499 tests 與 production build 通過（新程式碼都在 lazy chunk，增量出現在共用 chunk）。文件檢查與 `git diff --check` 通過。**未驗證**：瀏覽器實機（含 390px）、Docker 與正式 Supabase 部署。

**#92：行動裝置閱讀出口採 PWA**

形式決定採 PWA 而非 Flutter：沿用既有 Angular 前端、Offbeat 元件庫、Supabase 登入與 publication 讀取層，`/me/sync` 的 invalidation／100 篇 replacement snapshot 與 `/me/sync/operations` 離線重送契約已由 extension 驗證，可直接重用；Flutter 需另維護一套 UI、認證與商店發佈，對單一自架部署成本過高。這部分不新增後端資料模型、API 或 migration。

- 新增 manifest、icon 與 `@angular/service-worker`（21.2.12，與 core 對齊）。SW 只快取 app shell；`/api/**`、`/.well-known/**` 不進 Cache Storage，也不被導覽 fallback 成 index.html，避免共用快取造成跨帳號洩漏。容器啟動時 render 的 `env.js` 排除在 app shell 之外，改為 network-first，換 Supabase 設定後下次連線即生效。SW 以 lazy import 註冊、不用 `provideServiceWorker()`。
- `/me/offline`、`/me/offline/:id`：IndexedDB 以 user_id 分隔保存最近 100 篇摘要（白名單欄位，不存正文），離線可列出與閱讀，摘要走 `[innerHTML]` sanitizer。已讀／收藏／稍後讀樂觀疊加並 coalesce 排隊（最多 100 篇），重新連線先剔除失權操作，再用原帳號 token 重送，最後拉回 authoritative snapshot；404／409 丟棄並回到伺服器狀態，503／網路失敗保留，IndexedDB 寫入失敗立即回滾。
- 帳號隔離：claim 新帳號時同交易刪除其他帳號的快取與佇列；寫入在交易內比對 owner，延遲回應不能寫回；執行期間 session 結束或按登出即清空。`authInterceptor` 改為不覆寫呼叫端已帶的 Authorization，讓重送鎖定佇列主人的 token（既有只有管理 session 會自帶，帶的是同一個 token，行為不變）。
- 頁尾 ONLINE／OFFLINE 與待同步數；nginx 對 index.html、env.js、ngsw.json、SW 檔與 manifest 回 no-cache，manifest 補 MIME（location 層重寫安全 header）。

- review 修正（Codex P2）：`record`／`expired` 是 `computed`，`Date.now()` 不是 reactive dependency；離線頁一直開著或已安裝的 PWA 暫停後恢復時，若快取、模式與網路狀態都沒變，超過 24 小時的摘要仍會繼續顯示。改用時間 signal：快取到期時由計時器更新，頁面回到前景（`visibilitychange`）時也更新。新增兩個測試，修正前皆失敗。
- review 修正（Codex P2）：背景同步原本只在載入快取與離線→線上時判斷，頁面一直在線或休眠後恢復時 `navigator.onLine` 仍為 true，快取會一直停在舊版本直到 24 小時被隱藏。改為回到前景時與頁面在前景時快取滿 15 分鐘的計時器也會背景同步；計時器只在 `updatedAt` 數值改變時重排，同步失敗不會立即重試，已有同步進行時也不重複排入（喚醒時計時器與 visibility 事件會同時觸發）。新增兩個測試，修正前皆失敗。

**`/code-review` 修正（9 項，皆為本 PR 新增的程式）**

- 離線同步改由 `AuthService.currentSession()`（Supabase `getSession()`，過期先刷新）取得 token：恢復前景或重新連線時 signal 可能仍是過期 token，原本會 401 且 `TOKEN_REFRESHED` 同帳號不會重試。
- 同步途中登出或切換帳號：`_syncing` 原本只在世代未變時重設，下一位使用者的同步按鈕會一直停在「同步中」；現在登出／切換時重設，且最後一輪結束時一律清除。新帳號的 `sync()` 原本會併入上一帳號的在途同步並因 `!ok` 被吞掉，現在依世代區分，等舊同步停下後另起一輪。
- 登出時 Supabase sign-out 失敗（離線）而 session 仍在：`clearAll()` 已清掉 owner，原本之後所有寫入都靜默失敗；現在清除 owner 記錄並由 `reclaim()` 重新認領仍登入的使用者。
- 頁尾待同步數與最後同步時間原本讀未過濾的快取，dormant 狀態連線後會顯示上一個帳號的佇列；改為與可見快取一致。
- 後端：各 kind 最近一次 run 由 5 次 PostgREST 請求改為一支 `latest_worker_runs(text[])` RPC（INVOKER、service-only，真 PostgreSQL 測試）；`/admin/retention/tables` 的兩個獨立讀取改並行；門檻判斷式只留 SQL 一份，每筆回傳 `alerts`，Python 只傳門檻值，`>=` 邊界由 SQL fixture 驗證。
- 前端：兩個後台面板共用 `shared/bytes.ts` 的 MiB 格式化。
- 前端新增 5 個測試（刷新後 token、登出中同步、跨帳號不併入、sign-out 失敗後重新認領、dormant 連線後隱藏佇列），修正前皆失敗。

沒做什麼／已知未處理：離線不含正文、沒有應用內新版提示、沒有 server push 撤權（同 extension，24 小時 TTL）。token 在離線期間過期時 Supabase 無法恢復 session，此時只在離線狀態唯讀顯示上次帳號的快取，連線後隱藏、不刪除；共用裝置需先登出。nginx 沒有 CSP，日後加 CSP 需允許 `worker-src`／`manifest-src 'self'`。

驗證（PWA）：新增 55 項 frontend 測試（含 fake-indexeddb 的帳號分隔、切帳號、重送、回滾與 ngsw 設定）。以 Playwright Chromium 對 production build 的 fake API 於 390×844 走查 manifest、SW 註冊、離線重新載入、重新連線重送（原 token）、Cache Storage 無 `/api/`、登出清空 IndexedDB 與無水平溢出。未驗證：nginx 設定（沙箱無 Docker daemon）、真手機安裝（Android／iOS）、真 Supabase 登入與正式部署。

合併與 review 修正後整體驗證：backend **1648 passed**（含真 PostgreSQL）；frontend **54 files／565 tests** 通過，production build 成功，initial bundle 514.41 kB（既有 500 kB 預算警告延續；本 PR 前為 512.45 kB，未放寬預算）。

## 階段五十七：Compose 範例預設開啟正文縮減，並量化高流量來源的容量模型（PR #118，issue #117，2026-10-22）

#117 指出來源數上限（300）不等於寫入與儲存預算：2026-10-22 最近 24 小時約 3,900 篇新文章、全部來自正常 RSS refresh，前三名來源佔 36%，資料庫已逼近 Free Plan 500 MB。使用者明確指定的產品決策是 Compose 範例預設開啟既有的 article retention；收錄限額等政策要求先評估、不擅自決定。

**做了什麼**

- `docker-compose.yml` 的 `api` 與 `worker` 都改為 `ARTICLE_RETENTION_ENABLED: ${ARTICLE_RETENTION_ENABLED:-true}`，兩個容器不會互相矛盾；`.env` 明確的 `false` 仍優先。`30 天／200 篇／1440 分鐘` 未改。
- **程式碼層 fallback 刻意不改**：`services/retention.py::retention_enabled()` 仍只認明確 `true/1/yes/on`。issue 指定的是 Compose 範例預設，不是所有非 Compose 部署的執行語意；拼錯的布林值也不能開啟會移除正文的排程。
- `.env.example` 改成註解的 `# ARTICLE_RETENTION_ENABLED=true` 並寫清楚保留與移除的內容。刻意維持註解：取消註解會讓 `gen_env.py` 把值複製進新 `.env`，等於把 Compose 預設寫死。`gen_env.py` 從 `OPTIONAL_MISC_DEFAULTS` 移除這個變數（它的 Compose 預設與程式碼預設不同，不能再列成同一個「預設值」），改為依 `.env` 現值回報 Compose 預設／已啟用／已停用，且從不寫入。
- `GET /admin/operations/ingest` 新增 `hours`（只接受 24 或 168），後台「來源入庫量與版本更新」加上 24 小時／7 天切換。SQL 早就支援 1–168 小時，不需 migration；7 天的次數門檻乘以 7，每篇平均門檻不變。切換視窗只重抓入庫面板，兩個面板改用各自的 request 計數，避免切換時丟掉每表容量的回應。
- README 醒目說明預設開啟、保留內容、無法自行還原與明確 false 的停用方式；RUNBOOK 新增部署前（備份、dry-run、基準）、部署後（worker 實際設定、retention run summary、24–72 小時追蹤、不排程 `VACUUM FULL`）與回滾步驟；CRAWLER_OPERATIONS 新增容量模型與入庫治理提案。

**評估結論（正式資料庫唯讀查詢，2026-10-22 約 07:20 UTC）**

- 有正文的文章列平均 11.9 KB，其中 `search_vector` 5.9 KB 比正文本身（4.1 KB）還大；無正文 1.4 KB。每日新增約 33 MiB 列資料＋約 8.5 MiB 索引。
- 30 天正文保存在 500 MB 下不成立（窗口約 1.2 GiB）；縮減後每篇仍殘留約 3.5–4.5 KB（列、索引、版本、來源證據），即每天約 13–17 MiB 無上限成長，只靠縮減無法讓容量穩定。
- 每天需要縮減約 2,100 篇才跟得上，預設 200 篇只有約 10%。目前符合條件僅 74 篇，第一次排程影響很小；2026-10-16 單日 10,692 篇的尖峰約 11-15 起進入可縮減範圍。提案改為 60 分鐘間隔、批次維持 200（RPC 在交易內持有來源列鎖，放大批次會拉長 refresh 等待），未改預設。
- **Compose 預設 `FEED_DISCOVERY_ENABLED=false` 時新文章不會完成外連抽取，因此開啟 retention 也不會縮減它們**；正式環境探索開啟（97.7% 已抽取）所以有效。是否允許縮減未抽取版本仍待決定（#114 留下的同一個問題）。
- DEV Community 7 天 2,648 篇、52.5 MiB，約佔 7 天文章列資料的三分之一；issue 統計的 24 小時 907 篇對應 907 個 canonical URL，本次查 7 天 2,648 篇也全是不同 URL，沒有重複 INSERT（另有 `articles(feed_id,url)` 唯一約束）。其他前 10 名來源 7 天都 ≤ 4.1 MiB，問題是「高位元組」而非單純「高篇數」。
- 入庫治理方案（每來源每日預算、高位元組來源只存摘要、`search_vector` 只索引標題摘要、舊列到期、付費方案）的效果、完整性與成本比較寫在 CRAWLER_OPERATIONS。建議先對高位元組來源入庫只存摘要並把 retention 改成 60 分鐘，限額先只告警；全部待使用者決定。

**沒做的事**

- 沒有每來源／全站入庫預算、超額 ledger、只存摘要的入庫政策、`search_vector` 改寫或文章列到期；沒有封存 DEV Community，也沒有刪除或縮減任何既有文章。這些是產品政策，issue 要求先提案。
- 沒有每來源位元組的 API（需逐來源 `pg_column_size`，成本高，留待決定是否需要每日快照）；沒有改 retention 的天數、批次或間隔。
- 正式環境的 `.env` 實際值本 PR 無法確認；若正式 `.env` 沒有這個變數，部署本版後就會開始縮減。部署時同時會套用尚未上線的 PR #115、#116 migration。

**`/code-review` 修正（兩輪合計 14 項，去重後修 11 項）**

- `gen_env.py` 原本直接讀 `.env` 原文判斷：`ARTICLE_RETENTION_ENABLED="true"`、`'yes'` 或 `true  # 註解` 在 Compose 都解析成啟用，腳本卻回報「已停用」，等於對不可逆的縮減報錯狀態。新增 `compose_value()`，依 Compose 的規則去引號、去掉前面有空白的 `#` 註解（以 `docker compose config` 逐一實測對照）；啟用值集中為 `RETENTION_TRUE_VALUES`，測試逐值比對 `retention_enabled()`，避免兩邊定義漂移。
- 7 天視窗原本把三個次數門檻都乘以 7：版本更新數受每篇 10 版上限約束，同一個持續重送的來源在 24 小時被標示、7 天卻消失；每篇平均門檻不放大則讓 7 天對較慢的重送更敏感。改為只放大新增篇數門檻，版本門檻在兩個視窗相同，7 天的標示是 24 小時的超集合，並在 FEATURES、CRAWLER_OPERATIONS 與後台說明寫明語意。
- 後台標題與空狀態原本跟著使用者點的分頁：先部署前端、API 仍是舊版（忽略 `hours`、回 24 小時）時會把一天的資料標成「最近 7 天」。改為依回應的 `window_hours` 標示，載入中才用分頁值。
- 允許視窗的規則只留一份：`ingest_activity.window_error()`，router 與 service 共用，訊息由 `WINDOW_CHOICES` 產生。沒有改用 `Literal[24, 168]`：實測 FastAPI 不會把查詢字串 `"168"` 轉成 Literal 整數，合法請求會被 422。也沒有在 router 攔 `ValueError`，因為 pydantic 的 `ValidationError` 是它的子類，RPC 回傳格式錯誤會被誤報成 422 並帶出內容。
- `IngestActivity.window_hours`／`thresholds` 改為必填，不再與 `thresholds_for()` 各自維護一份 24 小時預設。
- 新增的 worker 測試與既有測試幾乎相同，改為參數化既有測試（discovery 開／關），並多驗證 refresh 與評審照常啟動。
- 文件：`worker.py` docstring、`docs/BACKGROUND_JOBS.md`、`docs/CONTENT_MODEL.md` 仍寫 retention「預設關閉／opt-in only」，與 Compose 新預設不符，已更新；FEATURES 的版本更新門檻列補上 7 天語意。
- 不修：切換 24 小時／7 天時每次重抓。只有管理員手動切換才會觸發，7 天查詢走 `article_revisions(recorded_at)` 索引範圍；加每個視窗的快取還要處理快取失效與「重新整理」語意，成本高於收益。

驗證：本地 PostgreSQL 16、Python 3.12 完整 backend **1663 passed**（含真 PostgreSQL）。新增 `tests/test_deploy_defaults.py`（Compose 兩個服務的預設與排程參數、`.env.example` 不寫死值、程式碼預設仍關閉、`gen_env.py` 在未設定／false／true 下只回報不寫入）；`tests/test_operations.py` 新增明確 false 時 refresh／discovery／評審仍啟動而 retention 不啟動；`tests/test_ingest_activity.py` 新增 7 天視窗門檻、非法視窗在 service 與 endpoint 皆拒絕。retention 對訂閱／收藏／已讀、待抽取與同 hash 競爭的保護沿用既有的 `tests/test_postgres_lifecycle.py` 與 `tests/sql/test_crawler_lifecycle.sql`，未改 SQL。`docker compose config` 實測：無 `.env` 與 `.env` 無此變數時 `api`／`worker` 皆為 `"true"`，`.env` 或 shell 設 `false` 時皆為 `"false"`。frontend **54 files／567 tests** 通過、production build 成功，initial bundle 514.41 kB（與前一版相同；既有 500 kB 預算警告延續）。**未驗證**：實際 Docker 容器運行、正式 Supabase 部署後的縮減與 24–72 小時容量變化、瀏覽器實機。

## 階段五十八：每來源文章上限，預設 1000 篇並保護收藏與人工事件引用（PR #119，issue #117／PR #118 後續，2026-10-22）

#118 把「舊文章列到期」列為待決定的產品政策。使用者決定：每個來源預設最多 1000 篇，超過就淘汰最舊的，收藏的跳過。討論後的修正（皆經使用者同意）：依 `timeline_at` 而非 `published_at` 判斷新舊、受保護文章不計入上限、保護範圍加上人工事件引用、已讀不保護、未抽取版本有 1.5 倍緩衝、每來源一個水位線防止重抓、可每來源覆寫、由 worker 分批執行。

**評估依據（正式資料庫唯讀查詢，2026-10-22）**：資料庫 477 MB、38,445 篇、378 個來源，每來源中位數 20.5 篇；超過 1000 篇的只有 8 個來源，合計多出 8,680 篇、約 45 MB 列資料，其中約 36 MB 來自 DEV Community（2,804 篇、56 MB、24 小時 799 篇）。有收藏的文章 1 篇、有已讀紀錄的 7 篇。篇數制讓高頻來源只留約一天、低頻來源保留數月；天數制會連低頻來源一起砍。

**做了什麼**

- migration `20261022120000_article_cap.sql`：`feeds.article_cap`（100–100000，NULL 用全域）、`feeds.article_cap_evicted_through`、`article_cap` 工作種類與 `worker_runs` kind，以及 `enforce_article_cap(p_default_cap, p_limit, p_dry_run, p_keep_unextracted)`。RPC 依 `timeline_at DESC, id DESC` 排名未受保護的文章，刪除排名超過上限者（開啟保護時未抽取的版本保留到 1.5 倍），依超出量由大到小處理來源，每批上限由參數決定。
- 保護：`user_bookmarks`（收藏與稍後讀）、`fact_articles`（人工事件，含已排除的連結）不刪除也不計入。已讀不保護、訂閱的來源同樣套用——讀得多或有人訂閱就整個來源無法淘汰，不是想要的語意。
- 鎖：交易 advisory lock 序列化整輪；來源列 `FOR UPDATE SKIP LOCKED`（與 `ingest_article_batch` 同順序，正在入庫的來源本輪略過、不讓 refresh 等待）；文章列先 `SKIP LOCKED`，再以新的語句快照重新檢查保護條件後刪除，沿用正文縮減的作法，在途的收藏（持有 FK KEY SHARE）會讓該文章保留。
- 防止重抓：刪除後只要 URL 還在 feed 中就會被重新 INSERT。沒有沿用 `article_cleanup_tombstones`（一 URL 一筆，正式環境已 1,271 筆，自動淘汰會無限成長），改為每來源一個水位線；既有的 `guard_removed_article()` trigger 對「該來源尚不存在且 `timeline_at` 不晚於水位線」的新列回傳 NULL，已存在的列照常 upsert。
- 全域設定 `article.cap`（`{enabled, max_per_feed}`，資料庫沒有這一列時為 `true／1000`，所以新部署預設啟用）；worker 新增常駐的 `article_cap` loop，每 60 分鐘、每批 500 篇，每次 claim 前讀設定，停用時不入列。`p_keep_unextracted` 跟隨探索的實際狀態（部署開關＋後台暫停，與探索排程共用 `can_start`），RPC 另對封存來源不給緩衝：這些情況沒有東西會完成抽取，若照樣保護，上限會默默變成 1.5 倍。
- API：`POST /admin/retention/article-cap/run`（預設 dry-run，`max_per_feed`、`limit` 有界）；`PATCH /admin/feeds/{id}/source` 接受 `article_cap`（明確 null 改回全域），回傳改為 `AdminFeed` 才看得到覆寫值。
- 後台：「總覽」新增「每來源文章上限」卡片（開關、全域上限、預覽下一批；降低上限或重新啟用時確認；儲存失敗顯示資料庫目前值並保留本地修改）；「來源設定」對話框新增文章上限欄位；「各排程最近一次執行」列出 `article_cap`。UI 只預覽，不提供立即刪除按鈕。
- 文件：CRAWLER_OPERATIONS 新增「每來源文章上限」並把治理表的「舊文章列到期」指向它；RUNBOOK 新增部署前預覽 SQL、在新 worker 啟動前停用的方法與回滾（含清除水位線）；GLOBAL_SETTINGS、FEATURES（API、路由、資料表、限制）、BACKGROUND_JOBS、CONTENT_MODEL、README 警告區塊、TODO。

**沒做的事／已知未處理**

- 沒有「立即套用」的後台按鈕，避免誤觸刪除；要排程外執行就用 API 的 `dry_run=false`。
- 批次與間隔是程式碼常數（500 篇、60 分鐘），沒有開放設定：每次刪除會為每篇文章與其已讀紀錄寫同步失效紀錄、持有共用的 sync clock 列鎖，放大批次會拉長其他寫入的等待。第一次上線約 18 小時清完 8,680 篇。
- 沒有發布日期的項目以抓取時間計，若來源反覆列出已刪除的無日期項目仍會被收回；正常 feed 只列最近 20–100 項。調高上限不會清除水位線，比水位線舊、從未入庫的項目仍會被略過（RUNBOOK 有清除方法）。
- refresh 以抓取前後的篇數差計算新增數，淘汰剛好落在同一來源兩次計數之間時新增數偏低、自適應間隔可能多退避一次；淘汰每小時一次且不碰正在寫入的來源，本版不處理。
- 刪除不會讓資料庫檔案變小，空間由之後的新文章重用；仍需回收時照 RUNBOOK 手動 `VACUUM FULL`。理論上 300 個來源都長到 1000 篇仍會超過 500 MB，上限不是硬性的容量保證；以目前分佈短期內碰不到。
- 高位元組來源只存摘要、每日入庫預算、`search_vector` 改寫仍待決定（TODO）。

**`/code-review` 修正（兩輪合計 18 項，去重後修 12 項）**

- 部署順序：README／RUNBOOK 原本叫維運在新版 worker 啟動前「到後台卡片停用」，但卡片在新版前端，而 RUNBOOK 把前端和 worker 一起啟動；worker 啟動時第一批立即執行，不是「一小時內」。改為先只更新 `api`、用 `PUT /api/admin/settings/article.cap` 停用，明確寫「不要一次 `docker compose up -d`」。
- 整頁標已讀：`/me/reads/mark-all` 帶 `article_ids` 時，只要頁面上有一篇已被淘汰，整批 upsert 就因外鍵違反回 409、前端回滾，重新整理前一直失敗。高流量來源每小時都會淘汰，這會在正常使用中發生；改為先查出仍存在的 id 再寫入，`marked` 只算實際寫入的。單篇標已讀／收藏已刪除文章仍回錯誤，離線重送原本就回 `unavailable`，未改。
- 預覽：「預覽下一批」原本用已儲存的上限，管理者在確認刪除前看不到新上限的影響；改以表單上的草稿值呼叫 API 的 `max_per_feed`，並標示預覽用的上限。
- 來源覆寫：在來源設定新增或調低 `article_cap` 不需確認，和全域卡片不一致；改為同樣確認（全域值在該頁未知，所以新增任何覆寫都確認）。
- 探索暫停與封存來源：原本只看部署開關，後台暫停探索或封存的來源都不會完成抽取，上限默默變成 1.5 倍；改用 `can_start`，RPC 對封存來源不給緩衝。
- 鎖：原本先鎖來源列才排名，超量全是受保護或保留待抽取的來源每小時都被鎖到整批 commit，讓它的 refresh 白等；抽出 `article_cap_candidates()`，先不加鎖排名，有可刪的才鎖並在鎖內重新排名。
- 守門 trigger 原本自己重算 `timeline_at`，理由（觸發順序）寫反了：`article_revision_prepare` 依名稱排在 `articles_cleanup_guard` 之前，`NEW.timeline_at` 已算好；改為直接使用，避免兩份公式漂移。
- 後台卡片：load 與儲存／預覽共用一個 sequence，儲存 409 後重新載入期間再按預覽或儲存，載入回應被丟掉、`loading` 卡住、版本過期；改為分開計數，並在載入中停用儲存與預覽。
- `ADD CONSTRAINT worker_runs_kind_check` 改用 `DO` 區塊包住（AGENTS.md 規則）；新增測試的 db mock 改為只提供用到的 builder，不用裸 `MagicMock`；`AdminService.runArticleCap` 補 service spec（`dry_run` 與 `max_per_feed` 參數）；RUNBOOK 原本說在「各排程最近一次執行」看 `deleted`，但那裡不顯示 summary，改指向 `GET /api/admin/operations`。
- 不修：worker 每輪讀兩次設定（`can_start` 一次、action 再確認一次），只是每小時多一次小查詢，換來「claim 後才停用」也會生效。上下限 100／100000 分散在 SQL CHECK、pydantic、service 與前端：各層各自擋是刻意的，集中成單一常數需要 `models.py` 匯入 service 模組、有循環匯入風險；邊界由 SQL fixture 與各層測試守住。

驗證：本地 PostgreSQL 16、Python 3.12 完整 backend **1711 passed**（含真 PostgreSQL；本 PR 前 1669）。新增 `tests/sql/test_article_cap.sql`（預覽不變動、批次預算先刪最舊、受保護文章不刪不計入、已讀 cascade、1.5 倍緩衝與關閉時降到上限、全域預設、水位線擋下已刪項目但允許受保護列更新與新／無日期項目、無水位線來源不受影響、參數與 CHECK 邊界、封存來源不給緩衝）、`tests/test_article_cap_postgres.py`（migration 重跑、正在入庫的來源被略過、只有受保護超量的來源不被鎖、在途收藏讓文章保留、兩輪並行不重複刪、anon／authenticated 無權呼叫）、`tests/test_me.py` 的整頁標已讀略過已刪文章、`tests/test_article_cap.py`（service 參數與契約、探索開關對應保護、設定預設與驗證、API 認證與邊界、worker loop 停用時不 claim）與 `tests/test_source_model.py` 的覆寫設定／清除；既有 worker 啟動測試補上新 loop。frontend **55 files／579 tests** 通過、production build 成功，initial bundle 514.41 kB（與前一版相同；既有 500 kB 預算警告延續）。**未驗證**：實際 Docker 容器、正式 Supabase 套用 migration 與第一輪刪除、瀏覽器實機操作後台卡片。
