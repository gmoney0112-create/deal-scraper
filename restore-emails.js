'use strict';
// One-shot script: patches 48 known-good emails from the log back into the CSV.
const fs   = require('fs');
const path = require('path');

const CSV = path.join(__dirname, 'output', 'property_management_companies.csv');

// Emails extracted from scraper-1781838382918.log (the 222-company / 48-email run)
const EMAIL_MAP = {
  'Real Property Management Alamo - San Antonio':        { email: 'jessica@rpmalamo.com',            source: 'Hunter' },
  'Liberty Management, Inc.':                            { email: 'mark@libertymgt.net',              source: 'Hunter' },
  'Hendricks Property Management':                       { email: 'kyle@sarents.com',                 source: 'Hunter' },
  'RentWerx Property Management':                       { email: 'arturo@rentwerx.com',              source: 'Hunter' },
  'MHN Property Management':                            { email: 'keith@mhnproperties.com',           source: 'Hunter' },
  'Peace of Mind Property Management':                  { email: 'janie@peaceofmind.co',             source: 'Hunter' },
  'ForeFront Property Management':                      { email: 'info@forefront.com',               source: 'Hunter' },
  'Pyramis Company Property Management':                { email: 'amy@pyramiscompany.com',            source: 'Hunter' },
  'Casa Cantera Property Management':                   { email: 'mariel@casacanteratx.com',         source: 'Hunter' },
  'HomeRiver Group San Antonio':                        { email: 'rwilde@homeriver.com',             source: 'Hunter' },
  'Bay Property Management Group San Antonio':          { email: 'info@texasbmg.com',                source: 'Hunter' },
  'Davidson Properties':                                { email: 'jeannie@davidsonproperties.com',   source: 'Hunter' },
  'Real Property Management LoneStar':                  { email: 'vanessa@rpmlonestar.com',          source: 'Hunter' },
  'Strategic Property Management, Inc.':                { email: 'jsepulveda@spm-roi.com',           source: 'Hunter' },
  'PMI Birdy Properties, CRMC':                         { email: 'diana@birdy.com',                  source: 'Hunter' },
  'Wright Property Management Group':                   { email: 'brittanym@wrightpg.com',           source: 'Hunter' },
  'Cornertstone Property Management':                   { email: 'bmcmillan@cornerstonepmtx.com',    source: 'Hunter' },
  'Hance Realty Real Estate & Property Management':     { email: 'silvia@hancerealty.com',           source: 'Hunter' },
  'Red Wagon Properties':                               { email: 'khochart@redwagonproperties.com',  source: 'Hunter' },
  'Windrose Realty, LLC':                               { email: 'elsa@windroserealty.com',          source: 'Hunter' },
  'Rooftop Property Management':                        { email: 'phillip@leasingrpm.com',           source: 'Hunter' },
  'Keyrenter Property Management San Antonio':          { email: 'robert@keyrentersanantonio.com',   source: 'Hunter' },
  'Property Professionals, Inc.':                       { email: 'liaqat@propertynb.com',            source: 'Hunter' },
  'River Tree Property Management':                     { email: 'brittany@rivertreepm.com',         source: 'Hunter' },
  'Grand Welcome New Braunfels':                        { email: 'hello@newbraunfelshost.com',       source: 'Hunter' },
  'Edwards Property Management':                        { email: 'leads@edwardspropertymgmt.com',    source: 'Hunter' },
  'Limestone Country Properties, LLC':                  { email: 'marieg@limestone-country.com',     source: 'Hunter' },
  'Red Mansions Realty':                                { email: 'lexie@rmrteam.com',                source: 'Hunter' },
  'Reliance PMPros':                                    { email: 'ma@reliancepmpros.com',            source: 'Hunter' },
  'Global Realty Group, LLC':                           { email: 'derek@grgsa.com',                  source: 'Hunter' },
  'Real Property Management Alamo - New Braunfels':     { email: 'jessica@rpmalamo.com',             source: 'Hunter' },
  'All County Alamo Property Management':               { email: 'jestrada@allcountyalamo.com',      source: 'Hunter' },
  'TX Real Estate Management':                          { email: 'info@txrealestatemanagement.com',  source: 'Hunter' },
  'Barclé Group New Braunfels Vacation Rental Management': { email: 'connor.albrecht@barclegroup.com', source: 'Hunter' },
  'Emerald Haus Group':                                 { email: 'info@emeraldhausgroup.com',        source: 'Hunter' },
  'Steps Realty,LLC':                                   { email: 'chris@stepsrealtyteam.com',        source: 'Hunter' },
  '3Z Property Management':                             { email: 'info@3zmanagement.com',            source: 'Hunter' },
  'Lokal Property Management':                          { email: 'guillermo@lokal.com',              source: 'Hunter' },
  'Vantage Real Estate Group, LLC':                     { email: 'karen@vantagerealestategroup.net', source: 'Hunter' },
  'Clark Realty & Associates':                          { email: 'kclark@clarkrealtysa.com',         source: 'Hunter' },
  'Randolph Field Realty Inc':                          { email: 'tzimdahl@randolphfield.com',       source: 'Hunter' },
  'Morris Realty':                                      { email: 'craigm@morrisrealtysa.com',        source: 'Hunter' },
  'Real Property Management Hill Country':              { email: 'brett@rpmhillcountry.com',         source: 'Hunter' },
  'LoneStar Properties':                                { email: 'karla@lonestarboerne.com',         source: 'Hunter' },
  'Real Property Management First Class':               { email: 'alice@rpmfirstclass.com',          source: 'Hunter' },
  'HomeLab Property Management':                        { email: 'christopher@homelabpm.com',        source: 'Hunter' },
  'Rose Residential':                                   { email: 'candyrose@roseresidentials.com',   source: 'Hunter' },
  'Brohill Realty Ltd':                                 { email: 'roxie@brohillrealty.com',          source: 'Hunter' },
};

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
    } else if (ch === ',' && !inQ) {
      fields.push(cur); cur = '';
    } else {
      cur += ch;
    }
  }
  fields.push(cur);
  return fields;
}

const raw     = fs.readFileSync(CSV, 'utf8');
const lines   = raw.trim().split(/\r?\n/);
const headers = splitCSVRow(lines[0]);

let patched = 0;
const rows = [lines[0]];

for (const line of lines.slice(1)) {
  const vals = splitCSVRow(line);
  const name = vals[headers.indexOf('Company Name')] || '';
  const match = EMAIL_MAP[name];

  if (match && !vals[headers.indexOf('Email')]) {
    vals[headers.indexOf('Email')]        = match.email;
    vals[headers.indexOf('Email Source')] = match.source;
    patched++;
  }
  rows.push(vals.map(csvEscape).join(','));
}

fs.writeFileSync(CSV, rows.join('\n'), 'utf8');

const total      = lines.length - 1;
const withEmail  = rows.slice(1).filter(r => splitCSVRow(r)[headers.indexOf('Email')]).length;
console.log(`Patched ${patched} emails back into CSV.`);
console.log(`Total: ${total} companies | ${withEmail} with email (${Math.round(withEmail/total*100)}%)`);
console.log(`CSV: ${CSV}`);
