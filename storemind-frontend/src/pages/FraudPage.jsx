import { useState } from 'react'
import { api } from '../api.js'
import { PageHeader, ErrorNotice, Stat, EmptyState } from '../components/Ui.jsx'

const RISK_PILL = {
  high: 'status-pill status-pill-blocked',
  medium: 'status-pill',
  low: 'status-pill status-pill-eligible',
}

export default function FraudPage() {
  const [harness, setHarness] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function runHarness() {
    setLoading(true)
    setError(null)
    try {
      const data = await api.fraudTestHarness()
      setHarness(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const pct = harness && harness.accuracy !== null ? Math.round(harness.accuracy * 100) : null

  return (
    <div className="page">
      <PageHeader
        title="Fraud signal detector"
        intro="Runs 9 labeled scenarios (5 returns, 4 Cash-on-Delivery) and scores the detector against ground truth. One flagged example proves nothing; the accuracy readout is the evidence."
      >
        <button type="button" className="btn" onClick={runHarness} disabled={loading}>
          {loading ? 'Running…' : harness ? 'Run again' : 'Run test harness'}
        </button>
      </PageHeader>

      <ErrorNotice>{error}</ErrorNotice>

      {!harness ? (
        <div className="card">
          <EmptyState title="No results yet">
            Run the test harness to see how each scenario was scored.
          </EmptyState>
        </div>
      ) : (
        <>
          <div className="card accuracy-panel">
            <div>
              <div className="accuracy-big">{pct !== null ? `${pct}%` : '—'}</div>
              <p style={{ marginTop: 6, fontSize: 14 }}>
                {harness.correct} of {harness.scored} correct · backend {harness.backend}
              </p>
            </div>
          </div>

          <div className="stats">
            <Stat value={harness.scored} label="Scenarios scored" />
            <Stat value={harness.correct} label="Correct" tone="good" />
            <Stat
              value={harness.scored - harness.correct}
              label="Incorrect"
              tone={harness.scored - harness.correct ? 'bad' : undefined}
            />
          </div>

          <div className="card table-card">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Type</th>
                    <th>Risk</th>
                    <th>Flagged</th>
                    <th>Expected</th>
                    <th>Result</th>
                    <th>Reasoning</th>
                    <th>Signals</th>
                  </tr>
                </thead>
                <tbody>
                  {harness.results.map((r) => (
                    <tr key={r.return_id} className={r.correct ? 'row-clear' : 'row-blocked'}>
                      <td className="cell-mono">{r.return_id}</td>
                      <td>{r.scenario_type === 'cod_refusal' ? 'COD' : 'Return'}</td>
                      <td>
                        <span className={RISK_PILL[r.risk_level] || 'status-pill'}>{r.risk_level}</span>
                      </td>
                      <td>{r.flagged ? 'Yes' : 'No'}</td>
                      <td>{r.ground_truth_flag === null ? '—' : r.ground_truth_flag ? 'Yes' : 'No'}</td>
                      <td>
                        <span
                          className={`status-pill ${
                            r.correct ? 'status-pill-eligible' : 'status-pill-blocked'
                          }`}
                        >
                          {r.correct ? 'Correct' : 'Wrong'}
                        </span>
                      </td>
                      <td className="cell-wrap">{r.reasoning}</td>
                      <td>
                        <div className="tag-list">
                          {r.signals.length === 0 ? '—' : r.signals.map((s, i) => (
                            <span className="tag" key={i}>
                              {s}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
