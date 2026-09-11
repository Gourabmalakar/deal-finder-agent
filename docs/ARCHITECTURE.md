# Architecture

Deal Finder has **two independent implementations of the same idea**,
sharing the same data files. Neither is a wrapper around the other — pick
whichever fits the moment, per `CLAUDE.md` §1.

```
                        data/category_sites.yaml
                        data/hotel_sites.yaml   ◀── hand-maintained site lists,
                        data/price_history.csv       read by BOTH paths below
                        data/evals.db  ◀── shared eval log (§4)
                                │
                ┌───────────────┴───────────────┐
                ▼                               ▼
   PATH A: Claude Code skills          PATH B: local webapp (webapp/)
   (this chat / any Claude Code        (FastAPI + headless Chromium,
    session in this repo)               runs standalone, no Claude needed)
```

## Path A — Claude Code skills

This is the higher-trust path: a real Claude Code session drives the
interactive Browser tool itself, so every match is confirmed by something
that can actually read the page and judge whether it's right.

```
 user message
      │
      ▼
 UserPromptSubmit hook (.claude/hooks/detect_deal_intent.sh)
      │   notices a shopping/hotel URL or hotel-search phrasing,
      │   injects a reminder to follow the matching skill + report contract
      ▼
 ecommerce-deal-finder / hotel-deal-finder skill
      │
      ├──▶ price-scout / hotel-scout subagent (WebSearch + WebFetch only —
      │     no live browser available to a subagent). Returns candidate
      │     URLs + claimed prices: leads, not confirmed answers.
      │
      ▼
 main session verifies live: Browser tool opens each candidate,
 confirms title/variant/price, screenshots it as proof
      │
      ├──▶ data/price_history.csv (append verified price)
      │
      ▼
 deal-evaluator subagent scores the report against evals/criteria.yaml
 before it's shown to the user (CLAUDE.md §6)
      │
      ▼
 ranked report, per docs/REPORT-FORMATS.md
```

**Why the scout subagents don't take screenshots:** a live screenshot needs
the interactive Browser pane, which only exists in the main session of an
interactive Claude Code / desktop-app conversation. A subagent has no such
pane, so it can only do text research — hence the two-phase design:
broad discovery (subagent, cheap, parallel-friendly) then live verification
(main session, the part that actually has to be trustworthy).

## Path B — local webapp

A self-contained FastAPI service that does the same job without any
Claude session in the loop — useful for quick experimentation, or as an
always-on backend once hosted (see "Hosting" below).

```
 browser (you) ──▶ GET /  ──▶ webapp/frontend/index.html (single page,
                               no framework, no build step)
                                    │
                    fetch POST /api/product-search  { query }
                    fetch POST /api/hotel-search     { place, dates, guests }
                                    │
                                    ▼
                          webapp/backend/main.py (FastAPI)
                                    │
                    ┌───────────────┼────────────────────┐
                    ▼               ▼                    ▼
          pick_category()   scraper.run_product_   scraper.run_hotel_
          (category_sites.  search() / run_hotel_   search() — same
           yaml keyword     search() — headless    shape, different
           match)           Chromium (Playwright),  site list
                             one tab per site,       (hotel_sites.yaml)
                             in parallel
                                    │
                                    ▼
                    per site: navigate → screenshot →
                    generic price-extraction heuristic
                    (+ a tuned selector for amazon.in — see below)
                                    │
                    ┌───────────────┴────────────────┐
                    ▼                                 ▼
        data/price_history.csv (append)     evaluator.evaluate_and_log()
                                              → data/evals.db (§4)
                                    │
                                    ▼
                    JSON response → frontend renders ranked
                    cards + screenshots + a disclosed "skipped
                    sites" list
```

### How hotel dates are made trustworthy

A wrong hotel price is visible; a *right* price for the *wrong stay* is
not. It is the one error a user can't catch by eye, so the hotel path is
built around proving dates rather than requesting them.

**Asking properly.** Google Travel ignores plain `checkin`/`checkout`
params outright — three different date ranges (including Christmas)
returned identical prices and an unchanged "Sep 30 - Oct 1" on the page.
Its dates live in a base64url protobuf `ts` param, whose layout was
recovered by harvesting seven real `ts` values out of Google's own
"popular dates" links and decoding them field by field
(`sites.google_travel_ts()`).

**Verifying it worked.** `scraper._page_confirms_dates()` asks each loaded
page which dates it is actually pricing — the site's own check-in/check-out
form fields first (Google fills them "Thu, Dec 24"; Booking exposes them
as `data-testid="date-display-field-start"`), the visible text second. Any
row the page won't confirm is returned as `wrong_dates` and reported under
skipped, never ranked. The evaluator enforces this automatically as
`priced_for_requested_dates`.

This is the general principle worth keeping: **encode what you can, but
believe only what the page says back.** A hand-built date encoding that
silently drifts would produce confidently wrong prices; a self-checking
one degrades to "no result" instead.

