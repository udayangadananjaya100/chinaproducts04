/**
 * test_mobile.js — Automated Verification of Mobile UX & Ergonomics (320px-480px & <=640px)
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const html = fs.readFileSync(path.join(__dirname, "website", "index.html"), "utf8");

// 1. Viewport meta tag with viewport-fit=cover
assert(
  html.includes('<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'),
  "Missing required viewport meta tag with viewport-fit=cover"
);

// 2. Minimum 44x44px touch target dimensions on interactive controls
for (const sel of [".qadd", ".qty-step", ".gal-nav", ".rm", ".x", ".chip", "select"]) {
  assert(html.includes(sel), `Missing selector ${sel} in mobile touch target CSS`);
}
assert(html.includes("min-width: 44px") && html.includes("min-height: 44px"), "Missing 44x44px minimum touch target rule");

// 3. Enforce font-size: 16px on mobile <input> and <select> elements to prevent iOS Safari auto-zoom
assert(html.includes("font-size: 16px !important"), "Missing font-size: 16px rule for mobile inputs/selects");

// 4. 2-column mobile product card grid & 2-line clamped titles
assert(
  html.includes("grid-template-columns: repeat(2, minmax(0, 1fr))"),
  "Missing 2-column mobile product card grid rule"
);
assert(html.includes("-webkit-line-clamp: 2"), "Missing 2-line title clamp (-webkit-line-clamp: 2)");

// 5. Bottom Sheets on mobile (<= 640px) with .sheet-handle pull-down-to-dismiss touch gestures
assert(html.includes("@media (max-width: 640px)"), "Missing <= 640px mobile breakpoint");
assert(html.includes('class="sheet-handle" data-dismiss="detail"'), "Missing .sheet-handle on #detail");
assert(html.includes('class="sheet-handle" data-dismiss="cart"'), "Missing .sheet-handle on #cart");
assert(html.includes('class="sheet-handle" data-dismiss="ratesModal"'), "Missing .sheet-handle on #ratesModal");
assert(html.includes("touchstart") && html.includes("touchmove") && html.includes("touchend"), "Missing touch gesture listeners");

// 6. Product Detail Modal steppers, gallery arrows, swipe navigation exclusion & sticky bottom bar
assert(html.includes('id="dQtyMinus"') && html.includes('id="dQtyPlus"'), "Missing #dQtyMinus / #dQtyPlus steppers");
assert(html.includes('id="dPrevImg"') && html.includes('id="dNextImg"'), "Missing #dPrevImg / #dNextImg gallery arrows");
assert(html.includes('t.closest("#dThumbs")'), "Swipe listener must exclude #dThumbs");
assert(html.includes("detail-sticky-bar"), "Missing sticky bottom bar in detail modal");

// 7. Floating bottom Mobile Cart Bar (#mobileCartBar) and sticky quotation total summary (#cartStickyTotal)
assert(html.includes('id="mobileCartBar"'), "Missing #mobileCartBar");
assert(html.includes('id="cartStickyTotal"'), "Missing #cartStickyTotal");

console.log("✅ [test_mobile.js] PASSED: All Mobile UX & Ergonomics requirements verified.");
