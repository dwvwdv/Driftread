import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatListModule } from '@angular/material/list';
import { MatChipsModule } from '@angular/material/chips';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDividerModule } from '@angular/material/divider';
import { environment } from '../../../environments/environment';
import {
  DiscoveryCycleSummary,
  DiscoverySource,
  DiscoveryStats,
  Feed,
  FeedCandidate,
  PaginatedFeedCandidates,
  PaginatedFeeds,
  SeedTargetsResult,
} from '../../models';

@Component({
  selector: 'app-admin',
  imports: [
    FormsModule,
    MatCardModule, MatButtonModule, MatFormFieldModule, MatInputModule,
    MatListModule, MatChipsModule, MatProgressSpinnerModule, MatCheckboxModule,
    MatSelectModule, MatSnackBarModule, MatDividerModule,
  ],
  templateUrl: './admin.html',
  styleUrl: './admin.scss',
})
export class Admin {
  private http = inject(HttpClient);
  private snack = inject(MatSnackBar);
  private base = environment.apiUrl;

  apiKey = signal('');
  importJson = signal('');
  importing = signal(false);

  archivedFeeds = signal<Feed[]>([]);
  loadingArchived = signal(false);
  activeFeeds = signal<Feed[]>([]);
  loadingActive = signal(false);

  // ── autonomous discovery ────────────────────────────────────────────────
  stats = signal<DiscoveryStats | null>(null);
  running = signal(false);
  candidates = signal<FeedCandidate[]>([]);
  loadingCandidates = signal(false);
  candidateCategory = signal('');
  candidateTags = signal('');
  blockHostOnReject = signal(false);
  seedUrls = signal('');
  seeding = signal(false);
  sources = signal<DiscoverySource[]>([]);
  newSourceUrl = signal('');
  newSourceKind = signal<'links_page' | 'opml'>('links_page');

  // No ngOnInit fetch: every admin call needs the key, and firing one before the
  // operator has typed it just produced a guaranteed 403 that was swallowed
  // silently. Each panel loads on demand instead.

  private headers(): HttpHeaders {
    return new HttpHeaders({ 'x-api-key': this.apiKey() });
  }

  private requireKey(): boolean {
    if (this.apiKey().trim()) return true;
    this.snack.open('請先輸入 Admin API Key', '關閉', { duration: 3000 });
    return false;
  }

  private fail(prefix: string) {
    return (e: any) =>
      this.snack.open(`${prefix}：${e.error?.detail ?? e.message}`, '關閉', {
        duration: 4000,
      });
  }

  // ── feed import / archive ───────────────────────────────────────────────

