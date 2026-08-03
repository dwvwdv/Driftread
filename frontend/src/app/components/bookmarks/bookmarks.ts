import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { DatePipe } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatTabsModule } from '@angular/material/tabs';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MeService } from '../../services/me';
import { AuthService } from '../../services/auth';
import { Article, BookmarkType } from '../../models';

@Component({
  selector: 'app-bookmarks',
  imports: [
    RouterLink,
    DatePipe,
    MatCardModule,
    MatButtonModule,
    MatTabsModule,
    MatProgressSpinnerModule,
  ],
  template: `
    @if (!auth.session()) {
      <p>請先 <a routerLink="/login">登入</a>。</p>
    } @else {
      <mat-tab-group (selectedIndexChange)="onTab($event)">
        <mat-tab label="收藏"></mat-tab>
        <mat-tab label="稍後閱讀"></mat-tab>
      </mat-tab-group>

      @if (loading()) {
        <mat-spinner />
      }

      <div class="grid">
        @for (a of items(); track a.id) {
          <mat-card>
            <mat-card-header>
              <mat-card-title>
                <a [routerLink]="['/articles', a.id]">{{ a.title }}</a>
              </mat-card-title>
              @if (a.published_at) {
                <mat-card-subtitle>{{ a.published_at | date: 'yyyy-MM-dd' }}</mat-card-subtitle>
              }
            </mat-card-header>
            @if (a.summary) {
              <mat-card-content
                ><p>{{ a.summary }}</p></mat-card-content
              >
            }
            <mat-card-actions>
              <button mat-button (click)="remove(a.id)">移除</button>
            </mat-card-actions>
          </mat-card>
        }
      </div>

      @if (!loading() && items().length === 0) {
        <p class="empty">目前沒有{{ tab() === 'favorite' ? '收藏' : '稍後閱讀' }}。</p>
      }
    }
  `,
  styles: [
    `
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 12px;
        margin-top: 12px;
      }
    `,
  ],
})
export class Bookmarks implements OnInit {
  protected auth = inject(AuthService);
  private me = inject(MeService);

  tab = signal<BookmarkType>('favorite');
  items = signal<Article[]>([]);
  loading = signal(false);

  ngOnInit(): void {
    if (this.auth.session()) this.load();
  }

  onTab(idx: number): void {
    this.tab.set(idx === 0 ? 'favorite' : 'read_later');
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.me.listBookmarks(this.tab()).subscribe({
      next: (a) => {
        this.items.set(a);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  remove(articleId: string): void {
    this.me.removeBookmark(articleId, this.tab()).subscribe(() => this.load());
  }
}
