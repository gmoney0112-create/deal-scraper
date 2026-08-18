"""
Digital Desert Lead Scraper v2 — resumable, checkpointed, target-capped
==========================================================================
Same filter as scrape_digital_desert_leads.py (no real website + <=25 reviews),
but:
  - Checkpoints to output/digital_desert_scrape_state.json after every
    location, so a real-money paid-API run survives interruption without
    re-paying for completed queries.
  - Stops once a target lead count is reached (avoids over-spending past
    what's actually needed).
  - Skips San Antonio / New Braunfels / Seguin — already queried in the
    2026-08-05 partial run (109 leads: 14/36/59 respectively). Original run
    had no checkpoint, so exact per-category completion is unknown; skipping
    these three entirely avoids ambiguous duplicate spend. 33 of 36 target
    cities are completely untouched, more than enough to hit any reasonable
    target without re-querying them.
  - Runs remaining SA-metro small towns FIRST (higher digital-desert yield
    observed: small markets have more genuinely offline businesses, and
    fewer results per query = less pagination = cheaper), then the 20 TX
    major cities if the target isn't hit yet.

Usage:
    python scripts/scrape_digital_desert_v2.py --target 1000
"""

import argparse
import json
import os
import re
import sys
import time
import random
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
API_KEY = os.getenv("GOOGLE_PLACES_KEY")
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "output" / "digital_desert_scrape_state.json"

FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.nationalPhoneNumber,places.internationalPhoneNumber,"
    "places.websiteUri,places.primaryType,places.types,"
    "places.rating,places.userRatingCount"
)

MAX_REVIEWS = 25
DELAY_SECS = 1.2

SOCIAL_PATTERNS = [
    "facebook.com", "fb.com", "instagram.com", "yelp.com",
    "google.com/maps", "maps.google", "goo.gl", "linktree.com",
    "linktr.ee", "twitter.com", "x.com", "tiktok.com",
    "thumbtack.com", "angi.com", "homeadvisor.com",
]

CATEGORIES = [
    ("plumber", "Home Services / Plumbing"),
    ("electrician", "Home Services / Electrical"),
    ("HVAC contractor", "Home Services / HVAC"),
    ("roofer", "Home Services / Roofing"),
    ("painter", "Home Services / Painting"),
    ("handyman", "Home Services / Handyman"),
    ("pest control", "Home Services / Pest Control"),
    ("garage door repair", "Home Services / Garage Door"),
    ("fence company", "Home Services / Fencing"),
    ("pool service", "Home Services / Pool"),
    ("appliance repair", "Home Services / Appliance Repair"),
    ("gutter cleaning", "Home Services / Gutters"),
    ("hair salon", "Beauty / Hair Salon"),
    ("barber shop", "Beauty / Barber"),
    ("nail salon", "Beauty / Nail Salon"),
    ("massage therapist", "Beauty / Massage"),
    ("beauty salon", "Beauty / Salon"),
    ("eyelash extension", "Beauty / Lash Studio"),
    ("waxing salon", "Beauty / Waxing"),
    ("tattoo shop", "Beauty / Tattoo"),
    ("auto repair shop", "Auto Services / Repair"),
    ("tire shop", "Auto Services / Tires"),
    ("car wash", "Auto Services / Car Wash"),
    ("auto body shop", "Auto Services / Body Shop"),
    ("oil change", "Auto Services / Oil Change"),
    ("taqueria", "Food & Beverage / Taqueria"),
    ("food truck", "Food & Beverage / Food Truck"),
    ("bakery", "Food & Beverage / Bakery"),
    ("mexican restaurant", "Food & Beverage / Restaurant"),
    ("tamale shop", "Food & Beverage / Tamales"),
    ("donut shop", "Food & Beverage / Donut"),
    ("ice cream shop", "Food & Beverage / Ice Cream"),
    ("house cleaning service", "Cleaning / Residential"),
    ("janitorial service", "Cleaning / Commercial"),
    ("carpet cleaning", "Cleaning / Carpet"),
    ("pressure washing", "Cleaning / Pressure Washing"),
    ("landscaping company", "Landscaping"),
    ("lawn care service", "Landscaping / Lawn Care"),
    ("tree service", "Landscaping / Tree"),
    ("daycare", "Childcare / Daycare"),
    ("moving company", "Moving & Storage"),
    ("alterations tailor", "Retail / Alterations"),
    ("laundromat", "Retail / Laundromat"),
    ("notary public", "Professional / Notary"),
    ("locksmith", "Home Services / Locksmith"),
    ("photography studio", "Creative / Photography"),
    ("catering", "Food & Beverage / Catering"),
    ("dog grooming", "Pet Services / Grooming"),
    ("dog boarding", "Pet Services / Boarding"),
]

# already covered 2026-08-05: San Antonio, New Braunfels, Seguin — skip
LOCATIONS_SA_METRO_REMAINING = [
    "Boerne TX", "Schertz TX", "Converse TX", "Floresville TX",
    "Pleasanton TX", "Helotes TX", "Selma TX", "Live Oak TX",
    "Cibolo TX", "Universal City TX", "Leon Valley TX", "Lytle TX", "Hondo TX",
]

LOCATIONS_TX_MAJOR = [
    "Houston TX", "Austin TX", "Dallas TX", "Fort Worth TX", "El Paso TX",
    "Lubbock TX", "Corpus Christi TX", "McAllen TX", "Laredo TX", "Amarillo TX",
    "Beaumont TX", "Midland TX", "Odessa TX", "Killeen TX", "Waco TX",
    "Tyler TX", "Abilene TX", "Harlingen TX", "Brownsville TX", "Wichita Falls TX",
]

