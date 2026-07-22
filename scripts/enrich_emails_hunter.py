"""
Email enrichment via Hunter.io domain search.
Takes the Google Places ICP CSV and finds decision-maker emails by domain.
Run locally — Hunter API is blocked from the remote container.
"""

import os, csv, re, time, requests
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()
HUNTER_KEY = os.getenv("HUNTER_API_KEY")
SEARCH_URL  = "https://api.hunter.io/v2/domain-search"

DECISION_MAKER_TITLES = {
    "owner", "ceo", "president", "founder", "co-founder", "principal",
    "managing partner", "partner", "director", "managing director",
    "managing attorney", "attorney", "broker", "broker-owner",
    "office manager", "general manager", "vice president", "vp",
}

def domain_from_url(url):
    if not url:
        return ""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = parsed.netloc or parsed.path
        host = re.sub(r"^www\.", "", host).strip().rstrip("/")
        return host.lower()
    except Exception:
        return ""

def best_email(emails):
    """Pick the most senior contact from Hunter results."""
    if not emails:
        return {}, ""

    def title_score(e):
        t = (e.get("position") or "").lower()
        for kw in ["owner","ceo","president","founder","managing partner","principal","partner","director","attorney","broker"]:
            if kw in t:
                return 1
        return 0

    ranked = sorted(emails, key=lambda e: (
        title_score(e),
        1 if e.get("confidence", 0) >= 70 else 0,
        e.get("confidence", 0)
    ), reverse=True)

    top = ranked[0]
    return top, top.get("value", "")

def hunter_domain_search(domain, limit=10):
    params = {
        "domain": domain,
        "api_key": HUNTER_KEY,
        "limit": limit,
        "type": "personal",   # personal = direct work emails, not info@
    }
    r = requests.get(SEARCH_URL, params=params, timeout=15)
    if r.status_code == 429:
        print("  Rate limited — sleeping 60s")
        time.sleep(60)
        r = requests.get(SEARCH_URL, params=params, timeout=15)
    r.raise_for_status()
    data = r.json().get("data", {})
    return data.get("emails", []), data.get("organization", "")

def main():
    import glob, sys

    # Find the most recent ICP output CSV
    candidates = sorted(glob.glob("output/ai_automation_icps_*.csv"), reverse=True)
    if not candidates:
        print("No ICP CSV found. Run scrape_ai_automation_icps.py first.")
        sys.exit(1)

    input_csv = candidates[0]
    print(f"Input  : {input_csv}")

    with open(input_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    out_fields = [
        "Business Name", "Address", "City", "State", "Phone", "Website",
        "ICP Category", "Tags", "Source", "Source Location",
        # enriched
        "Contact First Name", "Contact Last Name", "Contact Title",
        "Contact Email", "Email Confidence",
    ]

    ts = input_csv.split("icps_")[1].replace(".csv","")
    out_path = Path("output") / f"ai_automation_icps_enriched_{ts}.csv"
    ghl_path = Path("output") / f"ghl_ai_automation_icps_enriched_{ts}.csv"

    enriched = 0
    skipped_no_domain = 0

    results = []
    domains_done = {}   # domain → (email, person) to avoid re-calling

    for i, row in enumerate(rows):
        domain = domain_from_url(row.get("Website",""))

        if not domain:
            skipped_no_domain += 1
            row.update({"Contact First Name":"","Contact Last Name":"","Contact Title":"","Contact Email":"","Email Confidence":""})
            results.append(row)
            continue

        # Reuse if same domain already looked up (e.g. chain with multiple locations)
        if domain in domains_done:
            cached = domains_done[domain]
            row.update(cached)
            results.append(row)
            if cached.get("Contact Email"):
                enriched += 1
            continue

        try:
            emails, _ = hunter_domain_search(domain)
            top, email_addr = best_email(emails)
            first = top.get("first_name","")
            last  = top.get("last_name","")
            title = top.get("position","")
            conf  = top.get("confidence","")

            enrichment = {
                "Contact First Name": first,
                "Contact Last Name":  last,
                "Contact Title":      title,
                "Contact Email":      email_addr,
                "Email Confidence":   conf,
            }
            domains_done[domain] = enrichment
            row.update(enrichment)
            results.append(row)

            if email_addr:
                enriched += 1
                print(f"  [{i+1}/{len(rows)}] ✓ {row['Business Name']} → {email_addr} ({conf}% conf)")
            else:
                print(f"  [{i+1}/{len(rows)}] — {row['Business Name']} ({domain}) no email found")

        except Exception as e:
            print(f"  [{i+1}/{len(rows)}] ✗ {row['Business Name']}: {e}")
            row.update({"Contact First Name":"","Contact Last Name":"","Contact Title":"","Contact Email":"","Email Confidence":""})
            results.append(row)

        time.sleep(0.5)  # ~2 req/s, well under Hunter's 100 req/s limit

    # Write full enriched CSV
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        w.writerows(results)

    # Write GHL-ready version
    ghl_fields = ["First Name","Last Name","Email","Phone","Company Name","City","State","Website","Tags","Source","ICP Category","Contact Title"]
    with open(ghl_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ghl_fields)
        w.writeheader()
        for r in results:
            w.writerow({
                "First Name":    r.get("Contact First Name",""),
                "Last Name":     r.get("Contact Last Name",""),
                "Email":         r.get("Contact Email",""),
                "Phone":         r.get("Phone",""),
                "Company Name":  r.get("Business Name",""),
                "City":          r.get("City",""),
                "State":         r.get("State",""),
                "Website":       r.get("Website",""),
                "Tags":          r.get("Tags",""),
                "Source":        r.get("Source",""),
                "ICP Category":  r.get("ICP Category",""),
                "Contact Title": r.get("Contact Title",""),
            })

    print(f"\n{'='*60}")
    print(f"  Total businesses       : {len(results):,}")
    print(f"  With email found       : {enriched:,}  ({enriched/len(results)*100:.0f}%)")
    print(f"  No domain (skipped)    : {skipped_no_domain:,}")
    print(f"\n  Enriched CSV → {out_path}")
    print(f"  GHL CSV      → {ghl_path}")

if __name__ == "__main__":
    main()
