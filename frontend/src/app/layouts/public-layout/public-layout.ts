import { A11yModule } from '@angular/cdk/a11y';
import { ChangeDetectionStrategy, Component, ElementRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
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
  private host = inject<ElementRef<HTMLElement>>(ElementRef);

  protected drawerOpen = signal(false);
  protected accountOpen = signal(false);

  constructor() {
    // Navigating with the drawer or account menu open should close it — otherwise
    // the panel stays over the page the reader just asked for.
    this.router.events
      .pipe(
        filter((e) => e instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe(() => {
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
    if (this.accountOpen()) {
      this.closeAccount(false);
      return;
    }

    this.accountOpen.set(true);
    queueMicrotask(() => {
      this.host.nativeElement
        .querySelector<HTMLElement>('#account-menu [role="menuitem"]')
        ?.focus();
    });
  }

  /** Dismisses the account menu, optionally restoring focus to its trigger. */
  protected closeAccount(restoreFocus = true): void {
    if (!this.accountOpen()) return;
    this.accountOpen.set(false);
    if (restoreFocus) {
      queueMicrotask(() => {
        this.host.nativeElement.querySelector<HTMLButtonElement>('.account-trigger')?.focus();
      });
    }
  }

  protected onDrawerKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') this.closeDrawer();
  }

  /**
   * Escape inside the account popover.
   *
   * Separate from onDrawerKeydown: that only closes the drawer, so reusing it here
   * left the menu open with focus still inside it.
   *
   * Focus goes back to the trigger, otherwise dismissing the menu drops the
   * keyboard user at the top of the document.
   */
  protected onAccountKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Escape') return;
    event.stopPropagation();
    this.closeAccount();
  }

  async signOut(): Promise<void> {
    await this.auth.signOut();
    this.accountOpen.set(false);
    void this.router.navigateByUrl('/');
  }
}
