# Postman collection

> **Revision 2026-11-25-1** · 2026-11-25 22:14 Toronto time · last edited by Claude Code (Claude Opus 5.5) · [revision history](#revision-history)

Every endpoint the API origin serves, with assertions on status, shape and key
values. Required by `docs/reference/qqq-backtesting-model/backTesting-OLD-Architecture.md` §10:

> Postman collection for every service endpoint, checked in at `postman/` with
> an environment file per target (local, docker). Each request carries
> assertions (status, shape, key values) so the collection runs green in
> Postman's runner and in CI via Newman. Purpose: visual, manual exploration of
> the services alongside automated coverage.

| File | What it is |
|------|------------|
| `backtest.postman_collection.json` | 117 requests in 11 folders, 302 assertions |
| `fixtures/ZZPOSTMAN_daily.csv` | The three-bar file the import life cycle uploads |
| `local.postman_environment.json` | `baseUrl = http://localhost:8001` — from your own machine |
| `docker.postman_environment.json` | `baseUrl = http://server:8001` — from inside the compose network |

`baseUrl` is the API: the Spring Boot service, the one backend. There is no second address.

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

## Life cycles

The last folder, "Life cycles", takes each thing the API can create through its whole
life on the running stack: made, read back, removed, and checked gone.

| Life cycle | Steps |
|---|---|
| A backtest result | run `sma-crossover` with capital 12345, find the stored result, read it, delete it, read it again (404), find the delete in the jobs list, try to cancel that finished job (409) |
| A saved strategy | save `postman-lifecycle`, read it, save again (a new version), list both, remove every version, read it again (404) |
| An import, and its missing bar | upload three bars as `ZZPOSTMAN` from source `postman` (the 4th of November left out), find it in the log, read its bars; find the missing bar, pick the carried-forward close, fill it and read it among the bars; dry-run the undo, undo it; find the test instrument, dry-run its delete, delete it, and check its bars are gone |

Run the collection from the repository root, so the upload finds its file. A run
leaves nothing in the database: the import folder ends by deleting its test
instrument, which takes the repairs, flags and file history with it. The source
`postman` stays, and the archived CSV slice on disk, which the next run's upload
replaces (it is sent with `force`). Two runs in a row pass the same way.

The requests that read a stored walk-forward run (one run with its folds, the
recalculation dry run) are skipped when none is stored: a run takes minutes to
train, so the collection reads one rather than making one.

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

**Engine → Recalculate a walk-forward run at its own settings** is a dry run
(`dryRun: true`): it rescores the run from its stored signals, writes nothing,
and asserts every fold reproduces what is stored. Without `dryRun` the same
request rewrites the run's folds in place.

## Order matters inside a folder

A POST captures the id the GET after it uses, via collection variables
(`savedStrategyId`, `resultId`). Running a single request out of order can
therefore address an empty variable — run the folder, or the whole collection.

## Keeping it honest

§10 also says: "Every new endpoint ships with its Postman request and its unit
test in the same change." That is enforced rather than remembered —
the API's own test (`server/src/test/.../PostmanCoverageTest.java`) asks the running
service for its routes and fails if any lacks a request, if a request points at a
route that no longer exists, or if a request carries no assertion:

```bash
./gradlew :server:test --tests '*PostmanCoverageTest'
```

Adding a route without a request fails with the route named, e.g.
`routes with no Postman request: GET /api/brand-new-endpoint`.

## Revision history

Newest first, Toronto time. Every change to this file bumps the revision line at the top and adds a row here (`AGENTS.md`, Working rules).

| Revision | When | Who | Change |
|---|---|---|---|
| 2026-11-25-1 | 2026-11-25 22:14 | Claude Code (Claude Opus 5.5) | The missing-bar life cycle; the test instrument deleted at the end; run requests skipped when none is stored; counts (117 requests, 302 assertions) |
| 2026-11-24-1 | 2026-11-24 01:32 | Claude Code (Claude Fable 5.2) | The "Life cycles" folder (a result, a saved strategy, an import: created, read, removed, checked gone); six assertions brought up to date; counts (105 requests, 275 assertions) |
| 2026-11-23-2 | 2026-11-23 22:49 | Claude Code (Claude Fable 5.2) | One address: `baseUrl` is the Spring Boot service; `serverUrl` and the Node check are gone; counts (84 requests, 237 assertions) |
| 2026-11-23-1 | 2026-11-23 18:12 | Claude Code (Claude Fable 5.2) | `serverUrl` and the "Spring Boot service (G16)" folder: five requests for the new service's four routes |
| 2026-11-16-9 | 2026-11-16 16:26 | Claude Code (Claude Opus 5.5) | Counts (64 requests, 201 assertions): the targets, a money loss with one refused, a run's name kept (G49) |
| 2026-11-16-8 | 2026-11-16 15:43 | Claude Code (Claude Opus 5.5) | Counts (63 requests, 197 assertions): epochs, loss, failure and progress (G11) |
| 2026-11-16-7 | 2026-11-16 15:14 | Claude Code (Claude Opus 5.5) | Counts (63 requests, 195 assertions): a refused tax rate, and results' `taxRate` (G20) |
| 2026-11-16-6 | 2026-11-16 14:37 | Claude Code (Claude Opus 5.5) | Counts (62 requests, 192 assertions): a strategy's split (G37) |
| 2026-11-16-5 | 2026-11-16 14:24 | Claude Code (Claude Opus 5.5) | Counts (60 requests, 189 assertions): a result's test-window looks (G48) |
| 2026-11-16-4 | 2026-11-16 13:47 | Claude Code (Claude Opus 5.5) | Counts (60 requests, 188 assertions): the engine registry, a tree engine on a money loss refused, and engine fields on strategies and runs (B9) |
| 2026-11-16-3 | 2026-11-16 11:17 | Claude Code (Claude Opus 5.5) | Counts (58 requests, 181 assertions): the in-sample assertion (B4) |
| 2026-11-16-2 | 2026-11-16 09:17 | Claude Code (Claude Opus 5.5) | Counts (58 requests, 180 assertions): the naive-baseline assertion (B8) |
| 2026-11-16-1 | 2026-11-16 00:57 | Claude Code (Claude Opus 5.5) | Counts (58 requests, 179 assertions): the rule decisions request and the benchmark assertions (B7) |
| 2026-11-15-1 | 2026-11-15 14:14 | Claude Code (Claude Opus 5.5) | Revision stamp added; counts brought up to date (57 requests, 9 folders, 175 assertions); the recalculate dry run (B10) |
