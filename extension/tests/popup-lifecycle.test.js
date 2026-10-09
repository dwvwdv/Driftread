const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Offline = require('../offline.js');

const apiUrl = 'https://example.test/api';
const snapshot = { account_id: 'a', changed: true, cursor: 'one', authorized_article_ids: ['article'],
  items: [{ id: 'article', title: 'Private article', summary: 'Private summary',
    url: 'https://source.test/article', is_read: false, bookmark_types: [] }] };
function pendingCache(updatedAt = Date.now()) {
  const state = Offline.apply(Offline.empty('a'), snapshot, updatedAt);
  return { ...Offline.queue(state, 'article', 'favorite', true), apiUrl };
}
function element() {
  return { children: [], style: {}, textContent: '', value: '', handlers: {},
    addEventListener(event, handler) { this.handlers[event] = handler; },
    replaceChildren(...children) { this.children = children; },
    append(...children) { this.children.push(...children); },
    appendChild(child) { this.children.push(child); } };
}
async function settle() { for (let i = 0; i < 40; i++) await Promise.resolve(); }
function popupHarness(initialSession, state) {
  const session = { ...initialSession };
  const local = { readingCache: structuredClone(state) };
  const removals = [], listeners = [], requests = [];
  const elements = Object.fromEntries(['list', 'reading', 'status', 'sync', 'config-hint', 'open-options',
    'apiUrl', 'accessToken', 'saved', 'save', 'disconnect'].map(id => [id, element()]));
  let nextConnection = 0;
  const context = vm.createContext({ URL, URLSearchParams, DriftreadOffline: Offline,
    crypto: { randomUUID: () => `connection-${++nextConnection}` },
    document: { getElementById: id => elements[id], createElement: element, querySelectorAll: () => [] },
    chrome: {
      tabs: { query: async () => [{ id: 1 }], sendMessage: (id, message, callback) => callback({ feeds: [] }) },
      scripting: { executeScript: async () => [] }, runtime: { openOptionsPage() {} },
      permissions: { request: async () => true },
      storage: {
        session: {
          get: async (keys, callback) => { const cfg = { ...session }; if (callback) callback(cfg); return cfg; },
          set: async value => {
            const changes = Object.fromEntries(Object.entries(value).map(([key, newValue]) =>
              [key, { oldValue: session[key], newValue }]));
            Object.assign(session, value);
            for (const listener of listeners) listener(changes, 'session');
          },
          clear: async () => { for (const key of Object.keys(session)) delete session[key]; },
        },
        local: {
          get: async () => structuredClone(local),
          set: async value => Object.assign(local, structuredClone(value)),
          remove: async key => { removals.push(key); delete local[key]; },
        },
        sync: { remove() {} }, onChanged: { addListener: listener => listeners.push(listener) },
      },
    },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { status: 200, ok: true, json: async () => structuredClone(snapshot) };
    },
  });
  vm.runInContext(fs.readFileSync(require.resolve('../offline-client.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(require.resolve('../popup.js'), 'utf8'), context);
  return { session, local, elements, removals, requests,
    async reconnect() {
      vm.runInContext(fs.readFileSync(require.resolve('../options.js'), 'utf8'), context);
      elements.apiUrl.value = apiUrl;
      elements.accessToken.value = 'token-a';
      await elements.save.handlers.click();
      await settle();
    } };
}

test('opening popup after session loss hides private content and preserves queue for same-account reconnect', async () => {
  const state = pendingCache();
  const h = popupHarness({}, state);
  await settle();
  assert.equal(h.elements.reading.children.length, 0);
  assert.match(h.elements.status.textContent, /請連線/);
  assert.deepEqual(h.local.readingCache, state);
  assert.deepEqual(h.removals, []);
  await h.reconnect();
  assert.equal(h.requests[0].options.headers.Authorization, 'Bearer token-a');
  assert.equal(h.session.account, 'a');
  assert.deepEqual(h.local.readingCache.pending, state.pending);
  assert.equal(h.elements.reading.children.length, 1);
  assert.equal(h.elements.reading.children[0].children[0].textContent, 'Private article');
  assert.deepEqual(h.removals, []);
});

test('opening popup under a different active account removes the old account cache without rendering it', async () => {
  const h = popupHarness({ apiUrl, account: 'b', accessToken: 'token-b', connectionId: 'connection-b' }, pendingCache());
  await settle();
  assert.equal(h.elements.reading.children.length, 0);
  assert.equal(h.local.readingCache, undefined);
  assert.deepEqual(h.removals, ['readingCache']);
});

test('expired same-account summaries remain hidden while pending queue waits for renewed sync', async () => {
  const state = pendingCache(Date.now() - 25 * 60 * 60 * 1000);
  const h = popupHarness({ apiUrl, account: 'a', accessToken: 'token-a', connectionId: 'connection-a' }, state);
  await settle();
  assert.equal(h.elements.reading.children.length, 0);
  assert.match(h.elements.status.textContent, /24 小時/);
  assert.deepEqual(h.local.readingCache, state);
  assert.deepEqual(h.removals, []);
});
