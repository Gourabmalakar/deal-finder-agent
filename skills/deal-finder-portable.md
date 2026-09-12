---
name: deal-finder
description: >
  Find the cheapest verified price for a product, or the cheapest verified
  rate for a hotel on specific dates, across the sites that actually matter —
  with proof (screenshot/page read, exact link, timestamp) for every price
  reported. Use when asked for the best price, cheapest option, a price
  comparison, or a hotel rate for given dates and guests.
---

# Deal Finder — portable skill

This is a **self-contained** copy of the deal-finder methodology used in the
[Deal Finder Agent](https://github.com/Gourabmalakar/deal-finder-agent) repo.
Unlike the two skills under `.claude/skills/` there, it does not assume this
repo's subagents (`price-scout`, `hotel-scout`, `deal-evaluator`) or data
files exist — everything it needs is inline below, so it works standalone in
any Claude session, and (as plain instructions) in any other LLM that can
browse or search the web.

## How to use this file

- **Claude Code / Claude Desktop, this repo:** already present; nothing to
  do — the two specialised skills in `.claude/skills/` are more thorough for
  this repo specifically (they split discovery and verification across
  subagents and log to `data/evals.db`). Use this file instead when you want
  the same behaviour somewhere those don't exist.
- **Claude Code / Claude Desktop, a different project:** copy this file to
  that project's `.claude/skills/deal-finder/SKILL.md`. It will be picked up
  automatically when a message matches its `description` above.
- **Claude Projects / a custom Claude with persistent instructions:** paste
  this whole file into the project's custom instructions or knowledge.
- **Any other LLM (ChatGPT, Gemini, etc.), one-off:** paste this whole file
  as your first message, then follow it with your actual request (a product
  URL/description, or a place + dates + guest count).
- **An LLM with no live browsing/search tool at all:** it cannot follow §5
  below (nothing can be verified), so it must say so plainly and offer
  unverified candidate links instead of prices — see the "No browsing tool"
  note in §5.

---

## 0. Rules that override everything

1. **Never transact.** Never add to cart, check out, enter payment or login
   details, or create an account. Every output is a list of links a human
   acts on themselves.
2. **A price without proof is a guess.** Report a price only after actually
   opening the page (or, for a text-only tool, after reading a search
   result you clearly attribute) in *this* session — never from memory or
   a cached snippet presented as current. Note the exact source and when it
   was checked.
3. **Unverified is worse than unknown.** If a site can't be checked
   (blocked, CAPTCHA, out of stock, no matching listing), say so — don't
   guess a number. **For hotels this is stricter:** report a rate only if
   the page itself stated the requested check-in/check-out dates (its own
   date fields, or its visible text). A price for the wrong stay is a wrong
   answer, not a cheaper one, and looks identical to a correct one unless
   checked — many sites (Google among them) silently ignore date parameters
   in a URL and quote their own default dates instead.
4. **Be a good citizen of every site checked.** One page load per candidate,
   no repeated hits on the same URL, and never attempt to bypass a CAPTCHA
   or bot-detection wall — skip that site and disclose it instead.

---

## 1. The two modes

| | Trigger | Output |
|---|---|---|
| **Product** | A product URL, or a plain description ("cheapest 65-inch OLED TV") | The 5 cheapest **verified** prices across the sites that matter for that category, each with source, link, price, timestamp |
| **Hotel** | A place or hotel name + check-in/check-out dates + guest count | The 5 cheapest **verified** rates for *those exact dates*, each with source, booking link, price, timestamp |

---

## 2. Required inputs — ask if missing, never assume

- **Product:** if given only a vague description (which color, size,
  storage?), ask before searching — a confidently wrong variant is worse
  than a slower search.
- **Hotel:** place/hotel name, check-in date, check-out date, and guest
  count are all required. Ask for whichever is missing.

---

## 3. Default site lists

Use these unless the user names specific sites, or you're running inside
the parent repo (there, prefer `data/category_sites.yaml` and
`data/hotel_sites.yaml` — they're hand-maintained and may be more current).

**Product, by category** (first category whose keywords match the product
wins; else use general):

| Category | Keywords (sample) | Sites |
|---|---|---|
| Electronics | mobile, laptop, TV, camera, headphone, earbud, smartwatch, tablet, monitor, appliance | amazon.in, flipkart.com, croma.com, reliancedigital.in, tatacliq.com |
| Clothing/fashion | shirt, jeans, dress, kurta, saree, jacket, apparel | myntra.com, ajio.com, amazon.in, flipkart.com, tatacliq.com |
| Footwear | shoes, sneaker, sandal, heels, boots | myntra.com, ajio.com, amazon.in, flipkart.com, nykaafashion.com |
| Cosmetics/beauty | makeup, skincare, perfume, shampoo, haircare | nykaa.com, amazon.in, purplle.com, myntra.com, flipkart.com |
| Home/furniture | sofa, furniture, mattress, cookware, decor | amazon.in, flipkart.com, pepperfry.com, urbanladder.com, tatacliq.com |
| General (fallback) | — | amazon.in, flipkart.com, tatacliq.com, myntra.com, snapdeal.com |

Also check a price-history site if one is reachable (e.g. pricehistory.app,
buyhatke.com) for high/average/low context — this is supporting context,
not a substitute for a live price.

Outside India, substitute the equivalent large marketplaces/category sites
for the user's country/category — the *approach* (category → top sites →
verify each live) matters more than this specific India-first list.

**Hotel, default OTAs to check:** Google Hotels (fast overview, but confirm
its rate elsewhere too — it doesn't always show the true lowest fenced
rate), Booking.com, MakeMyTrip, Agoda, Goibibo, EaseMyTrip, Yatra. If the
user supplied direct links, those name the exact property — check them
first, then fill remaining slots from this list for the same hotel/dates.
Some OTAs actively block automated browsers or won't confirm dates in a
URL (Expedia and Hotels.com commonly show a bot-check; Cleartrip often
shows a price with no stated dates) — if a site behaves this way, skip it
and say so rather than forcing a result.

---

## 4. Workflow

### Step 1 — Identify the target
- Product URL given: open it, extract exact title/brand/variant — this is
  what every other site's listing must match, not just resemble.
- Product description only: use it as the query; ask first if the variant
  is ambiguous.
- Hotel: note the exact place/property name, dates, and guest count as
  given — these do not get "interpreted," only matched exactly.

### Step 2 — Find candidates on each site
For each site in the relevant list: search (web search, or the site's own
search) for the specific product/hotel — not the site's homepage or a
category page. Note the candidate URL and whatever price/rate is claimed
at this stage. Treat this as a **lead**, not a confirmed number, until
step 3.

### Step 3 — Verify live, with proof
For each candidate (aim for 5 confirmed results):
- Open the exact page with a live browsing tool.
- Confirm it's genuinely the same product/variant, or the same hotel
  property — reject a similarly-named substitute rather than reporting its
  price as if it matched.
- **Hotel-specific:** confirm the page's own date fields (or its visible
  text) state the requested check-in/check-out — not just that the URL
  contains them, since some sites ignore URL date parameters and quote a
  default stay instead. If it won't confirm, the rate does not get
  reported, no matter how promising it looked in step 2.
