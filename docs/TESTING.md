# Testing report — webapp scraper

A live test pass against `webapp/` (Path B — see `docs/ARCHITECTURE.md`),
run against real sites, not mocks. Raw responses saved under
`webapp/test_runs/*.json` (gitignored — local artifacts of this pass, not
a fixture suite). This is a point-in-time report; site markup and bot
detection change, so results will drift — re-run rather than trust this
file as current truth.

## What was tested

| # | Case | Category / mode | Result |
|---|---|---|---|
| 1 | "boAt Airdopes 141 earbuds" | electronics | Found bugs #1, #2 (below), fixed, re-verified clean |
| 2 | "Levis men slim fit jeans" | clothing_fashion | Category correctly detected via "jeans" keyword |
| 3 | "Nykaa matte lipstick" | cosmetics_beauty | Found bug #3, fixed, re-verified clean |
| 4 | `https://www.flipkart.com/search?q=samsung+galaxy+m14` | product URL input | Confirmed origin-URL resolution (title fetched, origin site excluded from re-search) |
| 5 | Hotel: Mysuru, 20→21 Sep, 2 guests | hotel | Found bug #4, fixed, re-verified |
| 6 | Hotel: Goa, 10→13 Oct, 4 guests | hotel, 3 nights, 4 guests | Consistent behavior, no new issues |
| 7 | Backend validation: checkout before checkin, empty query, malformed date | input validation | All three correctly rejected (400/422) |
| 8 | UI: tab toggle, both panels, live search render | frontend | Found bug #5 (below), fixed, re-verified |
| 9 | Eval logging | `/api/evals` after a real search | Row logged correctly, verdict returned inline |
| 10 | Uptime check | success + induced failure (dead port) | Both paths correct — silent on success, logs + notifies on failure |

## Bugs found and fixed during this pass

1. **Amazon's generic price heuristic grabbed a nav/promo price, not the
   listing.** Query "boAt Airdopes 141 earbuds" against amazon.in returned
   ₹500 with a nonsense "title" (page-navigation text) — the actual
   product on-screen was priced ₹2,199. Root cause: the generic "first ₹
   on the page" heuristic hit an "under ₹500" category filter link before
   reaching the real result grid. **Fix**: added a tuned selector for
   Amazon's stable `div[data-component-type="s-search-result"]` markup
   (`scraper._extract_amazon_result`); generic heuristic stays the
   fallback for every other site. Re-verified: amazon.in now returns
   ₹2,199 with title "boAt" matching the visible listing.
2. **TataCliq matched a price-range filter control**, not a listing:
   "Select All ₹0-₹1,000 filter option 3 products available for this
   filte[r]" at ₹1,000. **Fix**: added a denylist of filter/sort phrases
   ("filter", "select all", "sort by", "clear all", "price range") the
   heuristic now skips past, plus a rule dropping any candidate with more
   than 2 "₹" symbols in its snippet (a real listing shows at most a sale
   + strikethrough MRP; 3+ is a bucket list). Re-verified: tatacliq.com
   then returned a real "Boat Airdopes 163..." listing at ₹1,145.
3. **Flipkart matched its own price-range filter widget** for the
   cosmetics query: "Price . . . Min ₹200 ₹300 ₹400 ₹500 ₹600 to ₹200
   ₹300..." at ₹200. Same class of bug as #2, different site. Fix #2's
   >2-symbol rule catches this pattern too — re-verified: flipkart.com
   then returned a real "NYKAA Matte to Last..." lipstick at ₹579.
4. **Booking.com matched a Hindi-language budget-slider control**:
   "आपका बजट (प्रति रात) ₹ 200 - ₹ 8,000+" (`Your budget (per night)`) at
   ₹200. Neither prior fix caught it (exactly 2 "₹" symbols, and the
   denylist was English-only). **Fix**: added a regex for the
   "₹X - ₹Y"-shaped range pattern (language-agnostic — matches the digits
   either side of a dash, not the surrounding words) plus "budget"/"बजट"
   to the denylist. Re-verified: booking.com then returned a real listing
   ("मूल कीमत ₹2,981. मौजूदा कीमत ₹1,681." — original/current price copy),
   though see limitation below re: which of the two numbers it picks.
5. **The Product/Hotel toggle's gradient background painted over the
   active tab's text**, making it invisible (both the icon and the word
   "Product"/"Hotel" disappeared on the active tab). Root cause: the
   gradient "thumb" div had `position: absolute` (making it a positioned
   element) while the buttons were `position: static` — per CSS stacking
   rules, positioned elements always paint above static ones regardless of
   DOM order, so the thumb sat on top of the button's own text even though
   the button came later in the DOM. **Fix**: gave `.switch button`
   `position: relative; z-index: 1`. Re-verified visually in the Browser
   pane — both tab labels now render correctly, gradient still animates
   underneath.

## Confirmed, not fixed — genuine site-side blocks

These aren't bugs in this codebase; they're the sites actively detecting
and blocking a headless browser, which this project's rules explicitly
forbid trying to defeat (see `CLAUDE.md` §0.4). Confirmed by direct
Playwright reproduction outside the app, not just observed once:

- **myntra.com, nykaa.com, makemytrip.com, goibibo.com** consistently
  return `net::ERR_HTTP2_PROTOCOL_ERROR` on first navigation attempt — a
  connection-level reset, not a page-level CAPTCHA, consistent with
  anti-bot fingerprinting at the HTTP/2 or TLS layer. Tried disabling
  HTTP/2 client-side (`--disable-http2`) as a diagnostic: the failure mode
  changed to a plain timeout rather than resolving, confirming this is the
  remote side actively rejecting the connection, not a local
  misconfiguration. Left as-is — reported as `error`, not retried with
  evasion techniques.
- **croma.com, ajio.com, agoda.com** intermittently return a detectable
  CAPTCHA/bot-check page (reported as `blocked`), sometimes succeed on a
  later attempt (tatacliq.com behaved this way — blocked in the
  electronics test, succeeded in a later run) — consistent with
  probabilistic bot-scoring rather than a hard IP block.

## Known limitation not (yet) fixed

- **MRP vs. current-price ambiguity survives on some listings.** The
  Booking.com Hindi listing above ("original price ₹2,981. current price
  ₹1,681.") is a genuine listing, correctly passing every filter — but the
  generic heuristic returns the *first* ₹ match in the snippet, which
  here is the original/higher price, not the discounted one actually being
  charged. No fix attempted this pass (would need language-aware "which
  number is the sale price" logic per site); tracked here rather than
  silently accepted. Every reported price already carries a "confirm
  before buying" note for exactly this class of residual risk.
- **Google Hotels aggregator results carry a generic title** (e.g. just
  "Goa"), not a specific property name — it's showing a destination
  starting-price summary rather than one matched hotel. Expected behavior
  for an aggregator, not a bug, but worth knowing before trusting that row
  the same way as a named-hotel result.

## What this means for trust

Every fix above was found by *actually reading the screenshot and the
extracted title against what a human would see on the real page* — not by
assuming the code was right. The same discipline applies going forward:
a new site added to `data/category_sites.yaml` or `data/hotel_sites.yaml`
should get at least one live test run and a look at its screenshot before
being trusted, not just a URL template added to `sites.py` and assumed
correct.
