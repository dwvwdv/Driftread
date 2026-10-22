import { computed, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { OfflineMode, OfflineReadingService } from '../../offline/offline-reading';
import { overlay } from '../../offline/offline-state';
import { OfflineAccountRecord } from '../../offline/offline-store';
import { AuthService } from '../../services/auth';
import { ToastService } from '../../ui/toast/toast';
import { OfflineReading } from './offline-reading';

function offlineStub(record: OfflineAccountRecord | null, mode: OfflineMode = 'session') {
  const state = signal(record);
  return {
    record: state,
    mode: signal<OfflineMode>(mode),
    online: signal(true),
    syncing: signal(false),
    loaded: signal(true),
    expired: signal(false),
    error: signal<string | null>(null),
    items: computed(() => (state() ? overlay(state()!) : [])),
    pendingCount: computed(() => state()?.pending.length ?? 0),
    updatedAt: computed(() => state()?.updatedAt ?? null),
    sync: vi.fn().mockResolvedValue({ ok: true, dropped: 0 }),
    setState: vi.fn().mockResolvedValue({ queued: true, replay: null }),
  };
}

const cachedRecord = (): OfflineAccountRecord => ({
  userId: 'user-a',
  cursor: 'c',
  updatedAt: Date.now(),
  pending: [],
  items: [
    {
      id: 'a1',
      feed_id: 'f1',
      feed_title: 'Feed One',
      title: 'Cached title',
      url: 'https://example.com/a1',
      summary: '<p>Hello <b>offline</b></p>',
      author: null,
      published_at: '2026-10-01T00:00:00Z',
      timeline_at: null,
      is_read: false,
      bookmark_types: [],
    },
  ],
});

describe('OfflineReading', () => {
  function setup(
    record: OfflineAccountRecord | null,
    signedIn = true,
    mode: OfflineMode = 'session',
  ) {
    const offline = offlineStub(record, mode);
    const toast = { info: vi.fn(), success: vi.fn(), warning: vi.fn(), danger: vi.fn() };
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [OfflineReading],
      providers: [
        provideRouter([]),
        {
          provide: AuthService,
          useValue: { session: signal(signedIn ? { user: { id: 'user-a' } } : null) },
        },
        { provide: OfflineReadingService, useValue: offline },
        { provide: ToastService, useValue: toast },
      ],
    });
    const fixture = TestBed.createComponent(OfflineReading);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    return { fixture, el, offline, toast };
  }

  it('asks a signed-out visitor to log in when there is no cache', () => {
    const { el } = setup(null, false, 'none');
    expect(el.textContent).toContain('登入後同步');
    expect(el.querySelector('a[href^="/login"]')).not.toBeNull();
  });

  it('offers the opt-in sync when signed in without a cache', () => {
    const { el } = setup(null);
    expect(el.textContent).toContain('還沒有離線快取');
  });

  it('lists cached summaries as text previews with read/favorite/read-later toggles', async () => {
    const { el, offline, toast } = setup(cachedRecord());
    expect(el.textContent).toContain('Cached title');
    expect(el.textContent).toContain('Hello offline');
    expect(el.querySelector('.summary b')).toBeNull();
    const favorite = [...el.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === '收藏',
    )!;
    favorite.click();
    await vi.waitFor(() => expect(toast.info).toHaveBeenCalled());
    expect(offline.setState).toHaveBeenCalledWith('a1', 'favorite', true);
  });

  it('reports an operation that could not be queued', async () => {
    const { el, offline, toast } = setup(cachedRecord());
    offline.setState.mockResolvedValue({ queued: false });
    offline.error.set('待同步文章已達 100 篇，請先連線同步');
    [...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === '標為已讀')!.click();
    await vi.waitFor(() =>
      expect(toast.danger).toHaveBeenCalledWith(expect.stringContaining('100')),
    );
  });

  it('warns when a replay was dropped because the article is gone', async () => {
    const { el, offline, toast } = setup(cachedRecord());
    offline.setState.mockResolvedValue({
      queued: true,
      replay: Promise.resolve({ ok: true, dropped: 1 }),
    });
    [...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === '稍後讀')!.click();
    await vi.waitFor(() => expect(toast.warning).toHaveBeenCalled());
  });

  it('shows offline status and disables sync while offline', () => {
    const { el, offline, fixture } = setup(cachedRecord());
    offline.online.set(false);
    fixture.detectChanges();
    expect(el.textContent).toContain('目前離線');
    const sync = [...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === '同步')!;
    expect(sync.disabled).toBe(true);
  });

  it('dormant caches are read-only', () => {
    const { el } = setup(cachedRecord(), false, 'dormant');
    expect(el.textContent).toContain('登入尚未恢復');
    expect(el.textContent).toContain('Cached title');
    expect([...el.querySelectorAll('button')].some((b) => b.textContent?.trim() === '收藏')).toBe(
      false,
    );
  });

  it('sync reports success or the service error', async () => {
    const { el, offline, toast } = setup(cachedRecord());
    const sync = () =>
      [...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === '同步')!;
    sync().click();
    await vi.waitFor(() => expect(toast.success).toHaveBeenCalled());
    offline.sync.mockResolvedValue({ ok: false, dropped: 0 });
    offline.error.set('登入已失效，請重新登入後再同步');
    sync().click();
    await vi.waitFor(() =>
      expect(toast.danger).toHaveBeenCalledWith(expect.stringContaining('登入已失效')),
    );
  });
});
