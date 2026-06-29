#!/usr/bin/env python3
"""
Apify-powered scraper for service-based businesses nationwide.

Platforms:
  1. google_maps  — business discovery (name, phone, address, website, rating)
  2. linkedin     — decision-maker contacts at those businesses
  3. instagram    — social presence + follower data for outreach context

Usage:
  python apify_service_scraper.py --platform google_maps --category "plumbing" --state TX
  python apify_service_scraper.py --platform all --category "roofing" --cities "Austin,TX;Dallas,TX"
  python apify_service_scraper.py --platform instagram --handles-file leads.csv
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv(Path(__file__).parent.parent / ".env")

APIFY_KEY = os.getenv("APIFY_KEY")
if not APIFY_KEY:
    sys.exit("ERROR: APIFY_KEY not found in .env")

APIFY_BASE = "https://api.apify.com/v2"

# Apify actor IDs
ACTORS = {
    "google_maps": "apify/google-maps-scraper",
    "linkedin":    "anchor/linkedin-profile-scraper",   # people search by company
    "instagram":   "apify/instagram-scraper",
    "facebook":    "apify/facebook-pages-scraper",
}

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "scraped"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Service categories → Google Maps search terms
SERVICE_CATEGORIES = {
    "plumbing":          "plumbing company",
    "hvac":              "HVAC company",
    "roofing":           "roofing company",
    "landscaping":       "landscaping company",
    "tree_service":      "tree service company",
    "pest_control":      "pest control company",
    "electrical":        "electrical contractor",
    "cleaning":          "cleaning service company",
    "pool_service":      "pool service company",
    "painting":          "painting company",
    "flooring":          "flooring company",
    "gutters":           "gutter installation company",
    "pressure_washing":  "pressure washing company",
}

# Major US metro areas for nationwide coverage
# Format: "City, State abbreviation"
NATIONWIDE_CITIES = [
    # Texas
    "San Antonio, TX", "Houston, TX", "Dallas, TX", "Austin, TX", "Fort Worth, TX",
    "El Paso, TX", "Arlington, TX", "Corpus Christi, TX", "Plano, TX", "Lubbock, TX",
    # California
    "Los Angeles, CA", "San Diego, CA", "San Jose, CA", "San Francisco, CA",
    "Fresno, CA", "Sacramento, CA", "Long Beach, CA", "Oakland, CA", "Bakersfield, CA",
    # Florida
    "Jacksonville, FL", "Miami, FL", "Tampa, FL", "Orlando, FL", "St. Petersburg, FL",
    "Hialeah, FL", "Tallahassee, FL", "Fort Lauderdale, FL",
    # New York
    "New York, NY", "Buffalo, NY", "Rochester, NY", "Yonkers, NY", "Syracuse, NY",
    # Other major metros
    "Phoenix, AZ", "Tucson, AZ", "Mesa, AZ",
    "Chicago, IL", "Aurora, IL", "Naperville, IL",
    "Philadelphia, PA", "Pittsburgh, PA",
    "Columbus, OH", "Cleveland, OH", "Cincinnati, OH",
    "Indianapolis, IN", "Fort Wayne, IN",
    "Charlotte, NC", "Raleigh, NC", "Greensboro, NC",
    "Nashville, TN", "Memphis, TN", "Knoxville, TN",
    "Las Vegas, NV", "Reno, NV",
    "Seattle, WA", "Spokane, WA",
    "Denver, CO", "Colorado Springs, CO",
    "Louisville, KY", "Lexington, KY",
    "Portland, OR",
    "Oklahoma City, OK", "Tulsa, OK",
    "Atlanta, GA", "Augusta, GA",
    "Albuquerque, NM",
    "Omaha, NE",
    "Minneapolis, MN",
    "Kansas City, MO", "St. Louis, MO",
    "Wichita, KS",
    "Mesa, AZ",
    "Virginia Beach, VA", "Norfolk, VA", "Richmond, VA",
    "Milwaukee, WI",
    "Detroit, MI", "Grand Rapids, MI",
    "Baltimore, MD",
    "Boston, MA",
    "Salt Lake City, UT",
    "New Orleans, LA", "Baton Rouge, LA",
    "Birmingham, AL",
    "Little Rock, AR",
    "Columbia, SC", "Charleston, SC",
    "Sioux Falls, SD",
    "Fargo, ND",
    "Des Moines, IA",
    "Anchorage, AK",
    "Honolulu, HI",
]

# ---------------------------------------------------------------------------
# Apify helpers
# ---------------------------------------------------------------------------

def run_actor(actor_id: str, input_data: dict, timeout_secs: int = 300) -> list[dict]:
    """Run an Apify actor synchronously and return dataset items."""
    url = f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
    params = {"token": APIFY_KEY, "timeout": timeout_secs, "memory": 512}
    resp = requests.post(url, json=input_data, params=params, timeout=timeout_secs + 30)
    if resp.status_code != 200:
        print(f"  [WARN] Actor {actor_id} returned {resp.status_code}: {resp.text[:200]}")
        return []
    try:
        return resp.json() if isinstance(resp.json(), list) else resp.json().get("items", [])
    except Exception as e:
        print(f"  [WARN] Could not parse response: {e}")
        return []


def start_actor_async(actor_id: str, input_data: dict, memory_mb: int = 1024) -> str | None:
    """Start an actor run asynchronously; return run ID."""
    url = f"{APIFY_BASE}/acts/{actor_id}/runs"
    params = {"token": APIFY_KEY, "memory": memory_mb}
    resp = requests.post(url, json=input_data, params=params, timeout=30)
    if resp.status_code not in (200, 201):
        print(f"  [WARN] Could not start {actor_id}: {resp.status_code} {resp.text[:200]}")
        return None
    return resp.json().get("data", {}).get("id")


def wait_for_run(run_id: str, poll_secs: int = 10, max_wait: int = 600) -> list[dict]:
    """Poll a run until finished; return its dataset items."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}",
            params={"token": APIFY_KEY},
            timeout=15,
        )
        status = resp.json().get("data", {}).get("status", "")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
        print(f"    run {run_id[:8]}… status={status}, waiting {poll_secs}s")
        time.sleep(poll_secs)
    dataset_id = resp.json().get("data", {}).get("defaultDatasetId")
    if not dataset_id:
        return []
    items_resp = requests.get(
        f"{APIFY_BASE}/datasets/{dataset_id}/items",
        params={"token": APIFY_KEY, "format": "json", "limit": 5000},
        timeout=30,
    )
    return items_resp.json() if items_resp.ok else []


