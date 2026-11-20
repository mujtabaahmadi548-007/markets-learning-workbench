// Website mode (GitHub Pages). Loaded before common.js only on the published site.
// - Prices, news, charts, breakdowns: JSON files rebuilt by GitHub every ~10 minutes (real data, with timestamps).
// - Crypto prices: streamed live from Binance's public market-data feed straight into this browser.
// - Your progress (quiz answers, spaced repetition, lessons, concepts seen, price alerts) is saved in THIS browser
//   (localStorage). Use Settings → "Export my progress" to move it to another device.
(function () {
  "use strict";
  const BASE = window.MLW_BASE || "";
  const DATA = BASE + "/data/";
  const KEY = "mlw-progress-v1";

  // ---------------------------------------------------------------- progress store (this browser)
  function load() {
    try { return Object.assign({ attempts: [], schedule: {}, lessons: {}, seen: {}, alerts: [] }, JSON.parse(localStorage.getItem(KEY) || "{}")); }
    catch (e) { return { attempts: [], schedule: {}, lessons: {}, seen: {}, alerts: [] }; }
  }
  function save(p) { try { localStorage.setItem(KEY, JSON.stringify(p)); } catch (e) { /* storage blocked: progress lasts for this visit only */ } }
  let P = load();

  // ---------------------------------------------------------------- data files
  const cache = {};
  function getJSON(path, fresh) {
    if (fresh || !cache[path]) {
      cache[path] = fetch(DATA + path + (fresh ? "?t=" + Date.now() : ""), { cache: "no-cache" }).then((r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "Not available on the website yet" : "HTTP " + r.status);
        return r.json();
      });
      cache[path].catch(() => { delete cache[path]; });
    }
    return cache[path];
  }
  const NOT_HERE = "This action needs the app running on your PC. On the website it happens automatically every ~10 minutes.";

  // ---------------------------------------------------------------- news filtering (same rules as the server)
  function rankNow(a) {
    const t = Date.parse(a.published_at || a.fetched_at);
    const ageH = Math.max(0, (Date.now() - t) / 3600000);
    return a.importance * Math.exp(-ageH / 18);
  }
  async function news(q) {
    const d = await getJSON("news.json");
    const hours = Number(q.get("hours") || 24);
    const sym = q.get("symbol"), cls = q.get("asset_class"), src = q.get("source"), text = (q.get("q") || "").toLowerCase().trim();
    const hb = q.get("has_breakdown"), sort = q.get("sort") || "latest", limit = Number(q.get("limit") || 100);
    const now = Date.now();
    let out = d.articles.filter((a) => {
      const t = Date.parse(a.published_at || a.fetched_at);
      if (hours > 0 && now - t > hours * 3600000) return false;
      const strong = (a.tags || []).filter((x) => x.confidence >= 0.5);
      if (sym && !strong.some((x) => x.symbol === sym)) return false;
      if (cls && !strong.some((x) => x.asset_class === cls)) return false;
      if (src && a.source_name !== src) return false;
      if (text && !((a.title || "").toLowerCase().includes(text) || (a.description || "").toLowerCase().includes(text))) return false;
      if (hb === "yes" && !a.has_breakdown) return false;
      if (hb === "no" && a.has_breakdown) return false;
      return true;
    });
    out.sort((a, b) => Date.parse(b.published_at || b.fetched_at) - Date.parse(a.published_at || a.fetched_at));
    if (sort === "important") out.sort((a, b) => rankNow(b) - rankNow(a));
    if (q.get("distinct") === "true") {
      const seen = new Set();
      out = out.filter((a) => (seen.has(a.story) ? false : (seen.add(a.story), true)));
    }
    return { articles: out.slice(0, limit), last_run: d.built_at };
  }

  // ---------------------------------------------------------------- learning (spaced repetition, same as the app)
  function sm2(state, correct, now) {
    const q = correct ? 4 : 1;
    const ease = Math.max(1.3, (state.ease || 2.5) + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)));
    if (!correct) return { reps: 0, interval: 0, ease, next: new Date(now.getTime() + 10 * 60000).toISOString() };
    const reps = (state.reps || 0) + 1;
    const interval = reps === 1 ? 1 : reps === 2 ? 6 : Math.round(Math.max(1, state.interval || 1) * ease * 10) / 10;
    return { reps, interval, ease, next: new Date(now.getTime() + interval * 86400000).toISOString() };
  }
  async function learning() { return getJSON("learning.json"); }
  function qOut(q) {
    return { id: q.id, question: q.question, options: q.options, difficulty: q.difficulty, concepts: q.concepts, source: q.source };
  }
  function conceptStats(L) {
    const byId = Object.fromEntries(L.questions.map((q) => [q.id, q]));
    const stats = {};
    for (const at of P.attempts) {
      const q = byId[at.qid];
      for (const slug of (q && q.concepts) || []) (stats[slug] = stats[slug] || []).push(!!at.correct);
    }
    return stats;
  }
  function weakTopics(L, n) {
    const rows = Object.entries(conceptStats(L)).filter(([, v]) => v.length >= 2)
      .map(([slug, v]) => ({ slug, name: L.concepts[slug] || slug, attempts: v.length, accuracy: v.filter(Boolean).length / v.length }));
    rows.sort((a, b) => a.accuracy - b.accuracy || b.attempts - a.attempts);
    return rows.slice(0, n || 5);
  }
  async function quizNext(mode, limit) {
    const L = await learning();
    const answered = new Set(P.attempts.map((a) => a.qid));
    const now = Date.now();
    let qs;
    if (mode === "review") {
      qs = Object.entries(P.schedule).filter(([, s]) => Date.parse(s.next) <= now).sort((a, b) => Date.parse(a[1].next) - Date.parse(b[1].next))
        .map(([id]) => L.questions.find((q) => String(q.id) === id)).filter(Boolean);
    } else if (mode === "today") {
      qs = L.questions.filter((q) => q.source_type === "article" && now - Date.parse(q.created_at) <= 36 * 3600000 && !answered.has(q.id))
        .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
    } else if (mode === "weak") {
      const weak = new Set(weakTopics(L, 5).map((w) => w.slug));
      qs = L.questions.filter((q) => (q.concepts || []).some((c) => weak.has(c))).sort(() => Math.random() - 0.5);
    } else {
      qs = L.questions.filter((q) => (q.source_type === "concept" || q.source_type === "lesson") && !answered.has(q.id));
    }
    return { questions: qs.slice(0, limit || 10).map(qOut) };
  }
  async function quizAnswer(body) {
    const L = await learning();
    const q = L.questions.find((x) => x.id === body.question_id);
    if (!q) throw new Error("Question not found");
    const now = new Date();
    const correct = body.chosen === q.correct_index;
    P.attempts.push({ qid: q.id, chosen: body.chosen, correct, at: now.toISOString() });
    const st = sm2(P.schedule[q.id] || {}, correct, now);
    P.schedule[q.id] = st;
    save(P);
    return Object.assign(qOut(q), { correct, correct_index: q.correct_index, explanation: q.explanation,
      option_explanations: q.option_explanations, next_review_at: st.next, chosen: body.chosen });
  }
  async function stats() {
    const L = await learning();
    const now = Date.now();
    const byDay = {};
    const fmt = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Jakarta" });
    for (const a of P.attempts) { const d = fmt.format(new Date(a.at)); (byDay[d] = byDay[d] || []).push(!!a.correct); }
    const history = Object.entries(byDay).sort().slice(-14).map(([date, v]) => ({ date, answered: v.length, correct: v.filter(Boolean).length }));
    return {
      answered: P.attempts.length, correct: P.attempts.filter((a) => a.correct).length,
      due: Object.values(P.schedule).filter((s) => Date.parse(s.next) <= now).length, questions: L.questions.length,
      history, weak: weakTopics(L, 5), lessons_done: Object.keys(P.lessons).filter((id) => L.lessons.some((l) => String(l.id) === id)).length,
      lessons_total: L.lessons.length,
    };
  }

  // ---------------------------------------------------------------- alerts (checked in this browser)
  const CONDS = { above: "Price rises to or above", below: "Price falls to or below", pct_move: "Moves at least (±%)" };
  function alertsList() {
    return P.alerts.map((a) => Object.assign({}, a, { condition_text: CONDS[a.condition] || a.condition }))
      .sort((a, b) => (b.active - a.active) || Date.parse(b.created_at) - Date.parse(a.created_at));
  }
  function checkAlerts(q) {
    if (!q || q.price === null || !["LIVE", "DELAYED"].includes(q.status) || q.kind === "daily") return;
    let fired = false;
    for (const a of P.alerts) {
      if (!a.active || a.symbol !== q.symbol) continue;
      const hit = a.condition === "above" ? q.price >= a.value : a.condition === "below" ? q.price <= a.value
        : (q.change_pct !== null && q.change_pct !== undefined && Math.abs(q.change_pct) >= Math.abs(a.value));
      if (!hit) continue;
      a.active = false; a.triggered_at = new Date().toISOString(); a.triggered_price = q.price; fired = true;
      const M = window.MLW;
      const what = a.condition === "pct_move" ? `moved ${M.fmtNum(q.change_pct, 2)}% (alert: ±${a.value}%)`
        : `${a.condition === "above" ? "rose to" : "fell to"} ${M.fmtNum(q.price, q.decimals)} (alert: ${a.condition} ${M.fmtNum(a.value, q.decimals)})`;
      const text = `${q.symbol} ${what}. Source: ${q.source}.`;
      M.toast(`<strong>Price alert</strong><br>${M.esc(text)}`, "toast-alert");
      try { if ("Notification" in window && Notification.permission === "granted") new Notification("Price alert: " + q.symbol, { body: text }); } catch (e) { /* no notifications */ }
    }
    if (fired) save(P);
  }

  // ---------------------------------------------------------------- API shim (same URLs the app uses)
  window.MLW_STATIC_API = async function (url, opts) {
    const u = new URL(url, location.origin);
    const p = u.pathname.replace(/^\/+/, "/"), q = u.searchParams, method = (opts.method || "GET").toUpperCase();
    let body = opts.body;
    if (typeof body === "string") { try { body = JSON.parse(body); } catch (e) { /* ignore */ } }
    let m;
    if (p === "/api/news" && method === "GET") return news(q);
    if (p === "/api/news/sources") { const d = await getJSON("news.json"); return { sources: [...new Set(d.articles.map((a) => a.source_name))].sort() }; }
    if (p === "/api/sparklines") return getJSON("sparklines.json");
    if (p === "/api/quotes") return { quotes: [...LIVE.quotes.values()] };
    if ((m = p.match(/^\/api\/assets\/([^/]+)$/))) {
      const d = await getJSON(`assets/${decodeURIComponent(m[1])}.json`);
      const live = [...LIVE.quotes.values()].find((x) => x.symbol === d.quote.symbol);
      return Object.assign({}, d, { quote: live || d.quote, server_time: undefined });
    }
    if ((m = p.match(/^\/api\/candles\/([^/]+)$/))) return getJSON(`candles/${decodeURIComponent(m[1])}/${q.get("range") || "1D"}.json`);
    if ((m = p.match(/^\/api\/assets\/([^/]+)\/news$/))) {
      q.set("symbol", decodeURIComponent(m[1])); q.set("sort", "important");
      return news(q);
    }
    if ((m = p.match(/^\/api\/assets\/([^/]+)\/why$/))) {
      if (method !== "GET") throw new Error(NOT_HERE);
      try { return await getJSON(`why/${decodeURIComponent(m[1])}.json`); } catch (e) { return { explanation: null }; }
    }
    if ((m = p.match(/^\/api\/articles\/(\d+)\/moves$/))) {
      try { return await getJSON(`moves/${m[1]}.json`); }
      catch (e) { return { moves: [{ symbol: "", error: "Price moves are pre-computed on the website only for the most important recent articles." }] }; }
    }
    if (p === "/api/quiz/next") return quizNext(q.get("mode") || "today", Number(q.get("limit") || 10));
    if (p === "/api/quiz/answer") return quizAnswer(body);
    if (p === "/api/quiz/stats") return stats();
    if ((m = p.match(/^\/api\/lessons\/(\d+)\/complete$/))) {
      if (q.get("done") === "false") delete P.lessons[m[1]]; else P.lessons[m[1]] = new Date().toISOString();
      save(P);
      return { ok: true };
    }
    if (p === "/api/alerts" && method === "GET") return { alerts: alertsList() };
    if (p === "/api/alerts" && method === "POST") {
      if (!CONDS[body.condition] || !isFinite(body.value)) throw new Error("Choose a condition and a number");
      const id = (P.alerts.reduce((mx, a) => Math.max(mx, a.id), 0) || 0) + 1;
      P.alerts.push({ id, symbol: body.symbol, condition: body.condition, value: Number(body.value), active: true, created_at: new Date().toISOString() });
      save(P);
      const live = [...LIVE.quotes.values()].find((x) => x.symbol === body.symbol);
      setTimeout(() => checkAlerts(live), 50);
      return { id };
    }
    if ((m = p.match(/^\/api\/alerts\/(\d+)$/)) && method === "DELETE") {
      P.alerts = P.alerts.filter((a) => String(a.id) !== m[1]); save(P); return { ok: true };
    }
    throw new Error(NOT_HERE);
  };

  // ---------------------------------------------------------------- live layer: snapshot + Binance stream
  const LIVE = { quotes: new Map(), liveAt: new Map(), lastMsg: new Map() };
  function adapt(q) {
    // The snapshot was made on GitHub's servers; nothing in it is streaming into this page, so it is never "LIVE" here.
    const now = Date.now();
    const age = q.source_timestamp ? (now - Date.parse(q.source_timestamp)) / 1000 : null;
    if (q.status === "LIVE" || (q.status === "MARKET_CLOSED" && q.market && q.market.next_open && Date.parse(q.market.next_open) < now)) {
      q.status = "DELAYED"; q.status_label = "Delayed"; q.status_detail = "Website snapshot (rebuilt about every 10 minutes)";
    }
    if (q.status === "DELAYED" && q.kind !== "daily" && age !== null && age > 3600) {
      q.status = "STALE"; q.status_label = "Stale"; q.status_detail = "The website snapshot is more than an hour old";
    }
    return q;
  }
  function fromMiniTicker(q, d) {
    const c = Number(d.c), o = Number(d.o);
    return Object.assign({}, q, {
      price: c, prev_close: o, change: c - o, change_pct: o ? (c - o) / o * 100 : null, day_high: Number(d.h), day_low: Number(d.l),
      volume: Number(d.v), quote_currency: "USDT", change_basis: "24h", source: "Binance", provider: "binance",
      provider_symbol: d.s, source_note: `${d.s.replace(/USDT$/, "")}/USDT on Binance, streamed live into your browser`,
      kind: "stream", is_proxy: false, is_unofficial: false, fallback: false, error_message: null,
      source_timestamp: new Date(d.E).toISOString(), received_at: new Date().toISOString(),
      status: "LIVE", status_label: "Live", status_detail: "Streaming; updated within the last 5 seconds",
    });
  }

  window.MLW_STATIC_LIVE = function (dispatch, setConn) {
    let first = true, ws = null, retry = 1000;
    const changed = new Set();

    async function loadSnapshot() {
      try {
        const d = await getJSON("quotes.json", true);
        const out = [];
        for (const raw of d.quotes) {
          const cur = LIVE.quotes.get(raw.id);
          if (cur && cur.provider === "binance" && cur.kind === "stream" && Date.now() - (LIVE.lastMsg.get(raw.id) || 0) < 60000) continue;
          const q = adapt(raw);
          LIVE.quotes.set(q.id, q);
          out.push(q);
          checkAlerts(q);
        }
        dispatch({ type: first ? "snapshot" : "quotes", quotes: first ? [...LIVE.quotes.values()] : out });
        if (first) { first = false; startStream(); }
        if (!ws || ws.readyState !== 1) setConn("ok", "Website · updated " + new Date(d.built_at).toLocaleTimeString("en-GB", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit" }) + " WIB");
      } catch (e) { setConn("bad", "Could not load data"); }
    }

    function startStream() {
      const crypto = [...LIVE.quotes.values()].filter((q) => q.asset_class === "crypto");
      if (!crypto.length) return;
      const symOf = (q) => (q.provider === "binance" && q.provider_symbol) ? q.provider_symbol : q.symbol + "USDT";
      const map = new Map(crypto.map((q) => [symOf(q), q.id]));
      const streams = [...map.keys()].map((s) => s.toLowerCase() + "@miniTicker").join("/");
      try { ws = new WebSocket("wss://data-stream.binance.vision/stream?streams=" + streams); } catch (e) { return; }
      ws.onopen = () => { retry = 1000; setConn("ok", "Live crypto + website data"); };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data), d = msg.data;
        if (!d || d.e !== "24hrMiniTicker") return;
        const id = map.get(d.s);
        if (id === undefined) return;
        const q = fromMiniTicker(LIVE.quotes.get(id), d);
        LIVE.quotes.set(id, q);
        LIVE.lastMsg.set(id, Date.now());
        changed.add(id);
        checkAlerts(q);
      };
      ws.onclose = () => { setConn("bad", "Crypto stream reconnecting…"); setTimeout(startStream, retry); retry = Math.min(retry * 2, 30000); };
      ws.onerror = () => { try { ws.close(); } catch (e) { /* ignore */ } };
    }

    // Every second: send changed prices and keep the badges honest (LIVE only while updates keep arriving).
    setInterval(() => {
      const now = Date.now();
      for (const [id, t] of LIVE.lastMsg) {
        const q = LIVE.quotes.get(id);
        if (!q || q.kind !== "stream") continue;
        const age = (now - t) / 1000;
        const st = age <= 5 ? "LIVE" : age <= 120 ? "DELAYED" : "STALE";
        if (q.status !== st) {
          q.status = st;
          q.status_label = st === "LIVE" ? "Live" : st === "DELAYED" ? "Delayed" : "Stale";
          q.status_detail = st === "LIVE" ? "Streaming; updated within the last 5 seconds" : st === "DELAYED" ? "Stream connected; no new trade in the last few seconds" : "No update from the stream for over 2 minutes";
          changed.add(id);
        }
      }
      if (changed.size) {
        dispatch({ type: "quotes", quotes: [...changed].map((id) => LIVE.quotes.get(id)) });
        changed.clear();
      }
    }, 1000);

    loadSnapshot();
    setInterval(loadSnapshot, 120000);
  };

  // ---------------------------------------------------------------- page fills from this browser's progress
  function setText(id, text) { const el = document.getElementById(id); if (el) el.textContent = text; }
  function markSeen() {
    const el = document.querySelector("[data-concepts]");
    if (!el) return;
    let slugs = [];
    try { slugs = JSON.parse(el.dataset.concepts || "[]"); } catch (e) { return; }
    for (const s of slugs) P.seen[s] = (P.seen[s] || 0) + 1;
    save(P);
  }

  async function fillPages() {
    const L = await learning().catch(() => null);
    if (!L) return;
    const S = await stats();
    const M = window.MLW;
    const pct = (a, b) => (b ? Math.round(100 * a / b) : 0);
    const cstats = conceptStats(L);

    // Dashboard
    if (document.getElementById("kpi-learning")) {
      setText("kpi-alerts", String(P.alerts.filter((a) => a.active).length));
      setText("kpi-learning", `${S.lessons_done}/${S.lessons_total}`);
      setText("kpi-learning-sub", `lessons done · ${S.due} review${S.due === 1 ? "" : "s"} due`);
      setText("lp-lessons", `${S.lessons_done} / ${S.lessons_total}`);
      const bar = document.getElementById("lp-progress"); if (bar) bar.style.width = pct(S.lessons_done, S.lessons_total) + "%";
      setText("lp-answers-label", S.answered ? `Quiz answers (${pct(S.correct, S.answered)}% correct)` : "Quiz answers");
      setText("lp-answers", String(S.answered));
      setText("lp-due", String(S.due));
      const next = L.lessons.find((l) => !P.lessons[l.id]);
      const nx = document.getElementById("lp-next");
      if (nx) nx.innerHTML = next ? `<span>Next lesson<br><a href="${BASE}/learn/${next.id}"><strong>${M.esc(next.title)}</strong></a> <span class="small muted">· ${M.esc(next.path_title)}</span></span><a class="btn btn-sm btn-primary" href="${BASE}/learn/${next.id}">Start</a>` : "<span>All lessons done 🎉</span>";
    }
    // Learn page
    document.querySelectorAll("[data-lesson-id]").forEach((li) => {
      if (P.lessons[li.dataset.lessonId] && !li.querySelector(".done-mark")) li.insertAdjacentHTML("beforeend", ' <span class="up small done-mark">✔ done</span>');
    });
    document.querySelectorAll("[data-path-key]").forEach((card) => {
      const ids = [...card.querySelectorAll("[data-lesson-id]")].map((x) => x.dataset.lessonId);
      const done = ids.filter((id) => P.lessons[id]).length;
      const bar = card.querySelector(".progress > span"); if (bar) bar.style.width = pct(done, ids.length) + "%";
    });
    setText("learn-progress", `${S.lessons_done} of ${S.lessons_total}`);
    // Lesson page
    const doneBtn = document.getElementById("done");
    if (doneBtn && doneBtn.dataset.lessonId) {
      const isDone = !!P.lessons[doneBtn.dataset.lessonId];
      doneBtn.dataset.done = String(isDone);
      doneBtn.textContent = isDone ? "✔ Completed — mark as not done" : "Mark lesson as done";
    }
    // Glossary list + concept page
    document.querySelectorAll("tr[data-slug]").forEach((tr) => {
      const slug = tr.dataset.slug, seen = P.seen[slug] || 0, st = cstats[slug];
      const cells = tr.querySelectorAll("td");
      if (cells.length >= 5) {
        cells[3].textContent = String(seen);
        cells[4].textContent = st && st.length ? pct(st.filter(Boolean).length, st.length) + "%" : "—";
      }
      tr.dataset.seen = seen ? "seen" : "unseen";
    });
    const cs = document.getElementById("concept-progress");
    if (cs) {
      const slug = cs.dataset.slug, st = cstats[slug];
      cs.textContent = `Seen ${P.seen[slug] || 0} time(s)` + (st && st.length ? ` · Mastery ${pct(st.filter(Boolean).length, st.length)}%` : "");
    }
    // Quiz page
    if (document.getElementById("quiz-stats")) {
      setText("quiz-stats", `Answered ${S.answered} · correct ${S.correct}${S.answered ? ` (${pct(S.correct, S.answered)}%)` : ""} · ${S.due} due for review · ${S.questions} questions available`);
      const h = document.getElementById("quiz-history");
      if (h) h.innerHTML = S.history.length ? '<div class="history">' + S.history.map((x) => `<div class="hist-row"><span class="small">${x.date.slice(5)}</span><span class="hist-bar"><span style="width:${pct(x.correct, x.answered)}%"></span></span><span class="small num">${x.correct}/${x.answered}</span></div>`).join("") + "</div>" : '<p class="muted small">No answers yet.</p>';
      const w = document.getElementById("quiz-weak");
      if (w) w.innerHTML = S.weak.length ? '<table class="table"><tbody>' + S.weak.map((x) => `<tr><td><a href="${BASE}/glossary/${encodeURIComponent(x.slug)}">${M.esc(x.name)}</a></td><td class="r num">${pct(x.accuracy, 1)}%</td><td class="r small muted">${x.attempts} answers</td></tr>`).join("") + "</tbody></table>" : '<p class="muted small">Answer at least two questions on a concept to see it here.</p>';
    }
    // Alerts page
    const al = document.getElementById("alerts-list");
    if (al) {
      const list = alertsList();
      al.innerHTML = list.length ? '<div class="table-wrap"><table class="table"><thead><tr><th>Asset</th><th>Condition</th><th class="r">Value</th><th>State</th><th></th></tr></thead><tbody>' +
        list.map((a) => `<tr><td><a href="${BASE}/asset/${encodeURIComponent(a.symbol)}">${M.esc(a.symbol)}</a></td><td>${M.esc(a.condition_text)}</td><td class="r num">${a.value}${a.condition === "pct_move" ? "%" : ""}</td><td class="small">${a.active ? '<span class="up">● active</span>' : "triggered " + M.esc(M.shortJakarta(new Date(a.triggered_at))) + " at " + a.triggered_price}</td><td><button class="btn js-del" data-id="${a.id}" type="button">Delete</button></td></tr>`).join("") + "</tbody></table></div>"
        : '<p class="muted">No alerts yet. Alerts are checked while a page of this website is open in your browser.</p>';
      al.querySelectorAll(".js-del").forEach((b) => b.onclick = () => { P.alerts = P.alerts.filter((a) => String(a.id) !== b.dataset.id); save(P); location.reload(); });
    }
    // Settings page: export / import / reset this browser's progress
    const ex = document.getElementById("export-progress");
    if (ex) {
      ex.onclick = () => {
        const blob = new Blob([JSON.stringify(P, null, 1)], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob); a.download = "markets-workbench-progress.json"; a.click();
      };
      document.getElementById("import-progress").onchange = async (ev) => {
        try { P = Object.assign(load(), JSON.parse(await ev.target.files[0].text())); save(P); M.toast("Progress imported."); setTimeout(() => location.reload(), 600); }
        catch (e) { M.toast("That file could not be read.", "toast-err"); }
      };
      document.getElementById("reset-progress").onclick = () => {
        if (confirm("Delete all progress saved in this browser (quiz answers, lessons, alerts)?")) { localStorage.removeItem(KEY); location.reload(); }
      };
      setText("progress-summary", `${S.answered} quiz answers, ${S.lessons_done} lessons done, ${P.alerts.length} alerts, ${Object.keys(P.seen).length} concepts seen.`);
    }
  }

  document.addEventListener("DOMContentLoaded", () => { markSeen(); fillPages(); });
})();
