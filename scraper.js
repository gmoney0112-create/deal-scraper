'use strict';

require('dotenv').config();
const https = require('https');
const fs    = require('fs');
const path  = require('path');

// ── Keys ──────────────────────────────────────────────────────────────────────
const GOOGLE_KEY = process.env.GOOGLE_PLACES_KEY;
const YELP_KEY   = process.env.YELP_API_KEY;
const HUNTER_KEY = process.env.HUNTER_API_KEY;

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

// ── Google Places ─────────────────────────────────────────────────────────────
async function googleSearch(query, lat, lng, pageToken) {
  const p = new URLSearchParams({
    query, location: `${lat},${lng}`, radius: String(RADIUS), key: GOOGLE_KEY,
    ...(pageToken ? { pagetoken: pageToken } : {}),
  });
  const { body } = await withRetry(() =>
    httpGet(`https://maps.googleapis.com/maps/api/place/textsearch/json?${p}`)
  );
  if (body.status === 'REQUEST_DENIED')
    throw new Error(`Google API key denied: ${body.error_message}`);
  return body;
}

async function googleDetails(placeId) {
  const p = new URLSearchParams({
    place_id: placeId,
    fields: 'name,formatted_address,formatted_phone_number,website,business_status',
    key: GOOGLE_KEY,
  });
  const { body } = await withRetry(() =>
    httpGet(`https://maps.googleapis.com/maps/api/place/details/json?${p}`)
  );
  return body.result || {};
}

async function sweepGoogle(term, loc) {
  const out = [];
  let token = null, pages = 0;
  do {
    if (token) await sleep(2300); // Google requires a delay before next_page_token activates
    let body;
    try { body = await googleSearch(term, loc.lat, loc.lng, token); }
    catch (e) { error(`Google: ${e.message} [${loc.city} / "${term}"]`); break; }
    if (body.status === 'ZERO_RESULTS') break;
    out.push(...(body.results || []));
    token = body.next_page_token || null;
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

async function findEmail(name, website) {
  if (!HUNTER_KEY) return '';
  for (const domain of domainCandidates(name, website)) {
    await sleep(350);
    const email = await hunterLookup(domain);
    if (email) return email;
  }
  return '';
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
  info(`APIs: Google=yes  Yelp=${YELP_KEY ? 'yes' : 'NO'}  Hunter=${HUNTER_KEY ? 'yes' : 'NO'}`);
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
        if (seenIds.has(place.place_id)) continue;
        seenIds.add(place.place_id);

        let detail = {};
        try { detail = await googleDetails(place.place_id); await sleep(120); }
        catch (e) { warn(`    Details failed for ${place.name}: ${e.message}`); }

        if (detail.business_status === 'PERMANENTLY_CLOSED') continue;

        const co = {
          name:    detail.name    || place.name || '',
          address: detail.formatted_address || place.formatted_address || '',
          phone:   detail.formatted_phone_number || '',
          website: detail.website || '',
          email:   '',
          source:  'Google Places',
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
            name:    biz.name || '',
            address: addr,
            phone:   biz.display_phone || biz.phone || '',
            website: biz.url || '',
            email:   '',
            source:  'Yelp',
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

  // ── Phase 3: Hunter.io enrichment ─────────────────────────────────────
  if (HUNTER_KEY) {
    info('\nPHASE 3 — Hunter.io Email Enrichment');
    let enriched = 0;
    for (const co of companies) {
      const email = await findEmail(co.name, co.website);
      if (email) {
        co.email = email;
        enriched++;
        info(`  ${co.name} → ${email}`);
      }
    }
    info(`\nPhase 3 done — ${enriched}/${companies.length} emails found`);
  } else {
    warn('\nPhase 3 skipped — HUNTER_API_KEY not set');
  }

  // ── Phase 4: CSV export ────────────────────────────────────────────────
  info('\nPHASE 4 — Writing CSV');
  companies.sort((a, b) => a.name.localeCompare(b.name));

  const headers = ['Company Name', 'Address', 'Phone', 'Website', 'Email', 'Source'];
  const lines = [
    headers.map(csvEscape).join(','),
    ...companies.map(c =>
      [c.name, c.address, c.phone, c.website, c.email, c.source].map(csvEscape).join(',')
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
