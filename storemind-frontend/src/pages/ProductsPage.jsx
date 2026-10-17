import { useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '../api.js'
import {
  formatPrice,
  tierShortLabel,
  tierPillClassName,
  acpTierTooltip,
  brandSourceTooltip,
  imageSourceTooltip,
  ucpTierShortLabel,
  ucpTierPillClassName,
  ucpTierTooltip,
  ondcLabel,
  ondcPillClassName,
  ondcTooltip,
  sellerVerificationLabel,
  sellerVerificationTooltip,
  sellerVerificationClassName,
} from '../format.js'
import { PageHeader, Explainer, ErrorNotice, Stat, ScoreBar, GatePill, EmptyState } from '../components/Ui.jsx'

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'eligible', label: 'Eligible' },
  { id: 'blocked', label: 'Blocked' },
]

export default function ProductsPage() {
  const [products, setProducts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')

  // Seller identity is instance-level state, fetched independently so a
  // failure in one call never blocks the other from rendering.
  const [seller, setSeller] = useState(null)
  const [sellerError, setSellerError] = useState(null)

  const loadSeller = useCallback(() => {
    setSellerError(null)
    api
      .getSeller()
      .then((data) => setSeller(data))
      .catch((err) => setSellerError(err.message))
  }, [])

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api
      .getProducts(100)
      .then((data) => setProducts(data.products))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
    loadSeller()
  }, [loadSeller])

  useEffect(() => {
    load()
  }, [load])

  const counts = useMemo(
    () => ({
      all: products.length,
      eligible: products.filter((p) => p.gate_status !== 'blocked').length,
      blocked: products.filter((p) => p.gate_status === 'blocked').length,
    }),
    [products]
  )

  const summary = useMemo(
    () => ({
      acp: products.filter((p) => p.acp_tier && p.acp_tier !== 'tier_3_discovery').length,
      ucp: products.filter((p) => p.ucp_tier === 'checkout_ready').length,
      ondc: products.filter((p) => p.is_ondc_listable).length,
    }),
    [products]
  )

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    return products.filter((p) => {
      if (filter === 'eligible' && p.gate_status === 'blocked') return false
      if (filter === 'blocked' && p.gate_status !== 'blocked') return false
      if (!q) return true
      return [p.name, p.sku, p.brand, p.category].some((v) => v && String(v).toLowerCase().includes(q))
    })
  }, [products, search, filter])

  return (
    <div className="page">
      <PageHeader
        title="Products"
        intro="Every product in the store with its readiness score and its eligibility under ACP, UCP and ONDC. Hover a badge to see why."
      >
        <button type="button" className="btn btn-quiet" onClick={load} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </PageHeader>

      <ErrorNotice>{error}</ErrorNotice>

      <div className="stats">
        <Stat value={counts.all} label="Products in store" />
        <Stat value={counts.eligible} label="Pass the readiness gate" tone="good" />
        <Stat value={counts.blocked} label="Blocked by the gate" tone={counts.blocked ? 'bad' : undefined} />
        <Stat value={summary.acp} label="ACP Tier 1 or 2 (checkout-capable)" />
        <Stat value={summary.ucp} label="UCP checkout-ready" />
        <Stat value={summary.ondc} label="ONDC ready" />
      </div>

      <div className="card seller-bar">
        <strong>Seller identity</strong>
        <span className={sellerVerificationClassName(seller)} title={sellerVerificationTooltip(seller)}>
          {sellerVerificationLabel(seller)}
        </span>
        {sellerError && <span className="status-pill status-pill-blocked">{sellerError}</span>}
      </div>

      <div className="card table-card">
        <div className="table-tools">
          <div className="chips" role="group" aria-label="Filter by gate status">
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
          <input
            type="search"
            placeholder="Search name, SKU, brand or category"
            aria-label="Search products"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {visible.length === 0 ? (
          <EmptyState title={products.length === 0 ? 'No products yet' : 'No products match'}>
            {products.length === 0
              ? loading
                ? 'Loading the catalog…'
                : 'Ingest some catalog rows to see them here.'
              : 'Try a different search or filter.'}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Category</th>
                  <th className="num">Price</th>
                  <th>Readiness</th>
                  <th>Gate</th>
                  <th>ACP</th>
                  <th>UCP</th>
                  <th>ONDC</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((p) => (
                  <tr key={p.sku} className={p.gate_status === 'blocked' ? 'row-blocked' : undefined}>
                    <td>
                      <div className="product-cell">
                        {p.image_url ? (
                          <img
                            src={p.image_url}
                            alt=""
                            className="product-thumb"
                            title={imageSourceTooltip(p)}
                          />
                        ) : (
                          <span className="product-thumb-empty" title={imageSourceTooltip(p)}>
                            No photo
                          </span>
                        )}
                        <div>
                          <div className="product-name">
                            {p.url ? (
                              <a href={p.url} target="_blank" rel="noopener noreferrer">
                                {p.name}
                              </a>
                            ) : (
                              p.name
                            )}
                          </div>
                          <div className="product-meta" title={brandSourceTooltip(p)}>
                            <span className="mono">{p.sku}</span>
                            {p.brand ? ` · ${p.brand}` : ''}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="category-cell" title={p.category}>
                      {p.category}
                    </td>
                    <td
                      className="num cell-mono"
                      title={
                        p.currency_inferred
                          ? `Currency inferred from catalog majority (${Math.round(
                              (p.currency_inference_confidence ?? 0) * 100
                            )}% confidence) — not detected from this listing's own price`
                          : undefined
                      }
                    >
                      {formatPrice(p.price, p.currency, p.currency_inferred)}
                    </td>
                    <td>
                      <ScoreBar score={p.readiness_score} />
                    </td>
                    <td>
                      <GatePill status={p.gate_status} />
                    </td>
                    <td>
                      <span className={tierPillClassName(p.acp_tier)} title={acpTierTooltip(p)}>
                        {tierShortLabel(p.acp_tier)}
                      </span>
                    </td>
                    <td>
                      <span className={ucpTierPillClassName(p.ucp_tier)} title={ucpTierTooltip(p)}>
                        {ucpTierShortLabel(p.ucp_tier)}
                      </span>
                    </td>
                    <td>
                      <span className={ondcPillClassName(p)} title={ondcTooltip(p)}>
                        {ondcLabel(p)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Explainer title="What do ACP, UCP and ONDC mean?">
        <p>
          <strong>Readiness</strong> is the gate threshold alone: a score of 70 or more passes.
        </p>
        <p>
          <strong>ACP</strong> (OpenAI) is the fuller picture. It also needs a complete,
          protocol-conformant feed record, so a product can pass readiness and still land in Tier 3,
          discovery only.
        </p>
        <p>
          <strong>UCP</strong> (Google) is checked independently, so the two can disagree. UCP
          checkout does not require a product photo the way ACP full eligibility does.
        </p>
        <p>
          <strong>ONDC</strong> is a catalog-conformance check only, with no live checkout or
          network registration. It depends on seller-wide setup (packer identity, return policy,
          consumer care contact), so it often fails until that is configured, even for a product
          that is otherwise ACP and UCP ready.
        </p>
        <p>
          The list updates only when you click Refresh. Products ingested before UCP or ONDC
          eligibility existed show no badge for them until re-ingested.
        </p>
      </Explainer>
    </div>
  )
}
