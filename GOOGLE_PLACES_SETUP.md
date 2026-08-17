# Google Places API — Billing & Setup Handoff

## Status as of 2026-08-17

**Blocked.** API key `AIzaSyBKxmT0a-c_GApeM3ccV6f0IyZHCVOu33U` (stored in `.env` as
`GOOGLE_PLACES_KEY`) returns:

```json
{
   "error_message" : "You must enable Billing on the Google Cloud Project at https://console.cloud.google.com/project/_/billing/enable",
   "status" : "REQUEST_DENIED"
}
```

User confirmed a **paid, active billing account** exists ("My Billing Account 2",
$0.00 spend, status "Paid account"). The account itself is fine — **it is not linked
to the specific project that issued this API key.** That link is a separate step from
having a billing account. Re-tested after the user said they linked it; error was
identical, so either the link didn't take, the wrong project was linked, or the
required APIs aren't enabled on top of billing.

Verify command (run any time to re-check — safe, legacy endpoint, effectively free):
```bash
curl -s "https://maps.googleapis.com/maps/api/place/textsearch/json?query=plumber+in+san+antonio+tx&key=$GOOGLE_PLACES_KEY"
```
`"status": "OK"` with results = fixed. `"REQUEST_DENIED"` = still blocked, read the
`error_message` field, it's specific.

---

## Step-by-step fix

### Step 1 — Find the project that owns this key
Cloud Console → **APIs & Services → Credentials** →
find `AIzaSyBKxmT0a-c_GApeM3ccV6f0IyZHCVOu33U` in the API Keys list. The **project
name is shown in the project selector at the top-left of the page** while you're
looking at that key — that is the ONLY project that matters for the next steps.
If more than one Google Cloud project exists on this account, it is very easy to
fix billing on the wrong one — always confirm via this page, not by guessing.

### Step 2 — Link billing to that exact project
1. With the correct project selected (top-left project switcher), go to
   **Billing** in the left nav (or `console.cloud.google.com/billing/linkedaccount`).
2. If it says "This project has no billing account", click **Link a billing account**.
3. Select **"My Billing Account 2"** (the paid account already confirmed working).
4. Confirm the page now shows the billing account name next to the project.

Common mistake: linking billing at the *account* level (which just confirms the
billing account itself is valid) instead of the *project* level (which is what
APIs actually check). Step 2 must be done from inside the specific project.

### Step 3 — Enable the required APIs on that project
Still inside the same project: **APIs & Services → Library**, search and enable
**both** of these (they are separate toggles, and the scraper script needs the
second one specifically):
- **Places API** (legacy — used for quick diagnostic checks like the curl command above)
- **Places API (New)** — this is what `scrapers/scrape_digital_desert_leads.py` and
  `scripts/scrape_ai_automation_icps.py` actually call
  (`https://places.googleapis.com/v1/places:searchText`)

Each shows an "Enable" button if not yet active, or "Manage" if already on.

### Step 4 — Check the API key's own restrictions
Back in **APIs & Services → Credentials**, click into the key itself:
- **Application restrictions**: if set to "HTTP referrers" or "IP addresses", a
  server-side script (this one, running via curl/Python, no browser) will be
  rejected even with billing fixed — there's no HTTP referrer to check. Set this
  to **"None"** for now (or add this machine's outbound IP if you want it locked
  down — but "None" is simplest while testing).
- **API restrictions**: if set to "Restrict key", make sure **Places API** and
  **Places API (New)** are both checked in the allowed list. If set to
  "Don't restrict key", this isn't an issue.

### Step 5 — Set a budget alert (recommended, not required to unblock)
**Billing → Budgets & alerts → Create budget**, scope it to this project, set a
threshold (e.g. $50 or $100) with email alerts at 50%/90%/100%. This scrape will
run an estimated 1,800–3,000+ paid Text Search calls to reach the 2,000-lead
target — a budget alert means you find out by email, not by a surprise invoice.
Roughly ballpark the cost yourself against
[Places API pricing](https://mapsplatform.google.com/pricing/) before committing —
the field mask this script requests (rating, review count, website, phone, types)
is likely to fall in the "Pro" or "Enterprise" SKU tier, not the cheapest one.

### Step 6 — Verify end-to-end
Run the same curl check from the top of this doc. Once it returns
`"status": "OK"`, tell the session (or say "try again") — the enrichment agent
will re-test immediately and, once confirmed, move straight into running
`scrapers/scrape_digital_desert_leads.py` for the 2,000-new-lead target.

---

## What happens after this is unblocked (for the next session/agent)

1. Re-run the verify curl above; confirm `"status": "OK"`.
2. Run a **small test slice first** (a handful of categories/cities, not the
   full 1,764-query sweep) to confirm real yield and real per-call cost before
   committing to the full run — same caution pattern used for the Apollo
   enrichment in `HANDOFF.md`.
3. Run `python scripts/scrape_digital_desert_leads.py` (or a modified version
   capped/chunked to checkpoint periodically) to reach the ~2,000 new
   no-website-lead target. Current script covers 49 categories × 36 TX cities
   (1,764 base queries) — may need more categories/cities added if that
   undershoots 2,000 after filtering.
4. Phone numbers come free from this scrape (~97% coverage on the prior batch).
5. For email: **do not** blindly run every new lead through
   `scripts/apollo_enrich_digital_desert.py` — validated yield on the existing
   109-lead batch was ~1.8% (2 emails) at a cost of ~1 Apollo credit per lead
   checked (no batching on that endpoint). Consider running it only on a
   filtered subset (distinctive business names, not generic descriptors like
   "Car Wash") or accept phone-first outreach for this segment.
6. Merge results into `output/ghl_master_leads_*.csv` following the existing
   dedup pattern (by Company Name + City) documented in `HANDOFF.md`.
