"""
Apollo enrichment for "digital desert" leads (no website — name+state match)
==============================================================================
Companion to apollo_enrich_rest.py, which enriches leads BY DOMAIN. Digital
desert leads have no domain by definition, so this script searches by
organization name + state instead.

KEY DIFFERENCE FROM DOMAIN ENRICHMENT (cost implications):
  The domain search endpoint accepts up to 20 domains per call (batched).
  The name-search path does NOT batch — q_organization_name takes exactly
  one company name per call. Enriching N leads costs up to N search credits
  (not N/20), before any bulk_match credits. Expect a LOWER hit rate too:
  many no-website businesses have zero Apollo footprint at all.

DISCOVERED VIA TESTING (2026-08-17):
  - q_organization_name alone works and returns results.
  - organization_locations at CITY granularity often zeroes out results —
    small businesses frequently lack populated city-level HQ data in Apollo.
    STATE-level location (e.g. "Texas, US") works far more reliably.
  - person_seniorities still narrows correctly when combined with just the
    name + state-level location.

Usage:
    python scripts/apollo_enrich_digital_desert.py --input output/digital_desert_leads_XXXX.csv --max-leads 20   # test
    python scripts/apollo_enrich_digital_desert.py --input output/digital_desert_leads_XXXX.csv                  # full run

Source of truth: output/apollo_digital_desert_state.json (git-tracked, keyed by
"Company Name|City" since there's no domain to key on).
"""

import argparse
import csv
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
STATE_FILE = ROOT / "output" / "apollo_digital_desert_state.json"

SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
BULK_MATCH_URL = "https://api.apollo.io/api/v1/people/bulk_match"

RANK = [
    "owner", "ceo", "chief executive", "president", "founder",
    "co-founder", "principal", "managing partner", "partner",
    "director", "office manager", "general manager", "manager",
    "vice president", "vp",
]

STATE_NAME = {"TX": "Texas"}  # extend if leads expand beyond Texas

SUFFIXES = re.compile(r"\b(llc|inc|incorporated|corp|corporation|co|ltd|pllc|pc|dba|lp|llp)\b")


def normalize_name(name):
    n = (name or "").lower()
    n = re.sub(r"[^a-z0-9\s]", " ", n)
    n = SUFFIXES.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def name_matches(query_name, candidate_org_name):
    """Guard against Apollo's fuzzy org-name search returning unrelated orgs
    (observed: 'Waca' matched 'World Affairs Council of Austin' via acronym
    collision). Require exact normalized match, or containment with a
    minimum length on both sides to avoid short/generic-name false positives."""
    q = normalize_name(query_name)
    c = normalize_name(candidate_org_name)
    if not q or not c:
        return False
    if q == c:
        return True
    if len(q) >= 6 and len(c) >= 6 and (q in c or c in q):
        return True
    return False


def load_api_key():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("APOLLO_API_KEY="):
                return line.split("=", 1)[1].strip()
    key = os.environ.get("APOLLO_API_KEY")
    if not key:
        sys.exit("APOLLO_API_KEY not found in .env or environment")
    return key


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"processed_keys": [], "contacts": [], "notes": ""}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def seniority_score(title):
    title = (title or "").lower()
    for i, r in enumerate(RANK):
        if r in title:
            return i
    return 999


def lead_key(row):
    return f"{row.get('Company Name', '').strip()}|{row.get('City', '').strip()}"


