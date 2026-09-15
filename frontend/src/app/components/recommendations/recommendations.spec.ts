import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { Recommendations } from './recommendations';
import { RecommendationService } from '../../services/recommendation';
import { SubscriptionService } from '../../services/subscription';
import { AuthService } from '../../services/auth';
import { ToastService } from '../../ui/toast/toast';
import { Feed, RecommendedFeed } from '../../models';

const feed: Feed = {
  id: 'feed-1',
  title: 'Feed One',
  url: 'https://example.com/feed.xml',
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
};

const item: RecommendedFeed = { feed, reason: null };

/** The card behind `item` in the deck — what a premature advance eats. */
const nextFeed: Feed = { ...feed, id: 'feed-2', title: 'Feed Two' };
const nextItem: RecommendedFeed = { feed: nextFeed, reason: null };

describe('Recommendations subscribe action', () => {
  // Signed in by default; a test that needs signed-out sets this to null
  // *before* calling setup() — setup() itself must not touch it, or it would
  // clobber that override right back to signed-in.
  let session: { user: { id: string } } | null = { user: { id: 'user-1' } };
  let rec: {
    liked: () => string[];
    disliked: () => string[];
    skipped: () => string[];
    likeCalls: string[];
    like: (id: string) => void;
    dislike: (id: string) => void;
    skip: (id: string) => void;
    getRecommendations: () => ReturnType<RecommendationService['getRecommendations']>;
  };
  let subs: {
    subscribeCalls: string[];
    isSubscribed: (id: string) => boolean;
    isPending: (id: string) => boolean;
    subscribe: (id: string, onError?: (err: unknown) => void, onSuccess?: () => void) => void;
  };
  let navCalls: unknown[][];

  beforeEach(() => {
    session = { user: { id: 'user-1' } };
  });

  function setup() {
    rec = {
      liked: () => [],
      disliked: () => [],
      skipped: () => [],
      likeCalls: [],
      like: (id) => rec.likeCalls.push(id),
      dislike: () => {},
      skip: () => {},
      getRecommendations: () => of([item]),
    };
    subs = {
      subscribeCalls: [],
      isSubscribed: () => false,
      isPending: () => false,
      subscribe: (id, _onError, onSuccess) => {
        subs.subscribeCalls.push(id);
        onSuccess?.();
      },
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [Recommendations],
      providers: [
        provideRouter([]),
        { provide: RecommendationService, useValue: rec },
        { provide: SubscriptionService, useValue: subs },
        { provide: AuthService, useValue: { session: () => session } },
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
        },
      ],
    });

    const fixture = TestBed.createComponent(Recommendations);
    fixture.detectChanges();

    navCalls = [];
    const router = TestBed.inject(Router);
    router.navigate = (commands: any, extras?: any) => {
      navCalls.push([commands, extras]);
      return Promise.resolve(true);
    };

    return fixture.componentInstance;
  }

  it('sends a signed-out reader to log in with the feed to subscribe on return', () => {
    session = null;
    const page = setup();

    page.subscribe(item);

    expect(navCalls).toEqual([
      [['/login'], { queryParams: { redirect: '/recommendations', subscribeFeed: 'feed-1' } }],
    ]);
    expect(subs.subscribeCalls).toEqual([]);
    expect(rec.likeCalls).toEqual([]);
  });

  it('subscribes a signed-in reader, also recording it as liked, and advances the deck', () => {
    const page = setup();

    page.subscribe(item);

    expect(subs.subscribeCalls).toEqual(['feed-1']);
    expect(rec.likeCalls).toEqual(['feed-1']);
    expect(page.currentIndex()).toBe(1);
  });

  it('does not record liked or advance the deck when the subscribe request fails', () => {
    const page = setup();
    // liked is stored in localStorage and excludes the feed from every future
    // deck — recording it on a request that never actually succeeded would
    // strand an unsubscribed feed outside all future decks with no way back.
    subs.subscribe = (id, onError) => {
      subs.subscribeCalls.push(id);
      onError?.(new Error('boom'));
    };

    page.subscribe(item);

    expect(subs.subscribeCalls).toEqual(['feed-1']);
    expect(rec.likeCalls).toEqual([]);
    expect(page.currentIndex()).toBe(0);
  });
});

/**
 * 推薦回饋（喜歡／跳過）as the deck itself sees it. The persistence behind
 * those actions — PUT /me/feed-feedback/{feed_id}, its signed-out no-op, the
 * local state that is never rolled back on a failed persist, and the one
 * in-flight-request-per-feed guard — belongs to RecommendationService and is
 * covered in services/recommendation.spec.ts. What is covered here is the
 * wiring this component owns: which feed id each control reports, when the
 * card advances, and that a feedback action and an in-flight 訂閱 don't
 * clobber each other's position in the deck.
 */
