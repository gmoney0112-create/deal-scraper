'use strict';

require('dotenv').config();
const https = require('https');
const fs    = require('fs');
const path  = require('path');

// ── Keys ──────────────────────────────────────────────────────────────────────
const GOOGLE_KEY  = process.env.GOOGLE_PLACES_KEY;
const YELP_KEY    = process.env.YELP_API_KEY;
const HUNTER_KEY  = process.env.HUNTER_API_KEY;
const APOLLO_KEY  = process.env.APOLLO_API_KEY;
const SNOV_ID     = process.env.SNOV_CLIENT_ID;
const SNOV_SECRET = process.env.SNOV_SECRET;
const APIFY_KEY   = process.env.APIFY_KEY;
// CLAY_API_KEY stored in .env; Clay enrichment runs via their platform (table setup required)

if (!GOOGLE_KEY) {
  console.error('FATAL: GOOGLE_PLACES_KEY missing from .env');
  process.exit(1);
}

// ── Directories ───────────────────────────────────────────────────────────────
const OUT_DIR = path.join(__dirname, 'output');
const LOG_DIR = path.join(__dirname, 'logs');
[OUT_DIR, LOG_DIR].forEach(d => fs.mkdirSync(d, { recursive: true }));

const CSV_FILE = path.join(OUT_DIR, 'property_management_companies.csv');
const LOG_FILE = path.join(LOG_DIR, `scraper-${Date.now()}.log`);

// ── Logger ────────────────────────────────────────────────────────────────────
function log(level, msg) {
  const line = `[${new Date().toISOString()}] [${level}] ${msg}`;
  fs.appendFileSync(LOG_FILE, line + '\n');
  if (level === 'ERROR') process.stderr.write(line + '\n');
  else process.stdout.write(line + '\n');
}
const info  = msg => log('INFO',  msg);
const warn  = msg => log('WARN',  msg);
const error = msg => log('ERROR', msg);

// ── San Antonio MSA — all 8 counties ─────────────────────────────────────────
// Bexar | Comal | Guadalupe | Wilson | Atascosa | Medina | Bandera | Kendall
const MSA_LOCATIONS = [
  { city: 'San Antonio',   state: 'TX', lat: 29.4241, lng: -98.4936 },
  { city: 'New Braunfels', state: 'TX', lat: 29.7030, lng: -98.1245 },
  { city: 'Seguin',        state: 'TX', lat: 29.5688, lng: -97.9644 },
  { city: 'Schertz',       state: 'TX', lat: 29.5538, lng: -98.2695 },
  { city: 'Floresville',   state: 'TX', lat: 29.1366, lng: -98.1545 },
  { city: 'Boerne',        state: 'TX', lat: 29.7947, lng: -98.7320 },
  { city: 'Pleasanton',    state: 'TX', lat: 28.9666, lng: -98.4796 },
  { city: 'Hondo',         state: 'TX', lat: 29.3480, lng: -99.1417 },
  { city: 'Bandera',       state: 'TX', lat: 29.7274, lng: -99.0742 },
];

const SEARCH_TERMS = [
  'property management company',
  'real estate property management',
  'apartment management',
];

const RADIUS = 30000; // 30 km per city centre

// ── Utilities ─────────────────────────────────────────────────────────────────
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function withRetry(fn, attempts = 3, baseDelay = 1000) {
  for (let i = 1; i <= attempts; i++) {
    try { return await fn(); }
    catch (err) {
      if (i === attempts) throw err;
      const delay = baseDelay * (2 ** (i - 1));
      warn(`Retry ${i}/${attempts - 1} in ${delay}ms — ${err.message}`);
      await sleep(delay);
    }
  }
}

function httpGet(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const req = https.get(
      { hostname: u.hostname, path: u.pathname + u.search,
        headers: { 'User-Agent': 'deal-scraper/1.0', ...headers }, timeout: 15000 },
      res => {
        let raw = '';
        res.on('data', c => (raw += c));
        res.on('end', () => {
          try { resolve({ status: res.statusCode, body: JSON.parse(raw) }); }
          catch { reject(new Error(`JSON parse error (HTTP ${res.statusCode}): ${raw.slice(0, 80)}`)); }
        });
      }
    );
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timed out')); });
    req.on('error', reject);
  });
}

