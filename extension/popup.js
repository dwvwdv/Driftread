async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function loadConfig() {
  const cfg = await connection();
  return { apiUrl: cfg.apiUrl || '', apiKey: cfg.accessToken || '' };
}
async function importFeed(feedUrl) {
  return personalRequest('/discover/import', { method: 'POST', body: JSON.stringify({ feed_url: feedUrl }) });
}

function render(feeds, cfgOk) {
  const list = document.getElementById('list');
  list.innerHTML = '';
  if (!feeds.length) {
    list.innerHTML = '<p class="empty">此頁面沒有偵測到 RSS / Atom feed。</p>';
    return;
  }
  feeds.forEach((f) => {
    const div = document.createElement('div');
    div.className = 'feed';
    div.innerHTML = `
      <div class="feed-title"></div>
      <div class="feed-url"></div>
      <div style="margin-top:6px;">
        <button class="add">加入 Driftread</button>
      </div>
    `;
    div.querySelector('.feed-title').textContent = f.title || '(無標題)';
    div.querySelector('.feed-url').textContent = f.href;
    const btn = div.querySelector('button.add');
    btn.disabled = !cfgOk;
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = '匯入中...';
      try {
        const cfg = await loadConfig();
        await importFeed(f.href, cfg);
        btn.textContent = '已加入 ✓';
      } catch (e) {
        btn.textContent = '失敗';
        btn.title = String(e);
      }
    });
    list.appendChild(div);
  });
}

(async () => {
  const cfg = await loadConfig();
  const cfgOk = cfg.apiUrl && cfg.apiKey;
  document.getElementById('config-hint').style.display = cfgOk ? 'none' : 'block';
  document.getElementById('open-options').addEventListener('click', (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
  });
  const tab = await getActiveTab();
  try {
    const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] });
    chrome.tabs.sendMessage(tab.id, { type: 'driftread:detect' }, resp => render(resp?.feeds || [], cfgOk));
  } catch { render([], cfgOk); }
})();

async function renderReading(state) {
  const target = document.getElementById('reading'); target.replaceChildren();
  const cfg = await connection();
  if (!DriftreadOffline.usable(state, cfg.account)) {
    if (state && cfg.account && state.account !== cfg.account) await chrome.storage.local.remove('readingCache');
    document.getElementById('status').textContent = '請連線並同步閱讀（離線保存最多 24 小時）'; return;
  }
  for (const article of state.items) {
    const card = document.createElement('div'); card.className = 'feed';
    const title = document.createElement('a'); title.textContent = article.title;
    if (/^https?:\/\//.test(article.url)) { title.href = article.url; title.target = '_blank'; title.rel = 'noopener'; }
    const summary = document.createElement('p'); summary.textContent = article.summary || '';
    card.append(title, summary);
    for (const [kind, label] of [['read','已讀'],['favorite','收藏'],['read_later','稍後讀']]) {
      const button = document.createElement('button');
      const pending = state.pending.find(p => p.articleId === article.id && p.kind === kind);
      const enabled = pending ? pending.enabled : kind === 'read' ? article.is_read : article.bookmark_types.includes(kind);
      button.textContent = (enabled ? '取消' : '標記') + label;
      button.disabled = syncing;
      button.onclick = async () => {
        try { state = DriftreadOffline.queue(state, article.id, kind, !enabled); }
        catch (error) { document.getElementById('status').textContent = error.message; return; }
        await chrome.storage.local.set({ readingCache: state });
        await renderReading(state);
        document.getElementById('status').textContent = '變更已保存，連線後按同步送出';
      };
      card.appendChild(button);
    }
    target.appendChild(card);
  }
}
let syncing = false;
document.getElementById('sync').onclick = async () => {
  syncing = true;
  for (const button of document.querySelectorAll('#reading button')) button.disabled = true;
  const button = document.getElementById('sync'); button.disabled = true;
  try { await renderReading(await syncReading()); document.getElementById('status').textContent = '已同步最近 100 篇'; }
  catch (error) { document.getElementById('status').textContent = error.message; }
  finally { syncing = false; button.disabled = false; await renderReading((await chrome.storage.local.get('readingCache')).readingCache); }
};
chrome.storage.local.get('readingCache').then(result => renderReading(result.readingCache));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'session' && (changes.account || changes.accessToken)) {
    document.getElementById('reading').replaceChildren();
    chrome.storage.local.get('readingCache').then(result => renderReading(result.readingCache));
  }
});
