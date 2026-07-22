# Apollo Enrichment Handoff — Deal Scraper

## Mission
Enrich 901 remaining unenriched business domains with contact emails using the Apollo MCP server,
merge them into the GHL-ready CSV, and commit + push to `claude/new-session-grjs70`.

---

## Current State (as of 2026-07-22, batch 10 complete)

| Metric | Value |
|---|---|
| Total ICP businesses | 1,324 |
| Enriched contacts in JSON | 620 contacts across 76 domains |
| Current GHL CSV | `output/ghl_enriched_20260722_0446.csv` |
| Emails enriched | 91 (6.9%) |
| Remaining unenriched domains | **901** |
| Git branch | `claude/new-session-grjs70` |
| Remote | `gmoney0112-create/deal-scraper` |

The enriched contacts JSON lives at `/tmp/clay_enriched.json` (ephemeral — resets on container
restart). If it doesn't exist, rebuild it by running each `scripts/save_batch*.py` file in order,
OR start fresh (the merge script will just produce fewer emails).

---

## Apollo MCP Tools Available

The Apollo MCP server ID is `10f11cb1-0c60-42ee-ae56-c0b40a21720c`. Load these tools via ToolSearch:

```
select:mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__apollo_mixed_people_api_search,mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__apollo_people_bulk_match,mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__apollo_usage_stats_credit_usage_stats,mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__apollo_users_api_profile
```

### Permission setup
The global settings file at `/root/.claude/settings.json` should already allow Apollo calls:
```json
{
  "permissions": {
    "allow": ["mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__*"]
  }
}
```
If calls still prompt for approval, the user has pre-approved: **Allow all Apollo calls globally**.

---

## Enrichment Strategy (Credit-Efficient)

**Step 1 — Search** (`apollo_mixed_people_api_search`): 1 credit per request with results.
- Pass up to 20 domains at once via `q_organization_domains_list`
- Filter by senior seniority: `["owner", "c_suite", "vp", "director", "manager"]`
- Request 25-50 results per page
- Cost: ~45–50 credits for all 901 domains (batches of 20)

**Step 2 — Bulk Enrich** (`apollo_people_bulk_match`): 1 credit per person matched, max 10/call.
- For each domain, take the most senior person from search results
- Pass up to 10 people per bulk match call
- This returns `email` directly (work email, no waterfall needed)
- Cost: up to 901 credits (1 per successful match)

**Total estimated cost: ~950 credits.**

The user explicitly approved: "Do all 1000" and "Allow Apollo calls globally."

---

## Seniority Ranking (pick highest rank per domain)

```
owner > ceo > president > founder > co-founder > principal >
managing partner > partner > director > managing director >
managing attorney > attorney > broker > broker-owner >
office manager > general manager > vice president > vp
```

Use substring matching (lowercase). Apollo seniority tiers map: `c_suite` = CEO/president/founder,
`vp` = VP/vice president, `director` = director/managing director, `manager` = office/general manager.

---

## Unenriched Domains (901 total)

Full list: `output/unenriched_domains.txt`
Format: `domain|business_name|category|phone|city`

### Category breakdown
```
 98  Dental
 78  Real Estate
 78  Auto Dealership
 67  Insurance
 66  Construction / Contractor
 61  Marketing Agency
 60  Accounting / Finance
 56  Legal Services
 55  Property Management
 53  Mortgage
 46  Medical / Healthcare
 39  Logistics / Trucking
 37  Staffing / HR
 35  IT / MSP
 32  Financial Advisory
 27  Title / Closing
```

### Domains already enriched (skip these 76):
```python
ALREADY_ENRICHED = {
    "carabinshaw.com","jagamezlaw.com","dunhamlaw.com","aguirrelawpllc.com",
    "treyporterlaw.com","soyarsmorganlaw.com","seanhenricksen.com",
    "rebeccagonzalezlawfirm.com","zealousadvocate.com","felixgonzalezlaw.com",
    "johnstonmouton.com","texaslaborlaw.com","davislawfirm.us","norciolawfirm.com",
    "jbwelchlaw.com","rmfclawoffice.com","houstontaxattorneys.com",
    "reyeslaw.co","kingsleyandassociates.com","tlcfamilylaw.com",
    "demarcolaw.com","ameriestatelaw.com","myexlawyer.com",
    "aafmaa.com","jmfinancialgroup.com","pfgprivatewealth.com",
    "mystrategicadvisor.com","strategicfinancialwm.com",
    "acinsurance.us","insurancebykarl.com","texasstatefinancial.com",
    "texaswestinsurance.com","teamfarmer.com",
    "satorirealtytx.com","moseleyhomes.com","alamotx.com",
    "jbgoodwin.com","phyllisbrowne.com","neimanrealty.com",
    "kwsanantonio.com","phyllisguidry.com","austinrealestate.com",
    "mysanantoniohomesearch.com","theschradergroup.com","sahomeguide.net",
    "rpmalamo.com","cloverleafpropertymanagement.com",
    "gfshomeloans.com","contigotechnology.com",
    "emergencydental.com","exquisitesa.com","lafamiliainsurance.com",
    "newhorizonmortgage.com","2tenmarketing.com","muniz.cpa","cjtxaudit.com",
    "libertymgt.net","rentwerx.com",
    "trinitylegalgroup.com","texasfirsttitlesa.com",
    "sanantoniolandscape.com","jdpaving.com",
    "digitalfirstmarketing.com","marketingsanantonio.com",
    "saaccounting.com","taxofficeusa.com",
    "txstaffing.com","staffmarksa.com",
    # (full list in /tmp/clay_enriched.json — read domains from there)
}
```
**Actually: load the real enriched domain list dynamically:**
```python
import json
from pathlib import Path
enriched = json.loads(Path("/tmp/clay_enriched.json").read_text())
ALREADY_ENRICHED = set(c["domain"] for c in enriched)
```

