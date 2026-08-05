# Deal Scraper — Master Handoff

## 🛑 BLOCKED (2026-08-05) — Apollo/Clay MCP tool calls fail with -32003 in this environment

**Do not spend another session assuming a "fresh container" fixes this.** It was tried and
re-confirmed: a brand-new session on a brand-new branch (`claude/apollo-enrichment-32003-error-h8mda1`,
based straight off this branch's history) hit the *exact same* `MCP error -32003: MCP tool call
requires approval` on the very first call — `apollo_usage_stats_credit_usage_stats`, a plain
read-only stats call, no arguments.

Crucially, **`mcp__Clay__get-current-workspace` failed with the identical -32003 in the same
session**, immediately after the Apollo failure. Clay and Apollo are unrelated connectors with
separate OAuth grants — the only thing they share is that both are claude.ai-managed "connector"
MCP servers (UUID-prefixed tool names, e.g. `mcp__10f11cb1-.../*`, `mcp__58f44d22-.../*`), as
opposed to plain project-configured MCP servers. `.claude/settings.json`'s permission wildcard
does not prevent this — that file governs Claude Code's own permission prompts, not this gate.

**Conclusion: -32003 here is a live, per-call human-approval requirement on connector-style MCP
tools, and this session type (remote/headless Claude Code on the web, no interactive UI attached)
has no way to satisfy it.** It is not: stale OAuth state, a settings.json gap, or something that
clears on container restart. Retrying in a new session reproduces it identically.

**What actually needs to happen next:** run this from a session where a human is present to
click "Approve" in real time when the tool-approval dialog appears — i.e. Claude Code CLI on a
local machine, or the claude.ai chat UI, not a background/remote/triggered session. Alternatively,
check whether the Apollo and Clay connectors can be pre-authorized in claude.ai connector settings
in a way that skips per-call approval for headless sessions (unconfirmed whether that setting
exists — worth checking before another session burns time on this).

## ⚡ START HERE — New Session Checklist (once -32003 is actually resolved)

1. **Apollo is pre-approved in settings.json** — wildcard `mcp__Apollo_io__*` (and legacy `mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__*`) — this controls Claude Code's own prompts only, NOT the -32003 connector-approval gate above
2. **Load Apollo tools** via ToolSearch:
   ```
   select:mcp__Apollo_io__apollo_mixed_people_api_search,mcp__Apollo_io__apollo_people_bulk_match,mcp__Apollo_io__apollo_usage_stats_credit_usage_stats
   ```
3. **Check credits**: call `apollo_usage_stats_credit_usage_stats` (need ~950 credits; as of 2026-08-05 there are 2,970 lead credits available)
4. **Run enrichment**: follow Step 2 below — read `scripts/apollo_enrich_domains.py` for full algorithm
5. **Tomorrow only**: re-run `scripts/scrape_digital_desert_leads.py` (Google Places quota resets midnight Pacific)

### ⚠️ CRITICAL — persistence gotcha (bit a prior session on 2026-08-05)

`/tmp/clay_enriched.json` is **wiped every time the container restarts**, but
`scripts/merge_enriched_csv.py` reads ONLY that file and OVERWRITES
`output/ghl_enriched_20260722_0446.csv` from scratch — it does not merge with
what's already in the CSV. Running the merge script with a partial/fresh
`/tmp/clay_enriched.json` **destroys previously-enriched contacts**.

**The source of truth is now `output/apollo_enrichment_state.json`** (git-tracked,
survives restarts), not `/tmp/clay_enriched.json`. Every new session MUST, before
running any merge:
```python
import json
state = json.load(open("output/apollo_enrichment_state.json"))
json.dump(state["contacts"], open("/tmp/clay_enriched.json", "w"), indent=2)
```
And after adding new contacts each batch, append them into
`state["contacts"]` and rewrite `output/apollo_enrichment_state.json` (dedupe by
email) — not just `/tmp`. Then re-run `scripts/merge_enriched_csv.py` and commit
both files together.

---

## Current State (2026-08-05)

| Metric | Value |
|---|---|
| Total unique leads in master CSV | **1,423** |
| — ICP business leads (B2B) | 1,324 |
| — Digital desert leads (no website, ≤25 reviews) | 109 |
| Leads with email enriched | 109 (8.2% of 1,324 ICP rows) |
| Leads with phone | 1,384 (97.3%) |
| Target total leads | **2,500** |
| Still needed | **~1,077 more digital desert leads** |
| Apollo domains processed | 100 / 901 (batches 1-5 of 46) |
| Git branch | `claude/apollo-permissions-setup-xhndts` |
| Remote | `gmoney0112-create/deal-scraper` |

**2026-08-05 update:** Ran round 1 of Apollo domain enrichment (100 domains,
batches 0-4). Of 100 domains searched, only 18 had any people in Apollo's
database at all (most single-location small businesses have zero Apollo
footprint), and 13 of those returned a verified email via bulk_match — a
~13% domain yield. Note several matches are corporate/HQ contacts for
franchise chains (Aspen Dental, Comfort Dental, Jefferson Dental, MINT
dentistry, Familia Dental, Rodeo Dental) rather than the specific local
location's owner — Apollo only has data on the parent org for these. Contact
Title is preserved in the CSV so these can be filtered out before outreach
if a "true independent owner only" list is wanted. Extrapolating the 13%
domain yield across all 901 domains → roughly 115-120 more emails total
(~210-215 combined, ~16% coverage), below the 25-40% target in this doc's
original estimate — the original 950-credit/25-40% estimate assumed a much
higher database hit rate than small local businesses actually have.
Remaining work: batches 5-45 (801 domains). See the persistence gotcha above
before continuing.

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
