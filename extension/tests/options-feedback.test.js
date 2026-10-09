const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Offline = require('../offline.js');

function optionsHarness({ granted = true, request, status = 200, initialSession = {}, initialCache,
  beforeCacheWrite, beforeSessionWrite, onSessionWrite } = {}) {
  const html = fs.readFileSync(require.resolve('../options.html'), 'utf8');
  const handlers = {};
  // Read actual initial visibility from the template so text-only updates cannot pass.
  const elements = Object.fromEntries(['apiUrl', 'accessToken', 'saved', 'disconnect', 'save'].map(id => {
    const tag = html.match(new RegExp(`<[^>]+\\bid="${id}"[^>]*>`))[0];
    const display = tag.match(/style="[^"]*display\s*:\s*([^;"\s]+)/)?.[1] || '';
    return [id, { value: '', textContent: '', hidden: /\shidden(?:\s|>|=)/.test(tag),
      style: { display }, addEventListener: (_, handler) => handlers[id] = handler }];
  }));
  const session = { ...initialSession };
  const local = initialCache ? { readingCache: structuredClone(initialCache) } : {};
  const requests = [];
  let connectionId = 0;
  const context = vm.createContext({
    document: { getElementById: id => elements[id] }, URL, URLSearchParams,
    crypto: { randomUUID: () => `connection-${++connectionId}` },
    DriftreadOffline: Offline,
    chrome: {
      storage: {
        session: {
          get: async (keys, callback) => {
            const value = { ...session };
            if (callback) callback(value);
            return value;
          },
          set: async value => {
            if (beforeSessionWrite) await beforeSessionWrite(value);
            Object.assign(session, value);
            if (onSessionWrite) await onSessionWrite(session, local);
          },
          clear: async () => { for (const key of Object.keys(session)) delete session[key]; },
        },
        local: {
          get: async () => structuredClone(local),
          set: async value => {
            if (beforeCacheWrite) await beforeCacheWrite(value);
            Object.assign(local, structuredClone(value));
          },
          remove: async key => { delete local[key]; },
        },
        sync: { remove: () => {} },
      },
      permissions: { request: async () => granted },
    },
    fetch: async (url, options) => {
      requests.push({ url, options });
      const body = request ? await request(url, options) : { account_id: 'a', changed: true, cursor: 'one', items: [] };
      return { status, ok: status < 400, json: async () => body };
    },
  });
  vm.runInContext(fs.readFileSync(require.resolve('../offline-client.js'), 'utf8'), context);
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

const oldSession = { apiUrl: 'https://example.test/api', accessToken: 'old-token',
  account: 'a', connectionId: 'old-connection' };
const oldCache = { ...Offline.empty('a'), apiUrl: oldSession.apiUrl,
  items: [{ id: 'article', title: 'Offline article' }],
  pending: [{ articleId: 'article', kind: 'favorite', enabled: true }] };

test('failed candidate token or network retains the old connection and pending work', async () => {
  for (const failure of [{ status: 401 }, { status: 403 },
    { request: async () => { throw new Error('offline'); } }]) {
    const h = optionsHarness({ ...failure, initialSession: oldSession, initialCache: oldCache });
    await h.handlers.save();
    assert.deepEqual(h.session, oldSession);
    assert.deepEqual(h.local.readingCache, oldCache);
    assert.ok(h.visibleMessage());
    assert.equal(h.requests[0].options.headers.Authorization, 'Bearer token-a');
  }
});

test('same-account token renewal keeps queued intent while replacing the snapshot', async () => {
  const h = optionsHarness({ initialSession: oldSession, initialCache: oldCache });
  await h.handlers.save();
  assert.equal(h.session.accessToken, 'token-a');
  assert.notEqual(h.session.connectionId, oldSession.connectionId);
  assert.deepEqual(h.local.readingCache.pending, oldCache.pending);
  assert.deepEqual(h.local.readingCache.items, []);
  assert.equal(h.local.readingCache.account, 'a');
});

test('reconnecting after browser session loss preserves the matching API and account queue', async () => {
  const h = optionsHarness({ initialCache: oldCache });
  await h.handlers.save();
  assert.equal(h.session.account, 'a');
  assert.deepEqual(h.local.readingCache.pending, oldCache.pending);
});

test('candidate account is isolated and replaces old data only after successful sync', async () => {
  let resolveRequest;
  const h = optionsHarness({ initialSession: oldSession, initialCache: oldCache,
    request: () => new Promise(resolve => { resolveRequest = resolve; }) });
  const connecting = h.handlers.save();
  for (let i = 0; i < 20 && !resolveRequest; i++) await Promise.resolve();
  assert.equal(typeof resolveRequest, 'function');
  assert.deepEqual(h.session, oldSession);
  assert.deepEqual(h.local.readingCache, oldCache);
  resolveRequest({ account_id: 'b', changed: true, cursor: 'b-one', items: [{ id: 'b-article' }] });
  await connecting;
  assert.equal(h.session.account, 'b');
  assert.equal(h.local.readingCache.account, 'b');
  assert.deepEqual(h.local.readingCache.pending, []);
  assert.deepEqual(h.local.readingCache.items, [{ id: 'b-article' }]);
});

test('switching API deployments never transfers queued work even for the same account ID', async () => {
  const h = optionsHarness({ initialSession: oldSession, initialCache: oldCache });
  h.elements.apiUrl.value = 'https://another.test/api';
  await h.handlers.save();
  assert.equal(h.session.apiUrl, 'https://another.test/api');
  assert.deepEqual(h.local.readingCache.pending, []);
  assert.equal(h.local.readingCache.apiUrl, 'https://another.test/api');
});

test('malformed initial snapshot leaves the established connection and cache unchanged', async () => {
  for (const response of [{ account_id: 'a', changed: true, cursor: 'bad', items: null },
    { changed: true, cursor: 'bad', items: [] },
    { account_id: 'a', changed: false, cursor: 'bad', items: [] }]) {
    const h = optionsHarness({ initialSession: oldSession, initialCache: oldCache, request: async () => response });
    await h.handlers.save();
    assert.deepEqual(h.session, oldSession);
    assert.deepEqual(h.local.readingCache, oldCache);
    assert.match(h.visibleMessage(), /原連線已保留/);
  }
});

test('session change observers see the matching new snapshot instead of deleting it', async () => {
  let observed = false;
  const h = optionsHarness({ initialSession: oldSession, initialCache: oldCache,
    request: async () => ({ account_id: 'b', changed: true, cursor: 'b-one', items: [] }),
    onSessionWrite: async (session, local) => {
      observed = true;
      // Mirrors popup's mismatch removal on session.account notifications.
      if (local.readingCache.account !== session.account) delete local.readingCache;
    } });
  await h.handlers.save();
  assert.equal(observed, true);
  assert.equal(h.local.readingCache.account, 'b');
});

test('disconnect waits for in-flight cache or session writes and leaves no resurrected data', async () => {
  for (const write of ['beforeCacheWrite', 'beforeSessionWrite']) {
    let resolveWrite;
    let blocked = false;
    const h = optionsHarness({ initialSession: oldSession, initialCache: oldCache,
      [write]: () => {
        if (blocked) return;
        blocked = true;
        return new Promise(resolve => { resolveWrite = resolve; });
      } });
    const connecting = h.handlers.save();
    for (let i = 0; i < 40 && !resolveWrite; i++) await Promise.resolve();
    assert.equal(typeof resolveWrite, 'function');
    const disconnecting = h.handlers.disconnect();
    resolveWrite();
    await Promise.all([connecting, disconnecting]);
    assert.equal(h.session.account, undefined);
    assert.equal(h.local.readingCache, undefined);
    assert.equal(h.visibleMessage(), '已中斷連線並清除離線資料');
  }
});

test('cache belonging to an inactive account is not attached to the active account renewal', async () => {
  const h = optionsHarness({ initialSession: { ...oldSession, account: 'b' }, initialCache: oldCache });
  await h.handlers.save();
  assert.deepEqual(h.local.readingCache.pending, []);
});

test('a newer failed candidate invalidates the old in-flight cache commit without losing pending', async () => {
  let resolveWrite;
  let blocked = false;
  const h = optionsHarness({ initialSession: oldSession, initialCache: oldCache,
    request: async (_, options) => {
      if (options.headers.Authorization === 'Bearer bad-token') throw new Error('candidate failed');
      return { account_id: 'a', changed: true, cursor: 'one', items: [] };
    },
    beforeCacheWrite: () => {
      if (blocked) return;
      blocked = true;
      return new Promise(resolve => { resolveWrite = resolve; });
    } });
  const first = h.handlers.save();
  for (let i = 0; i < 40 && !resolveWrite; i++) await Promise.resolve();
  assert.equal(typeof resolveWrite, 'function');
  h.elements.accessToken.value = 'bad-token';
  const second = h.handlers.save();
  resolveWrite();
  await Promise.all([first, second]);
  assert.deepEqual(h.session, oldSession);
  assert.deepEqual(h.local.readingCache, oldCache);
  assert.equal(h.visibleMessage(), 'candidate failed');
});

test('the latest successful connection wins when an older session write is still in flight', async () => {
  let resolveWrite;
  let blocked = false;
  const h = optionsHarness({ initialSession: oldSession, initialCache: oldCache,
    request: async (_, options) => ({ account_id: options.headers.Authorization === 'Bearer token-b' ? 'b' : 'a',
      changed: true, cursor: 'one', items: [] }),
    beforeSessionWrite: () => {
      if (blocked) return;
      blocked = true;
      return new Promise(resolve => { resolveWrite = resolve; });
    } });
  const first = h.handlers.save();
  for (let i = 0; i < 40 && !resolveWrite; i++) await Promise.resolve();
  assert.equal(typeof resolveWrite, 'function');
  h.elements.accessToken.value = 'token-b';
  const second = h.handlers.save();
  for (let i = 0; i < 40; i++) await Promise.resolve();
  resolveWrite();
  await Promise.all([first, second]);
  assert.equal(h.session.account, 'b');
  assert.equal(h.session.accessToken, 'token-b');
  assert.equal(h.local.readingCache.account, 'b');
  assert.deepEqual(h.local.readingCache.pending, []);
});

test('a later candidate response still commits after an older session write finishes', async () => {
  let resolveWrite, resolveCandidate;
  let blocked = false;
  const h = optionsHarness({ initialSession: oldSession, initialCache: oldCache,
    request: async (_, options) => {
      if (options.headers.Authorization === 'Bearer token-b') {
        return new Promise(resolve => { resolveCandidate = resolve; });
      }
      return { account_id: 'a', changed: true, cursor: 'one', items: [] };
    },
    beforeSessionWrite: () => {
      if (blocked) return;
      blocked = true;
      return new Promise(resolve => { resolveWrite = resolve; });
    } });
  const first = h.handlers.save();
  for (let i = 0; i < 40 && !resolveWrite; i++) await Promise.resolve();
  assert.equal(typeof resolveWrite, 'function');
  h.elements.accessToken.value = 'token-b';
  const second = h.handlers.save();
  resolveWrite();
  await first;
  for (let i = 0; i < 40 && !resolveCandidate; i++) await Promise.resolve();
  assert.equal(typeof resolveCandidate, 'function');
  resolveCandidate({ account_id: 'b', changed: true, cursor: 'two', items: [] });
  await second;
  assert.equal(h.session.account, 'b');
  assert.equal(h.session.accessToken, 'token-b');
  assert.equal(h.local.readingCache.account, 'b');
});
