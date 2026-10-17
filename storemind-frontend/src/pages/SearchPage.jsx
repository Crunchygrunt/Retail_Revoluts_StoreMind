import { useState } from 'react'
import { api } from '../api.js'
import { formatPrice } from '../format.js'
import { PageHeader, ErrorNotice, ScoreBar, EmptyState } from '../components/Ui.jsx'

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleSearch(e) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const data = await api.search(query, category)
      setResults(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <PageHeader
        title="Storefront search"
        intro="Run the same catalog lookup a shopping agent would. Leave both fields empty to list everything."
      />

      <form onSubmit={handleSearch} className="card search-bar">
        <label className="field">
          <span>Keyword</span>
          <input
            placeholder="e.g. cotton kurta"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Category</span>
          <input
            placeholder="e.g. Apparel"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          />
        </label>
        <button type="submit" className="btn" disabled={loading}>
          {loading ? 'Searching…' : 'Search'}
        </button>
      </form>

      <ErrorNotice>{error}</ErrorNotice>

      {results ? (
        <div className="card table-card">
          <div className="table-tools">
            <strong>
              {results.count} {results.count === 1 ? 'result' : 'results'}
            </strong>
          </div>
          {results.products.length === 0 ? (
            <EmptyState title="Nothing matched">Try a broader keyword or clear the category.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Category</th>
                    <th className="num">Price</th>
                    <th>Readiness</th>
                  </tr>
                </thead>
                <tbody>
                  {results.products.map((p) => (
                    <tr key={p.sku}>
                      <td>
                        <div className="product-name">{p.name}</div>
                        <div className="product-meta mono">{p.sku}</div>
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
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : (
        <div className="card">
          <EmptyState title="No search yet">Enter a keyword or category and press Search.</EmptyState>
        </div>
      )}
    </div>
  )
}
