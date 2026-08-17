#!/bin/sh
# Runs as part of the official nginx image's own entrypoint, which execs
# every executable script under /docker-entrypoint.d/ (in name order) before
# starting nginx — see /docker-entrypoint.sh in the base image. No custom
# ENTRYPOINT/CMD needed for that: dropping a script here is the documented
# extension point.
#
# Renders window.__env from this container's SUPABASE_URL / SUPABASE_ANON_KEY
# so the one published GHCR image can point at any Supabase project without a
# rebuild. See frontend/src/app/services/runtime-config.ts for the read side.
set -eu

envsubst '${SUPABASE_URL} ${SUPABASE_ANON_KEY}' \
  < /etc/driftread/env.template.js \
  > /usr/share/nginx/html/env.js
