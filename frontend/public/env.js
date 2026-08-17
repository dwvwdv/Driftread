// Local-dev default (`ng serve` / a bare `ng build`, no Docker involved).
//
// The published Docker image overwrites this exact file inside the
// container at startup — see
// frontend/docker-entrypoint.d/15-render-driftread-env.sh — with values
// rendered from the SUPABASE_URL / SUPABASE_ANON_KEY environment variables.
// Empty strings here make src/app/services/runtime-config.ts fall back to
// src/environments/environment*.ts, unchanged from before this file existed.
window.__env = {
  supabaseUrl: '',
  supabaseAnonKey: '',
};
