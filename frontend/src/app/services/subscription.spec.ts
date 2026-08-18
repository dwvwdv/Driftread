import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { Observable, Subject, of, throwError } from 'rxjs';
import { SubscriptionService } from './subscription';
import { AuthService } from './auth';
import { MeService } from './me';
import { Feed } from '../models';

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

describe('SubscriptionService', () => {
  let session: ReturnType<typeof signal<{ user: { id: string } } | null>>;
  let me: {
    listCalls: number;
    subscribeCalls: string[];
    unsubscribeCalls: string[];
    listSubscriptions: () => ReturnType<MeService['listSubscriptions']>;
    subscribe: (id: string) => ReturnType<MeService['subscribe']>;
    unsubscribe: (id: string) => ReturnType<MeService['unsubscribe']>;
  };

  function setup() {
    session = signal<{ user: { id: string } } | null>({ user: { id: 'user-1' } });
    me = {
      listCalls: 0,
      subscribeCalls: [],
      unsubscribeCalls: [],
      listSubscriptions: () => {
        me.listCalls++;
        return of([feed('a'), feed('b')]);
      },
      subscribe: (id: string) => {
        me.subscribeCalls.push(id);
        return of(undefined);
      },
      unsubscribe: (id: string) => {
        me.unsubscribeCalls.push(id);
        return of(undefined);
      },
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        { provide: AuthService, useValue: { session } },
        { provide: MeService, useValue: me },
      ],
    });

    return TestBed.inject(SubscriptionService);
  }

  it('loads subscribed feed ids once a user id is available', () => {
    const svc = setup();
    TestBed.flushEffects();

    expect(svc.isSubscribed('a')).toBe(true);
    expect(svc.isSubscribed('b')).toBe(true);
    expect(svc.isSubscribed('c')).toBe(false);
    expect(svc.loaded()).toBe(true);
  });

  it('does not reload on every signal read for the same user', () => {
    const svc = setup();
    TestBed.flushEffects();
    svc.isSubscribed('a');
    TestBed.flushEffects();

    expect(me.listCalls).toBe(1);
  });

  it('resets to unloaded when the user signs out', () => {
    const svc = setup();
    TestBed.flushEffects();
    session.set(null);
    TestBed.flushEffects();

    expect(svc.isSubscribed('a')).toBe(false);
    expect(svc.loaded()).toBe(false);
  });

  it('marks a feed subscribed immediately, before the request resolves', () => {
    const svc = setup();
    TestBed.flushEffects();
    const pending = new Subject<void>();
    me.subscribe = (id: string) => {
      me.subscribeCalls.push(id);
      return pending;
    };

    svc.subscribe('c');

    expect(svc.isSubscribed('c')).toBe(true);
    expect(svc.isPending('c')).toBe(true);

    pending.next();
    pending.complete();
    expect(svc.isPending('c')).toBe(false);
  });

  it('rolls back the optimistic subscribe on failure', () => {
    const svc = setup();
    TestBed.flushEffects();
    me.subscribe = (id: string) => {
      me.subscribeCalls.push(id);
      return throwError(() => new Error('boom'));
    };
    let errorSeen: unknown;

    svc.subscribe('c', (err) => (errorSeen = err));

    expect(svc.isSubscribed('c')).toBe(false);
    expect(svc.isPending('c')).toBe(false);
    expect(errorSeen).toBeInstanceOf(Error);
  });

  it('rolls back the optimistic unsubscribe on failure', () => {
    const svc = setup();
    TestBed.flushEffects();
    me.unsubscribe = (id: string) => {
      me.unsubscribeCalls.push(id);
      return throwError(() => new Error('boom'));
    };

    svc.unsubscribe('a');

    expect(svc.isSubscribed('a')).toBe(true);
  });

  it('ignores a second subscribe call while the first is still in flight', () => {
    const svc = setup();
    TestBed.flushEffects();
    const pending = new Subject<void>();
    me.subscribe = (id: string) => {
      me.subscribeCalls.push(id);
      return pending;
    };

    svc.subscribe('c');
    svc.subscribe('c');

    expect(me.subscribeCalls).toEqual(['c']);
  });

  it('does not let a reload clobber a subscribe that is still in flight', () => {
    const svc = setup();
    TestBed.flushEffects();

    // A subscribe fired in the same tick as a reload (e.g. right after login)
    // whose server snapshot has not caught up with it yet.
    const staleList = new Subject<Feed[]>();
    me.listSubscriptions = () => {
      me.listCalls++;
      return staleList;
    };
    const subscribePending = new Subject<void>();
    me.subscribe = (id: string) => {
      me.subscribeCalls.push(id);
      return subscribePending;
    };

    svc.subscribe('c');
    svc.load();
    staleList.next([feed('a'), feed('b')]); // no 'c' yet
    staleList.complete();

    expect(svc.isSubscribed('c')).toBe(true);

    subscribePending.next();
    subscribePending.complete();
    expect(svc.isSubscribed('c')).toBe(true);
  });

  it('calls onSuccess once the subscribe request actually succeeds', () => {
    const svc = setup();
    TestBed.flushEffects();
    const pending = new Subject<void>();
    me.subscribe = (id: string) => {
      me.subscribeCalls.push(id);
      return pending;
    };
    let succeeded = false;

    svc.subscribe('c', undefined, () => (succeeded = true));
    expect(succeeded).toBe(false);

    pending.next();
    pending.complete();
    expect(succeeded).toBe(true);
  });

  it('drops a load() response that arrives after the user has signed out', () => {
    const svc = setup();
    const pending = new Subject<Feed[]>();
    me.listSubscriptions = () => {
      me.listCalls++;
      return pending;
    };

    TestBed.flushEffects(); // runs the initial load(), which stays in flight

    session.set(null); // signed out before GET /me/feeds returned
    TestBed.flushEffects();

    pending.next([feed('a')]); // stale response, for a user that is gone
    pending.complete();

    expect(svc.isSubscribed('a')).toBe(false);
    expect(svc.loaded()).toBe(false);
  });

  it('attributes a load() response to whichever account is current when it arrives', () => {
    const svc = setup();
    const pending = new Subject<Feed[]>();
    me.listSubscriptions = () => {
      me.listCalls++;
      return pending;
    };

    TestBed.flushEffects(); // initial load for user-1, still in flight

    session.set({ user: { id: 'user-2' } }); // switch accounts before it resolves
    TestBed.flushEffects(); // kicks off a second load(), also subscribed to `pending`

    pending.next([feed('a')]);
    pending.complete();

    // The user-1 request's callback sees it is no longer current and drops
    // it; the user-2 request's callback is the one that actually applies it.
    expect(svc.isSubscribed('a')).toBe(true);
    expect(svc.loaded()).toBe(true);
  });

  it('does not let a stale subscribe success reintroduce a feed after sign-out', () => {
    const svc = setup();
    TestBed.flushEffects(); // user-1, ids={a,b}
    const pending = new Subject<void>();
    me.subscribe = (id: string) => {
      me.subscribeCalls.push(id);
      return pending;
    };

    svc.subscribe('c'); // optimistic add while signed in as user-1

    session.set(null); // signs out mid-flight; the effect resets _ids/_pending
    TestBed.flushEffects();

    pending.next(); // stale success arrives after sign-out
    pending.complete();

    expect(svc.isSubscribed('c')).toBe(false);
    expect(svc.isPending('c')).toBe(false);
  });

  it('does not let a stale unsubscribe rollback reintroduce a feed into a different account', () => {
    const svc = setup();
    TestBed.flushEffects(); // user-1, ids={a,b}
    const pending = new Subject<void>();
    me.unsubscribe = (id: string) => {
      me.unsubscribeCalls.push(id);
      return pending;
    };

    svc.unsubscribe('a'); // user-1 optimistically unsubscribes 'a'

    // Switch accounts. The effect resets local state and kicks off a fresh
    // load; give it a response that legitimately does not include 'a', so
    // user-2 genuinely never subscribed to it.
    me.listSubscriptions = () => {
      me.listCalls++;
      return of([feed('b')]);
    };
    session.set({ user: { id: 'user-2' } });
    TestBed.flushEffects(); // user-2 now loaded with ids={b}

    pending.error(new Error('boom')); // user-1's stale rollback arrives late

    // Without the requestedFor guard this unconditionally re-adds 'a' —
    // to what is now user-2's state, which never had it.
    expect(svc.isSubscribed('a')).toBe(false);
  });

  it('preserves a confirmed write against a list snapshot that predates it but arrives after it settled', () => {
    const svc = setup();
    TestBed.flushEffects(); // ids={a,b}, loaded=true

    // The list request this snapshot represents is "issued" here — before
    // the write below even starts — exactly like a load() and a subscribe()
    // racing around the same login/reload moment.
    const asOf = svc.beginFetch();

    const subscribePending = new Subject<void>();
    me.subscribe = (id: string) => {
      me.subscribeCalls.push(id);
      return subscribePending;
    };
    svc.subscribe('c'); // optimistic add, pending

    // The write settles first...
    subscribePending.next();
    subscribePending.complete();
    expect(svc.isPending('c')).toBe(false);

    // ...and only then does the (older) snapshot arrive (no 'c' in it —
    // it was taken before the write committed). `_pending` no longer
    // protects 'c' since the write already left it; `asOf` is what proves
    // this snapshot predates the confirmation.
    svc.sync([feed('a'), feed('b')], asOf);

    expect(svc.isSubscribed('c')).toBe(true);
  });

  it('lets a later write on a different path supersede an old confirmed tombstone', () => {
    const svc = setup();
    TestBed.flushEffects(); // ids={a,b}, loaded=true

    svc.unsubscribe('a'); // confirms 'a' unsubscribed synchronously (of(undefined))
    expect(svc.isSubscribed('a')).toBe(false);

    // A later snapshot whose request was issued *after* that — e.g. MyFeeds
    // reloading after an OPML import re-subscribed 'a' through a path that
    // never goes through subscribe()/markSubscribed() at all — legitimately
    // shows 'a' again. It must win over the old tombstone, not lose to it
    // forever just because the tombstone happens to still be sitting there.
    const asOf = svc.beginFetch();
    svc.sync([feed('a'), feed('b')], asOf);

    expect(svc.isSubscribed('a')).toBe(true);
  });

  it('rejects an older-issued snapshot that arrives after a newer one already landed', () => {
    const svc = setup();
    TestBed.flushEffects(); // ids={a,b}, loaded=true

    // Two competing fetches — e.g. SubscriptionService's own load() (slower)
    // racing MyFeeds' independent fetch after an OPML import (faster).
    const olderAsOf = svc.beginFetch();
    const newerAsOf = svc.beginFetch();

    // The newer one arrives first.
    svc.sync([feed('a'), feed('c')], newerAsOf);
    expect(svc.isSubscribed('c')).toBe(true);
    expect(svc.isSubscribed('b')).toBe(false);

    // The older one arrives late — must not clobber state a newer snapshot
    // already applied.
    svc.sync([feed('a'), feed('b')], olderAsOf);

    expect(svc.isSubscribed('c')).toBe(true);
    expect(svc.isSubscribed('b')).toBe(false);
  });

  it('syncIdentity prevents a post-login write from being dropped once the identity effect catches up', () => {
    const svc = setup();
    TestBed.flushEffects(); // user-1

    session.set({ user: { id: 'user-2' } }); // signal changed; effect not yet flushed
    svc.syncIdentity(); // what Login calls immediately after a successful sign-in

    const pending = new Subject<void>();
    me.subscribe = (id: string) => {
      me.subscribeCalls.push(id);
      return pending;
    };
    svc.subscribe('c'); // tagged with user-2, thanks to syncIdentity() above

    TestBed.flushEffects(); // the effect finally catches up — loadedFor already matches, so this is a no-op

    pending.next();
    pending.complete();

    expect(svc.isSubscribed('c')).toBe(true);
  });

  it('without syncIdentity, a write issued in the gap before the effect flushes can be dropped once it does', () => {
    const svc = setup();
    TestBed.flushEffects(); // user-1

    session.set({ user: { id: 'user-2' } }); // signal changed; effect not yet flushed
    // No syncIdentity() call here — reproducing the race it fixes.

    const pending = new Subject<void>();
    me.subscribe = (id: string) => {
      me.subscribeCalls.push(id);
      return pending;
    };
    svc.subscribe('c'); // still tagged with user-1 — loadedFor hasn't caught up yet

    TestBed.flushEffects(); // the effect now catches up to user-2, resetting state

    pending.next(); // the write settles, but is now stale relative to user-2
    pending.complete();

    expect(svc.isSubscribed('c')).toBe(false);
  });

  it('retries a transient load() failure before giving up', () => {
    vi.useFakeTimers();
    try {
      const svc = setup();
      let attempt = 0;
      me.listSubscriptions = () => {
        me.listCalls++;
        // A fresh cold Observable per subscription, like HttpClient's really
        // is: retry() resubscribes to *this*, so each attempt must re-run
        // the subscriber body — a fixed throwError/of instance wouldn't.
        return new Observable<Feed[]>((subscriber) => {
          attempt++;
          if (attempt < 3) {
            subscriber.error(new Error('network blip'));
          } else {
            subscriber.next([feed('a')]);
            subscriber.complete();
          }
        });
      };

      TestBed.flushEffects(); // triggers load(): attempt 1, fails immediately
      expect(attempt).toBe(1);
      expect(svc.loaded()).toBe(false);

      vi.advanceTimersByTime(1000); // retry() waits, then attempt 2, fails
      expect(attempt).toBe(2);

      vi.advanceTimersByTime(1000); // attempt 3, succeeds
      expect(attempt).toBe(3);
      expect(svc.isSubscribed('a')).toBe(true);
      expect(svc.loaded()).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('markSubscribed records state without calling the API', () => {
    const svc = setup();
    TestBed.flushEffects();

    svc.markSubscribed('c');

    expect(svc.isSubscribed('c')).toBe(true);
    expect(me.subscribeCalls).toEqual([]);
  });

  it('markUnsubscribed records state without calling the API', () => {
    const svc = setup();
    TestBed.flushEffects();

    svc.markUnsubscribed('a');

    expect(svc.isSubscribed('a')).toBe(false);
    expect(me.unsubscribeCalls).toEqual([]);
  });

  it('sync lets another page that already fetched Feed[] reconcile the cache directly', () => {
    const svc = setup();
    TestBed.flushEffects();

    svc.sync([feed('x'), feed('y')]);

    expect(svc.isSubscribed('x')).toBe(true);
    expect(svc.isSubscribed('a')).toBe(false);
    expect(svc.loaded()).toBe(true);
    expect(me.listCalls).toBe(1); // only the initial auto-load
  });
});
