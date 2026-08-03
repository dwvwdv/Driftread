import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { Feed } from '../models';

const LIKED_KEY = 'driftread_liked';
const DISLIKED_KEY = 'driftread_disliked';

@Injectable({ providedIn: 'root' })
export class RecommendationService {
  private http = inject(HttpClient);
  private base = environment.apiUrl;

  private _liked = signal<string[]>(this._load(LIKED_KEY));
  private _disliked = signal<string[]>(this._load(DISLIKED_KEY));

  liked = this._liked.asReadonly();
  disliked = this._disliked.asReadonly();

  private _load(key: string): string[] {
    try {
      return JSON.parse(localStorage.getItem(key) ?? '[]');
    } catch {
      return [];
    }
  }

  like(feedId: string): void {
    const next = [...new Set([...this._liked(), feedId])];
    this._liked.set(next);
    this._disliked.set(this._disliked().filter((id) => id !== feedId));
    localStorage.setItem(LIKED_KEY, JSON.stringify(next));
    localStorage.setItem(DISLIKED_KEY, JSON.stringify(this._disliked()));
  }

  dislike(feedId: string): void {
    const next = [...new Set([...this._disliked(), feedId])];
    this._disliked.set(next);
    this._liked.set(this._liked().filter((id) => id !== feedId));
    localStorage.setItem(DISLIKED_KEY, JSON.stringify(next));
    localStorage.setItem(LIKED_KEY, JSON.stringify(this._liked()));
  }

  getRecommendations(limit = 10): Observable<Feed[]> {
    let params = new HttpParams().set('limit', limit);
    for (const id of this._liked()) params = params.append('liked', id);
    for (const id of this._disliked()) params = params.append('disliked', id);
    return this.http.get<Feed[]>(`${this.base}/recommendations`, { params });
  }
}