### Where the cross-site hotel prices actually come from

Five of the seven OTAs in `data/hotel_sites.yaml` cannot be read directly
(MakeMyTrip, Goibibo and Yatra block automated browsers; Agoda drops the
query; EaseMyTrip only sometimes renders). Their rates still appear,
because Google's property panel lists one row per booking provider with a
nightly price, a stay total and an outbound link — parsed by
`_extract_google_hotel_offers()`.

Only Google's **organic** rows (`/travel/lodging/clk`) are read. Its paid
rows (`/aclk`) look identical in text — both end "Visit site" — but are
per *room type* at a single provider, so keying on the text alone once
filled the results with "Superior Room / Deluxe Room Double / Suite" as if
those were competing booking sites.

Each organic row's `pcurl` parameter holds the provider's own deep link
with the dates in it, so the reported link goes to booking.com or
agoda.com rather than through Google's redirect.

Google answers the same query with **two different layouts**, and both
have to be handled: some names resolve straight to the property panel,
others to an area list view of many hotels with no provider list at all.
On a list view the card whose heading matches the search is followed
through to that hotel's own page. On a city search the cheapest few cards
are each resolved the same way, in parallel, so every row still ends at a
booking site. If no provider list can be reached, the run reports nothing
and says so — a link to a Google search page is never returned as an
answer.

### The scraper's extraction approach

`webapp/backend/scraper.py` opens each site's public **search results**
page (URL templates in `webapp/backend/sites.py`) rather than resolving to
one specific product/hotel page — that resolution step is what Path A's
live human/Claude judgment does and a fixed heuristic can't reliably
replace. Concretely, per site it:

1. Navigates with a real (headless) Chromium tab, one per site, in
   parallel via `asyncio.gather`.
2. Screenshots the page as visual proof, saved under
   `webapp/backend/static/screenshots/<run-id>/` and served at
   `/screenshots/...`.
3. Extracts a price with a **generic heuristic** — scan the DOM for the
   first "₹\<amount\>" that doesn't look like filter/sort chrome (a
   growing denylist: "select all", "sort by", price-range sliders like
   "₹200 - ₹8,000+", snippets with 3+ ₹ symbols) — **except amazon.in**,
   which gets a real selector
   (`div[data-component-type="s-search-result"]`) because the generic
   heuristic reliably grabbed a nav/promo price there in testing (see
   `docs/TESTING.md`).
4. If a product URL was given directly (not a description), `resolve_origin()`
   opens it first to read the *actual* page title before searching other
   sites — so "https://www.flipkart.com/..." searches other sites for the
   real product name, not the URL text itself.
5. Anything that doesn't yield a price is marked `blocked` (bot-check
   detected in the page), `no_match` (page loaded, no listing found), or
   `error` (navigation failed, e.g. the network-level `ERR_HTTP2_PROTOCOL_ERROR`
   several sites return to headless browsers — see `docs/TESTING.md`) —
   never silently dropped, never guessed.

This is deliberately **demo-grade**, not a claim of production accuracy —
see `webapp/README.md`'s "Known limitations" for what that means in
practice and which sites are affected.

## Evaluation system

`evals/criteria.yaml` is the one list both paths score against:

- **Automatic criteria** (has proof, no fabricated price, ranked & capped
  at 5, skipped sites explained, plausible price band) run on *every*
  webapp request with no LLM involved — `webapp/backend/evaluator.py`
  checks them and logs a row to `data/evals.db` (SQLite) before the
  response returns.
- **Judgment criteria** (is this really the same variant/hotel? does this
  specific price actually look right?) need something that can read a
  screenshot or search for context — that's the `deal-evaluator` subagent,
  invoked automatically at the end of every skill run, and on demand over
  the webapp's logged history via `/review-evals`.

`data/evals.db` schema (table `eval_runs`): `id, timestamp, source
('webapp'/'skill'), mode ('product'/'hotel'), query, request_json,
result_json, passed, failures_json, latency_s, subagent_reviewed,
subagent_notes`.

### Benchmarking against aggregators

One eval criterion (`aggregator_benchmark` in `evals/criteria.yaml`) is
worth explaining rather than just listing, because "why isn't this as
good as Google" has a real, non-obvious answer:

- **Hotels are a clean, direct comparison** — Google Hotels is already
  one of the 5 sites this tool checks, so its own displayed price on the
  same run *is* the benchmark. No separate lookup needed.
