# Deal Finder — local test webapp

A real, runnable local web app version of the Deal Finder Agent: a FastAPI
backend that drives a headless Chromium (Playwright) to check each site in
`../data/category_sites.yaml` / `../data/hotel_sites.yaml` live, and a
single-page frontend to search from.

This is a **local test build**, not a production deployment — no auth, no
rate limiting, runs on `127.0.0.1` only. It uses a generic, best-effort price
extraction heuristic (look for the first ₹ amount near the top of a site's
search results) rather than a hand-tuned selector per site, so some results
will be wrong or a site will come back "blocked"/"no match" — that's shown
plainly, never guessed at. See the caveat below on Amazon in particular.

## Run it

```bash
./run.sh
```

First run needs setup (already done once in this environment):

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/playwright install chromium
```

Then open **http://127.0.0.1:8000**.

## How it works

- `backend/main.py` — FastAPI app. `POST /api/product-search` and
  `POST /api/hotel-search` do the real work; `/screenshots/...` serves the
  proof screenshots captured for each site checked.
- `backend/scraper.py` — opens each site's search URL in a real headless
  browser tab (in parallel), screenshots it, and extracts a price with a
  generic heuristic. Every verified result is appended to
  `../data/price_history.csv` — the same log the Claude Code skills use, so
  this webapp and a Claude Code session build the same history.
- `backend/sites.py` — search-URL templates per domain, hand-maintained the
  same way the YAML site lists are.
- `frontend/index.html` — the page itself; no build step, no framework.

## Known limitations (read before trusting a result)

- **No site-specific selectors.** The price extractor just looks for the
  first plausible "₹<amount>" in the page and assumes it belongs to the top
  result. On sites with promotional banners, cashback badges, or "was
  ₹X"-style strikethrough pricing above the real listing, it can grab the
  wrong number — confirmed live in testing (an Amazon result returned a
  banner price, not the product price). **Always click through and confirm
  before buying** — the note under every price says so for a reason.
- **Amazon, Croma, and Tata CLiQ frequently block headless browsers**
  outright (shown as "blocked" rather than a guessed price).
- **Hotel site URL templates are approximate** except Booking.com, which
  supports dates/guests as real query parameters. The others may not filter
  by your exact dates — verify on the linked page.
- This is meant for **local experimentation**, not as a trustworthy
  shopping/booking assistant yet. The Claude Code skills
  (`ecommerce-deal-finder`, `hotel-deal-finder`) do real per-page
  verification with a live browser and are the more reliable path today.
