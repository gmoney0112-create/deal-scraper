"""
Merge Clay-enriched contacts with the Google Places ICP CSV.
Outputs a GHL-ready enriched CSV.
"""
import csv, json, re
from pathlib import Path
from urllib.parse import urlparse
import glob, sys

def domain_from_url(url):
    if not url:
        return ""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = parsed.netloc or parsed.path
        host = re.sub(r"^www\.", "", host).strip().rstrip("/")
        return host.lower().split("/")[0]
    except Exception:
        return ""

SENIORITY_KEYWORDS = [
    "owner", "ceo", "president", "founder", "co-founder", "principal",
    "managing partner", "partner", "director", "managing director",
    "managing attorney", "attorney", "broker", "broker-owner",
    "office manager", "general manager", "vice president", "vp",
]

def seniority_score(title):
    t = (title or "").lower()
    for i, kw in enumerate(SENIORITY_KEYWORDS):
        if kw in t:
            return len(SENIORITY_KEYWORDS) - i
    return 0

def main():
    # Find most recent ICP CSV
    candidates = sorted(glob.glob("output/ai_automation_icps_*.csv"), reverse=True)
    if not candidates:
        print("No ICP CSV found. Run scrape_ai_automation_icps.py first.")
        sys.exit(1)
    input_csv = candidates[0]
    print(f"ICP CSV   : {input_csv}")

    # Load enriched contacts
    enriched_path = Path("/tmp/clay_enriched.json")
    if not enriched_path.exists():
        print("No enriched contacts file found.")
        sys.exit(1)

    with open(enriched_path) as f:
        contacts = json.load(f)
    print(f"Contacts  : {len(contacts)} enriched records")

    # Build domain → best contact map
    domain_contacts = {}
    for c in contacts:
        d = c.get("domain", "")
        if not d:
            continue
        if d not in domain_contacts:
            domain_contacts[d] = []
        domain_contacts[d].append(c)

    # Pick most senior contact per domain
    def best_contact(contact_list):
        ranked = sorted(contact_list, key=lambda c: seniority_score(c.get("title", "")), reverse=True)
        return ranked[0]

    best_by_domain = {d: best_contact(cl) for d, cl in domain_contacts.items()}
    print(f"Domains   : {len(best_by_domain)} unique domains with contacts")

    # Load ICP rows
    with open(input_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"Businesses: {len(rows)}")

    # Merge
    enriched_count = 0
    ghl_rows = []
    for r in rows:
        domain = domain_from_url(r.get("Website", ""))
        contact = best_by_domain.get(domain, {})
        if contact:
            enriched_count += 1

        ghl_rows.append({
            "First Name":    contact.get("first", ""),
            "Last Name":     contact.get("last", ""),
            "Email":         contact.get("email", ""),
            "Phone":         r.get("Phone", ""),
            "Company Name":  r.get("Business Name", ""),
            "City":          r.get("City", ""),
            "State":         r.get("State", "TX"),
            "Website":       r.get("Website", ""),
            "Tags":          r.get("Tags", ""),
            "Source":        r.get("Source", "Google Places"),
            "ICP Category":  r.get("ICP Category", ""),
            "Contact Title": contact.get("title", ""),
        })

    # Write output
    ts = input_csv.split("icps_")[1].replace(".csv", "")
    out_path = Path("output") / f"ghl_enriched_{ts}.csv"
    ghl_fields = ["First Name","Last Name","Email","Phone","Company Name","City","State","Website","Tags","Source","ICP Category","Contact Title"]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ghl_fields)
        w.writeheader()
        w.writerows(ghl_rows)

    with_email = sum(1 for r in ghl_rows if r["Email"])
    with_phone = sum(1 for r in ghl_rows if r["Phone"])

    print(f"\n{'='*55}")
    print(f"  Total businesses   : {len(ghl_rows):,}")
    print(f"  With phone         : {with_phone:,}")
    print(f"  With email         : {with_email:,}  ({with_email/len(ghl_rows)*100:.1f}%)")
    print(f"\n  GHL CSV → {out_path}")
    print(f"{'='*55}")

    # Show sample of enriched
    enriched_sample = [r for r in ghl_rows if r["Email"]][:10]
    print("\nSample enriched contacts:")
    for r in enriched_sample:
        print(f"  {r['First Name']} {r['Last Name']} <{r['Email']}> — {r['Company Name']} ({r['Contact Title']})")

if __name__ == "__main__":
    main()
