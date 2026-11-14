# The Node API service (web/api/). It reads Postgres directly, proxies writes to the
# Python loader and engine calls to the Java engine, and renders the markdown
# doc pages. It no longer serves the React client — that is its own image
# (web/client/Dockerfile), so the client can be deployed independently.
# Build context is the project root (see docker-compose `context: .`).

FROM node:22-alpine
WORKDIR /app

# `npm install`, not `npm ci`: package-lock.json is gitignored (344b38d), so
# there is no lockfile in the build context.
COPY web/api/package.json ./
RUN npm install --omit=dev --no-audit --no-fund

COPY web/api/ ./

# Project docs go into /app/docs; DOCS_DIR tells the server to render markdown
# from there.
RUN mkdir -p /app/docs
COPY GETTING-STARTED.md README.md /app/docs/
# The QQQ design set keeps its readonly-doc/ path, because that is the path the
# DOCS registry addresses it by. JSON form because `Design Notes.md` contains a
# space, which the shell form would split into two arguments.
#
# Not reference/qqq-backtesting-model/: that is an earlier revision of these
# same three documents and the registry deliberately no longer serves it.
COPY ["readonly-doc/Architecture.md", "readonly-doc/Design Notes.md", "readonly-doc/Features.md", "/app/docs/readonly-doc/"]

ENV NODE_ENV=production
ENV DOCS_DIR=/app/docs
ENV PORT=8001

EXPOSE 8001
CMD ["node", "server.js"]
