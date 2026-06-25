"""
apollo_setup.py
Broken Branch SA — Apollo.io Sequence Setup
Run with: python3 apollo_setup.py --step <verify|mailbox|sequence|import|enroll|all>
"""

import os
import sys
import json
import time
import argparse
import logging
import pandas as pd
import requests
from datetime import datetime

# ──────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────
APOLLO_API_KEY   = os.environ.get("APOLLO_API_KEY", "")
BASE_URL         = "https://api.apollo.io/v1"
SENDER_EMAIL     = "support@brokenbranchsa.com"
SEQUENCE_NAME    = "Broken Branch SA — Property Managers Bexar County"
CSV_PATH         = "sa_pm_contacts_with_emails.csv"
LOG_FILE         = "apollo_run.log"

# ──────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────
# EMAIL COPY  (5 emails)
# ──────────────────────────────────────────
EMAILS = [
    {
        "day_offset": 0,
        "subject": "One fallen branch could cost you more than you think",
        "body": """Hi {{first_name}},

Property managers in Bexar County know that tree and landscaping issues rarely announce themselves before they become a problem. A dead branch over a parking lot, overgrown limbs brushing a roofline, or an unmaintained common area can quickly become a liability claim — and in most lease agreements, that responsibility lands on you.

At Broken Branch SA, we specialize in tree cutting, trimming, and full landscaping services built around the needs of property managers — not homeowners. That means reliable scheduling, clean job sites, proper cleanup, and documentation you can pass straight to your property owner.

We serve the entire Bexar County area including Converse, Schertz, Boerne, New Braunfels, and beyond.

One less thing to chase down. One vendor you can count on.

Book a free property walkthrough at www.brokenbranchsa.com — we'll assess your trees and grounds and give you a clear, no-pressure quote.

Broken Branch SA
www.brokenbranchsa.com"""
    },
    {
        "day_offset": 4,
        "subject": "Your tenants notice the yard before they notice anything else",
        "body": """Hi {{first_name}},

Vacancy is expensive. One of the fastest ways to keep good tenants — and attract new ones — is a property that looks maintained and cared for from the street.

Broken Branch SA works directly with property managers across the San Antonio metro to handle tree trimming, tree removal, and full landscaping upkeep on a schedule that works around your properties — not ours. No chasing down crews, no half-finished jobs, no debris left behind.

Whether you manage a single-family portfolio, apartment complexes, or commercial properties, we can build a recurring service plan that keeps your grounds looking sharp year-round without it becoming another item on your to-do list.

Ready to take it off your plate?

Visit www.brokenbranchsa.com to book a free walkthrough or get a quote. We'll come to you.

Broken Branch SA
www.brokenbranchsa.com"""
    },
    {
        "day_offset": 4,
        "subject": "Tired of landscapers who don't show up or clean up?",
        "body": """Hi {{first_name}},

If you've managed properties for any length of time in the San Antonio area, you've probably had at least one landscaping vendor ghost you, leave a mess, or do half the job and call it done.

That's the frustration we built Broken Branch SA to solve.

We specialize in tree trimming, tree cutting, and landscaping services for property managers who need a vendor they can actually count on — someone who shows up on time, does the full job, and leaves the property cleaner than they found it. No callbacks, no excuses.

We also understand your reporting needs. We can provide photos before and after each service, so you always have documentation for your property owners or HOA.

If your current vendor isn't cutting it — literally or otherwise — let's talk.

Book directly at www.brokenbranchsa.com or reply here and I'll reach out personally.

Broken Branch SA
www.brokenbranchsa.com"""
    },
    {
        "day_offset": 5,
        "subject": "San Antonio storm season is here — are your trees a risk?",
        "body": """Hi {{first_name}},

South Texas weather doesn't give much warning. One storm can turn an overgrown oak into a serious property damage issue — or worse, a tenant safety incident.

As a property manager, proactive tree maintenance isn't just about appearances. It's about protecting your properties, limiting your liability, and making sure you're not scrambling to find an emergency crew after the fact.

Broken Branch SA offers pre-season tree assessments, routine trimming programs, and emergency storm cleanup for property managers throughout Bexar County and surrounding areas including Helotes, Cibolo, Kyle, Seguin, and Castroville.

Get ahead of it before the next storm rolls through.

Schedule a free property assessment at www.brokenbranchsa.com — takes 15 minutes, saves a lot of headaches.

Broken Branch SA
www.brokenbranchsa.com"""
    },
    {
        "day_offset": 5,
        "subject": "Quick question about your outdoor maintenance",
        "body": """Hi {{first_name}},

I know your inbox stays full — I'll keep this short.

Broken Branch SA works with property managers across the San Antonio area on tree trimming, tree removal, and landscaping. We're local, reliable, and built around making your job easier rather than adding to it.

If you have properties in Bexar County or the surrounding area where trees or landscaping are becoming a maintenance headache, we'd love to earn your business.

No pressure — just a free walkthrough and an honest quote.

www.brokenbranchsa.com

Broken Branch SA"""
    }
]

