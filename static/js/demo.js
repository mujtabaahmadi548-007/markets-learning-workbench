// Demo investing: invest any pretend amount (up to $1,000,000,000 per investment) at the REAL live price and
// watch it move in real time. Orders fill only at a fresh real price (LIVE or DELAYED). Saved in this browser
// (localStorage) - the same on the website and in the app on your PC. Not real money, not advice.
(function () {
  "use strict";
  const M = window.MLW;
  const KEY = "mlw-demo-v2";
  const MAX_AMT = 1e9, MIN_AMT = 1;
  const FILLABLE = ["LIVE", "DELAYED"];
  const CLASS_NAMES = { crypto: "Crypto", metal: "Precious metals", energy: "Energy", stock: "Stocks", etf: "ETFs" };
  const TICK_MIN_GAP = 2, TICKS_MAX = 6000;
  const $ = (id) => document.getElementById(id);
  const nowSec = () => Math.floor(Date.now() / 1000);

  const quotes = new Map();   // symbol -> newest quote view
  const candles = new Map();  // "SYM|RANGE" -> {at, data}
  let acct = load();
  let selected = acct.open[0] ? acct.open[0].id : null;
  let dirty = true, saveDue = false;

  try { localStorage.removeItem("mlw-demo-v1"); } catch (e) { /* old format from the first version */ }

  // ---------------------------------------------------------------- storage
  function blank() { return { v: 2, open: [], closed: [] }; }
  function load() {
    try {
      const a = JSON.parse(localStorage.getItem(KEY) || "null");
      return a && a.v === 2 && Array.isArray(a.open) && Array.isArray(a.closed) ? a : blank();
    } catch (e) { return blank(); }
  }
  function save() {
    saveDue = false;
    try { localStorage.setItem(KEY, JSON.stringify(acct)); }
    catch (e) {  // storage full: thin out the oldest recorded ticks and try once more
      acct.open.forEach((p) => { p.ticks = p.ticks.filter((_, i) => i % 2 === 0); });
      try { localStorage.setItem(KEY, JSON.stringify(acct)); } catch (e2) { M.toast("Could not save the demo in this browser: " + M.esc(e2.message), "toast-err"); }
    }
  }

  // ---------------------------------------------------------------- helpers
  function parseAmount(s) {
    s = String(s || "").trim().toLowerCase().replace(/[$,\s_]/g, "").replace(/usd$/, "");
    const m = s.match(/^(\d*\.?\d+)([kmb])?$/);
    return m ? parseFloat(m[1]) * ({ k: 1e3, m: 1e6, b: 1e9 }[m[2]] || 1) : NaN;
  }
  const money = (v) => (v === null || v === undefined || !isFinite(v)) ? "—" : (v < 0 ? "−$" : "$") + M.fmtNum(Math.abs(v), 2);
  const signedMoney = (v) => (v > 0.004 ? "+" : "") + money(v);
  function pct(v) {
    if (v === null || !isFinite(v)) return "—";
    const d = Math.abs(v) < 0.1 ? 3 : 2, shown = Number(Math.abs(v).toFixed(d));
    return (shown === 0 ? "" : v > 0 ? "+" : "−") + M.fmtNum(Math.abs(v), d) + "%";
  }
  const cls = (v) => v > 0.004 ? "up" : v < -0.004 ? "down" : "flat";
  const arrow = (v) => v > 0.004 ? "▲" : v < -0.004 ? "▼" : "■";
  const qtyText = (v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: v >= 1000 ? 4 : 8 });
  const tradable = (q) => q && q.kind !== "daily" && q.currency !== "%" && q.status !== "UNAVAILABLE" && CLASS_NAMES[q.asset_class];
  const fillable = (q) => q && FILLABLE.includes(q.status) && q.price > 0;
  function px(sym, decimals) { return M.fmtNum(sym, decimals); }
  function durationText(sec) {
    sec = Math.max(0, Math.floor(sec));
    const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
    if (d) return `${d} d ${h} h`;
    if (h) return `${h} h ${m} min`;
    if (m) return `${m} min ${s} s`;
    return `${s} s`;
  }
  function whyNotFillable(q) {
    if (!q || q.price === null || q.price === undefined) return "No real price for this asset yet - wait a moment.";
    if (q.status === "MARKET_CLOSED") {
      const opens = q.market && q.market.next_open ? " It opens " + M.dayJakarta(new Date(q.market.next_open)) + " (Jakarta time)." : "";
      return `The ${q.symbol} market is closed, so a demo order cannot fill now.${opens}`;
    }
    if (q.status === "STALE") return `The newest ${q.symbol} price is too old (STALE), so it is not used for orders.`;
    return `${q.symbol} has no usable price right now (${q.status_label || q.status}).`;
  }
  const findPos = (id) => acct.open.find((p) => p.id === id);
  const priceNow = (p) => { const q = quotes.get(p.sym); return q && q.price > 0 ? q.price : null; };
  // Quantity and amount invested at a moment (positions can be added to or partly sold).
  function stateAt(p, t) {
    let st = p.events[0];
    for (const e of p.events) { if (e.t <= t) st = e; else break; }
    return st;
  }

  // ---------------------------------------------------------------- orders
  function open(sym, amount, tpPct, slPct) {
    const q = quotes.get(sym);
    if (!fillable(q)) return M.toast(M.esc(whyNotFillable(q)), "toast-err");
    if (!(amount >= MIN_AMT && amount <= MAX_AMT)) return M.toast("Enter an amount from $1 to $1,000,000,000.", "toast-err");
    const t = nowSec(), price = q.price, qty = amount / price;
    const p = { id: "p" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), sym, name: q.name,
      unit: M.unitOf(q), decimals: q.decimals, opened: t, entry: price, qty, invested: amount,
      entry_source: q.source, entry_status: q.status, entry_price_time: q.source_timestamp,
      tp: tpPct > 0 ? price * (1 + tpPct / 100) : null, sl: slPct > 0 && slPct < 100 ? price * (1 - slPct / 100) : null,
      hi: price, lo: price, events: [{ t, qty, invested: amount, kind: "buy", price, amount }], ticks: [[t, price]] };
    acct.open.unshift(p);
    selected = p.id;
    save(); dirty = true;
    M.toast(`<strong>Demo investment opened</strong><br>${money(amount)} in ${M.esc(sym)}: ${M.esc(qtyText(qty))} at ${M.esc(px(price, q.decimals))} ${M.esc(p.unit)} (${M.esc(q.status)}, ${M.esc(q.source || "")}).`);
    render(true);
  }

  function addMore(p, amount) {
    const q = quotes.get(p.sym);
    if (!fillable(q)) return M.toast(M.esc(whyNotFillable(q)), "toast-err");
    if (!(amount >= MIN_AMT)) return M.toast("Enter an amount to add.", "toast-err");
    if (p.invested + amount > MAX_AMT) return M.toast(`One investment can be at most $1,000,000,000 (this one has ${money(p.invested)}).`, "toast-err");
    const t = nowSec(), add = amount / q.price;
    p.qty += add; p.invested += amount; p.entry = p.invested / p.qty;
    p.events.push({ t, qty: p.qty, invested: p.invested, kind: "add", price: q.price, amount });
    save(); dirty = true;
    M.toast(`Added ${money(amount)} to ${M.esc(p.sym)} at ${M.esc(px(q.price, p.decimals))}. New average buy price: ${M.esc(px(p.entry, p.decimals))}.`);
    render(true);
  }

  function close(p, fraction, reason) {
    const q = quotes.get(p.sym);
    if (!fillable(q)) { if (reason === "manual") M.toast(M.esc(whyNotFillable(q)), "toast-err"); return false; }
    const t = nowSec(), price = q.price;
    const qty = fraction >= 1 ? p.qty : p.qty * fraction;
    const cost = p.invested * (qty / p.qty), value = qty * price, pl = value - cost;
    acct.closed.unshift({ sym: p.sym, name: p.name, unit: p.unit, decimals: p.decimals, qty, entry: p.entry, exit: price,
      invested: cost, value, pl, opened: p.opened, closed: t, reason, exit_source: q.source, exit_status: q.status, part: fraction < 1 });
    if (acct.closed.length > 500) acct.closed.length = 500;
    if (fraction >= 1) {
      acct.open = acct.open.filter((x) => x.id !== p.id);
      if (selected === p.id) selected = acct.open[0] ? acct.open[0].id : null;
    } else {
      p.qty -= qty; p.invested -= cost;
      p.events.push({ t, qty: p.qty, invested: p.invested, kind: "sell", price, amount: value });
    }
    save(); dirty = true;
    const why = { manual: "", "take-profit": "Take-profit reached. ", "stop-loss": "Stop-loss reached. " }[reason];
    M.toast(`<strong>Demo investment ${fraction >= 1 ? "closed" : "partly sold"}</strong><br>${why}${M.esc(qtyText(qty))} ${M.esc(p.sym)} sold at ${M.esc(px(price, p.decimals))} = ${money(value)} · profit/loss <span class="${cls(pl)}">${signedMoney(pl)}</span>.`,
      reason === "manual" ? "" : "toast-alert");
    render(true);
    return true;
  }

  // ---------------------------------------------------------------- live prices
  function onQuote(q) {
    quotes.set(q.symbol, q);
    if (!(q.price > 0) || !FILLABLE.includes(q.status)) return;
    const t = q.source_timestamp ? Math.floor(Date.parse(q.source_timestamp) / 1000) : nowSec();
    for (const p of acct.open.filter((x) => x.sym === q.symbol)) {
      if (t < p.opened) continue;
      const last = p.ticks[p.ticks.length - 1];
      if (!last || (t - last[0] >= TICK_MIN_GAP && q.price !== last[1]) || t - last[0] >= 60) {
        if (last && t <= last[0]) continue;
        p.ticks.push([t, q.price]);
        if (p.ticks.length > TICKS_MAX) p.ticks.splice(0, p.ticks.length - TICKS_MAX);
        saveDue = true;
        if (p.id === selected) liveChartUpdate(p, t, q.price);
      }
      p.hi = Math.max(p.hi, q.price); p.lo = Math.min(p.lo, q.price);
      // Automatic take-profit / stop-loss (only on a fresh real price).
      if (p.tp && q.price >= p.tp) close(p, 1, "take-profit");
      else if (p.sl && q.price <= p.sl) close(p, 1, "stop-loss");
    }
  }

  // ---------------------------------------------------------------- chart
  let chart = null, series = null, chartKey = "", chartPos = null, lines = [];
  const JKT_TIME = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Jakarta", hour: "numeric", minute: "2-digit", hour12: true });
  const JKT_TIME_S = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Jakarta", hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true });
  const JKT_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", day: "numeric", month: "short" });
  // Axis labels for dollar values: compact but precise enough to see small moves on a huge investment.
  function axisMoney(v) {
    const a = Math.abs(v), s = v < 0 ? "−$" : "$";
    if (a >= 1e9) return s + (a / 1e9).toFixed(6) + "B";
    if (a >= 1e6) return s + (a / 1e6).toFixed(4) + "M";
    return s + M.fmtNum(a, a >= 1e4 ? 0 : 2);
  }
  const range = () => document.querySelector('input[name="range"]:checked').value;
  const cmode = () => document.querySelector('input[name="cmode"]:checked').value;

  function ensureChart() {
    if (chart) return;
    chart = LightweightCharts.createChart($("d-chart"), {
      autoSize: true,
      layout: { background: { type: "solid", color: "#FFFFFF" }, textColor: "#33445C", fontFamily: "Inter, Segoe UI, system-ui, sans-serif" },
      grid: { vertLines: { color: "#EEF2F8" }, horzLines: { color: "#EEF2F8" } },
      rightPriceScale: { borderColor: "#E3E9F2" },
      timeScale: { borderColor: "#E3E9F2", timeVisible: true, secondsVisible: true, rightOffset: 4,
        tickMarkFormatter: (t, type) => (type === 4 ? JKT_TIME_S : type === 3 ? JKT_TIME : JKT_DAY).format(new Date(t * 1000)) },
      localization: {
        timeFormatter: (t) => JKT_DAY.format(new Date(t * 1000)) + ", " + JKT_TIME_S.format(new Date(t * 1000)),
        priceFormatter: (v) => cmode() === "price" && chartPos ? M.fmtNum(v, chartPos.decimals) : axisMoney(v),
      },
      crosshair: { mode: 0 },
    });
  }

  function candleRangeFor(p, r) {
    if (r === "1D" || r === "1W" || r === "1M") return r;
    if (r === "live") return null;
    const held = nowSec() - p.opened;
    return held < 86400 ? "1D" : held < 7 * 86400 ? "1W" : held < 30 * 86400 ? "1M" : "6M";
  }
  async function loadCandles(sym, r) {
    const key = sym + "|" + r, c = candles.get(key);
    if (c && Date.now() - c.at < 5 * 60 * 1000) return c.data;
    try {
      const d = await M.api(`/api/candles/${encodeURIComponent(sym)}?range=${r}`);
      // Never mix in a stand-in (proxy) instrument: its price is not the asset's price.
      const ok = d && !d.is_proxy && !d.close_only && Array.isArray(d.candles) ? d : { candles: [], skipped: d && d.is_proxy ? "proxy" : "none" };
      candles.set(key, { at: Date.now(), data: ok });
      return ok;
    } catch (e) { candles.set(key, { at: Date.now(), data: { candles: [] } }); return { candles: [] }; }
  }

  function toY(p, t, price) {
    const m = cmode();
    if (m === "price") return price;
    const st = stateAt(p, t);
    return m === "value" ? st.qty * price : st.qty * price - st.invested;
  }

  function points(p, cd) {
    const r = range(), m = cmode(), now = nowSec();
    let start = { live: now - 900, since: p.opened, "1D": now - 86400, "1W": now - 7 * 86400, "1M": now - 30 * 86400 }[r];
    if (m !== "price") start = Math.max(start, p.opened);
    const map = new Map();
    if (r !== "live" && cd && cd.candles) {
      for (const c of cd.candles) if (c.time >= start && c.close > 0) map.set(c.time, c.close);
    }
    if (p.opened >= start) map.set(p.opened, p.events[0].price);
    for (const [t, v] of p.ticks) if (t >= start) map.set(t, v);
    const pn = priceNow(p);
    if (pn !== null) map.set(Math.max(now, ...map.keys()), pn);
    return [...map.entries()].filter(([t]) => m === "price" || t >= p.opened).sort((a, b) => a[0] - b[0])
      .map(([t, v]) => ({ time: t, value: toY(p, t, v) }));
  }

  async function drawChart(force) {
    const p = findPos(selected);
    if (!p) return;
    const r = range(), m = cmode(), key = `${p.id}|${r}|${m}`;
    if (!force && key === chartKey) return;
    chartKey = key; chartPos = p;
    ensureChart();
    const cr = candleRangeFor(p, r);
    const cd = cr ? await loadCandles(p.sym, cr) : null;
    if (chartKey !== key) return; // the user switched while loading
    if (series) { chart.removeSeries(series); series = null; lines = []; }
    const pts = points(p, cd);
    const first = pts.length ? pts[0].value : 0, last = pts.length ? pts[pts.length - 1].value : 0;
    const base = m === "price" ? p.entry : m === "value" ? stateAt(p, nowSec()).invested : 0;
    const up = last >= base;
    series = chart.addBaselineSeries({
      baseValue: { type: "price", price: base },
      topLineColor: "#16A34A", topFillColor1: "rgba(22,163,74,0.22)", topFillColor2: "rgba(22,163,74,0.02)",
      bottomLineColor: "#DC2626", bottomFillColor1: "rgba(220,38,38,0.02)", bottomFillColor2: "rgba(220,38,38,0.22)",
      lineWidth: 2, priceLineVisible: true, lastValueVisible: true,
    });
    series.setData(pts);
    lines.push(series.createPriceLine({ price: base, color: "#64748B", lineWidth: 1, lineStyle: 2, axisLabelVisible: true,
      title: m === "price" ? "buy price" : m === "value" ? "invested" : "break-even" }));
    if (p.tp) lines.push(series.createPriceLine({ price: toY(p, nowSec(), p.tp), color: "#16A34A", lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: "take profit" }));
    if (p.sl) lines.push(series.createPriceLine({ price: toY(p, nowSec(), p.sl), color: "#DC2626", lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: "stop loss" }));
    const marks = p.events.filter((e) => pts.length && e.t >= pts[0].time).map((e) => ({ time: pts.reduce((b, x) => (x.time <= e.t ? x.time : b), pts[0].time),
      position: e.kind === "sell" ? "aboveBar" : "belowBar", color: e.kind === "sell" ? "#DC2626" : "#1D4ED8",
      shape: e.kind === "sell" ? "arrowDown" : "arrowUp", text: e.kind === "buy" ? "Buy" : e.kind === "add" ? "Add" : "Sell" }));
    try { series.setMarkers(marks); } catch (e) { /* markers are optional */ }
    chart.timeScale().fitContent();
    const src = cd && cd.candles && cd.candles.length ? `${cd.source || "chart source"} ${({ "5m": "5-minute", "1h": "1-hour", "1d": "daily" })[cd.timeframe] || ""} candles + ` : "";
    $("d-chart-note").textContent = `Real prices only: ${src}live prices recorded while this page is open. ` +
      (cd && cd.skipped === "proxy" ? "This asset's chart uses a stand-in instrument, so only recorded live prices are shown. " : "") +
      `Green above / red below the ${m === "price" ? "buy price" : m === "value" ? "amount invested" : "break-even line"}. Times are Jakarta time.` +
      (first === last && pts.length < 3 ? " The line grows as new prices arrive." : "");
    void up;
  }

  function liveChartUpdate(p, t, price) {
    if (!series || chartPos !== p || p.id !== selected) return;
    try { series.update({ time: Math.max(t, nowSec()), value: toY(p, t, price) }); } catch (e) { chartKey = ""; }
    if (range() === "live") chart.timeScale().scrollToRealTime();
  }

  // ---------------------------------------------------------------- rendering
  function fillSelect() {
    const sel = $("o-sym");
    const list = [...quotes.values()].filter(tradable);
    const sig = list.map((q) => q.symbol).sort().join(",");
    if (sel.dataset.sig === sig) return;
    sel.dataset.sig = sig;
    const keep = sel.value || new URLSearchParams(location.search).get("symbol") || "BTC";
    const groups = {};
    list.sort((a, b) => a.id - b.id).forEach((q) => { (groups[q.asset_class] = groups[q.asset_class] || []).push(q); });
    sel.innerHTML = Object.keys(CLASS_NAMES).filter((c) => groups[c]).map((c) =>
      `<optgroup label="${CLASS_NAMES[c]}">${groups[c].map((q) => `<option value="${M.esc(q.symbol)}">${M.esc(q.symbol)} — ${M.esc(q.name)}</option>`).join("")}</optgroup>`).join("");
    if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
  }

  function badgeHtml(q) { const b = M.badge(q); return `<span class="badge ${b.cls}" title="${M.esc(b.title)}">${M.esc(b.text)}</span>`; }

  function renderOrder() {
    const q = quotes.get($("o-sym").value);
    $("o-quote").innerHTML = q && q.price !== null && q.price !== undefined
      ? `Price now: <strong class="num">${M.esc(M.fmtPrice(q))} ${M.esc(M.unitOf(q))}</strong> ${badgeHtml(q)} · ${M.esc(q.source || "")} · ${M.esc(M.updatedText(q).text)}`
      : "Waiting for a real price…";
    const amt = parseAmount($("o-amt").value), pv = $("o-preview"), btn = $("o-submit");
    let msg = "", ok = false;
    if (!q) msg = "Choose an asset.";
    else if (!fillable(q)) msg = whyNotFillable(q);
    else if (!(amt > 0)) msg = "Enter how much to invest, or tap an amount.";
    else if (amt > MAX_AMT) msg = "The most you can invest at once is $1,000,000,000.";
    else if (amt < MIN_AMT) msg = "The smallest demo investment is $1.";
    else {
      ok = true;
      const tp = parseFloat($("o-tp").value), sl = parseFloat($("o-sl").value);
      msg = `You get <strong>${M.esc(qtyText(amt / q.price))} ${M.esc(q.symbol)}</strong> at <span class="num">${M.esc(M.fmtPrice(q))}</span> for <strong>${money(amt)}</strong>.` +
        ` Every 1% the price moves = ${money(amt / 100)}.` +
        (tp > 0 ? ` Take profit at ${M.esc(px(q.price * (1 + tp / 100), q.decimals))} (+${money(amt * tp / 100)}).` : "") +
        (sl > 0 && sl < 100 ? ` Stop loss at ${M.esc(px(q.price * (1 - sl / 100), q.decimals))} (−${money(amt * sl / 100)}).` : "");
    }
    pv.className = "demo-preview" + (!ok && (amt > 0 || (q && !fillable(q))) ? " bad" : "");
    pv.innerHTML = ok ? msg : M.esc(msg);
    btn.disabled = !ok;
    btn.textContent = ok ? `Invest ${money(amt)} in ${q.symbol} (demo)` : "Invest (demo)";
  }

  function totals() {
    let value = 0, inv = 0, missing = 0;
    for (const p of acct.open) { const pn = priceNow(p); inv += p.invested; if (pn === null) missing++; else value += p.qty * pn; }
    const closedPl = acct.closed.reduce((s, c) => s + c.pl, 0);
    return { value, inv, missing, closedPl };
  }

  function renderKpis() {
    const t = totals(), n = acct.open.length;
    $("k-value").textContent = t.missing ? "…" : money(t.value);
    $("k-value-sub").textContent = n ? `${n} open investment${n === 1 ? "" : "s"}${t.missing ? " · waiting for prices" : ""}` : "no open investments";
    $("k-inv").textContent = money(t.inv);
    $("k-inv-sub").textContent = n ? "in open investments" : "—";
    const openPl = t.missing ? null : t.value - t.inv;
    $("k-open").innerHTML = openPl === null ? "…" : `<span class="${cls(openPl)}">${arrow(openPl)} ${signedMoney(openPl)}</span>`;
    $("k-open-sub").textContent = openPl === null || !t.inv ? "—" : pct(openPl / t.inv * 100) + " on the amount invested";
    $("k-closed").innerHTML = `<span class="${cls(t.closedPl)}">${signedMoney(t.closedPl)}</span>`;
    $("k-closed-sub").textContent = acct.closed.length ? `${acct.closed.length} closed (incl. partial sales)` : "nothing closed yet";
  }

  function renderList() {
    const box = $("pos-list");
    if (!acct.open.length) { box.innerHTML = '<p class="muted small">None yet. Invest in something on the left — for example $1M in Bitcoin.</p>'; return; }
    box.innerHTML = acct.open.map((p) => {
      const q = quotes.get(p.sym), pn = priceNow(p);
      const value = pn === null ? null : p.qty * pn, pl = value === null ? null : value - p.invested;
      return `<button type="button" class="demo-pos${p.id === selected ? " sel" : ""}" data-id="${p.id}">
        <span class="dp-sym"><strong>${M.esc(p.sym)}</strong><span class="muted small">${money(p.invested)} invested</span></span>
        <span class="dp-val num">${money(value)}<span class="small ${pl === null ? "" : cls(pl)}">${pl === null ? "…" : `${arrow(pl)} ${signedMoney(pl)} (${pct(pl / p.invested * 100)})`}</span></span>
        <span class="dp-badge">${q ? badgeHtml(q) : ""}</span></button>`;
    }).join("");
  }

  function renderDetail() {
    const p = findPos(selected), box = $("detail");
    box.hidden = !p;
    if (!p) return;
    const q = quotes.get(p.sym), pn = priceNow(p), now = nowSec();
    const value = pn === null ? null : p.qty * pn, pl = value === null ? null : value - p.invested;
    $("d-title").innerHTML = `${M.esc(p.sym)} <span class="muted small">${M.esc(p.name || "")}</span> <a class="small" href="${M.base}/asset/${encodeURIComponent(p.sym)}">asset page →</a>`;
    $("d-sub").innerHTML = `Bought ${M.esc(M.formatJakarta(new Date(p.opened * 1000)))} · held ${durationText(now - p.opened)}` +
      (q ? ` · price now ${M.esc(M.fmtPrice(q))} ${M.esc(p.unit)} ${badgeHtml(q)} ${M.esc(q.source || "")}, ${M.esc(M.updatedText(q).text)}` : "");
    const ve = $("d-value"), prev = Number(ve.dataset.v);
    ve.textContent = money(value);
    if (value !== null && isFinite(prev) && prev !== value) {
      ve.classList.remove("flash-up", "flash-down"); void ve.offsetWidth; ve.classList.add(value > prev ? "flash-up" : "flash-down");
    }
    ve.dataset.v = value === null ? "" : value;
    $("d-pl").className = "num demo-big-pl " + (pl === null ? "" : cls(pl));
    $("d-pl").textContent = pl === null ? "waiting for a price" : `${arrow(pl)} ${signedMoney(pl)} (${pct(pl / p.invested * 100)})`;

    const priceCh = pn === null ? null : (pn / p.entry - 1) * 100;
    const hiV = p.qty * p.hi, loV = p.qty * p.lo;
    const st = [
      ["Amount invested", money(p.invested)],
      ["Value now", money(value)],
      ["Profit / loss", pl === null ? "—" : `<span class="${cls(pl)}">${signedMoney(pl)} (${pct(pl / p.invested * 100)})</span>`],
      ["Quantity", `${qtyText(p.qty)} ${M.esc(p.sym)}`],
      [p.events.length > 1 ? "Average buy price" : "Buy price", `${px(p.entry, p.decimals)} ${M.esc(p.unit)}`],
      ["Price now", pn === null ? "—" : `${px(pn, p.decimals)} ${M.esc(p.unit)} <span class="${cls(priceCh)}">(${pct(priceCh)})</span>`],
      ["Every 1% move", money(value === null ? p.invested / 100 : value / 100)],
      ["Highest since buy", `${px(p.hi, p.decimals)} → value ${money(hiV)} <span class="up">(${signedMoney(hiV - p.invested)})</span>`],
      ["Lowest since buy", `${px(p.lo, p.decimals)} → value ${money(loV)} <span class="${cls(loV - p.invested)}">(${signedMoney(loV - p.invested)})</span>`],
      ["Asset today", q && q.change_pct !== null && q.change_pct !== undefined ? `<span class="${cls(q.change_pct)}">${pct(q.change_pct)}</span> <span class="muted">${M.esc(M.fmtChange(q).basis)}</span>` : "—"],
      ["Bought at", `${M.esc(M.formatJakarta(new Date(p.opened * 1000)))} · ${M.esc(p.entry_source || "")} (${M.esc(p.entry_status || "")})`],
      ["Time held", durationText(now - p.opened)],
      ["Take profit", p.tp ? `${px(p.tp, p.decimals)} (${pct((p.tp / p.entry - 1) * 100)})${pn ? ` · ${pct((p.tp / pn - 1) * 100)} away` : ""}` : "not set"],
      ["Stop loss", p.sl ? `${px(p.sl, p.decimals)} (${pct((p.sl / p.entry - 1) * 100)})${pn ? ` · ${pct((p.sl / pn - 1) * 100)} away` : ""}` : "not set"],
      ["Orders", p.events.map((e) => `${e.kind === "buy" ? "Buy" : e.kind === "add" ? "Add" : "Sell"} ${money(e.amount)} @ ${px(e.price, p.decimals)}`).join(" · ")],
    ];
    $("d-stats").innerHTML = st.map(([k, v]) => `<div class="ds"><span class="ds-k">${k}</span><span class="ds-v num">${v}</span></div>`).join("");
    $("d-tpsl-now").textContent = `Now: take profit ${p.tp ? px(p.tp, p.decimals) : "off"}, stop loss ${p.sl ? px(p.sl, p.decimals) : "off"}. Leave a box empty to switch it off.`;
    box.querySelectorAll("[data-close]").forEach((b) => { b.disabled = !fillable(q); b.title = fillable(q) ? "" : whyNotFillable(q); });
  }

  function renderClosed() {
    $("closed-card").hidden = !acct.closed.length;
    $("closed-body").innerHTML = acct.closed.slice(0, 200).map((c) => `<tr>
      <td title="${M.esc(M.utcLabel(new Date(c.closed * 1000)))}">${M.esc(M.shortJakarta(new Date(c.closed * 1000)))}</td>
      <td><strong>${M.esc(c.sym)}</strong>${c.part ? ' <span class="chip">part</span>' : ""}</td>
      <td class="r num">${money(c.invested)}</td>
      <td class="r num">${M.esc(px(c.entry, c.decimals))}</td>
      <td class="r num">${M.esc(px(c.exit, c.decimals))}</td>
      <td class="r num">${money(c.value)}</td>
      <td class="r num ${cls(c.pl)}">${signedMoney(c.pl)} (${pct(c.pl / c.invested * 100)})</td>
      <td class="hide-sm small muted">${M.esc({ manual: "you closed it", "take-profit": "take-profit", "stop-loss": "stop-loss" }[c.reason] || c.reason)} · ${M.esc(c.exit_source || "")} ${M.esc(c.exit_status || "")}</td></tr>`).join("");
  }

  function render(force) {
    fillSelect();
    renderOrder();
    if (!dirty && !force) return;
    dirty = false;
    renderKpis(); renderList(); renderDetail(); renderClosed();
    if (findPos(selected)) drawChart(force); else chartKey = "";
  }

  // ---------------------------------------------------------------- events
  $("o-presets").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-amt]");
    if (b) { $("o-amt").value = M.fmtNum(Number(b.dataset.amt), 0); renderOrder(); }
  });
  ["o-sym", "o-amt", "o-tp", "o-sl"].forEach((id) => $(id).addEventListener("input", renderOrder));
  $("o-sym").addEventListener("change", renderOrder);
  $("order").onsubmit = (ev) => {
    ev.preventDefault();
    open($("o-sym").value, parseAmount($("o-amt").value), parseFloat($("o-tp").value), parseFloat($("o-sl").value));
    $("o-amt").value = ""; renderOrder();
    $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
  };
  $("pos-list").addEventListener("click", (ev) => {
    const b = ev.target.closest(".demo-pos");
    if (!b) return;
    selected = b.dataset.id; chartKey = ""; dirty = true; render(true);
    $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  document.querySelectorAll('input[name="range"], input[name="cmode"]').forEach((r) => r.addEventListener("change", () => drawChart(true)));
  $("detail").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-close]");
    const p = findPos(selected);
    if (!b || !p) return;
    const f = Number(b.dataset.close);
    if (f >= 1 && !confirm(`Close the whole ${p.sym} demo investment at the current price?`)) return;
    close(p, f, "manual");
  });
  $("add-form").onsubmit = (ev) => {
    ev.preventDefault();
    const p = findPos(selected);
    if (p) addMore(p, parseAmount($("add-amt").value));
    $("add-amt").value = "";
  };
  $("tpsl-form").onsubmit = (ev) => {
    ev.preventDefault();
    const p = findPos(selected);
    if (!p) return;
    const tp = parseFloat($("d-tp").value), sl = parseFloat($("d-sl").value);
    p.tp = tp > 0 ? p.entry * (1 + tp / 100) : null;
    p.sl = sl > 0 && sl < 100 ? p.entry * (1 - sl / 100) : null;
    save(); dirty = true; render(true);
    M.toast(`${M.esc(p.sym)}: take profit ${p.tp ? M.esc(px(p.tp, p.decimals)) : "off"}, stop loss ${p.sl ? M.esc(px(p.sl, p.decimals)) : "off"} (measured from the buy price).`);
  };
  $("reset").onclick = () => {
    if (!confirm("Delete all demo investments (open and closed) in this browser?")) return;
    acct = blank(); selected = null; save(); dirty = true; render(true);
  };
  $("export").onclick = () => {
    const blob = new Blob([JSON.stringify(acct)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "demo-investments.json"; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };
  $("import-file").onchange = async (ev) => {
    const f = ev.target.files[0];
    ev.target.value = "";
    if (!f) return;
    try {
      const a = JSON.parse(await f.text());
      if (!(a && a.v === 2 && Array.isArray(a.open) && Array.isArray(a.closed))) throw new Error("not a demo investments file");
      if (a.open.some((p) => !(p.invested > 0 && p.invested <= MAX_AMT && p.qty > 0 && Array.isArray(p.events) && Array.isArray(p.ticks)))) throw new Error("the file has an invalid investment");
      if (!confirm("Replace the demo investments in this browser with the imported ones?")) return;
      acct = a; selected = acct.open[0] ? acct.open[0].id : null; save(); dirty = true; render(true);
      M.toast("Demo investments imported.");
    } catch (e) { M.toast("Could not import: " + M.esc(e.message), "toast-err"); }
  };
  window.addEventListener("storage", (ev) => { if (ev.key === KEY) { acct = load(); dirty = true; chartKey = ""; render(true); } });

  // Live prices: the same real quotes the dashboard shows (crypto streams live).
  M.onLive((msg) => {
    if (msg.type !== "snapshot" && msg.type !== "quotes") return;
    msg.quotes.forEach(onQuote);
    dirty = true;
  });
  setInterval(() => {
    render(false);
    if (saveDue) save();
  }, 1000);
  setInterval(() => { if (findPos(selected) && range() !== "live") drawChart(true); }, 5 * 60 * 1000);
  render(true);
})();
