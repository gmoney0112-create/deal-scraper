'use strict';

const https  = require('https');
const http   = require('http');
const fs     = require('fs');
const path   = require('path');

// ── .env parser ───────────────────────────────────────────────────────────────
try {
  fs.readFileSync(path.join(__dirname, '.env'), 'utf8').split(/\r?\n/).forEach(line => {
    const m = line.match(/^([^#=]+)=(.*)$/);
    if (m) process.env[m[1].trim()] = m[2].trim();
  });
} catch { /* .env optional */ }

// ── Keys ──────────────────────────────────────────────────────────────────────
const GOOGLE_KEY  = process.env.GOOGLE_PLACES_KEY;
const YELP_KEY    = process.env.YELP_API_KEY;
const APOLLO_KEY  = process.env.APOLLO_API_KEY;
const HUNTER_KEY  = process.env.HUNTER_API_KEY;
const SNOV_ID     = process.env.SNOV_CLIENT_ID;
const SNOV_SECRET = process.env.SNOV_CLIENT_SECRET;
const APIFY_KEY   = process.env.APIFY_KEY;

const RESUME = process.argv.includes('--resume');

if (!RESUME && !GOOGLE_KEY) {
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
const info = msg => log('INFO',  msg);
const warn = msg => log('WARN',  msg);
const error = msg => log('ERROR', msg);

// ── San Antonio MSA — all 8 counties ─────────────────────────────────────────
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
    const u    = new URL(url);
    const data = JSON.stringify(body);
    const req  = https.request(
      {
        hostname: u.hostname, path: u.pathname + u.search, method: 'POST',
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

function normalizeName(n = '') {
  return n.toLowerCase()
    .replace(/\b(property|management|realty|real\s*estate|pm|inc|llc|co|company|corp|services?|group|assoc\w*)\b/g, '')
    .replace(/[^a-z0-9]/g, '');
}

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
  constructor() { this.phones = new Set(); this.names = []; this.removed = 0; }

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

// ── PM keyword filter ─────────────────────────────────────────────────────────
const PM_KEYWORDS = /\b(property|properties|management|realt(y|or|estate)|leasing|rental|residential|apartment|housing|homes?|estates?|hoa)\b/i;
function isPMCompany(name) { return PM_KEYWORDS.test(name || ''); }

// ── Domain helpers ────────────────────────────────────────────────────────────
function extractDomain(site) {
  if (!site) return null;
  try {
    return new URL(site.startsWith('http') ? site : `https://${site}`).hostname.replace(/^www\./, '');
  } catch { return null; }
}

const GENERIC_DOMAINS = new Set([
  'yelp.com', 'maps.google.com', 'google.com', 'facebook.com',
  'instagram.com', 'linkedin.com', 'tripadvisor.com', 'bing.com',
  'apple.com', 'gmail.com', 'hotmail.com', 'outlook.com',
]);

function isGenericDomain(domain) {
  if (!domain) return true;
  return GENERIC_DOMAINS.has(domain) || [...GENERIC_DOMAINS].some(g => domain.endsWith(`.${g}`));
}

function isLikelyGenericEmail(email) {
  const e = (email || '').toLowerCase();
  return /@(yelp\.com|google\.com|gmail\.com|hotmail\.com|outlook\.com|aol\.com|icloud\.com|mailinator\.com|example\.com|agentfire\.com|knck\.io|rentmanager\.com|mailchimp\.com|hubspot\.com|wixsite\.com|squarespace\.com|weebly\.com)$/i.test(e)
    || /^(info|sales|hello|admin|contact|support|webmaster|noreply|no-reply|test|example|user)@/i.test(e);
}

function domainCandidates(name, website) {
  const siteDomain = extractDomain(website);
  if (siteDomain && !isGenericDomain(siteDomain)) return [siteDomain];
  const slug = name.toLowerCase()
    .replace(/\b(property|management|realty|real estate|pm|inc|llc|co|company|corp)\b/g, '')
    .replace(/[^a-z0-9]/g, '');
  if (slug.length < 3) return [];
  return [`${slug}.com`, `${slug}pm.com`, `${slug}realty.com`];
}

// ── CSV helpers ───────────────────────────────────────────────────────────────
function csvEscape(v) {
  const s = v == null ? '' : String(v);
  return (s.includes(',') || s.includes('"') || s.includes('\n'))
    ? `"${s.replace(/"/g, '""')}"` : s;
}

function splitCSVRow(row) {
  const fields = [];
  let cur = '', inQ = false;
  for (let i = 0; i < row.length; i++) {
    const ch = row[i];
    if (ch === '"') {
      if (inQ && row[i+1] === '"') { cur += '"'; i++; }
      else inQ = !inQ;
    } else if (ch === ',' && !inQ) { fields.push(cur); cur = ''; }
    else cur += ch;
  }
  fields.push(cur);
  return fields;
}

const CSV_HEADERS = [
  'Company Name', 'Address', 'Phone', 'Website',
  'Contact Name', 'Contact Title', 'Email', 'Email Source', 'Lead Source',
];

function writeCSV(companies) {
  const lines = [
    CSV_HEADERS.map(csvEscape).join(','),
    ...companies.map(c =>
      [c.name, c.address, c.phone, c.website,
       c.contactName, c.contactTitle, c.email, c.emailSource, c.source]
        .map(csvEscape).join(',')
    ),
  ];
  fs.writeFileSync(CSV_FILE, lines.join('\n'), 'utf8');
}

function loadCSV() {
  const raw     = fs.readFileSync(CSV_FILE, 'utf8');
  const lines   = raw.trim().split(/\r?\n/);
  const headers = splitCSVRow(lines[0]);
  return lines.slice(1).map(line => {
    const vals = splitCSVRow(line);
    const obj  = {};
    headers.forEach((h, i) => { obj[h] = vals[i] ?? ''; });
    return {
      name:         obj['Company Name']  || '',
      address:      obj['Address']       || '',
      phone:        obj['Phone']         || '',
      website:      obj['Website']       || '',
      contactName:  obj['Contact Name']  || '',
      contactTitle: obj['Contact Title'] || '',
      email:        obj['Email']         || '',
      emailSource:  obj['Email Source']  || '',
      source:       obj['Lead Source']   || '',
    };
  });
}

// ── Google Places (New API) ───────────────────────────────────────────────────
const GOOGLE_FIELD_MASK = [
  'places.id', 'places.displayName', 'places.formattedAddress',
  'places.nationalPhoneNumber', 'places.websiteUri', 'places.businessStatus',
].join(',');

async function googleSearchNew(query, lat, lng, pageToken) {
  const bodyObj = {
    textQuery: query, pageSize: 20,
    locationBias: { circle: { center: { latitude: lat, longitude: lng }, radius: RADIUS } },
    ...(pageToken ? { pageToken } : {}),
  };
  const { status, body } = await withRetry(() =>
    httpPost('https://places.googleapis.com/v1/places:searchText', bodyObj, {
      'X-Goog-Api-Key': GOOGLE_KEY,
      'X-Goog-FieldMask': GOOGLE_FIELD_MASK,
    })
  );
  if (status === 403) throw new Error(`Google 403: ${body.error?.message || 'Forbidden'}`);
  if (status === 400) throw new Error(`Google 400: ${body.error?.message || 'Bad request'}`);
  return body;
}

async function sweepGoogle(term, loc) {
  const out = [];
  let token = null, pages = 0;
  do {
    if (token) await sleep(500);
    let body;
    try { body = await googleSearchNew(term, loc.lat, loc.lng, token); }
    catch (e) { error(`Google: ${e.message} [${loc.city} / "${term}"]`); break; }
    const places = body.places || [];
    if (!places.length) break;
    out.push(...places);
    token = body.nextPageToken || null;
    pages++;
    await sleep(350);
  } while (token && pages < 3);
  return out;
}

// ── Yelp Fusion ───────────────────────────────────────────────────────────────
const YELP_CATEGORIES = 'propmanagement,realestatesvcs';

async function sweepYelp(term, loc) {
  if (!YELP_KEY) return [];
  const p = new URLSearchParams({
    term, location: `${loc.city}, ${loc.state}`, limit: '50', categories: YELP_CATEGORIES,
  });
  try {
    const { status, body } = await withRetry(() =>
      httpGet(`https://api.yelp.com/v3/businesses/search?${p}`, { Authorization: `Bearer ${YELP_KEY}` })
    );
    if (status === 401) { warn('Yelp 401 — check YELP_API_KEY'); return []; }
    if (status === 429) { warn(`Yelp 429 — rate limited on ${loc.city}`); return []; }
    return body.businesses || [];
  } catch (e) { warn(`Yelp error [${loc.city} / "${term}"]: ${e.message}`); return []; }
}

// ── Apollo ────────────────────────────────────────────────────────────────────
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
        'https://api.apollo.io/api/v1/people/match',
        { domain, person_titles: APOLLO_TITLES, reveal_personal_emails: false },
        { 'x-api-key': APOLLO_KEY },
      )
    );
    if (status === 401) { warn('Apollo 401 — check APOLLO_API_KEY'); return null; }
    if (status === 403) { warn(`Apollo 403 — ${body.error?.message || 'check plan'}`); return null; }
    if (status === 429) { warn('Apollo 429 — rate limited'); return null; }
    if (status !== 200) { warn(`Apollo ${status}: ${body.error?.message || 'unknown'}`); return null; }
    const p = body.person;
    if (p && p.email && p.email_status !== 'unavailable') {
      return {
        email:        p.email,
        contactName:  p.name || `${p.first_name || ''} ${p.last_name || ''}`.trim(),
        contactTitle: p.title || '',
      };
    }
    return null;
  } catch (e) { warn(`Apollo error for ${domain}: ${e.message}`); return null; }
}

// ── Hunter.io ─────────────────────────────────────────────────────────────────
async function hunterLookup(domain) {
  if (!HUNTER_KEY) return null;
  const p = new URLSearchParams({ domain, api_key: HUNTER_KEY, limit: '1' });
  try {
    const { status, body } = await httpGet(`https://api.hunter.io/v2/domain-search?${p}`);
    if (status === 429) { warn(`Hunter 429 — rate limited for ${domain}`); return null; }
    if (status !== 200) { warn(`Hunter ${status} for ${domain}`); return null; }
    const emails = body.data?.emails || [];
    return emails.length ? emails[0].value : null;
  } catch (e) { warn(`Hunter error for ${domain}: ${e.message}`); return null; }
}

// ── Snov.io ───────────────────────────────────────────────────────────────────
async function getSnovToken() {
  if (!SNOV_ID || !SNOV_SECRET) return null;
  return new Promise(resolve => {
    const body = new URLSearchParams({
      grant_type: 'client_credentials',
      client_id: SNOV_ID,
      client_secret: SNOV_SECRET,
    }).toString();
    const req = https.request({
      hostname: 'api.snov.io', path: '/v1/oauth/access_token', method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Content-Length': Buffer.byteLength(body),
      },
    }, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try {
          const b = JSON.parse(d);
          if (b.access_token) { info('  Snov.io token acquired'); resolve(b.access_token); }
          else { warn(`Snov auth failed: ${d.slice(0, 100)}`); resolve(null); }
        } catch { warn('Snov auth parse error'); resolve(null); }
      });
    });
    req.on('error', e => { warn(`Snov auth error: ${e.message}`); resolve(null); });
    req.write(body);
    req.end();
  });
}

