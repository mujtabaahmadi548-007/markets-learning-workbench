// Briefing page: a price chart under any row of the "Prices now" table, as candles or a line of the closes.
// Same real candle data as the asset pages (/api/candles; on the website the JSON files the build writes).
(function () {
  "use strict";
  const M = window.MLW, LC = window.LightweightCharts;
  const table = document.getElementById("brief-prices"), tools = document.getElementById("bc-tools");
  if (!table || !tools || !LC) return;

  const KEY = "mlw-briefing-charts-v1";
  const prefs = Object.assign({ type: "candles", range: "1D", open: [] }, (() => {
    try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; }
  })());
  if (!["candles", "line"].includes(prefs.type)) prefs.type = "candles";
  if (!["1D", "1W", "1M", "6M", "1Y"].includes(prefs.range)) prefs.range = "1D";
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (e) { /* storage blocked */ } };

  const UP = "#16A34A", DOWN = "#DC2626";
  const DAILY = new Set(["1d", "1wk", "1mo"]);
  const TF_NAME = { "5m": "5-minute", "1h": "hourly", "1d": "daily", "1wk": "weekly", "1mo": "monthly" };
  const STEP = { "5m": 300, "1h": 3600, "1d": 86400 };
  const JKT_TIME = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Jakarta", hour: "numeric", minute: "2-digit", hour12: true });
  const JKT_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", day: "numeric", month: "short" });
  const UTC_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "numeric", month: "short" });
  const UTC_MON = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", month: "short" });
  const UTC_YEAR = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", year: "numeric" });
  const UTC_DATE = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short", year: "numeric" });

  const views = new Map();  // symbol -> view

  // On phones the table scrolls sideways; size the charts to the visible part, not the whole table.
  const wrap = table.closest(".table-wrap") || table.parentElement;
  const fitWidth = () => table.style.setProperty("--bc-w", Math.max(200, wrap.clientWidth - 16) + "px");
  fitWidth();
  window.addEventListener("resize", fitWidth);

  function fp(v, dec) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return Math.abs(v) < 1 && v !== 0 && dec <= 2 ? Number(v.toPrecision(3)).toString() : M.fmtNum(v, dec);
  }
  function when(v, t) {
    const d = new Date(t * 1000);
    return v.daily ? UTC_DATE.format(d) : JKT_DAY.format(d) + ", " + JKT_TIME.format(d);
  }

  function makeView(tr) {
    const sym = tr.dataset.symbol, dec = Number(tr.dataset.dec || 2);
    const row = document.createElement("tr");
    row.className = "bc-row";
    row.innerHTML = `<td colspan="${tr.children.length}"><div class="bc-wrap"><div class="chart-legend bc-legend"></div><div class="bc-chart"></div></div>
      <p class="small muted bc-note">Loading chart…</p></td>`;
    tr.after(row);
    const v = { sym, dec, tr, row, el: row.querySelector(".bc-chart"), legend: row.querySelector(".bc-legend"),
      note: row.querySelector(".bc-note"), chart: null, series: null, data: null, daily: false, token: 0 };
    v.chart = LC.createChart(v.el, {
      autoSize: true,
      layout: { background: { type: "solid", color: "#FFFFFF" }, textColor: "#33445C", fontFamily: "Inter, Segoe UI, system-ui, sans-serif" },
      grid: { vertLines: { color: "#EEF2F8" }, horzLines: { color: "#EEF2F8" } },
      rightPriceScale: { borderColor: "#E3E9F2", scaleMargins: { top: 0.18, bottom: 0.06 } },
      timeScale: {
        borderColor: "#E3E9F2", timeVisible: true, secondsVisible: false, rightOffset: 2,
        tickMarkFormatter: (t, type) => {
          const d = new Date(t * 1000);
          if (v.daily) return type === 0 ? UTC_YEAR.format(d) : type === 1 ? UTC_MON.format(d) : UTC_DAY.format(d);
          return type >= 3 ? JKT_TIME.format(d) : JKT_DAY.format(d);
        },
      },
      localization: { timeFormatter: (t) => when(v, t), priceFormatter: (p) => fp(p, dec) },
      crosshair: { mode: 0 },
    });
    v.chart.subscribeCrosshairMove((param) => {
      const cs = (v.data && v.data.candles) || [];
      const i = param && param.time !== undefined ? cs.findIndex((c) => c.time === param.time) : -1;
      legend(v, i < 0 ? cs.length - 1 : i);
    });
    return v;
  }

  function legend(v, i) {
    const cs = (v.data && v.data.candles) || [], b = cs[i];
    if (!b) { v.legend.innerHTML = ""; return; }
    const f = (x) => fp(x, v.dec), first = cs[0], base = v.data.close_only ? first.close : (first.open ?? first.close);
    const pct = base ? (b.close / base - 1) * 100 : null;
    let html = `<span class="lg-date">${M.esc(v.sym)} · ${M.esc(when(v, b.time))}</span><br>`;
    html += v.data.close_only || prefs.type === "line" ? `<span class="lg-k">Close</span> ${f(b.close)}`
      : `<span class="lg-k">O</span> ${f(b.open)} <span class="lg-k">H</span> ${f(b.high)} <span class="lg-k">L</span> ${f(b.low)} <span class="lg-k">C</span> ${f(b.close)}`;
    if (pct !== null) html += ` <span class="${pct >= 0 ? "up" : "down"}">${pct >= 0 ? "▲ +" : "▼ −"}${M.fmtNum(Math.abs(pct), 2)}%</span> <span class="lg-k">since start of ${M.esc(v.data.range || prefs.range)}</span>`;
    v.legend.innerHTML = html;
  }

  function draw(v) {
    const d = v.data, cs = d.candles || [];
    if (v.series) { v.chart.removeSeries(v.series); v.series = null; }
    v.daily = DAILY.has(d.timeframe);
    if (!cs.length) {
      legend(v, -1);
      v.note.textContent = "No chart data for this range." + ((d.problems || []).length ? " " + d.problems.join(" · ") : "");
      return;
    }
    const line = d.close_only || prefs.type === "line";
    if (line) {
      const up = cs[cs.length - 1].close >= (d.close_only ? cs[0].close : (cs[0].open ?? cs[0].close));
      v.series = v.chart.addLineSeries({ color: d.close_only ? "#1D4ED8" : up ? UP : DOWN, lineWidth: 2, priceLineVisible: false });
      v.series.setData(cs.map((c) => ({ time: c.time, value: c.close })));
    } else {
      v.series = v.chart.addCandlestickSeries({ upColor: UP, downColor: DOWN, borderVisible: false, wickUpColor: UP, wickDownColor: DOWN });
      v.series.setData(cs.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
    }
    v.chart.timeScale().fitContent();
    legend(v, cs.length - 1);
    const tf = TF_NAME[d.timeframe] || d.timeframe;
    const notes = [`Chart: ${d.source} — ${d.note || d.symbol}. ${d.close_only ? "Daily values only (line)" : tf + " bars"}, ${cs.length.toLocaleString("en-US")} points.`,
      v.daily ? "Bars are labelled by trading date." : "Times are Jakarta time.",
      d.close_only && prefs.type === "candles" ? "This source has closing values only, so it is always drawn as a line." : ""];
    if (d.is_proxy) notes.push("PROXY: a stand-in instrument that moves closely with this asset but is not the asset itself.");
    if (d.is_unofficial) notes.push("Unofficial source (Yahoo).");
    if (d.newer_note) notes.push(d.newer_note);
    notes.push(`<a href="${M.base}/asset/${encodeURIComponent(v.sym)}">Full chart with more ranges →</a>`);
    v.note.innerHTML = notes.filter(Boolean).map((n) => (n.startsWith("<a ") ? n : M.esc(n))).join(" ");
  }

  async function load(v) {
    const tok = ++v.token, range = prefs.range;
    v.note.textContent = "Loading chart…";
    let d;
    try { d = await M.api(`/api/candles/${encodeURIComponent(v.sym)}?range=${range}`); }
    catch (e) { d = { range, candles: [], problems: ["Could not load the chart data."] }; }
    if (tok !== v.token || !views.has(v.sym)) return;
    v.data = d;
    draw(v);
  }

  function setOpen(tr, on) {
    const sym = tr.dataset.symbol, btn = tr.querySelector(".bc-btn");
    if (on && !views.has(sym)) {
      const v = makeView(tr);
      views.set(sym, v);
      load(v);
    } else if (!on && views.has(sym)) {
      const v = views.get(sym);
      v.token++;
      v.chart.remove();
      v.row.remove();
      views.delete(sym);
    }
    tr.classList.toggle("bc-open", on);
    if (btn) { btn.setAttribute("aria-expanded", on); btn.title = on ? "Hide chart" : "Show chart"; }
  }
  function remember() { prefs.open = [...views.keys()]; save(); syncTools(); }

  const rows = () => [...table.querySelectorAll("tbody tr[data-symbol]")];
  table.addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[data-symbol]");
    if (!tr || ev.target.closest("a")) return;  // the symbol link still opens the asset page
    if (window.getSelection && String(window.getSelection()).length) return;  // selecting text, not toggling
    setOpen(tr, !views.has(tr.dataset.symbol));
    remember();
  });

  function syncTools() {
    const mark = (id, val) => document.querySelectorAll(`#${id} button`).forEach((b) => { b.classList.toggle("active", b.dataset.v === val); b.setAttribute("aria-pressed", b.dataset.v === val); });
    mark("bc-type", prefs.type);
    mark("bc-range", prefs.range);
    const all = rows().length && rows().every((tr) => views.has(tr.dataset.symbol));
    document.getElementById("bc-all").textContent = all ? "Close all charts" : "Open all charts";
  }
  document.querySelectorAll("#bc-type button").forEach((b) => b.addEventListener("click", () => {
    prefs.type = b.dataset.v; save(); syncTools();
    views.forEach((v) => { if (v.data) draw(v); });
  }));
  document.querySelectorAll("#bc-range button").forEach((b) => b.addEventListener("click", () => {
    prefs.range = b.dataset.v; save(); syncTools();
    views.forEach(load);
  }));
  document.getElementById("bc-all").addEventListener("click", () => {
    const all = rows().every((tr) => views.has(tr.dataset.symbol));
    rows().forEach((tr) => setOpen(tr, !all));
    remember();
  });

  // Keep the newest bar in step with the live price, only when the chart and the price come from the SAME
  // source and symbol (never mixing two instruments in one bar).
  M.onLive((msg) => {
    if (msg.type !== "snapshot" && msg.type !== "quotes") return;
    for (const q of msg.quotes) {
      const v = views.get(q.symbol);
      if (!v || !v.series || !v.data || v.data.close_only || q.price === null || q.price === undefined || !q.source_timestamp) continue;
      if (q.provider !== v.data.provider || q.provider_symbol !== v.data.symbol) continue;
      const cs = v.data.candles, last = cs[cs.length - 1], step = STEP[v.data.timeframe];
      const t = Math.floor(Date.parse(q.source_timestamp) / 1000);
      if (!last || !step || t < last.time || t >= last.time + step) continue;
      last.close = q.price; last.high = Math.max(last.high, q.price); last.low = Math.min(last.low, q.price);
      v.series.update(prefs.type === "line" ? { time: last.time, value: last.close } : { time: last.time, open: last.open, high: last.high, low: last.low, close: last.close });
      legend(v, cs.length - 1);
    }
  });

  rows().forEach((tr) => { if (prefs.open.includes(tr.dataset.symbol)) setOpen(tr, true); });
  syncTools();
})();
