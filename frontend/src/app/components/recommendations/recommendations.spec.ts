import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { Recommendations } from './recommendations';
import { RecommendationService } from '../../services/recommendation';
import { SubscriptionService } from '../../services/subscription';
import { AuthService } from '../../services/auth';
import { ToastService } from '../../ui/toast/toast';
import { Feed } from '../../models';

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

describe('Recommendations subscribe action', () => {
  let session: { user: { id: string } } | null;
  let rec: { liked: () => string[]; disliked: () => string[]; likeCalls: string[]; like: (id: string) => void; dislike: (id: string) => void; getRecommendations: () => ReturnType<RecommendationService['getRecommendations']> };
  let subs: { subscribeCalls: string[]; isSubscribed: (id: string) => boolean; isPending: (id: string) => boolean; subscribe: (id: string) => void };
  let navCalls: unknown[][];

  function setup() {
    session = { user: { id: 'user-1' } };
    rec = {
      liked: () => [],
      disliked: () => [],
      likeCalls: [],
      like: (id) => rec.likeCalls.push(id),
      dislike: () => {},
      getRecommendations: () => of([feed]),
    };
    subs = {
      subscribeCalls: [],
      isSubscribed: () => false,
      isPending: () => false,
      subscribe: (id) => subs.subscribeCalls.push(id),
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

    page.subscribe(feed);

    expect(navCalls).toEqual([
      [['/login'], { queryParams: { redirect: '/recommendations', subscribeFeed: 'feed-1' } }],
    ]);
    expect(subs.subscribeCalls).toEqual([]);
    expect(rec.likeCalls).toEqual([]);
  });

  it('subscribes a signed-in reader, also recording it as liked, and advances the deck', () => {
    const page = setup();

    page.subscribe(feed);

    expect(subs.subscribeCalls).toEqual(['feed-1']);
    expect(rec.likeCalls).toEqual(['feed-1']);
    expect(page.currentIndex()).toBe(1);
  });
});