async function snovSearch(token, domain) {
  if (!token) return null;
  try {
    const qs = new URLSearchParams({ token, domain, type: 'all', limit: '10' });
    const { status, body } = await httpGet(`https://api.snov.io/v1/get-emails-from-domain?${qs}`);
    if (status !== 200) { warn(`Snov HTTP ${status} for ${domain}`); return null; }
    const list   = body.emails || body.data?.emails || [];
    const emails = list.map(e => typeof e === 'string' ? e : e.email).filter(Boolean).map(s => s.toLowerCase());
    const good   = emails.filter(e => !isLikelyGenericEmail(e));
    return good.length ? good[0] : null;
  } catch (e) { warn(`Snov error for ${domain}: ${e.message}`); return null; }
}

// ── Website scrape ────────────────────────────────────────────────────────────
const CONTACT_PATHS = ['', '/contact', '/contact-us', '/about', '/about-us', '/team', '/staff'];
const PAGE_TIMEOUT  = 10000;

function fetchPage(urlStr, redirects = 0) {
  if (redirects > 3) return Promise.resolve(null);
  return new Promise(resolve => {
    const timer = setTimeout(() => resolve(null), PAGE_TIMEOUT);
    const done  = val => { clearTimeout(timer); resolve(val); };
    const lib   = urlStr.startsWith('https') ? https : http;
    try {
      const req = lib.get(urlStr, { headers: { 'User-Agent': 'Mozilla/5.0 deal-scraper/1.0' } }, res => {
        if ([301, 302, 307, 308].includes(res.statusCode) && res.headers.location) {
          const redir = res.headers.location.startsWith('http')
            ? res.headers.location
            : new URL(res.headers.location, urlStr).href;
          res.resume(); clearTimeout(timer);
          fetchPage(redir, redirects + 1).then(resolve);
          return;
        }
        if (res.statusCode !== 200) { res.resume(); return done(null); }
        let body = '';
        res.setEncoding('utf8');
        res.on('data', c => { body += c; if (body.length > 150_000) { req.destroy(); done(body); } });
        res.on('end',  () => done(body));
        res.on('error', () => done(null));
      });
      req.on('error', () => done(null));
    } catch { done(null); }
  });
}

