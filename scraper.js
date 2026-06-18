'use strict';

require('dotenv').config();
const https = require('https');
const fs = require('fs');
const path = require('path');

// ── API keys ──────────────────────────────────────────────────────────────────
const GOOGLE_KEY = process.env.GOOGLE_PLACES_KEY;
const YELP_KEY   = process.env.YELP_API_KEY;
const HUNTER_KEY = process.env.HUNTER_API_KEY;

if (!GOOGLE_KEY) {
  console.error('ERROR: GOOGLE_PLACES_KEY is missing from .env');
  process.exit(1);
}

// ── San Antonio MSA locations ─────────────────────────────────────────────────
// Covers all 8 MSA counties: Bexar, Comal, Guadalupe, Wilson, Atascosa,
// Bandera, Kendall, Medina
const MSA_LOCATIONS = [
  { city: 'San Antonio',  state: 'TX', lat: 29.4241,  lng: -98.4936 },
  { city: 'New Braunfels', state: 'TX', lat: 29.7030, lng: -98.1245 },
  { city: 'Seguin',        state: 'TX', lat: 29.5688,  lng: -97.9644 },
  { city: 'Schertz',       state: 'TX', lat: 29.5538,  lng: -98.2695 },
  { city: 'Floresville',   state: 'TX', lat: 29.1366,  lng: -98.1545 },
  { city: 'Boerne',        state: 'TX', lat: 29.7947,  lng: -98.7320 },
  { city: 'Pleasanton',    state: 'TX', lat: 28.9666,  lng: -98.4796 },
  { city: 'Hondo',         state: 'TX', lat: 29.3480,  lng: -99.1417 },
];

const GOOGLE_QUERIES = [
  'property management company',
  'property management',
  'real estate property management',
];

const YELP_TERMS = [
  'property management',
  'real estate management',
];

const RADIUS_METERS = 30000; // 30 km per city center

// ── Utilities ─────────────────────────────────────────────────────────────────
const sleep = ms => new Promise(r => setTimeout(r, ms));

function httpGet(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const options = {
      hostname: parsed.hostname,
      path: parsed.pathname + parsed.search,
      headers: { 'User-Agent': 'deal-scraper/1.0', ...headers },
      timeout: 15000,
    };
    const req = https.get(options, res => {
      let raw = '';
      res.on('data', chunk => (raw += chunk));
      res.on('end', () => {
        try { resolve(JSON.parse(raw)); }
        catch (e) { reject(new Error(`JSON parse failed: ${raw.slice(0, 120)}`)); }
      });
    });
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timed out')); });
    req.on('error', reject);
  });
}

function normalizePhone(phone) {
  return phone ? phone.replace(/\D/g, '') : '';
}

function extractDomain(website) {
  if (!website) return null;
  try {
    const u = new URL(website.startsWith('http') ? website : `https://${website}`);
    return u.hostname.replace(/^www\./, '');
  } catch { return null; }
}

function csvEscape(val) {
  const s = val == null ? '' : String(val);
  return s.includes(',') || s.includes('"') || s.includes('\n')
    ? `"${s.replace(/"/g, '""')}"`
    : s;
}

// ── Google Places ─────────────────────────────────────────────────────────────
async function googleTextSearch(query, lat, lng, pageToken) {
  const params = new URLSearchParams({
    query,
    location: `${lat},${lng}`,
    radius: String(RADIUS_METERS),
    key: GOOGLE_KEY,
    ...(pageToken ? { pagetoken: pageToken } : {}),
  });
  return httpGet(`https://maps.googleapis.com/maps/api/place/textsearch/json?${params}`);
}

async function googlePlaceDetails(placeId) {
  const params = new URLSearchParams({
    place_id: placeId,
    fields: 'name,formatted_address,formatted_phone_number,website,business_status',
    key: GOOGLE_KEY,
  });
  const res = await httpGet(`https://maps.googleapis.com/maps/api/place/details/json?${params}`);
  return res.result || {};
}

async function scrapeGoogle(query, loc) {
  const results = [];
  let pageToken = null;
  let pages = 0;

  do {
    if (pageToken) await sleep(2200); // Google requires a pause before next_page_token activates
    const data = await googleTextSearch(query, loc.lat, loc.lng, pageToken);
    if (data.status === 'REQUEST_DENIED') {
      console.error(`  Google API denied: ${data.error_message}`);
      break;
    }
    if (data.status !== 'OK' && data.status !== 'ZERO_RESULTS') {
      console.warn(`  Google status: ${data.status}`);
      break;
    }
    results.push(...(data.results || []));
    pageToken = data.next_page_token || null;
    pages++;
    await sleep(300);
  } while (pageToken && pages < 3);

  return results;
}

// ── Yelp ──────────────────────────────────────────────────────────────────────
async function scrapeYelp(term, loc) {
  if (!YELP_KEY) return [];
  const params = new URLSearchParams({
    term,
    location: `${loc.city}, ${loc.state}`,
    limit: '50',
    categories: 'realestate,propmanagement',
  });
  try {
    const data = await httpGet(
      `https://api.yelp.com/v3/businesses/search?${params}`,
      { Authorization: `Bearer ${YELP_KEY}` },
    );
    return data.businesses || [];
  } catch (e) {
    console.warn(`  Yelp error for ${loc.city}: ${e.message}`);
    return [];
  }
}

