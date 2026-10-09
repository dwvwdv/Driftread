const apiUrlEl = document.getElementById('apiUrl');
const tokenEl = document.getElementById('accessToken');
const savedEl = document.getElementById('saved');
let connectionGeneration = 0;
function showFeedback(message) {
  savedEl.textContent = message;
  savedEl.hidden = false;
}
chrome.storage.session.get(['apiUrl'], cfg => apiUrlEl.value = cfg.apiUrl || '');
// Erase legacy admin credentials, which personal reading never needs.
chrome.storage.sync.remove(['apiKey', 'apiUrl']);
document.getElementById('disconnect').addEventListener('click', async () => {
  const generation = ++connectionGeneration;
  await clearConnection();
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
    await clearConnection();
    if (generation !== connectionGeneration) return;
    const connectionId = crypto.randomUUID();
    const cfg = { apiUrl, accessToken, connectionId };
    await chrome.storage.session.set(cfg);
    if (generation !== connectionGeneration) return;
    const response = await personalRequest('/me/sync', {}, cfg);
    if (generation !== connectionGeneration || (await connection()).connectionId !== connectionId) return;
    await chrome.storage.session.set({ account: response.account_id });
    if (generation !== connectionGeneration || (await connection()).connectionId !== connectionId) return;
    await chrome.storage.local.set({ readingCache: DriftreadOffline.apply(DriftreadOffline.empty(response.account_id), response) });
    if (generation !== connectionGeneration) return;
    tokenEl.value = ''; showFeedback('帳號已連線');
  } catch (error) { if (generation === connectionGeneration) showFeedback(error.message); }
});
