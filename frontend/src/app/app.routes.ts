import { Routes } from '@angular/router';
import { adminGuard } from './guards/admin.guard';

/**
 * Two separate trees under two separate shells.
 *
 * Previously this was nine flat routes with /admin sitting among them as just
 * another leaf, sharing the public toolbar and with no guard at all.
 */
export const routes: Routes = [
  // MUST stay ahead of 'admin'. Once a key exists the guard passes, 'admin'
  // matches the 'admin/**' prefix, and 'unlock' would be resolved as an unknown
  // admin child instead of reaching this route.
  {
    path: 'admin/unlock',
    title: '進入後台 — Driftread',
    loadComponent: () => import('./features/admin/unlock/admin-unlock').then((m) => m.AdminUnlock),
  },
  {
    path: 'admin',
    canMatch: [adminGuard],
    loadComponent: () => import('./layouts/admin-layout/admin-layout').then((m) => m.AdminLayout),
    loadChildren: () => import('./features/admin/admin.routes').then((m) => m.ADMIN_ROUTES),
  },

  {
    path: '',
    loadComponent: () =>
      import('./layouts/public-layout/public-layout').then((m) => m.PublicLayout),
    children: [
      {
        path: '',
        pathMatch: 'full',
        title: '信息源 — 漂流閱讀 Driftread',
        loadComponent: () => import('./components/feed-list/feed-list').then((m) => m.FeedList),
      },
      {
        path: 'feeds/:id',
        loadComponent: () =>
          import('./components/feed-detail/feed-detail').then((m) => m.FeedDetail),
      },
      {
        path: 'articles/:id',
        loadComponent: () =>
          import('./components/article-reader/article-reader').then((m) => m.ArticleReader),
      },
      {
        path: 'me/stream',
        title: '我的閱讀 — 漂流閱讀 Driftread',
        loadComponent: () =>
          import('./components/reading-stream/reading-stream').then((m) => m.ReadingStream),
      },
      {
        path: 'recommendations',
        title: '猜你喜歡 — 漂流閱讀 Driftread',
        loadComponent: () =>
          import('./components/recommendations/recommendations').then((m) => m.Recommendations),
      },
      {
        path: 'discover',
        title: '發現 — 漂流閱讀 Driftread',
        loadComponent: () => import('./components/discover/discover').then((m) => m.Discover),
      },
      {
        path: 'login',
        title: '登入 — 漂流閱讀 Driftread',
        loadComponent: () => import('./components/login/login').then((m) => m.Login),
      },
      {
        path: 'me/feeds',
        title: '我的訂閱 — 漂流閱讀 Driftread',
        loadComponent: () => import('./components/my-feeds/my-feeds').then((m) => m.MyFeeds),
      },
      {
        path: 'me/bookmarks',
        title: '收藏 — 漂流閱讀 Driftread',
        loadComponent: () => import('./components/bookmarks/bookmarks').then((m) => m.Bookmarks),
      },
    ],
  },

  { path: '**', redirectTo: '' },
];
