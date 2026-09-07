# Deal Finder Agent

An AI agent that finds the cheapest *verified* price for a product or a hotel
room — across the sites that actually matter for that category — and shows
its work: live screenshot, direct booking/buy link, and price history.

---

## 0. The rules that override everything

1. **This system never transacts.** It never checks out, never places an
   order, never enters payment or login details anywhere, and never creates
   an account on the user's behalf. Every output is a ranked list of links —
   the human clicks "buy" or "book" themselves.
2. **A price without proof is a guess.** Every price reported must be backed
   by a live page the agent actually opened in this session — a screenshot,
   the exact URL, and a timestamp. Never report a number pulled only from
   memory, a cached search snippet, or an aggregator's summary card without
   opening the underlying page.
3. **Unverified is worse than unknown.** If a site couldn't be checked
   (blocked, CAPTCHA, out of stock, region-locked, listing doesn't match the
   product), say so explicitly in the report. A shorter list the agent
   actually confirmed beats a padded one it didn't.
4. **Be a good citizen of the sites it checks.** One page load per candidate,
   no repeated hammering of the same URL, no attempts to bypass bot detection
   or CAPTCHAs (that's a prohibited action for this agent regardless of
   context — see the environment's standing safety rules). If a site blocks
   automated access, skip it and say so.

---

## 1. What this does

| | |
|---|---|
| **E-commerce mode** | User gives a product URL (or a plain description). Agent identifies the product, works out its category, checks that category's top sites, and returns the **5 cheapest verified prices** with exact product-page links, screenshots, and price history. |
| **Hotel mode** | User gives a place, dates, guest count, and optionally links from Booking/MMT/Agoda etc. Agent checks the default hotel sites (plus any links supplied) and returns the **5 cheapest verified rates** for those exact dates/guests, with links and screenshots. |

Both modes are **decision support**, not a booking engine. The output is
always a list Gourab (or whoever is using this) acts on manually.

There are **two ways to run a search**, sharing the same data files:

| | Claude Code skills (§2) | Local webapp (`webapp/`) |
|---|---|---|
| Where | This chat, via the skills/subagents below | `webapp/run.sh` → a browser page at `localhost:8000` |
| Verification | A real person's live Browser tool, per-page | An automated headless Chromium, generic heuristics |
| Reliability | Higher — a human/Claude judges each match | Best-effort — see `webapp/README.md`'s known limitations |
| Use it for | A search you'll actually act on | Quick local experimentation, or as a always-on backend |

Full webapp architecture, endpoints, and the scraping approach are in
**`docs/ARCHITECTURE.md`**; this file stays focused on the skills/agents/
hooks side.

---

## 2. How it's organised

Same pattern as the family-office project: skills are *capabilities*,
subagents are *roles*, hooks are *reflexes*.

```
 user message ("here's a link" / "find me a hotel in Mysuru...")
        │
        ▼
 UserPromptSubmit hook (detect_deal_intent.sh) ── notices shopping/hotel
        │                                          intent, injects the
        │                                          report contract as context
        ▼
 main session ─────────────┬─────────────────────────┐
        │                  ▼                         ▼
        │           price-scout subagent      hotel-scout subagent
        │           (WebSearch candidate       (WebSearch candidate
        │            discovery, no browser)     discovery, no browser)
        │                  │                         │
        └──────────────────┴────────────┬────────────┘
                                         ▼
                     main session verifies top candidates LIVE
                     using the Browser tool: opens each page,
                     confirms title/variant/price, screenshots it
                                         │
                                         ▼
                          price-history.csv logged (this tool's own
                          growing record) + third-party price-history
                          site checked (PriceHistory.app / BuyHatke)
                                         │
                                         ▼
                            ranked report, per docs/REPORT-FORMATS.md
```

**Why subagents don't take the screenshots:** rendering a live page and
capturing a screenshot needs the interactive Browser pane, which is a main
-session capability. Subagents are for the parts that don't need a live
browser — broad candidate discovery over search results — so the two run in
parallel and the main session does only the verification pass, not the whole
search from scratch.

### Skills — `.claude/skills/`

| Skill | Use it when |
|---|---|
| `ecommerce-deal-finder` | User gives a product URL or description and wants the cheapest verified price across sites. |
| `hotel-deal-finder` | User gives a place + dates + guests (± links) and wants the cheapest verified hotel rate. |

### Subagents — `.claude/agents/`

| Subagent | Role |
|---|---|
| `price-scout` | Broad, text-only candidate discovery for a product across the top-5 sites for its category, plus price-aggregator sites. No live browser — returns candidate URLs + claimed prices for the main session to verify. |
| `hotel-scout` | Same, for a hotel across the default OTA list plus any user-supplied links. |
| `deal-evaluator` | Scores a completed search against `evals/criteria.yaml` before it's presented — both live skill runs and rows the webapp logged to `data/evals.db`. See §7. |

### Hooks — `.claude/hooks/`

| Hook | Fires on | Does |
|---|---|---|
| `detect_deal_intent.sh` | `UserPromptSubmit` | Scans the message for shopping-site URLs, hotel-site URLs, or hotel-search phrasing. If matched, injects a reminder to follow the relevant skill and the report contract in `docs/REPORT-FORMATS.md`. |

Registered in `.claude/settings.json`.

### Slash commands — `.claude/commands/`

| Command | Does |
|---|---|
| `/find-deal <url or description>` | Runs the ecommerce-deal-finder skill directly. |
| `/find-hotel <place, dates, guests, [links]>` | Runs the hotel-deal-finder skill directly. |

---

## 3. Data files

- `data/category_sites.yaml` — top-5 sites to check per product category
  (electronics, clothing, footwear, cosmetics, home/furniture, general
  fallback), plus the price-history sites to consult. **Hand-maintained** —
  update this as sites gain/lose relevance; don't let a skill invent new
  ones on the fly without adding them here first.
- `data/hotel_sites.yaml` — the default OTA/aggregator list checked for
  every hotel search.
- `data/price_history.csv` — **append-only** log of every price this agent
  has itself verified: `timestamp, product_or_hotel, site, url, price_inr,
  currency_native, notes`. This is the tool's own price-history record,
  independent of third-party price-history sites, and it only grows more
  useful the more it's used. Never edit past rows, only append.
- `runs/` — scratch space for screenshots captured during a session
  (gitignored). Deliver them to the user via `SendUserFile` or an Artifact;
  don't leave the user hunting for a local path.
- `data/evals.db` — SQLite, **shared** between the webapp (which writes to
  it automatically on every request) and the `deal-evaluator` subagent
  (which reads it, and writes its own qualitative verdicts back). See §6.

---

## 4. Report contract

Exact section-by-section formats for both modes are in
**`docs/REPORT-FORMATS.md`**. Follow it — a report missing a required field
(link, price, timestamp, or screenshot) is an incomplete report, not a
finished one.

---

## 5. Working style

- **Confirm the target before searching.** For e-commerce: if a plain
  description is ambiguous (which color, storage, size), ask — a wrong
  variant match is worse than a slower search. For hotels: dates and guest
  count are required inputs, not optional context; ask if either is missing.
- **Show the reasoning, not just the number.** Cheapest isn't always best —
  note when a lower price has a catch (no free cancellation, marketplace
  seller vs. official store, different variant, price valid only with a
  card offer). Flag it rather than silently picking the lowest number.
- **Currency.** Report in INR by default (this is an India-first tool per
  the site lists). If a site's native price is in another currency, show
  both and say which FX rate/timestamp was used.
- **Prices are point-in-time.** Always timestamp every reported price and
  say so plainly — a price checked an hour ago is not the same as a price
  checked now, especially for flash sales and dynamic hotel pricing.

---

## 6. Evaluation

Every search is checked, not just produced. `evals/criteria.yaml` is the
one list of pass/fail criteria both paths score against:

- The **webapp** runs the automatic (rule-based) criteria itself on every
  request (`webapp/backend/evaluator.py`) and logs the verdict to
  `data/evals.db` before returning a response — this needs no LLM call, so
  it happens on literally every query with no extra cost.
- The **`deal-evaluator` subagent** does the judgment-based checks a fixed
  rule can't (is this really the same variant/property? does a price look
  plausible for this specific item?) — invoked automatically at the end of
  every skill run (§2 step 6/7), and on demand over the webapp's logged
  history via `/review-evals`.

Run `/review-evals` periodically to have it sweep unreviewed webapp rows —
it writes verdicts back into `data/evals.db` and surfaces any repeating
pattern (e.g. one site consistently mis-parsed) worth fixing at the source
rather than patching row by row.

---

## 7. Uptime monitoring

`webapp/scripts/check_uptime.py` hits `/api/health` and exits non-zero if
the webapp doesn't respond. A scheduled task runs it periodically and
alerts if it fails — see `webapp/README.md` for the current target URL
(local vs. hosted) and how to change it after deploying. This checks that
the process is *up*, not that search results are good — that's what §6 is
for.
