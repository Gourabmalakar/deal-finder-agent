# Testing report — webapp scraper

## Round 3 — confirming and honestly labeling the sites that block us (2026-09-08)

Gourab asked to "fix the errors" showing up for myntra.com and
nykaafashion.com (generic "Could not load page (Error)"). Investigated
each independently of this app before deciding what, if anything, could
legitimately be fixed:

- **myntra.com**: a plain `curl` request to the same URL succeeds (HTTP
  200), but our headless browser is rejected regardless of HTTP/1.1 or
  HTTP/2 (tested both). This is bot-fingerprint detection specifically
  targeting automated browsers, not a network or protocol issue — the
  only way past it is stealth/evasion techniques (spoofing
  `navigator.webdriver`, faking a GPU/canvas fingerprint, etc.), which
  this project's own rules (CLAUDE.md §0.4) and this assistant's standing
  rules both forbid. **Not fixed, by design.**
- **nykaa.com / nykaafashion.com**: even a plain `curl` gets HTTP 403 —
  blocks every client, not just automation. **Not fixable at all**
  without impersonating a browser in ways this tool won't do.
- **goibibo.com**: even a plain `curl` fails at the HTTP/2 protocol level
  (`CURLE_HTTP2_STREAM_ERROR`) — a server-side issue affecting everyone,
  not specific to headless browsers.

What *was* genuinely fixable: the error message. These domains were
previously reported as a bare `error` with no explanation
("Could not load page (Error)."), indistinguishable from a real,
possibly-fixable bug. **Fix**: a `_KNOWN_BLOCKED_DOMAINS` set (myntra.com,
nykaa.com, nykaafashion.com, goibibo.com, makemytrip.com) now reports
these specifically as `blocked` with an explanation naming the confirmed
cause, instead of a generic error — so a real bug elsewhere doesn't get
mistaken for "oh, that's just blocked", and the user isn't left guessing
why a site never returns anything.

## Round 2 — bugs Gourab found using the app for real (2026-09-08)

Round 1 below was this project's own test pass. Round 2 came from Gourab
actually using the app and hitting two real failures — both were deeper
than they first looked, and fixing them properly surfaced several more
issues along the way. All confirmed fixed by re-running the same real
request afterward, not assumed.

1. **Pasting a real Amazon URL failed with "Something went wrong: [object
   Object]".** Root cause: real Amazon URLs carry long tracking query
   strings that exceeded the backend's 300-character cap on the query
   field, and separately, FastAPI/Pydantic returns validation errors as an
   *array* of `{msg, loc, ...}` objects rather than a string — the
   frontend was stringifying that array directly into the useless
   `[object Object]`. **Fix**: raised the cap to 2000 characters and
   added `errorMessage()` to the frontend to actually read `.msg` from
   each validation error.
2. **Hotel search for a named property ("Hotel Sepoy Grande") returned
   only 1 result, and it was for the wrong hotel** (Google Hotels matched
   "ibis Styles Mysuru" instead). Root cause: the extractor took the
   *first* ₹ price found on a results page without checking it actually
   named the requested property — a real problem on any multi-listing
   results page, not just an occasional miss. Fixing this properly meant
   fixing four separate things:
   - Added `_title_matches()`: a candidate must now share the search's
     distinctive words (capped at 4, so a long title doesn't need to
     match *all* of a short one) **and** specifically its first
     brand/name word, before its price counts as a match for a named
     product or hotel. A bare city name (e.g. "Goa") skips this — there's
     no specific property to confirm against.
   - **Apostrophe/punctuation broke real matches**: "Levis" (as typed)
     doesn't literally appear as a substring of "LEVI'S" (as sites spell
     it) — confirmed live, this alone rejected every genuine Levi's
     match. Fixed by stripping punctuation from both sides before
     comparing.
   - **The "blocked" vs "no_match" check was a false-positive machine**:
     it scanned raw HTML for the substring "captcha", which matches any
     page that merely *embeds* Google's reCAPTCHA widget as routine
     anti-abuse tooling (script URLs contain "recaptcha") — completely
     unrelated to whether the page actually blocked us. Confirmed live: a
     normal, fully-loaded Booking.com results page (correctly showing
     Hotel Sepoy Grande has no availability for the requested dates) got
     mislabeled "blocked". Fixed by scanning visible text only, for actual
     block phrasing ("verify you are a human", "unusual traffic", etc.),
     not script tag contents.
   - **Amazon's own extractor only ever checked the first result card**:
     real Amazon search pages routinely lead with 1-3 sponsored ads for a
     *different brand entirely* (confirmed live: searching "boAt Airdopes
     141" surfaced Noise and GOBOULT ads first) — and separately, some
     card layouts put only the bare brand name in `<h2>` ("boAt", nothing
     else) with the actual full title sitting in the product image's
     `alt` attribute instead. Fixed by scanning every card for one that
     actually names the brand, and reading title from the image `alt`
     text rather than assuming `<h2>` always has it.

   After all four fixes: Google Hotels correctly returned "Hotel Sepoy
   Grande ₹1,406" (matching the page's own visible "GREAT DEAL" badge
   exactly), and Booking/Agoda correctly reported *why* they had nothing
   (no availability for the dates; Agoda's URL template doesn't actually
   pre-fill its search — see limitations below) instead of a wrong price
   or a false "blocked". A regression pass across every Round 1 case
   confirmed no prior fix was undone (`webapp/test_runs/*.json` from this
   session).

## Round 1

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
