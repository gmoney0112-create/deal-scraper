"""
Scrape "digital desert" businesses — those with NO real website and ≤25 Google reviews.
These are prime prospects for:
  - Website / lead gen services
  - Review automation (get more reviews)
  - Marketing automation / AI tools

Targets home services, beauty/wellness, auto services, food, cleaning, landscaping,
and other local niches where many operators are still offline.

Output: output/digital_desert_leads_TIMESTAMP.csv
"""

import os, csv, time, json, re, random, requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
API_KEY    = os.getenv("GOOGLE_PLACES_KEY")
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Include rating + userRatingCount (key additions vs. existing scraper)
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.nationalPhoneNumber,places.internationalPhoneNumber,"
    "places.websiteUri,places.primaryType,places.types,"
    "places.rating,places.userRatingCount"
)

# ── Filter thresholds ─────────────────────────────────────────────────────────
MAX_REVIEWS = 25    # "very few" reviews
DELAY_SECS  = 1.2   # base delay between API calls (avoid QPS throttling)

# ── "Social / directory" URL patterns → treat as no real website ──────────────
SOCIAL_PATTERNS = [
    "facebook.com", "fb.com", "instagram.com", "yelp.com",
    "google.com/maps", "maps.google", "goo.gl", "linktree.com",
    "linktr.ee", "twitter.com", "x.com", "tiktok.com",
    "thumbtack.com", "angi.com", "homeadvisor.com",
]

# ── ICP categories targeting offline / under-reviewed businesses ──────────────
CATEGORIES = [
    # Home Services
    ("plumber",              "Home Services / Plumbing"),
    ("electrician",          "Home Services / Electrical"),
    ("HVAC contractor",      "Home Services / HVAC"),
    ("roofer",               "Home Services / Roofing"),
    ("painter",              "Home Services / Painting"),
    ("handyman",             "Home Services / Handyman"),
    ("pest control",         "Home Services / Pest Control"),
    ("garage door repair",   "Home Services / Garage Door"),
    ("fence company",        "Home Services / Fencing"),
    ("pool service",         "Home Services / Pool"),
    ("appliance repair",     "Home Services / Appliance Repair"),
    ("gutter cleaning",      "Home Services / Gutters"),
    # Beauty & Wellness
    ("hair salon",           "Beauty / Hair Salon"),
    ("barber shop",          "Beauty / Barber"),
    ("nail salon",           "Beauty / Nail Salon"),
    ("massage therapist",    "Beauty / Massage"),
    ("beauty salon",         "Beauty / Salon"),
    ("eyelash extension",    "Beauty / Lash Studio"),
    ("waxing salon",         "Beauty / Waxing"),
    ("tattoo shop",          "Beauty / Tattoo"),
    # Auto Services
    ("auto repair shop",     "Auto Services / Repair"),
    ("tire shop",            "Auto Services / Tires"),
    ("car wash",             "Auto Services / Car Wash"),
    ("auto body shop",       "Auto Services / Body Shop"),
    ("oil change",           "Auto Services / Oil Change"),
    # Food & Beverage
    ("taqueria",             "Food & Beverage / Taqueria"),
    ("food truck",           "Food & Beverage / Food Truck"),
    ("bakery",               "Food & Beverage / Bakery"),
    ("mexican restaurant",   "Food & Beverage / Restaurant"),
    ("tamale shop",          "Food & Beverage / Tamales"),
    ("donut shop",           "Food & Beverage / Donut"),
    ("ice cream shop",       "Food & Beverage / Ice Cream"),
    # Cleaning
    ("house cleaning service", "Cleaning / Residential"),
    ("janitorial service",   "Cleaning / Commercial"),
    ("carpet cleaning",      "Cleaning / Carpet"),
    ("pressure washing",     "Cleaning / Pressure Washing"),
    # Landscaping & Outdoor
    ("landscaping company",  "Landscaping"),
    ("lawn care service",    "Landscaping / Lawn Care"),
    ("tree service",         "Landscaping / Tree"),
    # Other local services
    ("daycare",              "Childcare / Daycare"),
    ("moving company",       "Moving & Storage"),
    ("alterations tailor",   "Retail / Alterations"),
    ("laundromat",           "Retail / Laundromat"),
    ("notary public",        "Professional / Notary"),
    ("locksmith",            "Home Services / Locksmith"),
    ("photography studio",   "Creative / Photography"),
    ("catering",             "Food & Beverage / Catering"),
    ("dog grooming",         "Pet Services / Grooming"),
    ("dog boarding",         "Pet Services / Boarding"),
]

