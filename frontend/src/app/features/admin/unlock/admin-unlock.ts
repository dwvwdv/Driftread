import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AdminKeyStore } from '../../../services/admin-key';
import { ObCallout } from '../../../ui/callout/callout';
import { ObIcon } from '../../../ui/icon/icon';

/**
 * Entry point to the admin console.
 *
 * Deliberately routed outside the guard, and registered before the '/admin' route
 * so it is never swallowed as an unknown admin child once a key exists.
 *
 * Nothing is validated here — there is no endpoint to validate a key against, and
 * inventing one would be a way to probe for a valid secret. The key is stored and
 * the first real admin request decides: a wrong one comes back 403, and
 * AdminService clears it and sends the operator straight back to this page.
 */
@Component({
  selector: 'app-admin-unlock',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, RouterLink, ObCallout, ObIcon],
  templateUrl: './admin-unlock.html',
  styleUrl: './admin-unlock.scss',
})
export class AdminUnlock {
  private keys = inject(AdminKeyStore);
  private router = inject(Router);
  private route = inject(ActivatedRoute);

  protected apiKey = '';
  protected error = signal('');

  protected submit(): void {
    const key = this.apiKey.trim();
    if (!key) {
      this.error.set('請輸入 Admin API Key');
      return;
    }

    this.error.set('');
    this.keys.set(key);

    // Return the operator to whatever they were trying to reach, when the guard
    // recorded it. Only same-origin paths are honoured — `redirect` comes from the
    // query string, so treating it as a URL would be an open-redirect.
    const redirect = this.route.snapshot.queryParamMap.get('redirect');
    const safe = redirect && redirect.startsWith('/admin') ? redirect : '/admin/dashboard';
    void this.router.navigateByUrl(safe);
  }
}
