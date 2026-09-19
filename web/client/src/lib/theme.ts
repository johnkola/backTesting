/**
 * The app's theme, stored once and applied to the whole document.
 *
 * daisyUI themes are driven by `data-theme` on <html>, so every page, the
 * maintenance screen and the rendered docs all follow this one attribute —
 * there is no per-page styling to keep in sync. The docs used to be a separate
 * server-rendered app pinned to `corporate`, which is most of why they looked
 * like a different product.
 */

export const THEMES = [
  'system',
  'light',
  'dark',
  'corporate',
  'business',
  'emerald',
  'night',
  'dracula',
  'nord',
] as const

export type Theme = (typeof THEMES)[number]

const STORAGE_KEY = 'backtest.theme'

/** The stored choice, or 'system' when there is none (or storage is blocked). */
export function readTheme(): Theme {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return (THEMES as readonly string[]).includes(raw ?? '') ? (raw as Theme) : 'system'
  } catch {
    return 'system'
  }
}

/**
 * Applies a theme to the document and remembers it.
 *
 * 'system' removes the attribute rather than picking a colour, which hands the
 * decision back to daisyUI's `--prefersdark` and keeps the page in step if the
 * OS flips while it is open.
 */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement
  if (theme === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', theme)

  try {
    if (theme === 'system') localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Private mode or blocked storage: the theme still applies for this visit.
  }
}
