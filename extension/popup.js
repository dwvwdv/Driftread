async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function loadConfig() {
  const cfg = await chrome.storage.sync.get(['apiUrl', 'apiKey']);
  return { apiUrl: cfg.apiUrl || '', apiKey: cfg.apiKey || '' };
}

async function importFeed(feedUrl, cfg) {
  const res = await fetch(`${cfg.apiUrl.replace(/\/$/, '')}/admin/feeds/from-url`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': cfg.apiKey,
    },
    body: JSON.stringify({ feed_url: feedUrl }),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`${res.status}: ${t}`);
  }
  return res.json();
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
  chrome.tabs.sendMessage(tab.id, { type: 'driftread:detect' }, (resp) => {
    if (chrome.runtime.lastError || !resp) {
      render([], cfgOk);
      return;
    }
    render(resp.feeds, cfgOk);
  });
})();
