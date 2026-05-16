import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { DiscoverResponse, Feed } from '../models';

@Injectable({ providedIn: 'root' })
export class DiscoverService {
  private http = inject(HttpClient);
  private base = environment.apiUrl;

  discover(url: string): Observable<DiscoverResponse> {
    return this.http.post<DiscoverResponse>(`${this.base}/discover`, { url });
  }

  importByUrl(feedUrl: string): Observable<Feed> {
    return this.http.post<Feed>(`${this.base}/discover/import`, { feed_url: feedUrl });
  }
}
