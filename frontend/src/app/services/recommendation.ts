import { Injectable, WritableSignal, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { FeedFeedbackType, RecommendedFeed } from '../models';
import { AuthService } from './auth';
import { MeService } from './me';

const LIKED_KEY = 'driftread_liked';
const DISLIKED_KEY = 'driftread_disliked';
const SKIPPED_KEY = 'driftread_skipped';

/** Matches the `max_length=50` on each query parameter server-side. */
const MAX_SIGNALS = 50;

/**
 * 喜歡／不喜歡／跳過 for 猜你喜歡 and the feed detail page's own 喜歡／不喜歡.
 *
 * Three distinct, mutually-exclusive local stances per feed (liking a feed
 * clears it from disliked/skipped, and so on) — mirroring the server's own
 * one-row-per-feed model (TODO.md 推薦回饋持久化, `user_feed_feedback`,
 * one PRIMARY KEY (user_id, feed_id)). `skipped` is deliberately separate
 * from `disliked`: TODO.md is explicit that a 猜你喜歡 card swipe ("跳過") is
 * only a short-term signal, not the same as feed-detail's explicit "不喜歡".
 *
 * Signed-in state additionally persists each action server-side
 * (MeService.setFeedFeedback) so it survives across devices — best-effort,
 * fire-and-forget, same as this service's local-only writes always were:
 * the three arrays here stay the immediate source of truth for this
 * browser's own UI (isLiked/isDisliked, the next getRecommendations() call),
 * and a failed persist call just means the next action from a fresh page
 * load tries again, not that anything here needs to roll back. Signed-out
 * state stays exactly as before — untouched by any of this.
 */
@Injectable({ providedIn: 'root' })
export class RecommendationService {
  private http = inject(HttpClient);
  private auth = inject(AuthService);
  private me = inject(MeService);
  private base = environment.apiUrl;

  private _liked = signal<string[]>(this._load(LIKED_KEY));
  private _disliked = signal<string[]>(this._load(DISLIKED_KEY));
  private _skipped = signal<string[]>(this._load(SKIPPED_KEY));

  liked = this._liked.asReadonly();
  disliked = this._disliked.asReadonly();
  skipped = this._skipped.asReadonly();

  private _load(key: string): string[] {
    try {
      return JSON.parse(localStorage.getItem(key) ?? '[]');
    } catch {
      return [];
    }
  }

  private _set(target: WritableSignal<string[]>, key: string, ids: string[]): void {
    target.set(ids);
    localStorage.setItem(key, JSON.stringify(ids));
  }

  like(feedId: string): void {
    this._set(this._liked, LIKED_KEY, [...new Set([...this._liked(), feedId])]);
    this._set(this._disliked, DISLIKED_KEY, this._disliked().filter((id) => id !== feedId));
    this._set(this._skipped, SKIPPED_KEY, this._skipped().filter((id) => id !== feedId));
    this._persist(feedId, 'liked');
  }

  dislike(feedId: string): void {
    this._set(this._disliked, DISLIKED_KEY, [...new Set([...this._disliked(), feedId])]);
    this._set(this._liked, LIKED_KEY, this._liked().filter((id) => id !== feedId));
    this._set(this._skipped, SKIPPED_KEY, this._skipped().filter((id) => id !== feedId));
    this._persist(feedId, 'disliked');
  }

  /**
   * A card-deck "跳過" — a light "not now", not a verdict. Kept out of the
   * next deck the same way 喜歡/不喜歡 are, but server-side (for a signed-in
   * caller) this only downweights the feed for a short window rather than
   * excluding it forever (see backend/routers/recommendations.py's
   * `_SKIP_DECAY`) — an old skip stops affecting anything and the feed can
   * resurface in a later batch.
   */
  skip(feedId: string): void {
    this._set(this._skipped, SKIPPED_KEY, [...new Set([...this._skipped(), feedId])]);
    this._set(this._liked, LIKED_KEY, this._liked().filter((id) => id !== feedId));
    this._set(this._disliked, DISLIKED_KEY, this._disliked().filter((id) => id !== feedId));
    this._persist(feedId, 'skipped');
  }

  private _persist(feedId: string, type: FeedFeedbackType): void {
    if (!this.auth.session()) return;
    this.me.setFeedFeedback(feedId, type).subscribe({ error: () => {} });
  }

  /**
   * The backend caps `liked`/`disliked`/`skipped` at 50 entries each
   * (backend/routers/recommendations.py). This used to append every stored id, so
   * the moment a user liked their 51st feed the request started failing
   * validation with a 422 and 猜你喜歡 was permanently broken for them — the more
   * someone used the feature, the sooner it died.
   *
   * Most recent wins: taste drifts, and the last 50 signals describe someone
   * better than their first 50 do.
   *
   * Sent for a signed-in caller too, not just anonymous: the backend already
   * reads persisted feedback independently for a signed-in caller, so this
   * is a redundant-but-harmless belt-and-braces signal that also covers the
   * gap between an action landing locally and its own persist call settling.
   */
  getRecommendations(limit = 10): Observable<RecommendedFeed[]> {
    let params = new HttpParams().set('limit', limit);
    for (const id of this._liked().slice(-MAX_SIGNALS)) params = params.append('liked', id);
    for (const id of this._disliked().slice(-MAX_SIGNALS)) params = params.append('disliked', id);
    for (const id of this._skipped().slice(-MAX_SIGNALS)) params = params.append('skipped', id);
    return this.http.get<RecommendedFeed[]>(`${this.base}/recommendations`, { params });
  }
}
