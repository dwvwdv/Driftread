import { LiveAnnouncer } from '@angular/cdk/a11y';
import { Overlay } from '@angular/cdk/overlay';
import { TestBed } from '@angular/core/testing';
import { ObToastHost, ToastService } from './toast';

describe('ToastService concurrent notifications', () => {
  let host: ObToastHost;
  let service: ToastService;
  let announce: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    host = new ObToastHost();
    announce = vi.fn();
    const position = {
      global: () => position,
      bottom: () => position,
      centerHorizontally: () => position,
    };
    TestBed.configureTestingModule({
      providers: [
        ToastService,
        { provide: LiveAnnouncer, useValue: { announce } },
        {
          provide: Overlay,
          useValue: {
            position: () => position,
            scrollStrategies: { noop: () => ({}) },
            create: () => ({ attach: () => ({ instance: host }) }),
          },
        },
      ],
    });
    service = TestBed.inject(ToastService);
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('shows and announces one copy of the same outage from concurrent requests', () => {
    for (let i = 0; i < 20; i++) service.info('後台驗證服務暫時無法使用');
    expect(host.toasts()).toHaveLength(1);
    expect(announce).toHaveBeenCalledExactlyOnceWith('後台驗證服務暫時無法使用', 'polite');
  });

  it('allows the same message after dismissal or expiry without extending its lifetime', () => {
    service.info('服務暫時無法使用');
    host.dismiss(host.toasts()[0].id);
    service.info('服務暫時無法使用');
    expect(host.toasts()).toHaveLength(1);
    vi.advanceTimersByTime(3000);
    service.info('服務暫時無法使用');
    vi.advanceTimersByTime(500);
    expect(host.toasts()).toHaveLength(0);
    service.info('服務暫時無法使用');
    expect(host.toasts()).toHaveLength(1);
    expect(announce).toHaveBeenCalledTimes(3);
  });

  it('preserves distinct messages and severity levels', () => {
    service.info('作業失敗');
    service.danger('作業失敗');
    service.info('另一次作業失敗');
    expect(host.toasts().map(({ text, tone }) => ({ text, tone }))).toEqual([
      { text: '作業失敗', tone: 'info' },
      { text: '作業失敗', tone: 'danger' },
      { text: '另一次作業失敗', tone: 'info' },
    ]);
    expect(announce).toHaveBeenCalledTimes(3);
  });
});
