/**
 * Carries a feed URL a signed-out reader tried to import across the redirect
 * to /login and back (see Discover.importFeed(), Login.submit()).
 *
 * Deliberately sessionStorage, not a query param: a query param would let
 * anyone craft `/login?importFeedUrl=<attacker URL>` and have it silently
 * fetched and imported into the global catalog — writing attacker-controlled
 * feed metadata and auto-subscribing the victim — the moment someone who
 * clicks the link happens to log in, with no click on Import at all. That
 * defeats the entire point of requiring a signed-in *user action* to write
 * to the catalog (docs/SECURITY.md #30; Codex review on PR #52). Only this
 * app's own JS, running on Discover after a real click, can set this key.
 *
 * Also bound to a one-time nonce carried in the login redirect's query
 * string, not just "any subsequent successful login": sessionStorage
 * outlives a single navigation, so without the nonce check, a reader who
 * clicks Import, backs out of /login without submitting, and later signs in
 * normally for something unrelated would silently have the abandoned import
 * resumed — treating that unrelated login as confirmation of a decision they
 * never made (second-round Codex review on PR #52). The nonce ties resumption
 * to the exact redirect Discover generated, not to login as a generic event.
 */
const KEY = 'driftread:pendingImportFeedUrl';

interface PendingImport {
  url: string;
  nonce: string;
}

/** Stashes the URL and returns a nonce to carry in the login redirect's query string. */
export function setPendingImportFeedUrl(url: string): string {
  const nonce = crypto.randomUUID();
  sessionStorage.setItem(KEY, JSON.stringify({ url, nonce } satisfies PendingImport));
  return nonce;
}

/**
 * Reads and clears the pending URL in one step, but only returns it when
 * `nonce` matches the one `setPendingImportFeedUrl()` handed out — any other
 * nonce (or none) clears a stale entry without resuming it.
 */
export function takePendingImportFeedUrl(nonce: string): string | null {
  const raw = sessionStorage.getItem(KEY);
  if (raw === null) return null;
  sessionStorage.removeItem(KEY);
  try {
    const stored = JSON.parse(raw) as PendingImport;
    return stored.nonce === nonce ? stored.url : null;
  } catch {
    return null;
  }
}
