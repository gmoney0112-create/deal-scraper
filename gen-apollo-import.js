'use strict';
// Generates a clean Apollo UI import CSV from property_management_companies.csv.
// Only exports records that have an email. Apollo UI import creates contacts
// with existence_level "full" — properly indexed and searchable.

const fs   = require('fs');
const path = require('path');

const CSV    = path.join(__dirname, 'output', 'property_management_companies.csv');
const OUT    = path.join(__dirname, 'output', 'apollo_import.csv');

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

function splitName(fullName) {
  if (!fullName) return ['', ''];
  const parts = fullName.trim().split(/\s+/);
  return parts.length === 1 ? [parts[0], ''] : [parts[0], parts.slice(1).join(' ')];
}

const raw     = fs.readFileSync(CSV, 'utf8');
const lines   = raw.trim().split(/\r?\n/);
const headers = splitCSVRow(lines[0]);

const idxName    = headers.indexOf('Company Name');
const idxEmail   = headers.indexOf('Email');
const idxCName   = headers.indexOf('Contact Name');
const idxTitle   = headers.indexOf('Contact Title');
const idxPhone   = headers.indexOf('Phone');
const idxSite    = headers.indexOf('Website');
const idxCity    = headers.indexOf('City');
const idxState   = headers.indexOf('State');

const records = lines.slice(1).map(l => splitCSVRow(l)).filter(r => r[idxEmail]);

// Apollo import column order
const apolloHeaders = [
  'First Name', 'Last Name', 'Email', 'Title',
  'Company', 'Phone', 'Website', 'City', 'State',
];

const rows = [apolloHeaders.join(',')];

for (const rec of records) {
  const [firstName, lastName] = splitName(rec[idxCName]);
  const row = [
    firstName,
    lastName,
    rec[idxEmail],
    rec[idxTitle] || 'Property Manager',
    rec[idxName],
    rec[idxPhone] || '',
    rec[idxSite]  || '',
    rec[idxCity]  || 'San Antonio',
    rec[idxState] || 'TX',
  ];
  rows.push(row.map(csvEscape).join(','));
}

fs.writeFileSync(OUT, rows.join('\n'), 'utf8');
console.log(`Apollo import CSV: ${records.length} contacts`);
console.log(`Saved: ${OUT}`);
