"""
Search-URL templates for every site in ../../data/category_sites.yaml and
../../data/hotel_sites.yaml.

These are best-effort public search URLs, hand-maintained the same way the
YAML site lists are — a site's search page structure changes over time, so
expect some of these to need updating. When a template is missing for a
site that's still in the YAML, the scraper falls back to a generic
"<domain>/search?q=<query>" guess and reports low confidence.

Each of the ones below was actually verified by opening it in a real
browser and confirming it lands on real results (not assumed from
convention) — except where a site is documented elsewhere as consistently
blocking automated access (see docs/TESTING.md), in which case the
template is a best guess since verifying it live isn't possible anyway.
"""
import base64
from datetime import date
from urllib.parse import quote_plus


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _pb_varint(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _pb_msg(field: int, payload: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def _pb_date(iso_date: str) -> bytes:
    y, m, d = (int(x) for x in iso_date.split("-"))
    return _pb_varint(1, y) + _pb_varint(2, m) + _pb_varint(3, d)


def google_travel_ts(checkin: str, checkout: str) -> str:
    """Google Travel carries its check-in/check-out inside a base64url
    protobuf `ts` parameter — plain `?checkin=&checkout=` params are
    ignored outright (verified live: identical prices and an unchanged
    "Sep 30 - Oct 1" on the page across three different date ranges,
    including peak-season Christmas).

    The layout below was NOT guessed. It was read off seven real `ts`
    values harvested from Google's own "popular dates" links in the page
    HTML and decoded field by field, every one of them the same shape:

        1: 0
        3 { 2 { 2 { 1 {y,m,d}   <- check-in
                    2 {y,m,d}   <- check-out
                    3: nights }
                 6 { 2: 0 }
                 7: 1 } }
        5 { 1 {} }

    And it is never trusted blind: scraper._page_confirms_dates() reads
    the dates Google renders back into its own Check-in/Check-out inputs
    and any price whose dates the page won't confirm is dropped rather
    than shown. A silently-wrong date encoding would produce confidently
    wrong prices, which is the one failure mode worth engineering against."""
    nights = (date.fromisoformat(checkout) - date.fromisoformat(checkin)).days
    stay = (_pb_msg(1, _pb_date(checkin)) + _pb_msg(2, _pb_date(checkout))
            + _pb_varint(3, max(nights, 1)))
    inner = _pb_msg(2, stay) + _pb_msg(6, _pb_varint(2, 0)) + _pb_varint(7, 1)
    body = _pb_varint(1, 0) + _pb_msg(3, _pb_msg(2, inner)) + _pb_msg(5, _pb_msg(1, b""))
    return base64.urlsafe_b64encode(body).decode().rstrip("=")


def _ddmmyyyy(iso_date: str) -> str:
    """YYYY-MM-DD -> DD/MM/YYYY, url-encoded, for sites (EaseMyTrip,
    Yatra) that take dates in that format rather than ISO."""
    y, m, d = iso_date.split("-")
    return f"{d}%2F{m}%2F{y}"


def product_search_url(domain: str, query: str) -> str:
    q = quote_plus(query)
    templates = {
        "amazon.in": f"https://www.amazon.in/s?k={q}",
        "flipkart.com": f"https://www.flipkart.com/search?q={q}",
        "myntra.com": f"https://www.myntra.com/{q.replace('+', '-')}",
        "ajio.com": f"https://www.ajio.com/search/?text={q}",
        "nykaa.com": f"https://www.nykaa.com/search/result/?q={q}",
        "nykaafashion.com": f"https://www.nykaafashion.com/catalogsearch/result/?q={q}",
        "purplle.com": f"https://www.purplle.com/search?q={q}",
        "croma.com": f"https://www.croma.com/searchB?q={q}%3Arelevance&text={q}",
        "reliancedigital.in": f"https://www.reliancedigital.in/search?q={q}%3Arelevance&searchType=default",
        "tatacliq.com": f"https://www.tatacliq.com/search/?searchCategory=all&text={q}",
        "snapdeal.com": f"https://www.snapdeal.com/search?keyword={q}",
        "pepperfry.com": f"https://www.pepperfry.com/site_product/search?q={q}",
        "urbanladder.com": f"https://www.urbanladder.com/search?q={q}",
    }
    return templates.get(domain, f"https://www.{domain}/search?q={q}")


def hotel_search_url(domain: str, place: str, checkin: str, checkout: str, guests: int) -> str:
    p = quote_plus(place)
    templates = {
        "booking.com": (
            f"https://www.booking.com/searchresults.html?ss={p}"
            f"&checkin={checkin}&checkout={checkout}&group_adults={guests}&no_rooms=1"
        ),
        "makemytrip.com": f"https://www.makemytrip.com/hotels/{p.replace('+', '-')}-hotels.html",
        "agoda.com": (
            f"https://www.agoda.com/search?city={p}"
            f"&checkIn={checkin}&checkOut={checkout}&adults={guests}"
        ),
        "goibibo.com": f"https://www.goibibo.com/hotels/find-hotels-in-{p.replace('+', '-')}/",
        # /travel/search + ts (not /travel/hotels/<slug>): the ts protobuf is
        # the only thing Google honours for dates — see google_travel_ts().
        "google.com/travel/hotels": (
            f"https://www.google.com/travel/search?q={p}"
            f"&ts={google_travel_ts(checkin, checkout)}&hl=en&gl=in&curr=INR"
        ),
        # Verified live: filling the real search form and reading the
        # resulting URL, not guessed from convention.
        "easemytrip.com": (
            f"https://www.easemytrip.com/hotel-new/search?city={p}%2C%20India"
            f"&cin={_ddmmyyyy(checkin)}&cOut={_ddmmyyyy(checkout)}"
            f"&Hotel=NA&Rooms=1&pax={guests}&ccode=IN&cid=1&stype=city"
        ),
        "yatra.com": (
            f"https://hotel.yatra.com/nextui/hotel-search/dom/search?"
            f"checkinDate={_ddmmyyyy(checkin)}&checkoutDate={_ddmmyyyy(checkout)}"
            f"&source=BOOKING_ENGINE&pg=1&tenant=B2C&isPersnldSrp=1"
            f"&city.name={p}&city.code={p}&state.name={p}&state.code={p}"
            f"&country.name=India&country.code=IND"
            f"&roomRequests%5B0%5D.id=1&roomRequests%5B0%5D.noOfAdults={guests}"
            f"&roomRequests%5B0%5D.noOfChildren=0"
        ),
    }
    return templates.get(domain, f"https://www.{domain}/search?q={p}")
