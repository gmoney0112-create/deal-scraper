'use strict';

const https = require('https');
const fs    = require('fs');
const path  = require('path');

// Parse .env
try {
  fs.readFileSync(path.join(__dirname, '.env'), 'utf8').split(/\r?\n/).forEach(line => {
    const m = line.match(/^([^#=]+)=(.*)$/);
    if (m) process.env[m[1].trim()] = m[2].trim();
  });
} catch {}

const APOLLO_KEY = process.env.APOLLO_API_KEY;
const CSV_IN     = path.join(__dirname, 'output', 'property_management_companies.csv');
const CSV_OUT    = path.join(__dirname, 'output', 'property_management_companies.csv');
const LOG_FILE   = path.join(__dirname, 'logs', `enrich-${Date.now()}.log`);

if (!APOLLO_KEY) { console.error('APOLLO_API_KEY missing from .env'); process.exit(1); }

const CREDIT_LIMIT = 114; // confirmed by user
const TITLES = [
  'owner', 'president', 'ceo', 'principal', 'managing director',
  'property manager', 'director of property management',
  'broker', 'real estate broker', 'managing broker', 'vice president',
];

const sleep = ms => new Promise(r => setTimeout(r, ms));

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  fs.appendFileSync(LOG_FILE, line + '\n');
  process.stdout.write(line + '\n');
}

function httpPost(url, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const data = JSON.stringify(body);
    const req = https.request(
      { hostname: u.hostname, path: u.pathname + u.search, method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data), ...headers },
        timeout: 15000 },
      res => {
        let raw = '';
        res.on('data', c => (raw += c));
        res.on('end', () => {
          try { resolve({ status: res.statusCode, body: JSON.parse(raw) }); }
          catch { reject(new Error(`JSON parse error (HTTP ${res.statusCode})`)); }
        });
      }
    );
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

// ── CSV parse / serialize ────────────────────────────────────────────────────
function parseCSV(text) {
  const lines = text.trim().split(/\r?\n/);
  const headers = splitCSVRow(lines[0]);
  return lines.slice(1).map(line => {
    const vals = splitCSVRow(line);
    const obj = {};
    headers.forEach((h, i) => { obj[h] = vals[i] ?? ''; });
    return obj;
  });
}

function splitCSVRow(row) {
  const fields = [];
  let cur = '', inQ = false;
  for (let i = 0; i < row.length; i++) {
    const ch = row[i];
    if (ch === '"') {
      if (inQ && row[i+1] === '"') { cur += '"'; i++; }
      else inQ = !inQ;
    } else if (ch === ',' && !inQ) {
      fields.push(cur); cur = '';
    } else {
      cur += ch;
    }
  }
  fields.push(cur);
  return fields;
}

function csvEscape(v) {
  const s = v == null ? '' : String(v);
  return (s.includes(',') || s.includes('"') || s.includes('\n'))
    ? `"${s.replace(/"/g, '""')}"` : s;
}

function extractDomain(site) {
  if (!site) return null;
  try { return new URL(site.startsWith('http') ? site : `https://${site}`).hostname.replace(/^www\./, ''); }
  catch { return null; }
}

const BLOCKED = new Set(['yelp.com','facebook.com','google.com','linkedin.com','instagram.com']);
const GENERIC_EMAIL = /@(yelp|google|gmail|hotmail|outlook|aol|icloud|example)\.com$/i;

function domainCandidates(name, website) {
  const d = extractDomain(website);
  if (d && !BLOCKED.has(d)) return [d];
  const slug = name.toLowerCase()
    .replace(/\b(property|management|realty|real estate|pm|inc|llc|co|company|corp)\b/g, '')
    .replace(/[^a-z0-9]/g, '');
  if (slug.length < 3) return [];
  return [`${slug}.com`, `${slug}pm.com`, `${slug}realty.com`];
}

// ── Apollo people/match ──────────────────────────────────────────────────────
async function apolloMatch(domain, companyName) {
  try {
    const { status, body } = await httpPost(
      'https://api.apollo.io/api/v1/people/match',
      { domain, organization_name: companyName, person_titles: TITLES, reveal_personal_emails: false },
      { 'x-api-key': APOLLO_KEY }
    );
    if (status === 422 || status === 404) return null;
    if (status === 429) { log('WARN Apollo 429 — rate limited, pausing 60s'); await sleep(60000); return null; }
    if (status === 403) { log('WARN Apollo 403 — access denied for this endpoint'); return null; }
    if (status !== 200) { log(`WARN Apollo ${status} for ${domain}`); return null; }

    const p = body.person;
    if (!p) return null;
    const email = p.email;
    if (!email || GENERIC_EMAIL.test(email)) return null;

    return {
      email,
      contactName:  p.name || `${p.first_name || ''} ${p.last_name || ''}`.trim(),
      contactTitle: p.title || '',
    };
  } catch (e) {
    log(`WARN Apollo error for ${domain}: ${e.message}`);
    return null;
  }
}

// ── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
  log('='.repeat(54));
  log('Apollo Enrichment — SA MSA Property Management');
  log(`Credit limit for this run: ${CREDIT_LIMIT}`);
  log('='.repeat(54));

  const raw     = fs.readFileSync(CSV_IN, 'utf8');
  const records = parseCSV(raw);

  const needsEmail = records.filter(r => !r['Email']);
  const toEnrich   = needsEmail.slice(0, CREDIT_LIMIT);

  log(`\nCSV: ${records.length} total | ${needsEmail.length} without email | enriching up to ${toEnrich.length}`);

  let enriched = 0, credits = 0;

  for (const rec of toEnrich) {
    const domains = domainCandidates(rec['Company Name'], rec['Website']);
    if (!domains.length) {
      log(`  SKIP ${rec['Company Name']} — no domain candidates`);
      continue;
    }

    let found = null;
    for (const domain of domains) {
      await sleep(400);
      found = await apolloMatch(domain, rec['Company Name']);
      if (found) break;
    }

    credits++;
    if (found) {
      rec['Email']         = found.email;
      rec['Contact Name']  = found.contactName  || rec['Contact Name'];
      rec['Contact Title'] = found.contactTitle || rec['Contact Title'];
      rec['Email Source']  = 'Apollo';
      enriched++;
      log(`  [Apollo] ${rec['Company Name']} (${found.contactName}) → ${found.email}`);
    } else {
      log(`  [miss]   ${rec['Company Name']}`);
    }

    if (credits >= CREDIT_LIMIT) { log('\nCredit limit reached — stopping enrichment'); break; }
  }

  // Write updated CSV
  const headers = Object.keys(records[0]);
  const lines = [
    headers.map(csvEscape).join(','),
    ...records.map(r => headers.map(h => csvEscape(r[h])).join(',')),
  ];
  fs.writeFileSync(CSV_OUT, lines.join('\n'), 'utf8');

  const totalWithEmail = records.filter(r => r['Email']).length;
  const pct = Math.round((totalWithEmail / records.length) * 100);

  log('');
  log('='.repeat(54));
  log(`Enriched: ${enriched}/${credits} attempts matched`);
  log(`Credits used: ${credits} of ${CREDIT_LIMIT} available`);
  log(`Total email coverage: ${totalWithEmail}/${records.length} (${pct}%)`);
  log(`CSV updated: ${CSV_OUT}`);
  log('='.repeat(54));
}

main().catch(err => { console.error('Fatal:', err.message); process.exit(1); });
