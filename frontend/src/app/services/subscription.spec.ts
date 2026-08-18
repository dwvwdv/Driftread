import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { Subject, of, throwError } from 'rxjs';
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
