import { A11yModule } from '@angular/cdk/a11y';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { AuthService } from '../../services/auth';
import { ObIcon } from '../../ui/icon/icon';
import { ObThemeToggle } from '../../ui/theme-toggle/theme-toggle';

/**
 * Shell for everything a reader sees.
 *
 * Replaces the old single app-wide shell (one toolbar plus a 1200px <main>) that
 * the admin console also rendered inside. The two are now structurally separate:
 * this layout knows nothing about /admin and, notably, no longer carries a 後台
 * link — that link used to sit in the public toolbar for every visitor, signed in
 * or not, advertising an operator console to the whole internet.
 *
 * The old bar also just overflowed on narrow screens: seven items, no collapse.
 * Fixed structurally rather than by shrinking type — three primary destinations
 * stay inline, personal items move into an account menu, and below 900px the
 * whole thing folds into a focus-trapped drawer.
 */
@Component({
  selector: 'app-public-layout',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [A11yModule, RouterOutlet, RouterLink, RouterLinkActive, ObIcon, ObThemeToggle],
  templateUrl: './public-layout.html',
  styleUrl: './public-layout.scss',
})
export class PublicLayout {
  protected auth = inject(AuthService);
  private router = inject(Router);

  protected drawerOpen = signal(false);
  protected accountOpen = signal(false);

  constructor() {
    // Navigating with the drawer or account menu open should close it — otherwise
    // the panel stays over the page the reader just asked for.
    this.router.events.pipe(filter((e) => e instanceof NavigationEnd)).subscribe(() => {
      this.drawerOpen.set(false);
      this.accountOpen.set(false);
    });
  }

  protected toggleDrawer(): void {
    this.drawerOpen.update((open) => !open);
  }

  protected closeDrawer(): void {
    this.drawerOpen.set(false);
  }

  protected toggleAccount(): void {
    this.accountOpen.update((open) => !open);
  }

  protected onDrawerKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') this.closeDrawer();
  }

  async signOut(): Promise<void> {
    await this.auth.signOut();
    this.accountOpen.set(false);
    void this.router.navigateByUrl('/');
  }
}