function httpPost(url, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const data = JSON.stringify(body);
    const req = https.request(
      {
        hostname: u.hostname,
        path: u.pathname + u.search,
        method: 'POST',
        headers: {
          'User-Agent': 'deal-scraper/1.0',
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(data),
          ...headers,
        },
        timeout: 15000,
      },
      res => {
        let raw = '';
        res.on('data', c => (raw += c));
        res.on('end', () => {
          try { resolve({ status: res.statusCode, body: JSON.parse(raw) }); }
          catch { reject(new Error(`JSON parse error (HTTP ${res.statusCode})`)); }
        });
      }
    );
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timeout')); });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

// ── Deduplication ─────────────────────────────────────────────────────────────
function normalizePhone(p = '') { return p.replace(/\D/g, ''); }

// Strip generic words before comparing names — catches "ABC Prop Mgmt" vs "ABC Property Management"
function normalizeName(n = '') {
  return n.toLowerCase()
    .replace(/\b(property|management|realty|real\s*estate|pm|inc|llc|co|company|corp|services?|group|assoc\w*)\b/g, '')
    .replace(/[^a-z0-9]/g, '');
}

// Levenshtein-based similarity (0–1)
function similarity(a, b) {
  if (!a || !b) return 0;
  const m = a.length, n = b.length;
  const dp = Array.from({ length: m + 1 }, (_, i) =>
    Array.from({ length: n + 1 }, (_, j) => (i === 0 ? j : j === 0 ? i : 0))
  );
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = a[i-1] === b[j-1]
        ? dp[i-1][j-1]
        : 1 + Math.min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1]);
  return 1 - dp[m][n] / Math.max(m, n);
}

class Deduplicator {
  constructor() {
    this.phones = new Set();
    this.names  = []; // normalized name strings
    this.removed = 0;
  }

  isDuplicate(co) {
    const ph = normalizePhone(co.phone);
    if (ph.length >= 10 && this.phones.has(ph)) { this.removed++; return true; }

    const nm = normalizeName(co.name);
    if (nm.length >= 4) {
      for (const existing of this.names) {
        if (similarity(nm, existing) >= 0.85) { this.removed++; return true; }
      }
    }
    return false;
  }

  register(co) {
    const ph = normalizePhone(co.phone);
    if (ph.length >= 10) this.phones.add(ph);
    const nm = normalizeName(co.name);
    if (nm.length >= 4) this.names.push(nm);
  }
}

// ── Domain helpers ────────────────────────────────────────────────────────────
function extractDomain(site) {
  if (!site) return null;
  try {
    return new URL(site.startsWith('http') ? site : `https://${site}`).hostname.replace(/^www\./, '');
  } catch { return null; }
}

// Returns website domain if available; otherwise guesses from company name
function domainCandidates(name, website) {
  const fromSite = extractDomain(website);
  if (fromSite) return [fromSite];

  const slug = name.toLowerCase()
    .replace(/\b(property|management|realty|real estate|pm|inc|llc|co|company|corp)\b/g, '')
    .replace(/[^a-z0-9]/g, '');
  if (slug.length < 3) return [];
  return [`${slug}.com`, `${slug}pm.com`, `${slug}realty.com`];
}

// ── Google Places API (New) ───────────────────────────────────────────────────
// Uses Places API (New) — returns all fields in one call, no separate Details needed
const GOOGLE_FIELD_MASK = [
  'places.id', 'places.displayName', 'places.formattedAddress',
  'places.nationalPhoneNumber', 'places.websiteUri', 'places.businessStatus',
  'nextPageToken',
].join(',');

async function googleSearch(query, lat, lng, pageToken) {
  const { status, body } = await withRetry(() =>
    httpPost(
      'https://places.googleapis.com/v1/places:searchText',
      {
        textQuery: query,
        locationBias: { circle: { center: { latitude: lat, longitude: lng }, radius: RADIUS } },
        pageSize: 20,
        ...(pageToken ? { pageToken } : {}),
      },
      { 'X-Goog-Api-Key': GOOGLE_KEY, 'X-Goog-FieldMask': GOOGLE_FIELD_MASK },
    )
  );
  if (status === 403) throw new Error(`Google Places denied (403): ${JSON.stringify(body)}`);
  if (status === 400) throw new Error(`Google Places bad request (400): ${JSON.stringify(body)}`);
  return body;
}

