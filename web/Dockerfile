# Multi-stage: build the React client, then bundle it into the Node server image.
# Build context is the project root (see docker-compose `context: .`).

# --- 1. Build React client ---
FROM node:22-alpine AS client-build
WORKDIR /build
COPY web/client/package.json web/client/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY web/client/ ./
RUN npm run build

# --- 2. Server runtime ---
FROM node:22-alpine
WORKDIR /app

# Server deps only (skip devDependencies)
COPY web/server/package.json web/server/package-lock.json* ./
RUN npm ci --omit=dev --no-audit --no-fund

# Server source
COPY web/server/ ./

# Built React assets live in /app/public; the server's `hasClientBuild` check
# auto-enables static + SPA-fallback when this directory exists.
COPY --from=client-build /build/dist ./public

# Project docs go into /app/docs; DOCS_DIR (set below) tells the server
# to render markdown from there.
RUN mkdir -p /app/docs
COPY README.md ARCHITECTURE.md /app/docs/

ENV NODE_ENV=production
ENV DOCS_DIR=/app/docs
ENV PORT=3000

EXPOSE 3000
CMD ["node", "server.js"]