# ──────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────
def headers():
    return {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": APOLLO_API_KEY
    }

def api(method, path, payload=None, retries=3):
    """Make an Apollo API call with retry on rate limit."""
    url = f"{BASE_URL}{path}"
    for attempt in range(1, retries + 1):
        try:
            resp = requests.request(method, url, headers=headers(), json=payload, timeout=30)
            log.debug(f"{method} {path} → {resp.status_code}")
            if resp.status_code == 429:
                wait = 10 * attempt
                log.warning(f"Rate limited. Waiting {wait}s (attempt {attempt}/{retries})")
                time.sleep(wait)
                continue
            return resp
        except requests.exceptions.RequestException as e:
            log.error(f"Request error on attempt {attempt}: {e}")
            if attempt == retries:
                raise
            time.sleep(5)

def ok(resp, label):
    """Assert response is success, else log and exit."""
    if resp.status_code not in (200, 201):
        log.error(f"FAILED — {label}: HTTP {resp.status_code}")
        log.error(resp.text)
        sys.exit(1)
    log.info(f"OK — {label}")
    return resp.json()

# ──────────────────────────────────────────
# STEPS
# ──────────────────────────────────────────

def step_verify():
    """Step 1: Verify API key and environment."""
    print("\n" + "="*50)
    print("STEP 1 — Verifying API key & environment")
    print("="*50)

    if not APOLLO_API_KEY:
        log.error("APOLLO_API_KEY is not set.")
        log.error("Run: export APOLLO_API_KEY=your_key_here")
        sys.exit(1)

    if not os.path.exists(CSV_PATH):
        log.error(f"CSV not found at: {CSV_PATH}")
        sys.exit(1)

    df = pd.read_csv(CSV_PATH)
    log.info(f"CSV loaded: {len(df)} contacts, columns: {df.columns.tolist()}")

    # Quick API ping
    resp = api("GET", "/auth/health")
    if resp.status_code == 200:
        log.info("Apollo API key is valid ✓")
    else:
        log.error(f"Apollo API key check failed: {resp.status_code} — {resp.text}")
        sys.exit(1)

    return True


def step_mailbox():
    """Step 2: Find the sender mailbox ID for support@brokenbranchsa.com."""
    print("\n" + "="*50)
    print("STEP 2 — Locating sender mailbox")
    print("="*50)

    resp = api("GET", "/email_accounts")
    data = ok(resp, "List mailboxes")

    accounts = data.get("email_accounts", [])
    log.info(f"Found {len(accounts)} connected mailbox(es)")

    for acct in accounts:
        log.info(f"  - {acct.get('email')} (id: {acct.get('id')}, active: {acct.get('active')})")

    match = next((a for a in accounts if a.get("email", "").lower() == SENDER_EMAIL.lower()), None)

    if not match:
        log.error(f"Mailbox '{SENDER_EMAIL}' is NOT connected in Apollo.")
        log.error("Go to app.apollo.io → Settings → Mailboxes and connect it first.")
        log.error("Then re-run this step.")
        sys.exit(1)

    mailbox_id = match["id"]
    log.info(f"Mailbox found ✓  id={mailbox_id}")

    # Save to state file for later steps
    save_state({"mailbox_id": mailbox_id})
    return mailbox_id


