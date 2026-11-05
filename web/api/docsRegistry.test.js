const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');
const { DOCS, DOC_BY_SLUG } = require('./docsRegistry');

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

  it('keeps the two Architecture documents distinct', () => {
    // One is this repo's code architecture at the root, the other the QQQ
    // system design under reference/. Same word, different documents; if their
    // slugs or files ever collide one silently shadows the other.
    assert.notEqual(DOCS.ARCHITECTURE.file, DOCS.QQQ_ARCHITECTURE.file);
    assert.notEqual(DOCS.ARCHITECTURE.slug, DOCS.QQQ_ARCHITECTURE.slug);
  });

  it('maps every slug back to its registry key', () => {
    assert.equal(DOC_BY_SLUG.size, Object.keys(DOCS).length);
    for (const [key, conf] of Object.entries(DOCS)) {
      assert.equal(DOC_BY_SLUG.get(conf.slug), key);
    }
  });

  it('serves the QQQ docs out of reference/, not the repo root', () => {
    // They describe the model, not this codebase. Moving one to the root would
    // put it beside the project's own docs and confuse the two Architectures.
    assert.ok(DOCS.QQQ_DESIGN.file.startsWith('reference/'));
    assert.ok(DOCS.QQQ_ARCHITECTURE.file.startsWith('reference/'));
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
});
