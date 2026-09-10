# Deal Finder Agent

An AI agent that finds the cheapest **verified** price for a product or a
hotel room, across the sites that actually matter for that category, and
shows its work — live screenshot, exact link, timestamp, price history.

Repo: https://github.com/Gourabmalakar/deal-finder-agent

Full architecture and rules: [`CLAUDE.md`](CLAUDE.md). Deep-dive on both
implementations, the eval system, and hosting:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Exact output format for
both modes: [`docs/REPORT-FORMATS.md`](docs/REPORT-FORMATS.md). Live test
results (bugs found and fixed, confirmed site blocks): [`docs/TESTING.md`](docs/TESTING.md).

## What this is (and isn't)

This is a **Claude Code project**, not a hosted website. Screenshots and
live price verification need a real, interactive browser (the Browser pane
in Claude Code / the Claude desktop app), so this runs as a chat session in
that environment — open this folder there and talk to it. It is not a
background server, has no API of its own, and never places an order or
books anything; every output is a list of links you act on yourself.

## Credentials, and what runs where

There are **two independent paths**, and only one of them involves Claude
at all:

- **The webapp** (`webapp/`) is plain Python — FastAPI plus a headless
  Playwright browser. It calls no LLM, holds no API key, and reads no
  credential of any kind. Anyone can clone this repo and run it; it will
  work with no account, no key and no sign-in.
- **The Claude Code path** (`.claude/`) is markdown: skills, subagents,
  slash commands and one shell hook. Those are *instructions* Claude
  follows in your own session, so they run on whatever Claude account is
  logged in on that machine. Nothing in this repo stores or transmits that
  login — Claude Code keeps its credentials in `~/.claude/`, outside the
  repo entirely. Cloning this repo gives someone the instructions, never
  the account.

Nothing here reads a `.env`, and no secret has ever been committed. The
one file deliberately kept out of version control is
`data/price_history.csv`, which logs what was searched and when.

## Using it

Open this folder in Claude Code (or the Claude desktop app's Code tab),
then either:

- Paste a product URL (Amazon, Flipkart, Myntra, Ajio, Nykaa, Croma, …) or
  describe a product, and ask for the best price — or run
  `/find-deal <url or description>`.
- Give a place or hotel name, travel dates and guest count, and ask for the
  best hotel rate — or run `/find-hotel <place, dates, guests>`. Every rate
  returned was proved to be for *those* dates: the site itself had to be seen
  stating them, or the price is dropped rather than shown.

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
for setup, hosting it publicly (Render), and its known limitations
(best-effort price extraction, not yet as reliable as the Claude Code
skills above). Run it with:

```bash
webapp/run.sh
```

then open http://127.0.0.1:8000. A `launchd` job alerts if it goes down —
see `webapp/README.md`'s "Uptime alerts" section.

## Evaluation

Every search — from the skills or the webapp — is scored against
[`evals/criteria.yaml`](evals/criteria.yaml). The webapp runs the
automatic checks itself and logs every query to `data/evals.db`; the
`deal-evaluator` subagent (`.claude/agents/deal-evaluator.md`) does the
judgment-based checks a fixed rule can't, both at the end of every skill
run and on demand over the webapp's history via `/review-evals`. Full
design in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#evaluation-system).

## Repo layout

```
CLAUDE.md                        overall instructions and rules
docs/ARCHITECTURE.md             both implementations, evals, hosting — the deep dive
docs/REPORT-FORMATS.md           exact output contract for both modes
docs/TESTING.md                  live test pass: bugs found/fixed, confirmed site blocks
evals/criteria.yaml              pass/fail criteria both paths score against
data/category_sites.yaml         top-5 sites per product category
data/hotel_sites.yaml            default hotel/OTA sites checked
data/price_history.csv           append-only log of every price this tool has verified
data/evals.db                    shared SQLite eval log (webapp writes, deal-evaluator reads/writes)
.claude/skills/                  the two workflows (ecommerce, hotel)
.claude/agents/                  price-scout, hotel-scout, deal-evaluator
.claude/hooks/                   intent-detection hook
.claude/commands/                /find-deal, /find-hotel, /review-evals
runs/                            scratch space for screenshots (gitignored)
webapp/                          local FastAPI + Playwright webapp — see webapp/README.md
render.yaml, webapp/Dockerfile   hosting config (Render)
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
