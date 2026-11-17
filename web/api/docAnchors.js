// Heading anchors for the docs the API renders, kept apart from server.js so a
// test can load them without starting the server.
//
// One rule, two users: `renderMarkdown` in server.js gives each heading this
// id, and docLinks.test.js checks every `/docs/<slug>#anchor` and `#anchor`
// link in the project docs against the same ids. A link checked against a
// different slug rule would pass here and land nowhere in the app (G69: 15
// links into a deleted doc went unnoticed for weeks).

/**
 * GitHub's heading-anchor rules: lowercase, drop punctuation, each space to a
 * hyphen.
 *
 * `\s` and not `\s+` — that is the whole difference between `build--run` and
 * `build-run`. Dropping the `&` from "Build & Run" leaves two spaces behind, and
 * GitHub turns both into hyphens; collapsing them produces an id no existing
 * link points at.
 */
function slugify(text) {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, '')
    .replace(/\s/g, '-');
}

/**
 * The ids `renderMarkdown` gives a document's headings, in order: ATX
 * headings outside fenced code, repeats suffixed -1, -2, … as GitHub does.
 */
function headingIds(markdown) {
  const ids = [];
  const seen = new Map();
  let fence = null;
  for (const line of markdown.split(/\r?\n/)) {
    const f = line.match(/^\s*(`{3,}|~{3,})/);
    if (f) {
      if (fence === null) fence = f[1][0];
      else if (f[1][0] === fence) fence = null;
      continue;
    }
    if (fence !== null) continue;
    const h = line.match(/^#{1,6}\s+(.*?)\s*#*\s*$/);
    if (!h) continue;
    const base = slugify(h[1]);
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    ids.push(n === 0 ? base : `${base}-${n}`);
  }
  return ids;
}

module.exports = { slugify, headingIds };
