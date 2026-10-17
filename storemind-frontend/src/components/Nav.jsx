import { Icon } from './Ui.jsx'

const GROUPS = [
  {
    heading: 'Catalog',
    tabs: [
      { id: 'overview', label: 'Overview' },
      { id: 'ingest', label: 'Ingest' },
      { id: 'products', label: 'Products' },
      { id: 'search', label: 'Search' },
    ],
  },
  {
    heading: 'Enforcement',
    tabs: [
      { id: 'checkout', label: 'Checkout' },
      { id: 'decisionLog', label: 'Decision log' },
      { id: 'fraud', label: 'Fraud detector' },
    ],
  },
]

export default function Nav({ active, onChange }) {
  return (
    <nav className="nav" aria-label="Main">
      {GROUPS.map((group) => (
        <div className="nav-group" key={group.heading}>
          <div className="nav-heading">{group.heading}</div>
          {group.tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={tab.id === active ? 'nav-btn active' : 'nav-btn'}
              aria-current={tab.id === active ? 'page' : undefined}
              onClick={() => onChange(tab.id)}
            >
              <Icon name={tab.id} />
              {tab.label}
            </button>
          ))}
        </div>
      ))}
    </nav>
  )
}
