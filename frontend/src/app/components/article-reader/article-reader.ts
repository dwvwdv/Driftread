import { Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { DatePipe } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ArticleService } from '../../services/article';
import { MeService } from '../../services/me';
import { AuthService } from '../../services/auth';
import { Article, BookmarkType } from '../../models';

@Component({
  selector: 'app-article-reader',
  imports: [
    RouterLink, DatePipe, MatCardModule, MatButtonModule,
    MatIconModule, MatProgressSpinnerModule,
  ],
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

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id') ?? '';
    this.articleService.getArticle(id).subscribe({
      next: a => {
        this.article.set(a);
        this.loading.set(false);
        if (this.auth.session()) {
          this.me.markRead(a.id).subscribe({ error: () => {} });
        }
      },
      error: () => { this.error.set('無法載入文章。'); this.loading.set(false); },
    });
  }

  toggle(type: BookmarkType): void {
    const a = this.article();
    if (!a || !this.auth.session()) return;
    const flag = type === 'favorite' ? this.favorited : this.readLater;
    const next = !flag();
    const obs = next
      ? this.me.addBookmark(a.id, type)
      : this.me.removeBookmark(a.id, type);
    obs.subscribe({ next: () => flag.set(next) });
  }
}
