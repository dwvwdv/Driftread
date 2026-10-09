importScripts('offline.js', 'storage-coordinator.js');

// Update the action badge when the content script reports feeds.
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg && msg.type === 'driftread:found' && sender.tab) {
    const tabId = sender.tab.id;
    const text = msg.count > 0 ? String(msg.count) : '';
    chrome.action.setBadgeText({ tabId, text });
    chrome.action.setBadgeBackgroundColor({ tabId, color: '#1976d2' });
  }
});

// Content scripts and external webpages cannot mutate personal credentials/cache.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== 'driftread:storage') return;
  const allowedPages = ['popup.html', 'options.html'].map(page => chrome.runtime.getURL(page));
  if (sender.id !== chrome.runtime.id || !allowedPages.includes(sender.url)) {
    sendResponse({ ok: false, error: '不允許的儲存請求' }); return;
  }
  dispatchStorageMutation(message).then(
    value => sendResponse({ ok: true, value }),
    error => sendResponse({ ok: false, error: ['帳號已切換', '請連線並同步閱讀',
      '待同步文章已達 100 篇，請先同步', 'Invalid offline action'].includes(error.message)
      ? error.message : '離線資料儲存失敗，請重試' }),
  );
  // Keep the worker/message alive even if the sending popup closes mid-write.
  return true;
});