# ---------------------------------------------------------------------------
# Platform scrapers
# ---------------------------------------------------------------------------

def scrape_google_maps(search_term: str, cities: list[str], max_per_city: int = 50) -> list[dict]:
    """
    Use apify/google-maps-scraper to find service businesses.
    Returns list of normalized business dicts.
    """
    queries = [f"{search_term} in {city}" for city in cities]
    print(f"\n[Google Maps] {len(queries)} queries for '{search_term}'")

    actor_input = {
        "searchStringsArray": queries,
        "maxCrawledPlacesPerSearch": max_per_city,
        "language": "en",
        "exportPlaceUrls": False,
        "includeHistogram": False,
        "includeOpeningHours": False,
        "includePeopleAlsoSearch": False,
        "additionalInfo": False,
        "scrapeDirectories": False,
        "deeperCityScrape": False,
    }

    items = run_actor(ACTORS["google_maps"], actor_input, timeout_secs=600)
    print(f"  → {len(items)} raw results")

    results = []
    seen = set()
    for item in items:
        phone = (item.get("phone") or "").strip()
        name = (item.get("title") or item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        results.append({
            "Business Name": name,
            "Category":      item.get("categoryName") or search_term,
            "Phone":         phone,
            "Website":       (item.get("website") or "").strip(),
            "Address":       (item.get("address") or "").strip(),
            "City":          (item.get("city") or "").strip(),
            "State":         (item.get("state") or "").strip(),
            "Zip":           (item.get("postalCode") or "").strip(),
            "Rating":        str(item.get("totalScore") or ""),
            "Reviews":       str(item.get("reviewsCount") or ""),
            "Google Maps URL": item.get("url") or "",
            "Plus Code":     item.get("plusCode") or "",
        })

    print(f"  → {len(results)} unique businesses")
    return results


def scrape_instagram(handles: list[str] = None, search_terms: list[str] = None,
                     max_posts: int = 3) -> list[dict]:
    """
    Use apify/instagram-scraper.
    Can target specific @handles OR hashtag/keyword searches.
    Returns profile + recent post data.
    """
    actor_input: dict = {
        "resultsLimit": max_posts,
        "scrapePosts": True,
        "scrapeFollowers": False,
        "scrapeFollowing": False,
    }
    if handles:
        actor_input["usernames"] = handles
        print(f"\n[Instagram] Scraping {len(handles)} handles")
    elif search_terms:
        actor_input["hashtags"] = [t.lstrip("#") for t in search_terms]
        print(f"\n[Instagram] Searching hashtags: {search_terms}")
    else:
        return []

    items = run_actor(ACTORS["instagram"], actor_input, timeout_secs=300)
    print(f"  → {len(items)} raw results")

    results = []
    for item in items:
        results.append({
            "Handle":        "@" + (item.get("username") or ""),
            "Full Name":     item.get("fullName") or "",
            "Bio":           (item.get("biography") or "").replace("\n", " "),
            "Followers":     str(item.get("followersCount") or ""),
            "Posts":         str(item.get("postsCount") or ""),
            "Website":       item.get("externalUrl") or "",
            "Business Email": item.get("businessEmail") or "",
            "Business Phone": item.get("businessPhoneNumber") or "",
            "Business Category": item.get("businessCategory") or "",
            "Profile URL":   f"https://instagram.com/{item.get('username', '')}",
            "Verified":      "Yes" if item.get("verified") else "No",
        })

    print(f"  → {len(results)} profiles")
    return results


def scrape_linkedin_company(company_names: list[str]) -> list[dict]:
    """
    Use anchor/linkedin-profile-scraper (company people search).
    NOTE: LinkedIn scraping is rate-limited; keep batches small.
    """
    print(f"\n[LinkedIn] Searching decision-makers at {len(company_names)} companies")
    results = []

    for company in company_names:
        actor_input = {
            "searchCompany": company,
            "searchKeywords": "owner OR president OR CEO OR manager OR director",
            "maxResults": 5,
        }
        items = run_actor(ACTORS["linkedin"], actor_input, timeout_secs=120)
        for item in items:
            results.append({
                "Full Name":    item.get("fullName") or "",
                "First Name":   item.get("firstName") or "",
                "Last Name":    item.get("lastName") or "",
                "Title":        item.get("headline") or "",
                "Company":      company,
                "Location":     item.get("location") or "",
                "LinkedIn URL": item.get("url") or "",
                "Profile Image": item.get("profilePicture") or "",
            })
        time.sleep(1)  # be polite

    print(f"  → {len(results)} LinkedIn profiles")
    return results


def scrape_facebook_pages(search_terms: list[str]) -> list[dict]:
    """
    Use apify/facebook-pages-scraper to find business pages.
    """
    print(f"\n[Facebook] Searching pages: {search_terms}")
    actor_input = {
        "startUrls": [],
        "searchTerms": search_terms,
        "maxPagesPerSearch": 20,
        "scrapeAbout": True,
        "scrapePosts": False,
        "scrapeReviews": False,
    }
    items = run_actor(ACTORS["facebook"], actor_input, timeout_secs=300)
    print(f"  → {len(items)} raw results")

    results = []
    for item in items:
        results.append({
            "Page Name":    item.get("title") or "",
            "Category":     item.get("categories") or "",
            "Phone":        item.get("phone") or "",
            "Website":      item.get("website") or "",
            "Address":      item.get("address") or "",
            "Email":        item.get("email") or "",
            "Likes":        str(item.get("likes") or ""),
            "Followers":    str(item.get("followers") or ""),
            "About":        (item.get("about") or "").replace("\n", " ")[:200],
            "Facebook URL": item.get("url") or "",
        })

    print(f"  → {len(results)} pages")
    return results


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def save_csv(rows: list[dict], filename: str) -> Path:
    if not rows:
        print(f"  [SKIP] No data to save for {filename}")
        return None
    out = OUTPUT_DIR / filename
    fieldnames = list(rows[0].keys())
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved {len(rows)} rows → {out}")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Apify service business scraper")
    p.add_argument("--platform", default="google_maps",
                   choices=["google_maps", "instagram", "linkedin", "facebook", "all"],
                   help="Which platform(s) to scrape")
    p.add_argument("--category", default="plumbing",
                   help="Service category key (see SERVICE_CATEGORIES) or free-form search term")
    p.add_argument("--state", default="",
                   help="Filter cities to a specific state abbreviation (e.g. TX)")
    p.add_argument("--cities", default="",
                   help="Semicolon-separated 'City, ST' list (overrides --state)")
    p.add_argument("--max-per-city", type=int, default=50,
                   help="Max results per city for Google Maps (default 50)")
    p.add_argument("--handles-file", default="",
                   help="CSV file with 'Handle' column for Instagram scraping")
    p.add_argument("--companies-file", default="",
                   help="CSV file with 'Business Name' column for LinkedIn scraping")
    p.add_argument("--nationwide", action="store_true",
                   help="Use the full nationwide city list (~80 metros)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print config and exit without calling Apify")
    return p.parse_args()


