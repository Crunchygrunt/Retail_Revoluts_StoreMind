# StoreMind — Phase 1

Spine of the pipeline: **raw catalog → Layer-0 validator → fallback-protected
Gemini structuring → store.** This is Week 1 from the build plan. No
readiness score, no gate, no storefront yet — that's Phase 2.

**No GCP access yet? The whole thing runs locally with zero cloud
dependencies right now.** Three interchangeable LLM backends and two
interchangeable store backends mean you build and rehearse today, then
flip two environment variables when your GCP project comes through — no
code changes.

## What's here

```
storemind/
├── app/
│   ├── layer0_validator.py          no-LLM sanity checks — DONE, tested
│   ├── fallback_parser.py           3-layer JSON recovery — DONE, tested
│   ├── readiness_score.py           agent readiness score, fail-open — DONE, tested
│   ├── storefront.py                agent search + mock checkout — DONE, tested
│   ├── catalog_agent_mock.py        offline mock LLM — DONE, tested, default
│   ├── catalog_agent_aistudio.py    real Gemini via free AI Studio key — DONE, verified
│   ├── catalog_agent.py             real Gemini via Vertex AI (needs GCP)
│   ├── local_store.py               file-backed store — DONE, tested, default
│   ├── firestore_client.py          real Firestore (needs GCP)
│   ├── main.py                      FastAPI service, backend-selectable
│   ├── requirements.txt             core deps — works fully offline
│   ├── requirements-aistudio.txt    add-on for the AI Studio backend
│   ├── requirements-gcp.txt         add-on for Vertex AI + Firestore
│   ├── .env.example
│   ├── Dockerfile
│   └── tests/                       run anywhere, no deps needed
├── sample_data/
│   ├── messy_catalog.csv            11 rows, 6 broken + 1 deliberately thin
│   ├── stress_test_catalog.csv      73 rows, wide variety of edge cases
│   ├── generate_stress_catalog.py   regenerates the stress catalog
│   ├── run_stress_test.py           full pipeline vs. the 73-row catalog
│   ├── preview_layer0.py            Layer-0 only, stdlib only
│   └── run_offline_pipeline.py      FULL pipeline incl. readiness score +
│                                     simulated agent search/checkout
├── deploy.sh                        GCP setup + Cloud Run deploy, for later
└── README.md
```

## One-time setup

```powershell
cd "C:\Users\Yuvraj Singh\OneDrive\Desktop\STOREMIND\app"
pip install -r requirements.txt
pip install -r requirements-aistudio.txt   # for real Gemini via a free API key
Copy-Item .env.example .env
notepad .env
```

In the `.env` file that opens, set:
```
STOREMIND_LLM_BACKEND=aistudio
GEMINI_API_KEY=your-real-key-here
STOREMIND_STORE_BACKEND=local
```
Save and close. `main.py` loads this automatically on startup via
`python-dotenv` — no more `$env:...` by hand. `.env` is in `.gitignore`,
so the key won't get committed if this goes into git for submission.

Get a free key at https://aistudio.google.com/apikey if you don't have
one yet — no GCP project or billing needed.

## Cold start — every time you reopen VS Code

