import { isDevMode } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';

bootstrapApplication(App, appConfig)
  .then(() => {
    // PWA service worker (issue #92). Imported lazily so registration code
    // stays out of the initial bundle; dev builds have no ngsw-worker.js.
    if (!isDevMode()) {
      void import('./app/offline/sw-registration').then((m) => m.scheduleServiceWorker(true));
    }
  })
  .catch((err) => console.error(err));
