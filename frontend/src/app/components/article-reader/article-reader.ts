import { Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { DatePipe } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ArticleService } from '../../services/article';
import { Article } from '../../models';

@Component({
  selector: 'app-article-reader',
  imports: [RouterLink, DatePipe, MatCardModule, MatButtonModule, MatProgressSpinnerModule],
  templateUrl: './article-reader.html',
  styleUrl: './article-reader.scss',
})
export class ArticleReader implements OnInit {
  private route = inject(ActivatedRoute);
  private articleService = inject(ArticleService);

  article = signal<Article | null>(null);
  loading = signal(true);
  error = signal('');

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id') ?? '';
    this.articleService.getArticle(id).subscribe({
      next: a => { this.article.set(a); this.loading.set(false); },
      error: () => { this.error.set('無法載入文章。'); this.loading.set(false); },
    });
  }
}
