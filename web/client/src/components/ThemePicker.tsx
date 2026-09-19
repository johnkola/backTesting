import { useState } from 'react'
import { applyTheme, readTheme, THEMES, type Theme } from '../lib/theme'

/** Human labels for the theme values; the rest are shown capitalised as-is. */
const LABELS: Partial<Record<Theme, string>> = {
  system: 'System',
}

/**
 * Theme control for the whole app.
 *
 * One `data-theme` on <html> styles every page, the maintenance screen and the
 * docs, so this is the only place a theme is chosen. It reads the stored value
 * on first render rather than in an effect, so it never disagrees with the
 * attribute the inline script in index.html already set.
 */
export default function ThemePicker() {
  const [theme, setTheme] = useState<Theme>(readTheme)

  function choose(next: Theme) {
    setTheme(next)
    applyTheme(next)
  }

  return (
    <div className="dropdown dropdown-end">
      <div tabIndex={0} role="button" className="btn btn-ghost btn-sm" aria-label="Change theme">
        Theme
        <svg
          width="12" height="12" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="3" aria-hidden="true"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </div>
      <ul
        tabIndex={0}
        className="dropdown-content menu bg-base-200 rounded-box z-20 mt-2 w-44 p-2 shadow"
      >
        {THEMES.map((t) => (
          <li key={t}>
            <button
              type="button"
              className={t === theme ? 'active' : ''}
              onClick={() => choose(t)}
            >
              {LABELS[t] ?? t[0].toUpperCase() + t.slice(1)}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
