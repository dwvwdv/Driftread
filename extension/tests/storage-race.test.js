const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const Offline = require('../offline.js');
const { background } = require('./helpers/background');

const apiUrl = 'https://example.test/api';
function snapshot(account = 'a') {
  return { account_id: account, changed: true, cursor: `${account}-snapshot`, authorized_article_ids: ['article'],
    items: [{ id: 'article', title: `${account} private`, summary: `${account} summary`,
      url: 'https://source.test/article', is_read: false, bookmark_types: [] }] };
}
function element() {
  return { children: [], style: {}, textContent: '', value: '', hidden: true, handlers: {},
    addEventListener(event, handler) { this.handlers[event] = handler; },
    replaceChildren(...children) { this.children = children; },
    append(...children) { this.children.push(...children); }, appendChild(child) { this.children.push(child); } };
}
async function settle() { for (let i = 0; i < 200; i++) await Promise.resolve(); }
function harness() {
  const session = { apiUrl, accessToken: 'token-a', account: 'a', connectionId: 'connection-a' };
  const initial = { ...Offline.apply(Offline.empty('a'), snapshot()), apiUrl };
  const local = { readingCache: Offline.queue(initial, 'article', 'favorite', true) };
  const changes = [];
  const h = { session, local, writes: 0, beforeWrite: async () => {}, fetch: async () => snapshot() };
  const storage = {
    session: {
      get: async (_, callback) => { const value = { ...session }; if (callback) callback(value); return value; },
      set: async value => {
        if (value.account) await h.beforeSessionWrite?.(value);
        const changed = Object.fromEntries(Object.entries(value).map(([key, newValue]) => [key, { newValue }]));
        Object.assign(session, value); for (const listener of changes) listener(changed, 'session');
      },
      clear: async () => { for (const key of Object.keys(session)) delete session[key]; },
    },
    local: {
      get: async () => structuredClone(local),
      set: async value => { h.writes++; await h.beforeWrite(value); Object.assign(local, structuredClone(value)); },
      remove: async key => { delete local[key]; },
    }, sync: { remove() {} }, onChanged: { addListener: listener => changes.push(listener) },
  };
  h.worker = background(storage);
  function page(name) {
    const elements = Object.fromEntries(['reading', 'list', 'status', 'sync', 'config-hint', 'open-options',
      'apiUrl', 'accessToken', 'save', 'disconnect', 'saved'].map(id => [id, element()]));
    let id = 0;
    const context = vm.createContext({ URL, URLSearchParams, DriftreadOffline: Offline,
      crypto: { randomUUID: () => `${name}-${++id}` },
      document: { getElementById: key => elements[key], createElement: element, querySelectorAll: () => [] },
      chrome: { storage, runtime: { ...h.worker.runtime(`${name}.html`),
        sendMessage: message => h.worker.runtime(`${name}.html`).sendMessage(message), openOptionsPage() {} },
        permissions: { request: async () => true }, tabs: { query: async () => [{ id: 1 }],
          sendMessage: (_, __, callback) => callback({ feeds: [] }) }, scripting: { executeScript: async () => [] } },
      fetch: async (url, options) => {
        const body = await h.fetch(url, options);
        return { status: body?.status ?? 200, ok: (body?.status ?? 200) < 400, json: async () => body };
      },
    });
    for (const script of ['offline-client.js', `${name}.js`])
      vm.runInContext(fs.readFileSync(require.resolve(`../${script}`), 'utf8'), context);
    return { context, elements };
  }
  h.popup = page('popup'); h.options = page('options');
  h.sync = () => vm.runInContext('syncReading()', h.popup.context);
  h.disconnect = () => h.options.elements.disconnect.handlers.click();
  h.connect = account => {
    h.options.elements.apiUrl.value = apiUrl; h.options.elements.accessToken.value = `token-${account}`;
    return h.options.elements.save.handlers.click();
  };
  h.blockWrite = writeNumber => {
    let release;
    h.beforeWrite = () => h.writes === writeNumber ? new Promise(resolve => { release = resolve; }) : undefined;
    return async () => { await settle(); assert.equal(typeof release, 'function'); release(); };
  };
  return h;
}

for (const phase of ['pull', 'acknowledgement', 'offline action']) {
  for (const transition of ['disconnect', 'switch']) {
    test(`separate popup/options contexts serialize delayed ${phase} before ${transition}`, async () => {
      const h = harness(); await settle();
      let pull = 0;
      h.fetch = async (url, options) => {
        if (options.headers.Authorization === 'Bearer token-b') return snapshot('b');
        if (url.endsWith('/operations')) return { status: 204 };
        pull++; return snapshot();
      };
      const release = h.blockWrite(phase === 'acknowledgement' ? 2 : 1);
      const old = phase === 'offline action'
        ? h.popup.elements.reading.children[0].children[2].onclick() : h.sync();
      // Observe rejection immediately; subsequent pulls are expected to lose the connection.
      const oldResult = Promise.resolve(old).catch(error => error);
      await settle();
      const changing = transition === 'disconnect' ? h.disconnect() : h.connect('b');
      await settle();
      assert.equal(h.session.account, 'a', 'transition must wait for the in-flight storage write');
      await release();
      await Promise.all([oldResult, changing]); await settle();
      if (transition === 'disconnect') {
        assert.equal(h.session.accessToken, undefined); assert.equal(h.local.readingCache, undefined);
      } else {
        assert.equal(h.session.account, 'b'); assert.equal(h.local.readingCache.account, 'b');
        assert.deepEqual(h.local.readingCache.pending, []);
        assert.equal(h.local.readingCache.items[0].title, 'b private');
      }
    });
  }
}