- **Products mostly aren't a fair 1:1 comparison, and that's worth saying
  plainly rather than implying this tool should match Google exactly.**
  Google Shopping is a **merchant product-feed aggregator** (Google
  Merchant Center) — thousands of sellers proactively submit structured
  product data to it, and Google indexes and ranks that feed. It's not a
  live scraper of a fixed retailer list the way this tool is. Checking
  Google Shopping for "boAt Airdopes 141 earbuds" live (docs/TESTING.md
  Round 4) returned listings almost entirely from small resellers
  ("Gadgets Now", "LowestRate Shopping", "Zepto", "dailydeals365.in",
  "Bharat COD") — none in `data/category_sites.yaml`, and several of
  uneven trustworthiness a serious deal-finder shouldn't casually surface
  anyway. Matching that data source isn't a crawler-quality problem to
  fix; it would mean either licensing Google's own data or scraping a
  constantly-shifting list of small marketplaces instead of major
  retailers — a different tool, not a better-tuned version of this one.
- **The comparison that DOES matter**: when Google Shopping happens to
  list one of this tool's own target sites for the same query, that's a
  fair, direct check — does this tool's result for that site name the
  same specific model/variant, at a price in the same ballpark? Round 4
  found and fixed a real gap this way: this tool was accepting a
  same-brand-but-wrong-model match instead of holding out for an exact
  one, even when the exact model was genuinely available on the same
  page. `webapp/backend/scraper.py`'s `_pick_best_match()` now prefers an
  exact match (brand + any specific model numbers) whenever one exists,
  falling back to the looser match only when it doesn't.

## How real aggregators actually get their data

Worth answering directly, because "your scraper isn't as good as Google"
invites the wrong fix. The honest answer isn't "scrape harder":

- **Google Shopping** is a merchant product-feed aggregator (Google
  Merchant Center) — sellers proactively submit structured product data
  (price, stock, images) to Google on a schedule; Google doesn't scrape
  their pages to get it. That's why it surfaces small resellers this tool
  never will, and why an exact 1:1 comparison usually isn't fair (see
  "Benchmarking against aggregators" above).
- **Google Hotels** and OTAs like **EaseMyTrip, Yatra, Booking.com,
  MakeMyTrip** run on GDS (Global Distribution System) connections and
  direct hotel-chain/channel-manager integrations, or on their own
  first-party inventory — not by rendering competitor sites in a browser.
- **Price-comparison sites and browser extensions** (PriceHistory.app,
  BuyHatke, Honey-style tools) that DO scrape typically do it through
  **official affiliate/partner APIs**: Amazon's Product Advertising API,
  Flipkart's Affiliate API, Booking.com's Affiliate Partner Program, etc.
  These are sanctioned, rate-limited, structured data feeds — the sites
  WANT this traffic because affiliate links earn them a commission — as
  opposed to an unannounced headless browser pretending to be a regular
  visitor, which is what trips the fingerprint detection documented in
  `docs/TESTING.md` (myntra.com, yatra.com, and others).

**What this means for this tool going forward**: the sustainable way to
close the remaining gaps (Myntra, Nykaa, Goibibo, MakeMyTrip, JioMart) is
signing up for these sites' own affiliate/partner programs and using
their real APIs — not a cleverer scraper, and not evasion techniques,
which this project's rules (`CLAUDE.md` §0.4) rule out regardless. That
signup has to be Gourab's own — account creation isn't something this
assistant does on his behalf — but once an API key exists for a given
site, wiring it into `webapp/backend/scraper.py` as an alternative to the
browser-scraping path for that domain is a well-scoped, valuable next
step.

## Uptime monitoring

`webapp/scripts/check_uptime.py` hits `/api/health` (target URL in
`webapp/monitor_config.json`) and exits non-zero on failure.
`webapp/scripts/alert_on_down.sh` wraps it: silent on success, logs to
`webapp/uptime.log` and fires a native notification on failure. A
**local** `launchd` agent (`~/Library/LaunchAgents/com.dealfinder.uptime.plist`)
runs it every 15 minutes — deliberately local rather than a cloud
scheduled task, since a cloud job cannot reach `127.0.0.1`. After hosting
the app publicly, only `monitor_config.json`'s `target_url` needs to
change; the same local job keeps working against the new address.

## Hosting

The webapp needs a real, persistent headless-Chromium process per
request — that rules out classic serverless functions (Vercel's Python/
Node functions have short execution limits and no good way to keep a
browser warm across invocations; getting Chromium running there needs a
special trimmed build like `@sparticuz/chromium` plus careful cold-start
engineering, and even then each request pays a slow Chromium boot). A
plain container host fits this job far better:

- **Render** (recommended, free tier available) — `render.yaml` and
  `webapp/Dockerfile` in this repo deploy it as a Blueprint. Free-tier
  caveats: the service sleeps after 15 minutes idle (a request wakes it in
  ~30-60s), and the filesystem is ephemeral — `data/price_history.csv` and
  `data/evals.db` reset on every redeploy/restart unless a paid persistent
  disk is attached.
- **Fly.io** is a reasonable alternative with a similar container model
  and a free allowance, if Render doesn't fit.

Deploying either one needs an account only the user can create (see the
standing rule against Claude creating accounts) — `webapp/README.md` has
the exact steps once that account exists.
