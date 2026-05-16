// Update the action badge when the content script reports feeds.
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg && msg.type === 'driftread:found' && sender.tab) {
    const tabId = sender.tab.id;
    const text = msg.count > 0 ? String(msg.count) : '';
    chrome.action.setBadgeText({ tabId, text });
    chrome.action.setBadgeBackgroundColor({ tabId, color: '#1976d2' });
  }
});
