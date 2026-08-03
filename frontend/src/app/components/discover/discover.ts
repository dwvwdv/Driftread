import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { DiscoverService } from '../../services/discover';
import { AuthService } from '../../services/auth';
import { MeService } from '../../services/me';
import { DiscoveredFeed } from '../../models';

@Component({
  selector: 'app-discover',
  imports: [
    RouterLink,
    FormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatProgressSpinnerModule,
  ],
  template: `
    <h2>從網址發現 RSS</h2>
    <p class="hint">貼上任何網站、部落格、文章網址 — 我們會幫你找出可用的 RSS / Atom feed。</p>

    <div class="row">
      <mat-form-field appearance="outline" class="grow">
        <mat-label>網址</mat-label>
        <input
          matInput
          [(ngModel)]="url"
          placeholder="https://example.com 或 example.com"
          (keyup.enter)="run()"
        />
      </mat-form-field>
      <button mat-flat-button color="primary" (click)="run()" [disabled]="busy()">發現</button>
    </div>

    @if (busy()) {
      <mat-spinner diameter="32" />
    }
    @if (error()) {
      <p class="error">{{ error() }}</p>
    }

    @if (candidates() !== null) {
      @if (candidates()!.length === 0) {
        <p class="empty">沒有找到 RSS / Atom feed。試試這個網站的首頁網址？</p>
      } @else {
        <div class="grid">
          @for (c of candidates(); track c.feed_url) {
            <mat-card>
              <mat-card-header>
                <mat-card-title>{{ c.title || '(無標題)' }}</mat-card-title>
                <mat-card-subtitle>{{ c.feed_url }}</mat-card-subtitle>
              </mat-card-header>
              <mat-card-actions>
                @if (c.already_exists && c.existing_feed_id) {
                  <a mat-button [routerLink]="['/feeds', c.existing_feed_id]">已收錄，前往查看</a>
                } @else if (importing() === c.feed_url) {
                  <span>匯入中...</span>
                } @else {
                  <button mat-flat-button color="primary" (click)="importFeed(c)">
                    {{ auth.session() ? '匯入並訂閱' : '匯入到資料庫' }}
                  </button>
                }
              </mat-card-actions>
            </mat-card>
          }
        </div>
      }
    }
  `,
  styles: [
    `
      .row {
        display: flex;
        gap: 8px;
        align-items: center;
      }
      .grow {
        flex: 1;
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        gap: 12px;
        margin-top: 12px;
      }
      .hint {
        color: #555;
      }
      .error {
        color: #d32f2f;
      }
    `,
  ],
})
export class Discover {
  protected auth = inject(AuthService);
  private discoverService = inject(DiscoverService);
  private me = inject(MeService);

  url = '';
  busy = signal(false);
  error = signal('');
  candidates = signal<DiscoveredFeed[] | null>(null);
  importing = signal<string | null>(null);

  run(): void {
    if (!this.url.trim()) return;
    this.busy.set(true);
    this.error.set('');
    this.candidates.set(null);
    this.discoverService.discover(this.url.trim()).subscribe({
      next: (r) => {
        this.candidates.set(r.candidates);
        this.busy.set(false);
      },
      error: (e) => {
        this.error.set(e?.error?.detail || '發現失敗');
        this.busy.set(false);
      },
    });
  }

  importFeed(c: DiscoveredFeed): void {
    this.importing.set(c.feed_url);
    this.discoverService.importByUrl(c.feed_url).subscribe({
      next: () => {
        this.importing.set(null);
        c.already_exists = true;
        this.candidates.set([...this.candidates()!]);
      },
      error: (e) => {
        this.importing.set(null);
        this.error.set(e?.error?.detail || '匯入失敗');
      },
    });
  }
}
