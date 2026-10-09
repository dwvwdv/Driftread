const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Offline = require('../offline.js');

function optionsHarness({ granted = true, request } = {}) {
  const html = fs.readFileSync(require.resolve('../options.html'), 'utf8');
  const handlers = {};
  // Read actual initial visibility from the template so text-only updates cannot pass.
  const elements = Object.fromEntries(['apiUrl', 'accessToken', 'saved', 'disconnect', 'save'].map(id => {
    const tag = html.match(new RegExp(`<[^>]+\\bid="${id}"[^>]*>`))[0];
    const display = tag.match(/style="[^"]*display\s*:\s*([^;"\s]+)/)?.[1] || '';
    return [id, { value: '', textContent: '', hidden: /\shidden(?:\s|>|=)/.test(tag),
      style: { display }, addEventListener: (_, handler) => handlers[id] = handler }];
  }));
  const session = {};
  const local = {};
  const requests = [];
  let connectionId = 0;
  const context = vm.createContext({
    document: { getElementById: id => elements[id] }, URL,
    crypto: { randomUUID: () => `connection-${++connectionId}` },
    DriftreadOffline: Offline,
    chrome: {
      storage: {
        session: {
          get: (keys, callback) => callback({ ...session }),
          set: async value => Object.assign(session, value),
        },
        local: { set: async value => Object.assign(local, value) },
        sync: { remove: () => {} },
      },
      permissions: { request: async () => granted },
    },
    clearConnection: async () => {
      for (const key of Object.keys(session)) delete session[key];
      for (const key of Object.keys(local)) delete local[key];
    },
    connection: async () => ({ ...session }),
    personalRequest: async (path, options, cfg) => {
      requests.push({ path, cfg });
      return request ? request() : { account_id: 'a', changed: true, cursor: 'one', items: [] };
    },
  });
  vm.runInContext(fs.readFileSync(require.resolve('../options.js'), 'utf8'), context);
  elements.apiUrl.value = 'https://example.test/api';
  elements.accessToken.value = 'token-a';
  const visibleMessage = () => {
    assert.equal(elements.saved.hidden, false, 'feedback is still hidden by the template');
    assert.notEqual(elements.saved.style.display, 'none', 'feedback has display:none');
    return elements.saved.textContent;
  };
  return { elements, handlers, session, local, requests, visibleMessage };
}

test('successful connection displays feedback and stores the account snapshot', async () => {
  const h = optionsHarness();
  assert.equal(h.elements.saved.hidden, true);
  await h.handlers.save();
  assert.equal(h.visibleMessage(), '帳號已連線');
  assert.equal(h.session.account, 'a');
  assert.equal(h.local.readingCache.account, 'a');
  assert.equal(h.elements.accessToken.value, '');
});

test('request failure displays an actionable message', async () => {
  const h = optionsHarness({ request: async () => { throw new Error('無法連線，請稍後再試'); } });
  await h.handlers.save();
  assert.equal(h.visibleMessage(), '無法連線，請稍後再試');
  assert.equal(h.local.readingCache, undefined);
});

test('permission denial displays feedback without starting an API request', async () => {
  const h = optionsHarness({ granted: false });
  await h.handlers.save();
  assert.equal(h.visibleMessage(), '需要 API 網域權限才能連線');
  assert.equal(h.requests.length, 0);
});

test('disconnect displays feedback and clears personal data', async () => {
  const h = optionsHarness();
  await h.handlers.save();
  await h.handlers.disconnect();
  assert.equal(h.visibleMessage(), '已中斷連線並清除離線資料');
  assert.equal(h.session.account, undefined);
  assert.equal(h.local.readingCache, undefined);
});

test('a late connection error cannot replace visible disconnect feedback', async () => {
  let rejectRequest;
  const h = optionsHarness({ request: () => new Promise((_, reject) => { rejectRequest = reject; }) });
  const connecting = h.handlers.save();
  for (let i = 0; i < 20 && !rejectRequest; i++) await Promise.resolve();
  assert.equal(typeof rejectRequest, 'function');
  await h.handlers.disconnect();
  rejectRequest(new Error('舊連線失敗'));
  await connecting;
  assert.equal(h.visibleMessage(), '已中斷連線並清除離線資料');
});
