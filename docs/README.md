# STOP: integration agent, do not touch this folder

Everything in `docs/` belongs to John. These documents hold his business and design decisions; only he changes them (on Google Drive, copied here every night by the Drive sync).

**Integration agent and any other coding agent:**

- Never edit, add, move, rename or delete anything in `docs/`, this file included.
- Read these documents; build what they say.
- If a doc is wrong, unclear or missing something, write it in `BACKLOG.md` at the repo root for John. Do not fix the doc.

CI enforces this (`.github/workflows/docs-guard.yml`): a commit that changes `docs/` fails unless it is the nightly Drive sync, and on the main branch the change is undone.

*Set by John, Nov 27, 2026.*
