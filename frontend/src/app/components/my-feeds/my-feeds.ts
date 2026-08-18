import { ChangeDetectionStrategy, Component, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MeService } from '../../services/me';
import { AuthService } from '../../services/auth';
import { SubscriptionService } from '../../services/subscription';
import { Feed, OpmlImportResult } from '../../models';
import { apiMessage } from '../../shared/http-errors';
import { ObCallout } from '../../ui/callout/callout';
import { ObIcon } from '../../ui/icon/icon';
import { ObLoading, ObEmpty } from '../../ui/state/state';
import { ObPageHeader } from '../../ui/page-header/page-header';
import { ToastService } from '../../ui/toast/toast';

/** Subscriptions, plus OPML interchange with other readers. */
@Component({
  selector: 'app-my-feeds',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, ObCallout, ObIcon, ObLoading, ObEmpty, ObPageHeader],
  templateUrl: './my-feeds.html',
  styleUrl: './my-feeds.scss',
})
export class MyFeeds {
  protected auth = inject(AuthService);
  private me = inject(MeService);
  private subs = inject(SubscriptionService);
  private toast = inject(ToastService);

  feeds = signal<Feed[]>([]);
  loading = signal(false);
  importResult = signal<OpmlImportResult | null>(null);
  showFailures = signal(false);
  exporting = signal(false);
  importing = signal(false);

  /** User id the subscriptions have already been loaded for. */
  private loadedFor: string | null = null;

  /**
   * `asOf` ticket of the last response actually applied to `feeds`. Two
   * loads for the *same* user id can still race (initial load vs. a
   * post-OPML-import reload); the `requestedFor` identity check alone can't
   * tell an older-but-slower response from a newer-but-faster one that
   * already landed. Mirrors SubscriptionService's own lastAppliedAsOf.
   */
  private lastAppliedAsOf = -1;

  /**
   * Set while a load() triggered by the reconciliation effect below is in
   * flight. SubscriptionService.sync() (called from every load()'s success
   * handler) always publishes a fresh `ids` Set, even when its contents are
   * unchanged — so without this guard, a feed that's still missing right
   * after that reload (e.g. its subscribe POST elsewhere hasn't committed
   * yet) would trigger another load() immediately, and again, for as long
   * as the write stays unconfirmed.
   */
  private reloadingForMissingId = false;

  constructor() {
    // Not `if (session()) load()` in ngOnInit: AuthService restores the persisted
    // session asynchronously, so on a direct visit that check runs before the
    // session exists and never runs again. The template would then flip from
    // "please sign in" to an empty subscription list once the session landed.
    //
    // Keyed on the user id so signing out and back in — or switching accounts —
    // reloads rather than showing the previous user's list.
    effect(() => {
      const userId = this.auth.session()?.user?.id ?? null;
      if (!userId) {
        this.loadedFor = null;
        return;
      }
      if (this.loadedFor === userId) return;
      this.loadedFor = userId;
      this.load();
    });

    // Reconciles this page's own rendered list with a subscribe/unsubscribe
    // that lands while it's open — e.g. (un)subscribing from feed detail,
    // then navigating here before that request settles: this page's own
    // load() can return the pre-write snapshot, and nothing else would tell
    // it once SubscriptionService's cache (the actual source of truth)
    // catches up. Removals are applied directly; SubscriptionService only
    // holds ids, not full Feed objects, so a newly subscribed id triggers an
    // actual reload to fetch that feed's data rather than being inserted
    // here.
    effect(() => {
      const ids = this.subs.ids();
      // hasNewId is computed from `current` inside the updater callback
      // (not via this.feeds() directly) so this effect only tracks
      // this.subs.ids() as a dependency — reading this.feeds() here too
      // would make the .update() write below re-trigger this same effect.
      let hasNewId = false;
      this.feeds.update((current) => {
        hasNewId = [...ids].some((id) => !current.some((f) => f.id === id));
        return current.filter((f) => ids.has(f.id));
      });
      if (hasNewId && this.loadedFor && !this.reloadingForMissingId) {
        this.reloadingForMissingId = true;
        this.load();
      }
    });
  }

