import { Link } from 'react-router-dom'

const cards = [
  { to: '/sources', title: 'Sources', desc: 'Data providers known to the system (yahoo, alpha-vantage, etc.).' },
  { to: '/instruments', title: 'Instruments', desc: 'Symbols loaded into the database with per-source candle counts and date ranges.' },
  { to: '/imports', title: 'Imports', desc: 'Audit log of every CSV import: which file, which source, when, how many rows.' },
  { to: '/results', title: 'Results', desc: 'Saved backtest runs. Open one to see metrics, trades, and the equity curve.' },
  { to: '/models', title: 'Models', desc: 'Trained NN models on disk: hyperparameters, validation accuracy, and which backtests reused them.' },
]

export default function HomePage() {
  return (
    <>
      <div className="hero bg-base-200 rounded-box mb-6">
        <div className="hero-content text-center py-10">
          <div className="max-w-xl">
            <h1 className="text-4xl font-bold">backtest</h1>
            <p className="py-3 text-base-content/70">
              Read-only view of the Java backtesting app: candle data, import history, and saved results.
            </p>
          </div>
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {cards.map((c) => (
          <Link key={c.to} to={c.to} className="card bg-base-200 hover:bg-base-300 transition">
            <div className="card-body">
              <h2 className="card-title">{c.title}</h2>
              <p>{c.desc}</p>
            </div>
          </Link>
        ))}
      </div>
    </>
  )
}
