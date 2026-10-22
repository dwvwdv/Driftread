import { TestBed } from '@angular/core/testing';
import { NetworkStatusService } from './network-status';

describe('NetworkStatusService', () => {
  afterEach(() => vi.restoreAllMocks());

  it('follows the browser online/offline events', () => {
    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
    TestBed.resetTestingModule();
    const svc = TestBed.inject(NetworkStatusService);
    expect(svc.online()).toBe(true);

    onLine.mockReturnValue(false);
    window.dispatchEvent(new Event('offline'));
    expect(svc.online()).toBe(false);

    onLine.mockReturnValue(true);
    window.dispatchEvent(new Event('online'));
    expect(svc.online()).toBe(true);
  });
});
