// Market-status badges worked out in the browser from three things only:
//   the current wall-clock time, the quote's own timestamp, and the market's schedule (New York time).
// A quote is never called "MARKET CLOSED" just because it is old: while a market is open an old quote is STALE.
//
// Rules
//   Crypto (24/7):                 LIVE <= 2 min · DELAYED 2-15 min · STALE > 15 min
//   US stocks, ETFs, indices       regular session Mon-Fri 09:30-16:00 ET (13:00 on early-close days), NYSE holidays closed
//     regular session:             LIVE <= 5 min · DELAYED 5-20 min · STALE > 20 min or from before today's session
//     04:00-09:30 / close-20:00:   PRE-MARKET / AFTER-HOURS
//     otherwise:                   MARKET CLOSED
//   Commodities, metals, DXY       Sun 18:00 ET - Fri 17:00 ET with a daily 17:00-18:00 break
//     open:                        LIVE <= 5 min · DELAYED 5-20 min · STALE > 20 min
//     weekend / daily break:       MARKET CLOSED
//   Official slow series:          DAILY EOD (US Treasury, FRED daily) · WEEKLY (FRED) (published once a week)
// Used by the website (static-site.js), by every badge (common.js) and by tests/js/market-status.test.js (Node).
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MLWStatus = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const MIN = 60000;
  const CAL = { holidays: new Set(), early: new Set() };

  // NYSE holidays and early-close days come from the server (Python "holidays" package) in quotes.json.
  function setCalendar(c) {
    if (!c) return;
    CAL.holidays = new Set(c.nyse_holidays || []);
    CAL.early = new Set(c.nyse_early_close || []);
  }

  const FMT = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", weekday: "short", hourCycle: "h23",
  });
  const WD = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
  // New York calendar date (YYYY-MM-DD), weekday (Mon = 0) and minutes after midnight.
  function ny(ms) {
    const p = {};
    for (const x of FMT.formatToParts(new Date(ms))) p[x.type] = x.value;
    return { date: `${p.year}-${p.month}-${p.day}`, wd: WD[p.weekday], min: (Number(p.hour) % 24) * 60 + Number(p.minute) };
  }

  function isTradingDay(t) { return t.wd < 5 && !CAL.holidays.has(t.date); }
  function closeMin(t) { return CAL.early.has(t.date) ? 13 * 60 : 16 * 60; }

  // US stocks: "regular" | "pre" | "post" | "closed"
  function usPhase(ms) {
    const t = ny(ms);
    if (!isTradingDay(t)) return "closed";
    const close = closeMin(t);
    if (t.min >= 570 && t.min < close) return "regular";
    if (t.min >= 240 && t.min < 570) return "pre";
    if (t.min >= close && t.min < close + 240) return "post";
    return "closed";
  }

  function open24x5(ms, schedule) {
    const t = ny(ms);
    const md = t.date.slice(5);
    if (schedule === "spot_24_5" ? (md === "12-25" || md === "01-01") : CAL.holidays.has(t.date)) return false;
    if (t.wd === 5) return false;                  // Saturday
    if (t.wd === 6) return t.min >= 18 * 60;      // Sunday: opens 18:00
    if (t.wd === 4) return t.min < 17 * 60;       // Friday: closes 17:00
    return !(t.min >= 17 * 60 && t.min < 18 * 60); // daily break
  }

  function isOpen(schedule, ms) {
    if (schedule === "crypto_24_7") return true;
    if (schedule === "us_equity") return usPhase(ms) === "regular";
    if (schedule === "futures_24_5" || schedule === "spot_24_5") return open24x5(ms, schedule);
    return true;
  }

  // Start of the session that is open at ms (every session boundary falls on :00 or :30).
  function sessionStart(schedule, ms) {
    if (schedule === "crypto_24_7" || !isOpen(schedule, ms)) return null;
    let t = Math.floor(ms / (30 * MIN)) * 30 * MIN;
    for (let i = 0; i < 16 * 48; i++, t -= 30 * MIN) {
      if (isOpen(schedule, t) && !isOpen(schedule, t - MIN)) return t;
    }
    return null;
  }

  function ageText(sec) {
    sec = Math.max(0, Math.floor(sec));
    if (sec < 60) return sec + " s";
    if (sec < 3600) return Math.floor(sec / 60) + " min";
    if (sec < 86400) { const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60); return m ? `${h} h ${m} min` : `${h} h`; }
    return Math.floor(sec / 86400) + " d";
  }

  const R = (status, label, detail) => ({ status, label, detail });
  const KEEP = new Set(["UNAVAILABLE", "CONNECTING"]);

  // Returns {status, label, detail} or null when the quote has no price/time (keep what the server said).
  function compute(q, nowMs) {
    if (!q || KEEP.has(q.status) || q.price === null || q.price === undefined || !q.source_timestamp) return null;
    const now = nowMs === undefined ? Date.now() : nowMs;
    const ts = Date.parse(q.source_timestamp);
    if (isNaN(ts)) return null;
    const age = (now - ts) / 1000;
    const schedule = (q.market && q.market.schedule) || (q.asset_class === "crypto" ? "crypto_24_7" : "us_equity");

    if (q.kind === "daily") {
      const days = Math.floor((Date.parse(new Date(now).toISOString().slice(0, 10)) - Date.parse(q.source_timestamp.slice(0, 10))) / 86400000);
      const limit = q.max_age_days || 5;
      if (days > limit) return R("STALE", "Stale", `Official series; the newest value is ${days} days old`);
      if (q.cadence === "weekly" || (q.max_age_days || 0) >= 7) return R("WEEKLY", "Weekly", "Official series published once a week (one value per business day)");
      return R("DAILY_EOD", "Daily EOD", "Official end-of-day value (published after the close)");
    }

    const fresh = (liveMax, delayedMax, what) => {
      if (age <= liveMax) return R("LIVE", "Live", `${what}; the quote is ${ageText(age)} old`);
      if (age <= delayedMax) return R("DELAYED", "Delayed", `${what}; the quote is ${ageText(age)} old`);
      return R("STALE", "Stale", `${what}, but the newest quote is ${ageText(age)} old`);
    };

    if (schedule === "crypto_24_7") return fresh(2 * 60, 15 * 60, "Crypto trades 24/7");

    if (schedule === "us_equity") {
      const phase = usPhase(now);
      if (phase === "regular") {
        const start = sessionStart(schedule, now);
        if (start !== null && ts < start) return R("STALE", "Stale", `The US market is open, but this quote is from before today's session (${ageText(age)} old)`);
        return fresh(5 * 60, 20 * 60, "US market open");
      }
      if (phase === "pre") return R("PRE_MARKET", "Pre-market", "Pre-market (04:00-09:30 New York time); the price is the last regular-session price");
      if (phase === "post") return R("AFTER_HOURS", "After-hours", "After-hours (until 20:00 New York time); the price is the regular-session close");
      return R("MARKET_CLOSED", "Market closed", "US market closed (night, weekend or holiday); showing the last close");
    }

    if (isOpen(schedule, now)) return fresh(5 * 60, 20 * 60, "Market open");
    const t = ny(now);
    const why = t.wd === 5 || (t.wd === 6 && t.min < 18 * 60) || (t.wd === 4 && t.min >= 17 * 60) ? "weekend"
      : t.min >= 17 * 60 && t.min < 18 * 60 ? "daily 17:00-18:00 New York break" : "holiday";
    return R("MARKET_CLOSED", "Market closed", `Market closed (${why}); showing the last price before the close`);
  }

  // Badge text (without the age suffix the page adds for DELAYED/STALE).
  function badgeText(q) {
    switch (q.status) {
      case "LIVE": return "LIVE";
      case "DELAYED": return q.kind === "daily" ? "DAILY EOD" : "DELAYED";
      case "MARKET_CLOSED": return "MARKET CLOSED";
      case "PRE_MARKET": return "PRE-MARKET";
      case "AFTER_HOURS": return "AFTER-HOURS";
      case "DAILY_EOD": return "DAILY EOD";
      case "WEEKLY": return q.provider === "fred" ? "WEEKLY (FRED)" : "WEEKLY";
      case "STALE": return "STALE";
      case "ERROR": return "ERROR";
      case "UNAVAILABLE": return "N/A";
      default: return "CONNECTING…";
    }
  }

  return { compute, badgeText, setCalendar, usPhase, isOpen, sessionStart };
});
