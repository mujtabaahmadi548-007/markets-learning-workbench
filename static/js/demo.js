// Demo trading: open as many pretend trades as you like (up to $1,000,000,000 each) at the REAL live price.
// Every trade gets its own tab with a live candlestick chart (real candles only), indicators and a side panel;
// "All trades" shows every open trade side by side. Orders fill only at a fresh real price (LIVE or DELAYED).
// Saved in this browser (localStorage) - the same on the website and in the app on your PC. Not real money, not advice.
(function () {
  "use strict";
  const M = window.MLW, LC = window.LightweightCharts;
  const KEY = "mlw-demo-v2", UIKEY = "mlw-demo-ui";
  const MAX_AMT = 1e9, MIN_AMT = 1;
  const FILLABLE = ["LIVE", "DELAYED"];
  const CLASS_NAMES = { crypto: "Crypto", metal: "Precious metals", energy: "Energy", stock: "Stocks", etf: "ETFs" };
  const TF_SEC = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };
  const TF_NAME = { "1m": "1-minute", "5m": "5-minute", "15m": "15-minute", "1h": "1-hour", "4h": "4-hour", "1d": "daily" };
  const BINANCE_REST = "https://data-api.binance.vision/api/v3/klines";
  const BINANCE_WS = "wss://data-stream.binance.vision/stream?streams=";
  const COL = { up: "#16A34A", down: "#DC2626", ma20: "#F59E0B", ma50: "#7C3AED", ema20: "#0EA5E9", bb: "#94A3B8", entry: "#1D4ED8" };
  const TICKS_MAX = 6000;
  const $ = (id) => document.getElementById(id);
  const nowSec = () => Math.floor(Date.now() / 1000);

  // ---------------------------------------------------------------- state
  const quotes = new Map();    // symbol -> newest quote view
  const symTicks = new Map();  // symbol -> [[t, price]] seen since this page opened
  let acct = load();
  const ui = loadUi();
  let tab = acct.open.length ? acct.open[0].id : "new";
  let dirty = true, saveDue = false, closedVer = -1;
  const basket = new Set();

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
  function loadUi() {
    const d = { type: "candles", ind: { vol: true, ma20: true, ma50: false, ema20: false, bb: false, rsi: true }, gtf: "5m" };
    try { const u = JSON.parse(localStorage.getItem(UIKEY) || "{}"); return { ...d, ...u, ind: { ...d.ind, ...(u.ind || {}) } }; } catch (e) { return d; }
  }
  function saveUi() { try { localStorage.setItem(UIKEY, JSON.stringify(ui)); } catch (e) { /* not important */ } }

  // ---------------------------------------------------------------- helpers
  function parseAmount(s) {
    s = String(s || "").trim().toLowerCase().replace(/[$,\s_]/g, "").replace(/usd$/, "");
    const m = s.match(/^(\d*\.?\d+)([kmb])?$/);
    return m ? parseFloat(m[1]) * ({ k: 1e3, m: 1e6, b: 1e9 }[m[2]] || 1) : NaN;
  }
  const money = (v) => (v === null || v === undefined || !isFinite(v)) ? "—" : (v < 0 ? "−$" : "$") + M.fmtNum(Math.abs(v), 2);
  const signedMoney = (v) => (v > 0.004 ? "+" : "") + money(v);
  function pct(v) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    const d = Math.abs(v) < 0.1 ? 3 : 2, shown = Number(Math.abs(v).toFixed(d));
    return (shown === 0 ? "" : v > 0 ? "+" : "−") + M.fmtNum(Math.abs(v), d) + "%";
  }
  const cls = (v) => v > 0.004 ? "up" : v < -0.004 ? "down" : "flat";
  const arrow = (v) => v > 0.004 ? "▲" : v < -0.004 ? "▼" : "■";
  const qtyText = (v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: v >= 1000 ? 4 : 8 });
  const fp = (v, d) => M.fmtNum(v, d);
  function shortMoney(v) {
    const a = Math.abs(v), s = v < 0 ? "−$" : "$";
    if (a >= 1e9) return s + (a / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return s + (a / 1e6).toFixed(2) + "M";
    if (a >= 1e4) return s + (a / 1e3).toFixed(1) + "K";
    return s + M.fmtNum(a, 2);
  }
  const tradable = (q) => q && q.kind !== "daily" && q.currency !== "%" && q.status !== "UNAVAILABLE" && CLASS_NAMES[q.asset_class];
  const fillable = (q) => q && FILLABLE.includes(q.status) && q.price > 0;
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
  const isCrypto = (sym) => { const q = quotes.get(sym); return !!q && q.asset_class === "crypto"; };
  function badgeHtml(q) { if (!q) return ""; const b = M.badge(q); return `<span class="badge ${b.cls}" title="${M.esc(b.title)}">${M.esc(b.text)}</span>`; }
  function tabLabel(p) {
    const same = acct.open.filter((x) => x.sym === p.sym);
    return same.length > 1 ? `${p.sym} #${same.length - same.indexOf(p)}` : p.sym;
  }

  // ---------------------------------------------------------------- orders
  function openTrade(sym, amount, tpPct, slPct, quiet) {
    const q = quotes.get(sym);
    if (!fillable(q)) { if (!quiet) M.toast(M.esc(whyNotFillable(q)), "toast-err"); return null; }
    if (!(amount >= MIN_AMT && amount <= MAX_AMT)) { if (!quiet) M.toast("Enter an amount from $1 to $1,000,000,000.", "toast-err"); return null; }
    const t = nowSec(), price = q.price, qty = amount / price;
    const p = { id: "p" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), sym, name: q.name,
      unit: M.unitOf(q), decimals: q.decimals, opened: t, entry: price, qty, invested: amount,
      entry_source: q.source, entry_status: q.status, entry_price_time: q.source_timestamp,
      tp: tpPct > 0 ? price * (1 + tpPct / 100) : null, sl: slPct > 0 && slPct < 100 ? price * (1 - slPct / 100) : null,
      hi: price, lo: price, tf: q.asset_class === "crypto" ? "1m" : "5m",
      events: [{ t, qty, invested: amount, kind: "buy", price, amount }], ticks: [[t, price]] };
    acct.open.push(p);
    save(); dirty = true;
    if (!quiet) M.toast(`<strong>Demo trade opened</strong><br>${money(amount)} in ${M.esc(sym)}: ${M.esc(qtyText(qty))} at ${M.esc(fp(price, q.decimals))} ${M.esc(p.unit)} (${M.esc(q.status)}, ${M.esc(q.source || "")}).`);
    return p;
  }

  function addMore(p, amount) {
    const q = quotes.get(p.sym);
    if (!fillable(q)) return M.toast(M.esc(whyNotFillable(q)), "toast-err");
    if (!(amount >= MIN_AMT)) return M.toast("Enter an amount to add.", "toast-err");
    if (p.invested + amount > MAX_AMT) return M.toast(`One trade can be at most $1,000,000,000 (this one has ${money(p.invested)}). Open another trade instead.`, "toast-err");
    const t = nowSec(), add = amount / q.price;
    p.qty += add; p.invested += amount; p.entry = p.invested / p.qty;
    p.events.push({ t, qty: p.qty, invested: p.invested, kind: "add", price: q.price, amount });
    save(); dirty = true;
    M.toast(`Added ${money(amount)} to ${M.esc(p.sym)} at ${M.esc(fp(q.price, p.decimals))}. New average buy price: ${M.esc(fp(p.entry, p.decimals))}.`);
    if (mainView && mainView.p === p) mainView.redraw();
  }

  function closeTrade(p, fraction, reason) {
    const q = quotes.get(p.sym);
    if (!fillable(q)) { if (reason === "manual") M.toast(M.esc(whyNotFillable(q)), "toast-err"); return false; }
    const t = nowSec(), price = q.price;
    const qty = fraction >= 1 ? p.qty : p.qty * fraction;
    const cost = p.invested * (qty / p.qty), value = qty * price, pl = value - cost;
    acct.closed.unshift({ sym: p.sym, name: p.name, unit: p.unit, decimals: p.decimals, qty, entry: p.entry, exit: price,
      invested: cost, value, pl, opened: p.opened, closed: t, reason, exit_source: q.source, exit_status: q.status, part: fraction < 1 });
    if (acct.closed.length > 500) acct.closed.length = 500;
    if (fraction >= 1) acct.open = acct.open.filter((x) => x.id !== p.id);
    else { p.qty -= qty; p.invested -= cost; p.events.push({ t, qty: p.qty, invested: p.invested, kind: "sell", price, amount: value }); }
    save(); dirty = true;
    const why = { manual: "", "take-profit": "Take-profit reached. ", "stop-loss": "Stop-loss reached. " }[reason];
    M.toast(`<strong>Demo trade ${fraction >= 1 ? "closed" : "partly sold"}</strong><br>${why}${M.esc(qtyText(qty))} ${M.esc(p.sym)} sold at ${M.esc(fp(price, p.decimals))} = ${money(value)} · profit/loss <span class="${cls(pl)}">${signedMoney(pl)}</span>.`,
      reason === "manual" ? "" : "toast-alert");
    if (fraction >= 1 && tab === p.id) setTab(acct.open.length ? "all" : "new");
    else if (mainView && mainView.p === p) mainView.redraw();
    return true;
  }

  // ---------------------------------------------------------------- real candles
  const candleCache = new Map(); // "SYM|tf" -> {at, data}
  function aggregate(cs, step) {
    const out = [];
    for (const c of cs) {
      const b = Math.floor(c.time / step) * step, last = out[out.length - 1];
      if (last && last.time === b) {
        last.high = Math.max(last.high, c.high); last.low = Math.min(last.low, c.low); last.close = c.close;
        if (c.volume !== null && c.volume !== undefined) last.volume = (last.volume || 0) + c.volume;
      } else out.push({ time: b, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume });
    }
    return out;
  }
  function tickCandles(sym) {
    const pts = new Map();
    for (const [t, v] of symTicks.get(sym) || []) pts.set(t, v);
    for (const p of acct.open) if (p.sym === sym) for (const [t, v] of p.ticks) pts.set(t, v);
    const sorted = [...pts.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ time: t, open: v, high: v, low: v, close: v }));
    return { candles: aggregate(sorted, 60), step: 60, provider: "ticks", symbol: sym, source: "live prices recorded in this browser",
      note: "1-minute candles built from the real live prices this browser has received (only while a demo page is open)." };
  }
  async function binanceCandles(sym, tf) {
    const q = quotes.get(sym);
    const pair = q && q.provider === "binance" && q.provider_symbol ? q.provider_symbol : sym + "USDT";
    const r = await fetch(`${BINANCE_REST}?symbol=${pair}&interval=${tf}&limit=500`);
    if (!r.ok) throw new Error("HTTP " + r.status);
    const rows = await r.json();
    return { candles: rows.map((k) => ({ time: Math.floor(k[0] / 1000), open: +k[1], high: +k[2], low: +k[3], close: +k[4], volume: +k[5] })),
      step: TF_SEC[tf], provider: "binance", symbol: pair, source: "Binance", live: true,
      note: `Real Binance ${pair} ${TF_NAME[tf]} candles, streaming live into this page.` };
  }
  async function appCandles(sym, tf) {
    if (tf === "1m") return tickCandles(sym);
    const plan = { "5m": ["1D", "5m"], "15m": ["1D", "5m"], "1h": ["1M", "1h"], "4h": ["1M", "1h"], "1d": ["1Y", "1d"] }[tf];
    let d = null;
    try { d = await M.api(`/api/candles/${encodeURIComponent(sym)}?range=${plan[0]}`); } catch (e) { d = null; }
    if (!d || !Array.isArray(d.candles) || !d.candles.length) {
      const t = tickCandles(sym);
      t.note = `No stored ${TF_NAME[tf]} candles for ${sym}; showing 1-minute candles built from live prices instead.`;
      return t;
    }
    let cs = d.candles.map((c) => ({ time: c.time, open: c.open ?? c.close, high: c.high ?? c.close, low: c.low ?? c.close, close: c.close, volume: c.volume }));
    let step = TF_SEC[d.timeframe] || TF_SEC[plan[1]], extra = "";
    if (d.timeframe === plan[1] && TF_SEC[tf] > step) { cs = aggregate(cs, TF_SEC[tf]); step = TF_SEC[tf]; extra = ` (combined from ${TF_NAME[plan[1]]} candles)`; }
    else if (d.timeframe !== plan[1]) extra = ` (only ${TF_NAME[d.timeframe] || d.timeframe} candles are available)`;
    const out = { candles: cs, step, provider: d.provider, symbol: d.symbol, source: d.source, proxy: !!d.is_proxy, unofficial: !!d.is_unofficial,
      note: `Real ${d.source} ${d.symbol} ${TF_NAME[tf]} candles${extra}${d.is_unofficial ? ", unofficial source" : ""}.` +
        (M.isStatic ? " On the website they are refreshed with every update (about every 10 minutes)." : "") };
    if (d.is_proxy) out.note = `PROXY: ${d.source} ${d.symbol} is a stand-in instrument (${d.note || "not the asset itself"}); its price differs from the price your trade uses, so your buy / take-profit / stop-loss lines are not drawn on it. Choose 1m for candles of the real price.`;
    return out;
  }
  async function loadCandles(sym, tf) {
    const key = sym + "|" + tf + "|" + isCrypto(sym), c = candleCache.get(key);
    const ttl = isCrypto(sym) ? (tf === "1m" ? 15e3 : 60e3) : 120e3;
    if (c && Date.now() - c.at < ttl && tf !== "1m") return c.data;
    let data;
    if (isCrypto(sym)) {
      try { data = await binanceCandles(sym, tf); }
      catch (e) { data = await appCandles(sym, tf === "1m" ? "1m" : tf); data.note = `Binance candles could not be loaded (${e.message}). ` + data.note; }
    } else data = await appCandles(sym, tf);
    candleCache.set(key, { at: Date.now(), data });
    return data;
  }

  // One combined Binance kline stream for every chart that shows crypto candles.
  const hub = {
    subs: new Map(), ws: null, key: "", timer: null, retry: 1000,
    sub(view, pair, tf) { this.subs.set(view, `${pair.toLowerCase()}@kline_${tf}`); this.sync(); },
    unsub(view) { if (this.subs.delete(view)) this.sync(); },
    sync() { clearTimeout(this.timer); this.timer = setTimeout(() => this.connect(), 250); },
    connect() {
      const streams = [...new Set(this.subs.values())].sort(), key = streams.join("/");
      if (key === this.key && this.ws) return;
      this.key = key;
      if (this.ws) { const old = this.ws; this.ws = null; old.onclose = null; try { old.close(); } catch (e) { /* ignore */ } }
      if (!streams.length) return;
      let ws;
      try { ws = new WebSocket(BINANCE_WS + key); } catch (e) { return; }
      this.ws = ws;
      ws.onopen = () => { this.retry = 1000; };
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data), k = m.data && m.data.k;
        if (!k) return;
        const c = { time: Math.floor(k.t / 1000), open: +k.o, high: +k.h, low: +k.l, close: +k.c, volume: +k.v };
        for (const [view, s] of this.subs) if (s === m.stream) view.onKline(c);
      };
      ws.onclose = () => {
        if (this.ws !== ws) return;
        this.ws = null; this.key = "";
        setTimeout(() => this.connect(), this.retry); this.retry = Math.min(this.retry * 2, 30000);
      };
      ws.onerror = () => { try { ws.close(); } catch (e) { /* ignore */ } };
    },
  };

  // ---------------------------------------------------------------- indicators (from the real candles)
  function sma(v, n) { const o = []; let s = 0; for (let i = 0; i < v.length; i++) { s += v[i]; if (i >= n) s -= v[i - n]; o.push(i >= n - 1 ? s / n : null); } return o; }
  function ema(v, n) {
    const o = [], k = 2 / (n + 1); let prev = null, s = 0;
    for (let i = 0; i < v.length; i++) {
      if (i < n - 1) { s += v[i]; o.push(null); continue; }
      prev = i === n - 1 ? (s + v[i]) / n : v[i] * k + prev * (1 - k);
      o.push(prev);
    }
    return o;
  }
  function boll(v, n, m) {
    const mid = sma(v, n), up = [], lo = [];
    for (let i = 0; i < v.length; i++) {
      if (mid[i] === null) { up.push(null); lo.push(null); continue; }
      let q = 0; for (let j = i - n + 1; j <= i; j++) q += (v[j] - mid[i]) ** 2;
      const sd = Math.sqrt(q / n); up.push(mid[i] + m * sd); lo.push(mid[i] - m * sd);
    }
    return { mid, up, lo };
  }
  function rsi(v, n) {
    const o = new Array(v.length).fill(null);
    if (v.length <= n) return o;
    let g = 0, l = 0;
    for (let i = 1; i <= n; i++) { const d = v[i] - v[i - 1]; if (d > 0) g += d; else l -= d; }
    g /= n; l /= n; o[n] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
    for (let i = n + 1; i < v.length; i++) {
      const d = v[i] - v[i - 1];
      g = (g * (n - 1) + Math.max(d, 0)) / n; l = (l * (n - 1) + Math.max(-d, 0)) / n;
      o[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
    }
    return o;
  }

  // ---------------------------------------------------------------- chart view (main chart or a mini chart)
  const JKT_TIME = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Jakarta", hour: "numeric", minute: "2-digit", hour12: true });
  const JKT_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", day: "numeric", month: "short" });
  function chartOptions(mini, hideTime) {
    return {
      autoSize: true,
      layout: { background: { type: "solid", color: "#FFFFFF" }, textColor: "#33445C", fontFamily: "Inter, Segoe UI, system-ui, sans-serif", fontSize: mini ? 10 : 12 },
      grid: { vertLines: { color: "#F1F4F9" }, horzLines: { color: "#F1F4F9" } },
      rightPriceScale: { borderColor: "#E3E9F2", minimumWidth: mini ? 0 : (window.innerWidth < 760 ? 66 : 92) },
      timeScale: { borderColor: "#E3E9F2", timeVisible: true, secondsVisible: false, rightOffset: 4, visible: !hideTime,
        tickMarkFormatter: (t, type) => (type >= 3 ? JKT_TIME : JKT_DAY).format(new Date(t * 1000)) },
      localization: { timeFormatter: (t) => JKT_DAY.format(new Date(t * 1000)) + ", " + JKT_TIME.format(new Date(t * 1000)) },
      crosshair: { mode: 0 },
      handleScroll: !mini, handleScale: !mini,
    };
  }

  class ChartView {
    constructor(el, o) {
      this.el = el; this.mini = !!(o && o.mini);
      this.legendEl = o && o.legendEl; this.rsiEl = o && o.rsiEl; this.noteEl = o && o.noteEl;
      this.chart = LC.createChart(el, chartOptions(this.mini));
      this.series = {}; this.rsiChart = null; this.rsiSeries = null; this.token = 0; this.data = null; this.p = null; this.hover = false;
      if (this.legendEl) this.chart.subscribeCrosshairMove((param) => this.legend(param));
      this.chart.timeScale().subscribeVisibleLogicalRangeChange((r) => { if (r && this.rsiChart && !this.rsiEl.hidden) this.rsiChart.timeScale().setVisibleLogicalRange(r); });
    }
    async show(p, tf) {
      this.p = p; this.tf = tf;
      const tok = ++this.token;
      hub.unsub(this);
      if (this.noteEl) this.noteEl.textContent = "Loading real candles…";
      const d = await loadCandles(p.sym, tf);
      if (tok !== this.token) return;
      this.data = { ...d, candles: d.candles.map((c) => ({ ...c })) };
      this.redraw(true);
      if (d.provider === "binance") hub.sub(this, d.symbol, tf);
    }
    stop() { this.token++; hub.unsub(this); }
    destroy() { this.stop(); this.chart.remove(); if (this.rsiChart) this.rsiChart.remove(); }
    clearSeries() {
      for (const s of Object.values(this.series)) { try { this.chart.removeSeries(s); } catch (e) { /* gone */ } }
      this.series = {};
    }
    redraw(fit) {
      const d = this.data, p = this.p;
      if (!d || !p) return;
      this.clearSeries();
      const cs = d.candles, type = this.mini ? "candles" : ui.type, dec = p.decimals ?? 2;
      const pf = { type: "price", precision: dec, minMove: 1 / 10 ** dec };
      const showVol = !this.mini && ui.ind.vol && cs.some((c) => c.volume > 0);
      if (type === "candles") {
        this.series.main = this.chart.addCandlestickSeries({ upColor: COL.up, downColor: COL.down, borderVisible: false, wickUpColor: COL.up, wickDownColor: COL.down, priceFormat: pf });
        this.series.main.setData(cs);
      } else {
        this.series.main = this.chart.addAreaSeries({ lineColor: COL.entry, topColor: "rgba(29,78,216,0.18)", bottomColor: "rgba(29,78,216,0.01)", lineWidth: 2, priceFormat: pf });
        this.series.main.setData(cs.map((c) => ({ time: c.time, value: c.close })));
      }
      this.series.main.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: showVol ? 0.24 : 0.08 } });
      if (showVol) {
        this.series.vol = this.chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
        this.chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
        this.series.vol.setData(cs.map((c) => ({ time: c.time, value: c.volume || 0, color: c.close >= c.open ? "rgba(22,163,74,0.35)" : "rgba(220,38,38,0.35)" })));
      }
      if (!this.mini) {
        const line = (color, style) => this.chart.addLineSeries({ color, lineWidth: style ? 1 : 1.5, lineStyle: style || 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: pf });
        if (ui.ind.ma20) this.series.ma20 = line(COL.ma20);
        if (ui.ind.ma50) this.series.ma50 = line(COL.ma50);
        if (ui.ind.ema20) this.series.ema20 = line(COL.ema20);
        if (ui.ind.bb) { this.series.bbU = line(COL.bb, 2); this.series.bbM = line(COL.bb, 1); this.series.bbL = line(COL.bb, 2); }
        this.indicators(false);
      }
      // Your trade on the chart (never on a stand-in proxy instrument, whose price differs).
      if (!d.proxy) {
        const pl = (price, color, title, style) => this.series.main.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
        pl(p.entry, COL.entry, p.events.length > 1 ? "avg buy" : "buy", 2);
        if (p.tp) pl(p.tp, COL.up, "take profit", 1);
        if (p.sl) pl(p.sl, COL.down, "stop loss", 1);
        if (cs.length) {
          const marks = p.events.filter((e) => e.t >= cs[0].time - d.step).map((e) => ({
            time: Math.max(cs[0].time, Math.floor(e.t / d.step) * d.step),
            position: e.kind === "sell" ? "aboveBar" : "belowBar", color: e.kind === "sell" ? COL.down : COL.entry,
            shape: e.kind === "sell" ? "arrowDown" : "arrowUp",
            text: this.mini ? "" : `${e.kind === "buy" ? "Buy" : e.kind === "add" ? "Add" : "Sell"} ${shortMoney(e.amount)}` }));
          try { this.series.main.setMarkers(marks.sort((a, b) => a.time - b.time)); } catch (e) { /* optional */ }
        }
      }
      this.rsiPane();
      if (fit !== false) this.fit();
      if (this.noteEl) this.noteEl.textContent = d.note + (cs.length ? ` ${cs.length} candles.` : " No candles yet - they appear as live prices arrive.") + " Times are Jakarta time.";
      this.legend(null);
    }
    fit() {
      const n = this.data.candles.length, show = this.mini ? 70 : 140;
      if (n > show) this.chart.timeScale().setVisibleLogicalRange({ from: n - show, to: n + 3 });
      else this.chart.timeScale().fitContent();
    }
    indicators(lastOnly) {
      const cs = this.data.candles, closes = cs.map((c) => c.close);
      const put = (s, vals) => {
        if (!s) return;
        const pts = cs.map((c, i) => vals[i] === null ? { time: c.time } : { time: c.time, value: vals[i] });
        if (lastOnly && pts.length) { try { s.update(pts[pts.length - 1]); } catch (e) { s.setData(pts); } } else s.setData(pts);
      };
      put(this.series.ma20, sma(closes, 20));
      put(this.series.ma50, sma(closes, 50));
      put(this.series.ema20, ema(closes, 20));
      if (this.series.bbU) { const b = boll(closes, 20, 2); put(this.series.bbU, b.up); put(this.series.bbM, b.mid); put(this.series.bbL, b.lo); }
      if (this.rsiSeries && this.rsiEl && !this.rsiEl.hidden) put(this.rsiSeries, rsi(closes, 14));
    }
    rsiPane() {
      if (!this.rsiEl) return;
      const on = ui.ind.rsi && this.data.candles.length > 15;
      this.rsiEl.hidden = !on;
      if (!on) return;
      if (!this.rsiChart) {
        this.rsiChart = LC.createChart(this.rsiEl, { ...chartOptions(false), handleScroll: false, handleScale: false });
        this.rsiSeries = this.rsiChart.addLineSeries({ color: "#7C3AED", lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true, priceFormat: { type: "price", precision: 1, minMove: 0.1 } });
        this.rsiSeries.createPriceLine({ price: 70, color: "#DC2626", lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "70" });
        this.rsiSeries.createPriceLine({ price: 30, color: "#16A34A", lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "30" });
      }
      const cs = this.data.candles, r = rsi(cs.map((c) => c.close), 14);
      this.rsiSeries.setData(cs.map((c, i) => r[i] === null ? { time: c.time } : { time: c.time, value: r[i] }));
      const range = this.chart.timeScale().getVisibleLogicalRange();
      if (range) this.rsiChart.timeScale().setVisibleLogicalRange(range);
    }
    updateLast() {
      const cs = this.data.candles, c = cs[cs.length - 1];
      if (!c || !this.series.main) return;
      try {
        this.series.main.update((this.mini ? "candles" : ui.type) === "candles" ? c : { time: c.time, value: c.close });
        if (this.series.vol) this.series.vol.update({ time: c.time, value: c.volume || 0, color: c.close >= c.open ? "rgba(22,163,74,0.35)" : "rgba(220,38,38,0.35)" });
      } catch (e) { this.redraw(false); return; }
      if (!this.mini) this.indicators(true);
      if (!this.hover) this.legend(null);
    }
    onKline(c) {
      const d = this.data;
      if (!d || d.provider !== "binance") return;
      const cs = d.candles, last = cs[cs.length - 1];
      if (last && c.time < last.time) return;
      if (last && c.time === last.time) cs[cs.length - 1] = c; else cs.push(c);
      if (cs.length > 1500) cs.splice(0, cs.length - 1500);
      this.updateLast();
    }
    onTick(q) {
      const d = this.data;
      if (!d || !this.p || q.symbol !== this.p.sym || !(q.price > 0) || d.proxy) return;
      const same = d.provider === "ticks" || (d.provider === q.provider && d.symbol === q.provider_symbol);
      if (!same) { if (!this.hover) this.legend(null); return; } // never mix two different sources in one candle
      const t = q.source_timestamp ? Math.floor(Date.parse(q.source_timestamp) / 1000) : nowSec();
      const b = Math.floor(t / d.step) * d.step, cs = d.candles, last = cs[cs.length - 1];
      if (!last || b > last.time) cs.push({ time: b, open: q.price, high: q.price, low: q.price, close: q.price, volume: d.provider === "ticks" ? undefined : 0 });
      else if (b === last.time) { last.close = q.price; last.high = Math.max(last.high, q.price); last.low = Math.min(last.low, q.price); }
      else return;
      this.updateLast();
    }
    legend(param) {
      if (!this.legendEl || !this.data || !this.p) return;
      const cs = this.data.candles, p = this.p;
      let c = null;
      if (param && param.time !== undefined && param.point && param.point.x >= 0) { c = cs.find((x) => x.time === param.time) || null; this.hover = !!c; }
      else this.hover = false;
      if (!c) c = cs[cs.length - 1];
      const q = quotes.get(p.sym), dec = p.decimals ?? 2;
      if (!c) { this.legendEl.innerHTML = `<div class="lg-row"><strong>${M.esc(p.sym)}</strong> · waiting for prices…</div>`; return; }
      const i = cs.indexOf(c), prev = i > 0 ? cs[i - 1].close : c.open, ch = (c.close / prev - 1) * 100;
      const closes = cs.map((x) => x.close);
      const ind = [];
      if (ui.ind.ma20) { const v = sma(closes, 20)[i]; if (v !== null) ind.push(`<span style="color:${COL.ma20}">MA20 ${fp(v, dec)}</span>`); }
      if (ui.ind.ma50) { const v = sma(closes, 50)[i]; if (v !== null) ind.push(`<span style="color:${COL.ma50}">MA50 ${fp(v, dec)}</span>`); }
      if (ui.ind.ema20) { const v = ema(closes, 20)[i]; if (v !== null) ind.push(`<span style="color:${COL.ema20}">EMA20 ${fp(v, dec)}</span>`); }
      if (ui.ind.bb) { const b = boll(closes, 20, 2); if (b.mid[i] !== null) ind.push(`<span style="color:#64748B">BB ${fp(b.lo[i], dec)} – ${fp(b.up[i], dec)}</span>`); }
      if (ui.ind.rsi) { const v = rsi(closes, 14)[i]; if (v !== null) ind.push(`<span style="color:#7C3AED">RSI ${M.fmtNum(v, 1)}</span>`); }
      const atValue = p.qty * c.close, atPl = atValue - p.invested;
      const when = this.data.step >= 86400 ? M.dateOnly(new Date(c.time * 1000)) : M.formatJakarta(new Date(c.time * 1000)).replace(/:\d\d (AM|PM)$/, " $1");
      this.legendEl.innerHTML =
        `<div class="lg-row"><strong>${M.esc(p.sym)}</strong> · ${M.esc(TF_NAME[this.tf] || "")} · ${M.esc(this.data.source || "")} · ${M.esc(when)}${this.hover ? "" : ' <span class="lg-live">● latest</span>'}</div>` +
        `<div class="lg-row num">O <b>${fp(c.open, dec)}</b> H <b>${fp(c.high, dec)}</b> L <b>${fp(c.low, dec)}</b> C <b class="${cls(ch)}">${fp(c.close, dec)}</b> <span class="${cls(ch)}">${arrow(ch)} ${pct(ch)}</span>` +
        (c.volume > 0 ? ` · Vol <b>${M.fmtVolume(c.volume)}</b>` : "") + `</div>` +
        (ind.length ? `<div class="lg-row num">${ind.join(" · ")}</div>` : "") +
        (this.data.proxy ? "" : `<div class="lg-row">At this price your trade = <b>${money(atValue)}</b> <span class="${cls(atPl)}">(${signedMoney(atPl)})</span></div>`);
      void q;
    }
  }

  // ---------------------------------------------------------------- live prices
  function onQuote(q) {
    quotes.set(q.symbol, q);
    if (!(q.price > 0) || !FILLABLE.includes(q.status)) return;
    const t = q.source_timestamp ? Math.floor(Date.parse(q.source_timestamp) / 1000) : nowSec();
    const st = symTicks.get(q.symbol) || [];
    const lt = st[st.length - 1];
    if (!lt || t > lt[0]) { st.push([t, q.price]); if (st.length > 4000) st.splice(0, st.length - 4000); symTicks.set(q.symbol, st); }
    for (const p of acct.open.filter((x) => x.sym === q.symbol)) {
      if (t < p.opened) continue;
      const last = p.ticks[p.ticks.length - 1];
      if (!last || (t > last[0] && (t - last[0] >= 2 && q.price !== last[1] || t - last[0] >= 60))) {
        p.ticks.push([t, q.price]);
        if (p.ticks.length > TICKS_MAX) p.ticks.splice(0, p.ticks.length - TICKS_MAX);
        saveDue = true;
      }
      p.hi = Math.max(p.hi, q.price); p.lo = Math.min(p.lo, q.price);
      if (p.tp && q.price >= p.tp) closeTrade(p, 1, "take-profit");      // automatic take-profit / stop-loss,
      else if (p.sl && q.price <= p.sl) closeTrade(p, 1, "stop-loss");   // only on a fresh real price
    }
    if (mainView && mainView.p && mainView.p.sym === q.symbol) mainView.onTick(q);
    for (const g of gridViews.values()) if (g.view.p && g.view.p.sym === q.symbol) g.view.onTick(q);
  }

  // ---------------------------------------------------------------- tabs and views
  let mainView = null;
  const gridViews = new Map(); // trade id -> {view, card}

  function setTab(t) {
    if (t !== "new" && t !== "all" && !findPos(t)) t = acct.open.length ? "all" : "new";
    tab = t;
    renderTabs();
    $("view-new").hidden = tab !== "new";
    $("view-all").hidden = tab !== "all";
    const p = findPos(tab);
    $("view-trade").hidden = !p;
    if (tab === "all") buildGrid(); else destroyGrid();
    if (p) {
      syncToolbar(p);
      if (!mainView) mainView = new ChartView($("chart-main"), { legendEl: $("legend"), rsiEl: $("chart-rsi"), noteEl: $("chart-note") });
      mainView.show(p, p.tf || (isCrypto(p.sym) ? "1m" : "5m"));
      renderSide();
    } else if (mainView) mainView.stop();
    if (tab === "new") renderOrder();
  }

  function renderTabs() {
    const box = $("tabs");
    const tabs = [`<button type="button" role="tab" class="ttab ttab-all" data-tab="all" aria-selected="${tab === "all"}">▦ All trades <span class="ttab-n">${acct.open.length}</span></button>`];
    for (const p of acct.open) {
      const pn = priceNow(p), pl = pn === null ? null : p.qty * pn - p.invested;
      tabs.push(`<button type="button" role="tab" class="ttab" data-tab="${p.id}" aria-selected="${tab === p.id}">
        <span class="ttab-sym">${M.esc(tabLabel(p))}</span>
        <span class="ttab-pl num ${pl === null ? "" : cls(pl)}">${pl === null ? "…" : `${arrow(pl)} ${pl >= 0 ? "+" : "−"}${shortMoney(Math.abs(pl))}`}</span>
        <span class="ttab-x" data-x="${p.id}" title="Close this trade" aria-label="Close ${M.esc(p.sym)} trade">×</span></button>`);
    }
    tabs.push(`<button type="button" role="tab" class="ttab ttab-new" data-tab="new" aria-selected="${tab === "new"}">＋ New trade</button>`);
    const html = tabs.join("");
    if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
  }

  function syncToolbar(p) {
    document.querySelectorAll('input[name="tf"]').forEach((r) => { r.checked = r.value === (p.tf || "5m"); });
    document.querySelectorAll('input[name="ctype"]').forEach((r) => { r.checked = r.value === ui.type; });
    document.querySelectorAll('#ind input').forEach((c) => { c.checked = !!ui.ind[c.value]; });
  }

  function renderSide() {
    const p = findPos(tab);
    if (!p) return;
    const q = quotes.get(p.sym), pn = priceNow(p), now = nowSec();
    const value = pn === null ? null : p.qty * pn, pl = value === null ? null : value - p.invested;
    $("t-sym").innerHTML = `${M.esc(tabLabel(p))} <span class="muted small">${M.esc(p.name || "")}</span>`;
    $("t-badge").innerHTML = badgeHtml(q);
    $("t-sub").innerHTML = `${q ? M.esc(q.source || "") + " · " + M.esc(M.updatedText(q).text) + " · " : ""}<a href="${M.base}/asset/${encodeURIComponent(p.sym)}">asset page →</a>`;
    const ve = $("t-value"), prev = Number(ve.dataset.v);
    ve.textContent = money(value);
    if (value !== null && ve.dataset.v !== "" && isFinite(prev) && prev !== value) {
      ve.classList.remove("flash-up", "flash-down"); void ve.offsetWidth; ve.classList.add(value > prev ? "flash-up" : "flash-down");
    }
    ve.dataset.v = value === null ? "" : String(value);
    $("t-pl").className = "ts-pl num " + (pl === null ? "" : cls(pl));
    $("t-pl").textContent = pl === null ? "waiting for a price" : `${arrow(pl)} ${signedMoney(pl)} (${pct(pl / p.invested * 100)})`;
    const priceCh = pn === null ? null : (pn / p.entry - 1) * 100;
    const rows = [
      ["Invested", money(p.invested)],
      ["Quantity", `${qtyText(p.qty)} ${M.esc(p.sym)}`],
      [p.events.length > 1 ? "Avg. buy price" : "Buy price", `${fp(p.entry, p.decimals)} ${M.esc(p.unit)}`],
      ["Price now", pn === null ? "—" : `${fp(pn, p.decimals)} <span class="${cls(priceCh)}">${pct(priceCh)}</span>`],
      ["Every 1% move", money((value ?? p.invested) / 100)],
      ["Best since buy", `${fp(p.hi, p.decimals)} <span class="up">${signedMoney(p.qty * p.hi - p.invested)}</span>`],
      ["Worst since buy", `${fp(p.lo, p.decimals)} <span class="${cls(p.qty * p.lo - p.invested)}">${signedMoney(p.qty * p.lo - p.invested)}</span>`],
      ["Asset today", q && q.change_pct !== null && q.change_pct !== undefined ? `<span class="${cls(q.change_pct)}">${pct(q.change_pct)}</span> <span class="muted">${M.esc(M.fmtChange(q).basis)}</span>` : "—"],
      ["24h / day range", q && q.day_low !== null && q.day_high !== null && q.day_low !== undefined ? `${fp(q.day_low, p.decimals)} – ${fp(q.day_high, p.decimals)}` : "—"],
      ["Opened", `${M.esc(M.formatJakarta(new Date(p.opened * 1000)))}`],
      ["Time held", durationText(now - p.opened)],
      ["Filled from", `${M.esc(p.entry_source || "")} (${M.esc(p.entry_status || "")})`],
      ["Take profit", p.tp ? `${fp(p.tp, p.decimals)} · ${pn ? pct((p.tp / pn - 1) * 100) + " away" : ""} · ${signedMoney(p.qty * p.tp - p.invested)}` : "off"],
      ["Stop loss", p.sl ? `${fp(p.sl, p.decimals)} · ${pn ? pct((p.sl / pn - 1) * 100) + " away" : ""} · ${signedMoney(p.qty * p.sl - p.invested)}` : "off"],
      ["Orders", p.events.map((e) => `${e.kind === "buy" ? "Buy" : e.kind === "add" ? "Add" : "Sell"} ${shortMoney(e.amount)} @ ${fp(e.price, p.decimals)}`).join("<br>")],
    ];
    $("t-stats").innerHTML = rows.map(([k, v]) => `<div class="ts-row"><dt>${k}</dt><dd class="num">${v}</dd></div>`).join("");
    $("d-tpsl-now").textContent = `Take profit ${p.tp ? fp(p.tp, p.decimals) : "off"}, stop loss ${p.sl ? fp(p.sl, p.decimals) : "off"} (from the buy price; leave a box empty to switch it off).`;
    document.querySelectorAll("#view-trade [data-close]").forEach((b) => { b.disabled = !fillable(q); b.title = fillable(q) ? "" : whyNotFillable(q); });
  }

  // "All trades": every open trade side by side, each with its own live mini chart.
  function buildGrid() {
    const grid = $("grid");
    const ids = new Set(acct.open.map((p) => p.id));
    for (const [id, g] of gridViews) if (!ids.has(id)) { g.view.destroy(); g.card.remove(); gridViews.delete(id); }
    const empty = grid.querySelector(".grid-empty");
    if (!acct.open.length) {
      if (!empty) grid.innerHTML = '<p class="muted grid-empty">No open trades yet. Open one — or several at once — in <strong>＋ New trade</strong>.</p>';
      return;
    }
    if (empty) empty.remove();
    for (const p of acct.open) {
      if (gridViews.has(p.id)) continue;
      const card = document.createElement("article");
      card.className = "gcard";
      card.innerHTML = `<header class="gc-head"><button type="button" class="gc-title" data-tab="${p.id}"><strong>${M.esc(tabLabel(p))}</strong> <span class="muted small">${M.esc(p.name || "")}</span></button><span class="gc-badge"></span></header>
        <div class="gc-nums"><span class="gc-val num"></span><span class="gc-pl num"></span></div>
        <div class="gc-chart"></div>
        <div class="gc-foot small muted"></div>
        <div class="gc-actions"><button type="button" class="btn btn-sm" data-tab="${p.id}">Full chart</button>
          <button type="button" class="btn btn-sm btn-danger" data-x="${p.id}">Close</button></div>`;
      grid.appendChild(card);
      const view = new ChartView(card.querySelector(".gc-chart"), { mini: true });
      view.show(p, ui.gtf);
      gridViews.set(p.id, { view, card });
    }
    renderGridText();
  }
  function destroyGrid() { for (const g of gridViews.values()) { g.view.destroy(); g.card.remove(); } gridViews.clear(); }
  function renderGridText() {
    for (const [id, g] of gridViews) {
      const p = findPos(id);
      if (!p) continue;
      const q = quotes.get(p.sym), pn = priceNow(p), value = pn === null ? null : p.qty * pn, pl = value === null ? null : value - p.invested;
      g.card.querySelector(".gc-badge").innerHTML = badgeHtml(q);
      g.card.querySelector(".gc-val").textContent = money(value);
      const plEl = g.card.querySelector(".gc-pl");
      plEl.className = "gc-pl num " + (pl === null ? "" : cls(pl));
      plEl.textContent = pl === null ? "…" : `${arrow(pl)} ${signedMoney(pl)} (${pct(pl / p.invested * 100)})`;
      g.card.querySelector(".gc-foot").textContent = `${money(p.invested)} at ${fp(p.entry, p.decimals)} · price now ${pn === null ? "—" : fp(pn, p.decimals)} · held ${durationText(nowSec() - p.opened)}`;
    }
  }

  // ---------------------------------------------------------------- new trade(s)
  function tradableList() {
    return [...quotes.values()].filter(tradable).sort((a, b) => a.id - b.id);
  }
  function fillSelect() {
    const sel = $("o-sym"), list = tradableList();
    const sig = list.map((q) => q.symbol).join(",");
    if (sel.dataset.sig === sig) return;
    sel.dataset.sig = sig;
    const keep = sel.value || new URLSearchParams(location.search).get("symbol") || "BTC";
    const groups = {};
    list.forEach((q) => { (groups[q.asset_class] = groups[q.asset_class] || []).push(q); });
    sel.innerHTML = Object.keys(CLASS_NAMES).filter((c) => groups[c]).map((c) =>
      `<optgroup label="${CLASS_NAMES[c]}">${groups[c].map((q) => `<option value="${M.esc(q.symbol)}">${M.esc(q.symbol)} — ${M.esc(q.name)}</option>`).join("")}</optgroup>`).join("");
    if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
    $("b-chips").innerHTML = Object.keys(CLASS_NAMES).filter((c) => groups[c]).map((c) =>
      `<div class="bgroup"><span class="bgroup-name">${CLASS_NAMES[c]}</span>${groups[c].map((q) =>
        `<button type="button" class="bchip" data-sym="${M.esc(q.symbol)}" aria-pressed="${basket.has(q.symbol)}">${M.esc(q.symbol)}</button>`).join("")}</div>`).join("");
  }
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
    else if (amt > MAX_AMT) msg = "The most for one trade is $1,000,000,000. Open several trades for more.";
    else if (amt < MIN_AMT) msg = "The smallest demo trade is $1.";
    else {
      ok = true;
      const tp = parseFloat($("o-tp").value), sl = parseFloat($("o-sl").value);
      msg = `You get <strong>${M.esc(qtyText(amt / q.price))} ${M.esc(q.symbol)}</strong> at <span class="num">${M.esc(M.fmtPrice(q))}</span> for <strong>${money(amt)}</strong>. Every 1% move = ${money(amt / 100)}.` +
        (tp > 0 ? ` Take profit at ${M.esc(fp(q.price * (1 + tp / 100), q.decimals))} (+${money(amt * tp / 100)}).` : "") +
        (sl > 0 && sl < 100 ? ` Stop loss at ${M.esc(fp(q.price * (1 - sl / 100), q.decimals))} (−${money(amt * sl / 100)}).` : "");
    }
    pv.className = "demo-preview" + (!ok && (amt > 0 || (q && !fillable(q))) ? " bad" : "");
    pv.innerHTML = ok ? msg : M.esc(msg);
    btn.disabled = !ok;
    btn.textContent = ok ? `Open ${money(amt)} ${q.symbol} trade (demo)` : "Open trade (demo)";
    renderBasket();
  }
  function renderBasket() {
    const amt = parseAmount($("b-amt").value), syms = [...basket], btn = $("b-submit"), pv = $("b-preview");
    if (!syms.length) { pv.className = "demo-preview"; pv.textContent = "Pick at least one asset."; btn.disabled = true; btn.textContent = "Open trades (demo)"; return; }
    const ok = syms.filter((s) => fillable(quotes.get(s))), no = syms.filter((s) => !fillable(quotes.get(s)));
    let html = "";
    if (!(amt > 0)) html = "Enter the amount for each trade.";
    else if (amt > MAX_AMT) html = "The most for one trade is $1,000,000,000.";
    else html = `<strong>${ok.length} trade${ok.length === 1 ? "" : "s"}</strong> of ${money(amt)} each (total ${money(amt * ok.length)}): ${ok.map((s) => `${M.esc(s)} @ ${M.esc(M.fmtPrice(quotes.get(s)))}`).join(", ")}.`;
    if (no.length) html += `<br><span class="down">Cannot fill now: ${no.map((s) => `${M.esc(s)} (${M.esc((quotes.get(s) || {}).status || "no price")})`).join(", ")}.</span>`;
    pv.className = "demo-preview";
    pv.innerHTML = html;
    btn.disabled = !(amt >= MIN_AMT && amt <= MAX_AMT && ok.length);
    btn.textContent = btn.disabled ? "Open trades (demo)" : `Open ${ok.length} trade${ok.length === 1 ? "" : "s"} (demo)`;
  }

  // ---------------------------------------------------------------- totals + closed list
  function renderKpis() {
    let value = 0, inv = 0, missing = 0;
    for (const p of acct.open) { const pn = priceNow(p); inv += p.invested; if (pn === null) missing++; else value += p.qty * pn; }
    const closedPl = acct.closed.reduce((s, c) => s + c.pl, 0), n = acct.open.length;
    $("k-value").textContent = missing ? "…" : money(value);
    $("k-value-sub").textContent = n ? `${n} open trade${n === 1 ? "" : "s"}${missing ? " · waiting for prices" : ""}` : "no open trades";
    $("k-inv").textContent = money(inv);
    $("k-inv-sub").textContent = n ? "in open trades" : "—";
    const openPl = missing ? null : value - inv;
    $("k-open").innerHTML = openPl === null ? "…" : `<span class="${cls(openPl)}">${arrow(openPl)} ${signedMoney(openPl)}</span>`;
    $("k-open-sub").textContent = openPl === null || !inv ? "—" : pct(openPl / inv * 100) + " on the amount invested";
    $("k-closed").innerHTML = `<span class="${cls(closedPl)}">${signedMoney(closedPl)}</span>`;
    $("k-closed-sub").textContent = acct.closed.length ? `${acct.closed.length} closed (incl. partial sales)` : "nothing closed yet";
  }
  function renderClosed() {
    if (closedVer === acct.closed.length + (acct.closed[0] ? acct.closed[0].closed : 0)) return;
    closedVer = acct.closed.length + (acct.closed[0] ? acct.closed[0].closed : 0);
    $("closed-card").hidden = !acct.closed.length;
    $("closed-body").innerHTML = acct.closed.slice(0, 200).map((c) => `<tr>
      <td title="${M.esc(M.utcLabel(new Date(c.closed * 1000)))}">${M.esc(M.shortJakarta(new Date(c.closed * 1000)))}</td>
      <td><strong>${M.esc(c.sym)}</strong>${c.part ? ' <span class="chip">part</span>' : ""}</td>
      <td class="r num">${money(c.invested)}</td>
      <td class="r num">${M.esc(fp(c.entry, c.decimals))}</td>
      <td class="r num">${M.esc(fp(c.exit, c.decimals))}</td>
      <td class="r num">${money(c.value)}</td>
      <td class="r num ${cls(c.pl)}">${signedMoney(c.pl)} (${pct(c.pl / c.invested * 100)})</td>
      <td class="hide-sm small muted">${M.esc({ manual: "you closed it", "take-profit": "take-profit", "stop-loss": "stop-loss" }[c.reason] || c.reason)} · ${M.esc(c.exit_source || "")} ${M.esc(c.exit_status || "")}</td></tr>`).join("");
  }

  function render() {
    fillSelect();
    renderKpis();
    renderTabs();
    renderClosed();
    if (tab === "new") renderOrder();
    else if (tab === "all") { buildGrid(); renderGridText(); }
    else renderSide();
  }

  // ---------------------------------------------------------------- events
  $("tabs").addEventListener("click", (ev) => {
    const x = ev.target.closest("[data-x]");
    if (x) {
      ev.stopPropagation();
      const p = findPos(x.dataset.x);
      if (p && confirm(`Close the ${p.sym} demo trade at the current price?`)) { closeTrade(p, 1, "manual"); render(); }
      return;
    }
    const b = ev.target.closest("[data-tab]");
    if (b) setTab(b.dataset.tab);
  });
  $("grid").addEventListener("click", (ev) => {
    const x = ev.target.closest("[data-x]");
    if (x) { const p = findPos(x.dataset.x); if (p && confirm(`Close the ${p.sym} demo trade at the current price?`)) { closeTrade(p, 1, "manual"); render(); } return; }
    const b = ev.target.closest("[data-tab]");
    if (b) setTab(b.dataset.tab);
  });
  document.querySelectorAll(".demo-presets[data-target]").forEach((box) => box.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-amt]");
    if (b) { $(box.dataset.target).value = M.fmtNum(Number(b.dataset.amt), 0); renderOrder(); }
  }));
  ["o-sym", "o-amt", "o-tp", "o-sl", "b-amt"].forEach((id) => $(id).addEventListener("input", renderOrder));
  $("o-sym").addEventListener("change", renderOrder);
  $("b-chips").addEventListener("click", (ev) => {
    const b = ev.target.closest(".bchip");
    if (!b) return;
    if (basket.has(b.dataset.sym)) basket.delete(b.dataset.sym); else basket.add(b.dataset.sym);
    b.setAttribute("aria-pressed", basket.has(b.dataset.sym));
    renderBasket();
  });
  $("order").onsubmit = (ev) => {
    ev.preventDefault();
    const p = openTrade($("o-sym").value, parseAmount($("o-amt").value), parseFloat($("o-tp").value), parseFloat($("o-sl").value));
    if (p) { $("o-amt").value = ""; setTab(p.id); render(); }
  };
  $("b-submit").onclick = () => {
    const amt = parseAmount($("b-amt").value), opened = [], skipped = [];
    for (const s of basket) { const p = openTrade(s, amt, 0, 0, true); if (p) opened.push(s); else skipped.push(s); }
    if (opened.length) M.toast(`<strong>${opened.length} demo trade${opened.length === 1 ? "" : "s"} opened</strong><br>${money(amt)} each in ${M.esc(opened.join(", "))}.` +
      (skipped.length ? `<br>Not filled (no fresh price / market closed): ${M.esc(skipped.join(", "))}.` : ""));
    else M.toast("No trade could be filled right now: " + M.esc(skipped.join(", ")), "toast-err");
    if (opened.length) { basket.clear(); $("b-chips").dataset.sig = ""; $("o-sym").dataset.sig = ""; setTab("all"); render(); }
  };
  document.querySelectorAll('input[name="tf"]').forEach((r) => r.addEventListener("change", () => {
    const p = findPos(tab);
    if (!p) return;
    p.tf = r.value; save();
    mainView.show(p, p.tf);
  }));
  document.querySelectorAll('input[name="ctype"]').forEach((r) => r.addEventListener("change", () => { ui.type = r.value; saveUi(); if (mainView) mainView.redraw(); }));
  document.querySelectorAll("#ind input").forEach((c) => c.addEventListener("change", () => { ui.ind[c.value] = c.checked; saveUi(); if (mainView) mainView.redraw(false); }));
  document.querySelectorAll('input[name="gtf"]').forEach((r) => {
    r.checked = r.value === ui.gtf;
    r.addEventListener("change", () => { ui.gtf = r.value; saveUi(); for (const [id, g] of gridViews) { const p = findPos(id); if (p) g.view.show(p, ui.gtf); } });
  });
  $("view-trade").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-close]");
    const p = findPos(tab);
    if (!b || !p) return;
    const f = Number(b.dataset.close);
    if (f >= 1 && !confirm(`Close the whole ${p.sym} demo trade at the current price?`)) return;
    closeTrade(p, f, "manual"); render();
  });
  $("add-form").onsubmit = (ev) => {
    ev.preventDefault();
    const p = findPos(tab);
    if (p) addMore(p, parseAmount($("add-amt").value));
    $("add-amt").value = ""; render();
  };
  $("tpsl-form").onsubmit = (ev) => {
    ev.preventDefault();
    const p = findPos(tab);
    if (!p) return;
    const tp = parseFloat($("d-tp").value), sl = parseFloat($("d-sl").value);
    p.tp = tp > 0 ? p.entry * (1 + tp / 100) : null;
    p.sl = sl > 0 && sl < 100 ? p.entry * (1 - sl / 100) : null;
    save(); if (mainView) mainView.redraw(false); render();
    M.toast(`${M.esc(p.sym)}: take profit ${p.tp ? M.esc(fp(p.tp, p.decimals)) : "off"}, stop loss ${p.sl ? M.esc(fp(p.sl, p.decimals)) : "off"}.`);
  };
  $("reset").onclick = () => {
    if (!confirm("Delete all demo trades (open and closed) in this browser?")) return;
    acct = blank(); save(); closedVer = -1; setTab("new"); render();
  };
  $("export").onclick = () => {
    const blob = new Blob([JSON.stringify(acct)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "demo-trades.json"; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };
  $("import-file").onchange = async (ev) => {
    const f = ev.target.files[0];
    ev.target.value = "";
    if (!f) return;
    try {
      const a = JSON.parse(await f.text());
      if (!(a && a.v === 2 && Array.isArray(a.open) && Array.isArray(a.closed))) throw new Error("not a demo trades file");
      if (a.open.some((p) => !(p.invested > 0 && p.invested <= MAX_AMT && p.qty > 0 && Array.isArray(p.events) && Array.isArray(p.ticks)))) throw new Error("the file has an invalid trade");
      if (!confirm("Replace the demo trades in this browser with the imported ones?")) return;
      acct = a; save(); closedVer = -1; setTab(acct.open.length ? "all" : "new"); render();
      M.toast("Demo trades imported.");
    } catch (e) { M.toast("Could not import: " + M.esc(e.message), "toast-err"); }
  };
  window.addEventListener("storage", (ev) => { if (ev.key === KEY) { acct = load(); closedVer = -1; setTab(tab); render(); } });

  // Live prices: the same real quotes the dashboard shows (crypto streams live).
  M.onLive((msg) => {
    if (msg.type !== "snapshot" && msg.type !== "quotes") return;
    const first = !quotes.size;
    msg.quotes.forEach(onQuote);
    dirty = true;
    if (first) setTab(tab);
  });
  setInterval(() => { if (dirty) { dirty = false; render(); } if (saveDue) save(); }, 1000);
  setInterval(() => { if (mainView && mainView.p && !isCrypto(mainView.p.sym) && mainView.tf !== "1m") mainView.show(mainView.p, mainView.tf); }, 3 * 60 * 1000);
  setTab(tab);
  render();
})();
