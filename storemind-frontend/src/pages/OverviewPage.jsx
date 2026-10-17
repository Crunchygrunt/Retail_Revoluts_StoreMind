import { useState } from 'react'
import { api } from '../api.js'
import { Icon, ErrorNotice } from '../components/Ui.jsx'

const UCP_SERVER = 'https://storemind-ingest-444891828026.us-central1.run.app'

const PIPELINE = [
  { title: 'Ingest', text: 'Messy spreadsheets and photo listings come in.' },
  { title: 'Structure and score', text: 'Each product is cleaned up and given a readiness score.' },
  { title: 'Gate', text: 'Products under the threshold cannot check out.', gate: true },
  { title: 'Decision log', text: 'Every allow or block is recorded with its reason.' },
]

const PROOF = [
  { number: '483/483', label: 'Unit tests passing' },
  { number: '50/50', label: 'Live Vertex AI calls on a 73-row stress test' },
  { number: '9/9', label: 'Fraud scenarios correct, using real Gemini' },
  { number: '70.0', label: 'Readiness score needed to pass the gate' },
]

const MODULES = [
  {
    id: 'ingest',
    title: 'Catalog structuring',
    text: 'Messy spreadsheets and photo listings in, a structured agent-ready catalog out. A no-LLM validator and a 3-layer fallback parser protect against bad input and bad model output.',
    cta: 'Open Ingest',
  },
  {
    id: 'search',
    title: 'Agent-facing storefront',
    text: 'A simulated shopping agent searches and transacts against the catalog in real time. Live proof, not a static checklist.',
    cta: 'Open Search',
  },
  {
    id: 'checkout',
    title: 'Fail-closed readiness gate',
    text: 'Below-threshold or feed-incomplete products are blocked from checkout, and every decision is logged with its reason.',
    cta: 'Open Checkout',
  },
  {
    id: 'products',
    title: 'Agent commerce readiness',
    text: 'Fills the fields ACP and UCP require (brand, product page, hosted image) without inventing anything. A product missing only a photo stays searchable and gets one specific fix.',
    cta: 'Open Products',
  },
  {
    id: 'fraud',
    title: 'Fraud signal detector',
    text: 'Screens returns for wardrobing, bracketing and vague reasons, and Cash-on-Delivery orders for refusal patterns. Tested on 9 labeled scenarios.',
    cta: 'Open Fraud detector',
  },
]

