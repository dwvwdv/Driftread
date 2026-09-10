import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { Observable, of } from 'rxjs';
import { RecommendationService } from './recommendation';
import { AuthService } from './auth';
import { MeService } from './me';
import { FeedFeedbackType } from '../models';

describe('RecommendationService', () => {
  let session: ReturnType<typeof signal<{ user: { id: string } } | null>>;
  let me: { feedbackCalls: [string, FeedFeedbackType][]; setFeedFeedback: MeService['setFeedFeedback'] };
  let httpMock: HttpTestingController;

  function setup(): RecommendationService {
    localStorage.clear();
    session = signal<{ user: { id: string } } | null>(null);
    me = {
      feedbackCalls: [],
      setFeedFeedback: (feedId: string, type: FeedFeedbackType) => {
        me.feedbackCalls.push([feedId, type]);
        return of(undefined);
      },
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: AuthService, useValue: { session: () => session() } },
        { provide: MeService, useValue: me },
      ],
    });
    httpMock = TestBed.inject(HttpTestingController);

    return TestBed.inject(RecommendationService);
  }

  afterEach(() => httpMock.verify());

  it('starts empty when localStorage has nothing stored', () => {
    const rec = setup();
    expect(rec.liked()).toEqual([]);
    expect(rec.disliked()).toEqual([]);
    expect(rec.skipped()).toEqual([]);
  });

  it('like() records the id and persists it to localStorage', () => {
    const rec = setup();
    rec.like('feed-1');
    expect(rec.liked()).toEqual(['feed-1']);
    expect(JSON.parse(localStorage.getItem('driftread_liked')!)).toEqual(['feed-1']);
  });

  it('like/dislike/skip are mutually exclusive per feed', () => {
    const rec = setup();
    rec.like('feed-1');
    rec.dislike('feed-1');
    expect(rec.liked()).toEqual([]);
    expect(rec.disliked()).toEqual(['feed-1']);

    rec.skip('feed-1');
    expect(rec.disliked()).toEqual([]);
    expect(rec.skipped()).toEqual(['feed-1']);

    rec.like('feed-1');
    expect(rec.skipped()).toEqual([]);
    expect(rec.liked()).toEqual(['feed-1']);
  });

  it('does not persist server-side when signed out', () => {
    const rec = setup();
    rec.like('feed-1');
    rec.dislike('feed-2');
    rec.skip('feed-3');
    expect(me.feedbackCalls).toEqual([]);
  });

  it('persists like/dislike/skip server-side when signed in', () => {
    const rec = setup();
    session.set({ user: { id: 'user-1' } });

    rec.like('feed-1');
    rec.dislike('feed-2');
    rec.skip('feed-3');

    expect(me.feedbackCalls).toEqual([
      ['feed-1', 'liked'],
      ['feed-2', 'disliked'],
      ['feed-3', 'skipped'],
    ]);
  });

  it('a failed persist call does not throw or revert local state', () => {
    const rec = setup();
    session.set({ user: { id: 'user-1' } });
    // An Observable error, not a synchronous throw, is the realistic
    // failure mode for an HTTP call — RecommendationService.like() must not
    // let a rejected persist call propagate back to the caller or undo the
    // local (already-applied) state.
    me.setFeedFeedback = () =>
      new Observable((subscriber) => subscriber.error(new Error('boom'))) as ReturnType<
        MeService['setFeedFeedback']
      >;

    expect(() => rec.like('feed-1')).not.toThrow();
    expect(rec.liked()).toEqual(['feed-1']);
  });

  it('getRecommendations sends the last 50 of each signal, most recent last, when signed out', () => {
    const rec = setup();
    for (let i = 0; i < 55; i++) rec.like(`feed-${i}`);

    rec.getRecommendations(10).subscribe();

    const req = httpMock.expectOne((r) => r.url.endsWith('/recommendations'));
    const likedIds = req.request.params.getAll('liked')!;
    expect(likedIds.length).toBe(50);
    expect(likedIds[0]).toBe('feed-5');
    expect(likedIds[49]).toBe('feed-54');
    req.flush([]);
  });

  it('getRecommendations omits liked/disliked/skipped params when signed in', () => {
    // Persisted server-side feedback is what a signed-in caller's
    // _load_signals reads fresh on every call — resending the local copy
    // too used to double-count a liked id's weight and, worse, keep an
    // expired skipped id excluding its feed forever (this array has no
    // timestamp to prune by, unlike the persisted row's own decay window).
    const rec = setup();
    session.set({ user: { id: 'user-1' } });
    rec.like('feed-1');
    rec.dislike('feed-2');
    rec.skip('feed-3');

    rec.getRecommendations(10).subscribe();

    const req = httpMock.expectOne((r) => r.url.endsWith('/recommendations'));
    expect(req.request.params.has('liked')).toBe(false);
    expect(req.request.params.has('disliked')).toBe(false);
    expect(req.request.params.has('skipped')).toBe(false);
    req.flush([]);
  });

  it('cancels an in-flight persist request when a second action hits the same feed first', () => {
    // Simulates 喜歡 then 不喜歡 clicked in quick succession on the same
    // feed, before the first request's response arrives — without
    // cancellation both PUTs would be in flight with no guarantee the
    // server processes them in the order they were sent.
    const rec = setup();
    session.set({ user: { id: 'user-1' } });
    let firstUnsubscribed = false;
    let callCount = 0;
    me.setFeedFeedback = (() => {
      callCount++;
      const isFirst = callCount === 1;
      return new Observable(() => {
        return () => {
          if (isFirst) firstUnsubscribed = true;
        };
      });
    }) as MeService['setFeedFeedback'];

    rec.like('feed-1');
    expect(firstUnsubscribed).toBe(false);
    rec.dislike('feed-1');
    expect(firstUnsubscribed).toBe(true);
    expect(callCount).toBe(2);
  });
});
