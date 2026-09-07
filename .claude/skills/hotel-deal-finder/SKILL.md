---
name: hotel-deal-finder
description: Find the cheapest verified hotel rate for a place, dates, and guest count across the top booking sites, with live screenshots. Use when the user asks to compare hotel prices, gives a place plus travel dates, or shares links from Booking.com, MakeMyTrip, Agoda, Goibibo, etc.
---

# Hotel deal finder

Goal: turn a place + dates + guest count (optionally with specific hotel
links) into the **5 cheapest verified rates**, each with proof — screenshot,
exact booking link, timestamp. Follow `docs/REPORT-FORMATS.md` for the exact
output shape.

## Steps

### 1. Confirm the search
Required inputs — **ask if any are missing, don't assume them**:
- Place / city (and specific hotel name, if the user has one in mind).
- Check-in and check-out dates.
- Number of guests (and rooms, if more than one).

If the user supplied one or more direct links (a specific Booking.com,
MakeMyTrip, or Agoda hotel page), note the exact hotel(s) they name — those
are the target properties, not just examples.

### 2. Pick the site list
- Read `data/hotel_sites.yaml` for the default OTA/aggregator list.
- If the user gave direct links, those come first (check the *exact* hotel
  and dates they linked); fill remaining slots from the default list for
  the same hotel and dates.
- If the user didn't name a specific hotel, use Google Hotels first as an
  overview of options for the place, then verify the cheapest few live
  through the individual OTAs.

### 3. Discover candidates
- Spawn the `hotel-scout` subagent with: place, hotel name (if any), dates,
  guest count, and the site list. It does text-only research
  (WebSearch/WebFetch) and returns candidate hotel pages with claimed
  rates — unverified leads, not final answers.

### 4. Verify live, with screenshots
For each candidate (aim for 5 confirmed rates):
- `navigate` to the exact hotel page on that site with the Browser tool,
  with the dates and guest count set (via the site's own date/guest
  picker if the URL doesn't already encode them).
- Confirm via `read_page`/`get_page_text` that it's the same property (not
  a similarly-named one), the dates match, and read off the total price for
  the stay, the per-night rate, room type, and cancellation policy.
- Screenshot the page (`computer` action `screenshot`, or `zoom` on the
  price/room-type area). Save under `runs/<place-slug>/<site>.png`.
- If a site shows no availability for those dates/guests, or blocks
  automated access: skip it and note why — don't guess a rate.

### 5. Log and compile
- Append one row per verified rate to `data/price_history.csv`
  (`item_type=hotel`) via Bash — append-only.
- Rank confirmed rates ascending by total price for the stay, keep the
  cheapest 5 (fewer is fine — never pad with a guess).
- Follow `docs/REPORT-FORMATS.md` exactly: lead-in, ranked table,
  screenshots, caveats.
- Deliver screenshots via `SendUserFile`, or publish an Artifact with the
  table and images (load `artifact-capabilities` first if using its asset
  store for the images).

### 6. Evaluate before presenting
- Spawn the `deal-evaluator` subagent with the compiled report and the raw
  `hotel-scout` output. Wait for its verdict before showing anything to the
  user. If it flags a failure (e.g. wrong property, dates don't match),
  fix what you can before presenting rather than reporting a known-bad row.

## Guardrails
- Never start a booking, never enter payment/traveler details, never create
  an account. This skill only reads public availability/price pages.
- Never invent a rate — every number must come from a page actually opened
  in this session, for the exact dates and guest count asked for.
- Respect the sites: one page load per candidate, no repeated hammering, no
  CAPTCHA bypass attempts — skip and disclose instead.
