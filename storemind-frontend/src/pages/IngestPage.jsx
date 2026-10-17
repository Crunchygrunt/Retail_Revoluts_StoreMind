import { useState } from 'react'
import Papa from 'papaparse'
import { api } from '../api.js'
import { PageHeader, Explainer, ErrorNotice, Stat, ScoreBar, GatePill } from '../components/Ui.jsx'

// A deliberately messy example: one well-sourced row, one thin row that
// should trigger the source_richness / fabrication-blindness guard and
// score low. Good for demoing that the gate reacts to real input quality.
const EXAMPLE_ROWS = [
  {
    name: 'Handwoven Cotton Kurta',
    description:
      'Breathable handwoven cotton kurta with block-printed detailing, available in three colors.',
    price: '899',
    category: 'Apparel',
    image_url: 'https://example.com/kurta.jpg',
  },
  {
    name: 'Item',
    description: '',
    price: '199',
    category: 'Misc',
  },
]

// Turns pasted or uploaded JSON text into a rows array. Accepts a bare
// array (what this page has always required) OR a {"rows": [...]}
// object (the shape /ingest itself takes, and the shape every file this
// page's own test sets ship in for direct curl use) -- pasting either
// one now just works, instead of silently double-wrapping the second
// form into something the backend rejects.
function parseRowsInput(text) {
  const parsed = JSON.parse(text)
  if (Array.isArray(parsed)) return parsed
  if (parsed && Array.isArray(parsed.rows)) return parsed.rows
  throw new Error('Input must be a JSON array of row objects (or a {"rows": [...]} object)')
}

// CSV -> the same plain-object row shape the JSON path already produces,
// so everything downstream (preview, edit, Ingest) is one path regardless
// of how the rows arrived. Column headers are expected to match the raw
// row fields this app already uses -- name, description, price, category,
// brand, url, image_url, sku, availability -- since that's what Layer-0
// and the catalog agents actually read; a header naming scheme beyond
// that isn't remapped.
function parseCsv(text) {
  const result = Papa.parse(text, { header: true, skipEmptyLines: true })
  if (result.errors.length > 0) {
    const first = result.errors[0]
    throw new Error(`CSV parse error (row ${first.row}): ${first.message}`)
  }
  // Drop blank cells rather than sending "" for every unfilled optional
  // column -- a seller's CSV is realistically sparse (most rows won't
  // have every column filled), and the backend already treats a missing
  // key and an empty string the same way for optional fields, so this
  // is purely about keeping the preview readable, not changing behavior.
  return result.data.map((row) => {
    const cleaned = {}
    for (const [key, value] of Object.entries(row)) {
      if (typeof value === 'string' && value.trim() === '') continue
      cleaned[key] = value
    }
    return cleaned
  })
}

// -- reflexion (added) --------------------------------------------------
// Renders the per-row "reflexion" block /ingest now returns alongside
// the existing readiness/ACP/UCP/ONDC fields -- see reflexion.py /
// reflexion_evaluator.py on the backend. A row that passed on its first
// attempt has nothing interesting to show here (stopped_reason "passed"
// with attempts === 1), so this deliberately renders as a quiet "1st
// try" rather than a full block for the common case -- the point of
// this cell is to make a RETRY visible, not to pad every row with
// machinery that didn't run.
//
// No new CSS classes assumed from styles.css (not available to write
// against here) -- kept self-contained with inline styles in the same
// spirit as this file's own existing `style={{ fontSize: 14 }}` usage
// a few lines below. Move these into styles.css later if you want
// matching hover/dark-mode treatment with the rest of the page.
function ReflexionCell({ reflexion }) {
  if (!reflexion) return <span className="hint">—</span>

  const { attempts, retried, improved, stopped_reason, score_before, score_after, fabrication_blocked, reflection_notes } =
    reflexion

  if (!retried) {
    return <span className="hint">1st try</span>
  }

  const scoreChange = score_before != null && score_after != null ? `${score_before} → ${score_after}` : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13, minWidth: 160 }}>
      <span style={{ fontWeight: 600, color: improved ? '#0a7a3d' : '#6b6b6b' }}>
        ↻ {attempts} attempt{attempts === 1 ? '' : 's'}
        {improved ? ' · improved' : ''}
      </span>
      {scoreChange && <span>{scoreChange}</span>}
      <span className="hint">{stopped_reason.replace(/_/g, ' ')}</span>
      {fabrication_blocked.length > 0 && (
        <span style={{ color: '#b42318' }} title="A retry proposed this value with no support in the raw source text — rejected, not trusted.">
          blocked: {fabrication_blocked.join(', ')}
        </span>
      )}
      {reflection_notes.length > 0 && (
        <details>
          <summary style={{ cursor: 'pointer', color: '#6b6b6b' }}>reflection notes</summary>
          {reflection_notes.map((note, i) => (
            <pre key={i} className="cell-wrap" style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: '4px 0' }}>
              {note}
            </pre>
          ))}
        </details>
      )}
    </div>
  )
}

