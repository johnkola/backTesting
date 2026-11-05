# Postman collection

Every endpoint the API origin serves, with assertions on status, shape and key
values. Required by `reference/qqq-backtesting-model/Architecture.md` §10:

> Postman collection for every service endpoint, checked in at `postman/` with
> an environment file per target (local, docker). Each request carries
> assertions (status, shape, key values) so the collection runs green in
> Postman's runner and in CI via Newman. Purpose: visual, manual exploration of
> the services alongside automated coverage.

| File | What it is |
|------|------------|
| `backtest.postman_collection.json` | 34 requests in 8 folders, 101 assertions |
| `local.postman_environment.json` | `baseUrl = http://localhost:8001` — from your own machine |
| `docker.postman_environment.json` | `baseUrl = http://api:8001` — from inside the compose network |

## Running it

```bash
docker compose up -d                      # the collection talks to the real stack
postman collection run postman/backtest.postman_collection.json \
  -e postman/local.postman_environment.json
```

Or open both files in the Postman app and use the Collection Runner, which is
the point of keeping this alongside the unit tests — the collection is
something you can click through while reading the code.

`postman login` is not needed to run a local file. The CLI prints a note about
publishing results to the Postman cloud and exits successfully without it.

Newman (`npx newman run …`) takes the same two files; it is not installed here,
so the commands above use the Postman CLI.

## Safe to run end to end

A collection that deletes data when you press Run is a trap. The destructive
endpoints are therefore covered through their **rejection** paths — a
non-numeric import id, an unknown instrument, an invalid rollup target, an
upload with no file — which proves the route is wired and validating without
removing anything or starting a long job.

Two requests do write, both additively and both obviously named:

- **Engine → Run a backtest** inserts one `backtest_results` row.
- **Strategy builder → Save a strategy** inserts one `strategies` row named
  `postman-smoke`. Saving never overwrites, so running the folder twice leaves
  two versions rather than replacing one.

Nothing in the collection trains a model: training is synchronous and writes to
disk, so `POST /api/nn/train` is exercised with an unknown symbol and asserted
to be refused quickly.

## Order matters inside a folder

A POST captures the id the GET after it uses, via collection variables
(`savedStrategyId`, `resultId`). Running a single request out of order can
therefore address an empty variable — run the folder, or the whole collection.

## Keeping it honest

§10 also says: "Every new endpoint ships with its Postman request and its unit
test in the same change." That is enforced rather than remembered —
`web/api/postmanCoverage.test.js` reads the routes out of `server.js` and fails
if any lacks a request, if a request points at a route that no longer exists, or
if a request carries no assertion:

```bash
cd web/api && npm test
```

Adding a route without a request fails with the route named, e.g.
`routes with no Postman request: GET /api/brand-new-endpoint`.
