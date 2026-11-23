// Asset page: live price, real-candle chart (TradingView Lightweight Charts), key numbers, source chain.
(function () {
  "use strict";
  const M = window.MLW;
  const symbol = document.getElementById("hero").dataset.symbol;
  let quote = null, chartData = null, range = "1D";
  let chart = null, series = null, volume = null;

  // Intraday candles are labelled in Jakarta time; daily, weekly and monthly bars by their date (UTC date = the
  // market's own trading date for these sources).
  const JKT_AXIS_TIME = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Jakarta", hour: "numeric", minute: "2-digit", hour12: true });
  const JKT_AXIS_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", day: "numeric", month: "short" });
  const JKT_FULL_DATE = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", day: "numeric", month: "short", year: "numeric" });
  const JKT_FULL = { format: (d) => JKT_FULL_DATE.format(d) + ", " + JKT_AXIS_TIME.format(d) };
  const U = (o) => new Intl.DateTimeFormat("en-GB", Object.assign({ timeZone: "UTC" }, o));
  const UTC_YEAR = U({ year: "numeric" }), UTC_MON = U({ month: "short" }), UTC_MON_Y = U({ month: "short", year: "numeric" });
  const UTC_DAY = U({ day: "numeric", month: "short" }), UTC_DATE = U({ weekday: "short", day: "numeric", month: "short", year: "numeric" });
  const UTC_DMY = U({ day: "numeric", month: "short", year: "numeric" });
  const DAILY_UP = new Set(["1d", "1wk", "1mo"]);
  const isDailyUp = () => chartData && DAILY_UP.has(chartData.timeframe);
  const TF_NAME = { "5m": "5-minute", "1h": "1-hour", "1d": "daily", "1wk": "weekly", "1mo": "monthly" };

  // Chart preferences (kept in this browser): style, scale, moving averages, volume.
  const PREF_KEY = "mlw-chart-prefs-v1";
  const prefs = Object.assign({ type: "auto", scale: "auto", ma: false, vol: true }, (() => {
    try { return JSON.parse(localStorage.getItem(PREF_KEY) || "{}"); } catch (e) { return {}; }
  })());
  const savePrefs = () => { try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch (e) { /* storage blocked */ } };
  let extra = [];  // moving-average series
  let bars = [], barIndex = new Map();

  // Prices with the asset's decimals, but 3 significant digits below 1 (split-adjusted 1980s-90s stock prices
  // such as 0.0437 would otherwise all read "0.04").
  function fp(v) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    const d = quote ? quote.decimals : 2;
    return Math.abs(v) < 1 && v !== 0 && d <= 2 ? Number(v.toPrecision(3)).toString() : M.fmtNum(v, d);
  }

  function barDate(t) {
    const d = new Date(t * 1000);
    if (!isDailyUp()) return JKT_FULL.format(d);
    if (chartData.timeframe === "1mo") return UTC_MON_Y.format(d);
    if (chartData.timeframe === "1wk") return "Week of " + UTC_DMY.format(d);
    return UTC_DATE.format(d);
  }

  // ---------------------------------------------------------------- chart
  function ensureChart() {
    if (chart) return;
    const el = document.getElementById("chart");
    chart = LightweightCharts.createChart(el, {
      autoSize: true,
      layout: { background: { type: "solid", color: "#FFFFFF" }, textColor: "#33445C", fontFamily: "Inter, Segoe UI, system-ui, sans-serif" },
      grid: { vertLines: { color: "#EEF2F8" }, horzLines: { color: "#EEF2F8" } },
      rightPriceScale: { borderColor: "#E3E9F2", scaleMargins: { top: 0.12, bottom: 0.08 } },
      timeScale: {
        // minBarSpacing below the default 0.5 px so "Reset zoom" can fit decades of bars on a phone screen
        borderColor: "#E3E9F2", timeVisible: true, secondsVisible: false, rightOffset: 3, minBarSpacing: 0.05,
        tickMarkFormatter: (t, type) => {
          const d = new Date(t * 1000);
          if (isDailyUp()) return type === 0 ? UTC_YEAR.format(d) : type === 1 ? UTC_MON.format(d) : UTC_DAY.format(d);
          return type >= 3 ? JKT_AXIS_TIME.format(d) : JKT_AXIS_DAY.format(d);
        },
      },
      localization: { timeFormatter: (t) => barDate(t), priceFormatter: (p) => fp(p) },
      crosshair: { mode: 0 },
      handleScale: { axisPressedMouseMove: { time: true, price: true } },
    });
    chart.subscribeCrosshairMove((param) => {
      const i = param && param.time !== undefined ? barIndex.get(param.time) : undefined;
      renderLegend(i === undefined ? bars.length - 1 : i);
    });
  }

  function effectiveType(data) {
    if (data.close_only) return "line";
    if (prefs.type !== "auto") return prefs.type;
    return data.range === "5Y" || data.range === "MAX" ? "area" : "candles";
  }
  function effectiveScale(data) {
    if (prefs.scale !== "auto") return prefs.scale;
    return data.range === "MAX" ? "log" : "normal";
  }

  function sma(values, n) {
    const out = [];
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      sum += values[i].close;
      if (i >= n) sum -= values[i - n].close;
      if (i >= n - 1) out.push({ time: values[i].time, value: sum / n });
    }
    return out;
  }

  function clearSeries() {
    if (series) { chart.removeSeries(series); series = null; }
    if (volume) { chart.removeSeries(volume); volume = null; }
    extra.forEach((s) => chart.removeSeries(s));
    extra = [];
  }

  function drawChart(data) {
    ensureChart();
    clearSeries();
    const c = data.candles || [];
    bars = c;
    barIndex = new Map(c.map((x, i) => [x.time, i]));
    const type = effectiveType(data), scale = effectiveScale(data);
    const rising = c.length > 1 && c[c.length - 1].close >= (c[0].open ?? c[0].close);
    const col = rising ? "#16A34A" : "#DC2626";
    if (type === "candles") {
      series = chart.addCandlestickSeries({ upColor: "#16A34A", downColor: "#DC2626", borderVisible: false,
        wickUpColor: "#16A34A", wickDownColor: "#DC2626" });
      series.setData(c.map((x) => ({ time: x.time, open: x.open, high: x.high, low: x.low, close: x.close })));
    } else if (type === "area") {
      series = chart.addAreaSeries({ lineColor: col, lineWidth: 2, topColor: rising ? "rgba(22,163,74,0.28)" : "rgba(220,38,38,0.25)",
        bottomColor: "rgba(255,255,255,0)", priceLineVisible: false });
      series.setData(c.map((x) => ({ time: x.time, value: x.close })));
    } else {
      series = chart.addLineSeries({ color: data.close_only ? "#1D4ED8" : col, lineWidth: 2, priceLineVisible: false });
      series.setData(c.map((x) => ({ time: x.time, value: x.close })));
    }
    // Price scale: normal, logarithmic (equal distances = equal % moves; best for decades of history) or % from start.
    chart.priceScale("right").applyOptions({ mode: { normal: 0, log: 1, pct: 2 }[scale] });
    if (prefs.ma && c.length >= 50) {
      for (const [n, color] of [[50, "#D97706"], [200, "#7C3AED"]]) {
        if (c.length < n) continue;
        const s = chart.addLineSeries({ color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        s.setData(sma(c, n));
        extra.push(s);
      }
    }
    if (prefs.vol && !data.close_only && c.some((x) => x.volume)) {
      volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", color: "#C7D5EA", lastValueVisible: false, priceLineVisible: false });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
      volume.setData(c.filter((x) => x.volume !== null && x.volume !== undefined).map((x) => ({ time: x.time, value: x.volume, color: x.close >= x.open ? "#BFE5CC" : "#F5C6C6" })));
    }
    chart.timeScale().fitContent();
    renderLegend(c.length - 1);
    syncTools(data);
  }

  // Legend over the chart: the bar under the cursor (or the newest one).
  function renderLegend(i) {
    const el = document.getElementById("chart-legend");
    if (!el) return;
    const b = bars[i];
    if (!b) { el.innerHTML = ""; return; }
    const f = fp;
    const prev = i > 0 ? bars[i - 1].close : null;
    const ch = prev ? (b.close / prev - 1) * 100 : null;
    const chTxt = ch === null ? "" : ` <span class="${ch >= 0 ? "up" : "down"}">${ch >= 0 ? "▲ +" : "▼ −"}${M.fmtNum(Math.abs(ch), 2)}%</span>`;
    let html = `<span class="lg-date">${M.esc(barDate(b.time))}</span>`;
    if (chartData.close_only) html += `<span class="lg-k">Close</span> ${f(b.close)}${chTxt}`;
    else html += `<span class="lg-k">O</span> ${f(b.open)} <span class="lg-k">H</span> ${f(b.high)} <span class="lg-k">L</span> ${f(b.low)} <span class="lg-k">C</span> ${f(b.close)}${chTxt}`;
    if (b.volume) html += `<span class="lg-k">Vol</span> ${M.fmtVolume(b.volume)}`;
    if (prefs.ma && extra.length) {
      const avg = (n) => (i >= n - 1 ? bars.slice(i - n + 1, i + 1).reduce((s, x) => s + x.close, 0) / n : null);
      const a50 = avg(50), a200 = avg(200);
      if (a50 !== null) html += ` <span class="ma50">MA50</span> ${f(a50)}`;
      if (a200 !== null) html += ` <span class="ma200">MA200</span> ${f(a200)}`;
    }
    el.innerHTML = html;
  }

  function syncTools(data) {
    const set = (id, v) => document.querySelectorAll(`#${id} button`).forEach((b) => { b.classList.toggle("active", b.dataset.v === v); b.setAttribute("aria-pressed", b.dataset.v === v); });
    set("ct-type", prefs.type);
    set("ct-scale", prefs.scale);
    document.getElementById("ct-ma").checked = !!prefs.ma;
    document.getElementById("ct-vol").checked = !!prefs.vol;
    document.querySelectorAll("#ct-type button").forEach((b) => { b.disabled = !!(data && data.close_only) && b.dataset.v !== "auto"; });
  }

  // "+412.3% over 5Y (from 31.70 on 22 Nov 2021 to 164.34 on 23 Nov 2026)"
  function renderSummary(data) {
    const el = document.getElementById("chart-summary"), c = data.candles || [];
    if (c.length < 2) { el.textContent = ""; return; }
    const first = c[0], last = c[c.length - 1], start = data.close_only ? first.close : (first.open ?? first.close);
    const pct = (last.close / start - 1) * 100, d = quote ? quote.decimals : 2;
    const day = (t) => (isDailyUp() ? UTC_DMY.format(new Date(t * 1000)) : JKT_FULL.format(new Date(t * 1000)));
    // weekly/monthly bars are stamped with their first day; the real last trading day comes with the data
    const endTime = data.last_date ? Date.parse(data.last_date + "T00:00:00Z") / 1000 : last.time;
    const years = (endTime - first.time) / (365.25 * 86400);
    const cagr = years >= 1 && start > 0 ? ` · ${M.fmtNum((Math.pow(last.close / start, 1 / years) - 1) * 100, 2)}% a year on average` : "";
    el.innerHTML = `<strong class="${pct >= 0 ? "up" : "down"}">${pct >= 0 ? "▲ +" : "▼ −"}${M.fmtNum(Math.abs(pct), 2)}%</strong> over ${M.esc(data.range === "MAX" ? "the full history" : data.range)}` +
      ` <span class="muted small">(from ${fp(start)} on ${M.esc(day(first.time))} to ${fp(last.close)} on ${M.esc(day(endTime))}${cagr})</span>`;
  }

  async function loadChart() {
    document.getElementById("chart-caption").textContent = "Loading chart…";
    const want = range;
    let data;
    try {
      data = await M.api(`/api/candles/${encodeURIComponent(symbol)}?range=${want}`);
    } catch (e) {
      data = { range: want, candles: [], problems: ["Could not load the chart data."] };
    }
    if (want !== range) return;  // another range was chosen while this one loaded
    chartData = data;
    const cap = document.getElementById("chart-caption"), warn = document.getElementById("chart-warning");
    if (!chartData.candles || !chartData.candles.length) {
      if (chart) clearSeries();
      bars = [];
      renderLegend(-1);
      document.getElementById("chart-summary").textContent = "";
      cap.textContent = "No chart data.";
      warn.hidden = false;
      warn.textContent = (chartData.problems || []).join(" · ");
      renderStats();
      return;
    }
    drawChart(chartData);
    renderSummary(chartData);
    const tfName = TF_NAME[chartData.timeframe] || chartData.timeframe;
    const since = chartData.history && chartData.range === "MAX" && chartData.first_date ? ` History starts ${M.esc(UTC_DMY.format(new Date(chartData.first_date + "T00:00:00Z")))} (${chartData.daily_points.toLocaleString("en-US")} trading days).` : "";
    cap.innerHTML = `Chart: <strong>${M.esc(chartData.source)}</strong> — ${M.esc(chartData.note || chartData.symbol)}. ` +
      `${chartData.close_only ? "Daily closing values (line)" : tfName + " bars"}, ${chartData.candles.length.toLocaleString("en-US")} points.${since} ` +
      (isDailyUp() ? "Bars are labelled by trading date." : "Times are Jakarta time (UTC+7).") + " Drag to scroll, wheel or pinch to zoom.";
    const notes = [];
    if (chartData.is_proxy) notes.push("PROXY: this chart shows a stand-in instrument, not the asset itself. It moves closely with it but can differ.");
    if (chartData.is_unofficial) notes.push("Unofficial source (Yahoo).");
    if (chartData.adjustment) notes.push(chartData.adjustment);
    if (chartData.timeframe === "1wk" || chartData.timeframe === "1mo") notes.push(`The full daily history is shown as ${tfName} bars so it loads quickly; MA 50/200 are then ${tfName} averages.`);
    if (chartData.daily_only_note) notes.push("This source has daily values only, so a longer stretch of daily data is shown.");
    if (chartData.newer_note) notes.push(chartData.newer_note);
    if (chartData.problems && chartData.problems.length) notes.push((chartData.history ? "" : "Skipped sources: ") + chartData.problems.join(" · "));
    warn.hidden = !notes.length;
    warn.textContent = notes.join(" ");
    renderStats();
  }

  function onTool(id, key) {
    document.querySelectorAll(`#${id} button`).forEach((b) => b.addEventListener("click", () => {
      prefs[key] = b.dataset.v; savePrefs();
      if (chartData && chartData.candles && chartData.candles.length) drawChart(chartData); else syncTools(chartData);
    }));
  }
  onTool("ct-type", "type");
  onTool("ct-scale", "scale");
  ["ma", "vol"].forEach((k) => document.getElementById("ct-" + k).addEventListener("change", (ev) => {
    prefs[k] = ev.target.checked; savePrefs();
    if (chartData && chartData.candles && chartData.candles.length) drawChart(chartData);
  }));
  document.getElementById("ct-fit").addEventListener("click", () => { if (chart) chart.timeScale().fitContent(); });
  syncTools(null);

  // ---------------------------------------------------------------- performance over the full history
  async function loadPerformance() {
    const box = document.getElementById("perf-box");
    if (!box) return;
    let p;
    try { p = await M.api(`/api/assets/${encodeURIComponent(symbol)}/performance`); }
    catch (e) { box.innerHTML = `<p class="muted small">${M.esc(e.message)}</p>`; return; }
    if (!p.available) { box.innerHTML = `<p class="muted small">${M.esc((p.problems || []).join(" · ") || "Not available.")}</p>`; return; }
    const d = quote ? quote.decimals : 2;
    const day = (s) => UTC_DMY.format(new Date(s + "T00:00:00Z"));
    const pc = (v) => (v === null || v === undefined ? "—" : `<span class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}${M.fmtNum(Math.abs(v), 2)}%</span>`);
    const rows = p.periods.map((r) => `<tr><td>${M.esc(r.label)}</td><td class="small muted">${M.esc(day(r.start_date))} · ${fp(r.start_close)}</td><td class="num">${pc(r.change_pct)}</td><td class="num">${pc(r.cagr_pct)}</td></tr>`).join("");
    box.innerHTML = `<div class="table-wrap"><table class="table perf-table"><thead><tr><th>Period</th><th>Start (close)</th><th class="r">Change</th><th class="r" title="Compound annual growth rate">Per year</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="small">Highest close <strong>${fp(p.highest_close.close)}</strong> on ${M.esc(day(p.highest_close.date))} · now ${pc(p.from_high_pct)} from it.
        Lowest close ${fp(p.lowest_close.close)} on ${M.esc(day(p.lowest_close.date))}.</p>
      <p class="small muted">${M.esc(p.basis)} From ${M.esc(day(p.first_date))} to ${M.esc(day(p.last_date))}: ${p.days.toLocaleString("en-US")} trading days (${p.years} years).
        Source: ${M.esc(p.source)} — ${M.esc(p.note || p.symbol)}${p.is_proxy ? " (PROXY: a stand-in instrument)" : ""}.${(p.problems || []).length ? " " + M.esc(p.problems.join(" · ")) : ""}</p>`;
  }

  // Keep the newest candle in step with the live price, but only when the chart and the price come from
  // the SAME source and symbol (never mixing instruments).
  function liveUpdateChart() {
    if (!series || !chartData || !quote || chartData.close_only || !chartData.candles.length) return;
    if (quote.provider !== chartData.provider || quote.provider_symbol !== chartData.symbol || quote.price === null) return;
    const last = chartData.candles[chartData.candles.length - 1];
    const step = { "5m": 300, "1h": 3600, "1d": 86400 }[chartData.timeframe];
    const t = Math.floor(Date.parse(quote.source_timestamp) / 1000);
    if (!step || t < last.time || t >= last.time + step) return; // outside the newest candle: wait for the next refresh
    last.close = quote.price;
    last.high = Math.max(last.high, quote.price);
    last.low = Math.min(last.low, quote.price);
    if (effectiveType(chartData) === "candles") series.update({ time: last.time, open: last.open, high: last.high, low: last.low, close: last.close });
    else series.update({ time: last.time, value: last.close });
    renderLegend(bars.length - 1);
  }

  // ---------------------------------------------------------------- price + stats
  function renderQuote() {
    if (!quote) return;
    const ch = M.fmtChange(quote), b = M.badge(quote), up = M.updatedText(quote);
    document.getElementById("price").textContent = M.fmtPrice(quote);
    document.getElementById("unit").textContent = M.unitOf(quote);
    const chEl = document.getElementById("change");
    chEl.className = "num " + ch.cls;
    chEl.innerHTML = `${ch.arrow} ${ch.text} <span class="basis">${M.esc(ch.basis)}</span>`;
    chEl.title = ch.title;
    const badge = document.getElementById("badge");
    badge.className = "badge " + b.cls;
    badge.textContent = b.text;
    badge.title = b.title;
    document.getElementById("source-line").innerHTML =
      `Source: <strong>${M.esc(quote.source || "—")}</strong> ${M.sourceFlags(quote)} — ${M.esc(quote.source_note || "")}<br>` +
      `<span class="muted">Price time: <span title="${M.esc(up.title)}">${M.esc(up.text)}</span>. ${M.esc(quote.status_detail || "")}</span>`;
    const m = quote.market || {};
    let sess = m.reason || "";
    if (!m.open && m.last_close) sess += `. Closed ${M.dayJakarta(new Date(m.last_close))}`;
    if (!m.open && m.next_open) sess += `. Opens ${M.dayJakarta(new Date(m.next_open))}`;
    document.getElementById("session-line").textContent = sess;
    const err = document.getElementById("error-line");
    err.hidden = !quote.error_message;
    err.textContent = quote.error_message ? "Last error: " + quote.error_message : "";
  }

  function renderStats() {
    if (!quote) return;
    const d = quote.decimals, rows = [];
    const basisName = { "24h": "Price 24 h ago", "24h_recorded": "Price 24 h ago (recorded here)", "prev_obs": "Previous day's value",
      "prev_close": "Previous close" }[quote.change_basis] || "Previous close";
    rows.push([basisName, M.fmtNum(quote.prev_close, d)]);
    rows.push([quote.change_basis === "24h" ? "24 h high" : "Day high", M.fmtNum(quote.day_high, d)]);
    rows.push([quote.change_basis === "24h" ? "24 h low" : "Day low", M.fmtNum(quote.day_low, d)]);
    rows.push([quote.change_basis === "24h" ? "24 h volume" : "Volume", M.fmtVolume(quote.volume)]);
    if (chartData && chartData.candles && chartData.candles.length) {
      const hi = Math.max(...chartData.candles.map((c) => c.high)), lo = Math.min(...chartData.candles.map((c) => c.low));
      const src = chartData.is_proxy ? "proxy chart" : "chart";
      const label = range === "MAX" ? "All-time" : range;
      rows.push([`${label} high (from ${src})`, M.fmtNum(hi, d)]);
      rows.push([`${label} low (from ${src})`, M.fmtNum(lo, d)]);
    }
    document.querySelector("#stats tbody").innerHTML = rows.map(([k, v]) => `<tr><td>${M.esc(k)}</td><td class="num r">${v}</td></tr>`).join("");
  }

  async function loadSources() {
    try {
      const d = await M.api(`/api/assets/${encodeURIComponent(symbol)}`);
      if (d.server_time) M.noteServerTime(d.server_time);
      if (!quote) { quote = d.quote; renderQuote(); renderStats(); }
      document.querySelector("#sources tbody").innerHTML = d.sources.map((s, i) => {
        const words = { IDLE: "standby", OK: "standby (working)", DISABLED: "switched off by you", DOWN: "failing", DEGRADED: "errors" };
        const state = s.active ? '<span class="pill ok">✔ in use</span>' : !s.available ? `<span class="muted">${M.esc(s.why_not)}</span>` : M.esc(words[s.health] || s.health);
        const flags = (s.unofficial ? ' <span class="chip">unofficial</span>' : "") + (s.proxy ? ' <span class="chip">proxy</span>' : "");
        return `<tr><td>${i + 1}</td><td>${M.esc(s.label)}${flags}</td><td class="small">${M.esc(s.note)}</td><td class="small">${state}</td></tr>`;
      }).join("");
    } catch (e) { /* retry on next interval */ }
  }

  document.querySelectorAll(".range-buttons button").forEach((btn) => btn.addEventListener("click", () => {
    range = btn.dataset.range;
    document.querySelectorAll(".range-buttons button").forEach((b) => { b.classList.toggle("active", b === btn); b.setAttribute("aria-pressed", b === btn); });
    loadChart();
  }));

  M.onLive((msg) => {
    if (msg.type !== "snapshot" && msg.type !== "quotes") return;
    const q = msg.quotes.find((x) => x.symbol === symbol);
    if (!q) return;
    quote = q;
    renderQuote();
    liveUpdateChart();
    if (msg.type === "snapshot") renderStats();
  });
  setInterval(() => { if (quote) { const b = M.badge(quote), el = document.getElementById("badge"); if (el.textContent !== b.text) el.textContent = b.text; } }, 1000);
  setInterval(renderStats, 5000);
  setInterval(loadSources, 10000);
  setInterval(() => { if (range === "1D") loadChart(); }, 120000);
  loadSources();
  loadChart();
  loadPerformance();

  // ---------------------------------------------------------------- news for this asset
  async function loadNews() {
    const box = document.getElementById("asset-news");
    try {
      const d = await M.api(`/api/assets/${encodeURIComponent(symbol)}/news?hours=72&limit=12`);
      box.innerHTML = d.articles.length ? d.articles.map((a) => M.renderArticle(a, { compact: true })).join("")
        : '<p class="muted small">No news tagged to this asset in the last 3 days.</p>';
    } catch (e) { box.innerHTML = `<p class="warn-text small">${M.esc(e.message)}</p>`; }
  }
  loadNews();
  M.onLive((msg) => { if (msg.type === "news") loadNews(); });

  // ---------------------------------------------------------------- why did it move?
  const conf = { low: "low", medium: "medium", high: "high" };
  function renderWhy(x) {
    const box = document.getElementById("why-box");
    if (!x) return;
    if (x.error) { box.innerHTML = `<p class="muted">${M.esc(x.error)}</p>`; return; }
    const c = x.content, arts = Object.fromEntries((c.articles || []).map((a) => [a.id, a]));
    const drivers = (c.likely_drivers || []).map((d) => `<li><strong>${M.esc(d.driver)}</strong> <span class="chip">confidence: ${conf[d.confidence] || d.confidence}</span><br>
      <span class="small">Evidence: ${d.evidence_article_ids.map((i) => arts[i] ? `<a href="${M.base}/news/${i}">${M.esc(arts[i].title)}</a> (${M.esc(arts[i].source_name)})` : `article ${i}`).join("; ")}</span></li>`).join("");
    box.innerHTML = `<p class="${c.unexplained ? "warn-text" : ""}"><strong>${M.esc(c.summary)}</strong></p>
      ${drivers ? `<ul class="drivers">${drivers}</ul>` : ""}
      ${c.note ? `<p class="small muted">${M.esc(c.note)}</p>` : ""}
      <details class="small"><summary>Data the explanation used</summary><pre class="data-block">${M.esc((c.data || []).join("\n"))}</pre></details>
      <p class="small muted">Made <span title="${M.esc(M.utcLabel(new Date(x.created_at)))}">${M.esc(M.shortJakarta(new Date(x.created_at)))}</span> · model ${M.esc(x.model)} ·
      ${x.validation_passed ? "number check passed" : "⚠ " + M.esc(c.validation_notes || "number check failed; affected sentences removed")}</p>`;
  }
  const whyBtn = document.getElementById("why-btn");
  async function runWhy(force) {
    if (!whyBtn) return;
    whyBtn.disabled = true; whyBtn.textContent = "Working… (15–30 s)";
    try { const d = await M.api(`/api/assets/${encodeURIComponent(symbol)}/why?force=${force}`, { method: "POST" }); renderWhy(d.explanation); }
    catch (e) { M.toast("Explanation failed: " + M.esc(e.message), "toast-err"); }
    whyBtn.disabled = false; whyBtn.textContent = "Explain again with the latest data";
  }
  if (whyBtn) whyBtn.onclick = () => runWhy(true);
  M.api(`/api/assets/${encodeURIComponent(symbol)}/why`).then((d) => {
    if (d.explanation) { renderWhy(d.explanation); if (whyBtn) whyBtn.textContent = "Explain again with the latest data"; }
    else if (new URLSearchParams(location.search).get("why") === "1") runWhy(false);
  }).catch(() => {});

  // ---------------------------------------------------------------- quick alert
  const form = document.getElementById("alert-form");
  if (form) form.onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      await M.api("/api/alerts", { method: "POST", body: { symbol, condition: document.getElementById("al-cond").value, value: Number(document.getElementById("al-val").value) } });
      M.toast("Alert created. Allow browser notifications on the Alerts page to get a pop-up.");
      form.reset();
    } catch (e) { M.toast("Could not create the alert: " + M.esc(e.message), "toast-err"); }
  };
})();
