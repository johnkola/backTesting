import { useEffect, type MouseEvent } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, type DocContent, type DocSummary } from '../lib/api'
import { useApiData } from '../lib/useApiData'
import Catalogue from '../components/Catalogue'

/** The Docs tab that is not a document: the engines' and benchmarks' catalogue (R74). */
const CATALOGUE = 'catalogue'

/**
 * The project docs, inside the app.
 *
 * They used to be server-rendered pages on the API origin with their own
 * navbar, their own daisyUI major version and a hard-coded `corporate` theme —
 * a second application you left the app to reach, with no link back. The API
 * now returns the rendered markdown as data (`GET /api/docs/:slug`) and this
 * page puts it in the same layout, nav and theme as everything else.
 *
 * Beside the documents, the catalogue: its entries come from the runner's
 * registries, not from Markdown, so it is a tab of its own with no history.
 */
export default function DocsPage() {
  const { slug = 'readme' } = useParams<{ slug: string }>()
  const [params] = useSearchParams()
  const rev = params.get('rev')
  const navigate = useNavigate()
  const { hash } = useLocation()

  const { data: registry } = useApiData<DocSummary[]>(
    (signal) => api.docs(signal).then((r) => r.items),
    [],
  )
  const catalogue = slug === CATALOGUE
  const { data: doc, error } = useApiData<DocContent | null>(
    (signal) => (catalogue ? Promise.resolve(null) : api.doc(slug, rev, signal)),
    [slug, rev],
  )

  /**
   * The docs link to each other as ordinary hrefs (`/docs/readme#model-cache`),
   * because they are markdown and have to keep working on GitHub too. Left
   * alone, each one full-reloads the SPA. Route them instead.
   */
  function onDocClick(e: MouseEvent<HTMLElement>) {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
    const link = (e.target as HTMLElement).closest('a')
    const href = link?.getAttribute('href')
    if (!href?.startsWith('/docs/')) return // external and same-page # links: leave them be
    e.preventDefault()
    navigate(href)
  }

  // The heading only exists once the fetched HTML is in the DOM, which is after
  // the browser has already given up on the hash it was handed.
  useEffect(() => {
    if (!doc || !hash) return
    document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView()
  }, [doc, hash])

  return (
    <section>
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div role="tablist" className="tabs tabs-box">
          {(registry ?? []).map((d) => (
            <Link
              key={d.slug}
              role="tab"
              to={`/docs/${d.slug}`}
              className={`tab ${d.slug === slug ? 'tab-active' : ''}`}
            >
              {d.label}
            </Link>
          ))}
          <Link role="tab" to={`/docs/${CATALOGUE}`} className={`tab ${catalogue ? 'tab-active' : ''}`}>
            Catalogue
          </Link>
        </div>
        <div className="flex-1" />
        {!catalogue && (
          <Link to={`/docs/${slug}/history`} className="link link-primary text-sm">
            View history
          </Link>
        )}
      </div>

      {catalogue && <Catalogue />}
      {error && <div className="alert alert-error">{error}</div>}
      {!catalogue && !doc && !error && <span className="loading loading-spinner" />}

      {doc?.revision && (
        <div className="alert alert-warning mb-4">
          <span>
            Viewing revision <span className="font-mono">#{doc.revision.id}</span>, captured{' '}
            {new Date(doc.revision.capturedAt).toLocaleString()}.
          </span>
          <Link to={`/docs/${slug}`} className="link">Back to current</Link>
        </div>
      )}

      {doc && (
        // The markdown is the repo's own docs and the design set under
        // docs/readonly-doc/, read from a read-only mount by the API — the same
        // trust boundary as when the API rendered these pages itself.
        <article
          className="prose lg:prose-lg max-w-none"
          onClick={onDocClick}
          dangerouslySetInnerHTML={{ __html: doc.html }}
        />
      )}
    </section>
  )
}