- Read the price/rate directly off the page. Capture proof: a screenshot
  if the tool supports it, otherwise a verbatim quote of the price with
  its exact source URL.
- If a site is blocked, CAPTCHA'd, out of stock, or shows no matching
  listing/no confirmable dates: skip it and record why. Don't guess.

**No browsing tool available:** if the environment has only text search
with no way to open and read a live page, this step cannot be completed —
say so explicitly, then offer the step-2 candidate links as **unverified
leads for the user to check themselves**, clearly labeled as such. Do not
present a search-result snippet's number as a confirmed price.

### Step 4 — Rank and report
- Sort confirmed results ascending by price (product) or total stay price
  (hotel). Keep the cheapest 5 — fewer is fine if fewer were confirmed;
  never pad with a guess to reach 5.
- Use the report format in §6.

### Step 5 — Self-check before presenting
Run the checklist in §7 against your own output before showing it. Fix
what you can (drop a bad row, re-verify, add a missing caveat) rather than
presenting something you can see fails a check.

---

## 5. Output format

**Lead-in:** one line stating what was searched (product name/variant, or
hotel + exact dates + guests) and when it was checked.

**Ranked table**, cheapest first, each row:
`# | Site | Price (with currency) | Link | ✓ verified / reason skipped`

For hotels, also show the per-night and total-stay figures, and state the
date range in the lead-in — don't make the reader infer it from a link.

**Skipped sites:** listed separately with a one-line reason each (blocked,
CAPTCHA, no match, dates not confirmable) — never silently dropped.

**Caveats:** anything that affects whether "cheapest" is actually "best" —
no free cancellation, marketplace seller vs. official store, price only
valid with a specific card/offer, different room type, etc.

**Timestamp:** state when the check was run. A price from an hour ago is
not the same as a price right now, especially for flash sales and dynamic
hotel pricing.

---

## 6. Self-check before presenting (inline evaluator)

- [ ] **has_proof** — every reported price traces to a page actually opened
      this session (screenshot, or a quoted read of it), not a remembered
      or assumed number.
- [ ] **no_fabricated_price** — nothing is reported as a price without an
      actual number found on a real page.
- [ ] **ranked_ascending**, capped at 5.
- [ ] **skipped_sites_explained** — every non-reported site from the
      candidate list has a stated reason.
- [ ] **timestamped**.
- [ ] **plausible_price** — does each number look like an actual product/
      room price, not a filter boundary, promo badge, or unrelated figure
      picked up by mistake?
- [ ] **variant_match / property_match** — is this genuinely the same
      product/variant, or the same hotel, asked about?
- [ ] **(hotel only) priced_for_requested_dates** — was every rate proved
      against the page's own stated dates, not just assumed from the URL?
- [ ] **no_prohibited_action** — nothing added to a cart, no form
      submitted, no login attempted, no CAPTCHA bypass attempted.

---

## 7. Relationship to the parent repo

If you're running inside the `deal-finder-agent` repo itself, the two
purpose-built skills (`.claude/skills/ecommerce-deal-finder/SKILL.md`,
`.claude/skills/hotel-deal-finder/SKILL.md`) are the more thorough path —
they split broad discovery (`price-scout`/`hotel-scout` subagents) from
live verification (main session), log every result to
`data/price_history.csv`, and hand off to the `deal-evaluator` subagent
for an independent pass against `evals/criteria.yaml`. Use this file when
you want the same rules and workflow somewhere that repo's subagents and
data files don't exist.