describe('Recommendations feedback actions', () => {
  const session = { user: { id: 'user-1' } };
  let rec: {
    likedIds: string[];
    /** Every feedback call in order, as [action, feedId]. */
    calls: [string, string][];
    liked: () => string[];
    disliked: () => string[];
    skipped: () => string[];
    like: (id: string) => void;
    dislike: (id: string) => void;
    skip: (id: string) => void;
    getRecommendations: () => ReturnType<RecommendationService['getRecommendations']>;
  };
  let subs: {
    subscribeCalls: string[];
    /**
     * Left armed by subscribe() so a test can land the response whenever it
     * likes — the real SubscriptionService only calls back once
     * POST /me/feeds/{id} actually answers, which is the whole window this
     * block is about.
     */
    settle: { success: () => void; fail: (err: unknown) => void } | null;
    isSubscribed: (id: string) => boolean;
    isPending: (id: string) => boolean;
    subscribe: (id: string, onError?: (err: unknown) => void, onSuccess?: () => void) => void;
  };

  function setup() {
    rec = {
      likedIds: [],
      calls: [],
      liked: () => rec.likedIds,
      disliked: () => [],
      skipped: () => [],
      like: (id) => {
        rec.calls.push(['like', id]);
        if (!rec.likedIds.includes(id)) rec.likedIds.push(id);
      },
      dislike: (id) => rec.calls.push(['dislike', id]),
      skip: (id) => rec.calls.push(['skip', id]),
      getRecommendations: () => of([item, nextItem]),
    };
    subs = {
      subscribeCalls: [],
      settle: null,
      isSubscribed: () => false,
      isPending: () => subs.settle !== null,
      subscribe: (id, onError, onSuccess) => {
        subs.subscribeCalls.push(id);
        subs.settle = {
          success: () => {
            subs.settle = null;
            onSuccess?.();
          },
          fail: (err) => {
            subs.settle = null;
            onError?.(err);
          },
        };
      },
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [Recommendations],
      providers: [
        provideRouter([]),
        { provide: RecommendationService, useValue: rec },
        { provide: SubscriptionService, useValue: subs },
        { provide: AuthService, useValue: { session: () => session } },
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
        },
      ],
    });

    const fixture = TestBed.createComponent(Recommendations);
    fixture.detectChanges();
    return fixture.componentInstance;
  }

  it('records 喜歡 for the card on screen and advances the deck', () => {
    const page = setup();

    page.like(item);

    expect(rec.calls).toEqual([['like', 'feed-1']]);
    expect(page.currentIndex()).toBe(1);
    expect(page.current?.feed.id).toBe('feed-2');
  });

  it('records 跳過 for the card on screen and advances the deck', () => {
    const page = setup();

    page.skip(item);

    expect(rec.calls).toEqual([['skip', 'feed-1']]);
    expect(page.currentIndex()).toBe(1);
    expect(page.current?.feed.id).toBe('feed-2');
  });

  it('advances immediately, without waiting for the feedback to be persisted', () => {
    const page = setup();

    // 喜歡/跳過 are best-effort server-side (RecommendationService._persist
    // never blocks and never rolls back), so unlike 訂閱 the card must not
    // sit there waiting for a response that may never come.
    page.like(item);
    page.skip(nextItem);

    expect(rec.calls).toEqual([
      ['like', 'feed-1'],
      ['skip', 'feed-2'],
    ]);
    expect(page.current).toBeNull();
    expect(page.remaining).toBe(0);
  });

  it('counts 已喜歡 off the service rather than a local tally', () => {
    const page = setup();
    expect(page.likedCount).toBe(0);

    page.like(item);

    // The header's 已喜歡 N 個 reads RecommendationService.liked(), which is
    // what actually survives a reload — a private counter here would drift
    // from it the moment 喜歡 happens anywhere else (feed detail's own button).
    expect(page.likedCount).toBe(1);
  });

  it('does not advance the deck again when a subscribe lands after a 跳過 moved it on', () => {
    const page = setup();

    page.subscribe(item); // POST /me/feeds/feed-1 in flight
    // 跳過/喜歡 stay clickable while a subscribe is pending — only the 訂閱
    // button itself is disabled — so the reader can move the deck on first.
    page.skip(item);
    expect(page.current?.feed.id).toBe('feed-2');

    subs.settle!.success(); // the subscribe finally answers

    // The subscribe still records its own stronger 喜歡 signal, but the deck
    // stays where the reader left it: advancing here would consume feed-2
    // without it ever being shown, and next() only ever moves forward.
    expect(rec.calls).toEqual([
      ['skip', 'feed-1'],
      ['like', 'feed-1'],
    ]);
    expect(page.currentIndex()).toBe(1);
    expect(page.current?.feed.id).toBe('feed-2');
  });

  it('still advances when the subscribe lands with the reader on the same card', () => {
    const page = setup();

    page.subscribe(item);
    expect(page.currentIndex()).toBe(0);
    expect(rec.calls).toEqual([]);

    subs.settle!.success();

    expect(rec.calls).toEqual([['like', 'feed-1']]);
    expect(page.currentIndex()).toBe(1);
  });

  it('leaves the deck alone when a subscribe fails after a 跳過 moved it on', () => {
    const page = setup();

    page.subscribe(item);
    page.skip(item);

    subs.settle!.fail(new Error('boom'));

    expect(rec.calls).toEqual([['skip', 'feed-1']]);
    expect(page.currentIndex()).toBe(1);
  });

  it('does not advance when loadMore() resurfaces the same feed id in a fresh deck', () => {
    const page = setup();

    page.subscribe(item); // POST /me/feeds/feed-1 in flight
    // getRecommendations() returns a brand-new array on every call, here with
    // feed-1 at the front again (the subscribe hasn't committed yet, so the
    // server has no reason to exclude it) — same id, unrelated card, from an
    // unrelated deck fetch. Comparing feed.id alone would wrongly treat it
    // as "still on the card that was subscribed from".
    page.loadMore();
    expect(page.currentIndex()).toBe(0);
    expect(page.current?.feed.id).toBe('feed-1');

    subs.settle!.success(); // the stale subscribe finally answers

    expect(rec.calls).toEqual([['like', 'feed-1']]);
    // Still on the fresh deck's first card — it must not be consumed unseen.
    expect(page.currentIndex()).toBe(0);
    expect(page.current?.feed.id).toBe('feed-1');
  });
});