# small towns first: higher yield + cheaper (fewer results = less pagination)
ALL_LOCATIONS = LOCATIONS_SA_METRO_REMAINING + LOCATIONS_TX_MAJOR


def is_social_url(url):
    if not url:
        return True
    url_lower = url.lower().strip("/")
    return any(pat in url_lower for pat in SOCIAL_PATTERNS)


def clean_phone(raw):
    if not raw:
        return ""
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    return ""


def text_search(query, page_token=None):
    body = {"textQuery": query, "maxResultCount": 20}
    if page_token:
        body["pageToken"] = page_token
    r = requests.post(
        SEARCH_URL,
        headers={"X-Goog-Api-Key": API_KEY, "X-Goog-FieldMask": FIELD_MASK,
                 "Content-Type": "application/json"},
        json=body, timeout=15,
    )
    r.raise_for_status()
    return r.json()


def text_search_with_retry(query, page_token=None, max_retries=6):
    for attempt in range(max_retries):
        try:
            return text_search(query, page_token)
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                wait = (2 ** attempt) * 3 + random.uniform(0, 2)
                print(f"  rate limited, retry {attempt+1}/{max_retries} in {wait:.0f}s")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Max retries exceeded: {query}")


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"done_locations": [], "seen": {}, "api_calls": 0, "errors": 0}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def row_from_entry(pid, entry):
    phone = clean_phone(entry.get("phone_raw", ""))
    website = entry.get("website_raw", "")
    icp = entry["_icp_label"]
    reviews = entry["review_count"]
    rating = entry.get("rating", "")

    if reviews == 0:
        tier_tag = "0 Reviews - Highest Priority"
    elif reviews <= 5:
        tier_tag = "1-5 Reviews"
    elif reviews <= 10:
        tier_tag = "6-10 Reviews"
    else:
        tier_tag = "11-25 Reviews"

    return {
        "First Name": "", "Last Name": "", "Email": "",
        "Phone": phone, "Company Name": entry["name"], "City": entry["city"],
        "State": "TX", "Website": website,
        "Tags": f"Digital Desert, Review Automation Target, Lead Gen Target, {tier_tag}, {icp}",
        "Source": "Google Places", "ICP Category": icp, "Contact Title": "",
        "Review Count": reviews, "Rating": rating if rating else "",
    }


def run(target):
    if not API_KEY:
        sys.exit("ERROR: GOOGLE_PLACES_KEY not set in .env")

    state = load_state()
    done_locations = set(state["done_locations"])
    seen = state["seen"]

    print(f"Starting with {len(seen)} leads already collected, "
          f"{len(done_locations)} locations already done. Target: {target}")

    for location in ALL_LOCATIONS:
        if location in done_locations:
            print(f"skip {location} (already done)")
            continue
        if len(seen) >= target:
            print(f"Target reached ({len(seen)} >= {target}), stopping before {location}")
            break

        city = location.replace(" TX", "").strip()
        loc_found_before = len(seen)
        location_fully_done = True

        for query_cat, icp_label in CATEGORIES:
            if len(seen) >= target:
                location_fully_done = False  # cut short by target — NOT actually done
                break
            query = f"{query_cat} in {location}"
            page_token = None
            pages = 0

            while pages < 3:
                try:
                    if page_token:
                        time.sleep(2.5)
                    else:
                        time.sleep(DELAY_SECS)
                    data = text_search_with_retry(query, page_token)
                    state["api_calls"] += 1
                except Exception as e:
                    print(f"  FAILED: {query}: {e}")
                    state["errors"] += 1
                    break

                results = data.get("places", [])
                qualifying = 0
                for r in results:
                    pid = r.get("id")
                    if not pid or pid in seen:
                        continue
                    website = r.get("websiteUri", "")
                    reviews = r.get("userRatingCount", 0) or 0
                    if not (is_social_url(website) and reviews <= MAX_REVIEWS):
                        continue
                    phone = r.get("internationalPhoneNumber") or r.get("nationalPhoneNumber", "")
                    seen[pid] = {
                        "_icp_label": icp_label,
                        "name": r.get("displayName", {}).get("text", ""),
                        "address": r.get("formattedAddress", ""),
                        "city": city, "phone_raw": phone, "website_raw": website,
                        "rating": r.get("rating"), "review_count": reviews,
                    }
                    qualifying += 1

                if qualifying:
                    print(f"  [{location}] {query_cat}: +{qualifying} (total: {len(seen)}, "
                          f"calls: {state['api_calls']})")

                page_token = data.get("nextPageToken")
                pages += 1
                if not page_token:
                    break

        if location_fully_done:
            done_locations.add(location)
            state["done_locations"] = list(done_locations)
        save_state(state)
        status = "done" if location_fully_done else "PARTIAL (target hit mid-location, will resume here)"
        print(f"=== {location} {status}: +{len(seen) - loc_found_before} leads "
              f"(running total: {len(seen)}, api_calls so far: {state['api_calls']}) ===")

    save_state(state)
    print(f"\nFinal: {len(seen)} unique leads, {state['api_calls']} API calls, {state['errors']} errors")

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = ROOT / "output" / f"digital_desert_leads_v2_{ts}.csv"
    fields = ["First Name", "Last Name", "Email", "Phone", "Company Name", "City",
               "State", "Website", "Tags", "Source", "ICP Category", "Contact Title",
               "Review Count", "Rating"]

    import csv
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for pid, entry in seen.items():
            w.writerow(row_from_entry(pid, entry))

    print(f"Output: {out_path}")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1000)
    args = ap.parse_args()
    run(args.target)