export default function OverviewPage({ onNavigate }) {
  const [question, setQuestion] = useState('')
  // Frozen at submit time, separate from the live input: the transcript must
  // show what was actually asked, not follow whatever is typed next.
  const [askedQuestion, setAskedQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [mcpResult, setMcpResult] = useState(null)
  const [mcpError, setMcpError] = useState(null)

  const askCatalog = (e) => {
    e.preventDefault()
    const trimmed = question.trim()
    if (!trimmed || asking) return
    setAskedQuestion(trimmed)
    setQuestion('')
    setAsking(true)
    setMcpError(null)
    setMcpResult(null)
    api
      .askCatalog(trimmed)
      .then((data) => setMcpResult(data))
      .catch((err) => setMcpError(err.message))
      .finally(() => setAsking(false))
  }

  const serverParam = encodeURIComponent(UCP_SERVER)

  return (
    <div className="page" style={{ gap: 44 }}>
      <section className="hero">
        <div>
          <h1>Enforced, not just scored.</h1>
          <p className="hero-sub">
            StoreMind turns messy product catalogs into an agent-ready storefront for SME and D2C
            retailers, then backs the readiness score with a gate that blocks unready products at
            checkout instead of leaving it to a dashboard number.
          </p>
          <div className="hero-actions">
            <button type="button" className="btn btn-lg" onClick={() => onNavigate('ingest')}>
              Start the demo
            </button>
            <button
              type="button"
              className="btn btn-quiet btn-lg"
              onClick={() => onNavigate('checkout')}
            >
              See the gate in action
            </button>
          </div>
        </div>
        <ol className="pipeline" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {PIPELINE.map((step, i) => (
            <li key={step.title} className={`pipe-step${step.gate ? ' pipe-gate' : ''}`}>
              <span className="pipe-dot">{i + 1}</span>
              <div>
                <h3>{step.title}</h3>
                <p>{step.text}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section>
        <div className="section-head">
          <h2>Verified results</h2>
        </div>
        <div className="proof-grid">
          {PROOF.map((p) => (
            <div className="card proof" key={p.label}>
              <span className="proof-number">{p.number}</span>
              <span className="proof-label">{p.label}</span>
            </div>
          ))}
          <div className="card proof">
            <span className="proof-number">42/43</span>
            <span className="proof-label">UCP conformance checks passing, independently audited</span>
            <a
              className="proof-badge"
              href={`https://spck.dev/check?server=${serverParam}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              <img
                src={`https://spck.dev/api/badge?server=${serverParam}`}
                alt="UCP conformance badge"
              />
            </a>
            <span className="proof-note">
              The one open check is TLS 1.3 transport, a Cloud Run platform default rather than
              application code.
            </span>
          </div>
        </div>
      </section>

      <section>
        <div className="section-head">
          <h2>Five modules, one dataset</h2>
          <p>Pick one to open it.</p>
        </div>
        <div className="module-grid">
          {MODULES.map((m) => (
            <button
              key={m.id}
              type="button"
              className="card module"
              onClick={() => onNavigate(m.id)}
            >
              <Icon name={m.id} />
              <h3>{m.title}</h3>
              <p>{m.text}</p>
              <span className="go">{m.cta} →</span>
            </button>
          ))}
        </div>
      </section>

      <section>
        <div className="section-head">
          <h2>Why this exists</h2>
        </div>
        <div className="split">
          <div className="card card-pad">
            <h3 className="card-title">The problem</h3>
            <p>
              AI shopping agents are starting to buy directly from retailers: ChatGPT's Instant
              Checkout on OpenAI's Agentic Commerce Protocol (ACP), and Google's Universal
              Commerce Protocol (UCP), launched with Shopify and Walmart. To be reachable, a
              catalog must satisfy a machine-readable spec: a stable product page, a real image, a
              brand, a seller identity, a stock status. A missing or malformed field can get the
              listing rejected outright.
            </p>
            <p>
              Large platforms hand this to their merchants for free. Left out are sellers without a
              platform: informal, WhatsApp- and Instagram-first D2C and SME sellers, common across
              South Asia, whose catalog is a spreadsheet and a folder of phone photos. Neither
              protocol serves this region yet, so the goal is to be ready before it does.
            </p>
          </div>
          <div className="card card-pad">
            <h3 className="card-title">Why not another readiness score</h3>
            <p>
              Readiness scorers are already commoditized, and platforms like Shopify build this in
              for their own merchants. None of them close the gaps they find; they report a number
              and leave the rest to the retailer.
            </p>
            <p>
              StoreMind is built for informal, platform-less, bilingual, long-tail sellers, and it
              resolves what it safely can: a store's own name in place of a missing brand, a
              generated product page in place of a missing URL, a hosted image in place of an
              unlinked photo. What is left, almost always a genuinely missing photo, is reported
              with the one fix that would close it.
            </p>
          </div>
        </div>
      </section>

      <section>
        <div className="section-head">
          <h2>Agents can shop this catalog directly</h2>
        </div>
        <p style={{ maxWidth: '78ch', marginBottom: 14, fontSize: 14 }}>
          The catalog is also exposed as an MCP (Model Context Protocol) server, so a general-purpose
          AI agent can query it in natural language with no integration code. It was proved against
          the live deployment using Gemini CLI, and you can try the same thing below: the question
          really calls Gemini and the catalog.
        </p>
        <div className="mcp-grid">
          <div className="card card-pad mcp-before">
            <h3 className="card-title">Without a machine-readable catalog</h3>
            <p style={{ fontSize: 14 }}>
              A shopping agent can only read whatever a rendered webpage shows. There is no reliable
              way to ask "is this in stock" or "what's the price in USD" and get a grounded answer.
              It has to scrape, guess, or give up.
            </p>
          </div>
          <div className="card card-pad">
            <h3 className="card-title">With StoreMind's MCP binding</h3>
            <form className="mcp-ask" onSubmit={askCatalog}>
              <input
                type="text"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Do you have cotton kurtas, and what do they cost?"
                aria-label="Ask about the catalog"
                disabled={asking}
              />
              <button type="submit" className="btn" disabled={asking || !question.trim()}>
                {asking ? 'Asking…' : 'Ask'}
              </button>
            </form>
            <ErrorNotice>{mcpError}</ErrorNotice>
            {(asking || mcpResult) && (
              <pre className="mcp-transcript">
                {`$ gemini\n> ${askedQuestion}\n`}
                {asking && !mcpResult && '\n[waiting for a response…]'}
                {mcpResult && (
                  <>
                    {mcpResult.tool_calls.length > 0
                      ? mcpResult.tool_calls
                          .map((c) => `\n[calling ${c.tool}(${JSON.stringify(c.arguments)})]`)
                          .join('')
                      : '\n[answered directly, no catalog lookup needed]'}
                    {`\n\n${mcpResult.answer}`}
                  </>
                )}
              </pre>
            )}
          </div>
        </div>
      </section>

      <aside className="footnote">
        <p>
          <strong>Built and independently verified:</strong> a Universal Commerce Protocol (UCP)
          integration, Google's agentic-commerce standard, running the same fail-closed readiness
          gate as ACP against a real, deployed server. The three checkout gates (readiness,
          availability, price and currency validity) are visible per product on the Products page.
          The MCP binding above is the same integration one layer further: a real agent, not just
          REST calls, querying the live catalog.
        </p>
        <p>
          <strong>Not in this build:</strong> an Ops Copilot alerts layer. That is a deliberate
          scope call, not an oversight.
        </p>
      </aside>
    </div>
  )
}
