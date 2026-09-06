---
name: ecommerce-deal-finder
description: Find the cheapest verified price for a product across the top sites for its category, with live screenshots and price history. Use when the user pastes a product URL (Amazon, Flipkart, Myntra, etc.) or describes a product and asks for the best price, cheapest option, where to buy it, or a price comparison.
---

# Ecommerce deal finder

Goal: turn one product (a URL or a description) into the **5 cheapest
verified prices** across the sites that matter for that category, each with
proof — screenshot, exact link, timestamp — plus price history. Follow
`docs/REPORT-FORMATS.md` for the exact output shape.

## Steps

### 1. Identify the product
- If given a URL: open it with the Browser tool (`navigate`, then
  `read_page` / `get_page_text`). Extract the exact product title, brand,
  and variant (color / storage / size) — this is the thing every other site
  must match, not just something similar.
- If given a description only: use it as the query directly. If the
  description is ambiguous about variant (which storage, which color, which
  size), **ask before searching** — a confidently wrong variant match is
  worse than a slower search.
- Note the origin site's own current price if a URL was given, for the
  "vs. cheapest" comparison in the report.

### 2. Pick the category and its site list
- Read `data/category_sites.yaml`. Match the product's title/breadcrumbs
  against each category's `match_keywords`; first match wins. If nothing
  matches, use the `general` fallback list.
- If the origin site isn't already in that category's list, still include it
  in the comparison (it's a live, confirmed data point already).

### 3. Discover candidates
- Spawn the `price-scout` subagent with: product title, brand, variant, and
  the category's site list. It does text-only research (WebSearch/WebFetch)
  across those sites plus price-aggregator pages and returns candidate URLs
  with claimed prices — unverified leads, not final answers.
- While that runs, you can independently search for obvious candidates
  yourself if you already have good leads (e.g. the origin URL itself).

### 4. Verify live, with screenshots
For each candidate site (aim to end up with 5 confirmed prices):
- `navigate` to the candidate's exact product page with the Browser tool.
- Confirm via `read_page`/`get_page_text` that the title/brand/variant
  actually match what was identified in step 1 — reject a listing for a
  different variant or a different (similarly-named) product rather than
  reporting its price as if it were the same item.
- Read the current price directly off the page (not off a search snippet).
- Take a screenshot (`computer` action `screenshot`, or `zoom` on the
  price/title area for a tighter proof shot). Save it under `runs/` with a
  clear filename (e.g. `runs/<product-slug>/<site>.png`).
- If a site is blocked, CAPTCHA'd, shows no stock, or shows no matching
  listing: skip it and note why — don't guess a price for it.

### 5. Price history
- Try each site in `data/category_sites.yaml`'s `price_history_sources` in
  order (e.g. PriceHistory.app, BuyHatke): search for the product, open the
  result, read off highest / average / lowest and the window covered, and
  screenshot the graph if there is one.
- Read `data/price_history.csv` for any prior entries on this exact product
  (match on `item_name`) — note the trend since the last time this tool
  checked it.
- **After finishing**, append one row per site you verified in step 4 to
  `data/price_history.csv` via Bash (append-only — never rewrite past rows):
  `timestamp,item_type=ecommerce,item_name,site,url,price_inr,currency_native,price_native,notes`.

### 6. Compile the report
- Rank all confirmed prices ascending, keep the cheapest 5 (fewer is fine if
  fewer were confirmed — never pad with an unverified guess to reach 5).
- Follow `docs/REPORT-FORMATS.md` exactly: lead-in, ranked table, price
  history, screenshots, caveats.
- Deliver screenshots via `SendUserFile`, or — if you want one shareable
  page — publish an Artifact with the table and images (upload images via
  the artifact's asset store; load the `artifact-capabilities` skill first
  if you go this route).

## Guardrails
- Never add anything to a cart, never check out, never enter any account,
  payment, or address details anywhere. This skill only reads public pages.
- Never invent or extrapolate a price — every number in the report must
  trace back to a page actually opened in this session.
- Respect the sites: one page load per candidate, don't re-hit the same URL
  repeatedly, and never attempt to bypass a CAPTCHA or bot-detection wall —
  skip and disclose instead.
