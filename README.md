# Deal Finder Agent

An AI agent that finds the cheapest **verified** price for a product or a
hotel room, across the sites that actually matter for that category, and
shows its work — live screenshot, exact link, timestamp, price history.

Full architecture and rules: [`CLAUDE.md`](CLAUDE.md). Exact output format
for both modes: [`docs/REPORT-FORMATS.md`](docs/REPORT-FORMATS.md).

## What this is (and isn't)

This is a **Claude Code project**, not a hosted website. Screenshots and
live price verification need a real, interactive browser (the Browser pane
in Claude Code / the Claude desktop app), so this runs as a chat session in
that environment — open this folder there and talk to it. It is not a
background server, has no API of its own, and never places an order or
books anything; every output is a list of links you act on yourself.

## Using it

Open this folder in Claude Code (or the Claude desktop app's Code tab),
then either:

- Paste a product URL (Amazon, Flipkart, Myntra, Ajio, Nykaa, Croma, …) or
  describe a product, and ask for the best price — or run
  `/find-deal <url or description>`.
- Give a place, travel dates, and guest count (optionally with links from
  Booking.com / MakeMyTrip / Agoda / Goibibo), and ask for the best hotel
  rate — or run `/find-hotel <place, dates, guests, links>`.

A hook (`.claude/hooks/detect_deal_intent.sh`) notices shopping/hotel intent
in your message automatically and reminds Claude to follow the right skill
and report contract, so you don't need to name the skill or command
yourself most of the time — just describe what you're looking for.

## How it works

```
your message
     │
     ▼
hook notices shopping/hotel intent
     │
     ▼
skill (ecommerce-deal-finder / hotel-deal-finder) drives the workflow
     │
     ├──▶ scout subagent (price-scout / hotel-scout): broad WebSearch
     │     candidate discovery — leads, not confirmed prices
     │
     ▼
main session verifies the top candidates LIVE in the Browser pane:
opens each page, confirms it's really the same product/hotel, reads
the real price, takes a screenshot
     │
     ▼
verified price logged to data/price_history.csv (this tool's own
growing record) + a third-party price-history site checked
     │
     ▼
ranked report: 5 cheapest verified prices, links, screenshots, history
```

See [`CLAUDE.md`](CLAUDE.md) §2 for why the scout subagents don't take
screenshots themselves (they don't have a live browser — only the main
session's Browser tool does).

## Local test webapp

`webapp/` is a real, runnable web app version — a FastAPI backend that
drives a headless browser to check sites live, plus a frontend page — for
testing outside of Claude Code. See [`webapp/README.md`](webapp/README.md)
for setup and its known limitations (best-effort price extraction, not yet
as reliable as the Claude Code skills above). Run it with:

```bash
webapp/run.sh
```

then open http://127.0.0.1:8000.

## Repo layout

```
CLAUDE.md                        overall instructions and rules
docs/REPORT-FORMATS.md           exact output contract for both modes
data/category_sites.yaml         top-5 sites per product category
data/hotel_sites.yaml            default hotel/OTA sites checked
data/price_history.csv           append-only log of every price this tool has verified
.claude/skills/                  the two workflows (ecommerce, hotel)
.claude/agents/                  the two scout subagents
.claude/hooks/                   intent-detection hook
.claude/commands/                /find-deal and /find-hotel
runs/                            scratch space for screenshots (gitignored)
```

## Maintaining the site lists

`data/category_sites.yaml` and `data/hotel_sites.yaml` are hand-maintained
on purpose — a skill should never invent a new site to check mid-search.
Update these files as sites gain or lose relevance, and add a category if a
new kind of product keeps showing up without a good match.

## Rules this agent follows

See [`CLAUDE.md`](CLAUDE.md) §0 in full, in short:
1. It never transacts — no checkout, no payment details, no accounts.
2. A price without a live screenshot and a timestamp isn't reported.
3. A site it couldn't check is disclosed as skipped, never guessed at.
4. It's a good citizen of the sites it checks — no CAPTCHA bypassing, no
   hammering the same page repeatedly.
