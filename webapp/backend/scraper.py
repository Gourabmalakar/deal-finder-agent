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
from urllib.parse import urlparse

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
# block automated browser connections outright: myntra.com rejects our
# headless Chromium specifically while a plain `curl` to the same URL
# succeeds (bot-fingerprint detection, not a network issue — not something
# this tool will try to evade); nykaa.com/nykaafashion.com return HTTP 403
# to curl too (blocks everyone); goibibo.com/makemytrip.com fail even a
# plain curl at the HTTP/2 protocol level (a server-side issue, not
# specific to us). Distinguishing this from a genuine transient failure
# means a real bug elsewhere doesn't get dismissed as "oh, that's just
# blocked" — and the user sees why, not a bare "(Error)".
_KNOWN_BLOCKED_DOMAINS = {"myntra.com", "nykaa.com", "nykaafashion.com",
                           "goibibo.com", "makemytrip.com"}
_KNOWN_BLOCKED_NOTE = ("This site consistently blocks automated browser connections — "
                        "confirmed independently of this tool (see docs/TESTING.md). "
                        "Not something fixable without bypassing bot detection, which "
                        "this tool won't do. Check the site directly.")

_HOTEL_STOPWORDS = {"hotel", "hotels", "resort", "the", "inn", "suites", "and", "a", "an"}
_HOTEL_NAME_HINTS = ("hotel", "resort", "inn", "suites", "villa", "palace", "residency")
_PRODUCT_STOPWORDS = {"buy", "online", "best", "price", "for", "with", "the", "and",
                       "in", "india", "at", "shop", "shopping", "a", "an"}


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


def _title_matches(title: str | None, hint: str, stopwords: set[str] = frozenset()) -> bool:
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
    title_tokens = set(_tokenize(title))
    hint_words = [w for w in _tokenize(hint) if w not in stopwords and (len(w) > 2 or w.isdigit())]
    if not hint_words:
        return True  # nothing distinctive to check against (e.g. a bare city name)

    if len(hint_words) <= 3:
        return all(w in title_tokens for w in hint_words)

    matches = sum(1 for w in hint_words if w in title_tokens)
    needed = min(max(1, (len(hint_words) + 1) // 2), 4)
    if matches < needed:
        return False
    return hint_words[0] in title_tokens


def _pick_best_match(candidates: list[tuple[float, str | None, str | None]],
                      hint: str, stopwords: set[str] = frozenset(),
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
                 if c[2] and all(w in set(_tokenize(c[2])) for w in must_have)]
        if exact:
            return min(exact, key=lambda c: c[0])
    loose = [c for c in candidates if _title_matches(c[2], hint, stopwords)]
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


def _extract_top_price_and_link(html: str, base_url: str, domain: str | None = None,
                                  title_hint: str | None = None,
                                  hint_stopwords: set[str] = frozenset(),
                                  require_hint_match: bool = False,
                                  page_title: str | None = None,
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
    _DENYLIST = ("filter", "select all", "sort by", "clear all", "price range", "budget", "बजट")
    _RANGE_RE = re.compile(r"₹\s?[\d][\d,.]*\s*[-–to]{1,4}\s*₹\s?[\d][\d,.]*")

    soup = BeautifulSoup(html, "html.parser")
    candidates: list[tuple[float, str | None, str | None]] = []
    for el in soup.find_all(string=PRICE_RE):
        m = PRICE_RE.search(el)
        if not m:
            continue
        price = _clean_price(m.group(1))
        if price is None or price < 50:  # filter noise like "₹5 off"
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
        best = _pick_best_match(candidates, title_hint, hint_stopwords)
        return best if best else (None, None, None)  # nothing on the page actually names it — don't guess

    return candidates[0]


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
            result["note"] = "Best-effort match — confirm variant/seller before buying."
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


async def _check_one_hotel_site(context, domain: str, place: str, checkin: str,
                                 checkout: str, guests: int, run_dir: Path) -> dict:
    url = hotel_search_url(domain, place, checkin, checkout, guests)
    page = await context.new_page()
    result = {"site": domain, "url": url, "status": "error", "price_inr": None,
              "title": None, "booking_url": None, "screenshot": None, "note": ""}
    try:
        await page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
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

        price, link, title = _extract_top_price_and_link(
            html, url, title_hint=place, hint_stopwords=_HOTEL_STOPWORDS,
            require_hint_match=_looks_like_specific_hotel(place),
            page_title=page_title,
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
            result["status"] = "ok"
            result["price_inr"] = price
            result["title"] = title or place
            result["booking_url"] = _absolutize(link, url) or url
            result["note"] = "Best-effort match — confirm dates, taxes and cancellation on the site."
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


async def resolve_origin(browser, url: str) -> tuple[dict, str | None]:
    """Open a user-supplied product URL directly (mirrors step 1 of the
    ecommerce-deal-finder skill): read its real title so category-matching
    and the other sites' searches use the actual product name, not the raw
    URL text, and grab its own price/screenshot as a confirmed data point."""
    context = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
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
    context = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
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
    context = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
    run_dir = _new_run_dir("hotel")
    tasks = [_check_one_hotel_site(context, d, place, checkin, checkout, guests, run_dir) for d in sites]
    results = await asyncio.gather(*tasks)
    await context.close()

    ok = [r for r in results if r["status"] == "ok"]
    ok.sort(key=lambda r: r["price_inr"])
    cheapest = ok[:5]
    others = [r for r in results if r["status"] != "ok"]

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
