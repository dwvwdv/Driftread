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
    path: 'cleanup',
    title: '批量清理 — Driftread 後台',
    loadComponent: () => import('./cleanup/admin-cleanup').then((m) => m.AdminCleanup),
  },
  {
    path: 'pending-sources',
    title: '待探索來源 — Driftread 後台',
    loadComponent: () =>
      import('./pending-sources/admin-pending-sources').then((m) => m.AdminPendingSources),
  },
  {
    path: 'mcp-authorize',
    title: '授權管理 MCP — Driftread 後台',
    loadComponent: () =>
      import('./mcp-authorize/admin-mcp-authorize').then((m) => m.AdminMcpAuthorize),
  },
  {
    path: 'mcp-connections',
    title: 'MCP 連線 — Driftread 後台',
    loadComponent: () =>
      import('./mcp-connections/admin-mcp-connections').then((m) => m.AdminMcpConnections),
  },
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
    path: 'settings',
    title: '全域設定 — Driftread 後台',
    loadComponent: () => import('./settings/admin-settings').then((m) => m.AdminSettings),
  },
  {
    path: 'import',
    title: '匯入 — Driftread 後台',
    loadComponent: () => import('./import/admin-import').then((m) => m.AdminImport),
  },
];
