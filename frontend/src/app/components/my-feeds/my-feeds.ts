import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MeService } from '../../services/me';
import { AuthService } from '../../services/auth';
import { Feed, OpmlImportResult } from '../../models';

@Component({
  selector: 'app-my-feeds',
  imports: [RouterLink, MatCardModule, MatButtonModule, MatIconModule, MatProgressSpinnerModule],
  template: `
    @if (!auth.session()) {
      <p>請先 <a routerLink="/login">登入</a>。</p>
    } @else {
      <div class="actions">
        <input #opml type="file" accept=".opml,.xml" hidden (change)="onOpml($event)" />
        <button mat-stroked-button (click)="opml.click()">匯入 OPML</button>
        <a mat-stroked-button [href]="exportUrl()">匯出 OPML</a>
      </div>

      @if (importResult(); as r) {
        <p class="info">
          已匯入 {{ r.imported }} 個 feed（成功訂閱 {{ r.subscribed }}）。
          @if (r.failed.length > 0) {
            失敗 {{ r.failed.length }} 個。
          }
        </p>
      }

      @if (loading()) {
        <mat-spinner />
      }

      <div class="grid">
        @for (f of feeds(); track f.id) {
          <mat-card class="feed-card">
            <mat-card-header>
              <mat-card-title
                ><a [routerLink]="['/feeds', f.id]">{{ f.title }}</a></mat-card-title
              >
              @if (f.category) {
                <mat-card-subtitle>{{ f.category }}</mat-card-subtitle>
              }
            </mat-card-header>
            <mat-card-content>
              @if (f.description) {
                <p>{{ f.description }}</p>
              }
            </mat-card-content>
            <mat-card-actions>
              <button mat-button color="warn" (click)="unsubscribe(f.id)">取消訂閱</button>
            </mat-card-actions>
          </mat-card>
        }
      </div>

      @if (!loading() && feeds().length === 0) {
        <p class="empty">尚未訂閱任何 feed，到 <a routerLink="/">信息源</a> 找找看吧。</p>
      }
    }
  `,
  styles: [
    `
      .actions {
        display: flex;
        gap: 8px;
        margin-bottom: 16px;
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
        gap: 12px;
      }
      .info {
        color: #2e7d32;
      }
    `,
  ],
})
export class MyFeeds implements OnInit {
  protected auth = inject(AuthService);
  private me = inject(MeService);

  feeds = signal<Feed[]>([]);
  loading = signal(false);
  importResult = signal<OpmlImportResult | null>(null);
  exportUrl = signal(this.me.exportOpmlUrl());

  ngOnInit(): void {
    if (this.auth.session()) this.load();
  }

  load(): void {
    this.loading.set(true);
    this.me.listSubscriptions().subscribe({
      next: (f) => {
        this.feeds.set(f);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  unsubscribe(id: string): void {
    this.me.unsubscribe(id).subscribe(() => this.load());
  }

  onOpml(ev: Event): void {
    const file = (ev.target as HTMLInputElement).files?.[0];
    if (!file) return;
    this.me.importOpml(file).subscribe({
      next: (r) => {
        this.importResult.set(r);
        this.load();
      },
    });
  }
}
