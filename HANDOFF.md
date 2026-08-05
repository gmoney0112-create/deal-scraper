# Deal Scraper — Master Handoff

## ⚡ START HERE — New Session Checklist

1. **Apollo is pre-approved** — `.claude/settings.json` has wildcard `mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__*`, no per-call prompts in new sessions
2. **Load Apollo tools** via ToolSearch:
   ```
   select:mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__apollo_mixed_people_api_search,mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__apollo_people_bulk_match,mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__apollo_usage_stats_credit_usage_stats
   ```
3. **Check credits**: call `apollo_usage_stats_credit_usage_stats` (need ~950 credits)
4. **Run enrichment**: follow Step 2 below — read `scripts/apollo_enrich_domains.py` for full algorithm
5. **Tomorrow only**: re-run `scripts/scrape_digital_desert_leads.py` (Google Places quota resets midnight Pacific)

---

## Current State (2026-08-05)

| Metric | Value |
|---|---|
| Total unique leads in master CSV | **1,423** |
| — ICP business leads (B2B) | 1,324 |
| — Digital desert leads (no website, ≤25 reviews) | 109 |
| Leads with email enriched | 90 (6.3%) |
| Leads with phone | 1,384 (97.3%) |
| Target total leads | **2,500** |
| Still needed | **~1,077 more digital desert leads** |
| Git branch | `claude/new-session-grjs70` |
| Remote | `gmoney0112-create/deal-scraper` |

---

## Key Files

| File | Purpose |
|---|---|
| `output/ghl_master_leads_20260805.csv` | **Master GHL CSV** — all 1,423 leads |
| `output/ghl_enriched_20260722_0446.csv` | Original 1,324 ICP leads (enriched, 90 emails) |
| `output/digital_desert_leads_20260805_1609.csv` | 109 digital desert leads (no website + ≤25 reviews) |
| `output/unenriched_domains.txt` | 901 ICP domains needing Apollo enrichment |
| `output/ai_automation_icps_20260722_0446.csv` | Source ICP CSV (1,324 businesses) |
| `scripts/scrape_digital_desert_leads.py` | Digital desert scraper (run when quota resets) |
| `scripts/scrape_ai_automation_icps.py` | Original ICP scraper |
| `scripts/merge_enriched_csv.py` | Merge enriched contacts → GHL CSV |
| `/tmp/clay_enriched.json` | Enriched contacts store (ephemeral, resets on restart) |

---

## Immediate Next Steps (Priority Order)

### Step 1 — Scrape more digital desert leads (quota resets daily at midnight Pacific)

The Google Places API has a **100 requests/day** limit. We used all 100 today.
Run the scraper again TOMORROW (or in a new day session):

```bash
cd /home/user/deal-scraper
python3 scripts/scrape_digital_desert_leads.py
```

This will run 1,764 queries (49 categories × 36 locations: SA metro + major TX cities).
Expected yield: **~1,100–1,500 digital desert leads** (targeting 1,176 more to reach 2,500 total).

After running, merge into master CSV:
```python
python3 - << 'EOF'
import csv
from pathlib import Path
from datetime import datetime

all_fields = [
    "First Name","Last Name","Email","Phone","Company Name",
    "City","State","Website","Tags","Source","ICP Category",
    "Contact Title","Review Count","Rating"
]

seen_keys = set()
all_rows = []

for src in sorted(Path("output").glob("*.csv")):
    if src.name.startswith("ghl_master") or src.name.startswith("ghl_enriched") or \
       src.name.startswith("ghl_ai") or src.name.startswith("ghl_all"):
        continue
    if "digital_desert" in src.name or "ai_automation_icps" in src.name:
        with open(src, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row.get("Company Name","").lower().strip(),
                       row.get("City","").lower().strip())
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_rows.append({f: row.get(f,"") for f in all_fields})

ts = datetime.now().strftime("%Y%m%d")
out = Path(f"output/ghl_master_leads_{ts}.csv")
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=all_fields)
    w.writeheader()
    w.writerows(all_rows)
print(f"Master CSV: {len(all_rows)} leads → {out}")
EOF
```

Then commit:
```bash
git add output/ scripts/
git commit -m "scrape: digital desert batch 2, X leads (total Y/2500)"
git push -u origin claude/new-session-grjs70
```

---

### Step 2 — Apollo enrichment of existing 901 ICP domains

**Apollo MCP server ID:** `10f11cb1-0c60-42ee-ae56-c0b40a21720c`

Load tools via ToolSearch:
```
select:mcp__Apollo_io__apollo_mixed_people_api_search,mcp__Apollo_io__apollo_people_bulk_match,mcp__Apollo_io__apollo_usage_stats_credit_usage_stats,mcp__Apollo_io__apollo_users_api_profile
```

**Note:** The Apollo MCP tools now appear with prefix `mcp__Apollo_io__` (not `mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__`).
The `mcp__Apollo_io__*` tools are the same server — use whichever appears in your session's deferred tools list.

**Permission setup** — global settings allow Apollo calls:
```json
{ "permissions": { "allow": ["mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__*", "mcp__Apollo_io__*"] } }
```

#### Credit-Efficient Strategy

**Step A — Search** (`apollo_mixed_people_api_search`): 1 credit per request
- Pass up to 20 domains at once via `q_organization_domains_list`
- Filter by senior seniority: `["owner", "c_suite", "vp", "director", "manager"]`
- Request 50 results per page
- Cost: ~45 credits for 901 domains (45 batches of 20)

**Step B — Bulk Match** (`apollo_people_bulk_match`): 1 credit per match, max 10/call
- For each domain, take the most senior person from search results
- Pass Apollo `id` (24-char hex) — do NOT try to match by name/domain alone
- Returns `email` directly
- Cost: up to 901 credits (1 per successful match)

