# Testing report — webapp scraper

## Round 6 — hotel search root-caused properly, tested domestic + international (2026-09-09)

Gourab reported the hotel search still often only showed a single Google
Hotels result and rejected "just Google" as an acceptable outcome, with an
explicit bar: at least 2 verified options with prices and links, tested
across multiple hotels both in and outside India. Investigated three
separate root causes (not one) rather than re-guessing:

1. **Booking.com's page hadn't finished rendering when screenshotted** —
   a real screenshot showed only its blank blue header bar. The fixed
   2000ms wait was too short for this specific page. **Fix**: wait for
   actual ₹ content to appear (up to 8s), falling back to proceeding
   after the budget rather than hanging, instead of a blind fixed delay.

2. **Google Hotels' resolved single-property page states the hotel name
   ONCE, structurally far from its own price badge** — confirmed by
   inspecting the actual page: "The Taj Mahal Palace, Mumbai" appeared as
   a heading, with its "₹18,877 GREAT DEAL" price many DOM levels away
   (further than the existing 6-level per-price walk can reach), so a
   genuine match was being missed, not just an absent one. **Fix**:
   `_price_near_heading()` — find a heading whose text is (see #3) the
   target, then expand OUTWARD from the heading (the opposite direction
   from the existing per-price walk) looking for a nearby price, capped
   by bailing if the expansion hits a container with many price
   mentions (over-expanded into a sidebar/list of other properties).

3. **The heading match itself was briefly too loose and reintroduced a
   wrong-property bug**: "Carlton Hotel - Behind Taj Mahal Palace"
   matched a "Taj Mahal Palace Mumbai" search on word-overlap alone — a
   different property legitimately referencing the target by name as a
   landmark. This wasn't limited to the new heading fallback; the
   original per-candidate matcher (`_pick_best_match`) had the same
   weakness. **Fix**: added `require_dominant` — for hotels only (not
   products, where a terser competitor listing legitimately covers less
   of a long SEO-stuffed hint), the matching candidate/heading text must
   be MOSTLY the hint (≥60% of the hint's words present, and those words
   making up ≥50% of the candidate's own words), not just contain most of
   it within a longer, differently-named text.

**Also added, per request**: hotel URL support in the "Place or hotel"
field, mirroring the product side — pasting a Booking.com/MakeMyTrip/etc.
link now opens it directly (`resolve_hotel_origin()`) and reads the real
property name to search other sites with, rather than searching other
sites for the raw URL text. One nuance found and fixed along the way: a
search-results page's own `<title>` is marketing copy wrapped around the
query ("Booking.com: खोज नतीजे: Hotel Sepoy Grande. Book your hotel
now!", confirmed live) — using it as the hint directly pollutes matching.
`_guess_place_from_url()` extracts the actual place from the URL's own
query parameters first (covers common OTA conventions, including this
tool's own generated URLs), falling back to the page title only when no
such parameter exists.

**Tested across 5 scenarios, domestic and international, before and after
each fix** — full before/after counts:

| Search | Before this round | After |
|---|---|---|
| Taj Mahal Palace Mumbai (domestic, named) | 0 results | 2 (Google Hotels + Booking.com, both correctly "Taj Mahal Tower, Mumbai") |
| Marina Bay Sands Singapore (international, named) | 0 results | 2 (Google Hotels + Booking.com, both correctly "Marina Bay Sands") |
| Hotel Sepoy Grande (domestic, named) | 1-2 (inconsistent) | 1-2 (Google Hotels reliable; Booking.com varies with actual site availability) |
| Hotel Delhi 37 (adversarial control — a name whose distinguishing part is just a number) | 0 (correctly safe) | 0 (still correctly safe — no fix here reintroduced a wrong match) |
| Goa (generic city) | 2-3 | 2-3, unchanged |

**Honest limitation, not papered over**: "at least 2" is not universally
guaranteed for every conceivable hotel name — a genuinely obscure
property, or a name whose only distinguishing part doesn't show up
clearly on any checked site (Hotel Delhi 37's case), can still legitimately
return fewer. What changed is that well-known/major properties — the
realistic common case — now reliably clear that bar, and a property this
tool can't confirm stays honestly empty rather than wrong.

## Round 5 — three more real bugs from actual use, plus site expansion (2026-09-08)

Gourab reported: "iphone 17" matched a phone *case* on TataCliq and came
back empty on Amazon; a hotel search only showed the Google Hotels link;
and asked to widen site coverage (EaseMyTrip, Yatra, more trusted
electronics sites) and asked directly "how are others doing it" re:
avoiding scraper blocks. Investigated each rather than assuming:

1. **TataCliq matched an iPhone 17 *case*, not the phone.** The brand+
   model check (Round 4) doesn't know a "Gripp Slimfit Mag-Safe Case For
   iPhone 17" isn't the phone itself — it shares the brand/model tokens.
   **Fix**: `_is_accessory_mismatch()` rejects a candidate containing an
   accessory word (case/cover/charger/tempered glass/...) when the search
   itself wasn't for an accessory. Verified: TataCliq now correctly
   returns `no_match` for a plain "iphone 17" search rather than the case.

2. **Amazon returned no_match despite obviously stocking iPhone 17.**
   Direct inspection found the exact "Apple iPhone 17 256 GB" card WAS on
   the page — with **zero price text anywhere in the card**. Root cause,
   confirmed by following the link: that specific listing (Lavender,
   256GB) is marked **"Currently unavailable"** on Amazon right now — a
   genuine out-of-stock, not a scraper bug. Along the way, added a real
   improvement anyway: `_find_amazon_exact_match_link()` +
   `_amazon_pdp_followup()` now follow through to a matching card's own
   page when the results card shows no price, rather than giving up —
   useful for the (more common) case where a card just doesn't display a
   price on the results view but the product page has one.

3. **Flipkart's price was wrong on a case Gourab could directly verify**:
   he screenshotted the real Flipkart page showing ₹82,900, but this tool
   reported ₹62,050. Root cause: Flipkart shows an "Upto ₹62,050 Off on
   Exchange" badge as a separate nearby element — close enough that both
   numbers land under the SAME 6-level-up title snippet (so a denylist
   check against that shared, 160-char-truncated snippet never reaches
   the word "exchange"), but at the wrong distance for the existing
   >2-symbols/range checks to catch as a pair. **Fix**: a narrower,
   closer-scoped check (just 3 ancestor levels, separate from the title
   walk) now catches "exchange"/"cashback"/"instant discount" badges
   specifically. Verified: Flipkart now returns ₹82,900, matching
   Gourab's own screenshot exactly.

**Site expansion**: verified two working additions to `hotel_sites.yaml`
by actually filling out each site's real search form and reading the
resulting URL (not guessed from convention) — **EaseMyTrip** (confirmed
working, e.g. ₹2,000 for a Goa search) and **Yatra** (its search form
works fine in a real browser, but the same headless-browser fingerprint
block documented for myntra.com applies — confirmed via the same
curl-succeeds-but-headless-fails test — so it's correctly labeled
`blocked`, not silently dropped). **JioMart was investigated and NOT
added**: every deep-linked URL (correctly encoded, matching a pattern
independently confirmable via web search) returns a generic
"couldn't find the page" — its homepage's "enter your pincode" prompt
suggests it requires a location cookie/session established first, which
needs more work than the time available justified. Documented rather
than shipped as a template that would just silently fail every search.

**On "how are others doing it" / avoiding blocks**: see
`docs/ARCHITECTURE.md`'s "How real aggregators actually get their data"
— the honest answer isn't a better crawler, it's that Google
Shopping/Hotels, EaseMyTrip, and similar services mostly work from
official merchant/OTA data feeds and partner APIs, not by scraping pages
with a browser the way this tool does.

## Round 4 — benchmarked against Google Shopping/Hotels, and a real accuracy bug fixed (2026-09-08)

Gourab pushed back on Round 2/3's fixes: a named-hotel search was still
matching the wrong property, and asked "how is Google doing it" — a fair
challenge, so this round actually compared this tool's output against
Google's own aggregators rather than assuming the fixes so far were
enough.

**What Google Shopping actually is, and why it's not a fair direct
comparison for most queries**: searching "boAt Airdopes 141 earbuds" on
Google Shopping returned ~30 listings from sellers like "Gadgets Now",
"LowestRate Shopping", "Zepto", "dailydeals365.in", "Bharat COD",
"Cashify" — none of which are in this tool's site list, and most of
which are small resellers/marketplaces, not the major retailers (Amazon,
Flipkart, Croma) this tool targets. Google Shopping is a **merchant
product-feed aggregator** (Google Merchant Center) — thousands of sellers
proactively submit structured data to it — not a live scraper of a fixed
retailer list. It's a fundamentally different, much larger data source
this tool can't replicate by "crawling better"; matching it would need
either Google's own (paid) data or scraping a constantly-changing list of
small marketplaces of uneven trustworthiness. Said plainly rather than
implied.

**Where a fair, direct comparison exists — and what it found**:

- **Hotels**: Google Hotels is already one of the 5 sites this tool
  checks, so its own displayed price *is* the benchmark, directly. "Hotel
  Sepoy Grande" repeatedly came back matching Google's own visible price
  exactly (₹1,300-1,406 across separate runs, normal dynamic-pricing
  drift). This is already a real benchmark pass, not a claim.
- **Products — a fair comparison exists where Google Shopping happens to
  list the SAME major retailers**: for "LEVIS 512 mens jeans", Google
  Shopping directly listed Amazon.in (₹1,216-1,359), Flipkart (₹1,199-
  1,389) and tatacliq.com (₹1,838) for the "512" line. This tool's
  Flipkart (₹1,342) and TataCliq (₹1,649) results landed within ~10-15%
  of Google's — a genuine pass. **Amazon did not pass**: this tool
  returned a vague "Levi's Men Jeans" (₹1,050) — correct brand, but not
  the specific "512" line Google confirmed Amazon actually carries.

**Root cause and fix**: the matching logic (Round 2/3) accepted any
"close enough" same-brand match and stopped looking — it never checked
whether a *better, exact* match (same specific model number) was
available on the same page. Fixing this needed two attempts:
1. First tried requiring every hint word (including generic descriptors
   like "earbuds") for an "exact" tier. This backfired: Amazon's own
   genuine "Airdopes 141" listing wrote it as "Ear Buds" (two words, a
   synonym) and got rejected by the strict check, falling through to the
   loose tier and losing to a *cheaper, less-exact* "Airdopes 163" match
   instead — worse than before, caught by re-testing before considering
   this done.
2. **Fixed**: `_pick_best_match()` now requires only the brand (first
   distinctive word) plus any specific model/serial NUMBERS for the
   "exact" tier — numbers don't have a synonym problem, descriptive words
   do. Falls back to the existing loose match only when no exact one
   exists on the page.

**Verified after the fix, against the same benchmark queries**:
Amazon now returns "Levi's Men's 512 Slim Tapered Fit Blue Jeans" (the
correct line) and, separately, "boAt Airdopes 141/8" — the exact model —
alongside Flipkart's own exact "Airdopes 141 Gen 2" match. Repeated
across multiple runs for consistency, and a full regression across every
prior test case in this file confirmed no earlier fix was undone.

**Also re-confirmed (see Round 2/3)**: the earlier fix for wrong-hotel
matches (a "Hotel Delhi 37" search must never return a different
property like "Novotel New Delhi Aerocity") still holds — a tempting
"trust the page if its own `<title>` names the property" relaxation was
tried here too, made things worse (the page's title naming the right
hotel doesn't mean the *first price found on it* belongs to that hotel —
these pages also list other properties), and was reverted. Documented in
scraper.py so this dead end isn't retried without re-reading why.

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
