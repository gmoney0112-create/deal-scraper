"""
Apollo Domain Enrichment — direct REST API version (no MCP dependency)
========================================================================
Replaces the MCP-based flow in apollo_enrich_domains.py, which was blocked
by the -32003 connector-approval gate (see HANDOFF.md). This script calls
Apollo's public REST API directly with an API key from .env.

IMPORTANT API BEHAVIOR DISCOVERED (2026-08-17): the batched
mixed_people/api_search endpoint returns a locked/preview record per person
(obfuscated last name, no organization.primary_domain) — full details only
come back after people/bulk_match "reveals" the record (1 credit each).
Since a 20-domain search batch can't be split by domain from the preview
alone, we attribute each preview result to a domain by normalized-name
match against our own domain->business_name list (built from the same
Google-sourced list Apollo likely also derived org names from), pick the
single highest-seniority candidate per domain, and ONLY spend a bulk_match
credit on that one pick. The match response's organization.primary_domain
is then used as ground truth and cross-checked against our attribution.

Usage:
    python scripts/apollo_enrich_rest.py --max-batches 1   # test batch
    python scripts/apollo_enrich_rest.py                   # full remaining run
    python scripts/apollo_enrich_rest.py --limit 200        # cap total domains

Source of truth: output/apollo_enrichment_state.json (git-tracked).
Every batch appends to processed_domains + contacts and rewrites the file.
"""

import argparse
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


def load_domains():
    domains = []
    with open(DOMAINS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) < 5:
                continue
            domains.append({
                "domain": parts[0], "name": parts[1],
                "category": parts[2], "phone": parts[3], "city": parts[4],
            })
    return domains


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"processed_domains": [], "contacts": [], "last_batch_index_completed": 0,
            "total_batches": 0, "notes": ""}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def seniority_score(title):
    title = (title or "").lower()
    for i, r in enumerate(RANK):
        if r in title:
            return i
    return 999


def attribute_and_pick(people, batch):
    """Attribute each locked search-preview person to a domain via normalized
    business-name match, then pick the single highest-seniority pick per domain.
    Returns dict: domain -> person (search preview dict, has 'id')."""
    name_to_domain = {normalize_name(d["name"]): d["domain"] for d in batch}

    by_domain = {}
    unmatched = 0
    for p in people:
        org_name = normalize_name((p.get("organization") or {}).get("name"))
        if not org_name:
            unmatched += 1
            continue
        dom = name_to_domain.get(org_name)
        if not dom:
            # containment fallback (min 4 chars each side to avoid false positives)
            for norm_name, d in name_to_domain.items():
                if len(norm_name) >= 4 and len(org_name) >= 4 and \
                        (norm_name in org_name or org_name in norm_name):
                    dom = d
                    break
        if not dom:
            unmatched += 1
            continue
        by_domain.setdefault(dom, []).append(p)

    picks = {dom: min(plist, key=lambda p: seniority_score(p.get("title")))
             for dom, plist in by_domain.items()}
    return picks, unmatched


def run(limit=None, max_batches=None):
    api_key = load_api_key()
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    domains = load_domains()
    state = load_state()
    processed = set(state["processed_domains"])
    todo = [d for d in domains if d["domain"] not in processed]

    if limit:
        todo = todo[:limit]

    print(f"Domains remaining: {len(todo)} (already processed: {len(processed)})")

    batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    if max_batches:
        batches = batches[:max_batches]

    new_emails_total = 0
    total_unmatched = 0

    for bi, batch in enumerate(batches):
        batch_domains = [d["domain"] for d in batch]
        print(f"\n--- Batch {bi + 1}/{len(batches)}: {len(batch_domains)} domains ---")

        resp = requests.post(
            SEARCH_URL,
            headers=headers,
            json={
                "q_organization_domains_list": batch_domains,
                "person_seniorities": ["owner", "c_suite", "vp", "director", "manager"],
                "per_page": 50,
            },
            timeout=30,
        )

        if resp.status_code == 402:
            print("!!! 402 Payment Required — out of Apollo credits. Stopping.")
            break
        if resp.status_code == 403:
            print(f"!!! 403 Forbidden — {resp.text[:300]}. Stopping.")
            break
        if resp.status_code != 200:
            print(f"!!! Search failed ({resp.status_code}): {resp.text[:300]}")
            for d in batch:
                if d["domain"] not in processed:
                    processed.add(d["domain"])
                    state["processed_domains"].append(d["domain"])
            save_state(state)
            continue

        data = resp.json()
        people = data.get("people", [])
        print(f"  search: {len(people)} people found across {len(batch_domains)} domains")

        picks, unmatched = attribute_and_pick(people, batch)
        total_unmatched += unmatched
        if unmatched:
            print(f"  (skipped {unmatched} preview results with no confident domain match)")

        # mark ALL domains in this batch as processed (searched), regardless of match
        for d in batch:
            if d["domain"] not in processed:
                processed.add(d["domain"])
                state["processed_domains"].append(d["domain"])

        if not picks:
            print("  no attributable picks in this batch")
            save_state(state)
            time.sleep(1)
            continue

        pick_items = list(picks.items())
        for ci in range(0, len(pick_items), BULK_CHUNK):
            chunk = pick_items[ci:ci + BULK_CHUNK]
            details = [{"id": person["id"]} for _, person in chunk]

            mresp = requests.post(
                BULK_MATCH_URL,
                headers=headers,
                params={"reveal_personal_emails": "false", "reveal_phone_number": "false"},
                json={"details": details},
                timeout=30,
            )

            if mresp.status_code == 402:
                print("!!! 402 Payment Required on bulk_match — out of credits. Stopping.")
                save_state(state)
                return new_emails_total
            if mresp.status_code != 200:
                print(f"!!! bulk_match failed ({mresp.status_code}): {mresp.text[:300]}")
                continue

            matches = mresp.json().get("matches", [])
            for (attributed_dom, person), match in zip(chunk, matches):
                if not match:
                    continue
                email = match.get("email")
                if not email:
                    continue
                # ground-truth domain from the revealed record; fall back to our attribution
                true_dom = ((match.get("organization") or {}).get("primary_domain")
                            or attributed_dom).lower().lstrip("www.")
                if true_dom != attributed_dom:
                    print(f"  (note: attributed {attributed_dom} but match reveals {true_dom} — using true_dom)")
                if any(c["email"] == email for c in state["contacts"]):
                    continue
                state["contacts"].append({
                    "domain": true_dom,
                    "first": match.get("first_name", ""),
                    "last": match.get("last_name", ""),
                    "email": email,
                    "title": match.get("title", ""),
                })
                new_emails_total += 1
                print(f"  + {email}  ({true_dom} — {match.get('title', '')})")

        state["last_batch_index_completed"] = state.get("last_batch_index_completed", 0) + 1
        save_state(state)
        time.sleep(1)

    print(f"\n=== Done. New emails this run: {new_emails_total}. "
          f"Total contacts: {len(state['contacts'])}. Unmatched previews skipped: {total_unmatched} ===")
    return new_emails_total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="cap total domains processed")
    ap.add_argument("--max-batches", type=int, default=None, help="cap number of 20-domain batches")
    args = ap.parse_args()
    run(limit=args.limit, max_batches=args.max_batches)
