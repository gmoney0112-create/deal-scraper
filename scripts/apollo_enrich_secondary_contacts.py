"""
Apollo secondary-contact enrichment — 2nd/3rd contact at already-verified domains
====================================================================================
The main ICP domain enrichment (apollo_enrich_rest.py) deliberately picked only the
SINGLE highest-seniority person per domain to save credits during the initial sweep.
This script revisits the 252 domains that already produced a verified real contact
and looks for additional senior people at those SAME domains — zero false-positive
risk (unlike name-based search) since a domain match is unambiguous, we already
proved these companies are real.

Usage:
    python scripts/apollo_enrich_secondary_contacts.py
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
STATE_FILE = ROOT / "output" / "apollo_enrichment_state.json"
DOMAINS_FILE = ROOT / "output" / "unenriched_domains.txt"

SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
BULK_MATCH_URL = "https://api.apollo.io/api/v1/people/bulk_match"

RANK = [
    "owner", "ceo", "chief executive", "president", "founder",
    "co-founder", "principal", "managing partner", "partner",
    "director", "managing director", "managing attorney",
    "attorney", "broker", "office manager", "general manager",
    "vice president", "vp",
]

BATCH_SIZE = 20
BULK_CHUNK = 10
MAX_ADDITIONAL_PER_DOMAIN = 2
SUFFIXES = re.compile(r"\b(llc|inc|incorporated|corp|corporation|co|ltd|pllc|pc|dba|lp|llp)\b")


def normalize_name(name):
    n = (name or "").lower()
    n = re.sub(r"[^a-z0-9\s]", " ", n)
    n = SUFFIXES.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def load_api_key():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("APOLLO_API_KEY="):
                return line.split("=", 1)[1].strip()
    key = os.environ.get("APOLLO_API_KEY")
    if not key:
        sys.exit("APOLLO_API_KEY not found in .env or environment")
    return key


def seniority_score(title):
    title = (title or "").lower()
    for i, r in enumerate(RANK):
        if r in title:
            return i
    return 999


def main():
    api_key = load_api_key()
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    existing_domains = sorted(set(c["domain"] for c in state["contacts"]))
    existing_emails_by_domain = {}
    for c in state["contacts"]:
        existing_emails_by_domain.setdefault(c["domain"], set()).add(c["email"].lower())

    print(f"Domains with an existing verified contact: {len(existing_domains)}")

    # business name lookup, needed for search-preview attribution
    name_by_domain = {}
    with open(DOMAINS_FILE, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("|")
            if len(parts) >= 2:
                name_by_domain[parts[0]] = parts[1]

    todo = [d for d in existing_domains if d in name_by_domain]
    print(f"Domains with a known business name (searchable): {len(todo)}")

    batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    new_contacts_total = 0

    for bi, batch in enumerate(batches):
        print(f"\n--- Batch {bi + 1}/{len(batches)}: {len(batch)} domains ---")
        resp = requests.post(
            SEARCH_URL, headers=headers,
            json={
                "q_organization_domains_list": batch,
                "person_seniorities": ["owner", "c_suite", "vp", "director", "manager"],
                "per_page": 50,
            },
            timeout=30,
        )
        if resp.status_code == 402:
            print("!!! 402 Payment Required — out of credits. Stopping.")
            break
        if resp.status_code != 200:
            print(f"!!! Search failed ({resp.status_code}): {resp.text[:200]}")
            continue

        people = resp.json().get("people", [])
        print(f"  search: {len(people)} people found across {len(batch)} domains")

        name_to_domain = {normalize_name(name_by_domain[d]): d for d in batch}
        by_domain = {}
        for p in people:
            org_name = normalize_name((p.get("organization") or {}).get("name"))
            dom = name_to_domain.get(org_name)
            if not dom:
                for norm_name, d in name_to_domain.items():
                    if len(norm_name) >= 4 and len(org_name) >= 4 and \
                            (norm_name in org_name or org_name in norm_name):
                        dom = d
                        break
            if dom:
                by_domain.setdefault(dom, []).append(p)

        # pick up to MAX_ADDITIONAL_PER_DOMAIN candidates per domain, ranked by seniority
        picks = []  # (domain, person)
        for dom, plist in by_domain.items():
            ranked = sorted(plist, key=lambda p: seniority_score(p.get("title")))
            for p in ranked[:MAX_ADDITIONAL_PER_DOMAIN]:
                picks.append((dom, p))

        if not picks:
            print("  no additional candidates found in this batch")
            continue

        for ci in range(0, len(picks), BULK_CHUNK):
            chunk = picks[ci:ci + BULK_CHUNK]
            details = [{"id": person["id"]} for _, person in chunk]
            mresp = requests.post(
                BULK_MATCH_URL, headers=headers,
                params={"reveal_personal_emails": "false", "reveal_phone_number": "false"},
                json={"details": details}, timeout=30,
            )
            if mresp.status_code == 402:
                print("!!! 402 Payment Required on bulk_match — out of credits. Stopping.")
                STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
                return
            if mresp.status_code != 200:
                print(f"!!! bulk_match failed ({mresp.status_code}): {mresp.text[:200]}")
                continue

            matches = mresp.json().get("matches", [])
            for (attributed_dom, person), match in zip(chunk, matches):
                if not match:
                    continue
                email = match.get("email")
                if not email:
                    continue
                true_dom = ((match.get("organization") or {}).get("primary_domain")
                            or attributed_dom).lower().lstrip("www.")
                if true_dom != attributed_dom:
                    continue  # cross-domain mismatch, skip (same safety net as primary script)
                if email.lower() in existing_emails_by_domain.get(true_dom, set()):
                    continue  # already have this exact contact
                state["contacts"].append({
                    "domain": true_dom,
                    "first": match.get("first_name", ""),
                    "last": match.get("last_name", ""),
                    "email": email,
                    "title": match.get("title", ""),
                    "secondary_contact": True,
                })
                existing_emails_by_domain.setdefault(true_dom, set()).add(email.lower())
                new_contacts_total += 1
                print(f"  + {email}  ({true_dom} — {match.get('title', '')}) [2nd contact]")

        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        time.sleep(1)

    print(f"\n=== Done. New secondary contacts: {new_contacts_total}. "
          f"Total contacts overall: {len(state['contacts'])} ===")


if __name__ == "__main__":
    main()
