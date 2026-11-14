const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');
const { DOCS, DOC_BY_SLUG, DESIGN_DOC_DIR } = require('./docsRegistry');
const { KNOWN_DOCS } = require('./docs');

/** The repo root, which is what DOCS_DIR points at outside a container. */
const ROOT = path.resolve(__dirname, '..', '..');

/**
 * Does this exact path exist, matching case at every segment?
 *
 * `fs.existsSync` is case-insensitive on Windows and on a default macOS
 * volume, so it happily opens `Design.md` for a registry entry spelled
 * `DESIGN.md` -- and then the Linux container, and Docker's case-sensitive
 * build context, 404 the doc. Walking `readdirSync` and comparing the real
 * entry name is the only check that fails on the machine where the mistake is
 * made rather than the one where it bites.
 *
 * This is not hypothetical: on 2026-10-24 these two files were renamed from
 * DESIGN.md/ARCHITECTURE.md to Design.md/Architecture.md, `git status` hid it
 * because core.ignorecase is true, and every api image build failed on
 * "/reference/qqq-backtesting-model/DESIGN.md: not found" while the docs kept
 * rendering locally off the bind mount.
 */
function existsCaseSensitive(relPath) {
  let dir = ROOT;
  for (const segment of relPath.split('/')) {
    let entries;
    try {
      entries = fs.readdirSync(dir);
    } catch {
      return false;
    }
    if (!entries.includes(segment)) return false;
    dir = path.join(dir, segment);
  }
  return true;
}

describe('docs registry', () => {
  it('registers at least the five docs the Docs tab shows', () => {
    assert.ok(Object.keys(DOCS).length >= 5);
  });

  it('points every entry at a file that exists, spelled exactly right', () => {
    const broken = [];
    for (const [key, conf] of Object.entries(DOCS)) {
      if (!existsCaseSensitive(conf.file)) broken.push(`${key} -> ${conf.file}`);
    }
    assert.deepEqual(broken, [], `registry entries with no matching file: ${broken}`);
  });

  it('points at files with content, not empty placeholders', () => {
    for (const [key, conf] of Object.entries(DOCS)) {
      const size = fs.statSync(path.join(ROOT, conf.file)).size;
      assert.ok(size > 100, `${key} (${conf.file}) is only ${size} bytes`);
    }
  });

  it('gives every doc a unique slug', () => {
    const slugs = Object.values(DOCS).map((d) => d.slug);
    assert.equal(new Set(slugs).size, slugs.length, `duplicate slug in ${slugs}`);
  });

  it('uses url-safe slugs', () => {
    for (const [key, conf] of Object.entries(DOCS)) {
      assert.match(conf.slug, /^[a-z0-9-]+$/, `${key} has an awkward slug: ${conf.slug}`);
      assert.equal(conf.slug, encodeURIComponent(conf.slug));
    }
  });

  it('gives every doc a label for its tab', () => {
    for (const [key, conf] of Object.entries(DOCS)) {
      assert.ok(conf.label && conf.label.trim(), `${key} has no label`);
    }
  });

  it('no longer offers the deleted repo-root ARCHITECTURE.md', () => {
    // There used to be two Architecture documents -- this repo's code
    // architecture at the root, and the QQQ system design -- and the test here
    // was that they stayed distinct. The root one was deleted when
    // readonly-doc/ became the design set, so the surviving risk is the
    // opposite one: an entry still pointing at the file that is gone. That is
    // what broke the Docs tab and this suite.
    const rootArchitecture = Object.entries(DOCS)
      .filter(([, conf]) => conf.file === 'ARCHITECTURE.md')
      .map(([key]) => key);
    assert.deepEqual(rootArchitecture, [], `points at the deleted root doc: ${rootArchitecture}`);
    assert.equal(DOCS.QQQ_ARCHITECTURE.file, `${DESIGN_DOC_DIR}Architecture.md`);
  });

  it('gives every doc its own file, so none shadows another', () => {
    // Two entries on one file render as two tabs of the same text under two
    // slugs, and record revisions under two names -- a split history of one
    // document. It is the failure the old two-Architectures check guarded
    // against, stated for the whole registry instead of that one pair.
    const files = Object.values(DOCS).map((d) => d.file);
    assert.equal(new Set(files).size, files.length, `duplicate file in ${files}`);
  });

  it('maps every slug back to its registry key', () => {
    assert.equal(DOC_BY_SLUG.size, Object.keys(DOCS).length);
    for (const [key, conf] of Object.entries(DOCS)) {
      assert.equal(DOC_BY_SLUG.get(conf.slug), key);
    }
  });

  it('serves the design set out of readonly-doc/, not the stale reference/ copy', () => {
    // readonly-doc/ is the set the author maintains by hand.
    // reference/qqq-backtesting-model/ holds an earlier revision of the same
    // three documents: its Design.md still fits normalization statistics on
    // each feature's full history, where the current text fits them on the
    // training window only, per fold. Serving the old copy would publish a
    // description of a data leak, so pointing back at it is a failure, not
    // just a stale path.
    for (const key of ['QQQ_DESIGN', 'QQQ_ARCHITECTURE', 'QQQ_FEATURES']) {
      assert.ok(DOCS[key], `${key} is not registered`);
      assert.ok(
        DOCS[key].file.startsWith(DESIGN_DOC_DIR),
        `${key} should come from ${DESIGN_DOC_DIR}, got ${DOCS[key].file}`,
      );
      assert.ok(!DOCS[key].file.startsWith('reference/'), `${key} points at the stale copy`);
    }
  });

  it('records a revision for every doc it renders', () => {
    // DOC-REVIEW pass finding #104: a key in the registry but not in
    // `docs.js`'s KNOWN_DOCS renders fine and silently never records a
    // revision, so its history page is empty forever. KNOWN_DOCS is now
    // derived from these keys, and this pins that -- re-listing them by hand
    // would reintroduce the drift.
    assert.deepEqual([...KNOWN_DOCS].sort(), Object.keys(DOCS).sort());
  });
});

