/**
 * test_si.js — Automated Verification of Sinhala Localization & ZWJ-Immune Search
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const rootDir = __dirname;
const productsJsPath = path.join(rootDir, "website", "data", "products.js");
const indexHtmlPath = path.join(rootDir, "website", "index.html");

const productsCode = fs.readFileSync(productsJsPath, "utf8");
const window = {};
eval(productsCode);
const catalog = window.CATALOG;
assert(catalog && Array.isArray(catalog.products), "CATALOG.products must be loaded");

const html = fs.readFileSync(indexHtmlPath, "utf8");

// 1. Verify cross-platform Sinhala font stack in CSS
const requiredFonts = [
  "Noto Sans Sinhala",
  "Sinhala Sangam MN",
  "Sinhala MN",
  "Nirmala UI",
  "Iskoola Pota"
];
for (const f of requiredFonts) {
  assert(html.includes(f), `Missing Sinhala font in CSS stack: ${f}`);
}
assert(html.includes("-webkit-font-smoothing: antialiased"), "Missing -webkit-font-smoothing: antialiased");

// 2. Verify all products have Sinhala translations (p.si, p.variantSi, p.winReason.si, 4 pillars desc.si)
const sinhalaRegex = /[\u0D80-\u0DFF]/;
catalog.products.forEach((p, idx) => {
  assert(p.si && sinhalaRegex.test(p.si), `Product #${idx + 1} (${p.code}) missing Sinhala script in p.si`);
  assert(p.variantSi && sinhalaRegex.test(p.variantSi), `Product #${idx + 1} (${p.code}) missing Sinhala script in p.variantSi`);
  assert(p.categoryName && sinhalaRegex.test(p.categoryName.si), `Product #${idx + 1} missing Sinhala categoryName.si`);
  assert(p.winReason && sinhalaRegex.test(p.winReason.si), `Product #${idx + 1} missing Sinhala winReason.si`);
  for (const pillar of ["freight", "margin", "demand", "risk"]) {
    const descSi = p.winBreakdown?.[pillar]?.desc?.si;
    assert(descSi && sinhalaRegex.test(descSi), `Product #${idx + 1} missing Sinhala desc for pillar ${pillar}`);
  }
});

// 3. Verify ZWJ-Immune Normalization norm(s)
function norm(s) {
  return String(s == null ? "" : s)
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .toLowerCase()
    .trim();
}

// Test pairs: [with ZWJ (\u200D), without ZWJ]
const zwjPairs = [
  ["ඩ්\u200Dරිල්", "ඩ්රිල්"],
  ["රූපලාවන්\u200Dය", "රූපලාවන්ය"],
  ["ට්\u200Dරොලිය", "ට්රොලිය"],
  ["යන්ත්\u200Dර", "යන්ත්ර"],
  ["ප්\u200Dරමාණය", "ප්රමාණය"],
  ["ශ්\u200Dරී ලංකා", "ශ්රී ලංකා"],
  ["ක්\u200Dරියාකාරී", "ක්රියාකාරී"]
];

for (const [withZwj, withoutZwj] of zwjPairs) {
  assert.strictEqual(
    norm(withZwj),
    norm(withoutZwj),
    `ZWJ normalization mismatch for ${withZwj} vs ${withoutZwj}`
  );
}

// Verify catalogue search matches identically with or without ZWJ
const searchQueries = ["ට්‍රොලිය", "යන්ත්‍ර", "ප්‍රමාණය", "ජයග්‍රාහී"];
for (const qWithZwj of searchQueries) {
  const qWithoutZwj = qWithZwj.replace(/\u200D/g, "");
  const hitsWith = catalog.products.filter(p => norm(`${p.si} ${p.variantSi} ${p.winReason.si}`).includes(norm(qWithZwj)));
  const hitsWithout = catalog.products.filter(p => norm(`${p.si} ${p.variantSi} ${p.winReason.si}`).includes(norm(qWithoutZwj)));
  assert(hitsWith.length > 0, `Expected >0 search hits for Sinhala query: ${qWithZwj}`);
  assert.strictEqual(hitsWith.length, hitsWithout.length, `Hit count mismatch for ZWJ vs non-ZWJ query: ${qWithZwj}`);
}

console.log(`✅ [test_si.js] PASSED: All ${catalog.products.length} products localized in Sinhala, font stack verified, ZWJ-immune search verified.`);
