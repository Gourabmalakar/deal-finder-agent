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


def _extract_amazon_result(html: str) -> tuple[float | None, str | None, str | None]:
    """Amazon's search-result markup (data-component-type="s-search-result")
    has been stable for years, and the generic heuristic below reliably
    grabbed the wrong number on amazon.in in testing (a nav/promo "under
    ₹500" link, not the listing price) — worth a real selector for the
    single most important site in the list, unlike the rest which stay
    generic."""
    soup = BeautifulSoup(html, "html.parser")
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
        title_el = card.select_one("h2 a span") or card.select_one("h2 span")
        link_el = card.select_one("h2 a") or card.select_one("a.a-link-normal")
        return price, (link_el.get("href") if link_el else None), \
            (title_el.get_text(strip=True) if title_el else None)
    return None, None, None


def _extract_top_price_and_link(html: str, base_url: str,
                                  domain: str | None = None) -> tuple[float | None, str | None, str | None]:
    """Generic heuristic: scan the DOM in document order, return the
    first ₹ price found together with the nearest enclosing/preceding <a>
    link and a short text snippet as a stand-in "title". Good enough for a
    demo, not a substitute for the real per-site verification the
    ecommerce-deal-finder skill does with a live browser. Amazon gets a
    tuned selector instead (see _extract_amazon_result) since it's the
    single most-checked site and the generic heuristic misfired on it."""
    if domain and "amazon" in domain:
        price, link, title = _extract_amazon_result(html)
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
                    title = text[:120]
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
        return price, link, title
    return None, None, None


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

        price, link, title = _extract_top_price_and_link(html, url, domain=domain)
        if price is None:
            lc = html.lower()
            if "captcha" in lc or "robot" in lc or "access denied" in lc:
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
        html = await page.content()
        shot_path = run_dir / f"{domain.replace('.', '_').replace('/', '_')}.png"
        await page.screenshot(path=str(shot_path), full_page=False)
        result["screenshot"] = f"/screenshots/{run_dir.name}/{shot_path.name}"

        price, link, title = _extract_top_price_and_link(html, url)
        if price is None:
            lc = html.lower()
            if "captcha" in lc or "robot" in lc:
                result["status"] = "blocked"
                result["note"] = "Site returned a bot check — skipped rather than bypassed."
            else:
                result["status"] = "no_match"
                result["note"] = "No visible rate found for these dates — check the link manually."
        else:
            result["status"] = "ok"
            result["price_inr"] = price
            result["title"] = title or place
            result["booking_url"] = _absolutize(link, url) or url
            result["note"] = "Best-effort match — confirm dates, taxes and cancellation on the site."
    except Exception as exc:  # noqa: BLE001
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

        price, _, _ = _extract_top_price_and_link(html, url, domain=domain)
        if price is not None:
            result["status"] = "ok"
            result["price_inr"] = price
        else:
            result["status"] = "no_match"
            result["note"] = "The link you provided — no price detected automatically, open it to check."
    except Exception as exc:  # noqa: BLE001
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