**Terminal 1 — the server:**
```powershell
cd "C:\Users\Yuvraj Singh\OneDrive\Desktop\STOREMIND\app"
.venv\Scripts\Activate.ps1        # skip if VS Code auto-activates it for you
uvicorn main:app --reload --port 8080
```
Leave this running. Check `llm_backend` in the `/ingest` response later —
if it says `mock` instead of `aistudio`, `.env` isn't being picked up
(confirm it's saved as `app/.env`, not `app/.env.txt` or the project root).

**Terminal 2 — everything else:**
```powershell
cd "C:\Users\Yuvraj Singh\OneDrive\Desktop\STOREMIND\sample_data"
.venv\Scripts\Activate.ps1
python preview_layer0.py
curl.exe -X POST http://localhost:8080/ingest -H "Content-Type: application/json" -d "@ingest_request_body.json"
curl.exe "http://localhost:8080/storefront/search?q=cotton"
curl.exe http://localhost:8080/decision-log
```

Checkout needs the body in a file first (PowerShell's inline JSON quoting
for `curl` is unreliable):
```powershell
Set-Content -Path checkout_request.json -Value '{"sku": "SKU-001", "quantity": 1}'
curl.exe -X POST http://localhost:8080/storefront/checkout -H "Content-Type: application/json" -d "@checkout_request.json"
```

To fully reset between test runs (wipes structured products, rejections,
and the decision log) — run from `app/`, since that's where it's written:
```powershell
Remove-Item local_store_data.json -ErrorAction SilentlyContinue
```

## Testing tiers (what each backend needs)

- **`mock` + `local`** (offline default) — nothing beyond `requirements.txt`.
  `python3 tests/test_layer0_validator.py`, `test_fallback_parser.py`,
  `test_readiness_score.py`, `test_storefront.py`, and
  `sample_data/run_offline_pipeline.py` all run this way, no `.env` needed.
- **`aistudio` + `local`** — needs `requirements-aistudio.txt` and a free
  API key in `.env`, as set up above. No GCP project.
- **`vertexai` + `firestore`** — needs `requirements-gcp.txt`, a GCP
  project, and `gcloud auth application-default login`. Set
  `STOREMIND_LLM_BACKEND=vertexai`, `STOREMIND_STORE_BACKEND=firestore`,
  `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION` in `.env` once you
  have access — or skip local iteration and run `deploy.sh` straight to
  Cloud Run (edit `PROJECT_ID` in it first).

Nothing about `main.py`, `layer0_validator.py`, `fallback_parser.py`,
`readiness_score.py`, or `storefront.py` changes between tiers — only
what's in `.env` does.

## What's verified right now

- `layer0_validator.py` — 9/9 unit tests passing
- `fallback_parser.py` — 6/6 unit tests passing
- `readiness_score.py` — 4/4 unit tests passing
- `storefront.py` (search + **fail-closed** checkout + decision log) — 9/9
  unit tests passing
- **`catalog_agent_aistudio.py` — verified against real Gemini** (`gemini-3.6-flash`)
  on the actual messy catalog: 4/4 rows structured successfully, all via
  `strict_json`, then searched and checked out live through the running
  FastAPI service (not just the offline script).
- `run_offline_pipeline.py` — full rehearsal script against the small demo
  catalog (now 11 rows, including one deliberately thin product so the
  gate proof doesn't depend on the mock's format-cycling landing on a low
  score by chance): Layer-0 → mock LLM → fallback parser → readiness
  scoring → local store → a simulated shopping agent searching and
  checking out, plus a genuinely blocked checkout on the thin product.
- `run_stress_test.py` — 73-row stress catalog, zero crashes, Layer-0
  correctly split 50 accepted / 23 rejected across ~8 distinct rejection
  categories, and exactly the 10 deliberately-thin products (and nothing
  else) landed below the readiness threshold once the bugs below were fixed.

What's **not** yet verified: `catalog_agent.py` (Vertex AI) and real
Firestore — those need GCP credentials this environment doesn't have.

**One open question worth resolving before the demo, not after:** every
product structured by real Gemini so far has scored 90+ (often a flat
100.0 across every component). The mock's low scores come from its
prose-fallback format genuinely omitting fields — real Gemini output
hasn't been tested against a case sparse enough to trigger a block. Worth
deliberately feeding one vague, low-information row through the AI Studio
backend before you rely on the gate visibly firing during a live demo —
see "Next" below.

## Next (Phase 3, per the build plan)

- **Run the stress catalog against real Gemini** (the test that actually
  matters — the offline mock run above found and fixed three real bugs,
  but only real Gemini output tells you if there are more):
  ```powershell
  cd sample_data
  python -c "
  import csv, json
  with open('stress_test_catalog.csv', newline='', encoding='utf-8') as f:
      rows = list(csv.DictReader(f))
  json.dump({'rows': rows}, open('stress_ingest_body.json', 'w', encoding='utf-8'), indent=2)
  "
  curl.exe -X POST http://localhost:8080/ingest -H "Content-Type: application/json" -d "@stress_ingest_body.json"
  ```
  Only 50 of the 73 rows clear Layer-0, so this is 50 real API calls —
  comfortably within free-tier limits (roughly 3-5 minutes at typical
  free-tier RPM), with automatic retry-with-backoff on rate-limit errors.
  Worth specifically checking: how the Hindi (ST-043), Chinese (ST-044),
  and emoji (ST-045) rows come back — that's a direct test of the
  "bilingual, JAPAC" positioning from the plan doc, and the mock can't
  tell you anything about that (see below).
- Fraud Signal Detector + Ops Copilot (flex modules, Week 3).
- Rewrite the competitive-differentiation slide.
- Dry-run the full demo narration: upload → structure → score → search →
  checkout (passes) → checkout a deliberately bad product (blocked) →
  show the decision log explaining both.

## Bugs found and fixed via the 73-row stress test (`stress_test_catalog.csv`)

Three real bugs were caught before Cloud Run debugging had to find them:

1. **Fallback parser had no keyword recovery path.** If real Gemini ever
   degraded to prose, keywords would always come back empty regardless of
   what the model wrote. Fixed — `Keywords: a, b, c` lines now parse into
   a list.
2. **`Material: cotton` / `Color: natural` lines corrupted `attributes`.**
   They overwrote it with a raw string instead of building a dict, which
   would have silently broken `len(attributes)`-based scoring downstream
   (a string's character count isn't its attribute count). Fixed.
3. **CJK product names were scored as generic.** `source_richness`'s
   name-specificity check split on whitespace — but Chinese/Japanese/
   Korean don't delimit words with spaces, so *any* CJK name was treated
   as a single generic "word," identical to a raw name that was literally
   `"Item"`. Given the plan's own "bilingual, JAPAC" positioning, this
   would have been an embarrassing bug to find live in front of judges.
   Fixed — CJK text is now judged by character length instead.

All three have permanent regression tests. Separately: the mock's simple
English-keyword category/attribute detection produces weak output for
non-Latin names (Chinese, Hindi) — that's an expected *mock* limitation,
not a pipeline bug, and real Gemini is multilingual and should handle it
fine. That's exactly why the real-Gemini stress run above matters more
than the mock run did.
