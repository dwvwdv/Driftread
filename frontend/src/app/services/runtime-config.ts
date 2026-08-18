import { environment } from '../../environments/environment';

/**
 * Runtime-injected Supabase config, read from `window.__env`.
 *
 * `environment.ts` is compiled into the JS bundle at `npm run build` time, so
 * the one GHCR frontend image built by CI ships with whatever values were in
 * the repo at build time — empty ones, since real credentials do not belong
 * in source control. That made the official image permanently unusable for
 * login (see docs/FEATURES.md §4).
 *
 * `window.__env` is set by a plain `<script src="env.js">` (see index.html),
 * loaded before this bundle so the value is already there by the time
 * AuthService's constructor runs. Two copies of that file exist:
 *
 *   - `public/env.js` — the local-dev default (empty), copied into the
 *     bundle by `ng build` like any other asset.
 *   - `frontend/docker-entrypoint.d/15-render-driftread-env.sh` — overwrites
 *     it inside the container at startup, rendered from the container's
 *     `SUPABASE_URL` / `SUPABASE_ANON_KEY` env vars.
 *
 * So one built image works against any Supabase project: which one is a
 * deploy-time decision, not a build-time one.
 *
 * Empty or missing fields fall back to `environment.ts`, which keeps `ng
 * serve` (no env.js override) and any build that never runs the Docker
 * entrypoint working exactly as before this existed.
 */
declare global {
  interface Window {
    __env?: {
      supabaseUrl?: string;
      supabaseAnonKey?: string;
    };
  }
}

export function runtimeSupabaseConfig(): { supabaseUrl: string; supabaseAnonKey: string } {
  const injected = window.__env;
  return {
    supabaseUrl: injected?.supabaseUrl?.trim() || environment.supabaseUrl,
    supabaseAnonKey: injected?.supabaseAnonKey?.trim() || environment.supabaseAnonKey,
  };
}
