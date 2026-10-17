// Locale used for thousands-grouping per currency, so a price displays
// with its real-world grouping convention rather than always defaulting
// to Western 3-digit groups — e.g. INR groups as lakh/crore (1,25,000),
// not 125,000. Intl.NumberFormat's `currency` style already resolves the
// correct symbol on its own; it's only the grouping cadence that needs
// an explicit locale to match.
const LOCALE_BY_CURRENCY = {
  INR: 'en-IN',
  IDR: 'id-ID',
  VND: 'vi-VN',
  THB: 'th-TH',
  USD: 'en-US',
  EUR: 'de-DE',
  GBP: 'en-GB',
}

// price is a bare number by the time it reaches here — currency
// identity used to be discarded after parsing, so every product's price
// looked the same regardless of which currency it actually was. Now
// carried through from Layer-0 as an ISO 4217 code (INR, IDR, ...) when
// recognized, or the raw currency token as-is (e.g. "Kč") when it isn't.
// Shared by ProductsPage, SearchPage, and CheckoutPage — all three list
// products with a price, and duplicating this per page risks the three
// copies drifting apart the next time a currency gets added.
//
// `inferred` marks a currency that came from currency_inference.py's
// catalog-majority fallback rather than a real Layer-0 detection —
// appends a visible "*" so it's never silently indistinguishable from
// a detected currency on the page, not just in the underlying data.
export function formatPrice(price, currency, inferred = false) {
  if (price === null || price === undefined) return '—'
  if (typeof price !== 'number') {
    const base = currency ? `${price} ${currency}` : String(price)
    return inferred ? `${base} *` : base
  }
  if (!currency) return price.toLocaleString()

  let formatted
  try {
    // Handles both the symbol lookup and the grouping in one call, for
    // a recognized ISO code.
    formatted = new Intl.NumberFormat(LOCALE_BY_CURRENCY[currency], {
      style: 'currency',
      currency,
    }).format(price)
  } catch {
    // Intl.NumberFormat throws on anything that isn't a valid ISO 4217
    // code — expected for the raw-token fallback case, not an error.
    formatted = `${price.toLocaleString(LOCALE_BY_CURRENCY[currency])} ${currency}`
  }
  return inferred ? `${formatted} *` : formatted
}

// ---------------------------------------------------------------------
// ACP eligibility tier — display labels and the CSS class each maps to.
//
// Tier 1 and Tier 2 deliberately share styling: both have IDENTICAL
// checkout capability (see acp_schema.py's derive_eligibility) — a
// product made conformant with a derived brand or a generated product
// page is not worse off than one the seller filled in completely, so
// it earns a distinguishing label, never a visual penalty. Tier 3
// borrows the existing gate-blocked styling since it genuinely can't
// check out — but it's a different concept from "Gate": a product can
// pass Gate (readiness score alone) and still land in Tier 3 if its
// ACP feed record is incomplete, which is exactly the distinction this
// page now needs to make legible at a glance.
const TIER_LABELS = {
  tier_1_compliant: 'Tier 1 · Compliant',
  tier_2_enriched: 'Tier 2 · Enriched',
  tier_3_discovery: 'Tier 3 · Discovery only',
}

export function tierLabel(tier) {
  return TIER_LABELS[tier] || tier || '—'
}

// Short form for the Products table pill itself — the full phrase
// above is long enough (up to "Tier 3 · Discovery only", 22 chars)
// that at the table's actual column width it was measured overflowing
// its cell by 50+px and bleeding into the next column. The full
// phrase is still available; it moved to the pill's tooltip (see
// acpTierTooltip below) rather than being lost.
const TIER_SHORT_LABELS = {
  tier_1_compliant: 'Tier 1',
  tier_2_enriched: 'Tier 2',
  tier_3_discovery: 'Tier 3',
}

export function tierShortLabel(tier) {
  return TIER_SHORT_LABELS[tier] || tier || '—'
}

// Tooltip text for the ACP pill — the full phrase always, with the
// specific remediation appended when there is one. Unlike the old
// behavior (a tooltip/hint only for tier_3_discovery), every tier now
// gets a tooltip, since the visible pill text was shortened for all
// three and shouldn't lose information for the two "good" tiers.
export function acpTierTooltip(product) {
  const full = tierLabel(product.acp_tier)
  const remediation =
    product.acp_tier === 'tier_3_discovery' ? product.acp_gaps?.remediation?.[0] : undefined
  return remediation ? `${full} — ${remediation}` : full
}

// A falsy/unrecognized tier (e.g. a product ingested before this
// feature existed) gets NO class rather than defaulting to "eligible" —
// showing an unknown as green would misreport it as fine when it's
// really just undetermined.
export function tierClassName(tier) {
  if (!tier) return ''
  return tier === 'tier_3_discovery' ? 'gate-blocked' : 'gate-eligible'
}