test('a delayed old auth failure and stale popup cleanup cannot clear a new account', async () => {
  const h = harness(); await settle();
  let denied;
  h.fetch = async (_, options) => options.headers.Authorization === 'Bearer token-b'
    ? snapshot('b') : new Promise(resolve => { denied = resolve; });
  const old = h.sync().catch(error => error); await settle();
  await h.connect('b');
  denied({ status: 401 }); await old;
  await vm.runInContext("storageMutation('prune', { connectionId: 'connection-a' })", h.popup.context);
  assert.equal(h.session.account, 'b'); assert.equal(h.local.readingCache.account, 'b');
});

test('disconnect invalidates a separately running candidate response while popup write waits', async () => {
  const h = harness(); await settle();
  let candidate;
  h.fetch = async (_, options) => options.headers.Authorization === 'Bearer token-b'
    ? new Promise(resolve => { candidate = resolve; }) : snapshot();
  const connecting = h.connect('b'); await settle();
  assert.equal(typeof candidate, 'function');
  const release = h.blockWrite(1);
  const old = h.sync().catch(error => error); await settle();
  const disconnecting = h.disconnect(); await settle();
  candidate(snapshot('b')); await connecting;
  await release(); await Promise.all([old, disconnecting]);
  assert.equal(h.session.account, undefined); assert.equal(h.local.readingCache, undefined);
});

for (const failure of ['cache', 'session']) {
  test(`rejected ${failure} storage write reports failure and preserves the established connection`, async () => {
    const h = harness(); await settle();
    const before = structuredClone(h.local.readingCache);
    h.fetch = async () => snapshot('b');
    if (failure === 'cache') h.beforeWrite = async () => { throw new Error('quota'); };
    else h.beforeSessionWrite = async () => { throw new Error('session unavailable'); };
    await h.connect('b');
    assert.equal(h.session.account, 'a'); assert.deepEqual(h.local.readingCache, before);
    assert.match(h.options.elements.saved.textContent, /儲存失敗/);
    assert.equal(h.options.elements.saved.hidden, false);
  });
}

test('worker rejects content-script and external senders before accessing personal storage', async () => {
  const h = harness(); await settle();
  const before = structuredClone(h.local.readingCache);
  for (const sender of [{ id: 'extension-id', url: 'https://source.test/article', tab: { id: 1 } },
    { id: 'other-extension', url: 'chrome-extension://extension-id/options.html' }]) {
    const result = await h.worker.send({ type: 'driftread:storage', action: 'clear' }, sender);
    assert.equal(result.ok, false);
  }
  assert.equal(h.session.account, 'a'); assert.deepEqual(h.local.readingCache, before);
});


test('candidate connection survives service worker idle restart during permission or network wait', async () => {
  const h = harness(); await settle();
  let candidate;
  h.fetch = async () => new Promise(resolve => { candidate = resolve; });
  const connecting = h.connect('b'); await settle();
  assert.equal(typeof candidate, 'function');
  assert.ok(h.session.connectionIntent, 'intent must outlive the worker realm');
  h.worker = background(h.popup.context.chrome.storage);
  candidate(snapshot('b')); await connecting;
  assert.equal(h.session.account, 'b'); assert.equal(h.local.readingCache.account, 'b');
  assert.equal(h.options.elements.saved.textContent, '帳號已連線');
});

test('coordinator preserves safe account-change and queue-limit product errors', async () => {
  const h = harness(); await settle();
  await assert.rejects(vm.runInContext("storageMutation('queue', { connectionId: 'stale', articleId: 'article', kind: 'read', enabled: true })",
    h.popup.context), /帳號已切換/);
  const state = Offline.empty('a'); state.updatedAt = Date.now();
  state.items = Array.from({ length: 101 }, (_, id) => ({ id: String(id) }));
  state.pending = state.items.slice(0, 100).map(article => ({ articleId: article.id, kind: 'read', enabled: true }));
  h.local.readingCache = state;
  await assert.rejects(vm.runInContext("storageMutation('queue', { connectionId: 'connection-a', articleId: '100', kind: 'read', enabled: true })",
    h.popup.context), /100 篇/);
  assert.equal(h.local.readingCache.pending.length, 100);
});
