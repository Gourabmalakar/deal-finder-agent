"""
Automatic, rule-based scoring against evals/criteria.yaml's `automatic:
true` criteria, run on every /api/product-search and /api/hotel-search
response before it's returned. Logs one row per query to data/evals.db
(SQLite) — the shared eval log the deal-evaluator subagent also reads and
writes to for its deeper, judgment-based checks (variant_match,
plausible_price follow-up).

This module only *checks structure*, it never judges whether a matched
listing is really the right product/hotel — that needs reading a title or
a screenshot, which is the subagent's job, not a fixed rule's.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "evals.db"

# Coarse plausibility bands per category — catches a heuristic grabbing an
# unrelated filter/promo number, not a judgment on whether a specific price
# is actually right (that's the subagent's job).
PLAUSIBLE_RANGES_INR = {
    "electronics": (100, 400_000),
    "clothing_fashion": (100, 25_000),
    "footwear": (200, 25_000),
    "cosmetics_beauty": (50, 15_000),
    "home_furniture": (200, 300_000),
    "general": (50, 400_000),
    # Upper bound raised from 200k after a true negative: The Ritz London
    # priced at ₹189k-219k a night, which is simply what the Ritz costs.
    # This band exists to catch a filter slider read as a room rate, not to
    # express an opinion about luxury hotels.
    "hotel": (300, 600_000),
}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            source TEXT NOT NULL,          -- 'webapp' or 'skill'
            mode TEXT NOT NULL,            -- 'product' or 'hotel'
            query TEXT NOT NULL,
            request_json TEXT,
            result_json TEXT,
            passed INTEGER NOT NULL,       -- 1/0 — every automatic criterion
            failures_json TEXT NOT NULL,   -- list of failed criterion ids
            latency_s REAL,
            subagent_reviewed INTEGER NOT NULL DEFAULT 0,
            subagent_notes TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_runs_ts ON eval_runs(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_runs_reviewed ON eval_runs(subagent_reviewed)")
    return conn


def _check_product_or_hotel(mode: str, request: dict, result: dict) -> list[str]:
    failures = []
    cheapest = result.get("cheapest", [])
    skipped = result.get("skipped", [])

    # has_proof
    if any(not r.get("screenshot") for r in cheapest):
        failures.append("has_proof")

    # no_fabricated_price
    if any(r.get("status") == "ok" and r.get("price_inr") is None for r in cheapest):
        failures.append("no_fabricated_price")

    # ranked_ascending + capped at 5
    prices = [r["price_inr"] for r in cheapest if r.get("price_inr") is not None]
    if len(cheapest) > 5 or prices != sorted(prices):
        failures.append("ranked_ascending")

    # skipped_sites_explained
    if any(not (r.get("note") or "").strip() for r in skipped):
        failures.append("skipped_sites_explained")

    # timestamped (sanity — not literally checking clock skew here, just presence)
    if not result.get("checked_at"):
        failures.append("timestamped")

    # plausible_price (coarse band per category)
    band_key = result.get("category", "general") if mode == "product" else "hotel"
    lo, hi = PLAUSIBLE_RANGES_INR.get(band_key, PLAUSIBLE_RANGES_INR["general"])
    if any(r.get("price_inr") is not None and not (lo <= r["price_inr"] <= hi) for r in cheapest):
        failures.append("plausible_price")

    if mode == "product":
        # category_site_list_used
        sites_checked = set(result.get("sites_checked", []))
        seen_sites = {r["site"] for r in cheapest + skipped}
        if not seen_sites <= (sites_checked | {r.get("site") for r in cheapest if r is not None}):
            failures.append("category_site_list_used")
    else:
        # dates_and_guests_match — response echoes the request's own inputs
        if (result.get("checkin") != request.get("checkin")
                or result.get("checkout") != request.get("checkout")
                or result.get("guests") != request.get("guests")):
            failures.append("dates_and_guests_match")

        # priced_for_requested_dates — the stricter one, and the reason it
        # exists: a hotel price for some other stay is a wrong answer, not
        # a cheaper one. Every ranked row must carry dates_accurate=True,
        # meaning the site itself was seen stating those dates.
        if any(r.get("dates_accurate") is not True for r in cheapest):
            failures.append("priced_for_requested_dates")

    return failures


def evaluate_and_log(mode: str, request: dict, result: dict, latency_s: float, source: str = "webapp") -> dict:
    """Run automatic checks and append a row to data/evals.db. Never raises
    — a logging failure must not break the actual search response."""
    query = request.get("query") or request.get("place") or "unknown"
    try:
        failures = _check_product_or_hotel(mode, request, result)
        passed = len(failures) == 0
        conn = _connect()
        conn.execute(
            "INSERT INTO eval_runs (timestamp, source, mode, query, request_json, result_json, "
            "passed, failures_json, latency_s) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                source, mode, query,
                json.dumps(request, default=str),
                json.dumps(result, default=str),
                1 if passed else 0,
                json.dumps(failures),
                latency_s,
            ),
        )
        conn.commit()
        conn.close()
        return {"passed": passed, "failures": failures}
    except Exception as exc:  # noqa: BLE001
        # Evaluation logging is best-effort observability, not a gate on
        # the user's search — swallow and report via the return value.
        return {"passed": None, "failures": [f"eval_logging_error:{type(exc).__name__}"]}


def recent_runs(limit: int = 50, only_failed: bool = False) -> list[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    q = "SELECT id, timestamp, source, mode, query, passed, failures_json, latency_s, subagent_reviewed, subagent_notes FROM eval_runs"
    if only_failed:
        q += " WHERE passed = 0"
    q += " ORDER BY id DESC LIMIT ?"
    rows = conn.execute(q, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
