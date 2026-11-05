// Which markdown files the Docs tab offers, and under which URL slug.
//
// Its own module so it can be tested: server.js calls app.listen() and opens a
// pool at require time, so nothing defined in there is reachable from a unit
// test. Adding a doc is a row here and nothing else -- /api/docs enumerates
// this object and the client renders one tab per entry.
//
// The first three are the project's own docs at the repo root: how to operate
// it, what to type, and how the code is shaped. The last two are the QQQ
// model's design and architecture, which live under reference/ because they
// describe the model rather than this codebase (Architecture.md §8.1a asks for
// both on the Docs tab).
//
// "Architecture" appears twice by name and they are different documents -- the
// root one is this repo's code architecture, the reference one is the QQQ
// system design -- so the slugs are what keep them apart.
//
// `file` is a path relative to DOCS_DIR, and its spelling is load-bearing:
// `docsRegistry.test.js` checks each one against the real directory entry
// case-sensitively. Windows and macOS would open `DESIGN.md` for a file named
// `Design.md`, but Docker's build context and any Linux deploy will not, so a
// case drift here is a doc tab that 404s only in production.
const DOCS = {
  GETTING_STARTED: { file: 'GETTING-STARTED.md', label: 'Getting Started', slug: 'getting-started' },
  README: { file: 'README.md', label: 'README', slug: 'readme' },
  ARCHITECTURE: { file: 'ARCHITECTURE.md', label: 'Architecture', slug: 'architecture' },
  QQQ_DESIGN: {
    file: 'reference/qqq-backtesting-model/Design.md',
    label: 'QQQ Design Notes',
    slug: 'qqq-design',
  },
  QQQ_ARCHITECTURE: {
    file: 'reference/qqq-backtesting-model/Architecture.md',
    label: 'QQQ Architecture',
    slug: 'qqq-architecture',
  },
};

/** slug -> registry key, so the client can address docs by the name in its URL. */
const DOC_BY_SLUG = new Map(Object.entries(DOCS).map(([name, c]) => [c.slug, name]));

module.exports = { DOCS, DOC_BY_SLUG };
