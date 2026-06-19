'use strict';
// Snov.io domain email search — queries Snov.io's database for professional
// emails at each company domain. Covers companies with a website but no email.

const https = require('https');
const fs    = require('fs');
const path  = require('path');

const CSV     = path.join(__dirname, 'output', 'property_management_companies.csv');
const LOG_DIR = path.join(__dirname, 'logs');
fs.mkdirSync(LOG_DIR, { recursive: true });
const LOG = path.join(LOG_DIR, `snov-emails-${Date.now()}.log`);

// Inline .env parser
const envPath = path.join(__dirname, '.env');
const env = {};
fs.readFileSync(envPath, 'utf8').trim().split(/\r?\n/).forEach(line => {
  if (!line || line.startsWith('#')) return;
  const eq = line.indexOf('=');
  if (eq < 0) return;
  env[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
});
const CLIENT_ID     = env.SNOV_CLIENT_ID;
const CLIENT_SECRET = env.SNOV_CLIENT_SECRET;
if (!CLIENT_ID || !CLIENT_SECRET) {
  console.error('SNOV_CLIENT_ID/SNOV_CLIENT_SECRET not in .env'); process.exit(1);
}

const GENERIC_EMAIL  = /@(gmail|yahoo|hotmail|outlook|aol|icloud|yelp|google|facebook|example|agentfire|knck|rentmanager|mailchimp|hubspot|wixsite|squarespace|weebly)\./i;
const FILE_EXT_RE    = /\.(png|jpg|jpeg|gif|svg|webp|css|js|pdf|ico|woff|ttf|mp4|mp3|zip|xml|json)$/i;
const PLACEHOLDER_RE = /^(user|test|example|noreply|no-reply)@(domain|example|test|placeholder|yoursite)\.(com|net|org)$/i;
const KNOWN_BAD      = new Set(['thecaldwell-w@m.knck.io','copernicus@copernicusrealtyllc.com']);

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  fs.appendFileSync(LOG, line + '\n');
  process.stdout.write(line + '\n');
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function isGoodEmail(e) {
  const s = (e || '').toLowerCase();
  return Boolean(s) && !GENERIC_EMAIL.test(s) && !FILE_EXT_RE.test(s) &&
         !PLACEHOLDER_RE.test(s) && !KNOWN_BAD.has(s);
}

function getDomain(urlStr) {
  try { return new URL(urlStr.startsWith('http') ? urlStr : 'https://' + urlStr).hostname.replace(/^www\./, ''); }
  catch { return ''; }
}

function httpsPost(hostname, path, payload) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload);
    const req  = https.request({
      hostname, path, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
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
    req.write(body);
    req.end();
  });
}

function httpsGet(url) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const req = https.get({ hostname: u.hostname, path: u.pathname + u.search }, res => {
      let d = '';
      res.setEncoding('utf8');
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(d) }); }
        catch { resolve({ status: res.statusCode, body: d }); }
      });
    });
    req.on('error', reject);
  });
}

async function getToken() {
  return new Promise((resolve, reject) => {
    const body = new URLSearchParams({
      grant_type: 'client_credentials',
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
    }).toString();
    const req = https.request({
      hostname: 'api.snov.io',
      path: '/v1/oauth/access_token',
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Content-Length': Buffer.byteLength(body),
      },
    }, res => {
      let d = '';
      res.setEncoding('utf8');
      res.on('data', c => d += c);
      res.on('end', () => {
        try {
          const b = JSON.parse(d);
          if (b.access_token) resolve(b.access_token);
          else reject(new Error(`Auth failed (${res.statusCode}): ${d.slice(0, 200)}`));
        } catch (e) { reject(new Error(`Auth parse error: ${d.slice(0, 200)}`)); }
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

async function searchDomain(token, domain) {
  const qs = new URLSearchParams({ token, domain, type: 'all', limit: '10' });
  const { status, body } = await httpsGet(`https://api.snov.io/v1/get-emails-from-domain?${qs}`);
  if (status !== 200) {
    log(`  Snov HTTP ${status} for ${domain}: ${JSON.stringify(body).slice(0, 100)}`);
    return [];
  }
  // Response: { success: true, emails: [{ email, firstName, lastName, position, ... }] }
  // OR: { emails: [...] } depending on plan
  const list = body.emails || body.data?.emails || [];
  return list.map(e => (typeof e === 'string' ? e : e.email)).filter(Boolean).map(s => s.toLowerCase());
}

function splitCSVRow(row) {
  const fields = [];
  let cur = '', inQ = false;
  for (let i = 0; i < row.length; i++) {
    const ch = row[i];
    if (ch === '"') {
      if (inQ && row[i + 1] === '"') { cur += '"'; i++; }
      else inQ = !inQ;
    } else if (ch === ',' && !inQ) { fields.push(cur); cur = ''; }
    else cur += ch;
  }
  fields.push(cur);
  return fields;
}

function csvEscape(v) {
  const s = v == null ? '' : String(v);
  return (s.includes(',') || s.includes('"') || s.includes('\n'))
    ? `"${s.replace(/"/g, '""')}"` : s;
}

async function main() {
  log('='.repeat(60));
  log('Snov.io Domain Email Search — SA MSA Property Management');
  log('='.repeat(60));

  const raw     = fs.readFileSync(CSV, 'utf8');
  const lines   = raw.trim().split(/\r?\n/);
  const headers = splitCSVRow(lines[0]);

  const idxName  = headers.indexOf('Company Name');
  const idxSite  = headers.indexOf('Website');
  const idxEmail = headers.indexOf('Email');
  const idxSrc   = headers.indexOf('Email Source');

  const records = lines.slice(1).map(l => splitCSVRow(l));
  const targets = records.filter(r => r[idxSite] && !r[idxEmail]);

  log(`\nCompanies with website but no email: ${targets.length}`);
  if (targets.length === 0) { log('Nothing to do.'); return; }

  log('Getting Snov.io access token...');
  let token;
  try { token = await getToken(); log('Token OK'); }
  catch (e) { log(`ERROR: ${e.message}`); process.exit(1); }

  let found = 0;
  for (const rec of targets) {
    const domain = getDomain(rec[idxSite]);
    if (!domain) { log(`SKIP ${rec[idxName]}: can't parse domain`); continue; }

    log(`Searching: ${rec[idxName]} (${domain})`);
    try {
      const emails = await searchDomain(token, domain);
      const good   = emails.filter(isGoodEmail);
      if (good.length) {
        rec[idxEmail] = good[0];
        rec[idxSrc]   = 'Snov.io';
        found++;
        log(`  FOUND: ${good[0]}${good.length > 1 ? ` (also: ${good.slice(1).join(', ')})` : ''}`);
      } else {
        log(`  MISS — ${emails.length ? `filtered: ${emails.join(', ')}` : 'no emails in DB'}`);
      }
    } catch (e) {
      log(`  ERROR: ${e.message}`);
    }
    await sleep(1200); // ~50 req/min safety margin
  }

  const out = [
    lines[0],
    ...records.map(r => headers.map((_, i) => csvEscape(r[i] || '')).join(',')),
  ];
  fs.writeFileSync(CSV, out.join('\n'), 'utf8');

  const totalEmail = records.filter(r => r[idxEmail]).length;
  const pct = Math.round((totalEmail / records.length) * 100);

  log('');
  log('='.repeat(60));
  log(`New emails found: ${found} / ${targets.length}`);
  log(`Total coverage: ${totalEmail} / ${records.length} (${pct}%)`);
  log(`Log: ${LOG}`);
  log('='.repeat(60));
}

main().catch(e => { console.error('Fatal:', e.message); process.exit(1); });
