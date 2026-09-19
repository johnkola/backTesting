# The React client as a standalone SPA: built to static files and served by
# nginx, with no backend of its own. It reaches the API by URL (VITE_API_URL,
# baked in at build time), so it can be deployed anywhere without carrying a
# server along. Build context is the project root.

# --- 1. Build ---
FROM node:22-alpine AS build
WORKDIR /build

# `npm install`, not `npm ci`: both package-lock.json files are gitignored
# (dropped in 344b38d), so there is no lockfile in the build context to
# install from.
COPY web/client/package.json ./
RUN npm install --no-audit --no-fund

COPY web/client/ ./

# Baked into the bundle at build time — Vite substitutes import.meta.env at
# compile time, so this cannot be changed by an env var at container start.
ARG VITE_API_URL=http://localhost:8001
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

# --- 2. Serve ---
FROM nginx:alpine
COPY --from=build /build/dist /usr/share/nginx/html
COPY web/client/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 3000
