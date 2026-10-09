// Tokens stay in extension session storage, never sync across Chrome accounts.
async function connection() {
  return chrome.storage.session.get(['apiUrl', 'accessToken', 'account', 'connectionId']);
}
async function clearConnection() {
  await chrome.storage.session.clear();
  await chrome.storage.local.remove('readingCache');
}
async function personalRequest(path, options = {}, expected = null) {
  const cfg = expected || await connection();
  if (expected && (await connection()).connectionId !== expected.connectionId) throw new Error('帳號已切換');
  if (!cfg.accessToken) throw new Error('請先連線帳號');
  const response = await fetch(cfg.apiUrl.replace(/\/$/, '') + path, {
    ...options, headers: { 'Content-Type': 'application/json',
      Authorization: `Bearer ${cfg.accessToken}`, ...(options.headers || {}) },
  });
  if (response.status === 401 || response.status === 403) {
    // Only erase the failing account; a new account may have connected in flight.
    if ((await connection()).connectionId === cfg.connectionId) await clearConnection();
    throw new Error('登入已失效，請重新連線');
  }
  if (!response.ok) { const error = new Error(`請求失敗 (${response.status})`); error.status = response.status; throw error; }
  return response.status === 204 ? null : response.json();
}
async function syncReading() {
  const cfg = await connection();
  if (!cfg.account) throw new Error('請先連線帳號');
  let state = (await chrome.storage.local.get('readingCache')).readingCache;
  if (state?.account !== cfg.account) state = DriftreadOffline.empty(cfg.account);
  async function pull() {
    const params = new URLSearchParams();
    if (state.cursor) params.set('cursor', state.cursor);
    for (const id of new Set(state.pending.map(p => p.articleId))) params.append('pending_ids', id);
    const response = await personalRequest('/me/sync?' + params, {}, cfg);
    if ((await connection()).connectionId !== cfg.connectionId) throw new Error('帳號已切換');
    state = DriftreadOffline.apply(state, response);
    state.pending = state.pending.filter(p => response.authorized_article_ids.includes(p.articleId));
    await chrome.storage.local.set({ readingCache: state });
  }
  // Apply rights/deletion invalidations before replay, including pending articles
  // that aged out of the bounded recent cache but remain authorized.
  await pull();
  const hadPending = state.pending.length > 0;
  for (const operation of [...state.pending]) {
    const { articleId, kind, enabled } = operation;
    const read = kind === 'read';
    const path = read ? `/me/articles/${articleId}/read` :
      enabled ? '/me/bookmarks' : `/me/bookmarks/${articleId}?bookmark_type=${kind}`;
    try {
      await personalRequest(path, { method: enabled ? 'POST' : 'DELETE',
        ...(read || !enabled ? {} : { body: JSON.stringify({ article_id: articleId, bookmark_type: kind }) }) }, cfg);
    } catch (error) { if (![404,409].includes(error.status)) throw error; }
    if ((await connection()).connectionId !== cfg.connectionId) throw new Error('帳號已切換');
    state.pending = state.pending.filter(p => p !== operation);
    await chrome.storage.local.set({ readingCache: state });
  }
  if (hadPending) await pull();
  return state;
}