def run(input_csv, max_leads=None, limit=None):
    api_key = load_api_key()
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    with open(input_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    state = load_state()
    processed = set(state["processed_keys"])
    todo = [r for r in rows if lead_key(r) not in processed]

    if limit:
        todo = todo[:limit]
    if max_leads:
        todo = todo[:max_leads]

    print(f"Leads remaining: {len(todo)} (already processed: {len(processed)})")

    new_emails = 0
    zero_footprint = 0

    for i, row in enumerate(todo):
        key = lead_key(row)
        name = row.get("Company Name", "").strip()
        state_abbr = row.get("State", "TX").strip()
        state_full = STATE_NAME.get(state_abbr, state_abbr)

        if not name:
            processed.add(key)
            state["processed_keys"].append(key)
            continue

        resp = requests.post(
            SEARCH_URL,
            headers=headers,
            json={
                "q_organization_name": name,
                "organization_locations": [f"{state_full}, US"],
                "person_seniorities": ["owner", "c_suite", "manager", "director"],
                "per_page": 10,
            },
            timeout=30,
        )

        if resp.status_code == 402:
            print("!!! 402 Payment Required — out of Apollo credits. Stopping.")
            break
        if resp.status_code != 200:
            print(f"  [{i+1}/{len(todo)}] {name}: search failed ({resp.status_code})")
            processed.add(key)
            state["processed_keys"].append(key)
            continue

        people = resp.json().get("people", [])
        # Guard against fuzzy-match false positives (e.g. short/acronym-like
        # names matching an unrelated org) before spending a match credit.
        confident = [p for p in people
                     if name_matches(name, (p.get("organization") or {}).get("name"))]

        # Ambiguity guard: a generic descriptor name (e.g. "Car Wash") can
        # pass the name filter against many DIFFERENT unrelated businesses
        # that all happen to contain the same words ("WashGuys Car Wash",
        # "Carmel Car Wash", ...). If confident matches span more than one
        # distinct organization, we can't tell which is ours — skip rather
        # than guess (observed on 2026-08-17: "Car Wash" matched 10 unrelated
        # car washes; only single-distinct-org cases were reliable).
        distinct_orgs = {normalize_name((p.get("organization") or {}).get("name")) for p in confident}
        if len(distinct_orgs) > 1:
            zero_footprint += 1
            processed.add(key)
            state["processed_keys"].append(key)
            print(f"  [{i+1}/{len(todo)}] {name}: too generic — {len(distinct_orgs)} distinct orgs "
                  f"matched, skipped as ambiguous")
            continue

        if not confident:
            zero_footprint += 1
            processed.add(key)
            state["processed_keys"].append(key)
            if people:
                print(f"  [{i+1}/{len(todo)}] {name}: {len(people)} result(s) but none name-matched "
                      f"(top: {(people[0].get('organization') or {}).get('name')!r}) — skipped")
            if (i + 1) % 20 == 0:
                print(f"  [{i+1}/{len(todo)}] processed so far... ({new_emails} emails found)")
            continue

        pick = min(confident, key=lambda p: seniority_score(p.get("title")))

        mresp = requests.post(
            BULK_MATCH_URL,
            headers=headers,
            params={"reveal_personal_emails": "false", "reveal_phone_number": "false"},
            json={"details": [{"id": pick["id"]}]},
            timeout=30,
        )

        processed.add(key)
        state["processed_keys"].append(key)

        if mresp.status_code == 402:
            print("!!! 402 Payment Required on bulk_match — out of credits. Stopping.")
            save_state(state)
            return new_emails
        if mresp.status_code != 200:
            print(f"  [{i+1}/{len(todo)}] {name}: bulk_match failed ({mresp.status_code})")
            save_state(state)
            continue

        matches = mresp.json().get("matches", [])
        match = matches[0] if matches else None
        email = match.get("email") if match else None

        if email and not any(c["email"] == email for c in state["contacts"]):
            state["contacts"].append({
                "lead_key": key,
                "company": name,
                "first": match.get("first_name", ""),
                "last": match.get("last_name", ""),
                "email": email,
                "title": match.get("title", ""),
            })
            new_emails += 1
            print(f"  [{i+1}/{len(todo)}] + {email}  ({name} — {match.get('title', '')})")

        if (i + 1) % 10 == 0:
            save_state(state)
        time.sleep(0.5)

    save_state(state)
    print(f"\n=== Done. New emails: {new_emails}. Zero-Apollo-footprint: {zero_footprint}/{len(todo)}. "
          f"Total contacts: {len(state['contacts'])} ===")
    return new_emails


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="digital desert leads CSV")
    ap.add_argument("--max-leads", type=int, default=None, help="cap leads processed this run")
    ap.add_argument("--limit", type=int, default=None, help="cap total todo list before processing")
    args = ap.parse_args()
    run(args.input, max_leads=args.max_leads, limit=args.limit)
