import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatListModule } from '@angular/material/list';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDividerModule } from '@angular/material/divider';
import { environment } from '../../../environments/environment';
import { Feed } from '../../models';

@Component({
  selector: 'app-admin',
  imports: [
    FormsModule,
    MatCardModule, MatButtonModule, MatFormFieldModule, MatInputModule,
    MatListModule, MatChipsModule, MatProgressSpinnerModule,
    MatSnackBarModule, MatDividerModule,
  ],
  templateUrl: './admin.html',
  styleUrl: './admin.scss',
})
export class Admin implements OnInit {
  private http = inject(HttpClient);
  private snack = inject(MatSnackBar);
  private base = environment.apiUrl;

  apiKey = signal('');
  importJson = signal('');
  importing = signal(false);

  archivedFeeds = signal<Feed[]>([]);
  loadingArchived = signal(false);

  ngOnInit(): void { this.loadArchived(); }

  private headers(): HttpHeaders {
    return new HttpHeaders({ 'x-api-key': this.apiKey() });
  }

  importFeeds(): void {
    let parsed: unknown;
    try { parsed = JSON.parse(this.importJson()); } catch {
      this.snack.open('JSON 格式錯誤', '關閉', { duration: 3000 });
      return;
    }
    const body = Array.isArray(parsed) ? { feeds: parsed } : parsed;
    this.importing.set(true);
    this.http.post(`${this.base}/admin/feeds`, body, { headers: this.headers() }).subscribe({
      next: (res: any) => {
        this.snack.open(`成功匯入 ${res.length} 個信息源`, '關閉', { duration: 3000 });
        this.importing.set(false);
        this.importJson.set('');
        this.loadArchived();
      },
      error: (e) => {
        this.snack.open(`匯入失敗：${e.error?.detail ?? e.message}`, '關閉', { duration: 4000 });
        this.importing.set(false);
      },
    });
  }

  loadArchived(): void {
    this.loadingArchived.set(true);
    this.http.get<Feed[]>(`${this.base}/admin/feeds/archived`, { headers: this.headers() }).subscribe({
      next: feeds => { this.archivedFeeds.set(feeds); this.loadingArchived.set(false); },
      error: () => this.loadingArchived.set(false),
    });
  }

  unarchive(feed: Feed): void {
    this.http.patch(`${this.base}/admin/feeds/${feed.id}/unarchive`, {}, { headers: this.headers() }).subscribe({
      next: () => {
        this.snack.open(`已取消封存：${feed.title}`, '關閉', { duration: 3000 });
        this.loadArchived();
      },
      error: (e) => this.snack.open(`失敗：${e.error?.detail}`, '關閉', { duration: 3000 }),
    });
  }

  refresh(feed: Feed): void {
    this.http.post(`${this.base}/admin/feeds/${feed.id}/refresh`, {}, { headers: this.headers() }).subscribe({
      next: (res: any) => this.snack.open(`已更新，新增 ${res.inserted} 篇`, '關閉', { duration: 3000 }),
      error: (e) => this.snack.open(`更新失敗：${e.error?.detail}`, '關閉', { duration: 3000 }),
    });
  }
}
