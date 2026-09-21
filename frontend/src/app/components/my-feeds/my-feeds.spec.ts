import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { signal } from '@angular/core';
import { MyFeeds } from './my-feeds';
import { MeService } from '../../services/me';
import { AuthService } from '../../services/auth';
import { SubscriptionService } from '../../services/subscription';
import { ToastService } from '../../ui/toast/toast';
import { SubscribedFeed } from '../../models';

const feed = (id: string): SubscribedFeed => ({
  id,
  title: `Feed ${id}`,
  url: `https://example.com/${id}.xml`,
  website_url: null,
  description: null,
  category: null,
  language: null,
  tags: [],
  article_count: 0,
  last_fetched_at: null,
  archived_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  custom_title: null,
});

describe('MyFeeds stale-response handling', () => {
  let session: ReturnType<typeof signal<{ user: { id: string } } | null>>;
  let subs: {
    syncCalls: SubscribedFeed[][];
    markUnsubscribedCalls: string[];
    beginFetch: () => number;
    ids: ReturnType<typeof signal<ReadonlySet<string>>>;
    sync: (feeds: SubscribedFeed[], asOf?: number) => void;
    markUnsubscribed: (id: string) => void;
  };
  let toastInfo: string[];
  let toastDanger: string[];
  // Re-detects changes on the same fixture, which is how a component-owned
  // effect (created via `effect()` in MyFeeds' constructor) actually flushes
  // in tests — TestBed.flushEffects() is for effects whose injector came
  // from TestBed.inject() directly, not from a ComponentFixture.
  let detect: () => void;

  /**
   * `listSubscriptions` defaults to an immediate empty list and `unsubscribe`
   * to an immediate success; pass either to control timing.
   */
  function setup(options?: {
    listSubscriptions?: () => ReturnType<MeService['listSubscriptions']>;
    unsubscribe?: () => ReturnType<MeService['unsubscribe']>;
    updateSubscription?: (
      feedId: string,
      customTitle: string | null,
    ) => ReturnType<MeService['updateSubscription']>;
  }) {
    session = signal<{ user: { id: string } } | null>({ user: { id: 'user-1' } });
    const me: {
      listSubscriptions: () => ReturnType<MeService['listSubscriptions']>;
      unsubscribe: () => ReturnType<MeService['unsubscribe']>;
      updateSubscription: (
        feedId: string,
        customTitle: string | null,
      ) => ReturnType<MeService['updateSubscription']>;
    } = {
      listSubscriptions: options?.listSubscriptions ?? (() => of([])),
      unsubscribe: options?.unsubscribe ?? (() => of(undefined)),
      updateSubscription: options?.updateSubscription ?? (() => of(undefined)),
    };
    let nextAsOf = 0;
    subs = {
      syncCalls: [],
      markUnsubscribedCalls: [],
      beginFetch: () => ++nextAsOf,
      // Mirrors the real SubscriptionService's ids closely enough for
      // MyFeeds' own reconciliation effect (which reads this) to behave
      // sanely across these tests, not just for the dedicated test below
      // that exercises it directly.
      ids: signal<ReadonlySet<string>>(new Set()),
      sync: (feeds) => {
        subs.syncCalls.push(feeds);
        subs.ids.set(new Set(feeds.map((f) => f.id)));
      },
      markUnsubscribed: (id) => {
        subs.markUnsubscribedCalls.push(id);
        const next = new Set(subs.ids());
        next.delete(id);
        subs.ids.set(next);
      },
    };
    toastInfo = [];
    toastDanger = [];

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [MyFeeds],
      providers: [
        provideRouter([]),
        { provide: MeService, useValue: me },
        { provide: AuthService, useValue: { session } },
        { provide: SubscriptionService, useValue: subs },
        {
          provide: ToastService,
          useValue: {
            info: (msg: string) => toastInfo.push(msg),
            danger: (msg: string) => toastDanger.push(msg),
            success: () => {},
            warning: () => {},
          },
        },
      ],
    });

    const fixture = TestBed.createComponent(MyFeeds);
    detect = () => fixture.detectChanges();
    detect(); // triggers the initial load() via the constructor's session effect
    return fixture.componentInstance;
  }

  it('drops a listSubscriptions response that arrives after the user has signed out', () => {
    const pending = new Subject<SubscribedFeed[]>();
    const page = setup({ listSubscriptions: () => pending }); // initial load is in flight as user-1

    session.set(null); // signs out before the response returns
    detect();

    pending.next([feed('a')]); // stale response, for a user that is gone
    pending.complete();

    expect(page.feeds()).toEqual([]);
    expect(subs.syncCalls).toEqual([]);
  });

  it('drops a listSubscriptions response that arrives after switching accounts, and applies the new account\'s own', () => {
    const requests: Subject<SubscribedFeed[]>[] = [];
    const page = setup({
      listSubscriptions: () => {
        const subject = new Subject<SubscribedFeed[]>();
        requests.push(subject);
        return subject;
      },
    });
    // requests[0]: user-1's initial load, still in flight.

    session.set({ user: { id: 'user-2' } }); // switches before it resolves
    detect(); // triggers a fresh load() for user-2 -> requests[1]

    requests[0].next([feed('a')]); // user-1's stale response arrives late
    requests[0].complete();

    // Dropped: user-2's own request (requests[1]) hasn't resolved yet, so
    // nothing should have been applied at all.
    expect(page.feeds()).toEqual([]);
    expect(subs.syncCalls).toEqual([]);

    requests[1].next([feed('b')]); // user-2's own, legitimate response
    requests[1].complete();

    expect(page.feeds()).toEqual([feed('b')]);
    expect(subs.syncCalls).toEqual([[feed('b')]]);
  });

  it('applies a response that arrives while still the same user', () => {
    const page = setup(); // default resolves synchronously during the initial detect()

    expect(page.feeds()).toEqual([]);
    expect(subs.syncCalls).toEqual([[]]);
  });

  it('drops a listSubscriptions error that arrives after the user has signed out', () => {
    const pending = new Subject<SubscribedFeed[]>();
    setup({ listSubscriptions: () => pending });

    session.set(null);
    detect();
    pending.error(new Error('boom'));

    expect(toastDanger).toEqual([]);
  });

  it('drops an unsubscribe response that arrives after switching accounts', () => {
    const pending = new Subject<void>();
    let listCalls = 0;
    const page = setup({
      // First call is user-1's initial load; the second is user-2's own,
      // triggered by the session switch below. Both feed 'a' being current
      // for user-2 too means an unguarded stale unsubscribe would produce a
      // *visibly different, wrong* result — not just coincidentally match.
      listSubscriptions: () => {
        listCalls++;
        return of(listCalls === 1 ? [feed('a')] : [feed('a'), feed('c')]);
      },
      unsubscribe: () => pending,
    });
    expect(page.feeds()).toEqual([feed('a')]);

    page.unsubscribe(feed('a')); // user-1 unsubscribes, still in flight

    session.set({ user: { id: 'user-2' } }); // switches before the response returns
    detect(); // user-2's own load lands synchronously: feeds = [a, c]
    expect(page.feeds()).toEqual([feed('a'), feed('c')]);

    pending.next(); // user-1's stale success arrives late
    pending.complete();

    // Must not drop 'a' from what is now user-2's list — they are still
    // genuinely subscribed to it — nor tell the shared cache user-2
    // unsubscribed from a feed they never touched.
    expect(page.feeds()).toEqual([feed('a'), feed('c')]);
    expect(subs.markUnsubscribedCalls).toEqual([]);
    expect(toastInfo).toEqual([]);
  });

  it('drops a same-user load() response that arrives after a newer same-user load() already applied', () => {
    const requests: Subject<SubscribedFeed[]>[] = [];
    const page = setup({
      listSubscriptions: () => {
        const subject = new Subject<SubscribedFeed[]>();
        requests.push(subject);
        return subject;
      },
    });
    // requests[0]: the initial load from the constructor's session effect, still in flight.

    page.load(); // e.g. a post-OPML-import reload, fired for the same user -> requests[1]

    requests[1].next([feed('b')]); // the newer request resolves first
    requests[1].complete();
    expect(page.feeds()).toEqual([feed('b')]);

    requests[0].next([feed('a')]); // the older, slower request resolves late
    requests[0].complete();

    // Must not overwrite the newer, already-applied result — even though
    // both requests share the same user id, so the requestedFor check alone
    // can't tell them apart.
    expect(page.feeds()).toEqual([feed('b')]);
    expect(subs.syncCalls).toEqual([[feed('b')]]);
  });

  it('drops a feed from the rendered list when it is unsubscribed elsewhere while this page is open', () => {
    const page = setup({ listSubscriptions: () => of([feed('a'), feed('b')]) });
    expect(page.feeds()).toEqual([feed('a'), feed('b')]);

    // Simulates a *different* page (feed detail, feed list, Discover) doing
    // its own successful unsubscribe: SubscriptionService's cache changes,
    // but nothing ever told this page's own `feeds` signal directly.
    subs.ids.set(new Set(['b']));
    detect();

    expect(page.feeds()).toEqual([feed('b')]);
  });

  it('reloads to pick up a feed subscribed elsewhere while this page is open', () => {
    let listCalls = 0;
    const page = setup({
      listSubscriptions: () => {
        listCalls++;
        // First call is the initial load; the second is the automatic
        // reload this test expects once the new id shows up in subs.ids().
        return of(listCalls === 1 ? [feed('a')] : [feed('a'), feed('c')]);
      },
    });
    expect(page.feeds()).toEqual([feed('a')]);
    expect(listCalls).toBe(1);

    // Simulates a *different* page (Discover, Recommendations) completing a
    // subscribe: SubscriptionService's cache gains the new id, but this
    // page has no Feed object for it — only an actual reload can supply one.
    subs.ids.set(new Set(['a', 'c']));
    detect();

    expect(listCalls).toBe(2);
    expect(page.feeds()).toEqual([feed('a'), feed('c')]);
  });

  it('does not fire concurrent reloads while one triggered for a missing id is still in flight', () => {
    const requests: Subject<SubscribedFeed[]>[] = [];
    const page = setup({
      listSubscriptions: () => {
        const subject = new Subject<SubscribedFeed[]>();
        requests.push(subject);
        return subject;
      },
    });
    requests[0].next([]); // initial load resolves empty
    requests[0].complete();
    expect(page.feeds()).toEqual([]);

    // An elsewhere page starts subscribing to 'x': its POST is still
    // pending, but SubscriptionService already optimistically added the id.
    subs.ids.set(new Set(['x']));
    detect();
    expect(requests.length).toBe(2); // triggers exactly one reload

    // A further ids() change arrives (e.g. another optimistic write) while
    // that reload is still unresolved. Must not pile on a second one.
    subs.ids.set(new Set(['x', 'y']));
    detect();
    expect(requests.length).toBe(2);

    requests[1].next([feed('x'), feed('y')]); // the in-flight reload resolves
    requests[1].complete();

    expect(page.feeds()).toEqual([feed('x'), feed('y')]);
    expect(requests.length).toBe(2); // no extra reload was ever fired
  });

  it('saves a trimmed custom title and reflects it in the rendered list', () => {
    const calls: Array<[string, string | null]> = [];
    const page = setup({
      listSubscriptions: () => of([feed('a')]),
      updateSubscription: (feedId, customTitle) => {
        calls.push([feedId, customTitle]);
        return of(undefined);
      },
    });

    page.startRename(feed('a'));
    page.renameValue.set('  My nickname  ');
    page.saveRename(feed('a'));

    expect(calls).toEqual([['a', 'My nickname']]);
    expect(page.feeds()).toEqual([{ ...feed('a'), custom_title: 'My nickname' }]);
    expect(page.renamingId()).toBeNull();
  });

  it('clears the custom title when the rename value is blank', () => {
    const calls: Array<[string, string | null]> = [];
    const page = setup({
      listSubscriptions: () => of([{ ...feed('a'), custom_title: 'Old nickname' }]),
      updateSubscription: (feedId, customTitle) => {
        calls.push([feedId, customTitle]);
        return of(undefined);
      },
    });

    page.startRename({ ...feed('a'), custom_title: 'Old nickname' });
    expect(page.renameValue()).toBe('Old nickname'); // editor seeds from the current value

    page.renameValue.set('   ');
    page.saveRename({ ...feed('a'), custom_title: 'Old nickname' });

    expect(calls).toEqual([['a', null]]);
    expect(page.feeds()).toEqual([{ ...feed('a'), custom_title: null }]);
  });

  it('leaves the list untouched and surfaces a toast when saving a rename fails', () => {
    const page = setup({
      listSubscriptions: () => of([feed('a')]),
      updateSubscription: () => throwError(() => new Error('boom')),
    });

    page.startRename(feed('a'));
    page.saveRename(feed('a'));

    expect(page.feeds()).toEqual([feed('a')]);
    expect(toastDanger.length).toBe(1);
  });

  it('closes the rename editor without saving on cancel', () => {
    const page = setup({ listSubscriptions: () => of([feed('a')]) });

    page.startRename(feed('a'));
    expect(page.renamingId()).toBe('a');

    page.cancelRename();
    expect(page.renamingId()).toBeNull();
  });

  it('ignores a second save while the first is still in flight', () => {
    const calls: Array<[string, string | null]> = [];
    const pending = new Subject<void>();
    const page = setup({
      listSubscriptions: () => of([feed('a')]),
      updateSubscription: (feedId, customTitle) => {
        calls.push([feedId, customTitle]);
        return pending.asObservable();
      },
    });

    page.startRename(feed('a'));
    page.renameValue.set('First');
    page.saveRename(feed('a')); // starts the request; renaming() is now true

    // A second Enter/click before the first resolves — e.g. the double-fire
    // this guard exists for — must not fire another PATCH.
    page.renameValue.set('Second');
    page.saveRename(feed('a'));
    // Nor should it be possible to jump to editing a different feed's title
    // while this save is still pending.
    page.startRename(feed('b'));

    expect(calls).toEqual([['a', 'First']]);
    expect(page.renamingId()).toBe('a');

    pending.next(undefined);
    pending.complete();

    expect(page.feeds()).toEqual([{ ...feed('a'), custom_title: 'First' }]);
    expect(page.renamingId()).toBeNull();
  });

  it('drops a leftover rename editor when the signed-in user changes', () => {
    const page = setup({ listSubscriptions: () => of([feed('a')]) });

    page.startRename(feed('a'));
    page.renameValue.set('Unsaved for user-1');
    expect(page.renamingId()).toBe('a');

    session.set({ user: { id: 'user-2' } });
    detect();

    expect(page.renamingId()).toBeNull();
    expect(page.renameValue()).toBe('');
  });
});
