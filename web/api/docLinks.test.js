// Every link the project docs make into the Docs tab lands somewhere (G69).
//
// README and GETTING-STARTED link into /docs/<slug>#anchor and to their own
// #anchor. The client renders a slug the registry does not know as "unknown
// doc", and an anchor no heading carries as the top of the page, so a broken
// link fails silently. 15 of them pointed into the deleted ARCHITECTURE.md
// for weeks. This walks every such link and checks it against the ids the
// renderer gives (docAnchors.headingIds, the rule server.js uses).
//
// Only the two project docs: the design set in docs/readonly-doc/ is the
// author's and may not be edited here, so a broken link in it could not be
// fixed by the test's failure.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');
const { headingIds } = require('./docAnchors');
const { DOCS, DOC_BY_SLUG } = require('./docsRegistry');

const ROOT = path.resolve(__dirname, '..', '..');
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/** `[text](target)` links outside fenced code, with their line numbers. */
function links(markdown) {
  const out = [];
  let fence = null;
  markdown.split(/\r?\n/).forEach((line, i) => {
    const f = line.match(/^\s*(`{3,}|~{3,})/);
    if (f) {
      if (fence === null) fence = f[1][0];
      else if (f[1][0] === fence) fence = null;
      return;
    }
    if (fence !== null) return;
    for (const m of line.matchAll(/\]\(([^)\s]+)\)/g)) out.push({ line: i + 1, target: m[1] });
  });
  return out;
}

/** What is wrong with each docs-tab link in one project doc, as readable lines. */
function brokenLinks(name) {
  const own = DOCS[name];
  const ownIds = new Set(headingIds(read(own.file)));
  const idsOf = new Map();
  const problems = [];
  for (const { line, target } of links(read(own.file))) {
    const where = `${own.file}:${line} ${target}`;
    if (target.startsWith('#')) {
      if (!ownIds.has(target.slice(1))) problems.push(`${where}: no such heading here`);
      continue;
    }
    const m = target.match(/^\/docs\/([^#?]+)(?:\?[^#]*)?(?:#(.+))?$/);
    if (!m) continue;
    const doc = DOC_BY_SLUG.get(m[1]);
    if (!doc) {
      problems.push(`${where}: no doc with slug "${m[1]}"`);
      continue;
    }
    if (!m[2]) continue;
    if (!idsOf.has(doc)) idsOf.set(doc, new Set(headingIds(read(DOCS[doc].file))));
    if (!idsOf.get(doc).has(m[2])) problems.push(`${where}: no heading "#${m[2]}" in ${DOCS[doc].file}`);
  }
  return problems;
}

describe('links from the project docs into the Docs tab (G69)', () => {
  for (const name of ['README', 'GETTING_STARTED']) {
    it(`${DOCS[name].file}: every /docs link and #anchor lands on a heading`, () => {
      assert.deepEqual(brokenLinks(name), []);
    });
  }
});

describe('headingIds, the renderer\'s anchor rule', () => {
  it('slugs as GitHub does and numbers repeats', () => {
    const md = '# Build & Run\n## `backtest` — inspect\n## Notes\n## Notes\n';
    assert.deepEqual(headingIds(md), ['build--run', 'backtest--inspect', 'notes', 'notes-1']);
  });

  it('skips headings inside fenced code', () => {
    assert.deepEqual(headingIds('```bash\n# not a heading\n```\n## Real\n'), ['real']);
  });
});
