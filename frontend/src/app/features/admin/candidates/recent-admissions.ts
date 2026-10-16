import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  Input,
  OnChanges,
  OnInit,
  SimpleChanges,
  inject,
  signal,
} from '@angular/core';
import { DatePipe } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subscription } from 'rxjs';
import { RecentAdmission } from '../../../models';
import { AdminService } from '../../../services/admin';
import { ObLoading, ObEmpty } from '../../../ui/state/state';

@Component({
  selector: 'app-recent-admissions',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, ObLoading, ObEmpty],
  templateUrl: './recent-admissions.html',
  styleUrl: './recent-admissions.scss',
})
export class RecentAdmissions implements OnInit, OnChanges {
  @Input() refreshVersion = 0;
  private admin = inject(AdminService);
  private destroyRef = inject(DestroyRef);
  private read?: Subscription;
  private sequence = 0;
  protected expanded = signal(true);
  protected items = signal<RecentAdmission[]>([]);
  protected loading = signal(true);
  protected error = signal(false);

  ngOnInit(): void {
    this.load();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['refreshVersion'] && !changes['refreshVersion'].firstChange) this.load();
  }

  protected load(): void {
    const sequence = ++this.sequence;
    this.read?.unsubscribe();
    this.loading.set(true);
    this.error.set(false);
    this.read = this.admin
      .recentAdmissions(10)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ items }) => {
          if (sequence !== this.sequence) return;
          this.items.set(items);
          this.loading.set(false);
        },
        error: () => {
          if (sequence !== this.sequence) return;
          this.error.set(true);
          this.loading.set(false);
        },
      });
  }

  protected methodLabel(method: RecentAdmission['method']): string {
    return (
      {
        ai_auto: 'AI 自動收錄',
        human_agree: '人工同意 AI 後收錄',
        human_disagree: '人工推翻 AI 後收錄',
        manual: '人工直接收錄',
        referrer_auto: '排程自動收錄',
        unknown: '歷史／其他管道（未記錄）',
      }[method] ?? '歷史／其他管道（未記錄）'
    );
  }
}
