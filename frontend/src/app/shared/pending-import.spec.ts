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
});