**Total estimated: ~950 credits**

#### Seniority Ranking (pick highest per domain)
```
owner > ceo > president > founder > co-founder > principal >
managing partner > partner > director > managing director >
managing attorney > attorney > broker > broker-owner >
office manager > general manager > vice president > vp
```

#### Domains to enrich
File: `output/unenriched_domains.txt` (901 domains, format: `domain|business_name|category|phone|city`)

#### Enrichment Loop Pseudocode
```
1. Load output/unenriched_domains.txt
2. Load /tmp/clay_enriched.json (or create [] if missing)
3. Filter to domains not yet in enriched set

For each batch of 20 domains:
  a. Call apollo_mixed_people_api_search:
     - q_organization_domains_list: [d1, d2, ..., d20]
     - person_seniorities: ["owner", "c_suite", "vp", "director", "manager"]
     - per_page: 50
  b. Per domain: pick most senior person from results
  c. Collect up to 10 people → call apollo_people_bulk_match (pass Apollo id)
  d. Save emails to /tmp/clay_enriched.json
  e. Every 5 batches: run merge + commit

4. Final merge + commit + push
```

#### Save Contacts After Each Batch
```python
import json
from pathlib import Path

p = Path("/tmp/clay_enriched.json")
existing = json.loads(p.read_text()) if p.exists() else []

def add(domain, first, last, email, title):
    if not email: return
    if any(c["email"] == email for c in existing): return
    existing.append({"first": first, "last": last, "email": email,
                     "title": title, "domain": domain})

# ... add contacts here ...

p.write_text(json.dumps(existing, indent=2))
print(f"Total: {len(existing)} contacts")
```

#### Run Merge Script
```bash
cd /home/user/deal-scraper
python3 scripts/merge_enriched_csv.py
```

---

### Step 3 — Enrich digital desert leads (no website — use name+city match)

Digital desert businesses have no domain, so Apollo domain search won't work.
Use `apollo_mixed_people_api_search` with company name:

```json
{
  "q_organization_name": "Joe's Plumbing",
  "person_titles": ["owner", "manager", "operator"],
  "q_organization_locations": ["San Antonio, Texas"],
  "person_seniorities": ["owner", "c_suite", "manager"],
  "per_page": 5
}
```

For each match, run `apollo_people_bulk_match` with the Apollo `id` to get email.
Credit cost is the same: 1 per search result page, 1 per bulk match.

---

## Apollo Search Parameters (domains)

```json
{
  "q_organization_domains_list": ["domain1.com", "domain2.com"],
  "person_seniorities": ["owner", "c_suite", "vp", "director", "manager"],
  "per_page": 50,
  "_rationale": "Finding senior contacts at target businesses for email enrichment"
}
```

---

## GHL CSV Columns

`First Name, Last Name, Email, Phone, Company Name, City, State, Website, Tags, Source, ICP Category, Contact Title, Review Count, Rating`

---

## Commit & Push Pattern

```bash
git add output/ scripts/ HANDOFF.md
git commit -m "enrich: apollo batch N, X emails (Y%) across Z domains"
git push -u origin claude/new-session-grjs70
```

Branch: `claude/new-session-grjs70`
Remote: `gmoney0112-create/deal-scraper`

---

## Digital Desert Scraper Details

**Script:** `scripts/scrape_digital_desert_leads.py`

**Filter:**
- `websiteUri` is empty OR is a social/directory link (Facebook, Yelp, Instagram, etc.)
- `userRatingCount` ≤ 25 (parameterized as `MAX_REVIEWS`)

**Categories (49 total):** Plumbing, Electrical, HVAC, Roofing, Painting, Handyman, Pest Control,
Garage Door, Fencing, Pool Service, Appliance Repair, Gutters, Hair Salon, Barber, Nail Salon,
Massage, Beauty Salon, Lash Studio, Waxing, Tattoo, Auto Repair, Tires, Car Wash, Body Shop,
Oil Change, Taqueria, Food Truck, Bakery, Mexican Restaurant, Tamale Shop, Donut Shop, Ice Cream,
House Cleaning, Janitorial, Carpet Cleaning, Pressure Washing, Landscaping, Lawn Care, Tree Service,
Daycare, Moving Company, Alterations, Laundromat, Notary, Locksmith, Photography, Catering,
Dog Grooming, Dog Boarding

**Locations (36 total):**
- SA Metro (16): San Antonio, New Braunfels, Seguin, Boerne, Schertz, Converse, Floresville,
  Pleasanton, Helotes, Selma, Live Oak, Cibolo, Universal City, Leon Valley, Lytle, Hondo
- TX Major Cities (20): Houston, Austin, Dallas, Fort Worth, El Paso, Lubbock, Corpus Christi,
  McAllen, Laredo, Amarillo, Beaumont, Midland, Odessa, Killeen, Waco, Tyler, Abilene,
  Harlingen, Brownsville, Wichita Falls

**Rate limiting:** 1.2s delay between queries + exponential backoff on 429 (up to 6 retries)
**Page token delay:** 2.5s (required by Google)

---

## Success Criteria

- [ ] 2,500 total unique leads in `output/ghl_master_leads_DATE.csv`
- [ ] Apollo enrichment complete for all 901 ICP domains
- [ ] `/tmp/clay_enriched.json` has maximum contacts saved
- [ ] All enriched contacts merged into master GHL CSV
- [ ] Final commit pushed to `claude/new-session-grjs70`

**Target email coverage:** 25-40% (625–1,000 of 2,500 businesses)
