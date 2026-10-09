const apiUrlEl = document.getElementById('apiUrl');
const tokenEl = document.getElementById('accessToken');
const savedEl = document.getElementById('saved');
let connectionGeneration = 0;
let connectionMutations = Promise.resolve();
function mutateConnection(generation, action) {
  const task = connectionMutations.then(() => generation === connectionGeneration ? action() : false);
  connectionMutations = task.catch(() => {});
  return task;
}
function showFeedback(message) {
  savedEl.textContent = message;
  savedEl.hidden = false;
}
chrome.storage.session.get(['apiUrl'], cfg => apiUrlEl.value = cfg.apiUrl || '');
// Erase legacy admin credentials, which personal reading never needs.
chrome.storage.sync.remove(['apiKey', 'apiUrl']);
document.getElementById('disconnect').addEventListener('click', async () => {
  const generation = ++connectionGeneration;
  await mutateConnection(generation, clearConnection);
  if (generation !== connectionGeneration) return;
  tokenEl.value = ''; showFeedback('已中斷連線並清除離線資料');
});
document.getElementById('save').addEventListener('click', async () => {
  const generation = ++connectionGeneration;
  const accessToken = tokenEl.value.trim();
  try {
    const apiUrl = apiUrlEl.value.trim().replace(/\/$/, '');
    const url = new URL(apiUrl);
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost','127.0.0.1'].includes(url.hostname))) {
      throw new Error('API 必須使用 HTTPS（本機開發除外）');
    }
    const granted = await chrome.permissions.request({ origins: [url.origin + '/*'] });
    if (generation !== connectionGeneration) return;
    if (!granted) throw new Error('需要 API 網域權限才能連線');
    await connectionMutations;
    if (generation !== connectionGeneration) return;
    const previous = await connection();
    const connectionId = crypto.randomUUID();
    const cfg = { apiUrl, accessToken, connectionId };
    const response = await personalRequest('/me/sync', {}, cfg, { candidate: true });
    if (generation !== connectionGeneration || (await connection()).connectionId !== previous.connectionId) return;
    if (!response || typeof response.account_id !== 'string' || !response.account_id ||
        response.changed !== true || typeof response.cursor !== 'string' || !Array.isArray(response.items)) {
      throw new Error('初始同步回應格式不正確，原連線已保留');
    }
    const committed = await mutateConnection(generation, async () => {
      const existing = (await chrome.storage.local.get('readingCache')).readingCache;
      if (generation !== connectionGeneration) return false;
      const sameAccount = existing?.account === response.account_id &&
        (!previous.account || previous.account === response.account_id) &&
        (!existing.apiUrl || existing.apiUrl === apiUrl) && (!previous.apiUrl || previous.apiUrl === apiUrl);
      const state = sameAccount ? existing : DriftreadOffline.empty(response.account_id);
      const readingCache = { ...DriftreadOffline.apply(state, response), apiUrl };
      // Prepare the cache before session notifications make the popup render it.
      await chrome.storage.local.set({ readingCache });
      if (generation !== connectionGeneration) {
        if (existing) await chrome.storage.local.set({ readingCache: existing });
        else await chrome.storage.local.remove('readingCache');
        return false;
      }
      await chrome.storage.session.set({ ...cfg, account: response.account_id });
      return true;
    });
    if (!committed || generation !== connectionGeneration) return;
    tokenEl.value = ''; showFeedback('帳號已連線');
  } catch (error) { if (generation === connectionGeneration) showFeedback(error.message); }
});
