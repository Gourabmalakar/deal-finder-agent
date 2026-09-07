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
    place: str = Field(..., min_length=2, max_length=200)
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
        if resolved_title:
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

    hotel_catalog = load_hotel_sites()
    sites = [s["domain"] for s in hotel_catalog["sites"]]
    browser = _state["browser"]
    result = await scraper.run_hotel_search(
        browser, body.place.strip(), body.checkin, body.checkout, body.guests, sites
    )
    result["sites_checked"] = sites

    eval_verdict = evaluator.evaluate_and_log(
        "hotel",
        {"place": body.place, "checkin": body.checkin, "checkout": body.checkout, "guests": body.guests},
        result, time.monotonic() - t0,
    )
    result["_eval"] = eval_verdict
    return result


@app.get("/api/evals")
async def get_evals(limit: int = 50, only_failed: bool = False):
    return {"runs": evaluator.recent_runs(limit=limit, only_failed=only_failed)}
