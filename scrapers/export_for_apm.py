#!/usr/bin/env python3
"""
Export deal-scraper leads into APM campaign-ready CSV format.

Maps any lead CSV (Apollo-enriched or Apify-scraped) into the column
structure expected by apm_campaign.py:
  FirstName, LastName, Email, Company, Title, Industry

Industry is auto-detected from job title + company name keywords, or
can be forced with --industry.

Usage:
  # Export Apollo plumbing/HVAC leads
  python scrapers/export_for_apm.py \
      --input output/all_plumbing_hvac_leads.csv \
      --output output/apm_ready_hvac.csv

  # Export Apify Google Maps results
  python scrapers/export_for_apm.py \
      --input output/scraped/google_maps_roofing_20250101_120000.csv \
      --output output/apm_ready_roofing.csv \
      --industry Roofing

  # Deduplicate against existing APM contacts
  python scrapers/export_for_apm.py \
      --input output/all_plumbing_hvac_leads.csv \
      --output output/apm_ready_hvac.csv \
      --existing /path/to/APM_ColdEmail_Contacts_GMass.csv
"""

import argparse
import csv
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Industry detection
# ---------------------------------------------------------------------------

INDUSTRY_RULES: list[tuple[str, list[str]]] = [
    ("PropertyManagement", [
        "property manag", "property manager", "leasing", "landlord",
        "real estate manag", "hoa", "homeowner assoc", "tenant",
    ]),
    ("HVAC", [
        "hvac", "heating", "cooling", "air condition", "refrigerat",
        "plumb", "pipefitter", "mechanical contractor",
    ]),
    ("Roofing", [
        "roof", "shingle", "gutter", "exterior contractor",
    ]),
    ("Landscaping", [
        "landscap", "lawn", "tree service", "tree trim", "arborist",
        "horticulture", "irrigation", "sod",
    ]),
    ("PestControl", [
        "pest control", "exterminator", "termite", "bug", "rodent",
    ]),
    ("Electrical", [
        "electric", "electrician", "wiring", "low voltage",
    ]),
    ("Cleaning", [
        "cleaning", "janitorial", "maid", "housekeeping", "sanit",
    ]),
    ("PoolService", [
        "pool", "spa service", "aquatic",
    ]),
    ("Painting", [
        "paint", "coatings", "stucco",
    ]),
    ("Flooring", [
        "floor", "carpet", "tile", "hardwood", "vinyl",
    ]),
    ("PressureWashing", [
        "pressure wash", "power wash", "soft wash",
    ]),
    ("GeneralContractor", [
        "general contractor", "construction", "remodel", "renovation",
        "handyman",
    ]),
]


def detect_industry(title: str, company: str) -> str:
    text = (title + " " + company).lower()
    for industry, keywords in INDUSTRY_RULES:
        if any(kw in text for kw in keywords):
            return industry
    return "HomeServices"


# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------

# Maps source CSV header variants → normalized name
COLUMN_ALIASES: dict[str, str] = {
    # Apollo enriched output
    "first name":  "FirstName",
    "last name":   "LastName",
    "email":       "Email",
    "company":     "Company",
    "title":       "Title",
    "phone":       "Phone",
    "city":        "City",
    "state":       "State",
    # Apify Google Maps output
    "business name": "Company",
    "phone":         "Phone",
    "website":       "Website",
    "address":       "Address",
    "rating":        "Rating",
    "category":      "Category",
    "google maps url": "GoogleMapsURL",
}


def normalize_headers(row: dict) -> dict:
    out = {}
    for key, val in row.items():
        normalized = COLUMN_ALIASES.get(key.lower().strip(), key.strip())
        out[normalized] = val.strip() if isinstance(val, str) else val
    return out


# ---------------------------------------------------------------------------
# Core export
# ---------------------------------------------------------------------------

def load_existing_emails(path: str) -> set[str]:
    existing = set()
    p = Path(path)
    if not p.exists():
        print(f"[WARN] Existing contacts file not found: {path}")
        return existing
    with open(p, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            email = (row.get("Email") or "").strip().lower()
            if email:
                existing.add(email)
    print(f"  Loaded {len(existing)} existing APM emails for deduplication")
    return existing


def export(input_path: str, output_path: str,
           force_industry: str = "",
           existing_path: str = "",
           require_email: bool = True) -> None:

    input_p = Path(input_path)
    if not input_p.exists():
        sys.exit(f"ERROR: Input file not found: {input_path}")

    existing_emails = load_existing_emails(existing_path) if existing_path else set()

    rows_in = 0
    rows_out = 0
    skipped_no_email = 0
    skipped_dupe = 0
    output_rows: list[dict] = []

    with open(input_p, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            rows_in += 1
            row = normalize_headers(raw)

            # Handle Apify Google Maps rows — no individual name, use company
            first = row.get("FirstName") or row.get("First Name") or ""
            last  = row.get("LastName")  or row.get("Last Name")  or ""
            company = row.get("Company") or ""
            title   = row.get("Title")   or row.get("Category")   or ""
            email   = row.get("Email")   or ""
            phone   = row.get("Phone")   or ""

            # For Google Maps rows without personal names, leave name blank
            # APM campaign handles company-only contacts fine
            if not first and not last and company:
                first = company
                last  = ""

            if require_email and not email:
                skipped_no_email += 1
                continue

            email_lc = email.lower()
            if email_lc in existing_emails:
                skipped_dupe += 1
                continue

            industry = force_industry or detect_industry(title, company)

            output_rows.append({
                "FirstName": first,
                "LastName":  last,
                "Email":     email,
                "Company":   company,
                "Title":     title,
                "Phone":     phone,
                "Industry":  industry,
            })

            existing_emails.add(email_lc)  # prevent dupes within this run
            rows_out += 1

    if not output_rows:
        print("No rows to export.")
        return

    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with open(out_p, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(output_rows[0].keys()))
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nExport complete:")
    print(f"  Input rows      : {rows_in}")
    print(f"  Exported        : {rows_out}")
    print(f"  Skipped (no email)  : {skipped_no_email}")
    print(f"  Skipped (duplicate) : {skipped_dupe}")
    print(f"  Output          : {out_p}")

    # Industry breakdown
    from collections import Counter
    industry_counts = Counter(r["Industry"] for r in output_rows)
    print("\n  Industry breakdown:")
    for ind, cnt in industry_counts.most_common():
        print(f"    {ind}: {cnt}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Export leads to APM campaign CSV format")
    p.add_argument("--input",    required=True, help="Source CSV (Apollo or Apify output)")
    p.add_argument("--output",   required=True, help="Destination APM-ready CSV")
    p.add_argument("--industry", default="",
                   help="Force a specific industry tag (skips auto-detect)")
    p.add_argument("--existing", default="",
                   help="Path to APM_ColdEmail_Contacts_GMass.csv for deduplication")
    p.add_argument("--allow-no-email", action="store_true",
                   help="Include rows without email (for phone-only outreach)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    export(
        input_path=args.input,
        output_path=args.output,
        force_industry=args.industry,
        existing_path=args.existing,
        require_email=not args.allow_no_email,
    )
