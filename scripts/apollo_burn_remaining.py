"""
Fast, broad domain-based enrichment to use remaining Apollo credits before reset.
All 901 ICP domains, broadened seniority, no per-domain cap (take everyone found,
dedupe against existing emails). Domain-verified (organization.primary_domain
cross-check) so safe despite the speed.
"""
import json, os, re, sys, time
from pathlib import Path
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "output" / "apollo_enrichment_state.json"
DOMAINS_FILE = ROOT / "output" / "unenriched_domains.txt"
SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
BULK_MATCH_URL = "https://api.apollo.io/api/v1/people/bulk_match"
SUFFIXES = re.compile(r"\b(llc|inc|incorporated|corp|corporation|co|ltd|pllc|pc|dba|lp|llp)\b")

def load_api_key():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("APOLLO_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("no key")

def normalize_name(name):
    n = (name or "").lower()
    n = re.sub(r"[^a-z0-9\s]", " ", n)
    n = SUFFIXES.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()

def main():
    headers = {"x-api-key": load_api_key(), "Content-Type": "application/json"}
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    existing_emails = set(c["email"].lower() for c in state["contacts"])

    domains = []
    name_by_domain = {}
    with open(DOMAINS_FILE, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("|")
            if len(parts) >= 2:
                domains.append(parts[0])
                name_by_domain[parts[0]] = parts[1]

    print(f"Domains to sweep broadly: {len(domains)}")
    batches = [domains[i:i+20] for i in range(0, len(domains), 20)]
    new_total = 0

    for bi, batch in enumerate(batches):
        resp = requests.post(SEARCH_URL, headers=headers, json={
            "q_organization_domains_list": batch,
            "person_seniorities": ["owner","founder","c_suite","vp","director","manager","senior","entry"],
            "per_page": 50,
        }, timeout=30)
        if resp.status_code == 402:
            print("OUT OF CREDITS. Stopping."); break
        if resp.status_code != 200:
            print(f"batch {bi} search failed {resp.status_code}"); continue

        people = resp.json().get("people", [])
        name_to_domain = {normalize_name(name_by_domain[d]): d for d in batch}
        by_domain = {}
        for p in people:
            org_name = normalize_name((p.get("organization") or {}).get("name"))
            dom = name_to_domain.get(org_name)
            if not dom:
                for norm_name, d in name_to_domain.items():
                    if len(norm_name) >= 4 and len(org_name) >= 4 and (norm_name in org_name or org_name in norm_name):
                        dom = d; break
            if dom:
                by_domain.setdefault(dom, []).append(p)

        picks = [(d, p) for d, plist in by_domain.items() for p in plist]
        print(f"batch {bi+1}/{len(batches)}: {len(people)} people, {len(picks)} candidates")

        for ci in range(0, len(picks), 10):
            chunk = picks[ci:ci+10]
            details = [{"id": p["id"]} for _, p in chunk]
            mresp = requests.post(BULK_MATCH_URL, headers=headers,
                params={"reveal_personal_emails":"false","reveal_phone_number":"false"},
                json={"details": details}, timeout=30)
            if mresp.status_code == 402:
                print("OUT OF CREDITS on match. Stopping.")
                STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
                return
            if mresp.status_code != 200:
                continue
            matches = mresp.json().get("matches", [])
            for (attributed_dom, person), match in zip(chunk, matches):
                if not match:
                    continue
                email = match.get("email")
                if not email or email.lower() in existing_emails:
                    continue
                true_dom = ((match.get("organization") or {}).get("primary_domain") or attributed_dom).lower().lstrip("www.")
                if true_dom != attributed_dom:
                    continue
                state["contacts"].append({
                    "domain": true_dom, "first": match.get("first_name",""),
                    "last": match.get("last_name",""), "email": email,
                    "title": match.get("title",""), "broad_sweep": True,
                })
                existing_emails.add(email.lower())
                new_total += 1
                print(f"  + {email} ({true_dom} - {match.get('title','')})")

        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        time.sleep(0.3)

    print(f"DONE. new={new_total} total={len(state['contacts'])}")

if __name__ == "__main__":
    main()
