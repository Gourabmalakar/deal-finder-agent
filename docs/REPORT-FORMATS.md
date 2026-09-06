# Report formats

Both modes produce a short lead-in (what was searched, when) followed by a
ranked table, followed by screenshots. Nothing in "Required fields" may be
omitted — if a value genuinely isn't available (e.g. a site has no stock),
write `not available` rather than dropping the row or the column.

---

## E-commerce report (`ecommerce-deal-finder`)

### Lead-in
- What was searched: product name, brand, and the exact variant matched
  (color / storage / size) if applicable.
- If the user gave a source URL, name the original site and its price for
  comparison.
- Timestamp of the check (date + time, IST).

### Ranked table — cheapest to most expensive, up to 5 rows

| Rank | Site | Price (₹) | vs. cheapest | Product page | Notes |
|---|---|---|---|---|---|
| 1 | Amazon.in | ₹24,490 | — | [link](https://…) | Official store, free delivery |

Required fields per row:
- **Site** — the platform actually checked (not an aggregator's name).
- **Price** — the live price confirmed on that page, in ₹. If the native
  currency isn't INR, show both and the FX rate used.
- **Product page** — the *exact* URL of the page checked (not a search
  results page, not the homepage).
- **Notes** — anything that qualifies the price: marketplace seller vs.
  official store, card/bank offer required, stock status, different
  variant than requested, free-delivery/COD availability.

### Price history
- Source checked (e.g. PriceHistory.app, BuyHatke) with its reported
  highest / average / lowest price and the time window covered.
- This tool's own logged history for the product, if `data/price_history.csv`
  has prior entries — note the trend (rising / falling / flat) since the
  last check.
- A one-line verdict: is now a good time to buy relative to history, or
  worth waiting? State the reasoning, not just an opinion.

### Screenshots
- One screenshot per verified site (proof of the live price), delivered via
  `SendUserFile` or embedded in a published Artifact.
- One screenshot of the price-history graph, if a price-history site had one.

### Caveats section (only if applicable)
- Sites that could not be checked and why (blocked, CAPTCHA, out of stock,
  region-locked, no matching listing found).

---

## Hotel report (`hotel-deal-finder`)

### Lead-in
- Place, check-in / check-out dates, number of nights, and guest count as
  confirmed with the user.
- If the user supplied specific hotel links, name the hotel(s) targeted.
- Timestamp of the check (date + time, IST).

### Ranked table — cheapest to most expensive, up to 5 rows

| Rank | Site | Hotel | Room type | Total price (₹) | Per night | Cancellation | Booking link |
|---|---|---|---|---|---|---|---|
| 1 | MakeMyTrip | Hotel Sepoy Grande | Standard Double | ₹1,541 | ₹1,541 | Free until 20 Sept | [link](https://…) |

Required fields per row:
- **Hotel** — exact property name (don't let a similarly-named property
  substitute for the one asked about).
- **Total price** for the full stay at the requested dates/guest count, plus
  **per-night** for comparison across different-length stays if relevant.
- **Cancellation** — free/non-refundable and the deadline if shown.
- **Booking link** — the *exact* page with those dates and guest count
  pre-filled if the URL supports it, not a generic hotel homepage.

### Screenshots
- One screenshot per verified site/rate, delivered via `SendUserFile` or
  embedded in a published Artifact.

### Caveats section (only if applicable)
- Sites where the requested dates/guests weren't available, where the price
  shown excludes taxes/fees actually payable, or where the site could not be
  checked at all.