describe('docs registry vs the container build', () => {
  it('every registered file is also copied into the image', () => {
    // A doc that renders locally off a bind mount but is missing from the
    // image works in compose and 404s anywhere else. The Dockerfile's COPY
    // lines are the second place these paths are written, so they have to
    // agree with the registry.
    const dockerfile = fs.readFileSync(path.join(ROOT, 'web', 'Dockerfile'), 'utf8');
    const missing = Object.entries(DOCS)
      .filter(([, conf]) => !dockerfile.includes(conf.file))
      .map(([key, conf]) => `${key} -> ${conf.file}`);
    assert.deepEqual(missing, [], `registered but not COPYed into the image: ${missing}`);
  });

  it('every registered file is bind-mounted into the api service', () => {
    // The third place these paths are written. A compose mount whose host side
    // does not exist does not fail the service -- Docker creates a directory
    // there -- so the doc renders as an EISDIR error rather than a missing
    // file, which is a worse symptom than a 404 and harder to read.
    const compose = fs.readFileSync(path.join(ROOT, 'docker-compose.yml'), 'utf8');
    const missing = Object.entries(DOCS)
      .filter(([, conf]) => !compose.includes(`/app/docs/${conf.file}`))
      .map(([key, conf]) => `${key} -> ${conf.file}`);
    assert.deepEqual(missing, [], `registered but not mounted in compose: ${missing}`);
  });
});

describe('docs registry vs the legacy redirects', () => {
  it('redirects every old doc URL at a slug the registry knows', () => {
    // server.js 301s the old server-rendered paths (/readme, /architecture,
    // /claude) into the client. A target the registry does not know is a 301
    // into a 404, which is exactly what deleting the root ARCHITECTURE.md
    // would have left behind: /claude and /architecture still pointed at an
    // `architecture` slug that no longer existed.
    //
    // Read as text, not required: server.js calls app.listen() and opens a
    // pool at require time.
    const src = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf8');
    const block = src.match(/for \(const \[slug, target\] of \[([\s\S]*?)\]\) \{/);
    assert.ok(block, 'could not find the redirect table in server.js');

    const pairs = [...block[1].matchAll(/\[\s*'([^']+)'\s*,\s*'([^']+)'\s*\]/g)]
      .map((m) => ({ from: m[1], to: m[2] }));
    assert.ok(pairs.length >= 4, `only found ${pairs.length} redirects`);

    const unknown = pairs
      .filter((pair) => !DOC_BY_SLUG.has(pair.to))
      .map((pair) => `/${pair.from} -> /docs/${pair.to}`);
    assert.deepEqual(unknown, [], `redirects to slugs no doc owns: ${unknown}`);
  });
});
