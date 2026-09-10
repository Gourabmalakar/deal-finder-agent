from __future__ import annotations

import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

import evaluator
import scraper

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    _state["pw"] = pw
    _state["browser"] = browser
    yield
    await browser.close()
    await pw.stop()


app = FastAPI(title="Deal Finder (local test)", lifespan=lifespan)
app.mount("/screenshots", StaticFiles(directory=str(scraper.SCREENSHOT_DIR)), name="screenshots")


def load_category_sites() -> dict:
    with open(DATA_DIR / "category_sites.yaml") as f:
        return yaml.safe_load(f)


def load_hotel_sites() -> dict:
    with open(DATA_DIR / "hotel_sites.yaml") as f:
        return yaml.safe_load(f)


def pick_category(query: str, catalog: dict) -> tuple[str, list[str]]:
    lc = query.lower()
    for name, cat in catalog["categories"].items():
        if name == "general":
            continue
        for kw in cat.get("match_keywords", []):
            if kw in lc:
                return name, cat["sites"]
    general = catalog["categories"]["general"]
    return "general", general["sites"]


class ProductQuery(BaseModel):
    # Real product URLs (Amazon especially) routinely carry long tracking
    # query strings — a 300-char cap rejected a real pasted link in testing.
    query: str = Field(..., min_length=2, max_length=2000)


class HotelQuery(BaseModel):
    # Long enough for a pasted booking-page URL (tracking params included),
    # same reasoning as ProductQuery's cap.
    place: str = Field(..., min_length=2, max_length=2000)
    checkin: str
    checkout: str
    guests: int = Field(..., ge=1, le=20)


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/product-search")
async def product_search(body: ProductQuery):
    t0 = time.monotonic()
    raw = body.query.strip()
    browser = _state["browser"]

    origin_result = None
    search_text = raw
    if raw.lower().startswith(("http://", "https://")):
        origin_result, resolved_title = await scraper.resolve_origin(browser, raw)
        # A title has to actually name a product before other sites get
        # searched for it. The same failure that killed hotel links happens
        # here: a page the site refuses to serve us still HAS a title, and
        # amazon.in's bot/404 page is titled just "Amazon.in" — which was
        # then searched for everywhere else and came back with shampoo.
        if not scraper.is_usable_product_title(resolved_title, raw):
            raise HTTPException(
                422,
                "Couldn't read a product name off that link — the site served a block "
                "or error page instead of the product. Type the product name instead "
                "(e.g. \"boAt Airdopes 141\").",
            )
        search_text = resolved_title

    catalog = load_category_sites()
    category, sites = pick_category(search_text, catalog)
    if origin_result:
        # don't re-search the site the user's own link already came from
        sites = [s for s in sites if s != origin_result["site"]]

    result = await scraper.run_product_search(browser, search_text, sites, origin_result=origin_result)
    result["category"] = category
    result["sites_checked"] = sites
    result["origin_query"] = raw
    result["resolved_query"] = search_text

    eval_verdict = evaluator.evaluate_and_log(
        "product", {"query": raw}, result, time.monotonic() - t0
    )
    result["_eval"] = eval_verdict
    return result


@app.post("/api/hotel-search")
async def hotel_search(body: HotelQuery):
    t0 = time.monotonic()
    if not DATE_RE.match(body.checkin) or not DATE_RE.match(body.checkout):
        raise HTTPException(400, "Dates must be in YYYY-MM-DD format.")
    if body.checkout <= body.checkin:
        raise HTTPException(400, "Check-out must be after check-in.")

    raw_place = body.place.strip()
    browser = _state["browser"]

    # Hotel URLs are deliberately not accepted. Resolving a pasted booking
    # link into a property name was tried and withdrawn: it depended on
    # reading a name out of a page that hotel sites routinely refuse to
    # serve us, and a name read off a blocked page ("Access Denied") got
    # searched for on every other site, producing confidently wrong
    # hotels. Typing the name is both more reliable and faster.
    if raw_place.lower().startswith(("http://", "https://")):
        raise HTTPException(
            422,
            "Type the hotel or city name instead of a link — e.g. \"Taj Mahal Palace "
            "Mumbai\" or \"Goa\". Links were removed because booking sites block us "
            "from reading them, which produced results for the wrong hotel.",
        )
    place = raw_place

    hotel_catalog = load_hotel_sites()
    sites = [s["domain"] for s in hotel_catalog["sites"]]

    result = await scraper.run_hotel_search(
        browser, place, body.checkin, body.checkout, body.guests, sites,
    )
    result["sites_checked"] = sites
    result["origin_place"] = raw_place
    result["resolved_place"] = place

    eval_verdict = evaluator.evaluate_and_log(
        "hotel",
        {"place": raw_place, "checkin": body.checkin, "checkout": body.checkout, "guests": body.guests},
        result, time.monotonic() - t0,
    )
    result["_eval"] = eval_verdict
    return result


@app.get("/api/evals")
async def get_evals(limit: int = 50, only_failed: bool = False):
    return {"runs": evaluator.recent_runs(limit=limit, only_failed=only_failed)}
