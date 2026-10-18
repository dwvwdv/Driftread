import { A11yModule } from '@angular/cdk/a11y';
import { ChangeDetectionStrategy, Component, ElementRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { ManageSessionService } from '../../services/manage-session';
import { ObIcon, IconName } from '../../ui/icon/icon';
import { ObThemeToggle } from '../../ui/theme-toggle/theme-toggle';
import { ToastService } from '../../ui/toast/toast';
import { apiMessage } from '../../shared/http-errors';

interface AdminNavItem {
  path: string;
  label: string;
  icon: IconName;
}

/**
 * Shell for the operator console.
 *
 * Deliberately unlike the public layout: a sidebar rather than a top bar, tighter
 * spacing, smaller type, more information per screen. The visual difference is
 * the point — crossing from the reader-facing site into the console should be
 * unmistakable, which it was not when both rendered inside the same toolbar and
 * the same centred 1200px column.
 *
 * Structure weight is dialled to 6 here against 7 outside (see --offset-sm): an
 * operator screen wants density, not presence.
 */
@Component({
  selector: 'app-admin-layout',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [A11yModule, RouterOutlet, RouterLink, RouterLinkActive, ObIcon, ObThemeToggle],
  templateUrl: './admin-layout.html',
  styleUrl: './admin-layout.scss',
})
export class AdminLayout {
  private keys = inject(ManageSessionService);
  private toast = inject(ToastService);
  private router = inject(Router);
  // Typed on inject() rather than as inject(ElementRef<HTMLElement>): the latter
  // resolves to ElementRef<any>, which makes nativeElement untyped and rejects the
  // generic querySelector call below.
  private host = inject<ElementRef<HTMLElement>>(ElementRef);

  protected drawerOpen = signal(false);

  protected items: readonly AdminNavItem[] = [
    { path: 'dashboard', label: '總覽', icon: 'monitor' },
    { path: 'candidates', label: '候選審核', icon: 'check' },
    { path: 'feeds', label: '信息源管理', icon: 'archive' },
    { path: 'cleanup', label: '批量清理', icon: 'archive' },
    { path: 'pending-sources', label: '待探索來源', icon: 'search' },
    { path: 'frontier', label: '探測與目錄', icon: 'search' },
    { path: 'settings', label: '全域設定', icon: 'search' },
    { path: 'import', label: '匯入', icon: 'upload' },
    { path: 'mcp-connections', label: 'MCP 連線', icon: 'check' },
  ];

  constructor() {
    // Navigating closes the drawer, but focus is not restored here: the click
    // that navigated already moved the user on, and the destination should own
    // focus rather than the trigger they came from.
    this.router.events
      .pipe(
        filter((e) => e instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe(() => {
        this.drawerOpen.set(false);
      });
  }

  protected toggleDrawer(): void {
    this.drawerOpen.update((open) => !open);
  }

  /**
   * Dismisses the drawer and puts focus back on the hamburger.
   *
   * The restore has to be explicit here. Unlike the public layout — whose drawer
   * lives inside an @if, so CDK destroys the trap and restores focus itself —
   * this sidebar is always in the DOM (it doubles as the desktop navigation), so
   * closing only *disables* the trap. Without this the keyboard user is left
   * focused on a link inside a now-hidden panel.
   */
  protected closeDrawer(): void {
    if (!this.drawerOpen()) return;
    this.drawerOpen.set(false);
    // After the class change has been applied, so the hamburger is focusable.
    queueMicrotask(() => {
      this.host.nativeElement.querySelector<HTMLButtonElement>('.hamburger')?.focus();
    });
  }

  protected onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') this.closeDrawer();
  }

  /** Revoke the elevated session before returning to the login screen. */
  protected async lock(): Promise<void> {
    try {
      await this.keys.logout();
      await this.router.navigate(['/manage/login']);
    } catch (error) {
      this.toast.danger(apiMessage(error, '無法撤銷後台 session，已清除本機登入'));
      await this.router.navigate(['/manage/login']);
    }
  }
}
