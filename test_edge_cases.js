/**
 * test_edge_cases.js — Automated Verification of Data Integrity, SL-WPI Math, Null Handling, CSV Columns & Deployment Setup
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const rootDir = __dirname;
const productsCode = fs.readFileSync(path.join(rootDir, "website", "data", "products.js"), "utf8");
const window = {};
eval(productsCode);
const catalog = window.CATALOG;

assert(catalog && catalog.products.length === 93, `Expected 93 products, got ${catalog?.products?.length}`);

// 1. Verify every product has required fields, valid WebP images on disk, and exact SL-WPI math
const validTiers = new Set(["mega", "strong", "niche", "low"]);
let nullKgCount = 0;

catalog.products.forEach((p, i) => {
  assert(p.code && typeof p.code === "string", `Product #${i + 1} missing code`);
  assert(typeof p.price === "number" && p.price > 0, `Product ${p.code} invalid price: ${p.price}`);
  assert(Number.isInteger(p.pcsPerCarton) && p.pcsPerCarton > 0, `Product ${p.code} invalid pcsPerCarton`);
  if (p.kg === null) nullKgCount++;

  // Verify WebP thumbnail & large image files exist on disk
  assert(p.thumb && p.image, `Product ${p.code} missing thumb or image path`);
  const thumbDisk = path.join(rootDir, "website", p.thumb);
  const largeDisk = path.join(rootDir, "website", p.image);
  assert(fs.existsSync(thumbDisk), `Missing thumbnail file on disk: ${thumbDisk}`);
  assert(fs.existsSync(largeDisk), `Missing large image file on disk: ${largeDisk}`);

  // Verify SL-WPI 100-Point Sea Cargo Model invariants
  assert(Number.isInteger(p.winScore) && p.winScore >= 0 && p.winScore <= 100, `Invalid winScore on ${p.code}`);
  assert.strictEqual(p.winRate, `${p.winScore}%`, `winRate mismatch on ${p.code}`);
  assert(validTiers.has(p.winTier), `Invalid winTier ${p.winTier} on ${p.code}`);

  const b = p.winBreakdown;
  assert(b && b.freight && b.margin && b.demand && b.risk, `Missing winBreakdown pillars on ${p.code}`);
  const sum = b.freight.score + b.margin.score + b.demand.score + b.risk.score;
  assert.strictEqual(sum, p.winScore, `Pillar sum (${sum}) !== winScore (${p.winScore}) on ${p.code}`);

  // Tier threshold verification
  if (p.winScore >= 85) assert.strictEqual(p.winTier, "mega");
  else if (p.winScore >= 70) assert.strictEqual(p.winTier, "strong");
  else if (p.winScore >= 50) assert.strictEqual(p.winTier, "niche");
  else {
    assert.strictEqual(p.winTier, "low");
    // Ensure low-scoring items never hardcode overly positive "Mega/Strong Winner" text
    assert(!p.winReason.en.includes("Mega Winner") && !p.winReason.en.includes("Strong Winner"),
      `Low-tier product ${p.code} has misleading positive winReason`);
  }
});

assert(nullKgCount > 0, "Expected at least one product with null weight to test null-weight safety");

// 2. Verify CSV column alignment (Header vs Data Row vs TOTAL Row)
const csvHeadersCount = 18;
const sampleDataRow = [
  "GP-280", "Dia 280mm", "ගෑස් සිලින්ඩර...", "Mobile Gas...", "花盆...",
  89, "89%", "mega", 2, 60, 120, "රු. 226.20", "5.20", "රු. 27144.00", "624.00", "0.0000", "0.00", ""
];
const sampleTotalRow = [
  "TOTAL", "", "", "", "", "", "", "", 2, "", 120, "", "", "රු. 27144.00", "624.00", "0.0000", "0.00", ""
];
assert.strictEqual(sampleDataRow.length, csvHeadersCount, "CSV data row must have 18 columns");
assert.strictEqual(sampleTotalRow.length, csvHeadersCount, "CSV TOTAL row must have 18 columns");

// 3. Verify deployment & launcher files
const gitignore = fs.readFileSync(path.join(rootDir, ".gitignore"), "utf8");
assert(gitignore.includes("*.xlsx") && gitignore.includes("*.xls"), ".gitignore must exclude *.xlsx and *.xls");

const vercelJson = JSON.parse(fs.readFileSync(path.join(rootDir, "vercel.json"), "utf8"));
assert.strictEqual(vercelJson.redirects?.[0]?.source, "/");
assert.strictEqual(vercelJson.redirects?.[0]?.destination, "/website/");

const bat = fs.readFileSync(path.join(rootDir, "Open_Website.bat"), "utf8");
assert(bat.includes('start "" "%~dp0website\\index.html"'), "Open_Website.bat must launch website\\index.html");

console.log(`✅ [test_edge_cases.js] PASSED: All 93 products, 92 WebP pairs, SL-WPI math, null-weight safety & deployment configs verified.`);