---

## How to Save Apollo Results

After each batch of Apollo enrichments, append contacts to `/tmp/clay_enriched.json`:

```python
import json
from pathlib import Path

existing = json.loads(Path("/tmp/clay_enriched.json").read_text())

def add(domain, first, last, email, title):
    if not email:
        return
    # Avoid duplicates
    if any(c["email"] == email for c in existing):
        return
    existing.append({
        "first": first,
        "last": last,
        "email": email,
        "title": title,
        "domain": domain
    })

# ... add contacts here ...

Path("/tmp/clay_enriched.json").write_text(json.dumps(existing, indent=2))
print(f"Total contacts: {len(existing)}")
```

**Contact schema:** `{"first": str, "last": str, "email": str, "title": str, "domain": str}`

If `/tmp/clay_enriched.json` doesn't exist (container restart), create it with `[]` as the base,
or run the merge script directly against whatever contacts you have.

---

## Merge Script

After saving contacts, run:
```bash
cd /home/user/deal-scraper
python3 scripts/merge_enriched_csv.py
```

This reads `/tmp/clay_enriched.json`, picks the most senior contact per domain, and outputs
`output/ghl_enriched_20260722_0446.csv` with columns:
`First Name, Last Name, Email, Phone, Company Name, City, State, Website, Tags, Source, ICP Category, Contact Title`

---

## Commit & Push After Each Batch

```bash
git add output/ghl_enriched_20260722_0446.csv
git commit -m "enrich: apollo batch N, X emails (Y%) across Z domains"
git push -u origin claude/new-session-grjs70
```

Branch: `claude/new-session-grjs70`
Remote: `gmoney0112-create/deal-scraper`

---

## Batch Processing Loop (pseudocode)

```
1. Load unenriched domains from output/unenriched_domains.txt
2. Load already-enriched domains from /tmp/clay_enriched.json
3. Filter to domains not yet enriched

For each batch of 20 domains:
  a. Call apollo_mixed_people_api_search with:
     - q_organization_domains_list: [domain1, domain2, ..., domain20]
     - person_seniorities: ["owner", "c_suite", "vp", "director", "manager"]
     - per_page: 50
  b. For each domain, find the most senior person in results
  c. Collect up to 10 people → call apollo_people_bulk_match
     - Pass id (Apollo ID from search) + domain for each person
     - Response includes email field directly
  d. Save any emails found to /tmp/clay_enriched.json
  e. Every 5 batches (100 domains), run merge + commit

4. Final merge + commit + push
```

---

## Apollo Search Parameters

```json
{
  "q_organization_domains_list": ["domain1.com", "domain2.com"],
  "person_seniorities": ["owner", "c_suite", "vp", "director", "manager"],
  "per_page": 50,
  "_conversation_ref": "apollo-enrichment-run",
  "_rationale": "Finding senior contacts at target businesses for email enrichment"
}
```

**Note:** `apollo_mixed_people_api_search` does NOT return emails. You must call
`apollo_people_bulk_match` with the Apollo `id` from search results to get emails.
Pass `id` (the 24-char hex Apollo ID) — don't try to re-match by name/domain alone.

---

## Key Files

| File | Purpose |
|---|---|
| `/tmp/clay_enriched.json` | Enriched contacts store (ephemeral) |
| `output/ai_automation_icps_20260722_0446.csv` | Source ICP CSV (1,324 businesses) |
| `output/ghl_enriched_20260722_0446.csv` | Output GHL CSV |
| `output/unenriched_domains.txt` | 901 domains to enrich |
| `scripts/merge_enriched_csv.py` | Merge script |

---

## Success Criteria

- All 901 domains attempted via Apollo
- `/tmp/clay_enriched.json` has maximum contacts saved
- `output/ghl_enriched_20260722_0446.csv` reflects all enriched contacts
- Final commit pushed to `claude/new-session-grjs70`

Target: 25-40% email coverage (300-500 of 1,324 businesses with emails), up from 6.9%.