  importFeeds(): void {
    if (!this.requireKey()) return;
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
        this.fail('匯入失敗')(e);
        this.importing.set(false);
      },
    });
  }

  loadArchived(): void {
    if (!this.requireKey()) return;
    this.loadingArchived.set(true);
    this.http.get<Feed[]>(`${this.base}/admin/feeds/archived`, { headers: this.headers() }).subscribe({
      next: feeds => { this.archivedFeeds.set(feeds); this.loadingArchived.set(false); },
      error: (e) => { this.fail('讀取封存清單失敗')(e); this.loadingArchived.set(false); },
    });
  }

  loadActive(): void {
    if (!this.requireKey()) return;
    this.loadingActive.set(true);
    this.http.get<PaginatedFeeds>(
      `${this.base}/admin/feeds?archived=false&page_size=100`, { headers: this.headers() },
    ).subscribe({
      next: page => { this.activeFeeds.set(page.items); this.loadingActive.set(false); },
      error: (e) => { this.fail('讀取來源清單失敗')(e); this.loadingActive.set(false); },
    });
  }

  archive(feed: Feed): void {
    if (!this.requireKey()) return;
    this.http.patch(`${this.base}/admin/feeds/${feed.id}/archive`, {}, { headers: this.headers() }).subscribe({
      next: () => {
        this.snack.open(`已封存：${feed.title}`, '關閉', { duration: 3000 });
        this.activeFeeds.update(list => list.filter(f => f.id !== feed.id));
      },
      error: this.fail('封存失敗'),
    });
  }

  unarchive(feed: Feed): void {
    if (!this.requireKey()) return;
    this.http.patch(`${this.base}/admin/feeds/${feed.id}/unarchive`, {}, { headers: this.headers() }).subscribe({
      next: () => {
        this.snack.open(`已取消封存：${feed.title}`, '關閉', { duration: 3000 });
        this.loadArchived();
      },
      error: this.fail('失敗'),
    });
  }

  refresh(feed: Feed): void {
    if (!this.requireKey()) return;
    this.http.post(`${this.base}/admin/feeds/${feed.id}/refresh`, {}, { headers: this.headers() }).subscribe({
      next: (res: any) => this.snack.open(`已更新，新增 ${res.inserted} 篇`, '關閉', { duration: 3000 }),
      error: this.fail('更新失敗'),
    });
  }

  // ── discovery overview ──────────────────────────────────────────────────

  loadStats(): void {
    if (!this.requireKey()) return;
    this.http.get<DiscoveryStats>(`${this.base}/admin/discovery/stats`, { headers: this.headers() })
      .subscribe({
        next: s => this.stats.set(s),
        error: this.fail('讀取統計失敗'),
      });
  }

  runCycle(): void {
    if (!this.requireKey()) return;
    this.running.set(true);
    this.http.post<DiscoveryCycleSummary>(`${this.base}/admin/discovery/run`, {}, { headers: this.headers() })
      .subscribe({
        next: (summary) => {
          this.running.set(false);
          this.snack.open(
            `完成：新增待探測 ${summary.harvest['targets_created'] ?? 0}、` +
            `新候選 ${summary.probe['candidates_new'] ?? 0}、入庫 ${summary.imported}`,
            '關閉', { duration: 5000 },
          );
          this.loadStats();
          this.loadCandidates();
        },
        error: (e) => {
          this.running.set(false);
          // 503 means the kill switch is off, which is a configuration state
          // rather than an error — say so plainly instead of showing a raw detail.
          if (e.status === 503) {
            this.snack.open(
              '自主發現目前為停用狀態（FEED_DISCOVERY_ENABLED=false）', '關閉',
              { duration: 5000 },
            );
            return;
          }
          this.fail('執行失敗')(e);
        },
      });
  }

  // ── candidate review ────────────────────────────────────────────────────

  loadCandidates(): void {
    if (!this.requireKey()) return;
    this.loadingCandidates.set(true);
    this.http.get<PaginatedFeedCandidates>(
      `${this.base}/admin/discovery/candidates?status=pending&page_size=100`,
      { headers: this.headers() },
    ).subscribe({
      next: page => { this.candidates.set(page.items); this.loadingCandidates.set(false); },
      error: (e) => { this.fail('讀取候選失敗')(e); this.loadingCandidates.set(false); },
    });
  }

  approveCandidate(candidate: FeedCandidate): void {
    if (!this.requireKey()) return;
    const tags = this.candidateTags().split(',').map(t => t.trim()).filter(Boolean);
    const body = { category: this.candidateCategory().trim() || null, tags };
    this.http.post<Feed>(
      `${this.base}/admin/discovery/candidates/${candidate.id}/approve`, body,
      { headers: this.headers() },
    ).subscribe({
      next: feed => {
        this.snack.open(`已入庫：${feed.title}`, '關閉', { duration: 3000 });
        this.candidates.update(list => list.filter(c => c.id !== candidate.id));
        this.loadStats();
      },
      error: this.fail('核准失敗'),
    });
  }

  rejectCandidate(candidate: FeedCandidate): void {
    if (!this.requireKey()) return;
    const body = { note: null, block_host: this.blockHostOnReject() };
    this.http.post(
      `${this.base}/admin/discovery/candidates/${candidate.id}/reject`, body,
      { headers: this.headers() },
    ).subscribe({
      next: () => {
        this.snack.open(
          this.blockHostOnReject() ? '已拒絕並封鎖該網域' : '已拒絕', '關閉',
          { duration: 3000 },
        );
        this.candidates.update(list => list.filter(c => c.id !== candidate.id));
        this.loadStats();
      },
      error: this.fail('拒絕失敗'),
    });
  }

  // ── seed targets ────────────────────────────────────────────────────────

  seedTargets(): void {
    if (!this.requireKey()) return;
    const urls = this.seedUrls().split('\n').map(u => u.trim()).filter(Boolean);
    if (!urls.length) {
      this.snack.open('請至少輸入一個網址', '關閉', { duration: 3000 });
      return;
    }
    this.seeding.set(true);
    this.http.post<SeedTargetsResult>(
      `${this.base}/admin/discovery/targets`, { urls }, { headers: this.headers() },
    ).subscribe({
      next: (res) => {
        this.seeding.set(false);
        this.seedUrls.set('');
        const parts = [`新增 ${res.accepted}`, `重排 ${res.requeued}`, `略過 ${res.skipped}`];
        if (res.rejected.length) parts.push(`拒絕 ${res.rejected.length}`);
        this.snack.open(parts.join('、'), '關閉', { duration: 5000 });
        this.loadStats();
      },
      error: (e) => { this.fail('加入失敗')(e); this.seeding.set(false); },
    });
  }

  // ── directory sources ───────────────────────────────────────────────────

  loadSources(): void {
    if (!this.requireKey()) return;
    this.http.get<DiscoverySource[]>(`${this.base}/admin/discovery/sources`, { headers: this.headers() })
      .subscribe({
        next: s => this.sources.set(s),
        error: this.fail('讀取目錄來源失敗'),
      });
  }

  addSource(): void {
    if (!this.requireKey()) return;
    const url = this.newSourceUrl().trim();
    if (!url) return;
    this.http.post<DiscoverySource[]>(
      `${this.base}/admin/discovery/sources`,
      { items: [{ url, kind: this.newSourceKind() }] },
      { headers: this.headers() },
    ).subscribe({
      next: () => {
        this.snack.open('已新增目錄來源', '關閉', { duration: 3000 });
        this.newSourceUrl.set('');
        this.loadSources();
      },
      error: this.fail('新增失敗'),
    });
  }

  toggleSource(source: DiscoverySource): void {
    if (!this.requireKey()) return;
    this.http.patch<DiscoverySource>(
      `${this.base}/admin/discovery/sources/${source.id}`,
      { enabled: !source.enabled },
      { headers: this.headers() },
    ).subscribe({
      next: () => this.loadSources(),
      error: this.fail('更新失敗'),
    });
  }

  reloadDefaultSources(): void {
    if (!this.requireKey()) return;
    this.http.post<{ loaded: number }>(
      `${this.base}/admin/discovery/sources/reload-defaults`, {},
      { headers: this.headers() },
    ).subscribe({
      next: (res) => {
        this.snack.open(`已載入 ${res.loaded} 個預設來源`, '關閉', { duration: 3000 });
        this.loadSources();
      },
      error: this.fail('載入失敗'),
    });
  }
}
