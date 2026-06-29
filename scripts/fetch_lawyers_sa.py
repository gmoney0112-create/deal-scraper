#!/usr/bin/env python3
"""
Fetch lawyers & law firms — San Antonio, Bexar County + surrounding counties.

Run this on your LOCAL machine (Windows/Mac), not in the cloud container.

  cd deal-scraper
  pip install requests python-dotenv
  python scripts/fetch_lawyers_sa.py

Output:
  output/lawyers_san_antonio_bexar.csv       — all contacts
  output/apm_ready_lawyers_san_antonio.csv   — APM campaign-ready format
"""

import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
APOLLO_KEY = os.getenv("APOLLO_API_KEY")
if not APOLLO_KEY:
    sys.exit("ERROR: APOLLO_API_KEY not found in .env")

BASE = "https://api.apollo.io/v1"
HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-cache"}

# ---------------------------------------------------------------------------
# Target areas
# ---------------------------------------------------------------------------
# Bexar County + Comal + Guadalupe + Wilson + Atascosa + Medina + Kendall + Bandera
SA_METRO_LOCATIONS = [
    "San Antonio, Texas",
    "New Braunfels, Texas",
    "Boerne, Texas",
    "Seguin, Texas",
    "Schertz, Texas",
    "Converse, Texas",
    "Universal City, Texas",
    "Helotes, Texas",
    "Leon Valley, Texas",
    "Live Oak, Texas",
    "Floresville, Texas",
    "Pleasanton, Texas",
    "Hondo, Texas",
    "Castroville, Texas",
    "Bandera, Texas",
    "Kerrville, Texas",
    "Cibolo, Texas",
    "Selma, Texas",
]

ATTORNEY_TITLES = [
    "attorney",
    "partner",
    "managing partner",
    "founding partner",
    "shareholder",
    "of counsel",
    "principal attorney",
    "senior attorney",
    "associate attorney",
    "trial attorney",
    "general counsel",
    "lawyer",
    "founder",
]

# SIC 8111 = Legal Services
LEGAL_SIC = ["8111"]


# ---------------------------------------------------------------------------
# Apollo helpers
# ---------------------------------------------------------------------------

def people_search(page: int, location: str) -> dict:
    payload = {
        "api_key": APOLLO_KEY,
        "page": page,
        "per_page": 100,
        "person_titles": ATTORNEY_TITLES,
        "organization_sic_codes": LEGAL_SIC,
        "organization_locations": [location],
    }
    r = requests.post(f"{BASE}/mixed_people/search", headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def bulk_enrich(people: list[dict]) -> list[dict]:
    """Enrich up to 10 people at a time to get emails."""
    enriched = []
    batch_input = [
        {"id": p["id"], "first_name": p.get("first_name", ""), "organization_name": p.get("organization_name", "")}
        for p in people if p.get("id")
    ]
    for i in range(0, len(batch_input), 10):
        batch = batch_input[i:i+10]
        payload = {"api_key": APOLLO_KEY, "details": batch}
        try:
            r = requests.post(f"{BASE}/people/bulk_match", headers=HEADERS, json=payload, timeout=30)
            if r.ok:
                for m in r.json().get("matches", []):
                    enriched.append(m)
        except Exception as e:
            print(f"  [WARN] Enrichment batch error: {e}")
        time.sleep(0.3)
    return enriched


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Lawyers & Law Firms — San Antonio + Surrounding Counties")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    all_people: dict[str, dict] = {}  # apollo_id → person dict

    for location in SA_METRO_LOCATIONS:
        print(f"\n[{location}]")
        page = 1
        location_count = 0
        while True:
            try:
                data = people_search(page, location)
            except Exception as e:
                print(f"  [ERROR] {e}")
                break

            people = data.get("people", [])
            total = data.get("pagination", {}).get("total_entries", 0)

            if not people:
                break

            for p in people:
                pid = p.get("id")
                if pid and pid not in all_people:
                    all_people[pid] = p
                    location_count += 1

            print(f"  page {page}: {len(people)} results (total available: {total}, unique so far: {len(all_people)})")

            if page * 100 >= min(total, 500):
                break
            page += 1
            time.sleep(0.5)

        print(f"  -> {location_count} unique new contacts from {location}")

    print(f"\n{'='*60}")
    print(f"Total unique contacts found: {len(all_people)}")

    # ---------------------------------------------------------------------------
    # Enrich for emails
    # ---------------------------------------------------------------------------
    print(f"\nEnriching {len(all_people)} contacts for emails...")
    people_list = list(all_people.values())
    enriched_map: dict[str, dict] = {}

    for i in range(0, len(people_list), 10):
        batch = people_list[i:i+10]
        results = bulk_enrich(batch)
        for r in results:
            if r.get("id"):
                enriched_map[r["id"]] = r
        if (i // 10 + 1) % 10 == 0:
            print(f"  Enriched {min(i+10, len(people_list))}/{len(people_list)}...")

    print(f"  Enrichment complete: {len(enriched_map)} matched")

    # ---------------------------------------------------------------------------
    # Build output rows
    # ---------------------------------------------------------------------------
    rows = []
    for pid, p in all_people.items():
        enriched = enriched_map.get(pid, {})
        org = enriched.get("organization") or p.get("organization") or {}

        email = enriched.get("email") or p.get("email") or ""
        phone = (org.get("phone") or "").strip()
        city  = enriched.get("city") or p.get("city") or ""
        state = enriched.get("state") or p.get("state") or ""

        # Try employment history for company
        company = (org.get("name") or p.get("organization_name") or "").strip()
        if not company:
            for emp in (enriched.get("employment_history") or []):
                if emp.get("current") and emp.get("organization_name"):
                    company = emp["organization_name"]
                    break

        rows.append({
            "apollo_id":   pid,
            "First Name":  p.get("first_name") or "",
            "Last Name":   p.get("last_name") or "",
            "Title":       p.get("title") or "",
            "Company":     company,
            "Email":       email,
            "Phone":       phone,
            "City":        city,
            "State":       state,
            "LinkedIn":    p.get("linkedin_url") or "",
        })

    out_dir = Path(__file__).parent.parent / "output"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Full CSV
    full_path = out_dir / f"lawyers_san_antonio_bexar_{ts}.csv"
    with open(full_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with_email = sum(1 for r in rows if r["Email"])
    with_phone = sum(1 for r in rows if r["Phone"])
    print(f"\nSaved {len(rows)} contacts → {full_path}")
    print(f"  With email: {with_email} | With phone: {with_phone}")

    # APM-ready CSV
    apm_rows = [r for r in rows if r["Email"]]
    apm_path = out_dir / f"apm_ready_lawyers_sa_{ts}.csv"
    with open(apm_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["FirstName","LastName","Email","Company","Title","Phone","Industry"])
        writer.writeheader()
        for r in apm_rows:
            writer.writerow({
                "FirstName": r["First Name"],
                "LastName":  r["Last Name"],
                "Email":     r["Email"],
                "Company":   r["Company"],
                "Title":     r["Title"],
                "Phone":     r["Phone"],
                "Industry":  "LegalServices",
            })

    print(f"APM-ready CSV: {with_email} contacts → {apm_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
