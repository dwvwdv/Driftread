// Tokens stay in extension session storage, never sync across Chrome accounts.
async function connection() {
  return chrome.storage.session.get(['apiUrl', 'accessToken', 'account']);
}
async function clearConnection() {
  await chrome.storage.session.clear();
  await chrome.storage.local.remove('readingCache');
}
async function personalRequest(path, options = {}) {
  const cfg = await connection();
  if (!cfg.accessToken) throw new Error('請先連線帳號');
  const response = await fetch(cfg.apiUrl.replace(/\/$/, '') + path, {
    ...options, headers: { 'Content-Type': 'application/json',
      Authorization: `Bearer ${cfg.accessToken}`, ...(options.headers || {}) },
  });
  if (response.status === 401 || response.status === 403) {
    // Only erase the failing account; a new account may have connected in flight.
    if ((await connection()).accessToken === cfg.accessToken) await clearConnection();
    throw new Error('登入已失效，請重新連線');
  }
  if (!response.ok) throw new Error(`請求失敗 (${response.status})`);
  return response.status === 204 ? null : response.json();
}
async function syncReading() {
  const cfg = await connection();
  if (!cfg.account) throw new Error('請先連線帳號');
  let state = (await chrome.storage.local.get('readingCache')).readingCache;
  if (state?.account !== cfg.account) state = DriftreadOffline.empty(cfg.account);
  for (const operation of [...state.pending]) {
    const { articleId, kind, enabled } = operation;
    const read = kind === 'read';
    const path = read ? `/me/articles/${articleId}/read` :
      enabled ? '/me/bookmarks' : `/me/bookmarks/${articleId}?bookmark_type=${kind}`;
    await personalRequest(path, { method: enabled ? 'POST' : 'DELETE',
      ...(read || !enabled ? {} : { body: JSON.stringify({ article_id: articleId, bookmark_type: kind }) }) });
    if ((await connection()).accessToken !== cfg.accessToken) throw new Error('帳號已切換');
    state.pending = state.pending.filter(p => p !== operation);
    // Persist after every successful idempotent operation. Replays are safe.
    await chrome.storage.local.set({ readingCache: state });
  }
  const response = await personalRequest('/me/sync' + (state.cursor ? `?cursor=${encodeURIComponent(state.cursor)}` : ''));
  if ((await connection()).accessToken !== cfg.accessToken) throw new Error('帳號已切換');
  state = DriftreadOffline.apply(state, response);
  await chrome.storage.local.set({ readingCache: state });
  return state;
}