async function sweepGoogle(term, loc) {
  const out = [];
  let token = null, pages = 0;
  do {
    if (token) await sleep(2300);
    let res;
    try { res = await googleSearch(term, loc.lat, loc.lng, token); }
    catch (e) { error(`Google: ${e.message} [${loc.city} / "${term}"]`); break; }
    out.push(...(res.places || []));
    token = res.nextPageToken || null;
    pages++;
    await sleep(350);
  } while (token && pages < 3);
  return out;
}

// ── Yelp Fusion ───────────────────────────────────────────────────────────────
async function sweepYelp(term, loc) {
  if (!YELP_KEY) return [];
  const p = new URLSearchParams({
    term,
    location: `${loc.city}, ${loc.state}`,
    limit: '50',
  });
  try {
    const { status, body } = await withRetry(() =>
      httpGet(`https://api.yelp.com/v3/businesses/search?${p}`,
              { Authorization: `Bearer ${YELP_KEY}` })
    );
    if (status === 401) { warn('Yelp 401 — check YELP_API_KEY'); return []; }
    if (status === 429) { warn(`Yelp 429 — rate limited on ${loc.city} / "${term}"`); return []; }
    return body.businesses || [];
  } catch (e) {
    warn(`Yelp error [${loc.city} / "${term}"]: ${e.message}`);
    return [];
  }
}

// ── Hunter.io ─────────────────────────────────────────────────────────────────
async function hunterLookup(domain) {
  const p = new URLSearchParams({ domain, api_key: HUNTER_KEY, limit: '1' });
  try {
    const { body } = await httpGet(`https://api.hunter.io/v2/domain-search?${p}`);
    const emails = body.data?.emails || [];
    return emails.length ? emails[0].value : null;
  } catch { return null; }
}

// ── Apollo.io ─────────────────────────────────────────────────────────────────
const APOLLO_TITLES = [
  'owner', 'president', 'ceo', 'principal', 'managing director',
  'property manager', 'director of property management',
  'real estate broker', 'broker', 'vice president', 'manager',
];

async function apolloSearch(domain) {
  if (!APOLLO_KEY || !domain) return null;
  try {
    const { status, body } = await withRetry(() =>
      httpPost(
        'https://api.apollo.io/v1/mixed_people/search',
        { q_organization_domains_or_urls: domain, person_titles: APOLLO_TITLES, page: 1, per_page: 5 },
        { 'x-api-key': APOLLO_KEY },
      )
    );
    if (status === 401) { warn('Apollo 401 — check APOLLO_API_KEY'); return null; }
    if (status === 429) { warn('Apollo 429 — rate limited'); return null; }

    for (const person of (body.people || [])) {
      if (person.email && person.email_status !== 'unavailable') {
        return {
          email:        person.email,
          contactName:  person.name || `${person.first_name || ''} ${person.last_name || ''}`.trim(),
          contactTitle: person.title || '',
        };
      }
    }
    return null;
  } catch (e) {
    warn(`Apollo error for ${domain}: ${e.message}`);
    return null;
  }
}

// ── Snov.io ───────────────────────────────────────────────────────────────────
let _snovToken = null;

async function getSnovToken() {
  if (_snovToken) return _snovToken;
  try {
    const { body } = await httpPost('https://api.snov.io/v1/oauth/access_token', {
      grant_type: 'client_credentials',
      client_id: SNOV_ID,
      client_secret: SNOV_SECRET,
    });
    _snovToken = body.access_token || null;
    return _snovToken;
  } catch (e) {
    warn(`Snov.io auth failed: ${e.message}`);
    return null;
  }
}

async function snovSearch(domain) {
  if (!SNOV_ID || !domain) return null;
  const token = await getSnovToken();
  if (!token) return null;
  try {
    const { body } = await httpPost('https://api.snov.io/v2/get-domain-emails', {
      access_token: token, domain, type: 'all', limit: 5,
    });
    const emails = (body.data?.emails || body.emails || []);
    if (!emails.length) return null;
    const e = emails[0];
    return {
      email:        e.email || '',
      contactName:  [e.firstName, e.lastName].filter(Boolean).join(' '),
      contactTitle: e.position || '',
    };
  } catch (e) {
    warn(`Snov.io error for ${domain}: ${e.message}`);
    return null;
  }
}

