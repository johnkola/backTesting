import { NavLink, Outlet } from 'react-router-dom'

const links = [
  { to: '/sources', label: 'Sources' },
  { to: '/instruments', label: 'Instruments' },
  { to: '/imports', label: 'Imports' },
  { to: '/results', label: 'Results' },
  { to: '/models', label: 'Models' },
]

export default function Layout() {
  return (
    <div className="min-h-screen bg-base-100">
      <div className="navbar bg-base-200 shadow-sm sticky top-0 z-10">
        <div className="flex-1">
          <NavLink to="/" className="btn btn-ghost text-xl">backtest</NavLink>
        </div>
        <div className="flex-none">
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
            <li>
              <a href="/readme" target="_blank" rel="noopener noreferrer">Docs</a>
            </li>
          </ul>
        </div>
      </div>
      <main className="max-w-7xl mx-auto p-6">
        <Outlet />
      </main>
    </div>
  )
}
