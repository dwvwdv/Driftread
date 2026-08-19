import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { ReadingStream } from './reading-stream';
import { AuthService } from '../../services/auth';
import { MeService } from '../../services/me';
import { ReadingStreamService } from '../../services/reading-stream';
import { ConfirmService } from '../../ui/confirm/confirm';
import { ToastService } from '../../ui/toast/toast';
import { PaginatedStream, StreamArticle, UnreadSummary } from '../../models';

const article = (id: string, overrides: Partial<StreamArticle> = {}): StreamArticle => ({
  id,
  feed_id: 'feed-1',
  feed_title: 'Feed One',
  title: `Article ${id}`,
  url: `https://example.com/${id}`,
  summary: null,
  author: null,
  published_at: '2026-08-14T10:00:00+00:00',
  fetched_at: '2026-08-14T10:00:00+00:00',
  is_read: false,
  read_at: null,
  ...overrides,
});

describe('ReadingStream', () => {
  let streamPage: PaginatedStream;
  let confirmCalls: number;
  let markAllCalls: unknown[];
  let getStreamCalls: { feedId?: string | null; unreadOnly?: boolean }[];

  function setup() {
    streamPage = { items: [article('a'), article('b', { is_read: true })], next_cursor: null };
    confirmCalls = 0;
    markAllCalls = [];
    getStreamCalls = [];

    const unreadSummary: UnreadSummary = {
      total_unread: 1,
      feeds: [{ feed_id: 'feed-1', feed_title: 'Feed One', unread_count: 1 }],
    };

    const me = {
      getStream: (opts: { feedId?: string | null; unreadOnly?: boolean }) => {
        getStreamCalls.push(opts);
        return of(streamPage);
      },
      getUnreadCounts: () => of(unreadSummary),
      markRead: () => of(undefined),
      markUnread: () => of(undefined),
      markAllRead: (body: unknown) => {
        markAllCalls.push(body);
        return of({ marked: 1 });
      },
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [ReadingStream],
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: { session: () => ({ user: { id: 'user-1' } }) } },
        { provide: MeService, useValue: me },
        {
          provide: ConfirmService,
          useValue: {
            ask: () => {
              confirmCalls++;
              return Promise.resolve(true);
            },
          },
        },
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
        },
      ],
    });

    const fixture = TestBed.createComponent(ReadingStream);
    fixture.detectChanges();
    // Same root injector as the component's own `inject(ReadingStreamService)`
    // — this is the real, un-mocked service (only MeService/AuthService below
    // it are faked), so assertions here observe exactly what the page does.
    const stream = TestBed.inject(ReadingStreamService);
    return { page: fixture.componentInstance, stream };
  }

  it('loads the stream and unread counts once signed in', () => {
    const { stream } = setup();

    expect(stream.items().map((a) => a.id)).toEqual(['a', 'b']);
    expect(stream.totalUnread()).toBe(1);
  });

  it('hideRead filters already-read rows out of the rendered list', () => {
    const { page } = setup();
    expect(page.visibleItems().map((a) => a.id)).toEqual(['a', 'b']);

    page.hideRead.set(true);

    expect(page.visibleItems().map((a) => a.id)).toEqual(['a']);
  });

  it('toggleRead marks an unread article read, and a read one back to unread', () => {
    const { page, stream } = setup();

    page.toggleRead(stream.items()[0]); // 'a', currently unread
    expect(stream.items()[0].is_read).toBe(true);

    page.toggleRead(stream.items()[0]);
    expect(stream.items()[0].is_read).toBe(false);
  });

  it('onFeedFilter reloads the stream scoped to the chosen feed', () => {
    const { page } = setup();
    getStreamCalls = [];

    page.onFeedFilter('feed-1');

    expect(page.feedId()).toBe('feed-1');
    expect(getStreamCalls.at(-1)?.feedId).toBe('feed-1');
  });

  it('markScopeRead asks for confirmation before marking the whole stream read', async () => {
    const { page } = setup();

    await page.markScopeRead();

    expect(confirmCalls).toBe(1);
    expect(markAllCalls).toEqual([{}]);
  });

  it('markScopeRead scopes to the active feed filter when one is set', async () => {
    const { page } = setup();
    page.feedId.set('feed-1');

    await page.markScopeRead();

    expect(markAllCalls).toEqual([{ feed_id: 'feed-1' }]);
  });

  it('clearFilters resets feed, unread-only and hide-read state', () => {
    const { page } = setup();
    page.feedId.set('feed-1');
    page.unreadOnly.set(true);
    page.hideRead.set(true);

    page.clearFilters();

    expect(page.feedId()).toBeNull();
    expect(page.unreadOnly()).toBe(false);
    expect(page.hideRead()).toBe(false);
  });
});