// ── Apify email extractor ─────────────────────────────────────────────────────
// Scrapes the company website and extracts any email addresses found.
// Slowest source — only runs when Hunter, Apollo, and Snov.io all fail.
async function apifyExtract(website) {
  if (!APIFY_KEY || !website) return null;
  try {
    const { status, body: run } = await httpPost(
      'https://api.apify.com/v2/acts/apify~email-extractor/runs',
      { startUrls: [{ url: website }], maxDepth: 1 },
      { Authorization: `Bearer ${APIFY_KEY}` },
    );
    if (status !== 201) { warn(`Apify start failed HTTP ${status}`); return null; }

    const runId = run.data?.id;
    if (!runId) return null;

    // Poll up to 60 s for the actor to finish
    for (let i = 0; i < 12; i++) {
      await sleep(5000);
      const { body: rs } = await httpGet(
        `https://api.apify.com/v2/actor-runs/${runId}`,
        { Authorization: `Bearer ${APIFY_KEY}` },
      );
      const st = rs.data?.status;
      if (st === 'SUCCEEDED') {
        const dsId = rs.data.defaultDatasetId;
        const { body: items } = await httpGet(
          `https://api.apify.com/v2/datasets/${dsId}/items`,
          { Authorization: `Bearer ${APIFY_KEY}` },
        );
        const found = (Array.isArray(items) ? items : [])
          .flatMap(item => item.emails || [])
          .filter(e => e && !e.includes('example.') && !e.includes('sentry.'));
        return found.length ? { email: found[0], contactName: '', contactTitle: '' } : null;
      }
      if (['FAILED', 'ABORTED', 'TIMED-OUT'].includes(st)) break;
    }
    return null;
  } catch (e) {
    warn(`Apify error for ${website}: ${e.message}`);
    return null;
  }
}

// ── Email waterfall: Hunter → Apollo → Snov.io → Apify ───────────────────────
async function findEmail(name, website) {
  const domains = domainCandidates(name, website);
  const empty   = { email: '', contactName: '', contactTitle: '', emailSource: '' };
  if (!domains.length && !website) return empty;

  // Phase A: Hunter.io
  if (HUNTER_KEY) {
    for (const domain of domains) {
      await sleep(350);
      const email = await hunterLookup(domain);
      if (email) return { email, contactName: '', contactTitle: '', emailSource: 'Hunter' };
    }
  }

  // Phase B: Apollo.io
  if (APOLLO_KEY) {
    for (const domain of domains) {
      await sleep(350);
      const r = await apolloSearch(domain);
      if (r) return { ...r, emailSource: 'Apollo' };
    }
  }

  // Phase C: Snov.io
  if (SNOV_ID) {
    for (const domain of domains) {
      await sleep(350);
      const r = await snovSearch(domain);
      if (r) return { ...r, emailSource: 'Snov.io' };
    }
  }

  // Phase D: Apify — scrapes the company website directly (slowest, last resort)
  if (APIFY_KEY && website) {
    const r = await apifyExtract(website);
    if (r) return { ...r, emailSource: 'Apify' };
  }

  return empty;
}

