import { NavLink, Outlet, useLocation } from 'react-router-dom'
import ErrorBoundary from './ErrorBoundary'
import ThemePicker from './ThemePicker'

const links = [
  { to: '/sources', label: 'Sources' },
  { to: '/instruments', label: 'Instruments' },
  { to: '/imports', label: 'Imports' },
  { to: '/train', label: 'Train' },
  { to: '/run', label: 'Run' },
  { to: '/results', label: 'Results' },
  { to: '/models', label: 'Models' },
  { to: '/docs', label: 'Docs' },
]

export default function Layout() {
  const location = useLocation()

  return (
    <div className="min-h-screen bg-base-100">
      <div className="navbar bg-base-200 shadow-sm sticky top-0 z-10">
        <div className="flex-1">
          <NavLink to="/" className="btn btn-ghost text-xl">backtest</NavLink>
        </div>
        <div className="flex-none flex items-center gap-1">
          <ul className="menu menu-horizontal px-1 gap-1">
            {links.map(({ to, label }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  className={({ isActive }) => (isActive ? 'active' : '')}
                >
                  {label}
                </NavLink>
              </li>
            ))}
          </ul>
          <ThemePicker />
        </div>
      </div>
      <main className="max-w-7xl mx-auto p-6">
        {/* Inside the layout on purpose: a page that throws should not take
            the navigation with it. Keyed by route so navigating away clears the
            error rather than carrying it to the next page. */}
        <ErrorBoundary key={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  )
}
