import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { error: Error | null }

/**
 * Catches a render crash in a page and shows it, instead of React unmounting
 * the tree and leaving a white screen.
 *
 * This exists because that is exactly what happened: one model row arrived
 * without a cache key, `cacheKey.slice()` threw, and the Models page rendered
 * as nothing at all — no error, no clue, nowhere to click. A blank page is the
 * worst possible failure report. It sits inside the layout, so the navbar
 * survives and the user can leave the broken page.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Page crashed:', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="alert alert-error flex-col items-start gap-2">
        <h2 className="font-semibold">This page failed to render.</h2>
        <p className="text-sm opacity-80">
          The data loaded, but something in it broke the page. The details are in the browser
          console.
        </p>
        <pre className="text-xs whitespace-pre-wrap font-mono opacity-90">{error.message}</pre>
        <button className="btn btn-sm" onClick={() => this.setState({ error: null })}>
          Try again
        </button>
      </div>
    )
  }
}
