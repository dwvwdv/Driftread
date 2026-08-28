import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, effect, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { ArticleService } from '../../services/article';
import { FeedService } from '../../services/feed';
import { MeService } from '../../services/me';
import { RecommendationService } from '../../services/recommendation';
import { SubscriptionService } from '../../services/subscription';
import { AuthService } from '../../services/auth';
import { FeedArticle, FeedWithArticles } from '../../models';
import { apiMessage } from '../../shared/http-errors';
import { ObIcon } from '../../ui/icon/icon';
import { ObListRow } from '../../ui/list-row/list-row';
import { ObLoading, ObError, ObEmpty } from '../../ui/state/state';
import { ToastService } from '../../ui/toast/toast';

const ARTICLES_PAGE_SIZE = 20;

/** One feed: its metadata, subscribe/like/dislike controls, and its full,
 * cursor-paginated article list (TODO.md "Feed 完整文章列表"). */
@Component({
  selector: 'app-feed-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, DatePipe, ObIcon, ObListRow, ObLoading, ObError, ObEmpty],
  templateUrl: './feed-detail.html',
  styleUrl: './feed-detail.scss',
})
export class FeedDetail implements OnInit {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private feedService = inject(FeedService);
  private articleService = inject(ArticleService);
  private me = inject(MeService);
  private rec = inject(RecommendationService);
  private subs = inject(SubscriptionService);
  protected auth = inject(AuthService);
  private toast = inject(ToastService);

  feed = signal<FeedWithArticles | null>(null);
  loading = signal(true);
  error = signal('');

  articles = signal<FeedArticle[]>([]);
  articlesLoading = signal(true);
  articlesLoadingMore = signal(false);
  articlesError = signal('');
  private nextCursor = signal<string | null>(null);
  hasMoreArticles = () => this.nextCursor() !== null;

  /** Bumped by every `loadArticles()` call. A fresh load (fired for the
   * initial anonymous page, an identity change from the effect below, or a
   * manual retry) supersedes anything already in flight — `loadArticles()`
   * and `loadMoreArticles()` both capture this when they fire and drop
   * their response if a newer `loadArticles()` has since started, so a slow
   * anonymous response can't land after the authenticated reload and wipe
   * out its is_read/is_bookmarked flags (same generation-counter pattern
   * `ReadingStreamService` uses for the same reason). */
  private articlesGeneration = 0;

  /** Article ids with an in-flight read/bookmark toggle — separate sets since
   * the two actions are independent and can both be in flight at once. */
  private pendingRead = signal<ReadonlySet<string>>(new Set());
  private pendingBookmark = signal<ReadonlySet<string>>(new Set());
  isReadPending = (id: string) => this.pendingRead().has(id);
  isBookmarkPending = (id: string) => this.pendingBookmark().has(id);

  /** Identity the currently loaded article page was fetched for.
   * `undefined` means "never loaded" — distinct from `null` (anonymous) so
   * the effect below still fires the very first load. */
  private articlesLoadedFor: string | null | undefined = undefined;

  constructor() {
    // AuthService restores a persisted session asynchronously — `session()`
    // starts null even for an already-signed-in reader on a direct visit —
    // so a one-shot load in ngOnInit can go out anonymous, get back
    // is_read/is_bookmarked all false, and never refresh once the real
    // session arrives (same class of bug bookmarks.ts and my-feeds.ts guard
    // against for their own user-scoped loads). Reacting to identity here
    // instead covers the initial anonymous load, the async session-restore
    // case, and sign-out/account-switch — all of which change what
    // is_read/is_bookmarked should read.
    effect(() => {
      const userId = this.auth.session()?.user?.id ?? null;
      if (this.articlesLoadedFor === userId) return;
      this.articlesLoadedFor = userId;
      this.loadArticles();
    });
  }

  get feedId(): string {
    return this.route.snapshot.paramMap.get('id') ?? '';
  }

  get isLiked(): boolean {
    return this.rec.liked().includes(this.feedId);
  }

  get isDisliked(): boolean {
    return this.rec.disliked().includes(this.feedId);
  }

  get isSubscribed(): boolean {
    return this.subs.isSubscribed(this.feedId);
  }

