import { inject } from '@angular/core';
import { CanMatchFn, Router } from '@angular/router';
import { AdminKeyStore } from '../services/admin-key';

/**
 * Gate on /admin/**.
 *
 * IMPORTANT — this is not authentication, and must not be mistaken for it.
 *
 * All it can check is whether an API key has been entered in this tab. It does
 * not and cannot validate that key; the only thing that does is the backend's
 * `_require_api_key` (backend/routers/admin.py), which compares it against
 * ADMIN_API_KEY and answers 403. Admin identity is a standalone shared secret and
 * is not derivable from a Supabase login, so there is nothing stronger available
 * on the client.
 *
 * What it buys is UX: the admin console no longer renders as a wall of panels
 * that all fail, and the operator is sent somewhere that explains why.
 *
 * Returns a UrlTree rather than false so the router redirects instead of falling
 * through to the '**' route, which would silently drop the operator on the public
 * home page.
 */
export const adminGuard: CanMatchFn = (_route, segments) => {
  const keys = inject(AdminKeyStore);
  const router = inject(Router);

  if (keys.hasKey()) return true;

  return router.createUrlTree(['/admin/unlock'], {
    queryParams: { redirect: '/' + segments.map((s) => s.path).join('/') },
  });
};
