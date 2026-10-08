const apiUrlEl = document.getElementById('apiUrl');
const tokenEl = document.getElementById('accessToken');
const savedEl = document.getElementById('saved');
chrome.storage.session.get(['apiUrl'], cfg => apiUrlEl.value = cfg.apiUrl || '');
// Erase legacy admin credentials, which personal reading never needs.
chrome.storage.sync.remove(['apiKey', 'apiUrl']);
document.getElementById('disconnect').addEventListener('click', async () => {
  await clearConnection(); tokenEl.value = ''; savedEl.textContent = '已中斷連線並清除離線資料';
});
document.getElementById('save').addEventListener('click', async () => {
  try {
    const apiUrl = apiUrlEl.value.trim().replace(/\/$/, '');
    const url = new URL(apiUrl);
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost','127.0.0.1'].includes(url.hostname))) {
      throw new Error('API 必須使用 HTTPS（本機開發除外）');
    }
    await chrome.permissions.request({ origins: [url.origin + '/*'] });
    const accessToken = tokenEl.value.trim();
    await clearConnection();
    await chrome.storage.session.set({ apiUrl, accessToken });
    const response = await personalRequest('/me/sync');
    await chrome.storage.session.set({ account: response.account_id });
    await chrome.storage.local.set({ readingCache: DriftreadOffline.apply(DriftreadOffline.empty(response.account_id), response) });
    tokenEl.value = ''; savedEl.textContent = '帳號已連線';
  } catch (error) { await clearConnection(); savedEl.textContent = error.message; }
});
