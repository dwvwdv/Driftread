import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ThemeService } from './services/theme';

/**
 * Root component.
 *
 * Holds nothing but the outlet now — chrome belongs to whichever layout the route
 * selects, so the public site and the admin console no longer share a shell.
 *
 * ThemeService is injected purely so it is constructed at startup and applies the
 * stored theme.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
export class App {
  private theme = inject(ThemeService);
}
