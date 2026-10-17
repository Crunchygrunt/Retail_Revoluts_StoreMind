import { useState, useEffect } from 'react'
import Nav from './components/Nav.jsx'
import OverviewPage from './pages/OverviewPage.jsx'
import IngestPage from './pages/IngestPage.jsx'
import ProductsPage from './pages/ProductsPage.jsx'
import SearchPage from './pages/SearchPage.jsx'
import CheckoutPage from './pages/CheckoutPage.jsx'
import DecisionLogPage from './pages/DecisionLogPage.jsx'
import FraudPage from './pages/FraudPage.jsx'
import { api } from './api.js'

const PAGES = {
  overview: OverviewPage,
  ingest: IngestPage,
  products: ProductsPage,
  search: SearchPage,
  checkout: CheckoutPage,
  decisionLog: DecisionLogPage,
  fraud: FraudPage,
}

export default function App() {
  const [active, setActive] = useState('overview')
  const [health, setHealth] = useState({ status: 'checking' })

  useEffect(() => {
    api
      .health()
      .then(() => setHealth({ status: 'ok' }))
      .catch((err) => setHealth({ status: 'error', message: err.message }))
  }, [])

  const ActivePage = PAGES[active]

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          StoreMind
        </div>
        <Nav active={active} onChange={setActive} />
        <div className="side-foot">
          <span className={`health health-${health.status}`} role="status">
            {health.status === 'checking' && 'Checking API…'}
            {health.status === 'ok' && 'API connected'}
            {health.status === 'error' && `API unreachable — ${health.message}`}
          </span>
        </div>
      </aside>
      <main className="main">
        <ActivePage onNavigate={setActive} />
      </main>
    </div>
  )
}
