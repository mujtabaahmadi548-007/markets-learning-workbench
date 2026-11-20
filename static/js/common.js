// Shared browser helpers: Jakarta time display (UTC on hover), number formatting, status badges,
// and the live WebSocket connection (with the connection indicator in the top bar).
(function () {
  "use strict";

  // Website (GitHub Pages) mode: data comes from pre-built JSON files and your progress lives in this browser.
  const STATIC = !!window.MLW_STATIC;
  const BASE = window.MLW_BASE || "";

  const JKT = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta", day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  const JKT_SHORT = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  const JKT_TIME = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const UTC_DATE = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" });
  const JKT_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });

  function formatJakarta(date) { return JKT.format(date).replace(/(\d{4}),? /, "$1, ") + " WIB"; }
  function utcLabel(date) { return date.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC"); }

  // Short time for tables: today -> "03:15:09 WIB", other days -> "19 Nov, 03:15:09 WIB"
  function shortJakarta(date) {
    const today = JKT_SHORT.format(new Date()).slice(0, 6);
    const s = JKT_SHORT.format(date);
    return (s.slice(0, 6) === today ? JKT_TIME.format(date) : s.replace(",", "")) + " WIB";
  }
  function dayJakarta(date) { return JKT_DAY.format(date).replace(",", "") + " WIB"; }
  function dateOnly(date) { return UTC_DATE.format(date); } // daily data: the date the value belongs to

  function renderTimes(root) {
    (root || document).querySelectorAll("time[data-utc]").forEach((el) => {
      const iso = el.getAttribute("data-utc");
      if (!iso) return;
      const d = new Date(iso);
      if (isNaN(d)) return;
      el.textContent = formatJakarta(d);
      el.title = utcLabel(d);
      el.setAttribute("datetime", d.toISOString());
    });
  }

  function tickClocks() {
    const now = new Date();
    document.querySelectorAll("time.js-clock").forEach((el) => { el.textContent = formatJakarta(now); el.title = utcLabel(now); });
  }

  // ---------------------------------------------------------------- numbers
  function fmtNum(v, decimals) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }
  function fmtVolume(v) {
    if (v === null || v === undefined) return "—";
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(2) + " B";
    if (a >= 1e6) return (v / 1e6).toFixed(2) + " M";
    if (a >= 1e3) return (v / 1e3).toFixed(1) + " K";
    return fmtNum(v, 2);
  }
  function fmtPrice(q) {
    if (q.price === null || q.price === undefined) return "—";
    const s = fmtNum(q.price, q.decimals);
    return q.currency === "%" ? s + "%" : s;
  }
  function unitOf(q) {
    if (q.currency === "%") return "";
    return q.quote_currency || q.currency || "";
  }
  const BASIS = { "24h": "24h", "prev_close": "vs prev close", "prev_obs": "vs prev day", "24h_recorded": "24h (recorded here)" };
  // Change text with arrow (never color alone). Yields: change in percentage points.
  function fmtChange(q) {
    if (q.change === null || q.change === undefined) {
      return { text: "—", cls: "", arrow: "", basis: "", title: q.provider === "goldapi" || q.kind === "stream"
        ? "No change yet: this source has no previous close. The app shows a 24h change once it has recorded prices for 24 hours."
        : "No change available from this source" };
    }
    // A move that rounds to 0.00% is shown as flat (■), not as a tiny rise or fall.
    const tiny = q.change_pct !== null && q.change_pct !== undefined && Math.abs(q.change_pct) < 0.005;
    const up = !tiny && q.change > 0, down = !tiny && q.change < 0;
    const arrow = up ? "▲" : down ? "▼" : "■";
    const cls = up ? "up" : down ? "down" : "flat";
    const sign = up ? "+" : down ? "−" : "";
    let text;
    if (q.currency === "%") {
      text = `${sign}${fmtNum(Math.abs(q.change), 2)} pp`;
    } else {
      const pct = q.change_pct === null || q.change_pct === undefined ? "" : ` (${sign}${fmtNum(Math.abs(q.change_pct), 2)}%)`;
      text = `${sign}${fmtNum(Math.abs(q.change), q.decimals)}${pct}`;
    }
    return { text, cls, arrow, basis: BASIS[q.change_basis] || "", title: "" };
  }
  function fmtAge(sec) {
    sec = Math.max(0, Math.floor(sec));
    if (sec < 60) return sec + " s";
    if (sec < 3600) return Math.floor(sec / 60) + " min";
    if (sec < 86400) { const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60); return m ? `${h} h ${m} min` : `${h} h`; }
    return Math.floor(sec / 86400) + " d";
  }

  // ---------------------------------------------------------------- server clock + badges
  let clockOffset = 0; // serverNow - clientNow (ms)
  function noteServerTime(iso) { const t = Date.parse(iso); if (!isNaN(t)) clockOffset = t - Date.now(); }
  function serverNow() { return Date.now() + clockOffset; }

  function badge(q) {
    const age = q.source_timestamp ? (serverNow() - Date.parse(q.source_timestamp)) / 1000 : null;
    let text, cls;
    switch (q.status) {
      case "LIVE": text = "LIVE"; cls = "badge-live"; break;
      case "DELAYED":
        cls = "badge-delayed";
        if (q.kind === "daily" && q.source_timestamp) text = "DAILY · " + dateOnly(new Date(q.source_timestamp));
        else text = "DELAYED" + (age !== null ? " · " + fmtAge(age) : "");
        break;
      case "MARKET_CLOSED": text = "MARKET CLOSED"; cls = "badge-closed"; break;
      case "STALE": text = "STALE" + (age !== null ? " · " + fmtAge(age) : ""); cls = "badge-stale"; break;
      case "ERROR": text = "ERROR"; cls = "badge-error"; break;
      case "UNAVAILABLE": text = "N/A"; cls = "badge-closed"; break;
      default: text = "CONNECTING…"; cls = "badge-closed";
    }
    let title = q.status_detail || "";
    if (q.status === "MARKET_CLOSED" && q.market) {
      title = `${q.market.reason}.` + (q.market.last_close ? ` Closed ${dayJakarta(new Date(q.market.last_close))}.` : "") +
        (q.market.next_open ? ` Opens ${dayJakarta(new Date(q.market.next_open))}.` : "");
    }
    if (q.error_message) title += (title ? " — " : "") + q.error_message;
    return { text, cls, title };
  }

  function updatedText(q) {
    if (!q.source_timestamp) return { text: "—", title: "" };
    const d = new Date(q.source_timestamp);
    if (q.kind === "daily") return { text: "value for " + dateOnly(d), title: "Daily data: only the date is known" };
    return { text: shortJakarta(d), title: utcLabel(d) };
  }

  function sourceFlags(q) {
    const f = [];
    if (q.fallback) f.push('<span class="chip chip-warn" title="The main source is not working; a backup source is in use">fallback</span>');
    if (q.is_unofficial) f.push('<span class="chip" title="Unofficial source (Yahoo). Personal use only; may be delayed">unofficial</span>');
    if (q.is_proxy) f.push('<span class="chip" title="Stand-in instrument, not the asset itself">proxy</span>');
    return f.join(" ");
  }

  function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  // ---------------------------------------------------------------- live connection
  const conn = document.getElementById("conn");
  function setConn(state, text) {
    if (!conn) return;
    conn.classList.toggle("ok", state === "ok");
    conn.classList.toggle("bad", state === "bad");
    conn.querySelector(".conn-text").textContent = text;
  }

  const listeners = [];
  let ws = null, retry = 1000, pingTimer = null;
  function connectLive() {
    if (STATIC) {  // website: snapshot files + crypto streamed straight from Binance into this browser
      ws = { static: true };
      window.MLW_STATIC_LIVE((msg) => { if (msg.server_time) noteServerTime(msg.server_time); listeners.forEach((fn) => fn(msg)); }, setConn);
      return;
    }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => {
      retry = 1000; setConn("ok", "Live connection");
      clearInterval(pingTimer);
      pingTimer = setInterval(() => { try { ws.send("ping"); } catch (e) { /* reconnect handles it */ } }, 30000);
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.server_time) noteServerTime(msg.server_time);
      listeners.forEach((fn) => fn(msg));
    };
    ws.onclose = () => {
      clearInterval(pingTimer);
      setConn("bad", "Reconnecting…");
      setTimeout(connectLive, retry);
      retry = Math.min(retry * 2, 15000);
    };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* ignore */ } };
  }
  function onLive(fn) { listeners.push(fn); if (!ws) connectLive(); }

  // ---------------------------------------------------------------- toasts + price-alert notifications
  function toast(html, kind) {
    const box = document.getElementById("toasts");
    if (!box) return;
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.innerHTML = html + '<button type="button" class="toast-x" aria-label="Close">×</button>';
    el.querySelector(".toast-x").onclick = () => el.remove();
    box.appendChild(el);
    setTimeout(() => el.remove(), 20000);
  }
  function onAlert(a) {
    const price = fmtNum(a.price, a.decimals);
    const what = a.condition === "pct_move" ? `moved ${fmtNum(a.change_pct, 2)}% (alert: ±${a.value}%)`
      : `${a.condition === "above" ? "rose to" : "fell to"} ${price} (alert: ${a.condition} ${fmtNum(a.value, a.decimals)})`;
    const text = `${a.symbol} ${what}. Source: ${a.source}.`;
    toast(`<strong>Price alert</strong><br>${esc(text)}`, "toast-alert");
    try {
      if ("Notification" in window && Notification.permission === "granted") new Notification("Price alert: " + a.symbol, { body: text });
    } catch (e) { /* notifications not available */ }
  }
  listeners.push((msg) => { if (msg.type === "alert" && msg.alert) onAlert(msg.alert); });

  // POST/GET JSON helper used by the pages; shows the server's error message on failure.
  async function api(url, opts) {
    if (STATIC) return window.MLW_STATIC_API(url, opts || {});
    const o = Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {});
    if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
    const r = await fetch(url, o);
    let data = null;
    try { data = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${r.status}`);
    return data;
  }

  // Article image: the publisher's own image when known; otherwise (or if it fails to load) a topic
  // illustration that is clearly labelled "illustration".
  function illustration(topic) { return `${BASE}/static/img/topics/${encodeURIComponent(topic || "markets")}.svg`; }
  function thumbHtml(a, cls) {
    const ill = illustration(a.topic);
    if (a.image_url) {
      return `<a class="${cls || "thumb"}" href="${BASE}/news/${a.id}" tabindex="-1" aria-hidden="true"><img src="${esc(a.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-fallback="${ill}"></a>`;
    }
    return `<a class="${cls || "thumb"}" href="${BASE}/news/${a.id}" tabindex="-1" aria-hidden="true"><img src="${ill}" alt="" loading="lazy"><span class="illus-tag">illustration</span></a>`;
  }
  // Any publisher image that fails to load (blocked, removed) switches to the labelled illustration.
  document.addEventListener("error", (ev) => {
    const img = ev.target;
    if (img && img.tagName === "IMG" && img.dataset.fallback && !img.dataset.fellBack) {
      img.dataset.fellBack = "1";
      img.src = img.dataset.fallback;
      img.insertAdjacentHTML("afterend", '<span class="illus-tag">illustration</span>');
    }
  }, true);

  // One news item (used by the News page, dashboard and asset pages). opts.card = grid card with a large image.
  function renderArticle(a, opts) {
    const t = a.published_at ? new Date(a.published_at) : null;
    const time = t ? `<span title="${esc(utcLabel(t))}">${esc(shortJakarta(t))}</span>` : '<span class="muted">time not given</span>';
    const tags = (a.tags || []).filter((x) => x.confidence >= 0.5).slice(0, 6)
      .map((x) => `<a class="chip tag" href="${BASE}/asset/${encodeURIComponent(x.symbol)}" title="${x.method} tag, confidence ${x.confidence}">${esc(x.symbol)}</a>`).join(" ");
    const bd = a.has_breakdown ? '<span class="chip chip-ok">AI breakdown</span>' : "";
    const off = a.is_official ? '<span class="chip chip-ok">official</span>' : "";
    const outlets = a.outlets > 1 ? `<span class="chip" title="Number of outlets covering this story">${a.outlets} outlets</span>` : "";
    const imp = `<span class="imp" title="Importance ${a.importance} / 100 (source quality, outlets, relevance, price move)"><span style="width:${Math.min(100, a.importance)}%"></span></span>`;
    const compact = opts && opts.compact;
    const desc = a.description && !compact ? `<p class="small muted desc">${esc(a.description.slice(0, 280))}${a.description.length > 280 ? "…" : ""}</p>` : "";
    const ext = `<a class="small" href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">Original ↗</a>`;
    if (opts && opts.card) {
      return `<article class="news-card">${thumbHtml(a, "thumb")}
        <div class="nc-body">
          <a class="news-title" href="${BASE}/news/${a.id}">${esc(a.title)}</a>
          <div class="small news-meta"><strong>${esc(a.source_name)}</strong> · ${time} · ${ext}</div>
          <div class="news-tags">${tags} ${bd} ${outlets}</div>
        </div></article>`;
    }
    return `<article class="news-item${compact ? " compact" : ""}">
      ${thumbHtml(a)}
      <div class="news-main">
        <a class="news-title" href="${BASE}/news/${a.id}">${esc(a.title)}</a>
        <div class="small news-meta"><strong>${esc(a.source_name)}</strong> · ${time} · ${ext} ${off} ${bd} ${outlets}</div>
        ${desc}
        <div class="news-tags">${tags}</div>
      </div>
      ${imp}
    </article>`;
  }

  window.MLW = { formatJakarta, utcLabel, shortJakarta, dayJakarta, dateOnly, renderTimes, fmtNum, fmtVolume, fmtPrice, unitOf,
    fmtChange, fmtAge, badge, updatedText, sourceFlags, esc, onLive, serverNow, noteServerTime, toast, api, renderArticle,
    base: BASE, isStatic: STATIC };

  renderTimes();
  tickClocks();
  setInterval(tickClocks, 1000);
  // Every page keeps the live connection (prices, price alerts, news notices).
  document.addEventListener("DOMContentLoaded", () => { if (!ws) connectLive(); });
})();
