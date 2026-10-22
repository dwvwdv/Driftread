import { inject } from '@angular/core';
import { HttpInterceptorFn } from '@angular/common/http';
import { environment } from '../../environments/environment';
import { AuthService } from '../services/auth';

/**
 * Attaches the Supabase access token to our own API calls.
 *
 * Narrowed from "every outgoing request, unconditionally". Two limits:
 *
 *  - Only requests to environment.apiUrl. A bearer token is a credential; it has
 *    no business being attached to a request to some third-party host just
 *    because it went through HttpClient.
 *
 *  - AdminService supplies both Authorization and X-Manage-Session itself.
 *    Do not replace those credentials from an interceptor.
 *
 * A request that already carries Authorization keeps it: the offline reader
 * (src/app/offline/offline-reading.ts) pins the token of the account whose
 * queue it is replaying, and swapping in whichever session is current by the
 * time the request is sent would replay A's operations as B.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const token = auth.accessToken;

  const isOwnApi = req.url.startsWith(environment.apiUrl);
  const isAdminApi = req.url.startsWith(`${environment.apiUrl}/admin`);

  if (token && isOwnApi && !isAdminApi && !req.headers.has('Authorization')) {
    req = req.clone({ setHeaders: { Authorization: `Bearer ${token}` } });
  }

  return next(req);
};