export default function IngestPage() {
  const [rowsText, setRowsText] = useState(JSON.stringify(EXAMPLE_ROWS, null, 2))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [fileName, setFileName] = useState(null)

  async function handleFileUpload(event) {
    const file = event.target.files?.[0]
    // Always clear the input value, even on failure -- otherwise
    // re-selecting the SAME file after fixing it does nothing, since
    // the browser only fires onChange when the selection changes.
    event.target.value = ''
    if (!file) return

    setError(null)
    setResult(null)
    setFileName(file.name)

    const isCsv = file.name.toLowerCase().endsWith('.csv')
    const isJson = file.name.toLowerCase().endsWith('.json')
    const isXlsx = file.name.toLowerCase().endsWith('.xlsx')
    if (!isCsv && !isJson && !isXlsx) {
      setError('Unsupported file type — upload a .csv, .json, or .xlsx file.')
      setFileName(null)
      return
    }

    // .xlsx is binary, not text -- parsed server-side (see api.js /
    // main.py's /parse/xlsx and xlsx_ingest.py for why this one path
    // isn't client-side like the other two). Send the raw File object;
    // don't read it as text first, that would corrupt the binary data.
    if (isXlsx) {
      try {
        const data = await api.parseXlsx(file)
        setRowsText(JSON.stringify(data.rows, null, 2))
      } catch (err) {
        setError(`Could not parse ${file.name}: ${err.message}`)
        setFileName(null)
      }
      return
    }

    let text
    try {
      text = await file.text()
    } catch (err) {
      setError(`Could not read the file: ${err.message}`)
      setFileName(null)
      return
    }

    try {
      const rows = isCsv ? parseCsv(text) : parseRowsInput(text)
      // Populated into the SAME textarea the paste flow uses, not
      // ingested directly -- one review step, one "Ingest" action,
      // regardless of how the rows arrived. Lets a CSV-parsing surprise
      // (a misnamed column, a stray blank row) get caught by eye before
      // it becomes a batch of Layer-0 rejections.
      setRowsText(JSON.stringify(rows, null, 2))
    } catch (err) {
      setError(`Could not parse ${file.name}: ${err.message}`)
      setFileName(null)
    }
  }

  async function handleIngest() {
    setError(null)
    setResult(null)

    let rows
    try {
      rows = parseRowsInput(rowsText)
    } catch (err) {
      setError(`Invalid JSON: ${err.message}`)
      return
    }

    setLoading(true)
    try {
      const data = await api.ingest(rows)
      setResult(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }


  const rejected = result ? result.layer0_rejected : 0
  // -- reflexion (added) -- how many rows the retry loop actually
  // improved, for the stats row. Only counts rows where reflexion ran
  // AND paid off, not every row that merely retried (a retry that
  // never beat the first attempt -- e.g. stopped_reason "max_retries"
  // with no improvement -- is not a win to advertise).
  const improvedByReflexion = result
    ? result.structuring_results.filter((r) => r.reflexion?.improved).length
    : 0

  return (
    <div className="page">
      <PageHeader
        title="Ingest"
        intro="Add raw catalog rows, review them, then run them through validation, structuring and readiness scoring."
      />

      <div className="steps">
        <section className="card card-pad">
          <div className="step-head">
            <span className="step-num">1</span>
            <h3>Add your rows</h3>
            <span className="hint">JSON, .csv or .xlsx</span>
          </div>
          <textarea
            rows={12}
            value={rowsText}
            aria-label="Catalog rows as JSON"
            onChange={(e) => setRowsText(e.target.value)}
          />
          <div className="button-row">
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => setRowsText(JSON.stringify(EXAMPLE_ROWS, null, 2))}
            >
              Load example
            </button>
            <label className="btn btn-quiet btn-file">
              {fileName ? `Loaded: ${fileName}` : 'Upload a file'}
              <input
                type="file"
                accept=".csv,.json,.xlsx,text/csv,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={handleFileUpload}
              />
            </label>
          </div>
          <Explainer title="Accepted fields and the example rows">
            <p>
              A CSV or Excel header row should use these field names: name, description, price,
              category, brand, url, image_url, sku, availability. Uploading only fills the box
              above, so check it before you ingest.
            </p>
            <p>
              The example has two rows: one well-described product, and one thin listing that the
              readiness score should catch.
            </p>
          </Explainer>
        </section>

        <section className="card card-pad">
          <div className="step-head">
            <span className="step-num">2</span>
            <h3>Run the pipeline</h3>
          </div>
          <p style={{ fontSize: 14 }}>
            Checks each row (Layer-0 validation), structures it with the Catalog Structuring Agent
            — retrying itself, with a specific correction, when it can fix its own miss without
            guessing — then scores readiness.
          </p>
          <div className="button-row">
            <button type="button" className="btn" onClick={handleIngest} disabled={loading}>
              {loading ? 'Ingesting…' : 'Ingest rows'}
            </button>
          </div>
          <div style={{ marginTop: 12 }}>
            <ErrorNotice>{error}</ErrorNotice>
          </div>
        </section>

        {result && (
          <section className="page" style={{ gap: 16 }}>
            <div className="step-head" style={{ marginBottom: 0 }}>
              <span className="step-num">3</span>
              <h3>What happened to each row</h3>
              <span className="hint">
                {result.llm_backend} / {result.store_backend}
              </span>
            </div>

            <div className="stats">
              <Stat value={result.submitted} label="Rows submitted" />
              <Stat value={result.layer0_accepted} label="Passed validation" tone="good" />
              <Stat value={rejected} label="Rejected by validation" tone={rejected ? 'bad' : undefined} />
              <Stat
                value={improvedByReflexion}
                label="Improved by reflexion"
                tone={improvedByReflexion ? 'good' : undefined}
              />
            </div>

            {result.rejections.length > 0 && (
              <div className="card card-pad">
                <h3 className="card-title">Rejected rows</h3>
                <ul className="rejection-list">
                  {result.rejections.map((r) => (
                    <li key={r.row_id}>
                      <strong>{r.row_id}</strong>: {r.reasons.join('; ')}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="card table-card">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Row</th>
                      <th>Structured</th>
                      <th>Method</th>
                      <th>Readiness</th>
                      <th>Gate</th>
                      <th>Reflexion</th>
                      <th>Warnings</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.structuring_results.map((r) => (
                      <tr
                        key={r.row_id}
                        className={
                          r.meets_readiness_threshold === false
                            ? 'row-blocked'
                            : r.meets_readiness_threshold
                            ? 'row-clear'
                            : undefined
                        }
                      >
                        <td className="cell-mono">{r.row_id}</td>
                        <td>{r.success ? 'Yes' : 'No'}</td>
                        <td className="cell-mono">{r.extraction_method}</td>
                        <td>
                          <ScoreBar score={r.readiness_score} />
                        </td>
                        <td>
                          {r.meets_readiness_threshold === null ? (
                            <span className="status-pill">Not scored</span>
                          ) : (
                            <GatePill status={r.meets_readiness_threshold ? 'eligible' : 'blocked'} />
                          )}
                        </td>
                        <td>
                          <ReflexionCell reflexion={r.reflexion} />
                        </td>
                        <td className="cell-wrap">{r.warnings.join('; ') || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
