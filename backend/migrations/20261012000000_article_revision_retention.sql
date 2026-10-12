-- #82 有界的文章版本歷史：每篇只保留最近 10 個版本（含 current）。
-- 摘要帶動態數字（例如 hnrss 的 points／comments）或每次輸出都改 pubDate
-- 的 feed，每次 refresh 都會新增一個版本；不設上限時這張表只增不減。
-- 在寫入版本的同一個 transaction 內清理，不依賴預設關閉的 retention 排程。
-- 設計理由見 docs/CONTENT_MODEL.md 的「身份與版本」。

-- service_role 對 article_revisions 只有 SELECT／INSERT（append-only），
-- 所以清理以 table owner 身分執行。函式回傳 trigger、沒有參數，無法透過
-- PostgREST 呼叫；只刪除剛寫入那篇文章、超過保留數的舊版本。
CREATE OR REPLACE FUNCTION driftread.prune_article_revisions()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 DELETE FROM driftread.article_revisions
  WHERE article_id=NEW.article_id AND revision_number<=NEW.revision_number-10;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION driftread.prune_article_revisions() FROM PUBLIC,anon,authenticated,service_role;

DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='article_revision_prune'
               AND tgrelid='driftread.article_revisions'::regclass) THEN
  CREATE TRIGGER article_revision_prune AFTER INSERT ON driftread.article_revisions
   FOR EACH ROW EXECUTE FUNCTION driftread.prune_article_revisions();
 END IF;
END $$;

-- 已套用 20261009000100 的資料庫一次清掉既有的超量版本；重跑時沒有可刪的列。
DELETE FROM driftread.article_revisions r USING driftread.articles a
 WHERE a.id=r.article_id AND r.revision_number<=a.revision_number-10;

NOTIFY pgrst, 'reload schema';