// ── CSV ───────────────────────────────────────────────────────────────────────
function csvEscape(v) {
  const s = v == null ? '' : String(v);
  return (s.includes(',') || s.includes('"') || s.includes('\n'))
    ? `"${s.replace(/"/g, '""')}"` : s;
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  const t0 = Date.now();
  info('='.repeat(54));
  info('Deal Scraper — San Antonio MSA Property Management');
  info(`APIs: Google=yes | Yelp=${YELP_KEY ? 'yes' : 'NO'} | Hunter=${HUNTER_KEY ? 'yes' : 'NO'} | Apollo=${APOLLO_KEY ? 'yes' : 'NO'} | Snov=${SNOV_ID ? 'yes' : 'NO'} | Apify=${APIFY_KEY ? 'yes' : 'NO'}`);
  info(`Log : ${LOG_FILE}`);
  info('='.repeat(54));

  const dedup     = new Deduplicator();
  const companies = [];
  const seenIds   = new Set(); // Google place_ids

  function add(co) {
    if (!co.name) return false;
    if (dedup.isDuplicate(co)) return false;
    dedup.register(co);
    companies.push(co);
    return true;
  }

  // ── Phase 1: Google Places ─────────────────────────────────────────────
  info('\nPHASE 1 — Google Places API');

  for (const loc of MSA_LOCATIONS) {
    for (const term of SEARCH_TERMS) {
      info(`  ${loc.city} / "${term}"`);
      const raw = await sweepGoogle(term, loc);
      info(`    ${raw.length} raw results`);

      for (const place of raw) {
        if (seenIds.has(place.id)) continue;
        seenIds.add(place.id);

        if (place.businessStatus === 'PERMANENTLY_CLOSED') continue;

        const co = {
          name:         place.displayName?.text || '',
          address:      place.formattedAddress  || '',
          phone:        place.nationalPhoneNumber || '',
          website:      place.websiteUri          || '',
          contactName:  '',
          contactTitle: '',
          email:        '',
          emailSource:  '',
          source:       'Google Places',
        };
        if (add(co)) info(`    + ${co.name}`);
      }
    }
  }

  info(`\nPhase 1 done — ${companies.length} unique companies`);

  // ── Phase 2: Yelp Fusion ───────────────────────────────────────────────
  const beforeYelp = companies.length;
  if (YELP_KEY) {
    info('\nPHASE 2 — Yelp Fusion API');

    for (const loc of MSA_LOCATIONS) {
      for (const term of SEARCH_TERMS) {
        info(`  ${loc.city} / "${term}"`);
        const businesses = await sweepYelp(term, loc);
        info(`    ${businesses.length} results`);

        for (const biz of businesses) {
          const addr = biz.location
            ? [biz.location.address1, biz.location.city, biz.location.state, biz.location.zip_code]
                .filter(Boolean).join(', ')
            : '';
          const co = {
            name:         biz.name || '',
            address:      addr,
            phone:        biz.display_phone || biz.phone || '',
            website:      biz.url || '',
            contactName:  '',
            contactTitle: '',
            email:        '',
            emailSource:  '',
            source:       'Yelp',
          };
          if (add(co)) info(`    + ${co.name}`);
        }
        await sleep(400);
      }
    }
    info(`\nPhase 2 done — +${companies.length - beforeYelp} new (${dedup.removed} total duplicates removed)`);
  } else {
    warn('\nPhase 2 skipped — YELP_API_KEY not set');
  }

  // ── Phase 3: Email enrichment (Hunter → Apollo waterfall) ────────────
  if (HUNTER_KEY || APOLLO_KEY || SNOV_ID || APIFY_KEY) {
    const chain = ['Hunter', APOLLO_KEY && 'Apollo', SNOV_ID && 'Snov.io', APIFY_KEY && 'Apify']
      .filter(Boolean).join(' → ');
    info(`\nPHASE 3 — Email Enrichment (${chain})`);
    let enriched = 0;
    for (const co of companies) {
      const result = await findEmail(co.name, co.website);
      if (result.email) {
        co.email        = result.email;
        co.contactName  = result.contactName;
        co.contactTitle = result.contactTitle;
        co.emailSource  = result.emailSource;
        enriched++;
        const who = result.contactName ? ` (${result.contactName})` : '';
        info(`  [${result.emailSource}] ${co.name}${who} → ${result.email}`);
      }
    }
    info(`\nPhase 3 done — ${enriched}/${companies.length} emails found`);
  } else {
    warn('\nPhase 3 skipped — no enrichment API keys set');
  }

  // ── Phase 4: CSV export ────────────────────────────────────────────────
  info('\nPHASE 4 — Writing CSV');
  companies.sort((a, b) => a.name.localeCompare(b.name));

  const headers = ['Company Name', 'Address', 'Phone', 'Website', 'Contact Name', 'Contact Title', 'Email', 'Email Source', 'Lead Source'];
  const lines = [
    headers.map(csvEscape).join(','),
    ...companies.map(c =>
      [c.name, c.address, c.phone, c.website, c.contactName, c.contactTitle, c.email, c.emailSource, c.source]
        .map(csvEscape).join(',')
    ),
  ];
  fs.writeFileSync(CSV_FILE, lines.join('\n'), 'utf8');

  // ── Summary ────────────────────────────────────────────────────────────
  const elapsed   = ((Date.now() - t0) / 1000).toFixed(1);
  const withEmail = companies.filter(c => c.email).length;
  const emailPct  = companies.length ? Math.round((withEmail / companies.length) * 100) : 0;

  info('');
  info('='.repeat(54));
  info(`Finished in ${elapsed}s`);
  info(`Companies  : ${companies.length} unique  (${dedup.removed} duplicates removed)`);
  info(`Email cover: ${withEmail}/${companies.length} (${emailPct}%)`);
  info(`CSV output : ${CSV_FILE}`);
  info(`Run log    : ${LOG_FILE}`);
  info('='.repeat(54));
}

main().catch(err => {
  error(`Fatal: ${err.message}`);
  process.exit(1);
});
