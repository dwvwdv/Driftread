// Template for /usr/share/nginx/html/env.js, rendered by
// docker-entrypoint.d/15-render-driftread-env.sh via `envsubst` when the
// container starts. `${SUPABASE_URL}` / `${SUPABASE_ANON_KEY}` below are
// envsubst placeholders, not TypeScript/Angular — this file never goes
// through `ng build`, it is copied into the nginx image as-is (see
// Dockerfile). Source of truth for the shape is public/env.js.
window.__env = {
  supabaseUrl: '${SUPABASE_URL}',
  supabaseAnonKey: '${SUPABASE_ANON_KEY}',
};
