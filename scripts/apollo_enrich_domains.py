"""
Apollo Domain Enrichment — autonomous batch script
====================================================
Run this in a Claude Code session where Apollo MCP is pre-approved
(project .claude/settings.json has "mcp__10f11cb1-0c60-42ee-ae56-c0b40a21720c__*").

This script is NOT meant to be executed directly with python3.
Instead, paste it as context into a Claude Code session and ask:
  "Run apollo_enrich_domains — enrich all 901 domains"

Claude will then call the MCP tools in a loop using this logic.
--------------------------------------------------------------------

ALGORITHM (for Claude to follow):

1. LOAD DOMAINS
   domains = [line.split("|") for line in open("output/unenriched_domains.txt")]
   → Each line: domain|business_name|category|phone|city

2. LOAD ALREADY-ENRICHED SET
   existing = json.loads(Path("/tmp/clay_enriched.json").read_text())
   already = set(c["domain"] for c in existing)
   todo = [d for d in domains if d[0] not in already]

3. FOR EACH BATCH OF 20 DOMAINS:
   a. Call apollo_mixed_people_api_search:
      - q_organization_domains_list: [d[0] for d in batch]
      - person_seniorities: ["owner", "c_suite", "vp", "director", "manager"]
      - per_page: 50
      - _conversation_ref: "enrich-aug05" (keep same across all calls)

   b. GROUP results by domain:
      for each person in results["people"]:
        org_domain = person.get("organization", {}).get("primary_domain", "")
        map domain → [person, ...]

   c. PICK BEST PERSON PER DOMAIN using seniority rank:
      RANK = ["owner","ceo","president","founder","co-founder","principal",
              "managing partner","partner","director","managing director",
              "managing attorney","attorney","broker","broker-owner",
              "office manager","general manager","vice president","vp"]
      For each person, score = min index of rank where title.lower() contains rank term
      Pick lowest score (highest seniority). Ties: pick first.

   d. COLLECT UP TO 10 people → call apollo_people_bulk_match:
      details = [{"id": person["id"], "domain": domain} for domain, person in picks]
      → Returns person["email"] if found

   e. SAVE emails to /tmp/clay_enriched.json:
      for each matched person with email:
        existing.append({"first": first, "last": last, "email": email,
                         "title": title, "domain": domain})
      Path("/tmp/clay_enriched.json").write_text(json.dumps(existing, indent=2))

4. EVERY 5 BATCHES (100 domains processed):
   - Run: python3 scripts/merge_enriched_csv.py
   - Run: git add output/ghl_enriched_20260722_0446.csv && git commit -m "enrich: ..." && git push

5. FINAL: merge + commit + push

-------------------------------------------------------------------
SENIORITY RANK (for step 3c):
-------------------------------------------------------------------
RANK = [
    "owner", "ceo", "chief executive", "president", "founder",
    "co-founder", "principal", "managing partner", "partner",
    "director", "managing director", "managing attorney",
    "attorney", "broker", "office manager", "general manager",
    "vice president", "vp"
]

def best_contact(people):
    def score(p):
        title = (p.get("title") or "").lower()
        for i, r in enumerate(RANK):
            if r in title:
                return i
        return 999
    return min(people, key=score)

-------------------------------------------------------------------
APOLLO SEARCH PARAMS:
-------------------------------------------------------------------
{
  "q_organization_domains_list": ["domain1.com", ..., "domain20.com"],
  "person_seniorities": ["owner", "c_suite", "vp", "director", "manager"],
  "per_page": 50,
  "_conversation_ref": "enrich-aug05",
  "_rationale": "Enriching senior contacts at target B2B businesses for outreach"
}

-------------------------------------------------------------------
APOLLO BULK MATCH PARAMS:
-------------------------------------------------------------------
{
  "details": [
    {"id": "abc123...", "domain": "example.com"},
    ...  (up to 10 per call)
  ],
  "_conversation_ref": "enrich-aug05",
  "_rationale": "Revealing work emails for most senior contacts found via search"
}

-------------------------------------------------------------------
SAVE CONTACTS PYTHON (run after each bulk_match):
-------------------------------------------------------------------
import json
from pathlib import Path

p = Path("/tmp/clay_enriched.json")
existing = json.loads(p.read_text()) if p.exists() else []

def add(domain, first, last, email, title):
    if not email: return
    if any(c.get("email") == email for c in existing): return
    existing.append({"first": first, "last": last,
                     "email": email, "title": title, "domain": domain})

# (call add() for each matched person with an email)

p.write_text(json.dumps(existing, indent=2))
print(f"Total contacts: {len(existing)}")

-------------------------------------------------------------------
COMMIT PATTERN (every 5 batches):
-------------------------------------------------------------------
git add output/ghl_enriched_20260722_0446.csv
git commit -m "enrich: apollo batch N, X emails (Y%) across Z domains"
git push -u origin claude/new-session-grjs70

-------------------------------------------------------------------
CREDIT ESTIMATE:
-------------------------------------------------------------------
- Search: ~45 credits (45 batches of 20 domains × 1 credit/call)
- Bulk match: up to 901 credits (1 credit per successful email match)
- Total: ~950 credits

-------------------------------------------------------------------
CONTACTS SCHEMA:
-------------------------------------------------------------------
{"first": str, "last": str, "email": str, "title": str, "domain": str}

-------------------------------------------------------------------
CONTACT STORE (ephemeral):
-------------------------------------------------------------------
/tmp/clay_enriched.json — resets on container restart.
If missing on session start, initialize with [].
"""

# This file is documentation/pseudocode for Claude to execute via MCP tools.
# It is NOT runnable as a standalone Python script.
print("This file documents the Apollo enrichment algorithm for Claude to execute.")
print("Open it in a new Claude Code session and ask: 'Run apollo_enrich_domains'")
