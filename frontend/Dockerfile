# Build stage — VITE_API_BASE_URL is baked into the JS bundle at
# build time (Vite env vars are compile-time, not runtime), so it
# must be passed as a build arg pointing at the real public domain.
# Since Caddy (the final stage) serves the frontend AND proxies
# /api + /uploads on the SAME domain, this can just be the site's own
# https://yourdomain.com — no separate API subdomain needed, and no
# CORS configuration required either (same-origin requests).
FROM node:22-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# Serve stage — Caddy serves the built static files and reverse-proxies
# /api and /uploads to the backend container. Caddy also handles TLS
# automatically via Let's Encrypt based on the domain in the Caddyfile
# — no certbot/nginx-ssl setup needed.
FROM caddy:2-alpine
COPY --from=build /app/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
