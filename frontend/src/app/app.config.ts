import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withInMemoryScrolling } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';

import { routes } from './app.routes';
import { authInterceptor } from './interceptors/auth.interceptor';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(
      routes,
      // Opening an article should start at the top of it, and going back should
      // land where you left off — neither happened before.
      withInMemoryScrolling({ scrollPositionRestoration: 'enabled', anchorScrolling: 'enabled' }),
    ),
    provideHttpClient(withInterceptors([authInterceptor])),
    // provideAnimationsAsync() is gone with Angular Material. CDK Overlay does not
    // need the animations package, and every transition in the Offbeat components
    // is plain CSS, so @angular/animations is no longer a dependency at all.
  ],
};
