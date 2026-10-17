import { useState, useEffect, useCallback } from 'react'
import { api } from '../api.js'
import { formatPrice } from '../format.js'
import { PageHeader, ErrorNotice, ScoreBar, EmptyState } from '../components/Ui.jsx'

// One column checks out an eligible product, the other a blocked one, so
// the fail-closed gate is visible in a single side-by-side glance — this
// screen IS the "enforced, not just scored" headline demo moment.
function CheckoutColumn({ title, tone, products, loading }) {
  const [sku, setSku] = useState('')
  const [quantity, setQuantity] = useState(1)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!sku && products.length > 0) setSku(products[0].sku)
  }, [products, sku])

  async function handleCheckout() {
    setError(null)
    setResult(null)
    setBusy(true)
    try {
      const data = await api.checkout(sku, Number(quantity) || 1)
      setResult(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const selected = products.find((p) => p.sku === sku)

  return (
    <section className={`card gate-col gate-col-${tone}`}>
      <div className="gate-col-head">
        <h3>{title}</h3>
        <span>{loading ? '' : `${products.length} available`}</span>
      </div>

      {loading ? (
        <p>Loading products…</p>
      ) : products.length === 0 ? (
        <EmptyState title="No products in this group">Ingest some catalog rows first.</EmptyState>
      ) : (
        <>
          <label className="field">
            <span>Product</span>
            <select value={sku} onChange={(e) => setSku(e.target.value)}>
              {products.map((p) => (
                <option key={p.sku} value={p.sku}>
                  {p.name} ({p.sku}) — {formatPrice(p.price, p.currency, p.currency_inferred)}
                </option>
              ))}
            </select>
          </label>
          {selected && <ScoreBar score={selected.readiness_score} large />}
          <div className="qty-row">
            <label className="field">
              <span>Quantity</span>
              <input
                type="number"
                min="1"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
              />
            </label>
            <button type="button" className="btn" onClick={handleCheckout} disabled={busy}>
              {busy ? 'Checking out…' : 'Check out'}
            </button>
          </div>
        </>
      )}

      <ErrorNotice>{error}</ErrorNotice>

      {result && (
        <div
          className={`verdict ${result.success ? 'verdict-pass' : 'verdict-block'}`}
          role="status"
        >
          <span className="verdict-word">{result.success ? 'Passed' : 'Blocked'}</span>
          <p>
            {result.success
              ? `${result.product_name} — quantity ${result.quantity}`
              : result.reason}
          </p>
          <p>
            Readiness {result.readiness_score ?? '—'} against a threshold of {result.threshold}
          </p>
        </div>
      )}
    </section>
  )
}

export default function CheckoutPage() {
  const [products, setProducts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api
      .getProducts(200)
      .then((data) => setProducts(data.products))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const eligible = products.filter((p) => p.gate_status === 'eligible')
  const blocked = products.filter((p) => p.gate_status === 'blocked')

  return (
    <div className="page">
      <PageHeader
        title="Checkout gate"
        intro="Check out one product from each side. The eligible one passes; the blocked one is rejected by the same real endpoint, and both attempts are logged."
      >
        <button type="button" className="btn btn-quiet" onClick={load} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh products'}
        </button>
      </PageHeader>

      <ErrorNotice>{error}</ErrorNotice>

      <div className="gate-grid">
        <CheckoutColumn title="Eligible product" tone="clear" products={eligible} loading={loading} />
        <CheckoutColumn title="Blocked product" tone="block" products={blocked} loading={loading} />
      </div>
    </div>
  )
}
