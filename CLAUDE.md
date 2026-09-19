Project docs live in `GETTING-STARTED.md` (the operating path end to end), `README.md` (roadmap, model cache, storage, deployment) and `ARCHITECTURE.md` (domain types, repos, web layer). Start there.

**Which doc gets a new fact.** The three do not overlap by design, and a fact belongs in exactly one of them; the others link to it. `GETTING-STARTED.md` owns the operating path and nothing else — it is the only end-to-end walkthrough, and it stays short by linking. `README.md` owns the operator surface: what to type and what to configure (CLI flags, API routes, env vars, recovery recipes, the roadmap). `ARCHITECTURE.md` owns the code: why the system is shaped this way, which invariants are load-bearing, and what a change must not break. When a topic has both an operator and a code side — the model cache, aggregation, compression, the drop-folder ingest — it is split along that line rather than told twice.

Standalone tools keep their own README beside the code rather than a section in these three — `tools/datagen/README.md` is the current one. A project doc should point at such a README, never restate it.

`DOC-REVIEW.md` and `UI-REVIEW.md` are rolling review trackers, not project docs: they are deliberately absent from the `DOCS` registry in `server.js` and do not render at `/docs`.

Note: the `/claude` route on the API is a 301 redirect to the client's `/docs/architecture` for back-compat — there is no separate Claude-facing doc page. The docs render inside the React client at `/docs`; the API serves them as JSON from `/api/docs`.
