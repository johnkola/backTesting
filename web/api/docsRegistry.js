// Which markdown files the Docs tab offers, and under which URL slug.
//
// Its own module so it can be tested: server.js calls app.listen() and opens a
// pool at require time, so nothing defined in there is reachable from a unit
// test. Adding a doc is a row here and nothing else -- /api/docs enumerates
// this object, the client renders one tab per entry, and `docs.js` derives its
// KNOWN_DOCS from these keys so a doc can never render without recording a
// revision.
//
// The first two are the project's own docs at the repo root: how to operate it,
// and what to type. The last three are the QQQ model's design set, which
// describes the model rather than this codebase (Architecture.md §8.1a asks for
// them on the Docs tab).
//
// Those three come from `readonly-doc/`, which the author maintains by hand and
// nothing in this repo may write to. It supersedes
// `reference/qqq-backtesting-model/`, which is an earlier revision of the same
// three documents and is deliberately NOT served: its Design.md still fits
// normalization statistics on each feature's full history, where the current
// text fits them on the training window only, per walk-forward fold. Serving
// the stale copy would put a description of a data leak on the Docs tab.
//
// There is no entry for a repo-root ARCHITECTURE.md: that document was deleted
// when `readonly-doc/` was created. The `/architecture` and `/claude` legacy
// redirects in server.js therefore point at `qqq-architecture`, which is the
// only Architecture document left.
//
// `file` is a path relative to DOCS_DIR, and its spelling is load-bearing in two
// ways. Case: `docsRegistry.test.js` checks each path against the real directory
// entry case-sensitively, because Windows and macOS would open `DESIGN.md` for a
// file named `Design.md` while Docker's build context and any Linux deploy will
// not -- a case drift here is a doc tab that 404s only in production. And the
// space in `Design Notes.md` is real; it has to survive into web/Dockerfile's
// COPY (JSON form, so the space is not an argument break) and the compose bind
// mounts, which the tests below check.
const DOCS = {
  GETTING_STARTED: { file: 'GETTING-STARTED.md', label: 'Getting Started', slug: 'getting-started' },
  README: { file: 'README.md', label: 'README', slug: 'readme' },
  QQQ_DESIGN: {
    file: 'readonly-doc/Design Notes.md',
    label: 'QQQ Design Notes',
    slug: 'qqq-design',
  },
  QQQ_ARCHITECTURE: {
    file: 'readonly-doc/Architecture.md',
    label: 'QQQ Architecture',
    slug: 'qqq-architecture',
  },
  QQQ_FEATURES: {
    file: 'readonly-doc/Features.md',
    label: 'QQQ Features',
    slug: 'qqq-features',
  },
};

/** Where the authoritative design set lives; see the note above. */
const DESIGN_DOC_DIR = 'readonly-doc/';

/** slug -> registry key, so the client can address docs by the name in its URL. */
const DOC_BY_SLUG = new Map(Object.entries(DOCS).map(([name, c]) => [c.slug, name]));

module.exports = { DOCS, DOC_BY_SLUG, DESIGN_DOC_DIR };
