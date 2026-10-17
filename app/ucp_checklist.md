# UCP Layer — Checklist (parked until ACP work is complete)

Researched but NOT built. Kept here so nothing has to be re-derived.

## Why UCP matters for this project

- UCP is co-developed by Google, Shopify, Etsy, Wayfair, Target, Walmart,
  **Amazon, Microsoft, Meta, Salesforce and Stripe**. Stripe co-developed
  ACP *and* co-develops UCP; Etsy was ACP's flagship launch partner *and*
  co-develops UCP. This is consolidation, not a two-sided protocol war.
- **Flipkart and Shopee are UCP endorsers** — the two largest platforms in
  StoreMind's target geography.
- This is a Google Cloud hackathon. UCP is Google's standard.
- Architecturally UCP fits StoreMind far better than ACP does: ACP is a
  flat file StoreMind *checks*, UCP is a live API StoreMind could *be*.

## Blockers (known, not fixable before the deadline)

- Needs a Merchant Center account, waitlist application, **Google approval**
  before going live, plus Google Pay setup.
- Checkout currently US / Canada / Australia only, select merchants.
- => Cannot go live. CAN demonstrate readiness, which is the whole premise.

## Endpoints to add

| UCP endpoint | Method | Maps from |
|---|---|---|
| `/catalog/search` | POST | existing `GET /storefront/search` |
| `/catalog/lookup` | POST | `store.get_product`, batched |
| `/catalog/product` | POST | `store.get_product`, single + full detail |

Request:  `{query, context, signals, attribution, filters, pagination}`
Response: `{ucp, products[], pagination, actions}`

## Field mapping

| StoreMind | UCP | Work |
|---|---|---|
| `sku` | `id` | rename |
| `name` | `title` | rename |
| `description` | `description` | none |
| `price` (float major units) | `price_range` / variant price in **minor units** | convert — 1499.0 -> 149900. Must be exact; rounding here is real money |
| `currency` | ISO 4217 | **already correct** — Session 3's currency work lands perfectly |
| `image_url` | `media` | reshape |
| — | `variants[]` (REQUIRED) | **real gap** — no variant concept exists |
| — | `ucp` response envelope | new, small |

Required product fields: `id`, `title`, `description`, `price_range`, `variants`.

## Other pieces

- [ ] `/.well-known/ucp` profile: declares capabilities, versions, endpoints,
      public keys. Self-contained, no dependency on the rest.
      Namespaces: `dev.ucp.shopping.catalog.search`,
      `dev.ucp.shopping.catalog.lookup`, `dev.ucp.shopping.checkout`,
      `dev.ucp.common.identity_linking`.
- [ ] Variant model — for SME sellers most products have no variants, so a
      single-variant array carrying the product's own price and availability
      is legitimate, not a fudge.
- [ ] Identity linking (OAuth 2.0, RFC 8414 discovery via
      `/.well-known/oauth-authorization-server`) — stable part of the spec,
      but likely out of scope.
- [ ] Payments are AP2. Out of scope for a demo.
- [ ] Reference: Shopify's Storefront MCP already implements UCP Catalog
      (`search_catalog`, `lookup_catalog`, `get_product`) — useful as a
      known-good implementation to compare shapes against.

## Strategic note for the pitch

Worth reframing StoreMind as protocol-agnostic agent-readiness, proven
against both, rather than "an ACP tool". Two further points that came out
of the research and should shape the deck:

1. The honest whitespace is **platform-less sellers**, not "retailers" —
   Shopify merchants already get UCP readiness essentially for free via
   Storefront MCP, and Merchant Center covers Google surfaces. The gap is
   the seller with no platform admin panel at all.
2. **Neither protocol is live in South Asia yet.** The pitch must say
   *when*, not *now*. Also: India's ONDC/Beckn is already agent-ready and
   is arguably the more relevant standard for the target geography — worth
   at least a slide, since ignoring it is a visible hole to anyone who
   knows the market.
   
   