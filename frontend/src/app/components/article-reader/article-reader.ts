import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  WritableSignal,
  effect,
  inject,
  signal,
} from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ArticleService } from '../../services/article';
import { MeService } from '../../services/me';
import { AuthService } from '../../services/auth';
import { Article, BookmarkType } from '../../models';
import { ObIcon } from '../../ui/icon/icon';
import { ObLoading, ObError } from '../../ui/state/state';

/**
 * Full-text reader.
 *
 * Structure weight drops to ~4 here against 7 everywhere else: no offset shadow on
 * the column, hairlines instead of 2px rules, monospace confined to the metadata
 * line. Long-form Traditional Chinese wants air and a quiet frame; brutalist
 * structure around a wall of text just fights the text.
 */
@Component({
  selector: 'app-article-reader',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, DatePipe, ObIcon, ObLoading, ObError],
  templateUrl: './article-reader.html',
  styleUrl: './article-reader.scss',
})
export class ArticleReader implements OnInit {
  private route = inject(ActivatedRoute);
  private articleService = inject(ArticleService);
  private me = inject(MeService);
  protected auth = inject(AuthService);

  article = signal<Article | null>(null);
  loading = signal(true);
  error = signal('');
  favorited = signal(false);
  readLater = signal(false);

  /** Bookmark types the reader has toggled — see applyBookmarkState. */
  private touched = new Set<BookmarkType>();

  /** Article id the signed-in extras have already been run for. */
  private syncedFor: string | null = null;

  constructor() {
    // Reactive rather than sampled once inside the article callback.
    //
    // AuthService restores the persisted Supabase session asynchronously, and the
    // article itself is a public request that can easily win that race. Checking
    // `session()` once at article-load time therefore saw null on a direct visit,
    // and nothing re-ran when the session arrived a moment later: the bookmark
    // buttons appeared with both states reading "off" even for a saved article,
    // and the read receipt was never sent.
    //
    // As an effect it fires whenever *both* are available, in either order — and
    // also covers signing in while the article is already open.
    effect(() => {
      const session = this.auth.session();
      const article = this.article();
      if (!session || !article || this.syncedFor === article.id) return;

      this.syncedFor = article.id;
      this.me.markRead(article.id).subscribe({ error: () => undefined });
      this.loadBookmarkState(article.id);
    });
  }

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    const id = this.route.snapshot.paramMap.get('id') ?? '';
    this.loading.set(true);
    this.error.set('');
    // A retry re-reads the real state, so previous intent no longer wins.
    this.touched.clear();

    this.articleService.getArticle(id).subscribe({
      next: (a) => {
        this.article.set(a);
        this.loading.set(false);
        // The signed-in extras (read receipt, bookmark state) are driven by the
        // effect in the constructor, which waits for the session too.
      },
      error: () => {
        this.error.set('無法載入文章。');
        this.loading.set(false);
      },
    });
  }

  /**
   * Reads back which bookmarks this article already has.
   *
   * Without this both toggles always rendered as "off", so reopening a favourited
   * article showed an empty star and clicking it looked like it did nothing (the
   * write is an upsert, so the state never actually changed).
   *
   * Two whole-list fetches is more than this needs, but GET /api/me/bookmarks only
   * takes a bookmark_type — there is no per-article membership check to call.
   */
  private loadBookmarkState(articleId: string): void {
    for (const type of ['favorite', 'read_later'] as const) {
      this.me.listBookmarks(type).subscribe({
        next: (list) =>
          this.applyBookmarkState(
            type,
            list.some((a) => a.id === articleId),
          ),
        error: () => undefined,
      });
    }
  }

  /**
   * Applies a membership read, unless the reader has already toggled that type.
   *
   * The buttons are live as soon as the article renders, which is before these
   * reads land. Without the guard a click in that window races: the write
   * succeeds and sets the signal true, then the older read — which observed the
   * state from before the write — resolves and sets it back to false, leaving the
   * UI disagreeing with the server until a reload.
   *
   * Guarding the read is better than disabling the buttons: nothing has to be
   * greyed out, and the user's expressed intent always wins over a snapshot taken
   * before it.
   */
  private applyBookmarkState(type: BookmarkType, isMember: boolean): void {
    if (this.touched.has(type)) return;
    this.flagFor(type).set(isMember);
  }

  toggle(type: BookmarkType): void {
    const a = this.article();
    if (!a || !this.auth.session()) return;

    // Recorded on intent, not on success — an in-flight read must lose to a click
    // that has already happened, whatever the write goes on to do.
    this.touched.add(type);

    const flag = this.flagFor(type);
    const next = !flag();
    const request = next ? this.me.addBookmark(a.id, type) : this.me.removeBookmark(a.id, type);
    request.subscribe({ next: () => flag.set(next) });
  }

  private flagFor(type: BookmarkType): WritableSignal<boolean> {
    return type === 'favorite' ? this.favorited : this.readLater;
  }
}