def resolve_cities(args) -> list[str]:
    if args.cities:
        return [c.strip() for c in args.cities.split(";") if c.strip()]
    if args.nationwide:
        return NATIONWIDE_CITIES
    if args.state:
        return [c for c in NATIONWIDE_CITIES if c.endswith(f", {args.state.upper()}")]
    # default: top 20 metros
    return NATIONWIDE_CITIES[:20]


def main():
    args = parse_args()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    category_key = args.category.lower().replace(" ", "_")
    search_term = SERVICE_CATEGORIES.get(category_key, args.category)
    cities = resolve_cities(args)

    print(f"=== Apify Service Scraper ===")
    print(f"Platform : {args.platform}")
    print(f"Category : {search_term}")
    print(f"Cities   : {len(cities)} locations")
    print(f"Output   : {OUTPUT_DIR}")
    if args.dry_run:
        print("\n[DRY RUN] Exiting.")
        return

    platforms = (
        ["google_maps", "instagram", "linkedin", "facebook"]
        if args.platform == "all"
        else [args.platform]
    )

    gm_results = []

    # 1. Google Maps
    if "google_maps" in platforms:
        gm_results = scrape_google_maps(search_term, cities, args.max_per_city)
        save_csv(gm_results, f"google_maps_{category_key}_{ts}.csv")

    # 2. Instagram
    if "instagram" in platforms:
        handles = []
        if args.handles_file and Path(args.handles_file).exists():
            with open(args.handles_file) as f:
                handles = [r["Handle"].lstrip("@") for r in csv.DictReader(f) if r.get("Handle")]
        else:
            hashtags = [
                f"#{category_key}",
                f"#{category_key}company",
                "#localservices",
                "#homeservices",
            ]
            ig_results = scrape_instagram(search_terms=hashtags)
            save_csv(ig_results, f"instagram_{category_key}_{ts}.csv")

        if handles:
            ig_results = scrape_instagram(handles=handles)
            save_csv(ig_results, f"instagram_handles_{category_key}_{ts}.csv")

    # 3. LinkedIn
    if "linkedin" in platforms:
        company_names = []
        if args.companies_file and Path(args.companies_file).exists():
            with open(args.companies_file) as f:
                company_names = [r["Business Name"] for r in csv.DictReader(f) if r.get("Business Name")]
        elif gm_results:
            company_names = [r["Business Name"] for r in gm_results[:50]]
        if company_names:
            li_results = scrape_linkedin_company(company_names)
            save_csv(li_results, f"linkedin_{category_key}_{ts}.csv")
        else:
            print("\n[LinkedIn] No company list available — pass --companies-file or run google_maps first")

    # 4. Facebook
    if "facebook" in platforms:
        fb_terms = [f"{search_term} {city}" for city in cities[:10]]
        fb_results = scrape_facebook_pages(fb_terms)
        save_csv(fb_results, f"facebook_{category_key}_{ts}.csv")

    print(f"\nDone. Results saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
