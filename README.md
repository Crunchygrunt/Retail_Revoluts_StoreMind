# StoreMind

**Agent-readiness and commerce-intelligence copilot for India's SME/D2C retailers.**
Built for the Google Cloud AI Builder Cup 2026 — Retail & Commerce track.

> *"Enforced, not just scored."* A product's eligibility for agent-driven checkout is a hard,
> fail-closed gate — not a dashboard number a seller can ignore.

---

## 🌐 Live Website

**Try StoreMind:** https://storemind-508614.web.app/

---

## The problem

ChatGPT, Gemini, and other shopping agents are becoming a real commerce surface — but every
readiness tool in this space gives SME/D2C sellers a *score* and leaves enforcement to them.
Meanwhile the sellers this matters most for — India's informal, WhatsApp/Instagram-native,
sub-₹40L-turnover businesses — are excluded by both ends of the stack: their catalogs are messy
photos and spreadsheets, not structured feeds, and GSTIN-only identity verification locks out
exactly the segment most in need of it.

StoreMind closes both gaps: it structures a messy catalog into an agent-queryable one, scores it
honestly (penalizing confident fabrication, not just rewarding shape), **enforces** the score at
the checkout gate, and verifies seller identity through paths that actually fit the informal
segment (GSTIN *or* Udyam *or* UPI+PAN *or* UPI+WhatsApp).

## What it does

| Stage | What happens |
|---|---|
| **Layer-0 validation** | Deterministic, no-LLM sanity checks on raw rows before any Gemini call — locale-aware price parsing (₹ lakh/crore, SEA thousands separators), mojibake detection, duplicate SKU handling |
| **Catalog Structuring Agent** | Gemini (Vertex AI) extracts name, category, description, attributes, keywords, price, and — only when actually present in the source — brand, net quantity, and manufacture date. Never fabricates an absent field |
| **Reflexion self-correction** | A bounded retry loop classifies every structuring gap as *model-fixable* (the source has the answer, the model missed it) or *source-limited* (genuinely absent — never retried). Every retry is grounded against the source text before being trusted, so a correction can never become a fabrication |
| **Episodic memory** | Validated corrections persist to Firestore and are retrieved — by category, by gap type, by semantic similarity via Gemini embeddings — to inform future rows, so the system measurably improves with usage |
| **Readiness scoring** | A six-component rubric dominated by *source richness* (45% weight) — the one component that looks at the raw input, not Gemini's polished output, specifically to catch confident fabrication from thin source data |
| **Fail-closed checkout gate** | Below-threshold products are genuinely blocked at the mock checkout endpoint and the live MCP tool — not flagged, blocked |
| **Protocol compliance** | Eligible products map to ACP (OpenAI), UCP (Google), and ONDC (India's open commerce network) — live-queryable by a real agent via a mounted MCP endpoint, proven against Gemini CLI in production |
| **Seller identity** | Either-or verification (GSTIN / Udyam / UPI+PAN / UPI+WhatsApp) — offline, deterministic, checksum-validated where a checksum exists (GSTIN), structure-only where none is published (PAN, Udyam) |
| **Fraud Signal Detector** | Flags return/COD-refusal patterns (bracketing, wardrobing, high refusal rate) against a labeled, accuracy-measured test harness — not a single demo example |

## Architecture — how we use Google Cloud

- **Cloud Run** — the FastAPI backend: ingestion, the agent-facing storefront, the fail-closed
  checkout gate, and a mounted MCP server for live agent queries.
- **Firestore** — the single source of truth: structured products, the decision log (every gate
  decision, with its reason), seller verification state, reflexion's episodic memory
  (`reflections` collection, with Firestore native vector search over Gemini embeddings),
  and UCP checkout session/idempotency state.
- **Gemini (Vertex AI)** — `gemini-3.6-flash` for catalog structuring, vision-based brand
  extraction, and reflexion's self-correction; `gemini-embedding-001` for episodic-memory
  retrieval.
- **Firebase Hosting** — serves the React frontend.
- **Cloud Storage** — hosts seller-supplied product images that arrive as raw bytes rather than
  public URLs (common for a WhatsApp-sourced catalog).

Every backend is swappable via environment variable — `mock`/`aistudio`/`vertexai` for the LLM,
`local`/`firestore` for storage — so the entire pipeline runs and is fully testable with **zero
GCP access**, which is also how most of this was built and verified before touching live infra.

## Repository layout

```
app/
  main.py                      FastAPI app — all routes
  catalog_agent*.py            Three interchangeable structuring backends (vertexai/aistudio/mock)
  reflexion*.py                Self-correction loop, classifier, episodic memory
  readiness_score.py           The fabrication-resistant scoring rubric
  acp_schema.py / ucp_*.py / ondc_*.py   Protocol conformance + eligibility
  seller_profile.py / *_validator.py     Either-or seller identity verification
  fraud_detector.py            Return/COD fraud signal detection
  storefront.py                Agent-facing search + fail-closed checkout
  firestore_client.py / local_store.py   Swappable storage backends
  tests/                       483 tests passing, pytest
storemind-frontend/
  src/pages/IngestPage.jsx     Catalog ingestion UI, including the reflexion transparency panel
  ...
```

## Running it locally (fully offline)

```bash
cd app
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

cp deploy.env.example deploy.env      # fill in your own values, or leave as mock/local
cp seller.env.example seller.env

uvicorn main:app --reload --port 8080
```

Open `http://localhost:8080/docs` for the interactive API, or point `storemind-frontend/`'s
`api.js` base URL at `localhost:8080` and run it with `cd storemind-frontend && npm run dev`.

## Running the test suite

```bash
cd app
python -m pytest -v
```

## Demo evidence, not just a demo

- `GET /fraud/test-harness` — runs 5 labeled return scenarios, returns an accuracy readout.
- `GET /reflexion/ab-harness` — runs a labeled 10-row dataset with and against without reflexion,
  returns the measured readiness lift and threshold-crossing delta. `python3 reflexion_ab_harness.py`
  runs the same comparison standalone.
- `GET /decision-log` — every gate decision made, with its reason, for live narration.

## What's deliberately out of scope

- Live ONDC network participation (registry onboarding, `/select`/`/init`/`/confirm`) —
  catalog conformance only, by design.
- Live financial verification (UPI penny-drop) — would duplicate the built, offline either-or
  system and conflicts with a standing "no paid live APIs" constraint.
- A central OAuth authorization server — neither UCP nor ACP require one for agent-to-merchant
  trust; both use signed requests instead.

---

Built by [Retail Revoluts] for the Google Cloud AI Builder Cup 2026.