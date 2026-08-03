import { Routes } from '@angular/router';

/**
 * Admin console pages.
 *
 * Replaces the single /admin route whose component stacked six unrelated panels
 * into one scrolling page.
 */
export const ADMIN_ROUTES: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  {
    path: 'dashboard',
    title: '總覽 — Driftread 後台',
    loadComponent: () => import('./dashboard/admin-dashboard').then((m) => m.AdminDashboard),
  },
  {
    path: 'candidates',
    title: '候選審核 — Driftread 後台',
    loadComponent: () => import('./candidates/admin-candidates').then((m) => m.AdminCandidates),
  },
  {
    path: 'feeds',
    title: '信息源管理 — Driftread 後台',
    loadComponent: () => import('./feeds/admin-feeds').then((m) => m.AdminFeeds),
  },
  {
    path: 'frontier',
    title: '探測與目錄 — Driftread 後台',
    loadComponent: () => import('./frontier/admin-frontier').then((m) => m.AdminFrontier),
  },
  {
    path: 'import',
    title: '匯入 — Driftread 後台',
    loadComponent: () => import('./import/admin-import').then((m) => m.AdminImport),
  },
];
