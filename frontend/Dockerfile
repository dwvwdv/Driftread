FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist/driftread/browser /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Runtime Supabase config (see src/app/services/runtime-config.ts): the
# template is rendered into env.js by the entrypoint script below every time
# the container starts, from SUPABASE_URL / SUPABASE_ANON_KEY env vars —
# not baked into the image at build time.
COPY env.template.js /etc/driftread/env.template.js
COPY docker-entrypoint.d/15-render-driftread-env.sh /docker-entrypoint.d/15-render-driftread-env.sh
RUN chmod +x /docker-entrypoint.d/15-render-driftread-env.sh
EXPOSE 80
