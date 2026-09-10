"""
Best-effort live scraper used by the local test webapp.

This is a demo-grade scraper: it opens each site's public search page in a
real (headless) browser, takes a screenshot as proof, and tries to pull out
a plausible price with a generic heuristic (look for the first "₹<amount>"
near the top of the results). It does not use site-specific selectors for
every domain, so accuracy varies — a site that changes its markup, blocks
automated browsers, or shows a CAPTCHA will come back as "blocked" or
"no_match" rather than a guessed number. That's deliberate: an unverified
price is worse than a visibly missing one (see CLAUDE.md rule 2).

Nothing here logs in, fills a cart, or submits a form — it only opens
public search/listing pages.
"""
from __future__ import annotations

import asyncio
import csv
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote_plus

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from sites import product_search_url, hotel_search_url

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
DATA_DIR = ROOT / "data"
SCREENSHOT_DIR = Path(__file__).resolve().parent / "static" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

PRICE_RE = re.compile(r"₹\s?([\d][\d,]{2,})")
NAV_TIMEOUT_MS = 20_000
PER_SITE_BUDGET_S = 25

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


async def _new_context(browser, locale: str | None = None):
    """One place to create browser contexts.

    Hotel searches pass locale="en-IN"; product searches deliberately pass
    nothing, and the asymmetry is load-bearing in both directions.

    Hotels need it because Booking.com geo-detects and served its entire
    results page in Hindi ("गुरु. 24 दिसं."), which left the date check
    unable to read Booking's own dates and dropped a correctly-priced site
    as though it had ignored the request. (Its Hindi budget slider had
    already caused a separate misread earlier.)

    Products must NOT have it: amazon.in answers a search URL with a file
    download rather than a page whenever any explicit locale is set —
    "Page.goto: Download is starting", tested identically for en-IN, en-GB
    and en-US, and gone the moment the option is dropped. Setting one
    globally turned every Amazon check into an error."""
    kwargs = {"user_agent": UA, "viewport": {"width": 1280, "height": 900}}
    if locale:
        kwargs["locale"] = locale
    return await browser.new_context(**kwargs)


