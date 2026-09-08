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
from urllib.parse import quote_plus


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
        "google.com/travel/hotels": f"https://www.google.com/travel/hotels/{p.replace('+', '-')}",
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
