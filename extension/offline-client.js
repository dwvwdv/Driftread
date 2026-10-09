// Tokens stay in extension session storage, never sync across Chrome accounts.
async function connection() {
  return chrome.storage.session.get(['apiUrl', 'accessToken', 'account', 'connectionId']);
}
async function storageMutation(action, data = {}) {
  const result = await chrome.runtime.sendMessage({ type: 'driftread:storage', action, ...data });
  if (!result?.ok) throw new Error(result?.error || '離線資料儲存失敗，請重試');
  return result.value;
}
async function clearConnection(expected = null, intent = null) {
  return storageMutation('clear', { expected: Boolean(expected), connectionId: expected?.connectionId, intent });
}
async function personalRequest(path, options = {}, expected = null, { candidate = false } = {}) {
  const cfg = expected || await connection();
  // Candidate credentials are checked without replacing or clearing the active account.
  if (expected && !candidate && (await connection()).connectionId !== expected.connectionId) throw new Error('帳號已切換');
  if (!cfg.accessToken) throw new Error('請先連線帳號');
  const response = await fetch(cfg.apiUrl.replace(/\/$/, '') + path, {
    ...options, headers: { 'Content-Type': 'application/json',
      Authorization: `Bearer ${cfg.accessToken}`, ...(options.headers || {}) },
  });
  if (response.status === 401 || response.status === 403) {
    // Only erase the failing account; a new account may have connected in flight.
    if (!candidate) await clearConnection(cfg);
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
    state = await storageMutation('pull', { connectionId: cfg.connectionId, response });
  }
  // Apply rights/deletion invalidations before replay, including pending articles
  // that aged out of the bounded recent cache but remain authorized.
  await pull();
  const hadPending = state.pending.length > 0;
  for (const operation of [...state.pending]) {
    const { articleId, kind, enabled } = operation;
    try {
      await personalRequest('/me/sync/operations', { method: 'POST',
        body: JSON.stringify({ article_id: articleId, kind, enabled }) }, cfg);
    } catch (error) { if (![404,409].includes(error.status)) throw error; }
    state = await storageMutation('acknowledge', { connectionId: cfg.connectionId, operation });
  }
  if (hadPending) await pull();
  return state;
}