def step_sequence():
    """Step 3: Create the 5-email sequence in Apollo."""
    print("\n" + "="*50)
    print("STEP 3 — Creating email sequence")
    print("="*50)

    state = load_state()

    # Create sequence
    payload = {
        "name": SEQUENCE_NAME,
        "permissions": "team_can_view",
        "active": False   # activate after contacts are enrolled
    }
    resp = api("POST", "/emailer_campaigns", payload)
    data = ok(resp, "Create sequence")

    sequence_id = data["emailer_campaign"]["id"]
    log.info(f"Sequence created ✓  id={sequence_id}  name='{SEQUENCE_NAME}'")

    # Add each email step
    position = 1
    for i, email in enumerate(EMAILS):
        step_payload = {
            "emailer_campaign_id": sequence_id,
            "position": position,
            "type": "auto_email",
            "wait_time": email["day_offset"],
            "wait_period": "days",
            "emailer_template": {
                "subject": email["subject"],
                "body_text": email["body"]
            }
        }
        step_resp = api("POST", "/emailer_steps", step_payload)
        step_data = ok(step_resp, f"Add email step {i+1} (Day +{email['day_offset']})")
        step_id = step_data.get("emailer_step", {}).get("id", "?")
        log.info(f"  Step {i+1} added ✓  subject='{email['subject']}'  step_id={step_id}")
        position += 1
        time.sleep(0.5)  # be gentle with the API

    state["sequence_id"] = sequence_id
    save_state(state)
    log.info(f"All 5 email steps created ✓")
    return sequence_id


def step_import():
    """Step 4: Import all 115 contacts from CSV into Apollo."""
    print("\n" + "="*50)
    print("STEP 4 — Importing contacts")
    print("="*50)

    df = pd.read_csv(CSV_PATH)
    log.info(f"Preparing to import {len(df)} contacts...")

    contact_ids = []
    failed = []

    for idx, row in df.iterrows():
        payload = {
            "first_name": str(row["First Name"]).strip(),
            "last_name": str(row["Last Name"]).strip(),
            "email": str(row["Email"]).strip(),
            "title": str(row["Title"]).strip(),
            "organization_name": str(row["Company"]).strip()
        }

        resp = api("POST", "/contacts", payload)

        if resp.status_code in (200, 201):
            contact = resp.json().get("contact", {})
            cid = contact.get("id")
            if cid:
                contact_ids.append(cid)
                log.info(f"  [{idx+1}/{len(df)}] Imported: {row['First Name']} {row['Last Name']} <{row['Email']}> id={cid}")
            else:
                # Contact may already exist — try to find by email
                existing = find_contact_by_email(row["Email"])
                if existing:
                    contact_ids.append(existing)
                    log.info(f"  [{idx+1}/{len(df)}] Already exists: {row['Email']} id={existing}")
                else:
                    log.warning(f"  [{idx+1}/{len(df)}] Could not get ID for {row['Email']}")
                    failed.append(row["Email"])
        else:
            log.warning(f"  [{idx+1}/{len(df)}] FAILED import for {row['Email']}: {resp.status_code}")
            failed.append(row["Email"])

        time.sleep(0.3)  # rate limit safety

    log.info(f"\nImport complete: {len(contact_ids)} succeeded, {len(failed)} failed")
    if failed:
        log.warning(f"Failed emails: {failed}")

    state = load_state()
    state["contact_ids"] = contact_ids
    state["failed_imports"] = failed
    save_state(state)

    return contact_ids


def find_contact_by_email(email):
    """Search Apollo for a contact by email and return their ID."""
    resp = api("GET", f"/contacts/search?q_keywords={email}&per_page=1")
    if resp.status_code == 200:
        contacts = resp.json().get("contacts", [])
        if contacts:
            return contacts[0].get("id")
    return None


