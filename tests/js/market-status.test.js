// Unit tests for the browser's market-status badges (static/js/market-status.js).
// Run:  node --test tests/js/market-status.test.js   (Node 18+; also run by tests/test_js.py inside pytest)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../../static/js/market-status.js");

// The calendar the website gets from the server (Python "holidays" package) in quotes.json.
S.setCalendar({ nyse_holidays: ["2026-11-26", "2026-12-25"], nyse_early_close: ["2026-11-27", "2026-12-24"] });

const at = (iso) => Date.parse(iso);
const MON_MIDDAY = at("2026-11-23T17:53:00Z");        // Mon 23 Nov 2026 12:53 PM New York (EST, UTC-5)
const FRI_CLOSE = "2026-11-20T21:00:00Z";             // Fri 20 Nov 2026 4:00 PM New York
const SAT_NOON = at("2026-11-21T17:00:00Z");          // Sat 21 Nov 2026 12:00 PM New York
const stock = (ts, extra) => Object.assign({ symbol: "NVDA", asset_class: "stock", price: 163.76, kind: "poll",
  source_timestamp: ts, market: { schedule: "us_equity" } }, extra);

test("(a) open US session + fresh quote -> LIVE", () => {
  const r = S.compute(stock("2026-11-23T17:51:00Z"), MON_MIDDAY);  // 2 min old
  assert.equal(r.status, "LIVE");
});

test("(b) open US session + Friday's quote -> STALE, never MARKET CLOSED", () => {
  const r = S.compute(stock(FRI_CLOSE, { price: 125.53 }), MON_MIDDAY);
  assert.equal(r.status, "STALE");
  assert.match(r.detail, /before today's session/);
});

test("(c) weekend + Friday's quote -> MARKET CLOSED", () => {
  assert.equal(S.compute(stock(FRI_CLOSE), SAT_NOON).status, "MARKET_CLOSED");
});

test("(d) FRED weekly series -> WEEKLY (FRED)", () => {
  const q = { symbol: "USDBROAD", price: 122.65, kind: "daily", cadence: "weekly", max_age_days: 10, provider: "fred",
    source_timestamp: "2026-11-13T00:00:00+00:00", market: { schedule: "us_equity" } };
  const r = S.compute(q, MON_MIDDAY);
  assert.equal(r.status, "WEEKLY");
  assert.equal(S.badgeText(Object.assign({}, q, r)), "WEEKLY (FRED)");
});

test("US Treasury end-of-day value -> DAILY EOD", () => {
  const q = { symbol: "US10Y", price: 4.92, kind: "daily", cadence: "daily", provider: "treasury",
    source_timestamp: "2026-11-20T00:00:00+00:00", market: { schedule: "us_equity" } };
  const r = S.compute(q, MON_MIDDAY);
  assert.equal(r.status, "DAILY_EOD");
  assert.equal(S.badgeText(Object.assign({}, q, r)), "DAILY EOD");
});

test("US session thresholds: <=5 min LIVE, 5-20 DELAYED, >20 STALE", () => {
  assert.equal(S.compute(stock("2026-11-23T17:48:00Z"), MON_MIDDAY).status, "LIVE");     // 5 min
  assert.equal(S.compute(stock("2026-11-23T17:40:00Z"), MON_MIDDAY).status, "DELAYED");  // 13 min
  assert.equal(S.compute(stock("2026-11-23T17:30:00Z"), MON_MIDDAY).status, "STALE");    // 23 min
});

test("pre-market and after-hours, holidays and early closes", () => {
  assert.equal(S.compute(stock(FRI_CLOSE), at("2026-11-23T12:00:00Z")).status, "PRE_MARKET");   // 7:00 AM NY
  assert.equal(S.compute(stock("2026-11-23T21:00:00Z"), at("2026-11-23T22:00:00Z")).status, "AFTER_HOURS"); // 5 PM
  assert.equal(S.compute(stock(FRI_CLOSE), at("2026-11-24T02:00:00Z")).status, "MARKET_CLOSED"); // 9 PM NY
  assert.equal(S.compute(stock(FRI_CLOSE), at("2026-11-26T17:00:00Z")).status, "MARKET_CLOSED"); // Thanksgiving
  assert.equal(S.usPhase(at("2026-11-27T18:30:00Z")), "post");     // 1:30 PM on the early-close Friday
  assert.equal(S.usPhase(at("2026-11-27T17:30:00Z")), "regular");  // 12:30 PM
});

test("crypto: <=2 min LIVE, 2-15 DELAYED, >15 STALE, any day", () => {
  const c = (ts) => ({ symbol: "BTC", asset_class: "crypto", price: 61758, kind: "stream", source_timestamp: ts, market: { schedule: "crypto_24_7" } });
  assert.equal(S.compute(c("2026-11-21T16:59:00Z"), SAT_NOON).status, "LIVE");
  assert.equal(S.compute(c("2026-11-21T16:50:00Z"), SAT_NOON).status, "DELAYED");
  assert.equal(S.compute(c("2026-11-21T16:40:00Z"), SAT_NOON).status, "STALE");
});

test("commodities and DXY: open Sun 18:00 - Fri 17:00 ET with a daily break", () => {
  const f = (ts, sched) => ({ symbol: "WTI", asset_class: "energy", price: 80.85, kind: "poll", source_timestamp: ts, market: { schedule: sched || "futures_24_5" } });
  assert.equal(S.compute(f("2026-11-23T17:50:00Z"), MON_MIDDAY).status, "LIVE");
  assert.equal(S.compute(f("2026-11-23T17:40:00Z"), MON_MIDDAY).status, "DELAYED");
  assert.equal(S.compute(f("2026-11-23T17:00:00Z"), MON_MIDDAY).status, "STALE");
  assert.equal(S.compute(f("2026-11-20T21:59:00Z"), SAT_NOON).status, "MARKET_CLOSED");                  // weekend
  assert.equal(S.compute(f("2026-11-23T21:59:00Z"), at("2026-11-23T22:30:00Z")).status, "MARKET_CLOSED"); // 17:30 break
  assert.equal(S.compute(f("2026-11-22T23:05:00Z"), at("2026-11-22T23:10:00Z")).status, "LIVE");          // Sun 18:10 open
  assert.equal(S.compute(f("2026-11-26T15:00:00Z", "spot_24_5"), at("2026-11-26T15:02:00Z")).status, "LIVE"); // gold trades on Thanksgiving
});

test("no price or unavailable: keep what the server said", () => {
  assert.equal(S.compute({ status: "UNAVAILABLE", price: null }, MON_MIDDAY), null);
  assert.equal(S.compute(stock(null), MON_MIDDAY), null);
});
