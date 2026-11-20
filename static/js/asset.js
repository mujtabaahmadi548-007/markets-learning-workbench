// Asset page: live price, real-candle chart (TradingView Lightweight Charts), key numbers, source chain.
(function () {
  "use strict";
  const M = window.MLW;
  const symbol = document.getElementById("hero").dataset.symbol;
  let quote = null, chartData = null, range = "1D";
  let chart = null, series = null, volume = null;

  const JKT_AXIS_TIME = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Jakarta", hour: "numeric", minute: "2-digit", hour12: true });
  const JKT_AXIS_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", day: "numeric", month: "short" });
  const UTC_AXIS_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "numeric", month: "short", year: "2-digit" });
  const JKT_FULL_DATE = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", day: "numeric", month: "short", year: "numeric" });
  const JKT_FULL = { format: (d) => JKT_FULL_DATE.format(d) + ", " + JKT_AXIS_TIME.format(d) };

  // ---------------------------------------------------------------- chart
  function ensureChart() {
    if (chart) return;
    const el = document.getElementById("chart");
    chart = LightweightCharts.createChart(el, {
      autoSize: true,
      layout: { background: { type: "solid", color: "#FFFFFF" }, textColor: "#33445C", fontFamily: "Inter, Segoe UI, system-ui, sans-serif" },
      grid: { vertLines: { color: "#EEF2F8" }, horzLines: { color: "#EEF2F8" } },
      rightPriceScale: { borderColor: "#E3E9F2" },
      timeScale: {
        borderColor: "#E3E9F2", timeVisible: true, secondsVisible: false,
        // Axis labels in Jakarta time (daily data is labelled by its date)
        tickMarkFormatter: (t, type) => {
          const d = new Date(t * 1000);
          if (chartData && chartData.timeframe === "1d") return UTC_AXIS_DAY.format(d);
          return type >= 3 ? JKT_AXIS_TIME.format(d) : JKT_AXIS_DAY.format(d);
        },
      },
      localization: {
        timeFormatter: (t) => {
          const d = new Date(t * 1000);
          return chartData && chartData.timeframe === "1d" ? UTC_AXIS_DAY.format(d) : JKT_FULL.format(d);
        },
        priceFormatter: (p) => quote ? M.fmtNum(p, quote.decimals) : String(p),
      },
      crosshair: { mode: 0 },
    });
  }

  function drawChart(data) {
    ensureChart();
    if (series) { chart.removeSeries(series); series = null; }
    if (volume) { chart.removeSeries(volume); volume = null; }
    const c = data.candles || [];
    if (data.close_only) {
      series = chart.addLineSeries({ color: "#1D4ED8", lineWidth: 2 });
      series.setData(c.map((x) => ({ time: x.time, value: x.close })));
    } else {
      series = chart.addCandlestickSeries({ upColor: "#16A34A", downColor: "#DC2626", borderVisible: false,
        wickUpColor: "#16A34A", wickDownColor: "#DC2626" });
      series.setData(c.map((x) => ({ time: x.time, open: x.open, high: x.high, low: x.low, close: x.close })));
      if (c.some((x) => x.volume)) {
        volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", color: "#C7D5EA" });
        chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
        volume.setData(c.filter((x) => x.volume !== null).map((x) => ({ time: x.time, value: x.volume, color: x.close >= x.open ? "#BFE5CC" : "#F5C6C6" })));
      }
    }
    chart.timeScale().fitContent();
  }

  async function loadChart() {
    document.getElementById("chart-caption").textContent = "Loading chart…";
    try {
      chartData = await M.api(`/api/candles/${encodeURIComponent(symbol)}?range=${range}`);
    } catch (e) {
      chartData = { candles: [], problems: ["Could not load the chart data."] };
    }
    const cap = document.getElementById("chart-caption"), warn = document.getElementById("chart-warning");
    if (!chartData.candles || !chartData.candles.length) {
      if (series) { chart.removeSeries(series); series = null; }
      if (volume) { chart.removeSeries(volume); volume = null; }
      cap.textContent = "No chart data.";
      warn.hidden = false;
      warn.textContent = (chartData.problems || []).join(" · ");
      renderStats();
      return;
    }
    drawChart(chartData);
    const tfName = { "5m": "5-minute", "1h": "1-hour", "1d": "daily" }[chartData.timeframe] || chartData.timeframe;
    cap.innerHTML = `Chart: <strong>${M.esc(chartData.source)}</strong> — ${M.esc(chartData.note || chartData.symbol)}. ` +
      `${chartData.close_only ? "Daily closing values (line)" : tfName + " candles"}, ${chartData.candles.length} points. Times are Jakarta time (UTC+7).`;
    const notes = [];
    if (chartData.is_proxy) notes.push("PROXY: this chart shows a stand-in instrument, not the asset itself. It moves closely with it but can differ.");
    if (chartData.is_unofficial) notes.push("Unofficial source (Yahoo).");
    if (chartData.daily_only_note) notes.push("This source has daily values only, so a longer stretch of daily data is shown.");
    if (chartData.problems && chartData.problems.length) notes.push("Skipped sources: " + chartData.problems.join(" · "));
    warn.hidden = !notes.length;
    warn.textContent = notes.join(" ");
    renderStats();
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
    series.update({ time: last.time, open: last.open, high: last.high, low: last.low, close: last.close });
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
      rows.push([`${range} high (from ${src})`, M.fmtNum(hi, d)]);
      rows.push([`${range} low (from ${src})`, M.fmtNum(lo, d)]);
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
