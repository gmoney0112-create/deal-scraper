'use strict';
// One-shot: applies saved Apify contact-info-scraper results to the CSV.

const fs   = require('fs');
const path = require('path');

const CSV        = path.join(__dirname, 'output', 'property_management_companies.csv');
const RESULTS    = path.join(__dirname, 'apify-results-raw.json');

const GENERIC_EMAIL  = /@(gmail|yahoo|hotmail|outlook|aol|icloud|yelp|google|facebook|example|agentfire|knck|rentmanager|mailchimp|hubspot|wixsite|squarespace|weebly)\./i;
const FILE_EXT_RE    = /\.(png|jpg|jpeg|gif|svg|webp|css|js|pdf|ico|woff|ttf|mp4|mp3|zip|xml|json)$/i;
const PLACEHOLDER_RE = /^(user|test|example|noreply|no-reply)@(domain|example|test|placeholder|yoursite)\.(com|net|org)$/i;

// Known bad emails (picked up before and removed)
const KNOWN_BAD = new Set([
  'thecaldwell-w@m.knck.io',
  'copernicus@copernicusrealtyllc.com',
  'user@domain.com',
]);

function isGoodEmail(e) {
  return Boolean(e) &&
    !GENERIC_EMAIL.test(e) &&
    !FILE_EXT_RE.test(e) &&
    !PLACEHOLDER_RE.test(e) &&
    !KNOWN_BAD.has(e.toLowerCase());
}

function getDomain(urlStr) {
  try { return new URL(urlStr).hostname.replace(/^www\./, ''); }
  catch { return ''; }
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

const items   = JSON.parse(fs.readFileSync(RESULTS, 'utf8'));
const raw     = fs.readFileSync(CSV, 'utf8');
const lines   = raw.trim().split(/\r?\n/);
const headers = splitCSVRow(lines[0]);

const idxName  = headers.indexOf('Company Name');
const idxSite  = headers.indexOf('Website');
const idxEmail = headers.indexOf('Email');
const idxSrc   = headers.indexOf('Email Source');

const records = lines.slice(1).map(l => splitCSVRow(l));

let found = 0;
let skipped = 0;

for (const item of items) {
  const originUrl = item.originalStartUrl || '';
  if (!originUrl) continue;
  const apifyDomain = getDomain(originUrl);

  // Find matching record by domain
  const rec = records.find(r => {
    if (!r[idxSite]) return false;
    return getDomain(r[idxSite]) === apifyDomain;
  });

  if (!rec) { console.log(`No CSV match for: ${originUrl}`); continue; }
  if (rec[idxEmail]) { console.log(`Already has email: ${rec[idxName]} (${rec[idxEmail]})`); continue; }

  const emails = (item.emails || [])
    .map(e => (typeof e === 'string' ? e : (e.email || '')))
    .filter(Boolean)
    .map(e => e.toLowerCase())
    .filter(isGoodEmail);

  if (emails.length) {
    rec[idxEmail] = emails[0];
    rec[idxSrc]   = 'Apify';
    found++;
    console.log(`FOUND  ${rec[idxName]}: ${emails[0]}`);
  } else {
    const raw = (item.emails || []).map(e => typeof e === 'string' ? e : e.email).filter(Boolean);
    skipped++;
    console.log(`SKIP   ${rec[idxName]}: ${raw.join(', ') || '(no emails)'}`);
  }
}

const out = [
  lines[0],
  ...records.map(r => headers.map((_, i) => csvEscape(r[i] || '')).join(',')),
];
fs.writeFileSync(CSV, out.join('\n'), 'utf8');

const totalEmail = records.filter(r => r[idxEmail]).length;
const pct = Math.round((totalEmail / records.length) * 100);

console.log('');
console.log(`Applied: ${found} new emails | ${skipped} filtered out`);
console.log(`Total coverage: ${totalEmail} / ${records.length} (${pct}%)`);
