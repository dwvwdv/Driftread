import { A11yModule } from '@angular/cdk/a11y';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { AdminKeyStore } from '../../services/admin-key';
import { ObIcon, IconName } from '../../ui/icon/icon';
import { ObThemeToggle } from '../../ui/theme-toggle/theme-toggle';

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
  private keys = inject(AdminKeyStore);
  private router = inject(Router);

  protected drawerOpen = signal(false);

  protected items: readonly AdminNavItem[] = [
    { path: 'dashboard', label: '總覽', icon: 'monitor' },
    { path: 'candidates', label: '候選審核', icon: 'check' },
    { path: 'feeds', label: '信息源管理', icon: 'archive' },
    { path: 'frontier', label: '探測與目錄', icon: 'search' },
    { path: 'import', label: '匯入', icon: 'upload' },
  ];

  constructor() {
    this.router.events.pipe(filter((e) => e instanceof NavigationEnd)).subscribe(() => {
      this.drawerOpen.set(false);
    });
  }

  protected toggleDrawer(): void {
    this.drawerOpen.update((open) => !open);
  }

  protected closeDrawer(): void {
    this.drawerOpen.set(false);
  }

  protected onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') this.closeDrawer();
  }

  /** Drops the key for this tab and returns to the unlock screen. */
  protected lock(): void {
    this.keys.clear();
    void this.router.navigate(['/admin/unlock']);
  }
}
