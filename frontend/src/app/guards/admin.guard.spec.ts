import { TestBed } from '@angular/core/testing';
import { Router, UrlSegment, UrlTree, provideRouter } from '@angular/router';
import { adminGuard } from './admin.guard';
import { AdminKeyStore } from '../services/admin-key';

function segments(...paths: string[]): UrlSegment[] {
  return paths.map((p) => new UrlSegment(p, {}));
}

function run(...paths: string[]): boolean | UrlTree {
  return TestBed.runInInjectionContext(
    () => adminGuard({}, segments(...paths)) as boolean | UrlTree,
  );
}

describe('adminGuard', () => {
  beforeEach(() => {
    sessionStorage.clear();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
  });

  it('lets the operator through once a key is present', () => {
    TestBed.inject(AdminKeyStore).set('secret-key');
    expect(run('admin', 'dashboard')).toBe(true);
  });

  it('redirects to unlock rather than returning false', () => {
    // Returning false would fall through to the '**' route and dump the operator
    // on the public home page with no explanation.
    const result = run('admin', 'dashboard');
    expect(result).toBeInstanceOf(UrlTree);
    expect(TestBed.inject(Router).serializeUrl(result as UrlTree)).toContain('/admin/unlock');
  });

  it('remembers where the operator was heading', () => {
    const result = run('admin', 'candidates') as UrlTree;
    const url = TestBed.inject(Router).serializeUrl(result);

    expect(url).toContain('redirect=');
    expect(decodeURIComponent(url)).toContain('/admin/candidates');
  });
});
