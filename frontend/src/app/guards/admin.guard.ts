import { inject } from '@angular/core';
import { CanMatchFn, Router } from '@angular/router';
import { AuthService } from '../services/auth';
import { ManageSessionService } from '../services/manage-session';

// UX gate. The server independently checks the owner, active auth session, and OTP session.
export const adminGuard: CanMatchFn = async (_route, segments) => {
  const auth = inject(AuthService);
  const sessions = inject(ManageSessionService);
  const router = inject(Router);
  await auth.ready;
  if (sessions.token()) return true;
  return router.createUrlTree(['/manage/login'], {
    queryParams: {
      // Keep the opaque OAuth request through password and Telegram login.
      redirect: router.getCurrentNavigation()?.extractedUrl
        ? router.serializeUrl(router.getCurrentNavigation()!.extractedUrl)
        : '/' + segments.map((s) => s.path).join('/'),
    },
  });
};
