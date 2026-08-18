import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { signal } from '@angular/core';
import { MyFeeds } from './my-feeds';
import { MeService } from '../../services/me';
import { AuthService } from '../../services/auth';
import { SubscriptionService } from '../../services/subscription';
import { ToastService } from '../../ui/toast/toast';
import { Feed } from '../../models';

const feed = (id: string): Feed => ({
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
});

describe('MyFeeds stale-response handling', () => {
  let session: ReturnType<typeof signal<{ user: { id: string } } | null>>;
  let subs: {
    syncCalls: Feed[][];
    markUnsubscribedCalls: string[];
    beginFetch: () => number;
    ids: ReturnType<typeof signal<ReadonlySet<string>>>;
    sync: (feeds: Feed[], asOf?: number) => void;
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
  }) {
    session = signal<{ user: { id: string } } | null>({ user: { id: 'user-1' } });
    const me: {
      listSubscriptions: () => ReturnType<MeService['listSubscriptions']>;
      unsubscribe: () => ReturnType<MeService['unsubscribe']>;
    } = {
      listSubscriptions: options?.listSubscriptions ?? (() => of([])),
      unsubscribe: options?.unsubscribe ?? (() => of(undefined)),
    };
    subs = {
      syncCalls: [],
      markUnsubscribedCalls: [],
      beginFetch: () => 0,
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
    const pending = new Subject<Feed[]>();
    const page = setup({ listSubscriptions: () => pending }); // initial load is in flight as user-1

    session.set(null); // signs out before the response returns
    detect();

    pending.next([feed('a')]); // stale response, for a user that is gone
    pending.complete();

    expect(page.feeds()).toEqual([]);
    expect(subs.syncCalls).toEqual([]);
  });

  it('drops a listSubscriptions response that arrives after switching accounts, and applies the new account\'s own', () => {
    const requests: Subject<Feed[]>[] = [];
    const page = setup({
      listSubscriptions: () => {
        const subject = new Subject<Feed[]>();
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
    const pending = new Subject<Feed[]>();
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
});