// ── Hunter.io ─────────────────────────────────────────────────────────────────
async function hunterDomainSearch(domain) {
  if (!HUNTER_KEY || !domain) return '';
  const params = new URLSearchParams({ domain, api_key: HUNTER_KEY, limit: '1' });
  try {
    const data = await httpGet(`https://api.hunter.io/v2/domain-search?${params}`);
    const emails = data.data?.emails || [];
    return emails.length > 0 ? emails[0].value : '';
  } catch { return ''; }
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  console.log('Deal Scraper — San Antonio MSA Property Management');
  console.log('='.repeat(52));
  console.log(`Google Places: yes | Yelp: ${YELP_KEY ? 'yes' : 'no (key missing)'} | Hunter: ${HUNTER_KEY ? 'yes' : 'no (key missing)'}`);
  console.log('');

  const seenPlaceIds = new Set();
  const seenPhones   = new Set();
  const companies    = [];

  // ── Phase 1: Google Places ──────────────────────────────────────────────
  console.log('PHASE 1: Google Places API');
  console.log('-'.repeat(52));

  for (const loc of MSA_LOCATIONS) {
    for (const query of GOOGLE_QUERIES) {
      process.stdout.write(`Searching: "${query}" near ${loc.city} ... `);
      const rawResults = await scrapeGoogle(query, loc);
      process.stdout.write(`${rawResults.length} raw\n`);

      for (const place of rawResults) {
        if (seenPlaceIds.has(place.place_id)) continue;
        seenPlaceIds.add(place.place_id);

        await sleep(100);
        const detail = await googlePlaceDetails(place.place_id);
        if (detail.business_status === 'PERMANENTLY_CLOSED') continue;

        const phone = normalizePhone(detail.formatted_phone_number || '');
        if (phone && seenPhones.has(phone)) continue;
        if (phone) seenPhones.add(phone);

        companies.push({
          name:    detail.name || place.name || '',
          address: detail.formatted_address || place.formatted_address || '',
          phone:   detail.formatted_phone_number || '',
          website: detail.website || '',
          email:   '',
          source:  'Google Places',
        });

        process.stdout.write(`  + ${detail.name || place.name}\n`);
        await sleep(120);
      }
    }
  }

  // ── Phase 2: Yelp ───────────────────────────────────────────────────────
  if (YELP_KEY) {
    console.log('');
    console.log('PHASE 2: Yelp Fusion API');
    console.log('-'.repeat(52));

    for (const loc of MSA_LOCATIONS) {
      for (const term of YELP_TERMS) {
        process.stdout.write(`Searching: "${term}" in ${loc.city} ... `);
        const businesses = await scrapeYelp(term, loc);
        process.stdout.write(`${businesses.length} results\n`);

        for (const biz of businesses) {
          const phone = normalizePhone(biz.phone || '');
          if (phone && seenPhones.has(phone)) continue;
          if (phone) seenPhones.add(phone);

          const addr = biz.location
            ? [biz.location.address1, biz.location.city, biz.location.state, biz.location.zip_code]
                .filter(Boolean).join(', ')
            : '';

          companies.push({
            name:    biz.name || '',
            address: addr,
            phone:   biz.display_phone || biz.phone || '',
            website: biz.url || '',
            email:   '',
            source:  'Yelp',
          });
          process.stdout.write(`  + ${biz.name}\n`);
        }
        await sleep(400);
      }
    }
  }

  // ── Phase 3: Hunter.io email enrichment ─────────────────────────────────
  if (HUNTER_KEY) {
    console.log('');
    console.log('PHASE 3: Hunter.io Email Enrichment');
    console.log('-'.repeat(52));

    let enriched = 0;
    for (const co of companies) {
      const domain = extractDomain(co.website);
      if (!domain) continue;
      await sleep(300);
      const email = await hunterDomainSearch(domain);
      if (email) {
        co.email = email;
        enriched++;
        process.stdout.write(`  ${co.name} -> ${email}\n`);
      }
    }
    console.log(`Enriched: ${enriched}/${companies.length} companies`);
  }

  // ── Write CSV ────────────────────────────────────────────────────────────
  const headers = ['Company Name', 'Address', 'Phone', 'Website', 'Email', 'Source'];
  const rows = [
    headers.map(csvEscape).join(','),
    ...companies.map(c =>
      [c.name, c.address, c.phone, c.website, c.email, c.source].map(csvEscape).join(',')
    ),
  ];

  const outFile = path.join(__dirname, 'property_management_companies.csv');
  fs.writeFileSync(outFile, rows.join('\n'), 'utf8');

  console.log('');
  console.log('='.repeat(52));
  console.log(`Done! ${companies.length} unique companies saved to:`);
  console.log(`  ${outFile}`);
  if (HUNTER_KEY) {
    const withEmail = companies.filter(c => c.email).length;
    const pct = companies.length ? Math.round((withEmail / companies.length) * 100) : 0;
    console.log(`  Email coverage: ${withEmail}/${companies.length} (${pct}%)`);
  }
}

main().catch(err => {
  console.error('Fatal error:', err.message);
  process.exit(1);
});