  get subscribePending(): boolean {
    return this.subs.isPending(this.feedId);
  }

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set('');
    this.feedService.getFeed(this.feedId).subscribe({
      next: (f) => {
        this.feed.set(f);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('無法載入信息源。');
        this.loading.set(false);
      },
    });
  }

  /** Replaces `articles` with the first page. Independent of `load()` above —
   * a feed with metadata that fails to load could still, in principle, list
   * its articles, and the two requests have no reason to block each other. */
  loadArticles(): void {
    const generation = ++this.articlesGeneration;
    this.articlesLoading.set(true);
    // A fresh load supersedes any load-more, or read/bookmark toggle, in
    // flight for the previous generation — those requests' own callbacks
    // will now bail out on the generation check without ever clearing their
    // flag themselves, so the flags have to be reset here instead. Without
    // this, a toggle started under a since-replaced identity would leave its
    // article permanently stuck "pending" once that same article id
    // reappears under the new identity's freshly loaded page.
    this.articlesLoadingMore.set(false);
    this.pendingRead.set(new Set());
    this.pendingBookmark.set(new Set());
    this.articlesError.set('');
    this.articleService.getArticles(this.feedId, null, ARTICLES_PAGE_SIZE).subscribe({
      next: (page) => {
        if (generation !== this.articlesGeneration) return;
        this.articles.set(page.items);
        this.nextCursor.set(page.next_cursor);
        this.articlesLoading.set(false);
      },
      error: () => {
        if (generation !== this.articlesGeneration) return;
        this.articlesError.set('無法載入文章列表。');
        this.articlesLoading.set(false);
      },
    });
  }

  /** Appends the next page. No-op if there is no next page or a load-more is
   * already in flight — guards a double click / scroll-triggered double fire. */
  loadMoreArticles(): void {
    const cursor = this.nextCursor();
    if (!cursor || this.articlesLoadingMore()) return;
    const generation = this.articlesGeneration;
    this.articlesLoadingMore.set(true);
    this.articleService.getArticles(this.feedId, cursor, ARTICLES_PAGE_SIZE).subscribe({
      next: (page) => {
        if (generation !== this.articlesGeneration) return;
        this.articles.update((current) => [...current, ...page.items]);
        this.nextCursor.set(page.next_cursor);
        this.articlesLoadingMore.set(false);
      },
      error: (err: unknown) => {
        if (generation !== this.articlesGeneration) return;
        this.articlesLoadingMore.set(false);
        this.toast.danger(apiMessage(err, '載入更多文章失敗'));
      },
    });
  }

  private patchArticle(articleId: string, patch: Partial<FeedArticle>): void {
    this.articles.update((items) =>
      items.map((a) => (a.id === articleId ? { ...a, ...patch } : a)),
    );
  }

  private setPending(set: 'read' | 'bookmark', articleId: string, pending: boolean): void {
    const target = set === 'read' ? this.pendingRead : this.pendingBookmark;
    const next = new Set(target());
    if (pending) next.add(articleId);
    else next.delete(articleId);
    target.set(next);
  }

  /** Requires sign-in — the row's toggle button is only rendered once
   * `auth.session()` is truthy, same guard `toggleSubscribe()` needs for the
   * unauthenticated case (there the click itself redirects to login instead,
   * since subscribing has a place to resume to; a single article's read
   * state does not, so the button simply doesn't appear signed out). */
  toggleRead(article: FeedArticle): void {
    if (this.isReadPending(article.id)) return;
    const wasRead = article.is_read;
    // If sign-out/account-switch reloads the list before this settles, the
    // reload bumps articlesGeneration — this toggle's own callback below
    // then knows its optimistic patch and pending flag belong to a list
    // that's no longer on screen, and skips touching the new one.
    const generation = this.articlesGeneration;
    this.setPending('read', article.id, true);
    this.patchArticle(article.id, { is_read: !wasRead });

    const request = wasRead ? this.me.markUnread(article.id) : this.me.markRead(article.id);
    request.subscribe({
      next: () => {
        if (generation !== this.articlesGeneration) return;
        this.setPending('read', article.id, false);
      },
      error: (err: unknown) => {
        if (generation !== this.articlesGeneration) return;
        this.setPending('read', article.id, false);
        this.patchArticle(article.id, { is_read: wasRead });
        this.toast.danger(apiMessage(err, wasRead ? '標為未讀失敗' : '標為已讀失敗'));
      },
    });
  }

  toggleBookmark(article: FeedArticle): void {
    if (this.isBookmarkPending(article.id)) return;
    const wasBookmarked = article.is_bookmarked;
    const generation = this.articlesGeneration;
    this.setPending('bookmark', article.id, true);
    this.patchArticle(article.id, { is_bookmarked: !wasBookmarked });

    const request = wasBookmarked
      ? this.me.removeBookmark(article.id, 'favorite')
      : this.me.addBookmark(article.id, 'favorite');
    request.subscribe({
      next: () => {
        if (generation !== this.articlesGeneration) return;
        this.setPending('bookmark', article.id, false);
      },
      error: (err: unknown) => {
        if (generation !== this.articlesGeneration) return;
        this.setPending('bookmark', article.id, false);
        this.patchArticle(article.id, { is_bookmarked: wasBookmarked });
        this.toast.danger(apiMessage(err, wasBookmarked ? '取消收藏失敗' : '收藏失敗'));
      },
    });
  }

  like(): void {
    this.rec.like(this.feedId);
  }

  dislike(): void {
    this.rec.dislike(this.feedId);
  }

  /**
   * Not signed in: sends the reader to log in first, then straight back to this
   * feed with the subscription completed — rather than dropping the click
   * entirely or landing them on the home page with no memory of what they meant
   * to do.
   */
  toggleSubscribe(): void {
    const feedId = this.feedId;
    if (!this.auth.session()) {
      void this.router.navigate(['/login'], {
        queryParams: { redirect: `/feeds/${feedId}`, subscribeFeed: feedId },
      });
      return;
    }

    if (this.isSubscribed) {
      this.subs.unsubscribe(feedId, (err) => this.toast.danger(apiMessage(err, '取消訂閱失敗')));
    } else {
      this.subs.subscribe(feedId, (err) => this.toast.danger(apiMessage(err, '訂閱失敗')));
    }
  }
}
