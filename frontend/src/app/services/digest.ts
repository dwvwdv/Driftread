import { inject, Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { environment } from '../../environments/environment';

export interface DigestItem { id: string; title: string; summary: string | null; feed_title: string | null; }
export interface DailyDigestResult { date: string; timezone: string; items: DigestItem[]; truncated: boolean; }
@Injectable({ providedIn: 'root' })
export class DigestService {
  private http = inject(HttpClient);
  get(day: string, timezone: string) {
    return this.http.get<DailyDigestResult>(`${environment.apiUrl}/me/digest`, {
      params: new HttpParams().set('date', day).set('timezone', timezone),
    });
  }
  rss() { return this.http.get(`${environment.apiUrl}/me/rss`, { responseType: 'text' }); }
}
