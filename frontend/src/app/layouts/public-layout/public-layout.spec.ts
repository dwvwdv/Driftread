import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { OfflineReadingService } from '../../offline/offline-reading';
import { AuthService } from '../../services/auth';
import { ReadingStreamService } from '../../services/reading-stream';
import { PublicLayout } from './public-layout';

describe('PublicLayout offline integration', () => {
  function setup() {
    const calls: string[] = [];
    const offline = {
      online: signal(true),
      mode: signal('session'),
      pendingCount: signal(0),
      clearAll: vi.fn(async () => void calls.push('clearAll')),
    };
    const auth = {
      session: signal({ user: { id: 'user-a', email: 'a@example.com' } }),
      userEmail: 'a@example.com',
      signOut: vi.fn(async () => void calls.push('signOut')),
    };
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [PublicLayout],
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: auth },
        { provide: OfflineReadingService, useValue: offline },
        { provide: ReadingStreamService, useValue: { totalUnread: signal(0) } },
      ],
    });
    const fixture = TestBed.createComponent(PublicLayout);
    fixture.detectChanges();
    return { fixture, offline, calls, el: fixture.nativeElement as HTMLElement };
  }

  it('clears the private offline cache before signing out', async () => {
    const { fixture, calls } = setup();
    await fixture.componentInstance.signOut();
    expect(calls).toEqual(['clearAll', 'signOut']);
  });

  it('shows ONLINE/OFFLINE and the pending queue in the status bar', () => {
    const { fixture, offline, el } = setup();
    expect(el.querySelector('footer')?.textContent).toContain('ONLINE');
    offline.online.set(false);
    offline.pendingCount.set(2);
    fixture.detectChanges();
    const footer = el.querySelector('footer')!;
    expect(footer.textContent).toContain('OFFLINE');
    expect(footer.textContent).toContain('2 項待同步');
    expect(footer.querySelector('a[href="/me/offline"]')).not.toBeNull();
  });
});