// Compact pill version of the tier badge — used on the Products page
// table, where the always-visible remediation-hint text under the
// plain tierClassName badge was, once a second tier column (UCP) was
// added alongside it, the single widest and tallest thing in the row,
// forcing the whole table past the viewport. Same tier logic as
// tierClassName above, styled as a small chip instead; the reason for
// a non-eligible tier moves to a title tooltip on the pill (see
// ProductsPage.jsx) rather than always-visible wrapped text — the same
// disclosure pattern this file already uses for brandSourceTooltip and
// imageSourceTooltip, extended here rather than invented fresh.
// tierClassName itself is left unchanged and still exported, in case
// another page already depends on the plain dot+text styling.
export function tierPillClassName(tier) {
  if (!tier) return 'status-pill'
  return tier === 'tier_3_discovery'
    ? 'status-pill status-pill-blocked'
    : 'status-pill status-pill-eligible'
}

// Why a brand value is what it is, for a tooltip — same purpose as the
// currency_inferred tooltip below: a derived value should never be
// silently indistinguishable from one the seller actually stated.
const BRAND_SOURCE_TEXT = {
  source: "Stated directly in this product's own listing",
  vision: 'Read from the product photo',
  seller_name: 'Using your store name — no product-specific brand was found',
}

export function brandSourceTooltip(product) {
  if (!product.brand_source) return undefined
  if (product.brand_source === 'inferred') {
    const pct = Math.round((product.brand_inference_confidence ?? 0) * 100)
    return `Inferred from ${pct}% of this catalog's other products`
  }
  return BRAND_SOURCE_TEXT[product.brand_source]
}

// Same idea for the product photo: where it came from, or plainly why
// there isn't one — this is the one ACP-required field StoreMind will
// never fabricate on the seller's behalf.
const IMAGE_SOURCE_TEXT = {
  source_url: "Linked from the seller's own listing",
  hosted: "Hosted by StoreMind from the seller's own photo",
  none: "No photo — the one thing StoreMind can't add on the seller's behalf",
}

export function imageSourceTooltip(product) {
  return IMAGE_SOURCE_TEXT[product.image_source]
}

// ---------------------------------------------------------------------
// UCP eligibility tier — mirrors the ACP tier section above, three UCP
// tiers instead (see ucp_eligibility.py): checkout_ready / catalog_only
// / not_ucp_ready. catalog_only deliberately reuses the SAME
// "gate-blocked" (red) treatment as ACP's own tier_3_discovery above,
// not a softer in-between color — this project's existing tierClassName
// already treats "still discoverable, not checkout-eligible" as
// gate-blocked for ACP, and UCP's catalog_only is the identical
// situation under a different protocol. Consistency with an existing
// decision, not a new one.
const UCP_TIER_LABELS = {
  checkout_ready: 'Checkout-ready',
  catalog_only: 'Catalog only',
  not_ucp_ready: 'Not UCP-ready',
}

export function ucpTierLabel(tier) {
  return UCP_TIER_LABELS[tier] || tier || '—'
}

// Short form, same reasoning as tierShortLabel above — "Checkout-ready"
// and "Not UCP-ready" also measured overflowing their column.
const UCP_TIER_SHORT_LABELS = {
  checkout_ready: 'Ready',
  catalog_only: 'Catalog only',
  not_ucp_ready: 'Not ready',
}

export function ucpTierShortLabel(tier) {
  return UCP_TIER_SHORT_LABELS[tier] || tier || '—'
}

// Tooltip text for the UCP pill — same pattern as acpTierTooltip:
// full phrase always, blocker reason appended when one exists.
export function ucpTierTooltip(product) {
  const full = ucpTierLabel(product.ucp_tier)
  const blocker = product.ucp_tier && product.ucp_tier !== 'checkout_ready' ? ucpBlockerHint(product) : undefined
  return blocker ? `${full} — ${blocker}` : full
}

// Same falsy-handling philosophy as tierClassName above: a product
// ingested before ucp_eligibility.py existed has no ucp_tier at all,
// and showing that as green ("eligible") would misreport an unknown as
// fine. No class, not a default-eligible one.
export function ucpTierClassName(tier) {
  if (!tier) return ''
  return tier === 'checkout_ready' ? 'gate-eligible' : 'gate-blocked'
}

// Pill version, same reasoning as tierPillClassName above.
export function ucpTierPillClassName(tier) {
  if (!tier) return 'status-pill'
  return tier === 'checkout_ready'
    ? 'status-pill status-pill-eligible'
    : 'status-pill status-pill-blocked'
}

// The one-line reason under a non-checkout_ready UCP badge — same
// remediation-hint pattern and same CSS class as the ACP tier above,
// just reading from ucp_blockers (ucp_eligibility.py) instead of
// acp_gaps.remediation.
export function ucpBlockerHint(product) {
  return product.ucp_blockers && product.ucp_blockers[0]
}

