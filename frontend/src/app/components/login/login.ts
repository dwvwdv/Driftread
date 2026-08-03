import { Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { AuthService } from '../../services/auth';

@Component({
  selector: 'app-login',
  imports: [FormsModule, MatCardModule, MatFormFieldModule, MatInputModule, MatButtonModule],
  template: `
    <mat-card class="auth-card">
      <mat-card-header>
        <mat-card-title>{{ mode() === 'login' ? '登入' : '註冊' }} Driftread</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        @if (!auth.isConfigured()) {
          <p class="warn">尚未設定 Supabase URL / Anon Key，請先在 environment 中配置。</p>
        }
        <mat-form-field appearance="outline" class="full">
          <mat-label>Email</mat-label>
          <input matInput type="email" [(ngModel)]="email" autocomplete="email" />
        </mat-form-field>
        <mat-form-field appearance="outline" class="full">
          <mat-label>密碼</mat-label>
          <input matInput type="password" [(ngModel)]="password" autocomplete="current-password" />
        </mat-form-field>
        @if (error()) {
          <p class="error">{{ error() }}</p>
        }
        @if (info()) {
          <p class="info">{{ info() }}</p>
        }
      </mat-card-content>
      <mat-card-actions>
        <button mat-flat-button color="primary" (click)="submit()" [disabled]="busy()">
          {{ mode() === 'login' ? '登入' : '註冊' }}
        </button>
        <button mat-button (click)="toggle()">
          {{ mode() === 'login' ? '沒有帳號？註冊' : '已有帳號？登入' }}
        </button>
      </mat-card-actions>
    </mat-card>
  `,
  styles: [
    `
      .auth-card {
        max-width: 420px;
        margin: 40px auto;
      }
      .full {
        width: 100%;
      }
      .error {
        color: #d32f2f;
      }
      .info {
        color: #2e7d32;
      }
      .warn {
        color: #ed6c02;
      }
    `,
  ],
})
export class Login {
  protected auth = inject(AuthService);
  private router = inject(Router);

  mode = signal<'login' | 'signup'>('login');
  email = '';
  password = '';
  error = signal('');
  info = signal('');
  busy = signal(false);

  toggle(): void {
    this.mode.set(this.mode() === 'login' ? 'signup' : 'login');
    this.error.set('');
    this.info.set('');
  }

  async submit(): Promise<void> {
    this.error.set('');
    this.info.set('');
    if (!this.email || !this.password) {
      this.error.set('請輸入 Email 與密碼');
      return;
    }
    this.busy.set(true);
    const result =
      this.mode() === 'login'
        ? await this.auth.signIn(this.email, this.password)
        : await this.auth.signUp(this.email, this.password);
    this.busy.set(false);
    if (result.error) {
      this.error.set(result.error);
      return;
    }
    if (this.mode() === 'signup') {
      this.info.set('註冊成功，請檢查信箱完成驗證後再登入。');
      return;
    }
    this.router.navigateByUrl('/');
  }
}
