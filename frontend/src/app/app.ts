import { Component, HostListener, OnInit, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { Nav } from './components/nav/nav';
import { UpdateService } from './services/update';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, Nav],
  template: `
    <app-nav />
    <main class="page-content">
      <router-outlet />
    </main>
  `,
  styles: [`
    .page-content {
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px 16px;
    }
  `]
})
export class App implements OnInit {
  private updateService = inject(UpdateService);

  ngOnInit(): void {
    this.updateService.init();
  }

  @HostListener('document:visibilitychange')
  onVisibilityChange(): void {
    if (document.visibilityState === 'visible') {
      this.updateService.checkForUpdate();
    }
  }
}
