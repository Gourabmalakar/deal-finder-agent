# Deal Finder — local test webapp

A real, runnable local web app version of the Deal Finder Agent: a FastAPI
backend that drives a headless Chromium (Playwright) to check each site in
`../data/category_sites.yaml` / `../data/hotel_sites.yaml` live, and a
single-page frontend to search from.

This is a **local test build**, not a production deployment — no auth, no
rate limiting, runs on `127.0.0.1` only (until hosted — see "Hosting"
below). It uses a generic, best-effort price extraction heuristic (look
for the first ₹ amount near the top of a site's search results, skipping
filter/sort chrome) for most sites, plus a tuned selector for amazon.in
specifically. Some results will still be wrong or a site will come back
"blocked"/"no match" — that's shown plainly, never guessed at. See
`../docs/TESTING.md` for the live test pass this build has actually been
through, including bugs found and fixed.

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
- `backend/evaluator.py` — runs automatically on every search: checks the
  response against `../evals/criteria.yaml`'s automatic criteria and logs
  a row to `../data/evals.db`. See `GET /api/evals` to inspect recent runs,
  and `../docs/ARCHITECTURE.md` for the full eval design (the webapp does
  the rule-based half; the `deal-evaluator` Claude subagent does the
  judgment-based half over the same log via `/review-evals`).
- `frontend/index.html` — the page itself; no build step, no framework.

## Hosting it publicly

`Dockerfile` (repo root context — see the comment at its top) and
`../render.yaml` deploy this as a container on **Render**
(https://render.com), recommended over Vercel because the app needs a real,
persistent headless-Chromium process per request — not a good fit for
classic serverless functions (short execution limits, no easy way to keep
a browser warm; see `../docs/ARCHITECTURE.md`'s "Hosting" section for why).

Steps (needs your own Render account — account creation and login aren't
something this assistant can do for you):
1. Push this repo to GitHub (already done if you're reading this from
   `https://github.com/Gourabmalakar/deal-finder-agent`).
2. On https://render.com: **New +** → **Blueprint**, connect the repo. It
   reads `render.yaml` and provisions the service automatically.
3. First deploy takes a few minutes (Playwright's base image is large).
   Once live, the URL is `https://<service-name>.onrender.com`.
4. Update `monitor_config.json`'s `target_url` to
   `https://<service-name>.onrender.com/api/health` so the local uptime
   watcher (below) checks the real deployment.

**Free-tier caveats**: the service sleeps after 15 minutes idle (a request
wakes it in ~30-60s), and the filesystem is ephemeral — `data/price_history.csv`
and `data/evals.db` reset on every redeploy/restart unless you add a paid
persistent disk.

## Uptime alerts

A `launchd` user agent (`~/Library/LaunchAgents/com.dealfinder.uptime.plist`)
runs `scripts/alert_on_down.sh` every 15 minutes. It's silent when the app
is healthy; when `/api/health` fails, it appends to `webapp/uptime.log`
and fires a native macOS notification. This runs locally (not a cloud
schedule) because it needs to reach whatever's actually running — today
that's `127.0.0.1`, which a cloud job can never see.

- **Check it's running**: `launchctl list | grep dealfinder`
- **Run a check right now**: `webapp/scripts/check_uptime.py`
- **After hosting the app publicly**: edit `webapp/monitor_config.json`'s
  `target_url` to the hosted `/api/health` URL — no other change needed,
  the same local job keeps working against the new address.
- **Stop it**: `launchctl unload ~/Library/LaunchAgents/com.dealfinder.uptime.plist`

## Known limitations (read before trusting a result)

Full detail and repro steps in `../docs/TESTING.md`; in short:

- **Still mostly a generic heuristic**, with real fixes for the failure
  modes actually observed live (Amazon's own selector; filter/sort/budget-
  range chrome denylisted). It can still grab the *wrong one of two real
  prices* on a listing that shows both an original and a discounted price
  in the same snippet — confirmed on a Booking.com result. **Always click
  through and confirm before buying**, which is why every result carries
  that note.
- **myntra.com, nykaa.com, makemytrip.com, and goibibo.com consistently
  reject headless-browser connections** at the network level
  (`ERR_HTTP2_PROTOCOL_ERROR`) — confirmed by direct reproduction, not a
  one-off. Reported as `error`, never retried with evasion. croma.com,
  ajio.com, and agoda.com show a detectable bot-check intermittently
  (reported as `blocked`).
- **Hotel site URL templates are approximate** except Booking.com, which
  supports dates/guests as real query parameters. The others may not filter
  by your exact dates — verify on the linked page. Google Hotels results
  can carry a generic destination-level title rather than one specific
  property (it's an aggregator's summary, not a bug).
- This is meant for **local experimentation and quick checks**, not yet as
  reliable as the Claude Code skills (`ecommerce-deal-finder`,
  `hotel-deal-finder`), which do real per-page judgment with a live
  browser and a human/Claude actually reading the result.
