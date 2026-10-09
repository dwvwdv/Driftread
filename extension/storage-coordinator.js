// All personal storage writes belong to this service worker, not page lifetimes.
let storageMutations = Promise.resolve();
let latestConnectionIntent;
function dispatchStorageMutation(message) {
  if (message.action === 'intent') {
    latestConnectionIntent = message.intent;
  }
  const task = storageMutations.then(() => applyStorageMutation(message));
  storageMutations = task.catch(() => {});
  return task;
}
async function applyStorageMutation(message) {
  const cfg = await chrome.storage.session.get(['apiUrl', 'accessToken', 'account', 'connectionId', 'connectionIntent']);
  const matches = cfg.connectionId === message.connectionId;
  const intent = latestConnectionIntent ?? cfg.connectionIntent;
  if (message.action === 'intent') {
    await chrome.storage.session.set({ connectionIntent: message.intent });
    return true;
  }
  if (message.action === 'clear') {
    if (message.intent && message.intent !== intent) return false;
    if (message.expected && !matches) return false;
    await chrome.storage.session.clear();
    await chrome.storage.local.remove('readingCache');
    return true;
  }
  if (message.action === 'connect') {
    if (!matches || message.intent !== intent) return false;
    const existing = (await chrome.storage.local.get('readingCache')).readingCache;
    const { response, candidate } = message;
    const sameAccount = existing?.account === response.account_id &&
      (!cfg.account || cfg.account === response.account_id) &&
      (!existing.apiUrl || existing.apiUrl === candidate.apiUrl) && (!cfg.apiUrl || cfg.apiUrl === candidate.apiUrl);
    const state = sameAccount ? existing : DriftreadOffline.empty(response.account_id);
    const readingCache = { ...DriftreadOffline.apply(state, response), apiUrl: candidate.apiUrl };
    async function restoreCache() {
      if (existing) await chrome.storage.local.set({ readingCache: existing });
      else await chrome.storage.local.remove('readingCache');
    }
    // Prepare cache before session notifications can render the new account.
    await chrome.storage.local.set({ readingCache });
    if (message.intent !== (latestConnectionIntent ?? cfg.connectionIntent)) { await restoreCache(); return false; }
    try { await chrome.storage.session.set({ ...candidate, account: response.account_id }); }
    catch (error) { await restoreCache(); throw error; }
    return true;
  }
  if (!matches || !cfg.account) throw new Error('帳號已切換');
  let state = (await chrome.storage.local.get('readingCache')).readingCache;
  if (message.action === 'prune') {
    if (state && state.account !== cfg.account) await chrome.storage.local.remove('readingCache');
    return;
  }
  if (state?.account !== cfg.account) state = DriftreadOffline.empty(cfg.account);
  if (message.action === 'pull') {
    state = DriftreadOffline.apply(state, message.response);
    state.pending = state.pending.filter(p => message.response.authorized_article_ids.includes(p.articleId));
  } else if (message.action === 'acknowledge') {
    const operation = message.operation;
    state = { ...state, pending: state.pending.filter(p => !(p.articleId === operation.articleId &&
      p.kind === operation.kind && p.enabled === operation.enabled)) };
  } else if (message.action === 'queue') {
    if (!DriftreadOffline.usable(state, cfg.account)) throw new Error('請連線並同步閱讀');
    state = DriftreadOffline.queue(state, message.articleId, message.kind, message.enabled);
  } else throw new Error('不支援的儲存操作');
  await chrome.storage.local.set({ readingCache: state });
  return state;
}
