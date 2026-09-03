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
 */
const KEY = 'driftread:pendingImportFeedUrl';

export function setPendingImportFeedUrl(url: string): void {
  sessionStorage.setItem(KEY, url);
}

/** Reads and clears the pending URL in one step — each stored URL resumes at most once. */
export function takePendingImportFeedUrl(): string | null {
  const url = sessionStorage.getItem(KEY);
  if (url !== null) sessionStorage.removeItem(KEY);
  return url;
}