  load(): void {
    this.loading.set(true);
    // Captured so a response that outlives the user it was fetched for
    // (signed out, or switched accounts, before this returned) gets dropped
    // instead of showing this page's own list under the wrong account, or
    // passing that stale data into the shared SubscriptionService cache via
    // sync() below — sync() itself has no way to know which user a given
    // Feed[] was fetched for, so this check has to happen here.
    const requestedFor = this.auth.session()?.user?.id ?? null;
    // See SubscriptionService.beginFetch(): drawn before issuing the
    // request, so sync() below can correctly order this snapshot against
    // writes and against any other fetch (e.g. SubscriptionService's own
    // load()) racing it.
    const asOf = this.subs.beginFetch();
    this.me.listSubscriptions().subscribe({
      next: (feeds) => {
        // Cleared unconditionally, regardless of which call site started
        // this particular load(): this specific flight is over either way,
        // and the reconciliation effect will re-trigger another one itself
        // if the id it cared about is still missing once ids() next changes.
        this.reloadingForMissingId = false;
        // Checked before touching `loading`: if this is a stale response
        // arriving after an account switch, a genuinely in-flight load for
        // the new account is likely still running, and clearing `loading`
        // here would flash an empty list before that one lands.
        if ((this.auth.session()?.user?.id ?? null) !== requestedFor) return;
        this.loading.set(false);
        // Guards against a same-user race: an older, slower load() response
        // (e.g. the initial load) arriving after a newer one (e.g. a
        // post-OPML-import reload) already applied. requestedFor alone can't
        // catch this since both requests share the same user id.
        if (asOf < this.lastAppliedAsOf) return;
        this.lastAppliedAsOf = asOf;
        this.feeds.set(feeds);
        // Reconciles the shared subscription cache from the same response,
        // rather than have SubscriptionService.load() fire a second, redundant
        // GET /me/feeds for the very list this page just fetched.
        this.subs.sync(feeds, asOf);
      },
      error: (e: unknown) => {
        this.reloadingForMissingId = false;
        if ((this.auth.session()?.user?.id ?? null) !== requestedFor) return;
        this.loading.set(false);
        this.toast.danger(apiMessage(e, '讀取訂閱失敗'));
      },
    });
  }

  unsubscribe(feed: Feed): void {
    // Captured so a response arriving after a sign-out/account switch can't
    // remove this feed from whoever is signed in *now*'s list, or tell the
    // shared cache it was unsubscribed for them — same class of bug as
    // SubscriptionService.unsubscribe()'s own guard.
    const requestedFor = this.auth.session()?.user?.id ?? null;
    this.me.unsubscribe(feed.id).subscribe({
      next: () => {
        if (!requestedFor || (this.auth.session()?.user?.id ?? null) !== requestedFor) return;
        this.toast.info(`已取消訂閱：${feed.title}`);
        this.feeds.update((list) => list.filter((f) => f.id !== feed.id));
        // Keeps FeedDetail/FeedList/Discover, which all read SubscriptionService
        // rather than their own copy, in sync without a redundant DELETE of
        // their own.
        this.subs.markUnsubscribed(feed.id);
      },
      error: (e: unknown) => {
        if (!requestedFor || (this.auth.session()?.user?.id ?? null) !== requestedFor) return;
        this.toast.danger(apiMessage(e, '取消訂閱失敗'));
      },
    });
  }

  onOpml(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    this.importing.set(true);
    this.showFailures.set(false);
    this.me.importOpml(file).subscribe({
      next: (result) => {
        this.importing.set(false);
        this.importResult.set(result);
        this.load();
      },
      error: (e: unknown) => {
        this.importing.set(false);
        this.toast.danger(apiMessage(e, 'OPML 匯入失敗'));
      },
    });

    // Lets the same file be picked again after a failed attempt; without this the
    // change event never fires a second time for an identical selection.
    input.value = '';
  }

  /**
   * Fetches the OPML through HttpClient so the auth interceptor attaches the
   * token, then saves it. See MeService.exportOpml — the old <a href> always 401'd.
   */
  exportOpml(): void {
    this.exporting.set(true);
    this.me.exportOpml().subscribe({
      next: (blob) => {
        this.exporting.set(false);
        const url = URL.createObjectURL(blob);
        try {
          const link = document.createElement('a');
          link.href = url;
          link.download = 'driftread.opml';
          link.click();
        } finally {
          // Released either way; a retained object URL pins the blob in memory.
          URL.revokeObjectURL(url);
        }
      },
      error: (e: unknown) => {
        this.exporting.set(false);
        this.toast.danger(apiMessage(e, 'OPML 匯出失敗'));
      },
    });
  }

  toggleFailures(): void {
    this.showFailures.update((open) => !open);
  }

  async copyFailures(): Promise<void> {
    const failed = this.importResult()?.failed ?? [];
    try {
      await navigator.clipboard.writeText(failed.join('\n'));
      this.toast.success('已複製失敗清單');
    } catch {
      // Clipboard access needs a secure context and can be denied outright.
      this.toast.warning('無法存取剪貼簿，請手動選取複製');
    }
  }
}