# ── SA Metro (primary) + major TX cities (expansion) ─────────────────────────
LOCATIONS_SA_METRO = [
    "San Antonio TX",
    "New Braunfels TX",
    "Seguin TX",
    "Boerne TX",
    "Schertz TX",
    "Converse TX",
    "Floresville TX",
    "Pleasanton TX",
    "Helotes TX",
    "Selma TX",
    "Live Oak TX",
    "Cibolo TX",
    "Universal City TX",
    "Leon Valley TX",
    "Lytle TX",
    "Hondo TX",
]

LOCATIONS_TX_MAJOR = [
    "Houston TX",
    "Austin TX",
    "Dallas TX",
    "Fort Worth TX",
    "El Paso TX",
    "Lubbock TX",
    "Corpus Christi TX",
    "McAllen TX",
    "Laredo TX",
    "Amarillo TX",
    "Beaumont TX",
    "Midland TX",
    "Odessa TX",
    "Killeen TX",
    "Waco TX",
    "Tyler TX",
    "Abilene TX",
    "Harlingen TX",
    "Brownsville TX",
    "Wichita Falls TX",
]

ALL_LOCATIONS = LOCATIONS_SA_METRO + LOCATIONS_TX_MAJOR

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_social_url(url: str) -> bool:
    """Return True if the URL is a social/directory page or empty (no real site)."""
    if not url:
        return True
    url_lower = url.lower().strip("/")
    return any(pat in url_lower for pat in SOCIAL_PATTERNS)


