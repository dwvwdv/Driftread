import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', loadComponent: () => import('./components/feed-list/feed-list').then(m => m.FeedList) },
  { path: 'feeds/:id', loadComponent: () => import('./components/feed-detail/feed-detail').then(m => m.FeedDetail) },
  { path: 'articles/:id', loadComponent: () => import('./components/article-reader/article-reader').then(m => m.ArticleReader) },
  { path: 'recommendations', loadComponent: () => import('./components/recommendations/recommendations').then(m => m.Recommendations) },
  { path: 'discover', loadComponent: () => import('./components/discover/discover').then(m => m.Discover) },
  { path: 'login', loadComponent: () => import('./components/login/login').then(m => m.Login) },
  { path: 'me/feeds', loadComponent: () => import('./components/my-feeds/my-feeds').then(m => m.MyFeeds) },
  { path: 'me/bookmarks', loadComponent: () => import('./components/bookmarks/bookmarks').then(m => m.Bookmarks) },
  { path: 'admin', loadComponent: () => import('./components/admin/admin').then(m => m.Admin) },
  { path: '**', redirectTo: '' },
];
