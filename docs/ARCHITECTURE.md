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
