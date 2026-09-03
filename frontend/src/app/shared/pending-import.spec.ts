import { setPendingImportFeedUrl, takePendingImportFeedUrl } from './pending-import';

const KEY = 'driftread:pendingImportFeedUrl';

describe('pending-import', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('resumes only with the nonce handed out at stash time', () => {
    const nonce = setPendingImportFeedUrl('https://example.com/feed.xml');

    expect(takePendingImportFeedUrl(nonce)).toBe('https://example.com/feed.xml');
  });

  it('is read-once: a matching nonce only resumes the first time', () => {
    const nonce = setPendingImportFeedUrl('https://example.com/feed.xml');
    takePendingImportFeedUrl(nonce);

    expect(takePendingImportFeedUrl(nonce)).toBeNull();
  });

  it('clears the entry on a mismatched nonce without resuming it', () => {
    setPendingImportFeedUrl('https://example.com/feed.xml');

    expect(takePendingImportFeedUrl('some-other-nonce')).toBeNull();
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it('returns null when nothing is stashed', () => {
    expect(takePendingImportFeedUrl('any-nonce')).toBeNull();
  });

  it('still returns a usable nonce when sessionStorage.setItem throws', () => {
    // Private browsing / disabled storage (third-round Codex review on PR
    // #52) must not stop Discover.importFeed()'s redirect to /login — it
    // just means there's nothing to resume afterward, same as AdminKeyStore's
    // handling of the same failure mode (services/admin-key.ts).
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage disabled');
    });

    const nonce = setPendingImportFeedUrl('https://example.com/feed.xml');

    expect(nonce).toBeTruthy();
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it('resumes nothing when sessionStorage.getItem throws, instead of raising', () => {
    const nonce = setPendingImportFeedUrl('https://example.com/feed.xml');
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage disabled');
    });

    expect(() => takePendingImportFeedUrl(nonce)).not.toThrow();
    expect(takePendingImportFeedUrl(nonce)).toBeNull();
  });

  it('still returns a usable, unique nonce when crypto.randomUUID is unavailable', () => {
    // An insecure (non-HTTPS, non-localhost) origin or an older browser can
    // lack crypto.randomUUID() entirely — throwing there, uncaught, would
    // abort Discover.importFeed() before router.navigate() ever runs
    // (fourth-round Codex review on PR #52).
    vi.spyOn(crypto, 'randomUUID').mockImplementation(() => {
      throw new TypeError('crypto.randomUUID is not a function');
    });

    const first = setPendingImportFeedUrl('https://example.com/feed.xml');
    const second = setPendingImportFeedUrl('https://example.com/feed.xml');

    expect(first).toBeTruthy();
    expect(first).not.toBe(second);
    expect(takePendingImportFeedUrl(second)).toBe('https://example.com/feed.xml');
  });
});
