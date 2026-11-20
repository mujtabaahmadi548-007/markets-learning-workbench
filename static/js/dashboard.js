// Dashboard: live price board, market sessions, biggest movers, sparklines.
(function () {
  "use strict";
  const M = window.MLW;
  const quotes = new Map(); // id -> latest view from the server
  const rows = new Map(); // id -> row element
  let sparks = {};

  function sparkSvg(id) {
    const s = sparks[id];
    if (!s || !s.points || s.points.length < 2) return '<span class="muted small">—</span>';
    const pts = s.points, w = 110, h = 28;
    const min = Math.min(...pts), max = Math.max(...pts), span = max - min || 1;
    const d = pts.map((v, i) => `${(i / (pts.length - 1) * w).toFixed(1)},${(h - 2 - (v - min) / span * (h - 4)).toFixed(1)}`).join(" ");
    const cls = pts[pts.length - 1] >= pts[0] ? "spark-up" : "spark-down";
    const title = `${s.range} chart from ${s.source}${s.is_proxy ? " (proxy)" : ""}`;
    return `<svg class="spark ${cls}" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${M.esc(title)}"><title>${M.esc(title)}</title><polyline points="${d}"/></svg>`;
  }

  // Cell contents for one row (each cell is only rewritten when its HTML changes).
  function cells(q) {
    const ch = M.fmtChange(q), b = M.badge(q), up = M.updatedText(q);
    const unit = M.unitOf(q);
    return {
      price: `${M.fmtPrice(q)}${unit ? ` <span class="unit">${M.esc(unit)}</span>` : ""}`,
      change: `<span class="${ch.cls}" title="${M.esc(ch.title)}">${ch.arrow} ${ch.text}</span>${ch.basis ? `<span class="basis">${M.esc(ch.basis)}</span>` : ""}`,
      range: (q.day_low !== null && q.day_high !== null) ? `${M.fmtNum(q.day_low, q.decimals)} – ${M.fmtNum(q.day_high, q.decimals)}` : "—",
      spark: sparkSvg(q.id),
      badge: `<span class="badge ${b.cls}" title="${M.esc(b.title)}">${M.esc(b.text)}</span>`,
      source: `<span title="${M.esc(q.source_note || "")}">${M.esc(q.source || "—")}</span> ${M.sourceFlags(q)}<br>` +
              `<span class="muted" title="${M.esc(up.title)}">${M.esc(up.text)}</span>`,
    };
  }

  function makeRow(q) {
    const row = document.createElement("div");
    row.className = "board-row";
    row.setAttribute("role", "row");
    row.innerHTML = `
      <span class="cell-asset" role="cell"><a href="${M.base}/asset/${encodeURIComponent(q.symbol)}"><strong>${M.esc(q.symbol)}</strong></a>
        <span class="muted small">${M.esc(q.name)}</span></span>
      <span class="r num price" role="cell" data-f="price"></span>
      <span class="r num" role="cell" data-f="change"></span>
      <span class="r num hide-sm small" role="cell" data-f="range"></span>
      <span class="hide-sm" role="cell" data-f="spark"></span>
      <span role="cell" data-f="badge"></span>
      <span class="hide-sm small" role="cell" data-f="source"></span>`;
    return row;
  }

  function paint(row, q) {
    const c = cells(q);
    for (const [k, html] of Object.entries(c)) {
      const el = row.querySelector(`[data-f="${k}"]`);
      if (el && el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
    }
  }

  function upsert(q) {
    const prev = quotes.get(q.id);
    quotes.set(q.id, q);
    let row = rows.get(q.id);
    if (!row) {
      const body = document.querySelector(`[data-body="${q.asset_class}"]`);
      if (!body) return;
      body.closest(".board-group").hidden = false;
      row = makeRow(q);
      body.appendChild(row);
      rows.set(q.id, row);
    }
    paint(row, q);
    if (prev && prev.price !== null && q.price !== null && prev.price !== q.price) {
      const cell = row.querySelector('[data-f="price"]');
      cell.classList.remove("flash-up", "flash-down");
      void cell.offsetWidth; // restart the animation
      cell.classList.add(q.price > prev.price ? "flash-up" : "flash-down");
    }
  }

  function renderSessions() {
    const seen = new Map();
    for (const q of quotes.values()) if (q.market && !seen.has(q.market.schedule)) seen.set(q.market.schedule, q.market);
    const names = { crypto_24_7: "Crypto", us_equity: "US stocks", futures_24_5: "Oil & futures", spot_24_5: "Gold & silver" };
    const html = [];
    for (const [sched, m] of seen) {
      let title = m.reason;
      if (!m.open && m.next_open) title += ". Opens " + M.dayJakarta(new Date(m.next_open));
      const detail = sched === "crypto_24_7" ? "Open 24/7" : (m.open ? "Open" : "Closed");
      html.push(`<span class="session ${m.open ? "open" : "closed"}" title="${M.esc(title)}">${m.open ? "●" : "○"} ${names[sched] || sched}: ${detail}</span>`);
    }
    const el = document.getElementById("sessions");
    const out = html.join("");
    if (el.dataset.html !== out) { el.innerHTML = out; el.dataset.html = out; }
  }

  function renderMovers() {
    const list = [...quotes.values()].filter((q) => q.change_pct !== null && q.change_pct !== undefined && q.currency !== "%"
      && ["LIVE", "DELAYED", "MARKET_CLOSED"].includes(q.status) && q.kind !== "daily");
    const el = document.getElementById("movers");
    if (!list.length) { el.innerHTML = '<p class="muted">Waiting for prices…</p>'; return; }
    list.sort((a, b) => b.change_pct - a.change_pct);
    // Only real rises under "Up most" and real falls under "Down most" (moves under 0.005 % count as flat).
    const top = list.filter((q) => q.change_pct >= 0.005).slice(0, 3);
    const bottom = list.filter((q) => q.change_pct <= -0.005).reverse().slice(0, 3);
    const none = (txt) => `<p class="muted small">${txt}</p>`;
    const item = (q) => {
      const ch = M.fmtChange(q);
      return `<div class="mover"><a href="${M.base}/asset/${encodeURIComponent(q.symbol)}"><strong>${M.esc(q.symbol)}</strong></a>
        <span class="num ${ch.cls}">${ch.arrow} ${ch.cls === "up" ? "+" : ch.cls === "down" ? "−" : ""}${M.fmtNum(Math.abs(q.change_pct), 2)}%</span>
        <span class="muted small">${M.esc(ch.basis)}</span>
        <a class="btn btn-sm" href="${M.base}/asset/${encodeURIComponent(q.symbol)}?why=1#why">Why?</a></div>`;
    };
    const out = `<div class="mover-col"><span class="muted small">Up most</span>${top.length ? top.map(item).join("") : none("Nothing is up right now.")}</div>
      <div class="mover-col"><span class="muted small">Down most</span>${bottom.length ? bottom.map(item).join("") : none("Nothing is down right now.")}</div>`;
    if (el.dataset.html !== out) { el.innerHTML = out; el.dataset.html = out; }
  }

  function onMessage(msg) {
    if (msg.type === "snapshot" || msg.type === "quotes") {
      msg.quotes.forEach(upsert);
      renderSessions();
      renderMovers();
    }
  }

  // Every second: refresh ticking texts (ages in badges) - only changed cells are rewritten.
  setInterval(() => { for (const [id, q] of quotes) { const r = rows.get(id); if (r) paint(r, q); } }, 1000);

  async function loadSparks() {
    try {
      sparks = (await M.api("/api/sparklines")).sparklines || {};
      for (const [id, q] of quotes) { const r2 = rows.get(id); if (r2) paint(r2, q); }
    } catch (e) { /* try again next time */ }
  }
  async function loadNews() {
    const box = document.getElementById("top-news");
    try {
      const d = await M.api("/api/news?sort=important&hours=24&limit=6&distinct=true");
      box.innerHTML = d.articles.length ? d.articles.map((a) => M.renderArticle(a, { card: true })).join("")
        : '<p class="muted small">No news yet - the first fetch runs a few seconds after start.</p>';
    } catch (e) { box.innerHTML = `<p class="warn-text small">${M.esc(e.message)}</p>`; }
  }
  loadNews();
  setInterval(loadNews, 120000);
  M.onLive((msg) => { if (msg.type === "news") loadNews(); });

  loadSparks();
  setTimeout(loadSparks, 15000);
  setInterval(loadSparks, 60000);
  setInterval(renderMovers, 5000);

  M.onLive(onMessage);
})();
