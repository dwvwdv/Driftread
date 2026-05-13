// Scan the current page for <link rel="alternate"> feed declarations and
// report them back to the extension. Runs in every tab at document_idle.
(function () {
  function detectFeeds() {
    const links = document.querySelectorAll('link[rel="alternate"]');
    const feeds = [];
    links.forEach((link) => {
      const type = (link.getAttribute('type') || '').toLowerCase();
      const href = link.getAttribute('href');
      if (!href) return;
      if (
        type.includes('rss') ||
        type.includes('atom') ||
        type === 'application/xml' ||
        type === 'text/xml'
      ) {
        const absolute = new URL(href, document.baseURI).href;
        feeds.push({
          href: absolute,
          title: link.getAttribute('title') || document.title,
          type,
        });
      }
    });
    return feeds;
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === 'driftread:detect') {
      sendResponse({ feeds: detectFeeds(), pageUrl: location.href, pageTitle: document.title });
    }
    return true;
  });

  const found = detectFeeds();
  chrome.runtime.sendMessage({ type: 'driftread:found', count: found.length });
})();
