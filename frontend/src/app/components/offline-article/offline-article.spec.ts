import { computed, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { OfflineMode, OfflineReadingService } from '../../offline/offline-reading';
import { overlay } from '../../offline/offline-state';
import { OfflineAccountRecord } from '../../offline/offline-store';
import { ToastService } from '../../ui/toast/toast';
import { OfflineArticle } from './offline-article';

const record = (summary: string): OfflineAccountRecord => ({
  userId: 'user-a',
  cursor: 'c',
  updatedAt: Date.now(),
  pending: [{ articleId: 'a1', kind: 'favorite', enabled: true }],
  items: [
    {
      id: 'a1',
      feed_id: 'f1',
      feed_title: 'Feed One',
      title: 'Offline article',
      url: 'https://example.com/a1',
      summary,
      author: 'Ann',
      published_at: null,
      timeline_at: null,
      is_read: false,
      bookmark_types: [],
    },
  ],
});

describe('OfflineArticle', () => {
  function setup(
    id: string,
    data: OfflineAccountRecord | null,
    online = true,
    mode: OfflineMode = 'session',
  ) {
    const state = signal(data);
    const offline = {
      mode: signal(mode),
      online: signal(online),
      loaded: signal(true),
      error: signal<string | null>(null),
      items: computed(() => (state() ? overlay(state()!) : [])),
      setState: vi.fn().mockResolvedValue({ queued: true, replay: null }),
    };
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [OfflineArticle],
      providers: [
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: of(convertToParamMap({ id })) } },
        { provide: OfflineReadingService, useValue: offline },
        { provide: ToastService, useValue: { info: vi.fn(), warning: vi.fn(), danger: vi.fn() } },
      ],
    });
    const fixture = TestBed.createComponent(OfflineArticle);
    fixture.detectChanges();
    return { el: fixture.nativeElement as HTMLElement, offline };
  }

  it('renders the cached summary through the sanitizer, never raw script', () => {
    const { el } = setup(
      'a1',
      record('<p>Safe <em>text</em></p><img src=x onerror="alert(1)"><script>alert(2)</script>'),
    );
    const prose = el.querySelector('.prose')!;
    expect(prose.querySelector('em')?.textContent).toBe('text');
    expect(prose.querySelector('script')).toBeNull();
    expect(prose.querySelector('img')?.getAttribute('onerror')).toBeNull();
    expect(el.textContent).toContain('待同步');
    const original = el.querySelector<HTMLAnchorElement>('a[target="_blank"]')!;
    expect(original.rel).toBe('noopener noreferrer');
  });

  it('links to the full online reader only when online', () => {
    expect(setup('a1', record('x')).el.textContent).toContain('線上閱讀全文');
    expect(setup('a1', record('x'), false).el.textContent).not.toContain('線上閱讀全文');
  });

  it('toggles favorite from the overlaid state', async () => {
    const { el, offline } = setup('a1', record('x'));
    [...el.querySelectorAll('button')].find((b) => b.textContent?.includes('已收藏'))!.click();
    await vi.waitFor(() => expect(offline.setState).toHaveBeenCalledWith('a1', 'favorite', false));
  });

  it('explains a missing article instead of rendering stale data', () => {
    const { el } = setup('nope', record('x'));
    expect(el.textContent).toContain('離線快取裡沒有這篇文章');
  });

  it('hides actions for a dormant (read-only) cache', () => {
    const { el } = setup('a1', record('x'), false, 'dormant');
    expect(el.querySelectorAll('.actions button')).toHaveLength(0);
  });
});
