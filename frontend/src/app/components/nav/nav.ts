import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { AuthService } from '../../services/auth';

@Component({
  selector: 'app-nav',
  imports: [RouterLink, RouterLinkActive, MatToolbarModule, MatButtonModule, MatIconModule],
  templateUrl: './nav.html',
  styleUrl: './nav.scss',
})
export class Nav {
  protected auth = inject(AuthService);
  private router = inject(Router);

  async signOut(): Promise<void> {
    await this.auth.signOut();
    this.router.navigateByUrl('/');
  }
}