// ---------------------------------------------------------------------
// ONDC listability — genuinely simpler than the ACP/UCP tier helpers
// above: ONDC has no three-tier split (see ondc_eligibility.py's own
// docstring for why) - a product either forms a valid on_search item
// or it doesn't, so this is a two-state pill, not a tier label. Same
// "hover for the specific reason" pattern as everywhere else on this
// page, reading from ondc_blockers instead of acp_gaps.remediation or
// ucp_blockers.
export function ondcLabel(product) {
  if (product.is_ondc_listable === undefined || product.is_ondc_listable === null) return '\u2014'
  return product.is_ondc_listable ? 'ONDC Ready' : 'Not ONDC-ready'
}

// Same falsy-handling philosophy as tierClassName/ucpTierClassName
// above: a product ingested before ONDC eligibility existed has
// is_ondc_listable === undefined, and showing that as green would
// misreport an unknown as fine. No class, not a default-eligible one.
export function ondcPillClassName(product) {
  if (product.is_ondc_listable === undefined || product.is_ondc_listable === null) return 'status-pill'
  return product.is_ondc_listable
    ? 'status-pill status-pill-eligible'
    : 'status-pill status-pill-blocked'
}

// The domain (e.g. "ONDC:RET12") is shown even for a BLOCKED product
// when known — see ondc_eligibility.py's own docstring: a product
// missing packer info can still usefully say which domain it would
// list under once fixed, same remediation-hint instinct as the ACP
// tooltip above. Every blocker is shown, not just the first one (an
// ONDCMappingError collects ALL applicable gaps at once, unlike ACP/
// UCP's single-reason messages) - this is the one place on this page
// where a tooltip is a real checklist, not a single sentence.
export function ondcTooltip(product) {
  const domain = product.ondc_domain ? `Domain: ${product.ondc_domain}` : undefined
  if (product.is_ondc_listable) return domain || 'ONDC Ready'
  const blockers = product.ondc_blockers && product.ondc_blockers.length ? product.ondc_blockers.join('; ') : undefined
  return [domain, blockers].filter(Boolean).join(' \u2014 ') || undefined
}

// ---------------------------------------------------------------------
// Seller identity verification — SELLER-level, not per-product. Unlike
// every tier helper above, this doesn't take a product; it takes the
// single seller object from api.getSeller(), since GSTIN/Udyam/UPI+PAN/
// UPI+WhatsApp verification (seller_profile.py) is one fact about the
// whole instance, not something that varies row to row. Same
// label/tooltip/pill-class three-function shape as the tier helpers
// above, and reuses the same status-pill CSS classes rather than
// introducing new ones — this is a banner, not a table column, but the
// underlying "pill with a hover reason" idea is identical.
const SELLER_VERIFICATION_METHOD_LABELS = {
  gstin: 'GSTIN',
  udyam: 'Udyam Registration',
  'upi+pan': 'UPI ID + PAN',
  'upi+whatsapp': 'UPI ID + WhatsApp',
}

// Three states, not two: verified / configured-but-failed / nothing
// configured at all. The third is deliberately NOT styled as a
// failure — an unconfigured seller identity is optional and doesn't
// block anything (see seller_profile.py's blocks_ingestion), so
// treating it visually the same as a real failure would misreport an
// intentional, harmless default as a problem — the same falsy-handling
// philosophy tierClassName and ucpTierClassName already use above.
export function sellerVerificationLabel(seller) {
  if (!seller) return 'Seller status unavailable'
  if (seller.verified) {
    return `Verified · ${SELLER_VERIFICATION_METHOD_LABELS[seller.verification_method] || seller.verification_method}`
  }
  if (seller.any_verification_field_configured) return 'Identity verification failed'
  return 'Identity not configured'
}

export function sellerVerificationTooltip(seller) {
  if (!seller) return undefined
  if (seller.verified) {
    const holder = seller.gstin_holder_type || seller.pan_holder_type
    const method = SELLER_VERIFICATION_METHOD_LABELS[seller.verification_method] || seller.verification_method
    return holder ? `Verified via ${method} (${holder})` : `Verified via ${method}`
  }
  if (seller.any_verification_field_configured) {
    return seller.verification_reasons?.length
      ? seller.verification_reasons.join('; ')
      : 'A configured identity field did not pass verification'
  }
  return (
    'No seller identity field configured (GSTIN, Udyam, PAN, UPI ID, or ' +
    'WhatsApp number) — this is optional and does not block ingestion'
  )
}

export function sellerVerificationClassName(seller) {
  // Bare 'status-pill' with no modifier was a real bug: that class
  // alone has structural styling (padding, shape, the dot's size) but
  // NO background and NO dot color — those only exist on the
  // -eligible/-blocked modifiers. Every path here now resolves to a
  // modifier class, including the neutral ones, so nothing ever
  // renders as unstyled plain text.
  if (!seller) return 'status-pill status-pill-neutral'
  if (seller.verified) return 'status-pill status-pill-eligible'
  if (seller.any_verification_field_configured) return 'status-pill status-pill-blocked'
  return 'status-pill status-pill-neutral'
}
