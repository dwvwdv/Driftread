import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, Subject, throwError } from 'rxjs';
import { AuthService } from '../../services/auth';
import { DailyDigestResult, DigestService } from '../../services/digest';
import { DailyDigest } from './daily-digest';

describe('DailyDigest', () => {
  function setup(loggedIn = true) {
    const session = signal<null | {user:{id:string}}>(loggedIn ? {user:{id:'a'}} : null);
    const api = { get: vi.fn().mockReturnValue(of({date:'2026-10-09',timezone:'UTC',items:[],truncated:false})), rss: vi.fn() };
    TestBed.configureTestingModule({ imports:[DailyDigest], providers:[provideRouter([]),
      {provide:AuthService,useValue:{session}}, {provide:DigestService,useValue:api}] });
    const fixture = TestBed.createComponent(DailyDigest); fixture.detectChanges();
    return {fixture,api,session,component:fixture.componentInstance};
  }
  it('requires login and never queries an anonymous digest', () => {
    const {api,fixture} = setup(false); expect(api.get).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain('登入');
  });
  it('sends date and IANA timezone and renders empty state', () => {
    const {api,fixture,component} = setup();
    component.day='2026-10-09'; component.timezone='Asia/Taipei'; component.load(); fixture.detectChanges();
    expect(api.get).toHaveBeenLastCalledWith('2026-10-09','Asia/Taipei');
    expect(fixture.nativeElement.textContent).toContain('沒有符合條件');
  });
  it('cancels prior responses and purges on account logout', () => {
    const {api,session,fixture,component} = setup();
    const pending = new Subject<DailyDigestResult>(); api.get.mockReturnValue(pending); component.load();
    session.set(null); fixture.detectChanges();
    pending.next({date:'old',timezone:'UTC',items:[],truncated:false});
    expect(component.result()).toBeNull();
  });
  it('account switch cancels old digest and RSS responses', () => {
    const {api,session,fixture,component} = setup();
    const pending = new Subject<DailyDigestResult>(); const rss = new Subject<string>();
    api.get.mockReturnValue(pending); api.rss.mockReturnValue(rss);
    component.load(); component.downloadRss();
    api.get.mockReturnValue(new Subject<DailyDigestResult>());
    session.set({user:{id:'b'}}); fixture.detectChanges();
    expect(rss.observed).toBe(false);
    pending.next({date:'old',timezone:'UTC',items:[],truncated:false});
    // Account B has a separate fresh query subscription.
    expect(component.result()).toBeNull();
  });
  it('destroy cancels pending requests and anonymous RSS never queries', () => {
    const {api,fixture,component,session} = setup();
    const pending = new Subject<DailyDigestResult>(); const rss = new Subject<string>();
    api.get.mockReturnValue(pending); api.rss.mockReturnValue(rss);
    component.load(); component.downloadRss(); fixture.destroy();
    expect(pending.observed).toBe(false); expect(rss.observed).toBe(false);
    session.set(null); component.downloadRss(); expect(api.rss).toHaveBeenCalledTimes(1);
  });
  it('shows query errors without stale data', () => {
    const {api,component} = setup(); api.get.mockReturnValue(throwError(() => new Error('bad zone'))); component.load();
    expect(component.result()).toBeNull(); expect(component.error()).toContain('無法載入');
  });
});
