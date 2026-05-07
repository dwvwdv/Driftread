import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', loadComponent: () => import('./components/feed-list/feed-list').then(m => m.FeedList) },
  { path: 'feeds/:id', loadComponent: () => import('./components/feed-detail/feed-detail').then(m => m.FeedDetail) },
  { path: 'articles/:id', loadComponent: () => import('./components/article-reader/article-reader').then(m => m.ArticleReader) },
  { path: 'recommendations', loadComponent: () => import('./components/recommendations/recommendations').then(m => m.Recommendations) },
  { path: 'admin', loadComponent: () => import('./components/admin/admin').then(m => m.Admin) },
  { path: '**', redirectTo: '' },
];
