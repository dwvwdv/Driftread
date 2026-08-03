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
 *  - Not /admin/*. Those endpoints authenticate with the X-API-Key header and
 *    ignore Authorization entirely, so sending it is pure exposure for no effect.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const token = auth.accessToken;

  const isOwnApi = req.url.startsWith(environment.apiUrl);
  const isAdminApi = req.url.startsWith(`${environment.apiUrl}/admin`);

  if (token && isOwnApi && !isAdminApi) {
    req = req.clone({ setHeaders: { Authorization: `Bearer ${token}` } });
  }

  return next(req);
};