function extractEmails(html) {
  if (!html) return [];
  const raw = html.match(/[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g) || [];
  return [...new Set(raw.map(e => e.toLowerCase()))].filter(e => !isLikelyGenericEmail(e));
}

async function scrapeWebsite(website) {
  const base = website.startsWith('http') ? website : `https://${website}`;
  for (const p of CONTACT_PATHS) {
    const url = p ? `${base.replace(/\/$/, '')}${p}` : base;
    try {
      const html   = await fetchPage(url);
      const emails = extractEmails(html);
      if (emails.length) return emails[0];
    } catch {}
    await sleep(300);
  }
  return null;
}

// ── Apify batch (handles JS-heavy sites) ─────────────────────────────────────
const APIFY_ACTOR = 'vdrmota~contact-info-scraper';

function apifyReq(method, endpoint, body) {
  return new Promise((resolve, reject) => {
    const sep     = endpoint.includes('?') ? '&' : '?';
    const full    = `https://api.apify.com/v2${endpoint}${sep}token=${APIFY_KEY}`;
    const u       = new URL(full);
    const payload = body ? JSON.stringify(body) : null;
    const req     = https.request({
      hostname: u.hostname, path: u.pathname + u.search, method,
      headers: { 'Content-Type': 'application/json', ...(payload ? { 'Content-Length': Buffer.byteLength(payload) } : {}) },
    }, res => {
      let d = '';
      res.setEncoding('utf8');
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(d) }); }
        catch { resolve({ status: res.statusCode, body: d }); }
      });
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

async function apifyBatch(companies) {
  info(`\nPHASE 4 — Apify batch scrape (${companies.length} JS-heavy sites)`);

  const urls = companies.map(c => c.website.startsWith('http') ? c.website : `https://${c.website}`);

  let runId;
  try {
    const { status, body } = await apifyReq('POST', `/acts/${APIFY_ACTOR}/runs`, {
      startUrls: urls.map(u => ({ url: u })),
      maxCrawlDepth: 2,
      maxPagesPerCrawl: 6,
    });
    if (status !== 201 && status !== 200)
      throw new Error(`HTTP ${status}: ${JSON.stringify(body).slice(0, 200)}`);
    runId = body.data?.id;
    if (!runId) throw new Error('No run ID returned');
    info(`  Apify run started: ${runId}`);
  } catch (e) { warn(`Apify start failed: ${e.message}`); return; }

  // Poll for completion (up to 6 min)
  const deadline = Date.now() + 360_000;
  let succeeded  = false;
  while (Date.now() < deadline) {
    await sleep(10_000);
    const { status, body } = await apifyReq('GET', `/actor-runs/${runId}`);
    if (status !== 200) continue;
    const s = body.data?.status;
    info(`  Apify run ${runId}: ${s}`);
    if (s === 'SUCCEEDED') { succeeded = true; break; }
    if (['FAILED', 'ABORTED', 'TIMED-OUT'].includes(s)) { warn(`Apify run ${s}`); return; }
  }
  if (!succeeded) { warn('Apify run timed out (6 min)'); return; }

  const { status, body: items } = await apifyReq('GET', `/actor-runs/${runId}/dataset/items`);
  if (status !== 200) { warn('Apify dataset fetch failed'); return; }

  const results = Array.isArray(items) ? items : (items.items || []);
  info(`  Apify returned ${results.length} items`);

  let found = 0;
  for (const item of results) {
    const pageUrl = item.url || item.inputUrl || '';
    if (!pageUrl) continue;

    const co = companies.find(c => {
      const site = c.website.startsWith('http') ? c.website : `https://${c.website}`;
      try { return new URL(site).origin === new URL(pageUrl).origin; } catch { return false; }
    });
    if (!co || co.email) continue;

    const raw    = item.emails || [];
    const emails = raw
      .map(e => typeof e === 'string' ? e : (e.email || ''))
      .filter(Boolean).map(e => e.toLowerCase())
      .filter(e => !isLikelyGenericEmail(e));

    if (emails.length) {
      co.email       = emails[0];
      co.emailSource = 'Apify';
      found++;
      info(`  [Apify] ${co.name} → ${emails[0]}`);
    }
  }
  info(`  Apify phase done — ${found}/${companies.length} new emails`);
}

// ── Email waterfall: Apollo → Hunter → Snov.io → Website ─────────────────────
async function findEmail(name, website, snovToken) {
  const domains = domainCandidates(name, website);

  // 1. Apollo — best quality: named decision-maker + verified email
  if (APOLLO_KEY) {
    for (const domain of domains) {
      await sleep(350);
      const result = await apolloSearch(domain);
      if (result && !isLikelyGenericEmail(result.email))
        return { ...result, emailSource: 'Apollo' };
    }
  }

  // 2. Hunter — domain-level email from MX records / crawl
  if (HUNTER_KEY) {
    for (const domain of domains) {
      await sleep(250);
      const email = await hunterLookup(domain);
      if (email && !isLikelyGenericEmail(email))
        return { email, contactName: '', contactTitle: '', emailSource: 'Hunter' };
    }
  }

  // 3. Snov.io — professional email database
  if (snovToken) {
    for (const domain of domains) {
      await sleep(1200); // ~50 req/min limit
      const email = await snovSearch(snovToken, domain);
      if (email)
        return { email, contactName: '', contactTitle: '', emailSource: 'Snov.io' };
    }
  }

  // 4. Website scrape — free, works for static sites; JS-heavy sites handled by Apify batch
  const siteDomain = extractDomain(website);
  if (siteDomain && !isGenericDomain(siteDomain)) {
    const email = await scrapeWebsite(website);
    if (email)
      return { email, contactName: '', contactTitle: '', emailSource: 'Website' };
  }

  return { email: '', contactName: '', contactTitle: '', emailSource: '' };
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  const t0 = Date.now();
  info('='.repeat(54));
  info(`Deal Scraper — San Antonio MSA${RESUME ? ' [RESUME]' : ''}`);
  info([
    `Google=${GOOGLE_KEY ? 'yes' : 'NO'}`,
    `Yelp=${YELP_KEY ? 'yes' : 'NO'}`,
    `Apollo=${APOLLO_KEY ? 'yes' : 'NO'}`,
    `Hunter=${HUNTER_KEY ? 'yes' : 'NO'}`,
    `Snov=${SNOV_ID ? 'yes' : 'NO'}`,
    `Apify=${APIFY_KEY ? 'yes' : 'NO'}`,
  ].join('  '));
  info(`Log: ${LOG_FILE}`);
  info('='.repeat(54));

  let companies = [];
  const dedup   = new Deduplicator();
  const seenIds = new Set();

  function add(co) {
    if (!co.name || dedup.isDuplicate(co)) return false;
    dedup.register(co);
    companies.push(co);
    return true;
  }

  if (RESUME) {
    // ── Resume: skip discovery, load existing CSV ─────────────────────────
    info('\nRESUME mode — loading existing CSV');
    try {
      companies = loadCSV();
      info(`Loaded ${companies.length} companies from CSV`);
    } catch (e) {
      error(`Cannot load CSV for resume: ${e.message}`);
      process.exit(1);
    }
  } else {
    // ── Phase 1: Google Places ────────────────────────────────────────────
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
            name:         place.displayName?.text  || '',
            address:      place.formattedAddress   || '',
            phone:        place.nationalPhoneNumber || '',
            website:      place.websiteUri || '',
            contactName: '', contactTitle: '', email: '', emailSource: '',
            source:       'Google Places',
          };
          if (add(co)) info(`    + ${co.name}`);
        }
      }
    }
    info(`\nPhase 1 done — ${companies.length} unique companies`);

    // ── Phase 2: Yelp Fusion ──────────────────────────────────────────────
    const beforeYelp = companies.length;
    if (YELP_KEY) {
      info('\nPHASE 2 — Yelp Fusion API');
      for (const loc of MSA_LOCATIONS) {
        for (const term of SEARCH_TERMS) {
          info(`  ${loc.city} / "${term}"`);
          const businesses = await sweepYelp(term, loc);
          info(`    ${businesses.length} results`);
          for (const biz of businesses) {
            if (!isPMCompany(biz.name)) continue;
            if (biz.location?.state && biz.location.state !== 'TX') continue;
            const addr = biz.location
              ? [biz.location.address1, biz.location.city, biz.location.state, biz.location.zip_code]
                  .filter(Boolean).join(', ')
              : '';
            const co = {
              name:    biz.name || '', address: addr,
              phone:   biz.display_phone || biz.phone || '', website: '',
              contactName: '', contactTitle: '', email: '', emailSource: '',
              source:  'Yelp',
            };
            if (add(co)) info(`    + ${co.name}`);
          }
          await sleep(400);
        }
      }
      info(`\nPhase 2 done — +${companies.length - beforeYelp} new (${dedup.removed} duplicates removed)`);
    } else {
      warn('\nPhase 2 skipped — YELP_API_KEY not set');
    }

    // Save CSV after discovery so --resume can reload it
    companies.sort((a, b) => a.name.localeCompare(b.name));
    writeCSV(companies);
    info(`\nDiscovery CSV saved — ${companies.length} companies`);
  }

  // ── Phase 3: Email waterfall (Apollo → Hunter → Snov.io → Website) ───────
  const needsEmail = companies.filter(c => !c.email);
  const hasEnrichKey = APOLLO_KEY || HUNTER_KEY || SNOV_ID;

  if (needsEmail.length && hasEnrichKey) {
    info(`\nPHASE 3 — Email Enrichment Waterfall`);
    info(`  ${needsEmail.length} companies without email`);
    info(`  Waterfall: ${[APOLLO_KEY && 'Apollo', HUNTER_KEY && 'Hunter', SNOV_ID && 'Snov.io', 'Website'].filter(Boolean).join(' → ')}`);

    const snovToken = (SNOV_ID && SNOV_SECRET) ? await getSnovToken() : null;

    let enriched = 0;
    for (let i = 0; i < needsEmail.length; i++) {
      const co     = needsEmail[i];
      const result = await findEmail(co.name, co.website, snovToken);
      if (result.email) {
        co.email        = result.email;
        co.contactName  = result.contactName  || co.contactName;
        co.contactTitle = result.contactTitle || co.contactTitle;
        co.emailSource  = result.emailSource;
        enriched++;
        const who = result.contactName ? ` (${result.contactName})` : '';
        info(`  [${result.emailSource}] ${co.name}${who} → ${result.email}`);
      }

      // Checkpoint every 10 companies so --resume can pick up mid-run
      if ((i + 1) % 10 === 0) {
        writeCSV(companies);
        info(`  [checkpoint] ${i + 1}/${needsEmail.length} processed — ${enriched} emails so far`);
      }
    }
    info(`\nPhase 3 done — ${enriched}/${needsEmail.length} emails found`);
  } else if (!needsEmail.length) {
    info('\nPhase 3 skipped — all companies already have emails');
  } else {
    warn('\nPhase 3 skipped — no enrichment API keys set (APOLLO_API_KEY, HUNTER_API_KEY, SNOV_CLIENT_ID)');
  }

  // ── Phase 4: Apify batch for JS-heavy sites still without email ───────────
  const apifyTargets = companies.filter(c =>
    !c.email && c.website && !isGenericDomain(extractDomain(c.website))
  );
  if (apifyTargets.length && APIFY_KEY) {
    await apifyBatch(apifyTargets);
  } else if (apifyTargets.length) {
    warn(`\nPhase 4 skipped — ${apifyTargets.length} sites remain without email (set APIFY_KEY to scan JS-heavy sites)`);
  }

  // ── Phase 5: Final CSV ────────────────────────────────────────────────────
  info('\nPHASE 5 — Writing final CSV');
  companies.sort((a, b) => a.name.localeCompare(b.name));
  writeCSV(companies);

  // ── Summary ───────────────────────────────────────────────────────────────
  const elapsed   = ((Date.now() - t0) / 1000).toFixed(1);
  const withEmail = companies.filter(c => c.email).length;
  const emailPct  = companies.length ? Math.round((withEmail / companies.length) * 100) : 0;
  const bySource  = {};
  companies.filter(c => c.emailSource).forEach(c => {
    bySource[c.emailSource] = (bySource[c.emailSource] || 0) + 1;
  });

  info('');
  info('='.repeat(54));
  info(`Finished in ${elapsed}s`);
  info(`Companies  : ${companies.length} unique  (${dedup.removed} duplicates removed)`);
  info(`Email cover: ${withEmail}/${companies.length} (${emailPct}%)`);
  if (Object.keys(bySource).length) {
    info(`By source  : ${Object.entries(bySource).map(([k, v]) => `${k}=${v}`).join('  ')}`);
  }
  info(`CSV output : ${CSV_FILE}`);
  info(`Run log    : ${LOG_FILE}`);
  info('='.repeat(54));
}

main().catch(err => {
  error(`Fatal: ${err.message}`);
  process.exit(1);
});
