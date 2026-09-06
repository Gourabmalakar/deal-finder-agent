#!/usr/bin/env bash
# UserPromptSubmit hook: notices when a message looks like a deal-finding
# request (a shopping-site or hotel-site URL, or hotel-search phrasing) and
# reminds Claude to follow the matching skill and the report contract in
# docs/REPORT-FORMATS.md. Prints nothing (no extra context) when nothing
# matches, and never blocks the prompt either way.
set -euo pipefail

input="$(cat)"

# Pull the prompt text out of the hook's JSON payload. Fall back to the raw
# input if python3 isn't available or the shape is unexpected, so this hook
# degrades to a no-op rather than breaking prompt submission.
prompt="$(printf '%s' "$input" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("prompt", ""))
except Exception:
    pass
' 2>/dev/null || true)"

lc="$(printf '%s' "$prompt" | tr '[:upper:]' '[:lower:]')"

ecommerce_domains='amazon\.(in|com)|flipkart\.com|myntra\.com|ajio\.com|nykaa\.com|purplle\.com|tatacliq\.com|croma\.com|reliancedigital\.in|snapdeal\.com|pepperfry\.com|urbanladder\.com'
hotel_domains='booking\.com|makemytrip\.com|agoda\.com|goibibo\.com|expedia\.|cleartrip\.com|travel/hotels'
hotel_words='hotel|hotels|resort|homestay|check-?in|check-?out|per night'

if printf '%s' "$lc" | grep -qE "$ecommerce_domains"; then
  cat <<'EOF'
This message contains a shopping-site link. Follow the ecommerce-deal-finder
skill (.claude/skills/ecommerce-deal-finder/SKILL.md) and the report
contract in docs/REPORT-FORMATS.md: identify the exact product/variant, pick
the top-5 sites for its category from data/category_sites.yaml, verify each
price live with the Browser tool (screenshot as proof), check price history,
and log verified prices to data/price_history.csv.
EOF
elif printf '%s' "$lc" | grep -qE "$hotel_domains|$hotel_words"; then
  cat <<'EOF'
This message looks like a hotel search. Follow the hotel-deal-finder skill
(.claude/skills/hotel-deal-finder/SKILL.md) and the report contract in
docs/REPORT-FORMATS.md: confirm place, dates, and guest count if any are
missing, check the sites in data/hotel_sites.yaml (plus any links supplied),
verify each rate live with the Browser tool (screenshot as proof), and log
verified rates to data/price_history.csv.
EOF
fi

exit 0
