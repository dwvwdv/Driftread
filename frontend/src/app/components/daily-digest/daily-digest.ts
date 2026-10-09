import { Component, effect, inject, OnDestroy, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';
import { AuthService } from '../../services/auth';
import { DailyDigestResult, DigestService } from '../../services/digest';

@Component({
  selector: 'app-daily-digest',
  imports: [FormsModule, RouterLink],
  templateUrl: './daily-digest.html',
  styles: [':host{display:block;max-width:900px;margin:2rem auto;padding:1rem} .controls{display:flex;gap:1rem;flex-wrap:wrap} article{border-bottom:1px solid var(--border);padding:1rem 0}'],
})
export class DailyDigest implements OnDestroy {
  readonly auth = inject(AuthService);
  private api = inject(DigestService);
  readonly result = signal<DailyDigestResult | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  day = this.localDate(new Date());
  private request?: Subscription;
  private generation = 0;
  private rssRequest?: Subscription;
  constructor() {
    effect(() => {
      const session = this.auth.session();
      this.generation++;
      this.request?.unsubscribe(); this.rssRequest?.unsubscribe(); this.result.set(null); this.error.set('');
      if (session) this.load(); else this.loading.set(false);
    });
  }
  private localDate(value: Date) {
    return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
  }
  load() {
    if (!this.auth.session()) return;
    const generation = ++this.generation;
    this.request?.unsubscribe(); this.loading.set(true); this.error.set(''); this.result.set(null);
    this.request = this.api.get(this.day, this.timezone).subscribe({
      next: (result) => { if (generation === this.generation) { this.result.set(result); this.loading.set(false); } },
      error: () => { if (generation === this.generation) { this.error.set('無法載入每日閱讀，請確認日期與時區後重試。'); this.loading.set(false); } },
    });
  }
  downloadRss() {
    const owner = this.auth.session()?.user.id;
    if (!owner) return;
    this.rssRequest?.unsubscribe();
    this.rssRequest = this.api.rss().subscribe({ next: (xml) => {
      if (owner !== this.auth.session()?.user.id) return;
      const url = URL.createObjectURL(new Blob([xml], { type: 'application/rss+xml' }));
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'driftread-personal.rss'; anchor.click();
      URL.revokeObjectURL(url);
    }, error: () => { if (owner === this.auth.session()?.user.id) this.error.set('無法下載個人 RSS，請重試。'); } });
  }
  ngOnDestroy() {
    this.generation++; this.request?.unsubscribe(); this.rssRequest?.unsubscribe();
  }
}