def step_enroll():
    """Step 5: Enroll all contacts into the sequence."""
    print("\n" + "="*50)
    print("STEP 5 — Enrolling contacts in sequence")
    print("="*50)

    state = load_state()
    sequence_id = state.get("sequence_id")
    contact_ids = state.get("contact_ids", [])
    mailbox_id  = state.get("mailbox_id")

    if not sequence_id:
        log.error("No sequence_id found in state. Run --step sequence first.")
        sys.exit(1)
    if not contact_ids:
        log.error("No contact_ids found in state. Run --step import first.")
        sys.exit(1)
    if not mailbox_id:
        log.error("No mailbox_id found in state. Run --step mailbox first.")
        sys.exit(1)

    log.info(f"Enrolling {len(contact_ids)} contacts into sequence {sequence_id}")
    log.info(f"Sender mailbox: {SENDER_EMAIL} (id={mailbox_id})")

    enrolled = 0
    failed   = []

    # Apollo supports batch enrollment — send in chunks of 25
    chunk_size = 25
    chunks = [contact_ids[i:i+chunk_size] for i in range(0, len(contact_ids), chunk_size)]

    for chunk_idx, chunk in enumerate(chunks):
        payload = {
            "contact_ids": chunk,
            "emailer_campaign_id": sequence_id,
            "send_email_from_email_account_id": mailbox_id,
            "sequence_active_in_other_campaigns": False
        }
        resp = api("POST", "/emailer_campaigns/add_contact_ids", payload)

        if resp.status_code in (200, 201):
            result = resp.json()
            contacts_added = result.get("contacts", [])
            enrolled += len(contacts_added)
            log.info(f"  Chunk {chunk_idx+1}/{len(chunks)}: {len(contacts_added)} enrolled ✓")
        else:
            log.warning(f"  Chunk {chunk_idx+1}/{len(chunks)}: FAILED — {resp.status_code} — {resp.text}")
            failed.extend(chunk)

        time.sleep(1)  # pause between chunks

    # Activate the sequence now that contacts are enrolled
    activate_resp = api("POST", f"/emailer_campaigns/{sequence_id}/mark_archived", {"archived": False})
    log.info("Sequence activated ✓")

    state["enrolled"] = enrolled
    state["failed_enrollment"] = failed
    save_state(state)

    return enrolled, failed


def print_final_report():
    """Print a clean summary of everything that was done."""
    state = load_state()
    print("\n" + "="*50)
    print("FINAL REPORT — Broken Branch SA Apollo Setup")
    print("="*50)
    print(f"  Sequence Name   : {SEQUENCE_NAME}")
    print(f"  Sequence ID     : {state.get('sequence_id', 'N/A')}")
    print(f"  Sender Mailbox  : {SENDER_EMAIL} (id={state.get('mailbox_id', 'N/A')})")
    print(f"  Contacts Imported: {len(state.get('contact_ids', []))}")
    print(f"  Contacts Enrolled: {state.get('enrolled', 0)}")
    print(f"  Import Failures  : {len(state.get('failed_imports', []))}")
    print(f"  Enroll Failures  : {len(state.get('failed_enrollment', []))}")
    print(f"  Log File         : {LOG_FILE}")
    print(f"  View in Apollo   : https://app.apollo.io/#/sequences")
    print()

    fi = state.get("failed_imports", [])
    fe = state.get("failed_enrollment", [])
    if fi:
        print(f"  ⚠ Failed Imports : {fi}")
    if fe:
        print(f"  ⚠ Failed Enrolls : {fe}")
    if not fi and not fe:
        print("  ✓ All contacts imported and enrolled successfully!")

    print("="*50 + "\n")


# ──────────────────────────────────────────
# STATE FILE  (persist IDs between steps)
# ──────────────────────────────────────────
STATE_FILE = "apollo_state.json"

def save_state(data):
    existing = load_state()
    existing.update(data)
    with open(STATE_FILE, "w") as f:
        json.dump(existing, f, indent=2)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


# ──────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Broken Branch SA — Apollo Setup")
    parser.add_argument("--step", choices=["verify","mailbox","sequence","import","enroll","all"],
                        required=True, help="Which step to run")
    args = parser.parse_args()

    log.info(f"Starting apollo_setup.py — step: {args.step}")
    log.info(f"Timestamp: {datetime.now().isoformat()}")

    if args.step == "verify" or args.step == "all":
        step_verify()

    if args.step == "mailbox" or args.step == "all":
        step_mailbox()

    if args.step == "sequence" or args.step == "all":
        step_sequence()

    if args.step == "import" or args.step == "all":
        step_import()

    if args.step == "enroll" or args.step == "all":
        step_enroll()

    print_final_report()


if __name__ == "__main__":
    main()