def _clean_price(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _extract_amazon_result(html: str, title_hint: str | None = None,
                             hint_stopwords: set[str] = frozenset(),
                             require_hint_match: bool = False,
                             ) -> tuple[float | None, str | None, str | None]:
    """Amazon's search-result markup (data-component-type="s-search-result")
    has been stable for years, and the generic heuristic below reliably
    grabbed the wrong number on amazon.in in testing (a nav/promo "under
    ₹500" link, not the listing price) — worth a real selector for the
    single most important site in the list, unlike the rest which stay
    generic. Scans EVERY card rather than just the first: real Amazon
    results routinely lead with 1-3 sponsored ads for a *different* brand
    entirely (confirmed live: searching "boAt Airdopes 141" surfaced Noise
    and GOBOULT ads before any boAt listing) — taking the first card
    unconditionally would report a competitor's price as if it were the
    searched-for product."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[tuple[float, str | None, str | None]] = []
    for card in soup.select('div[data-component-type="s-search-result"]'):
        price_el = card.select_one("span.a-price span.a-offscreen") or card.select_one("span.a-price-whole")
        if not price_el:
            continue
        m = re.search(r"([\d][\d,]{2,})", price_el.get_text(strip=True))
        if not m:
            continue
        price = _clean_price(m.group(1))
        if price is None:
            continue
        # h2's own text can be just the brand name alone on some card
        # layouts (confirmed live: a real boAt result's <h2> contained only
        # "boAt", nothing else — every hint-word check then failed even
        # though it was the right product). The product image's alt text
        # reliably carries the FULL descriptive title regardless of card
        # layout, so prefer that; fall back to h2 for the rare card with
        # no image alt.
        img_el = card.select_one("img[alt]")
        title = img_el.get("alt") if img_el else None
        if not title:
            title_el = card.select_one("h2 a span") or card.select_one("h2 span")
            title = title_el.get_text(strip=True) if title_el else None
        # Prefer a real product-page link (contains "/dp/") over h2's own
        # anchor, which can be empty on the same card layout.
        link_el = card.select_one('a.a-link-normal[href*="/dp/"]') or card.select_one("h2 a") \
            or card.select_one("a.a-link-normal")
        link = link_el.get("href") if link_el else None
        candidates.append((price, link, title))

    if not candidates:
        return None, None, None
    if require_hint_match and title_hint:
        best = _pick_best_match(candidates, title_hint, hint_stopwords)
        return best if best else (None, None, None)
    return candidates[0]


# Domains confirmed (independently of this app — see docs/TESTING.md) to
# block automated browser connections outright: myntra.com and yatra.com
# reject our headless Chromium specifically while a plain `curl` to the
# same URL succeeds (bot-fingerprint detection, not a network issue — not
# something this tool will try to evade); nykaa.com/nykaafashion.com
# return HTTP 403 to curl too (blocks everyone); goibibo.com/makemytrip.com
# fail even a plain curl at the HTTP/2 protocol level (a server-side
# issue, not specific to us). Distinguishing this from a genuine transient
# failure means a real bug elsewhere doesn't get dismissed as "oh, that's
# just blocked" — and the user sees why, not a bare "(Error)".
_KNOWN_BLOCKED_DOMAINS = {"myntra.com", "nykaa.com", "nykaafashion.com",
                           "goibibo.com", "makemytrip.com", "yatra.com"}
_KNOWN_BLOCKED_NOTE = ("This site consistently blocks automated browser connections — "
                        "confirmed independently of this tool (see docs/TESTING.md). "
                        "Not something fixable without bypassing bot detection, which "
                        "this tool won't do. Check the site directly.")

_HOTEL_STOPWORDS = {"hotel", "hotels", "resort", "the", "inn", "suites", "and", "a", "an"}
_HOTEL_NAME_HINTS = ("hotel", "resort", "inn", "suites", "villa", "palace", "residency")
_PRODUCT_STOPWORDS = {"buy", "online", "best", "price", "for", "with", "the", "and",
                       "in", "india", "at", "shop", "shopping", "a", "an"}
_ACCESSORY_WORDS = {"case", "cover", "protector", "tempered", "glass", "skin",
                     "pouch", "strap", "charger", "cable", "screenguard",
                     "bumper", "holder", "stand", "sticker", "adapter"}


def _is_accessory_mismatch(title: str, hint: str) -> bool:
    """True when `title` is an accessory (case/cover/charger/...) FOR the
    thing being searched, but the search itself wasn't for an accessory.
    Confirmed live: searching plain "iphone 17" matched a TataCliq listing
    for a "Gripp Slimfit Mag-Safe Case For iPhone 17 Back Cover" ahead of
    the phone itself — the brand+model check alone can't tell a case isn't
    the device. Only rejects when the hint itself doesn't ALSO ask for an
    accessory (so "iphone 17 case" still matches normally)."""
    hit = _ACCESSORY_WORDS & set(_tokenize(title))
    return bool(hit) and not (_ACCESSORY_WORDS & set(_tokenize(hint)))


def _looks_like_specific_hotel(place: str) -> bool:
    """Distinguish "find any hotel in this city" (place = a bare city name)
    from "find this specific property" (a multi-word name, or one
    containing a hotel-ish word) — only the latter should require the
    result to actually name that property."""
    words = place.strip().split()
    if len(words) >= 3:
        return True
    return any(h in place.lower() for h in _HOTEL_NAME_HINTS)


def _tokenize(text: str) -> list[str]:
    """Word-tokenize for matching: drop apostrophes so "Levi's" and
    "Levis" tokenize the same (fixes a real miss — see _title_matches),
    but otherwise split on every non-alphanumeric character rather than
    stripping it. That second part matters more than it looks: blanket-
    stripping punctuation (an earlier version of this function did) turns
    "3.7K" (a review count) into "37k" — which then coincidentally
    contains the digit sequence "37" and falsely matched a search for
    "Hotel Delhi 37" against an unrelated "Hotel Smart Plaza Delhi
    Airport" listing, confirmed live. Tokenizing keeps "3" and "7k" as
    separate words, so that collision can't happen."""
    cleaned = text.lower().replace("'", "").replace("’", "")
    return re.findall(r"[a-z0-9]+", cleaned)


def _title_matches(title: str | None, hint: str, stopwords: set[str] = frozenset(),
                    require_dominant: bool = False) -> bool:
    """Does this candidate's title plausibly refer to `hint` (a product
    name or a specific hotel)? Two regimes, because a hotel/short-product
    name and a long SEO-stuffed title need different bars:

    - Short hints (<=3 distinctive words, typical for a hotel name like
      "Hotel Delhi 37"): require EVERY word, including numeric ones.
      Confirmed live: dropping short numeric tokens (the old rule needed
      len>2) let "Hotel Delhi 37" match an unrelated "Hotel Krone Delhi"
      on the word "delhi" alone — a city name shared by hundreds of
      listings.
    - Long hints (verbose Amazon-style titles): at least half the words
      (capped at 4 — a 20-word title shouldn't need half to show up
      verbatim on a competitor's differently-worded listing), AND
      specifically the first word (almost always the brand, by
      convention) — confirmed live, word-count alone let an unrelated
      "KUMB144" earbud match a boAt search on generic words like
      "earbuds"/"bluetooth" alone.
    """
    if not title:
        return False
    if _is_accessory_mismatch(title, hint):
        return False
    title_token_list = [w for w in _tokenize(title) if len(w) > 2 or w.isdigit()]
    title_tokens = set(title_token_list)
    hint_words = [w for w in _tokenize(hint) if w not in stopwords and (len(w) > 2 or w.isdigit())]
    if not hint_words:
        return True  # nothing distinctive to check against (e.g. a bare city name)

    if require_dominant:
        # For hotels: a DIFFERENT property can legitimately reference the
        # target by name as a landmark ("Carlton Hotel - Behind Taj Mahal
        # Palace" for a "Taj Mahal Palace Mumbai" search — confirmed live,
        # word-overlap alone doesn't catch this). Require the candidate
        # text to be MOSTLY the hint, not just mention most of it within
        # a longer, differently-named title. Not used for products: a
        # terser competitor listing legitimately covers less of a long,
        # SEO-stuffed hint — this ratio would wrongly reject real matches
        # there.
        hint_set = set(hint_words)
        overlap = sum(1 for w in title_token_list if w in hint_set)
        if not title_token_list or overlap < len(hint_words) * 0.6 or overlap / len(title_token_list) < 0.5:
            return False

    if len(hint_words) <= 3:
        return all(w in title_tokens for w in hint_words)

    matches = sum(1 for w in hint_words if w in title_tokens)
    needed = min(max(1, (len(hint_words) + 1) // 2), 4)
    if matches < needed:
        return False
    return hint_words[0] in title_tokens


def _pick_best_match(candidates: list[tuple[float, str | None, str | None]],
                      hint: str, stopwords: set[str] = frozenset(),
                      require_dominant: bool = False,
                      ) -> tuple[float, str | None, str | None] | None:
    """Among candidates that pass _title_matches (the "close enough"
    bar), prefer one that's an EXACT match — the brand plus every specific
    model/serial NUMBER, not necessarily every descriptive word.
    Benchmarked live against Google Shopping for "boAt Airdopes 141
    earbuds": Google showed Amazon.in genuinely carries the exact
    "Airdopes 141" model, but this tool's loose threshold was already
    satisfied by a different model (163) it found first and never
    checked whether a better, exact match existed on the same page.

    Deliberately NOT requiring every hint word for "exact": a first
    version did, and it backfired — requiring the literal word "earbuds"
    rejected that same genuine "Airdopes 141" listing because Amazon
    phrased it as "Ear Buds" (two words, a synonym), falling through to
    the loose tier and losing to a cheaper, less-exact match anyway.
    Numbers don't have that synonym problem (a model number is what it
    is), so they stay mandatory for "exact"; other words don't.

    Falls back to the loose match only when no exact one exists."""
    hint_words = [w for w in _tokenize(hint) if w not in stopwords and (len(w) > 2 or w.isdigit())]
    if hint_words:
        must_have = [hint_words[0]] + [w for w in hint_words if w.isdigit()]
        exact = [c for c in candidates
                 if c[2] and not _is_accessory_mismatch(c[2], hint)
                 and all(w in set(_tokenize(c[2])) for w in must_have)
                 and (not require_dominant or _title_matches(c[2], hint, stopwords, require_dominant=True))]
        if exact:
            return min(exact, key=lambda c: c[0])
    loose = [c for c in candidates if _title_matches(c[2], hint, stopwords, require_dominant=require_dominant)]
    if loose:
        return min(loose, key=lambda c: c[0])
    return None


def _extract_amazon_pdp_price(html: str) -> float | None:
    """Amazon's PRODUCT-DETAIL page (what resolve_origin actually opens
    when the user pastes a product link) has a completely different DOM
    from its search-results page — _extract_amazon_result's
    s-search-result selector doesn't exist here at all, so the generic
    heuristic was falling through and grabbing an unrelated ₹ figure
    (confirmed live: a real amazon.in /dp/ page returned ₹500 — nowhere
    near the actual price — because a PDP has many small ₹ mentions
    scattered around: EMI-per-month text, "save ₹X" coupon banners,
    delivery-charge notes). Amazon's own `.a-price .a-offscreen`
    component is what it uses specifically for THE price, in a stable
    cascade of core-price containers, most-specific first."""
    soup = BeautifulSoup(html, "html.parser")
    for selector in (
        "#corePriceDisplay_desktop_feature_div span.a-price span.a-offscreen",
        "#corePrice_feature_div span.a-price span.a-offscreen",
        "#apex_desktop span.a-price span.a-offscreen",
        "#ppd span.a-price span.a-offscreen",
        "span.a-price span.a-offscreen",
    ):
        el = soup.select_one(selector)
        if el:
            m = re.search(r"([\d][\d,]{2,})", el.get_text(strip=True))
            if m:
                price = _clean_price(m.group(1))
                if price is not None:
                    return price
    return None


def _find_amazon_exact_match_link(html: str, hint: str, stopwords: set[str] = frozenset()) -> str | None:
    """Some Amazon search-result cards for the exact product show NO price
    at all anywhere in the card (confirmed live: searching "iphone 17"
    found two cards literally titled "Apple iPhone 17 256 GB..." — an
    exact match — with zero ₹ mentions in either card's full text; Amazon
    defers to a "choose options" flow instead of one card price for some
    new/high-variant listings). Rather than reporting no_match when the
    right product IS on the page, find its link so the caller can follow
    through to the product page itself and read the real price there."""
    soup = BeautifulSoup(html, "html.parser")
    hint_words = [w for w in _tokenize(hint) if w not in stopwords and (len(w) > 2 or w.isdigit())]
    if not hint_words:
        return None
    must_have = [hint_words[0]] + [w for w in hint_words if w.isdigit()]
    for card in soup.select('div[data-component-type="s-search-result"]'):
        img = card.select_one("img[alt]")
        title = img.get("alt") if img else None
        if not title or _is_accessory_mismatch(title, hint):
            continue
        if all(w in set(_tokenize(title)) for w in must_have):
            link_el = card.select_one('a.a-link-normal[href*="/dp/"]') or card.select_one("h2 a")
            if link_el and link_el.get("href"):
                return link_el.get("href")
    return None


async def _amazon_pdp_followup(context, base_url: str, link: str, run_dir: Path) -> dict:
    """Navigate to a product page found by _find_amazon_exact_match_link
    and read its real price + a fresh screenshot (proof of the page the
    price actually came from, not the search-results page that had none)."""
    full_url = _absolutize(link, base_url)
    result = {"price": None, "title": None, "url": full_url, "screenshot": None}
    if not full_url:
        return result
    page = await context.new_page()
    try:
        await page.goto(full_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(1200)
        html = await page.content()
        result["price"] = _extract_amazon_pdp_price(html)
        result["title"] = (await page.title() or "").strip()[:150] or None
        shot_path = run_dir / "amazon_in_followup.png"
        await page.screenshot(path=str(shot_path), full_page=False)
        result["screenshot"] = f"/screenshots/{run_dir.name}/{shot_path.name}"
    except Exception:  # noqa: BLE001
        pass
    finally:
        await page.close()
    return result


def _extract_top_price_and_link(html: str, base_url: str, domain: str | None = None,
                                  title_hint: str | None = None,
                                  hint_stopwords: set[str] = frozenset(),
                                  require_hint_match: bool = False,
                                  page_title: str | None = None,
                                  require_dominant: bool = False,
                                  ) -> tuple[float | None, str | None, str | None]:
    """Generic heuristic: scan the DOM for every plausible ₹ price
    (skipping filter/sort/range chrome), then pick a candidate — good
    enough for a demo, not a substitute for the real per-page verification
    the ecommerce-deal-finder/hotel-deal-finder skills do with a live
    browser and actual judgment.

    Without a title_hint, or when a hint match isn't required, this
    returns the FIRST plausible candidate in document order (usually the
    top/most relevant result). When `require_hint_match` is set (a named
    product or a specific hotel, not just a city), a candidate must
    actually mention the thing being searched for — otherwise this
    returns no match rather than silently reporting a different item's
    price as if it were the requested one (confirmed live: Google Hotels
    and Booking.com both returned an unrelated property's price for a
    named-hotel search before this check existed — see docs/TESTING.md).

    Amazon gets a tuned selector instead of this heuristic entirely (see
    _extract_amazon_result) since it's the single most-checked site and
    the generic heuristic misfired on it.

    `page_title` is accepted but deliberately NOT used to relax matching:
    tried using it as a "the page's own title already confirms it" bypass
    for named-hotel searches, but reverted — a hotel-search results page
    (even one Google resolves to a specific-property detail panel) still
    lists many OTHER properties in the same DOM (a sidebar, "similar
    hotels"), so the page's title naming the right property doesn't mean
    the first price found on it belongs to that property. Confirmed live:
    this relaxation made Google Hotels return "Novotel New Delhi
    Aerocity" for a "Hotel Delhi 37" search — reintroducing the exact
    wrong-property bug the per-candidate check exists to prevent. Kept as
    a parameter (unused) rather than removed, as a flag against trying
    this same approach again without re-reading this note."""
    if domain and "amazon" in domain:
        price, link, title = _extract_amazon_result(
            html, title_hint=title_hint, hint_stopwords=hint_stopwords,
            require_hint_match=require_hint_match,
        )
        if price is not None:
            return price, link, title
        # fall through to the generic heuristic as a backup, not a guess

    # Text that marks a match as a filter/sort/facet control rather than an
    # actual listing (e.g. TataCliq's "Select All ₹0-₹1,000" price-range
    # filter) — caught live in testing, so keep scanning past these instead
    # of returning the first ₹ match found.
    # "exchange" added after a real miss: Flipkart shows an "Upto ₹62,050
    # Off on Exchange" badge as a text node near the real "₹82,900" price
    # — separate enough in the DOM that the two didn't land in the same
    # >2-symbols/range check, so the (lower, wrong) exchange amount was
    # extracted as if it were the price. Confirmed against the user's own
    # screenshot showing the real Flipkart price was ₹82,900, not ₹62,050.
    _DENYLIST = ("filter", "select all", "sort by", "clear all", "price range",
                 "budget", "बजट", "exchange", "cashback", "instant discount")
    _RANGE_RE = re.compile(r"₹\s?[\d][\d,.]*\s*[-–to]{1,4}\s*₹\s?[\d][\d,.]*")
    # "Above ₹ 30,000", "Under ₹2,000" — the open-ended end of the same
    # price-filter list, which has no second ₹ for _RANGE_RE to catch.
    _FILTER_BOUND_RE = re.compile(r"^\s*(above|under|below|upto|up to|over|less than)\s*₹", re.I)
    _CURRENT_PRICE_RE = re.compile(r"current price\s*₹\s?([\d][\d,]{2,})", re.I)

    soup = BeautifulSoup(html, "html.parser")
    candidates: list[tuple[float, str | None, str | None]] = []
    for el in soup.find_all(string=PRICE_RE):
        m = PRICE_RE.search(el)
        if not m:
            continue
        price = _clean_price(m.group(1))
        if price is None or price < 50:  # filter noise like "₹5 off"
            continue

        # Check the price node's OWN text for filter chrome before anything
        # else. The title-based guards below only run once the 6-level walk
        # finds an ancestor whose text doesn't start with a ₹ — and a price
        # FILTER's every ancestor starts with one, so a filter bucket
        # sailed past all of them with title=None and full_text="".
        # Confirmed live: EaseMyTrip's "₹ 1 - ₹ 2,000 / ₹ 2,001 - ₹ 4,000 /
        # Above ₹ 30,000" per-night filter list made a Goa search report
        # "₹2,000" as the cheapest rate, which is not a rate at all.
        own_text = el.strip()
        if _RANGE_RE.search(own_text) or _FILTER_BOUND_RE.match(own_text):
            continue

        # Booking.com writes both prices into one string for screen readers:
        # "Original price ₹ 17,998. Current price ₹ 17,458." — taking the
        # first ₹ there quotes the crossed-out price, i.e. more than the
        # hotel actually charges.
        cur = _CURRENT_PRICE_RE.search(own_text)
        if cur:
            better = _clean_price(cur.group(1))
            if better is not None and better >= 50:
                price = better

        # A NARROW check first, just the closest 3 ancestors: catches a
        # badge like "Upto ₹62,050 Off on Exchange" that sits right next
        # to the real price ("₹82,900") as a sibling-ish element — close
        # enough that both share the SAME 6-level-up title snippet below
        # (so a denylist check against that shared, truncated-at-160-char
        # title never sees the word "exchange" at all), but distinct at
        # this narrower distance. Confirmed live: without this, Flipkart's
        # exchange-bonus figure was extracted as if it were the price,
        # confirmed wrong against the user's own screenshot.
        close_text = ""
        n = el.parent
        for _ in range(3):
            if n is None:
                break
            close_text += " " + n.get_text(" ", strip=True)
            n = n.parent
        if any(kw in close_text.lower() for kw in ("exchange", "cashback", "instant discount")):
            continue

        # walk up to find an enclosing link and a plausible title
        node = el.parent
        link = None
        title = None
        full_text = ""
        for _ in range(6):
            if node is None:
                break
            if node.name == "a" and node.get("href"):
                link = node.get("href")
            if title is None:
                text = node.get_text(" ", strip=True)
                if text and len(text) > 8 and "₹" not in text[:8]:
                    full_text = text
                    title = text[:160]
            node = node.parent
        if title and any(kw in title.lower() for kw in _DENYLIST):
            continue  # looks like filter/sort chrome, not a listing — keep scanning
        if full_text.count("₹") > 2:
            # a real listing shows at most a sale + strikethrough MRP price;
            # 3+ symbols in one snippet is a price-range slider/list of
            # buckets (e.g. "Min ₹200 ₹300 ₹400 ₹500... to ₹200 ₹300...")
            continue
        if _RANGE_RE.search(full_text):
            # "₹200 - ₹8,000+" style budget-slider text (caught live on
            # Booking.com, including its Hindi-language variant)
            continue
        candidates.append((price, link, title))

    if not candidates:
        return None, None, None

    if require_hint_match and title_hint:
        best = _pick_best_match(candidates, title_hint, hint_stopwords, require_dominant=require_dominant)
        if best:
            return best
        # Per-price-node matching found nothing — try one more thing
        # before giving up. Confirmed live on Google Hotels: when a search
        # resolves to a page specifically ABOUT one property, that page
        # states the property's name ONCE (its own heading) and shows its
        # price nearby but further away in the DOM than the 6-level
        # per-price walk above can reach — so a genuine match was being
        # missed, not just an absent one.
        heading_result = _price_near_heading(soup, title_hint, hint_stopwords, require_dominant=require_dominant)
        if heading_result:
            return heading_result
        return None, None, None  # nothing on the page actually names it — don't guess

    return candidates[0]


def _price_near_heading(soup: BeautifulSoup, hint: str,
                          stopwords: set[str] = frozenset(),
                          require_dominant: bool = True,
                          ) -> tuple[float, str | None, str | None] | None:
    """Find a heading (h1/h2/h3) whose text matches `hint` tightly, then
    expand OUTWARD from it (not from a price, the other direction from
    the per-candidate walk above) looking for a nearby price. Caps how far
    it's willing to expand by requiring the found container to have only
    a FEW price mentions (<=5) — if expanding hits a container with many
    (the whole page, a sidebar of other properties), that's over-expanded
    and this bails rather than risk grabbing an unrelated listing's price.

    The heading match itself needs to be much stricter than the per-price
    snippet check: a DIFFERENT property can legitimately reference the
    target by name as a landmark (confirmed live: "Carlton Hotel - Behind
    Taj Mahal Palace" wrongly matched a "Taj Mahal Palace Mumbai" search
    on word-overlap alone). Requiring the heading to be MOSTLY the hint —
    not just contain most of the hint's words somewhere within a longer,
    differently-named heading — rules that out."""
    hint_words = [w for w in _tokenize(hint) if w not in stopwords and (len(w) > 2 or w.isdigit())]
    if not hint_words:
        return None
    hint_set = set(hint_words)
    for heading in soup.find_all(["h1", "h2", "h3"]):
        text = heading.get_text(" ", strip=True)
        if not text or _is_accessory_mismatch(text, hint):
            continue
        heading_tokens = [w for w in _tokenize(text) if len(w) > 2 or w.isdigit()]
        if not heading_tokens:
            continue
        overlap = sum(1 for w in heading_tokens if w in hint_set)
        if overlap < len(hint_words) * 0.6 or overlap / len(heading_tokens) < 0.5:
            continue  # mentions the target, but isn't mostly it — a different property
        node = heading.parent
        for _ in range(15):
            if node is None:
                break
            node_text = node.get_text(" ", strip=True)
            prices_here = PRICE_RE.findall(node_text)
            if prices_here:
                if len(prices_here) > 5:
                    break  # over-expanded into a list of many properties — bail
                price = _clean_price(prices_here[0])
                if price is not None and price >= 50:
                    link_el = node.find("a", href=True)
                    return price, (link_el.get("href") if link_el else None), text
                break
            node = node.parent
    return None


_BLOCK_PHRASES = (
    "unusual traffic", "verify you are a human", "verify you're a human",
    "are you a robot", "access to this page has been denied",
    "checking your browser", "robot check", "enable javascript and cookies",
    "please complete the security check",
)


def _looks_blocked(html: str) -> bool:
    """Real bot-check pages say so in their VISIBLE text ("verify you're
    human", "unusual traffic..."). Checking raw HTML for a bare "captcha"
    substring — the original approach — false-positives on any ordinary
    page that merely embeds Google's reCAPTCHA widget as routine anti-abuse
    tooling (its script URL/class names contain "recaptcha", which
    contains "captcha"): confirmed live, a normal Booking.com results page
    that loaded fine and simply had no availability for the dates asked
    got mislabeled "blocked" this way. Scanning stripped visible text for
    actual block phrasing avoids that."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    visible = soup.get_text(" ", strip=True).lower()[:6000]
    return any(phrase in visible for phrase in _BLOCK_PHRASES)


def _absolutize(link: str | None, base_url: str) -> str | None:
    if not link:
        return None
    if link.startswith("http"):
        return link
    from urllib.parse import urljoin
    return urljoin(base_url, link)


async def _check_one_product_site(context, domain: str, query: str, run_dir: Path) -> dict:
    url = product_search_url(domain, query)
    page = await context.new_page()
    result = {"site": domain, "url": url, "status": "error", "price_inr": None,
              "title": None, "product_url": None, "screenshot": None, "note": ""}
    try:
        await page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)  # let lazy content settle
        html = await page.content()
        shot_path = run_dir / f"{domain.replace('.', '_')}.png"
        await page.screenshot(path=str(shot_path), full_page=False)
        result["screenshot"] = f"/screenshots/{run_dir.name}/{shot_path.name}"

        price, link, title = _extract_top_price_and_link(
            html, url, domain=domain, title_hint=query,
            hint_stopwords=_PRODUCT_STOPWORDS, require_hint_match=True,
        )

        followed_up = False
        if price is None and domain and "amazon" in domain:
            # The exact product may be on the page with no card-level
            # price at all (confirmed live — see _find_amazon_exact_match_link).
            # Follow through to its own page rather than reporting no_match
            # when the right item is genuinely right there.
            follow_link = _find_amazon_exact_match_link(html, query, _PRODUCT_STOPWORDS)
            if follow_link:
                followup = await _amazon_pdp_followup(context, url, follow_link, run_dir)
                if followup["price"] is not None:
                    price, link, title = followup["price"], follow_link, followup["title"]
                    followed_up = True
                    if followup["screenshot"]:
                        result["screenshot"] = followup["screenshot"]

        if price is None:
            if _looks_blocked(html):
                result["status"] = "blocked"
                result["note"] = "Site returned a bot check — skipped rather than bypassed."
            else:
                result["status"] = "no_match"
                result["note"] = "No matching listing with a visible price found."
        else:
            result["status"] = "ok"
            result["price_inr"] = price
            result["title"] = title or query
            result["product_url"] = _absolutize(link, url) or url
            result["note"] = (
                "No price shown on the search results card for this exact listing — "
                "followed the link to its own page instead."
                if followed_up else
                "Best-effort match — confirm variant/seller before buying."
            )
    except Exception as exc:  # noqa: BLE001
        if domain in _KNOWN_BLOCKED_DOMAINS:
            result["status"] = "blocked"
            result["note"] = _KNOWN_BLOCKED_NOTE
        else:
            result["status"] = "error"
            result["note"] = f"Could not load page ({type(exc).__name__})."
    finally:
        await page.close()
    return result


def _extract_google_hotel_offers(html: str) -> list[dict]:
    """Pull the per-provider offer list out of a Google Hotels property
    page — the single highest-value thing on it, and previously thrown
    away entirely.

    When Google resolves a search to one specific hotel, its panel lists
    that hotel's price at each booking provider, each as a link:

        Skyscanner      ₹1,931   Visit site
        MakeMyTrip.com  ₹2,076   Visit site
        Official Site   ₹2,646   Visit site
        EaseMyTrip.com  ₹1,891   Visit site

    That's several genuinely comparable prices with booking links from
    ONE page load — including providers (MakeMyTrip, Goibibo, Agoda) that
    block this tool's own headless browser outright, so their prices are
    otherwise unreachable. Extracting one number from this page and
    discarding the rest was why a pasted hotel link came back with
    nothing to compare against.

    The href is what separates a real provider offer from an ad, and the
    distinction matters: "ends with 'Visit site'" alone was not enough.
    Google's panel carries BOTH

      * organic provider rows, linking to /travel/lodging/clk — one per
        booking site (Agoda, EaseMyTrip.com, Yatra.com, ...), each with a
        nightly price and a stay total; and
      * paid rows, linking to /aclk — which are ads, and are per ROOM TYPE
        rather than per provider.

    Both end in "Visit site", so keying on that text alone filled the
    results with "Superior Room", "Deluxe Room Double" and "Suite" as if
    they were competing booking sites — four of five rows in a live run
    were room types at one provider, which is not a price comparison at
    all. Only the organic rows are taken.

    Each organic row reads: <provider>, ..., "Nightly price with taxes +
    fees", "Stay total with taxes + fees", ₹nightly, ₹nightly, ₹nightly,
    ₹total, "Visit site" — so the first price is the nightly rate and the
    last is the whole stay. Both are kept; the nightly rate is what gets
    compared, because that is what every other site in the list quotes."""
    soup = BeautifulSoup(html, "html.parser")
    offers: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        if "/travel/lodging/clk" not in a["href"]:
            continue  # an ad (/aclk) or some other link — see docstring
        text = a.get_text("\n", strip=True)
        if not text or "₹" not in text:
            continue
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not lines or lines[-1].lower() != "visit site":
            continue

        prices = [p for p in (_clean_price(m) for m in PRICE_RE.findall(text))
                  if p is not None and p >= 50]
        if not prices:
            continue
        price = prices[0]
        stay_total = prices[-1] if prices[-1] > price else None

        if "official site" in text.lower():
            provider = "Official site"
        else:
            provider = lines[0]
        provider = provider.strip(" ·")[:40]
        if not provider:
            continue

        # Same provider can appear twice (a featured placement plus its
        # normal row) — keep the cheaper.
        existing = offers.get(provider.lower())
        if existing and existing["price_inr"] <= price:
            continue
        offers[provider.lower()] = {
            "provider": provider,
            "price_inr": price,
            "stay_total_inr": stay_total,
            "link": a.get("href"),
        }
    return sorted(offers.values(), key=lambda o: o["price_inr"])


_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def _date_renderings(iso_date: str) -> list[str]:
    """Every spelling of one date a hotel site plausibly renders.

    Both the abbreviated and full month name are needed, and neither
    contains the other in the order sites write them: Booking.com renders
    "24 December 2026", in which "24 Dec" does not appear — checking only
    the short form marked Booking.com and Cleartrip as pricing the wrong
    dates when they were pricing the right ones."""
    y, m, d = (int(x) for x in iso_date.split("-"))
    full = _MONTHS[m - 1]
    short = full[:3]
    out = [
        iso_date,                       # 2026-12-24
        f"{d:02d}/{m:02d}/{y}",         # 24/12/2026
        f"{m:02d}/{d:02d}/{y}",         # 12/24/2026
        f"{d}-{m:02d}-{y}",             # 24-12-2026
    ]
    for mon in {full, short}:
        out += [f"{mon} {d}", f"{d} {mon}", f"{mon} {d:02d}", f"{d:02d} {mon}"]
    return out


async def _page_confirms_dates(page, checkin: str, checkout: str) -> bool:
    """Ask the page which dates it is actually pricing, and believe only
    the page.

    This is the whole answer to "the dates are not being passed on". Every
    site here takes dates in its URL, and two of the most useful ones
    quietly ignore them — Google outright (its plain checkin/checkout
    params do nothing), others when a date is unavailable or the format is
    off. A price for the wrong dates looks exactly like a price for the
    right ones, so it is the one error a user cannot catch by eye.

    Preferred oracle is the site's own check-in/check-out form fields
    (Google fills them with "Thu, Dec 24" / "Sat, Dec 26" — authored by
    Google, not inferred by us). Falling back to the page's visible text
    is weaker but still evidence: the date has to appear somewhere on a
    page that is genuinely priced for it."""
    try:
        values = await page.evaluate("""() => {
            const out = {};
            const sel = 'input, [role="textbox"], [data-testid], [data-cy]';
            document.querySelectorAll(sel).forEach(el => {
                const label = (el.getAttribute('aria-label') || el.getAttribute('placeholder')
                    || el.getAttribute('data-testid') || el.getAttribute('data-cy') || '').toLowerCase();
                const val = (el.value || el.textContent || '').trim();
                if (!val || val.length > 60) return;
                const isDateField = label.includes('date') || label.includes('check');
                if (!isDateField) return;
                if (label.includes('check-in') || label.includes('check in')
                    || label.includes('checkin') || label.includes('start') || label.includes('from'))
                    out.checkin = out.checkin || val;
                if (label.includes('check-out') || label.includes('check out')
                    || label.includes('checkout') || label.includes('end') || label.includes('to'))
                    out.checkout = out.checkout || val;
            });
            return out;
        }""")
    except Exception:  # noqa: BLE001
        values = {}

    if values.get("checkin") and values.get("checkout"):
        in_ok = any(r.lower() in values["checkin"].lower() for r in _date_renderings(checkin))
        out_ok = any(r.lower() in values["checkout"].lower() for r in _date_renderings(checkout))
        return bool(in_ok and out_ok)

    try:
        text = (await page.inner_text("body")).lower()
    except Exception:  # noqa: BLE001
        return False
    return (any(r.lower() in text for r in _date_renderings(checkin))
            and any(r.lower() in text for r in _date_renderings(checkout)))


async def _check_one_hotel_site(context, domain: str, place: str, checkin: str,
                                 checkout: str, guests: int, run_dir: Path) -> list[dict]:
    url = hotel_search_url(domain, place, checkin, checkout, guests)
    page = await context.new_page()
    result = {"site": domain, "url": url, "status": "error", "price_inr": None,
              "title": None, "booking_url": None, "screenshot": None,
              "dates_accurate": None, "note": ""}
    try:
        await page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        # A blind fixed wait was the wrong tool here: confirmed live,
        # Booking.com's screenshot came back showing only its blue header
        # bar — the page's actual results hadn't rendered yet by 2000ms,
        # so extraction ran against an empty page and (correctly, given
        # nothing was there) found no price. Wait for actual ₹ content to
        # appear instead, up to a real budget; if it never shows up within
        # that budget (a genuinely slow or price-less page), proceed
        # anyway rather than hang — extraction will honestly report
        # no_match rather than pretend this fixed everything.
        try:
            await page.wait_for_selector("text=/₹/", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
        await page.wait_for_timeout(800)
        # The tab's own <title> is a second, independent confirmation
        # signal — when a site resolves a named-hotel search to a page
        # specifically about that property (e.g. Google's own "Hotel
        # Delhi 37 - Google hotels"), that's authored by the site itself,
        # not inferred by our own snippet-matching guesswork. Not used for
        # product search: a search-RESULTS page's title usually just
        # echoes the query regardless of what's actually on it, which
        # would make this check circular there.
        page_title = await page.title()
        html = await page.content()
        shot_path = run_dir / f"{domain.replace('.', '_').replace('/', '_')}.png"
        await page.screenshot(path=str(shot_path), full_page=False)
        result["screenshot"] = f"/screenshots/{run_dir.name}/{shot_path.name}"

        # Ask the page itself, before reading any price off it, whether it
        # is pricing the dates that were asked for.
        dates_ok = await _page_confirms_dates(page, checkin, checkout)
        result["dates_accurate"] = dates_ok
        stay = f"{checkin} to {checkout}"

        # Google's property panel carries several providers' prices for
        # this exact hotel, each with a booking link — strictly more
        # useful than the single headline number the generic extractor
        # would pull off the same page, so prefer it when present.
        if "google.com/travel/hotels" in domain:
            offers = _extract_google_hotel_offers(html)
            if offers:
                return [{
                    "site": o["provider"],
                    "url": url,
                    "status": "ok" if dates_ok else "wrong_dates",
                    "price_inr": o["price_inr"],
                    "stay_total_inr": o.get("stay_total_inr"),
                    "title": page_title.replace(" - Google hotels", "").strip() or place,
                    "booking_url": _absolutize(o["link"], "https://www.google.com/") or url,
                    "screenshot": result["screenshot"],
                    "dates_accurate": dates_ok,
                    "note": (f"Google's listed price at this provider for {stay}, confirmed "
                             "against the dates Google shows in its own check-in/check-out "
                             "fields. Confirm taxes and cancellation on the provider's site."
                             if dates_ok else
                             "Dropped: Google did not confirm it was pricing your dates, so "
                             "this price is for some other stay."),
                } for o in offers[:5]]

        price, link, title = _extract_top_price_and_link(
            html, url, title_hint=place, hint_stopwords=_HOTEL_STOPWORDS,
            require_hint_match=_looks_like_specific_hotel(place),
            page_title=page_title, require_dominant=True,
        )
        if price is None:
            if _looks_blocked(html):
                result["status"] = "blocked"
                result["note"] = "Site returned a bot check — skipped rather than bypassed."
            else:
                result["status"] = "no_match"
                if _looks_like_specific_hotel(place):
                    result["note"] = f"Couldn't confirm a listing actually naming \"{place}\" here — check the link manually."
                else:
                    result["note"] = "No visible rate found for these dates — check the link manually."
        else:
            result["price_inr"] = price
            result["title"] = title or place
            result["booking_url"] = _absolutize(link, url) or url
            result["status"] = "ok" if dates_ok else "wrong_dates"
            result["note"] = (
                f"Priced for {stay}, confirmed against the dates the site itself shows "
                "— check taxes and cancellation before booking."
                if dates_ok else
                f"Dropped: this page never confirmed it was pricing {stay}, so the rate "
                "shown on it belongs to some other stay."
            )
    except Exception as exc:  # noqa: BLE001
        if domain in _KNOWN_BLOCKED_DOMAINS:
            result["status"] = "blocked"
            result["note"] = _KNOWN_BLOCKED_NOTE
        else:
            result["status"] = "error"
            result["note"] = f"Could not load page ({type(exc).__name__})."
    finally:
        await page.close()
    return [result]


def _new_run_dir(prefix: str) -> Path:
    run_id = f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    run_dir = SCREENSHOT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _log_price_history(rows: list[dict]):
    csv_path = DATA_DIR / "price_history.csv"
    is_new = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "item_type", "item_name", "site", "url",
                              "price_inr", "currency_native", "price_native", "notes"])
        for r in rows:
            writer.writerow(r)


_BLOCKED_TITLE_RE = re.compile(
    r"access denied|just a moment|attention required|are you a human|robot check|"
    r"security check|forbidden|not found|^error|blocked|captcha|unavailable|"
    r"page not found|service unavailable", re.I)


def is_usable_product_title(title: str | None, url: str) -> bool:
    """Is this page title a product name, or the site's own name on a page
    that never loaded the product?

    Confirmed live on amazon.in: a URL whose product page didn't serve
    came back titled exactly "Amazon.in". That got searched for on every
    other site, which returned a shampoo called "Amazon Series" — a
    confident answer to a question nobody asked. A title that is just the
    site's own domain words carries no product in it, so it is treated as
    a failure to read the page, not as the product's name."""
    if not title:
        return False
    cleaned = title.strip()
    if len(cleaned) < 8 or _BLOCKED_TITLE_RE.search(cleaned):
        return False
    host_words = {w for w in re.split(r"[.\-_]+", urlparse(url).netloc.lower())
                  if w and w not in ("www", "com", "in", "co", "net", "org")}
    title_words = {w for w in _tokenize(cleaned) if w}
    return bool(title_words - host_words - {"in", "com"})


async def resolve_origin(browser, url: str) -> tuple[dict, str | None]:
    """Open a user-supplied product URL directly (mirrors step 1 of the
    ecommerce-deal-finder skill): read its real title so category-matching
    and the other sites' searches use the actual product name, not the raw
    URL text, and grab its own price/screenshot as a confirmed data point."""
    context = await _new_context(browser)
    run_dir = _new_run_dir("origin")
    page = await context.new_page()
    domain = urlparse(url).netloc.replace("www.", "")
    result = {"site": domain, "url": url, "status": "error", "price_inr": None,
              "title": None, "product_url": url, "screenshot": None,
              "note": "The link you provided."}
    title_text = None
    try:
        await page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        title_text = (await page.title() or "").strip() or None
        html = await page.content()
        shot_path = run_dir / "origin.png"
        await page.screenshot(path=str(shot_path), full_page=False)
        result["screenshot"] = f"/screenshots/{run_dir.name}/{shot_path.name}"
        result["title"] = title_text[:150] if title_text else None

        # A pasted product link is (virtually) always a product-DETAIL
        # page, never a search-results page — Amazon's two page types have
        # entirely different markup, so this needs its own extractor
        # rather than the search-results heuristic used elsewhere.
        if "amazon" in domain:
            price = _extract_amazon_pdp_price(html)
        else:
            price, _, _ = _extract_top_price_and_link(html, url, domain=domain)
        if price is not None:
            result["status"] = "ok"
            result["price_inr"] = price
        else:
            result["status"] = "no_match"
            result["note"] = "The link you provided — no price detected automatically, open it to check."
    except Exception as exc:  # noqa: BLE001
        if domain in _KNOWN_BLOCKED_DOMAINS:
            result["status"] = "blocked"
            result["note"] = _KNOWN_BLOCKED_NOTE
        else:
            result["status"] = "error"
            result["note"] = f"Could not load the link you provided ({type(exc).__name__})."
    finally:
        await page.close()
        await context.close()
    return result, title_text


async def run_product_search(browser, query: str, sites: list[str],
                               origin_result: dict | None = None) -> dict:
    context = await _new_context(browser)
    run_dir = _new_run_dir("product")
    tasks = [_check_one_product_site(context, d, query, run_dir) for d in sites]
    results = await asyncio.gather(*tasks)
    await context.close()

    all_results = list(results) + ([origin_result] if origin_result else [])

    ok = [r for r in all_results if r["status"] == "ok"]
    ok.sort(key=lambda r: r["price_inr"])
    cheapest = ok[:5]
    others = [r for r in all_results if r["status"] != "ok"]

    now = datetime.now(timezone.utc).isoformat()
    _log_price_history([
        [now, "ecommerce", query, r["site"], r["product_url"], r["price_inr"], "INR", r["price_inr"], "webapp best-effort"]
        for r in cheapest
    ])

    return {
        "query": query,
        "checked_at": now,
        "cheapest": cheapest,
        "skipped": others,
    }


async def run_hotel_search(browser, place: str, checkin: str, checkout: str,
                            guests: int, sites: list[str]) -> dict:
    context = await _new_context(browser, locale="en-IN")
    run_dir = _new_run_dir("hotel")
    tasks = [_check_one_hotel_site(context, d, place, checkin, checkout, guests, run_dir) for d in sites]
    per_site = await asyncio.gather(*tasks)
    await context.close()

    # Each site returns a LIST now — Google Hotels contributes one row per
    # booking provider it lists for the property, not just one row total.
    results = [r for rows in per_site for r in rows]
    all_results = results

    # "ok" now means the site confirmed it was pricing the requested dates
    # — a row that couldn't prove that comes back as "wrong_dates" and is
    # reported as skipped rather than ranked. A price for the wrong stay is
    # not a cheaper price, it's a wrong answer.
    ok = [r for r in all_results if r["status"] == "ok"]
    ok.sort(key=lambda r: r["price_inr"])
    cheapest = ok[:5]
    others = [r for r in all_results if r["status"] != "ok"]

    now = datetime.now(timezone.utc).isoformat()
    label = f"{place} {checkin}->{checkout} x{guests}"
    _log_price_history([
        [now, "hotel", label, r["site"], r["booking_url"], r["price_inr"], "INR", r["price_inr"], "webapp best-effort"]
        for r in cheapest
    ])

    return {
        "place": place,
        "checkin": checkin,
        "checkout": checkout,
        "guests": guests,
        "checked_at": now,
        "cheapest": cheapest,
        "skipped": others,
    }
