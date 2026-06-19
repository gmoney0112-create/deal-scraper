'use strict';
// Uses Apify's contact-info-scraper actor to find emails for companies
// that have a website but no email in the CSV.
// Actor: vdrmota/contact-info-scraper (crawls site, extracts emails/phones)

const https = require('https');
const fs    = require('fs');
const path  = require('path');

const CSV     = path.join(__dirname, 'output', 'property_management_companies.csv');
const LOG_DIR = path.join(__dirname, 'logs');
fs.mkdirSync(LOG_DIR, { recursive: true });
const LOG = path.join(LOG_DIR, `apify-emails-${Date.now()}.log`);

// Inline .env parser
const envPath = path.join(__dirname, '.env');
const env = {};
fs.readFileSync(envPath, 'utf8').trim().split(/\r?\n/).forEach(line => {
  if (!line || line.startsWith('#')) return;
  const eq = line.indexOf('=');
  if (eq < 0) return;
  env[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
});
const APIFY_KEY = env.APIFY_API_KEY || process.env.APIFY_API_KEY;
if (!APIFY_KEY) { console.error('APIFY_API_KEY not set in .env'); process.exit(1); }

const ACTOR_ID = 'vdrmota~contact-info-scraper';

const GENERIC_EMAIL  = /@(gmail|yahoo|hotmail|outlook|aol|icloud|yelp|google|facebook|example|agentfire|knck|rentmanager|mailchimp|hubspot|wixsite|squarespace|weebly)\./i;
const FILE_EXT_RE    = /\.(png|jpg|jpeg|gif|svg|webp|css|js|pdf|ico|woff|ttf|mp4|mp3|zip|xml|json)$/i;
const PLACEHOLDER_RE = /^(user|test|example|noreply|no-reply)@(domain|example|test|placeholder|yoursite)\.(com|net|org)$/i;

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  fs.appendFileSync(LOG, line + '\n');
  process.stdout.write(line + '\n');
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function apifyReq(method, endpoint, body) {
  return new Promise((resolve, reject) => {
    const sep  = endpoint.includes('?') ? '&' : '?';
    const full = `https://api.apify.com/v2${endpoint}${sep}token=${APIFY_KEY}`;
    const u    = new URL(full);
    const payload = body ? JSON.stringify(body) : null;
    const req = https.request({
      hostname: u.hostname,
      path: u.pathname + u.search,
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(payload ? { 'Content-Length': Buffer.byteLength(payload) } : {}),
      },
    }, res => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', c => data += c);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
        catch { resolve({ status: res.statusCode, body: data }); }
      });
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
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

function normalizeUrl(site) {
  const s = site.trim();
  return s.startsWith('http') ? s : `https://${s}`;
}

function sameOrigin(a, b) {
  try { return new URL(a).origin === new URL(b).origin; }
  catch { return false; }
}

function isGoodEmail(e) {
  return Boolean(e) &&
    !GENERIC_EMAIL.test(e) &&
    !FILE_EXT_RE.test(e) &&
    !PLACEHOLDER_RE.test(e);
}

async function startRun(urls) {
  const { status, body } = await apifyReq('POST', `/acts/${ACTOR_ID}/runs`, {
    startUrls: urls.map(u => ({ url: u })),
    maxCrawlDepth: 2,
    maxPagesPerCrawl: 6,
  });
  if (status !== 201 && status !== 200) {
    throw new Error(`Start failed: HTTP ${status} — ${JSON.stringify(body).slice(0, 200)}`);
  }
  const id = body.data?.id;
  if (!id) throw new Error(`No run ID: ${JSON.stringify(body).slice(0, 200)}`);
  return id;
}

async function waitForRun(runId, timeoutMs = 360000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await sleep(8000);
    const { status, body } = await apifyReq('GET', `/actor-runs/${runId}`);
    if (status !== 200) { log(`  Status check HTTP ${status} — retrying`); continue; }
    const s = body.data?.status;
    log(`  Run ${runId}: ${s}`);
    if (s === 'SUCCEEDED') return true;
    if (['FAILED', 'ABORTED', 'TIMED-OUT'].includes(s)) return false;
  }
  log(`  Timed out after ${timeoutMs / 1000}s`);
  return false;
}

async function getResults(runId) {
  const { status, body } = await apifyReq('GET', `/actor-runs/${runId}/dataset/items`);
  if (status !== 200) { log(`  Dataset fetch HTTP ${status}`); return []; }
  return Array.isArray(body) ? body : (Array.isArray(body?.items) ? body.items : []);
}

async function main() {
  log('='.repeat(60));
  log('Apify Email Enrichment — SA MSA Property Management');
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

  if (targets.length === 0) {
    log('Nothing to do.');
    return;
  }

  const urls = targets.map(r => normalizeUrl(r[idxSite]));
  log(`URLs to scan:\n${urls.map(u => `  ${u}`).join('\n')}\n`);

  let runId;
  try {
    runId = await startRun(urls);
    log(`Run started: ${runId}`);
  } catch (e) {
    log(`ERROR: ${e.message}`);
    process.exit(1);
  }

  log('Waiting for actor to finish (up to 6 min)...');
  const ok = await waitForRun(runId);
  if (!ok) { log('Run did not succeed.'); return; }

  const items = await getResults(runId);
  log(`\nDataset returned ${items.length} item(s)`);

  let found = 0;
  for (const item of items) {
    const pageUrl = item.url || item.inputUrl || '';
    if (!pageUrl) continue;

    const targetRec = targets.find(r => sameOrigin(normalizeUrl(r[idxSite]), pageUrl));
    if (!targetRec) continue;
    if (targetRec[idxEmail]) continue;

    const raw = item.emails || [];
    const emails = raw
      .map(e => (typeof e === 'string' ? e : (e.email || '')))
      .filter(Boolean)
      .map(e => e.toLowerCase())
      .filter(isGoodEmail);

    if (emails.length) {
      targetRec[idxEmail] = emails[0];
      targetRec[idxSrc]   = 'Apify';
      found++;
      log(`  FOUND for ${targetRec[idxName]}: ${emails[0]}`);
    }
  }

  // Write updated CSV
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
  log(`CSV updated: ${CSV}`);
  log(`Log: ${LOG}`);
  log('='.repeat(60));
}

main().catch(e => { console.error('Fatal:', e.message); process.exit(1); });
