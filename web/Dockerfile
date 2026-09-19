# The Node API service. It reads Postgres directly, proxies writes to the
# Python loader and engine calls to the Java engine, and renders the markdown
# doc pages. It no longer serves the React client — that is its own image
# (web/client/Dockerfile), so the client can be deployed independently.
# Build context is the project root (see docker-compose `context: .`).

FROM node:22-alpine
WORKDIR /app

# `npm install`, not `npm ci`: package-lock.json is gitignored (344b38d), so
# there is no lockfile in the build context.
COPY web/server/package.json ./
RUN npm install --omit=dev --no-audit --no-fund

COPY web/server/ ./

# Project docs go into /app/docs; DOCS_DIR tells the server to render markdown
# from there.
RUN mkdir -p /app/docs
COPY GETTING-STARTED.md README.md ARCHITECTURE.md /app/docs/

ENV NODE_ENV=production
ENV DOCS_DIR=/app/docs
ENV PORT=8001

EXPOSE 8001
CMD ["node", "server.js"]
