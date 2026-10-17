// Shared building blocks so every page speaks the same visual language.

// Must match the backend gate threshold (shown as "70.0" across the app).
export const GATE_THRESHOLD = 70

export function PageHeader({ title, intro, children }) {
  return (
    <div className="page-head">
      <div>
        <h2>{title}</h2>
        {intro && <p>{intro}</p>}
      </div>
      {children && <div className="page-head-actions">{children}</div>}
    </div>
  )
}

// Long reference copy lives here instead of a wall of text above the content.
export function Explainer({ title = 'How to read this page', children }) {
  return (
    <details className="explainer">
      <summary>{title}</summary>
      <div className="explainer-body">{children}</div>
    </details>
  )
}

export function ErrorNotice({ children }) {
  if (!children) return null
  return (
    <div className="notice" role="alert">
      {children}
    </div>
  )
}

export function Stat({ value, label, tone }) {
  return (
    <div className={`card stat${tone ? ` stat-${tone}` : ''}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

// Readiness score drawn against the gate threshold: the tick is the line a
// product has to cross to check out.
export function ScoreBar({ score, large = false }) {
  if (score === null || score === undefined) return <span className="cell-mono">—</span>
  const pct = Math.max(0, Math.min(100, score))
  const passes = score >= GATE_THRESHOLD
  return (
    <div
      className={`score${passes ? '' : ' score-fail'}${large ? ' score-lg' : ''}`}
      title={`Score ${score} — gate threshold is ${GATE_THRESHOLD}`}
    >
      <span className="score-num">{Math.round(score * 10) / 10}</span>
      <span className="score-track" aria-hidden="true">
        <span className="score-fill" style={{ width: `${pct}%` }} />
        <span className="score-tick" style={{ left: `${GATE_THRESHOLD}%` }} />
      </span>
    </div>
  )
}

export function GatePill({ status }) {
  if (!status) return <span className="status-pill">Unknown</span>
  const blocked = status === 'blocked'
  return (
    <span className={`status-pill ${blocked ? 'status-pill-blocked' : 'status-pill-eligible'}`}>
      {blocked ? 'Blocked' : 'Eligible'}
    </span>
  )
}

export function EmptyState({ title, children }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  )
}

const ICONS = {
  overview: 'M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10',
  ingest: 'M12 3v12m0 0l-4-4m4 4l4-4M4 17v3h16v-3',
  products: 'M4 7l8-4 8 4-8 4-8-4zm0 5l8 4 8-4M4 17l8 4 8-4',
  search: 'M11 4a7 7 0 100 14 7 7 0 000-14zm9 16l-4.5-4.5',
  checkout: 'M5 12l4.5 4.5L19 7',
  decisionLog: 'M6 4h12v16H6zM9 9h6M9 13h6M9 17h3',
  fraud: 'M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6l8-3zm0 6v4m0 3v.01',
}

export function Icon({ name }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={ICONS[name]} />
    </svg>
  )
}