def text_search(query: str, page_token: str = None) -> dict:
    body = {"textQuery": query, "maxResultCount": 20}
    if page_token:
        body["pageToken"] = page_token
    r = requests.post(
        SEARCH_URL,
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": FIELD_MASK,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def text_search_with_retry(query: str, page_token: str = None, max_retries: int = 6) -> dict:
    """Retry on 429 with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return text_search(query, page_token)
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                wait = (2 ** attempt) * 3 + random.uniform(0, 2)
                print(f"  ⚠ 429 rate limit — retry {attempt+1}/{max_retries} in {wait:.0f}s …")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Max retries exceeded: {query}")


def clean_phone(raw: str) -> str:
    if not raw:
        return ""
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    return ""


# ── Main scrape ───────────────────────────────────────────────────────────────

def scrape() -> list[dict]:
    seen: dict[str, dict] = {}   # place_id → raw entry

    total_queries = len(CATEGORIES) * len(ALL_LOCATIONS)
    done = 0
    api_calls = 0
    errors = 0

    for location in ALL_LOCATIONS:
        city = location.replace(" TX", "").strip()
        for query_cat, icp_label in CATEGORIES:
            query = f"{query_cat} in {location}"
            done += 1

            print(f"[{done}/{total_queries}] {query}  (found so far: {len(seen)})")

            page_token = None
            pages = 0

            while pages < 3:
                try:
                    if page_token:
                        time.sleep(2.5)   # Google requires delay before next_page_token
                    else:
                        time.sleep(DELAY_SECS)
                    data = text_search_with_retry(query, page_token)
                    api_calls += 1
                except Exception as e:
                    print(f"  ✗ Failed: {e}")
                    errors += 1
                    break

                results = data.get("places", [])
                qualifying_this_page = 0

                for r in results:
                    pid = r.get("id")
                    if not pid or pid in seen:
                        continue

                    website  = r.get("websiteUri", "")
                    reviews  = r.get("userRatingCount", 0) or 0
                    rating   = r.get("rating")

                    # ── Digital desert filter ──────────────────────────────
                    no_real_website = is_social_url(website)
                    low_reviews     = reviews <= MAX_REVIEWS

                    if not (no_real_website and low_reviews):
                        continue

                    phone = r.get("internationalPhoneNumber") or r.get("nationalPhoneNumber", "")

                    seen[pid] = {
                        "_icp_label":   icp_label,
                        "name":         r.get("displayName", {}).get("text", ""),
                        "address":      r.get("formattedAddress", ""),
                        "city":         city,
                        "phone_raw":    phone,
                        "website_raw":  website,
                        "rating":       rating,
                        "review_count": reviews,
                    }
                    qualifying_this_page += 1

                if qualifying_this_page:
                    print(f"  ✓ +{qualifying_this_page} | total: {len(seen)}")

                page_token = data.get("nextPageToken")
                pages += 1
                if not page_token:
                    break

    print(f"\n✓ {len(seen)} unique leads ({api_calls} API calls, {errors} errors).\n")

    # ── Build final rows ──────────────────────────────────────────────────────
    rows = []
    for pid, entry in seen.items():
        phone   = clean_phone(entry.get("phone_raw", ""))
        website = entry.get("website_raw", "")
        icp     = entry["_icp_label"]
        reviews = entry["review_count"]
        rating  = entry.get("rating", "")

        # Tag tier based on review count
        if reviews == 0:
            tier_tag = "0 Reviews - Highest Priority"
        elif reviews <= 5:
            tier_tag = "1-5 Reviews"
        elif reviews <= 10:
            tier_tag = "6-10 Reviews"
        else:
            tier_tag = "11-25 Reviews"

        rows.append({
            "First Name":    "",
            "Last Name":     "",
            "Email":         "",
            "Phone":         phone,
            "Company Name":  entry["name"],
            "City":          entry["city"],
            "State":         "TX",
            "Website":       website,   # empty or social link = no real site
            "Tags":          f"Digital Desert, Review Automation Target, Lead Gen Target, {tier_tag}, {icp}",
            "Source":        "Google Places",
            "ICP Category":  icp,
            "Contact Title": "",
            "Review Count":  reviews,
            "Rating":        rating if rating else "",
        })

    return rows


def main():
    if not API_KEY:
        raise SystemExit("ERROR: GOOGLE_PLACES_KEY not set in .env")

    print("=" * 70)
    print("  Digital Desert Lead Scraper")
    print(f"  {len(CATEGORIES)} categories × {len(ALL_LOCATIONS)} locations = {len(CATEGORIES)*len(ALL_LOCATIONS)} queries")
    print(f"  Filter: no real website AND ≤{MAX_REVIEWS} Google reviews")
    print("=" * 70 + "\n")

    rows = scrape()

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = Path("output") / f"digital_desert_leads_{ts}.csv"
    out_path.parent.mkdir(exist_ok=True)

    fields = [
        "First Name", "Last Name", "Email", "Phone", "Company Name",
        "City", "State", "Website", "Tags", "Source", "ICP Category",
        "Contact Title", "Review Count", "Rating",
    ]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # ── Summary ──────────────────────────────────────────────────────────────
    with_phone = sum(1 for r in rows if r["Phone"])
    with_0_rev = sum(1 for r in rows if int(r["Review Count"]) == 0)
    with_web   = sum(1 for r in rows if r["Website"])

    print(f"\n{'=' * 70}")
    print(f"  Total digital-desert leads : {len(rows):,}")
    print(f"  With phone number          : {with_phone:,}")
    print(f"  Zero reviews               : {with_0_rev:,}")
    print(f"  Has social/dir URL         : {with_web:,}  (still no real site)")
    print(f"\n  Output → {out_path}")
    print("=" * 70)

    by_cat: dict[str, int] = {}
    for r in rows:
        cat = r["ICP Category"]
        by_cat[cat] = by_cat.get(cat, 0) + 1

    print("\nBreakdown by ICP category:")
    for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {cat:<50} {n:>4}")

    print(f"\n  Target was ~1,176+ | Got: {len(rows):,}")
    return out_path


if __name__ == "__main__":
    main()
