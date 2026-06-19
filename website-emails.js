'use strict';
// Scrapes each company website for email addresses — no API cost, works directly from their pages.
// Targets companies in the CSV that have a website but no email.
// Visits homepage + /contact, /about, /contact-us, /about-us — picks first non-generic email found.

const https  = require('https');
const http   = require('http');
const fs     = require('fs');
const path   = require('path');

const CSV     = path.join(__dirname, 'output', 'property_management_companies.csv');
const LOG_DIR = path.join(__dirname, 'logs');
fs.mkdirSync(LOG_DIR, { recursive: true });
const LOG = path.join(LOG_DIR, `website-emails-${Date.now()}.log`);

const GENERIC_EMAIL = /@(gmail|yahoo|hotmail|outlook|aol|icloud|yelp|google|facebook|example|agentfire|knck|rentmanager|mailchimp|hubspot|wixsite|squarespace|weebly)\./i;
const FILE_EXT_RE   = /\.(png|jpg|jpeg|gif|svg|webp|css|js|pdf|ico|woff|ttf|mp4|mp3|zip|xml|json)$/i;
const PLACEHOLDER_RE = /^(user|test|example|noreply|no-reply)@(domain|example|test|placeholder|yoursite)\.(com|net|org)$/i;
const CONTACT_PATHS = ['', '/contact', '/contact-us', '/about', '/about-us', '/team', '/staff'];
const TIMEOUT_MS    = 12000;

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  fs.appendFileSync(LOG, line + '\n');
  process.stdout.write(line + '\n');
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function fetchPage(urlStr, redirectCount = 0) {
  if (redirectCount > 3) return Promise.resolve(null);
  return new Promise(resolve => {
    // Hard deadline — covers DNS hang, TCP stall, TLS delay
    const timer = setTimeout(() => resolve(null), TIMEOUT_MS);
    const done  = val => { clearTimeout(timer); resolve(val); };
    const lib   = urlStr.startsWith('https') ? https : http;
    try {
      const req = lib.get(urlStr, { headers: { 'User-Agent': 'Mozilla/5.0' } }, res => {
        if ((res.statusCode === 301 || res.statusCode === 302) && res.headers.location) {
          const redir = res.headers.location.startsWith('http')
            ? res.headers.location
            : new URL(res.headers.location, urlStr).href;
          res.resume();
          clearTimeout(timer);
          fetchPage(redir, redirectCount + 1).then(resolve);
          return;
        }
        if (res.statusCode !== 200) { res.resume(); return done(null); }
        let body = '';
        res.setEncoding('utf8');
        res.on('data', chunk => {
          body += chunk;
          if (body.length > 200000) { req.destroy(); done(body); }
        });
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
  return [...new Set(raw.map(e => e.toLowerCase()))].filter(e =>
    !GENERIC_EMAIL.test(e) &&
    !FILE_EXT_RE.test(e) &&
    !PLACEHOLDER_RE.test(e)
  );
}

function normalizeUrl(site) {
  const s = site.trim();
  return s.startsWith('http') ? s : `https://${s}`;
}

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
      if (inQ && row[i + 1] === '"') { cur += '"'; i++; }
      else inQ = !inQ;
    } else if (ch === ',' && !inQ) { fields.push(cur); cur = ''; }
    else cur += ch;
  }
  fields.push(cur);
  return fields;
}

async function scrapeWebsite(baseUrl) {
  const base = normalizeUrl(baseUrl);
  for (const p of CONTACT_PATHS) {
    const url = p ? `${base.replace(/\/$/, '')}${p}` : base;
    const html = await fetchPage(url);
    const emails = extractEmails(html);
    if (emails.length) return emails[0]; // first non-generic email wins
    await sleep(300);
  }
  return null;
}

async function main() {
  log('='.repeat(56));
  log('Website Email Scraper — SA MSA Property Management');
  log('='.repeat(56));

  const raw     = fs.readFileSync(CSV, 'utf8');
  const lines   = raw.trim().split(/\r?\n/);
  const headers = splitCSVRow(lines[0]);

  const idxName   = headers.indexOf('Company Name');
  const idxSite   = headers.indexOf('Website');
  const idxEmail  = headers.indexOf('Email');
  const idxSrc    = headers.indexOf('Email Source');
  const idxCName  = headers.indexOf('Contact Name');

  const records = lines.slice(1).map(l => splitCSVRow(l));
  const targets = records.filter(r => r[idxSite] && !r[idxEmail]);

  log(`\nCSV total: ${records.length} | with website, no email: ${targets.length}`);
  log('Starting website scrape...\n');

  let found = 0;
  for (const rec of targets) {
    const name = rec[idxName];
    const site = rec[idxSite];
    log(`Scraping: ${name} (${site})`);

    const email = await scrapeWebsite(site);
    if (email) {
      rec[idxEmail] = email;
      rec[idxSrc]   = 'Website';
      found++;
      log(`  FOUND: ${email}`);
    } else {
      log(`  MISS — no email on site`);
    }
    await sleep(500);
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
  log('='.repeat(56));
  log(`Found: ${found}/${targets.length} new emails from websites`);
  log(`Total email coverage: ${totalEmail}/${records.length} (${pct}%)`);
  log(`CSV updated: ${CSV}`);
  log(`Log: ${LOG}`);
  log('='.repeat(56));
}

main().catch(e => { console.error('Fatal:', e.message); process.exit(1); });
