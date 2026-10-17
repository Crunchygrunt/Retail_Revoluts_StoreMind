// // Thin fetch wrapper over the StoreMind FastAPI service. Every function
// // here maps 1:1 to an endpoint in app/main.py — see that file for the
// // authoritative request/response shapes.

// const BASE_URL =
//   import.meta.env.VITE_API_BASE_URL || 'https://storemind-ingest-444891828026.us-central1.run.app'

// async function request(path, options = {}) {
//   // FormData (file uploads) must NOT get an explicit Content-Type —
//   // the browser sets one itself, including the multipart boundary
//   // fetch/the server need to split fields apart. Setting it manually
//   // here would send a boundary-less header and the upload would fail
//   // to parse server-side.
//   const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData

//   let res
//   try {
//     res = await fetch(`${BASE_URL}${path}`, {
//       headers: isFormData
//         ? { ...(options.headers || {}) }
//         : { 'Content-Type': 'application/json', ...(options.headers || {}) },
//       ...options,
//     })
//   } catch (err) {
//     // Most commonly this is CORS or the service being unreachable — surface
//     // it plainly rather than a generic "Failed to fetch".
//     throw new Error(
//       `Could not reach the API at ${BASE_URL}${path}. Check CORS is enabled on the backend and the URL is correct. (${err.message})`
//     )
//   }

//   const data = await res.json().catch(() => null)

//   if (!res.ok) {
//     const message = data?.detail || data?.reason || `Request failed with status ${res.status}`
//     throw new Error(typeof message === 'string' ? message : JSON.stringify(message))
//   }

//   return data
// }

// export const api = {
//   health: () => request('/health'),

//   ingest: (rows) => request('/ingest', { method: 'POST', body: JSON.stringify({ rows }) }),

//   // .xlsx is a binary zip format, not text — the File object goes
//   // straight into FormData rather than through JSON.stringify, same as
//   // a plain HTML file-upload form field. Parsing only: this returns
//   // {"rows": [...]} for the Ingest page to review, it does not run the
//   // rows through the pipeline itself (see main.py's /parse/xlsx).
//   parseXlsx: (file) => {
//     const body = new FormData()
//     body.append('file', file)
//     return request('/parse/xlsx', { method: 'POST', body })
//   },

//   getProducts: (limit = 50) => request(`/products?limit=${limit}`),

//   search: (q = '', category = '') =>
//     request(`/storefront/search?q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}`),

//   checkout: (sku, quantity = 1) =>
//     request('/storefront/checkout', {
//       method: 'POST',
//       body: JSON.stringify({ sku, quantity }),
//     }),

//   decisionLog: (limit = 100) => request(`/decision-log?limit=${limit}`),

//   fraudCheck: (scenario) =>
//     request('/fraud/check', { method: 'POST', body: JSON.stringify({ scenario }) }),

//   fraudTestHarness: () => request('/fraud/test-harness'),
// }




// Thin fetch wrapper over the StoreMind FastAPI service. Every function
// here maps 1:1 to an endpoint in app/main.py — see that file for the
// authoritative request/response shapes.

const BASE_URL =
  import.meta.env.VITE_API_BASE_URL || 'https://storemind-ingest-444891828026.us-central1.run.app'

async function request(path, options = {}) {
  // FormData (file uploads) must NOT get an explicit Content-Type —
  // the browser sets one itself, including the multipart boundary
  // fetch/the server need to split fields apart. Setting it manually
  // here would send a boundary-less header and the upload would fail
  // to parse server-side.
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData

  let res
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      headers: isFormData
        ? { ...(options.headers || {}) }
        : { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    })
  } catch (err) {
    // Most commonly this is CORS or the service being unreachable — surface
    // it plainly rather than a generic "Failed to fetch".
    throw new Error(
      `Could not reach the API at ${BASE_URL}${path}. Check CORS is enabled on the backend and the URL is correct. (${err.message})`
    )
  }

  const data = await res.json().catch(() => null)

  if (!res.ok) {
    const message = data?.detail || data?.reason || `Request failed with status ${res.status}`
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message))
  }

  return data
}

export const api = {
  health: () => request('/health'),

  ingest: (rows) => request('/ingest', { method: 'POST', body: JSON.stringify({ rows }) }),

  // .xlsx is a binary zip format, not text — the File object goes
  // straight into FormData rather than through JSON.stringify, same as
  // a plain HTML file-upload form field. Parsing only: this returns
  // {"rows": [...]} for the Ingest page to review, it does not run the
  // rows through the pipeline itself (see main.py's /parse/xlsx).
  parseXlsx: (file) => {
    const body = new FormData()
    body.append('file', file)
    return request('/parse/xlsx', { method: 'POST', body })
  },

  getProducts: (limit = 50) => request(`/products?limit=${limit}`),

  getSeller: () => request('/seller'),

  search: (q = '', category = '') =>
    request(`/storefront/search?q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}`),

  checkout: (sku, quantity = 1) =>
    request('/storefront/checkout', {
      method: 'POST',
      body: JSON.stringify({ sku, quantity }),
    }),

  // The live MCP agent widget (Overview page) — a real natural-language
  // question, answered by Gemini deciding whether/how to call the same
  // mounted MCP endpoint an external agent (Gemini CLI) already proved
  // works against production. See mcp_agent_bridge.py.
  askCatalog: (question) =>
    request('/mcp/ask', {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),

  decisionLog: (limit = 100) => request(`/decision-log?limit=${limit}`),

  fraudCheck: (scenario) =>
    request('/fraud/check', { method: 'POST', body: JSON.stringify({ scenario }) }),

  fraudTestHarness: () => request('/fraud/test-harness'),
}
