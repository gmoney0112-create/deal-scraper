"""
Scrape 1,000-1,500 prime AI-automation ICP businesses from Google Places.
Covers Bexar County + surrounding counties + Texas statewide.
"""

import os, csv, time, json, re, requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GOOGLE_PLACES_KEY")
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
SEARCH_URL  = "https://maps.googleapis.com/maps/api/place/textsearch/json"

# ── ICP categories (businesses that clearly benefit from AI automation) ──────
CATEGORIES = [
    "law firm",
    "medical clinic",
    "dental office",
    "real estate agency",
    "accounting firm",
    "insurance agency",
    "financial advisor",
    "mortgage company",
    "marketing agency",
    "staffing agency",
    "general contractor",
    "auto dealership",
    "property management company",
    "IT managed services",
    "title company",
    "logistics company",
]

# ── Geographic targets ───────────────────────────────────────────────────────
BEXAR_AND_SURROUNDING = [
    "San Antonio TX",
    "New Braunfels TX",
    "Seguin TX",
    "Boerne TX",
    "Kerrville TX",
    "Floresville TX",
    "Hondo TX",
    "Pleasanton TX",
    "Schertz TX",
    "Converse TX",
]

TX_STATEWIDE = [
    "Houston TX",
    "Austin TX",
    "Dallas TX",
    "Fort Worth TX",
    "Corpus Christi TX",
    "Lubbock TX",
    "El Paso TX",
]

ALL_LOCATIONS = BEXAR_AND_SURROUNDING + TX_STATEWIDE

# ── Helpers ──────────────────────────────────────────────────────────────────
def text_search(query, page_token=None):
    params = {"query": query, "key": API_KEY}
    if page_token:
        params["pagetoken"] = page_token
    r = requests.get(SEARCH_URL, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def place_details(place_id):
    params = {
        "place_id": place_id,
        "fields": "name,formatted_phone_number,website,formatted_address,types",
        "key": API_KEY,
    }
    r = requests.get(DETAILS_URL, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("result", {})

def clean_phone(raw):
    if not raw:
        return ""
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    return ""

def infer_category(types, query_category):
    mapping = {
        "law firm":                  "Legal Services",
        "medical clinic":            "Medical / Healthcare",
        "dental office":             "Dental",
        "real estate agency":        "Real Estate",
        "accounting firm":           "Accounting / Finance",
        "insurance agency":          "Insurance",
        "financial advisor":         "Financial Advisory",
        "mortgage company":          "Mortgage",
        "marketing agency":          "Marketing Agency",
        "staffing agency":           "Staffing / HR",
        "general contractor":        "Construction / Contractor",
        "auto dealership":           "Auto Dealership",
        "property management company": "Property Management",
        "IT managed services":       "IT / MSP",
        "title company":             "Title / Closing",
        "logistics company":         "Logistics / Trucking",
    }
    return mapping.get(query_category, query_category.title())

# ── Main scrape ──────────────────────────────────────────────────────────────
def scrape():
    seen = {}   # place_id → row
    total_queries = len(CATEGORIES) * len(ALL_LOCATIONS)
    done = 0

    for location in ALL_LOCATIONS:
        for category in CATEGORIES:
            query = f"{category} in {location}"
            done += 1
            print(f"[{done}/{total_queries}] {query}")

            page_token = None
            pages = 0
            while pages < 3:   # max 3 pages = 60 results per query
                try:
                    if page_token:
                        time.sleep(2.2)   # Google requires delay before next_page_token
                    data = text_search(query, page_token)
                except Exception as e:
                    print(f"  ⚠ search error: {e}")
                    break

                results = data.get("results", [])
                for r in results:
                    pid = r.get("place_id")
                    if pid and pid not in seen:
                        seen[pid] = {
                            "_place_id":   pid,
                            "_query_cat":  category,
                            "_location":   location,
                            "name":        r.get("name", ""),
                            "address":     r.get("formatted_address", ""),
                        }

                page_token = data.get("next_page_token")
                pages += 1
                if not page_token:
                    break

                print(f"  → page {pages+1}, {len(seen)} unique so far")

            time.sleep(0.3)

    print(f"\n✓ {len(seen)} unique places found. Fetching details…\n")

    rows = []
    ids = list(seen.keys())
    for i, pid in enumerate(ids):
        entry = seen[pid]
        try:
            det = place_details(pid)
            phone_raw = det.get("formatted_phone_number", "")
            website   = det.get("website", "")
            types     = det.get("types", [])
        except Exception as e:
            print(f"  ⚠ details error on {entry['name']}: {e}")
            phone_raw, website, types = "", "", []

        city_state = entry["_location"]
        city  = city_state.split(" TX")[0].strip()
        state = "TX"

        rows.append({
            "Business Name":  entry["name"],
            "Address":        entry["address"],
            "City":           city,
            "State":          state,
            "Phone":          clean_phone(phone_raw),
            "Website":        website,
            "ICP Category":   infer_category(types, entry["_query_cat"]),
            "Tags":           f"AI Automation ICP, {infer_category(types, entry['_query_cat'])}",
            "Source":         "Google Places",
            "Source Location": city_state,
        })

        if (i + 1) % 50 == 0:
            print(f"  Details: {i+1}/{len(ids)} done")
        time.sleep(0.12)

    return rows

def main():
    print(f"Starting AI-automation ICP scrape — {len(CATEGORIES)} categories × {len(ALL_LOCATIONS)} locations")
    print(f"Target: 1,000–1,500 unique businesses\n")

    rows = scrape()

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = Path("output") / f"ai_automation_icps_{ts}.csv"
    out_path.parent.mkdir(exist_ok=True)

    fields = ["Business Name","Address","City","State","Phone","Website","ICP Category","Tags","Source","Source Location"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # Also write a GHL-ready version (matching their import column names)
    ghl_path = Path("output") / f"ghl_ai_automation_icps_{ts}.csv"
    ghl_fields = ["First Name","Last Name","Email","Phone","Company Name","City","State","Website","Tags","Source"]
    with open(ghl_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ghl_fields)
        w.writeheader()
        for r in rows:
            w.writerow({
                "First Name":   "",
                "Last Name":    "",
                "Email":        "",
                "Phone":        r["Phone"],
                "Company Name": r["Business Name"],
                "City":         r["City"],
                "State":        r["State"],
                "Website":      r["Website"],
                "Tags":         r["Tags"],
                "Source":       r["Source"],
            })

    with_phone = sum(1 for r in rows if r["Phone"])
    with_web   = sum(1 for r in rows if r["Website"])

    print(f"\n{'='*60}")
    print(f"  Total businesses : {len(rows):,}")
    print(f"  With phone       : {with_phone:,}")
    print(f"  With website     : {with_web:,}")
    print(f"\n  Full CSV   → {out_path}")
    print(f"  GHL CSV    → {ghl_path}")
    print(f"{'='*60}")

    by_cat = {}
    for r in rows:
        c = r["ICP Category"]
        by_cat[c] = by_cat.get(c, 0) + 1
    print("\nBreakdown by ICP category:")
    for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {cat:<35} {n:>5}")

if __name__ == "__main__":
    main()
