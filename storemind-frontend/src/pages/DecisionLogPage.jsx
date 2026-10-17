import { useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '../api.js'
import { PageHeader, ErrorNotice, EmptyState, Stat } from '../components/Ui.jsx'

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'blocked', label: 'Blocked' },
  { id: 'allowed', label: 'Allowed' },
]

export default function DecisionLogPage() {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('all')

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api
      .decisionLog(200)
      .then((data) => setEntries(data.entries))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // Newest first.
  const ordered = useMemo(() => entries.slice().reverse(), [entries])
  const isBlocked = (e) => e.event_type === 'gate_blocked'
  const blockedCount = ordered.filter(isBlocked).length
  const counts = { all: ordered.length, blocked: blockedCount, allowed: ordered.length - blockedCount }
  const visible = ordered.filter((e) =>
    filter === 'all' ? true : filter === 'blocked' ? isBlocked(e) : !isBlocked(e)
  )

  return (
    <div className="page">
      <PageHeader
        title="Decision log"
        intro="Every gate decision with its reason: the evidence behind “enforced, not just scored.” Each checkout attempt adds a row."
      >
        <button type="button" className="btn btn-quiet" onClick={load} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </PageHeader>

      <ErrorNotice>{error}</ErrorNotice>

      <div className="stats">
        <Stat value={counts.all} label="Decisions logged" />
        <Stat value={counts.allowed} label="Allowed" tone="good" />
        <Stat value={counts.blocked} label="Blocked" tone={counts.blocked ? 'bad' : undefined} />
      </div>

      <div className="card table-card">
        <div className="table-tools">
          <div className="chips" role="group" aria-label="Filter decisions">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                className="chip"
                aria-pressed={filter === f.id}
                onClick={() => setFilter(f.id)}
              >
                {f.label}
                <span className="count">{counts[f.id]}</span>
              </button>
            ))}
          </div>
        </div>
        {visible.length === 0 ? (
          <EmptyState title="No decisions yet">
            Check out a product on the Checkout page and the decision shows up here.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>SKU</th>
                  <th>Decision</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((e, i) => (
                  <tr key={i} className={isBlocked(e) ? 'row-blocked' : 'row-clear'}>
                    <td className="cell-mono" style={{ whiteSpace: 'nowrap' }}>
                      {e.timestamp}
                    </td>
                    <td className="cell-mono">{e.product_sku}</td>
                    <td>
                      <span
                        className={`status-pill ${
                          isBlocked(e) ? 'status-pill-blocked' : 'status-pill-eligible'
                        }`}
                        title={e.event_type}
                      >
                        {isBlocked(e) ? 'Blocked' : 'Allowed'}
                      </span>
                    </td>
                    <td className="cell-wrap">{e.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
