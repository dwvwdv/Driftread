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
  const intent = crypto.randomUUID();
  try {
    await storageMutation('intent', { intent });
    await mutateConnection(generation, () => clearConnection(null, intent));
  } catch (error) { if (generation === connectionGeneration) showFeedback(error.message); return; }
  if (generation !== connectionGeneration) return;
  tokenEl.value = ''; showFeedback('已中斷連線並清除離線資料');
});
document.getElementById('save').addEventListener('click', async () => {
  const generation = ++connectionGeneration;
  const accessToken = tokenEl.value.trim();
  const intent = crypto.randomUUID();
  const begun = storageMutation('intent', { intent });
  begun.catch(() => {}); // Permission validation may reject before this message finishes.
  try {
    const apiUrl = apiUrlEl.value.trim().replace(/\/$/, '');
    const url = new URL(apiUrl);
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost','127.0.0.1'].includes(url.hostname))) {
      throw new Error('API 必須使用 HTTPS（本機開發除外）');
    }
    const [granted] = await Promise.all([chrome.permissions.request({ origins: [url.origin + '/*'] }), begun]);
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
    const committed = await mutateConnection(generation, () => storageMutation('connect', {
      intent, connectionId: previous.connectionId, candidate: cfg, response,
    }));
    if (!committed || generation !== connectionGeneration) return;
    tokenEl.value = ''; showFeedback('帳號已連線');
  } catch (error) { if (generation === connectionGeneration) showFeedback(error.message); }
});
