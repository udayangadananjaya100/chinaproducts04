/**
 * test_dom_sim.js — End-to-End DOM Simulation & Function Execution Test
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const rootDir = __dirname;
const productsCode = fs.readFileSync(path.join(rootDir, "website", "data", "products.js"), "utf8");
const html = fs.readFileSync(path.join(rootDir, "website", "index.html"), "utf8");

// Extract inline <script> from website/index.html
const scriptMatches = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert(scriptMatches.length > 0, "Could not find inline application script in website/index.html");
const appScript = scriptMatches[scriptMatches.length - 1][1];

// Build lightweight DOM mock to run the exact inline script from website/index.html
function makeMockEl(id = "") {
  const classes = new Set();
  return {
    id,
    value: "",
    textContent: "",
    innerHTML: "",
    placeholder: "",
    src: "",
    style: {},
    className: "",
    classList: {
      add: (...c) => c.forEach(x => classes.add(x)),
      remove: (...c) => c.forEach(x => classes.delete(x)),
      contains: c => classes.has(c)
    },
    listeners: {},
    addEventListener(ev, fn) {
      this.listeners[ev] = this.listeners[ev] || [];
      this.listeners[ev].push(fn);
    },
    getAttribute(attr) {
      return this[attr] || "detail";
    },
    querySelectorAll() {
      return [];
    },
    closest() {
      return null;
    },
    parentElement: { style: {} }
  };
}

const elements = {};
const store = {};
const sandbox = {
  window: {},
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; }
  },
  document: {
    documentElement: { lang: "en" },
    body: { appendChild() {}, removeChild() {} },
    getElementById(id) {
      if (!elements[id]) elements[id] = makeMockEl(id);
      return elements[id];
    },
    querySelectorAll() {
      return [makeMockEl("handle1")];
    },
    createElement() {
      return makeMockEl("a");
    }
  },
  Blob: class {},
  URL: { createObjectURL: () => "blob:mock", revokeObjectURL: () => {} },
  console
};
sandbox.window = sandbox;

vm.createContext(sandbox);
vm.runInContext(productsCode, sandbox);
vm.runInContext(appScript, sandbox);

// 1. Verify initial render populated all 93 products sorted by win-desc
assert.strictEqual(sandbox.CATALOG.products.length, 93);
assert(elements["grid"].innerHTML.includes("card"), "Grid should render product cards on boot");

// 2. Test norm() ZWJ stripping directly from window.norm
assert.strictEqual(sandbox.window.norm("ඩ්\u200Dරිල්"), sandbox.window.norm("ඩ්රිල්"));
assert.strictEqual(sandbox.window.norm("රූපලාවන්\u200Dය"), sandbox.window.norm("රූපලාවන්ය"));

// 3. Test openDetail() populates detail modal & 4-pillar SL-WPI breakdown
const firstProd = sandbox.CATALOG.products[0];
sandbox.window.openDetail(firstProd.id);
assert(elements["detail"].classList.contains("open"), "openDetail should open #detail modal");
assert.strictEqual(elements["dCodeBadge"].textContent, firstProd.code);
assert(elements["dWinPillars"].innerHTML.includes("pts"), "4-pillar SL-WPI breakdown must render in #dWinPillars");

// 4. Test adding products (including null-weight product prod_001 and weighted product prod_006)
sandbox.window.quickAdd("prod_001"); // GP-280 (has kg: null)
sandbox.window.quickAdd("prod_001"); // now 2 cartons
sandbox.window.updateCartItemQty("prod_006", 3); // JF-02 (has kg: 25.8, cbm: 0.118)

const totals = sandbox.window.computeCartTotals();
assert.strictEqual(totals.totalCartons, 5);
assert.strictEqual(totals.totalPcs, 2 * 60 + 3 * 40);
assert(!Number.isNaN(totals.totalKg) && totals.totalKg === 3 * 25.8, "Null weight must be safely handled without NaN");
assert(!Number.isNaN(totals.totalCbm) && Math.abs(totals.totalCbm - 3 * 0.118) < 1e-6, "CBM total must be accurate");

// 5. Test buildCartCSVString() UTF-8 BOM & exact column alignment
const csv = sandbox.window.buildCartCSVString();
assert(csv.startsWith("\uFEFF"), "CSV export must start with UTF-8 BOM (\\uFEFF)");
const csvLines = csv.slice(1).split("\r\n");
assert.strictEqual(csvLines.length, 4, "CSV should have 1 header + 2 item rows + 1 TOTAL row");

// Parse CSV columns accounting for quoted fields
function parseCsvRow(line) {
  const cols = [];
  let cur = "", inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') inQuotes = false;
      else cur += ch;
    } else {
      if (ch === '"') inQuotes = true;
      else if (ch === ",") { cols.push(cur); cur = ""; }
      else cur += ch;
    }
  }
  cols.push(cur);
  return cols;
}

const headerCols = parseCsvRow(csvLines[0]);
const item1Cols = parseCsvRow(csvLines[1]);
const item2Cols = parseCsvRow(csvLines[2]);
const totalCols = parseCsvRow(csvLines[3]);

assert.strictEqual(headerCols.length, 18, "CSV header must have 18 columns");
assert.strictEqual(item1Cols.length, headerCols.length, "CSV row 1 column count must match header");
assert.strictEqual(item2Cols.length, headerCols.length, "CSV row 2 column count must match header");
assert.strictEqual(totalCols.length, headerCols.length, "CSV TOTAL row column count must match header");
assert.strictEqual(totalCols[0], "TOTAL");
assert.strictEqual(totalCols[8], "5"); // Total Cartons column aligned

console.log("✅ [test_dom_sim.js] PASSED: Full VM execution of website/index.html, DOM simulation, cart math & CSV column alignment verified.");
