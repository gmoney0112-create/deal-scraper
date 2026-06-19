'use strict';
// One-shot: clears 5 known false-positive emails the website scraper picked up.
const fs   = require('fs');
const path = require('path');
const CSV  = path.join(__dirname, 'output', 'property_management_companies.csv');

// Companies whose scraped email was a false positive
const BAD = new Set([
  'John Chunn Realty, LLC',           // chosen-sprite@2x.png — image filename
  'T2M Real Estate',                  // support@agentfire.com — website builder platform
  'Wheeler Property Management',      // user@domain.com — placeholder
  'The Caldwell Seguin',              // thecaldwell-w@m.knck.io — CRM drip address
  'Northwest Real Estate and Property Management', // copernicus@copernicusrealtyllc.com — different company
]);

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

const raw     = fs.readFileSync(CSV, 'utf8');
const lines   = raw.trim().split(/\r?\n/);
const headers = splitCSVRow(lines[0]);
const idxName = headers.indexOf('Company Name');
const idxEmail = headers.indexOf('Email');
const idxSrc   = headers.indexOf('Email Source');

let cleared = 0;
const out = [lines[0]];

for (const line of lines.slice(1)) {
  const cols = splitCSVRow(line);
  const name = cols[idxName];
  if (BAD.has(name) && cols[idxEmail]) {
    console.log(`Clearing bad email for: ${name} (was: ${cols[idxEmail]})`);
    cols[idxEmail] = '';
    cols[idxSrc]   = '';
    cleared++;
  }
  out.push(cols.map(csvEscape).join(','));
}

fs.writeFileSync(CSV, out.join('\n'), 'utf8');

const total     = lines.length - 1;
const withEmail = out.slice(1).filter(l => splitCSVRow(l)[idxEmail]).length;
console.log(`\nCleared ${cleared} false-positive emails.`);
console.log(`Total: ${total} companies | ${withEmail} with email (${Math.round(withEmail/total*100)}%)`);
