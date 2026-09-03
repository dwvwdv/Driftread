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
 *
 * sessionStorage access is wrapped in try/catch, same as AdminKeyStore
 * (services/admin-key.ts): it can throw (private browsing, disabled storage —
 * third-round Codex review on PR #52). Failing here must not stop the click
 * itself from reaching /login — it just means the import can't be resumed
 * automatically, same as if the reader had never clicked at all. Nonce
 * generation gets the same treatment (fourth-round Codex review on PR #52):
 * `crypto.randomUUID()` needs a secure context and a fairly recent browser,
 * and throwing there — outside any try/catch — would abort the click before
 * `router.navigate()` ever runs. The fallback doesn't need to be
 * cryptographically unguessable: this nonce is generated and compared
 * entirely on this one tab, never transmitted anywhere an attacker could
 * observe it.
 */
const KEY = 'driftread:pendingImportFeedUrl';

interface PendingImport {
  url: string;
  nonce: string;
}

function generateNonce(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }
}

/** Stashes the URL and returns a nonce to carry in the login redirect's query string. */
export function setPendingImportFeedUrl(url: string): string {
  const nonce = generateNonce();
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ url, nonce } satisfies PendingImport));
  } catch {
    // Nothing was actually stashed; takePendingImportFeedUrl() will find
    // nothing to resume, which is the correct outcome here.
  }
  return nonce;
}

/**
 * Reads and clears the pending URL in one step, but only returns it when
 * `nonce` matches the one `setPendingImportFeedUrl()` handed out — any other
 * nonce (or none) clears a stale entry without resuming it.
 */
export function takePendingImportFeedUrl(nonce: string): string | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw === null) return null;
    sessionStorage.removeItem(KEY);
    const stored = JSON.parse(raw) as PendingImport;
    return stored.nonce === nonce ? stored.url : null;
  } catch {
    return null;
  }
}
