// Pure cache/queue operations shared by the popup and Node regression tests.
const DriftreadOffline = {
  empty(account) { return { account, items: [], pending: [], cursor: null, updatedAt: 0 }; },
  queue(state, articleId, kind, enabled) {
    if (!['read', 'favorite', 'read_later'].includes(kind) ||
        !state.items.some(a => a.id === articleId)) throw new Error('Invalid offline action');
    const pending = state.pending.filter(p => !(p.articleId === articleId && p.kind === kind));
    pending.push({ articleId, kind, enabled: Boolean(enabled) });
    return { ...state, pending };
  },
  apply(state, response, now = Date.now()) {
    if (response.account_id !== state.account) throw new Error('Account changed');
    const items = response.changed ? response.items.map(({ content, search_vector, ...item }) => item) : state.items;
    return { ...state, items, cursor: response.cursor, updatedAt: now };
  },
  usable(state, account, now = Date.now()) {
    return Boolean(state && account) && state.account === account && now - state.updatedAt < 24 * 60 * 60 * 1000;
  },
};
if (typeof module !== 'undefined') module.exports = DriftreadOffline;
