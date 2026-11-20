// Demo investing: a pretend-money account (up to $1,000,000,000) that buys and sells at the real live prices
// this app already shows. Orders fill only at a fresh real price (LIVE or DELAYED). Everything is saved in this
// browser (localStorage) - the same on the website and in the app on your PC. Not real money, not advice.
(function () {
  "use strict";
  const M = window.MLW;
  const KEY = "mlw-demo-v1";
  const MIN_START = 1000, MAX_START = 1e9;
  const FILLABLE = ["LIVE", "DELAYED"];
  const CLASS_NAMES = { crypto: "Crypto", metal: "Precious metals", energy: "Energy", stock: "Stocks", etf: "ETFs" };
  const $ = (id) => document.getElementById(id);

  const quotes = new Map(); // symbol -> latest quote view
  let acct = load();
  let dirty = true;

  // ---------------------------------------------------------------- storage
  function load() {
    try {
      const a = JSON.parse(localStorage.getItem(KEY) || "null");
      return a && a.v === 1 && isFinite(a.cash) ? a : null;
    } catch (e) { return null; }
  }
  function save() {
    try {
      if (acct) localStorage.setItem(KEY, JSON.stringify(acct)); else localStorage.removeItem(KEY);
    } catch (e) { M.toast("Could not save the demo account in this browser: " + M.esc(e.message), "toast-err"); }
  }

  // ---------------------------------------------------------------- helpers
  function parseAmount(s) {
    s = String(s || "").trim().toLowerCase().replace(/[$,\s_]/g, "").replace(/usd$/, "");
    const m = s.match(/^(\d*\.?\d+)([kmb])?$/);
    if (!m) return NaN;
    return parseFloat(m[1]) * ({ k: 1e3, m: 1e6, b: 1e9 }[m[2]] || 1);
  }
  function money(v) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    return (v < 0 ? "−$" : "$") + M.fmtNum(Math.abs(v), 2);
  }
  function signedMoney(v) { return (v > 0.004 ? "+" : "") + money(v); }
  function pct(v) {
    if (!isFinite(v)) return "—";
    const d = Math.abs(v) < 0.1 ? 3 : 2, shown = Number(Math.abs(v).toFixed(d));
    return (shown === 0 ? "" : v > 0 ? "+" : "−") + M.fmtNum(Math.abs(v), d) + "%";
  }
  function cls(v) { return v > 0.004 ? "up" : v < -0.004 ? "down" : "flat"; }
  function qtyText(v) { return Number(v).toLocaleString("en-US", { maximumFractionDigits: v >= 1000 ? 4 : 8 }); }
  function tradable(q) { return q && q.kind !== "daily" && q.currency !== "%" && q.status !== "UNAVAILABLE" && CLASS_NAMES[q.asset_class]; }
  function fillable(q) { return q && FILLABLE.includes(q.status) && q.price > 0; }
  function priceOf(sym) { const q = quotes.get(sym); return q && q.price > 0 ? q.price : null; }

  function whyNotFillable(q) {
    if (!q || q.price === null || q.price === undefined) return "No real price for this asset yet - wait a moment.";
    if (q.status === "MARKET_CLOSED") {
      const opens = q.market && q.market.next_open ? " It opens " + M.dayJakarta(new Date(q.market.next_open)) + " (Jakarta time)." : "";
      return `The ${q.symbol} market is closed, so a demo order cannot fill now.${opens}`;
    }
    if (q.status === "STALE") return `The newest ${q.symbol} price is too old (STALE), so it is not used for orders.`;
    return `${q.symbol} has no usable price right now (${q.status_label || q.status}).`;
  }

  // Account value from the newest real prices. Positions without a price yet make the total unknown.
  function valuation() {
    if (!acct) return null;
    let invested = 0, missing = 0;
    for (const [sym, p] of Object.entries(acct.positions)) {
      const px = priceOf(sym);
      if (px === null) missing++; else invested += p.qty * px;
    }
    return { invested, missing, total: missing ? null : acct.cash + invested };
  }

  // ---------------------------------------------------------------- orders
  function currentOrder() {
    const q = quotes.get($("o-sym").value);
    const side = document.querySelector('input[name="side"]:checked').value;
    const mode = document.querySelector('input[name="mode"]:checked').value;
    const amt = parseAmount($("o-amt").value);
    const held = q && acct.positions[q.symbol] ? acct.positions[q.symbol].qty : 0;
    const o = { q, side, mode, amt, held, ok: false, msg: "" };
    if (!q) { o.msg = "Choose an asset."; return o; }
    if (!fillable(q)) { o.msg = whyNotFillable(q); return o; }
    if (!(amt > 0)) { o.msg = "Enter an amount."; return o; }
    o.price = q.price;
    o.qty = mode === "usd" ? amt / q.price : amt;
    o.value = o.qty * q.price;
    if (side === "buy" && o.value > acct.cash * (1 + 1e-9)) {
      o.msg = `Not enough demo cash: this order needs ${money(o.value)}, you have ${money(acct.cash)}.`; return o;
    }
    if (side === "sell") {
      if (held <= 0) { o.msg = `You do not own any ${q.symbol} in the demo account.`; return o; }
      if (o.qty > held * (1 + 1e-9)) { o.msg = `You only hold ${qtyText(held)} ${q.symbol} (worth ${money(held * q.price)}).`; return o; }
      if (o.qty > held) o.qty = held;
    }
    if (side === "buy" && o.value > acct.cash) { o.value = acct.cash; o.qty = o.value / q.price; }
    o.value = o.qty * q.price;
    if (o.value < 0.01) { o.msg = "The order is smaller than one cent."; return o; }
    o.ok = true;
    return o;
  }

  function execute() {
    const o = currentOrder(); // re-read the newest real price at the moment of the click
    if (!o.ok) { M.toast(M.esc(o.msg), "toast-err"); return; }
    const q = o.q, sym = q.symbol, now = new Date().toISOString();
    const pos = acct.positions[sym] || { qty: 0, cost: 0 };
    let realized = null;
    if (o.side === "buy") {
      acct.cash -= o.value;
      pos.qty += o.qty;
      pos.cost += o.value;
      acct.positions[sym] = pos;
    } else {
      const avg = pos.cost / pos.qty;
      realized = (o.price - avg) * o.qty;
      acct.cash += o.value;
      acct.realized += realized;
      pos.cost -= avg * o.qty;
      pos.qty -= o.qty;
      if (pos.qty <= 1e-12 || pos.qty * o.price < 0.005) delete acct.positions[sym]; else acct.positions[sym] = pos;
    }
    if (Math.abs(acct.cash) < 0.005) acct.cash = 0;
    acct.trades.push({ t: now, sym, name: q.name, side: o.side, qty: o.qty, price: o.price, value: o.value, realized,
      status: q.status, source: q.source, price_time: q.source_timestamp, unit: M.unitOf(q) });
    if (acct.trades.length > 2000) acct.trades.splice(0, acct.trades.length - 2000);
    recordEquity(true);
    save();
    $("o-amt").value = "";
    M.toast(`<strong>Demo order filled</strong><br>${o.side === "buy" ? "Bought" : "Sold"} ${M.esc(qtyText(o.qty))} ${M.esc(sym)} at ${M.esc(M.fmtNum(o.price, q.decimals))} ${M.esc(M.unitOf(q))} = ${M.esc(money(o.value))} (${M.esc(q.status)}, ${M.esc(q.source || "")}).`);
    dirty = true; render();
  }

  // ---------------------------------------------------------------- equity history (recorded while this page is open)
  function recordEquity(force) {
    if (!acct) return;
    const v = valuation();
    if (!v || v.total === null) return;
    const now = Date.now(), pts = acct.equity;
    const last = pts[pts.length - 1];
    if (!force && last && now - last[0] < 55000) return;
    if (!force && !acct.trades.length) return;
    pts.push([now, Math.round(v.total * 100) / 100]);
    if (pts.length > 4000) pts.splice(0, pts.length - 4000);
    save();
  }

  function renderChart() {
    const box = $("equity-chart");
    const pts = acct.equity.slice();
    const v = valuation();
    if (v && v.total !== null && pts.length) pts.push([Date.now(), v.total]);
    if (pts.length < 2) { box.innerHTML = '<p class="muted small">The chart starts with your first demo order.</p>'; return; }
    const w = 600, h = 200, t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
    const vals = pts.map((p) => p[1]).concat([acct.start_cash]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < hi * 1e-6) { hi += Math.max(1, hi * 0.001); lo -= Math.max(1, lo * 0.001); }
    const x = (t) => ((t - t0) / Math.max(1, t1 - t0)) * w;
    const y = (val) => h - 8 - ((val - lo) / (hi - lo)) * (h - 16);
    const line = pts.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
    const area = `0,${h} ${line} ${w},${h}`;
    const base = y(acct.start_cash).toFixed(1);
    box.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Demo account value over time">
        <polygon class="eq-area" points="${area}"/><line class="eq-base" x1="0" x2="${w}" y1="${base}" y2="${base}"/>
        <polyline class="eq-line" points="${line}"/></svg>
      <div class="demo-chart-labels"><span>${M.esc(M.shortJakarta(new Date(t0)))}</span>
        <span>low ${money(lo)} · high ${money(hi)} · dashed = starting cash</span><span>now</span></div>`;
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
    list.sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.id - b.id)
      .forEach((q) => { (groups[q.asset_class] = groups[q.asset_class] || []).push(q); });
    sel.innerHTML = Object.keys(CLASS_NAMES).filter((c) => groups[c]).map((c) =>
      `<optgroup label="${CLASS_NAMES[c]}">${groups[c].map((q) => `<option value="${M.esc(q.symbol)}">${M.esc(q.symbol)} — ${M.esc(q.name)}</option>`).join("")}</optgroup>`).join("");
    if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
  }

  function renderOrder() {
    const side = document.querySelector('input[name="side"]:checked').value;
    const mode = document.querySelector('input[name="mode"]:checked').value;
    $("order").dataset.side = side;
    $("o-amt-label").textContent = mode === "usd" ? "Amount (USD)" : "Quantity";
    $("o-amt").placeholder = mode === "usd" ? "e.g. 50,000 or 2.5m" : "e.g. 1.5";
    const q = quotes.get($("o-sym").value);
    const qline = $("o-quote");
    if (q && q.price !== null && q.price !== undefined) {
      const b = M.badge(q), up = M.updatedText(q);
      const held = acct.positions[q.symbol];
      qline.innerHTML = `Price now: <strong class="num">${M.esc(M.fmtPrice(q))} ${M.esc(M.unitOf(q))}</strong>
        <span class="badge ${b.cls}" title="${M.esc(b.title)}">${M.esc(b.text)}</span> · ${M.esc(q.source || "")} · ${M.esc(up.text)}` +
        (held ? `<br>You hold ${M.esc(qtyText(held.qty))} ${M.esc(q.symbol)} (worth ${money(held.qty * q.price)})` : "");
    } else qline.textContent = "Waiting for a real price…";
    const o = currentOrder();
    const pv = $("o-preview");
    if (o.ok) {
      pv.className = "demo-preview";
      pv.innerHTML = `${side === "buy" ? "Buy" : "Sell"} <strong>${M.esc(qtyText(o.qty))} ${M.esc(o.q.symbol)}</strong> at
        <span class="num">${M.esc(M.fmtNum(o.price, o.q.decimals))}</span> = <strong class="num">${money(o.value)}</strong>.
        Cash after: ${money(side === "buy" ? acct.cash - o.value : acct.cash + o.value)}.`;
    } else {
      pv.className = "demo-preview" + (o.amt > 0 || (o.q && !fillable(o.q)) ? " bad" : "");
      pv.textContent = o.msg;
    }
    $("o-submit").disabled = !o.ok;
    $("o-submit").textContent = o.ok ? `${side === "buy" ? "Buy" : "Sell"} ${o.q.symbol} (demo)` : "Place demo order";
  }

  function renderKpis() {
    const v = valuation();
    const start = acct.start_cash;
    $("k-cash").textContent = money(acct.cash);
    $("k-inv").textContent = money(v.invested) + (v.missing ? "+" : "");
    const n = Object.keys(acct.positions).length;
    $("k-inv-sub").textContent = `${n} position${n === 1 ? "" : "s"}` + (v.missing ? ` · ${v.missing} waiting for a price` : "");
    if (v.total === null) {
      $("k-total").textContent = "…"; $("k-total-sub").textContent = "waiting for prices";
      $("k-pl").textContent = "…"; $("k-pl-sub").textContent = "";
      $("k-cash-sub").textContent = "";
      return;
    }
    const pl = v.total - start, unreal = pl - acct.realized;
    $("k-total").textContent = money(v.total);
    $("k-total-sub").textContent = `started with ${money(start)} · ${M.shortJakarta(new Date(acct.created_at))}`;
    $("k-cash-sub").textContent = `${M.fmtNum(acct.cash / v.total * 100, 1)}% of the account`;
    $("k-pl").innerHTML = `<span class="${cls(pl)}">${signedMoney(pl)}</span>`;
    $("k-pl-sub").textContent = `${pct(pl / start * 100)} · realized ${signedMoney(acct.realized)} · open ${signedMoney(unreal)}`;
  }

  function renderPositions() {
    const entries = Object.entries(acct.positions);
    $("pos-card").hidden = false;
    const v = valuation();
    if (!entries.length) { $("pos-body").innerHTML = '<tr><td colspan="8" class="muted">No positions yet. Buy something with the order form.</td></tr>'; return; }
    $("pos-body").innerHTML = entries.sort((a, b) => (priceOf(b[0]) || 0) * b[1].qty - (priceOf(a[0]) || 0) * a[1].qty).map(([sym, p]) => {
      const q = quotes.get(sym), px = priceOf(sym), avg = p.cost / p.qty;
      const value = px === null ? null : p.qty * px, pl = value === null ? null : value - p.cost;
      const b = q ? M.badge(q) : { cls: "badge-closed", text: "…", title: "" };
      const weight = value !== null && v.total ? value / v.total * 100 : null;
      const d = q ? q.decimals : 2;
      return `<tr><td><a href="${M.base}/asset/${encodeURIComponent(sym)}"><strong>${M.esc(sym)}</strong></a></td>
        <td class="r num">${M.esc(qtyText(p.qty))}</td>
        <td class="r num">${M.esc(M.fmtNum(avg, d))}</td>
        <td class="r num">${px === null ? "—" : M.esc(M.fmtNum(px, d))} <span class="badge ${b.cls}" title="${M.esc(b.title)}">${M.esc(b.text)}</span></td>
        <td class="r num">${money(value)}</td>
        <td class="r num ${pl === null ? "" : cls(pl)}">${pl === null ? "—" : signedMoney(pl) + " (" + pct(pl / p.cost * 100) + ")"}</td>
        <td class="r num hide-sm">${weight === null ? "—" : M.fmtNum(weight, 1) + "%"}</td>
        <td><button class="btn btn-sm js-sellall" type="button" data-sym="${M.esc(sym)}"${fillable(q) ? "" : ` disabled title="${M.esc(whyNotFillable(q))}"`}>Sell all</button></td></tr>`;
    }).join("");
  }

  function renderHistory() {
    $("hist-card").hidden = false;
    const t = acct.trades.slice(-200).reverse();
    $("hist-body").innerHTML = t.length ? t.map((x) => `<tr>
        <td title="${M.esc(M.utcLabel(new Date(x.t)))}">${M.esc(M.shortJakarta(new Date(x.t)))}</td>
        <td class="side-${x.side}">${x.side === "buy" ? "BUY" : "SELL"}</td>
        <td><a href="${M.base}/asset/${encodeURIComponent(x.sym)}">${M.esc(x.sym)}</a></td>
        <td class="r num">${M.esc(qtyText(x.qty))}</td>
        <td class="r num">${M.esc(M.fmtNum(x.price, x.price >= 100 ? 2 : 4))} <span class="muted small">${M.esc(x.unit || "")}</span></td>
        <td class="r num">${money(x.value)}</td>
        <td class="r num ${x.realized === null ? "" : cls(x.realized)}">${x.realized === null ? "—" : signedMoney(x.realized)}</td>
        <td class="hide-sm small muted">${M.esc(x.source || "")} · ${M.esc(x.status || "")}${x.price_time ? " · price of " + M.esc(M.shortJakarta(new Date(x.price_time))) : ""}</td></tr>`).join("")
      : '<tr><td colspan="8" class="muted">No orders yet.</td></tr>';
  }

  function render() {
    const started = !!acct;
    $("setup-card").hidden = started;
    $("trade-area").hidden = !started;
    $("demo-kpis").hidden = !started;
    if (!started) { $("pos-card").hidden = true; $("hist-card").hidden = true; return; }
    fillSelect();
    renderOrder();
    if (!dirty) return;
    dirty = false;
    renderKpis(); renderPositions(); renderHistory(); renderChart();
  }

  // ---------------------------------------------------------------- events
  $("presets").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-amt]");
    if (b) $("start-amt").value = M.fmtNum(Number(b.dataset.amt), 0);
  });
  $("start-form").onsubmit = (ev) => {
    ev.preventDefault();
    const amt = parseAmount($("start-amt").value);
    if (!(amt >= MIN_START && amt <= MAX_START)) {
      $("start-hint").textContent = `Enter an amount from $1,000 to $1,000,000,000 (you can type 250k, 5m or 1b).`;
      return;
    }
    acct = { v: 1, start_cash: Math.round(amt * 100) / 100, cash: Math.round(amt * 100) / 100, realized: 0,
      created_at: new Date().toISOString(), positions: {}, trades: [], equity: [] };
    save(); dirty = true; render();
    M.toast(`Demo account started with ${money(acct.cash)} of pretend money.`);
  };
  document.querySelectorAll('#order input[type="radio"]').forEach((r) => r.addEventListener("change", renderOrder));
  $("o-sym").addEventListener("change", renderOrder);
  $("o-amt").addEventListener("input", renderOrder);
  function setFraction(f) {
    const q = quotes.get($("o-sym").value);
    if (!q || !(q.price > 0)) return;
    const side = document.querySelector('input[name="side"]:checked').value;
    const mode = document.querySelector('input[name="mode"]:checked').value;
    const held = acct.positions[q.symbol] ? acct.positions[q.symbol].qty : 0;
    const usd = side === "buy" ? acct.cash * f : held * q.price * f;
    const qty = side === "buy" ? acct.cash * f / q.price : held * f;
    if (mode === "usd") $("o-amt").value = (Math.floor(usd * 100) / 100).toLocaleString("en-US", { maximumFractionDigits: 2 });
    else $("o-amt").value = String(f === 1 && side === "sell" ? held : Math.floor(qty * 1e8) / 1e8);
    renderOrder();
  }
  $("o-max").onclick = () => setFraction(1);
  $("o-pcts").addEventListener("click", (ev) => { const b = ev.target.closest("[data-pct]"); if (b) setFraction(Number(b.dataset.pct) / 100); });
  $("order").onsubmit = (ev) => { ev.preventDefault(); execute(); };
  $("pos-body").addEventListener("click", (ev) => {
    const b = ev.target.closest(".js-sellall");
    if (!b) return;
    $("o-sym").value = b.dataset.sym;
    document.querySelector('input[name="side"][value="sell"]').checked = true;
    document.querySelector('input[name="mode"][value="qty"]').checked = true;
    setFraction(1);
    $("order").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  $("reset").onclick = () => {
    if (!confirm("Start over? This deletes the demo account, its positions and its order history in this browser.")) return;
    acct = null; save(); dirty = true; render();
  };
  $("export").onclick = () => {
    const blob = new Blob([JSON.stringify(acct, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "demo-account.json"; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };
  $("import-file").onchange = async (ev) => {
    const f = ev.target.files[0];
    ev.target.value = "";
    if (!f) return;
    try {
      const a = JSON.parse(await f.text());
      const okNum = (x) => typeof x === "number" && isFinite(x);
      if (!(a && a.v === 1 && okNum(a.cash) && okNum(a.start_cash) && a.start_cash <= MAX_START && a.positions && Array.isArray(a.trades))) throw new Error("not a demo account file");
      if (!confirm("Replace the demo account in this browser with the imported one?")) return;
      a.equity = Array.isArray(a.equity) ? a.equity : []; a.realized = okNum(a.realized) ? a.realized : 0;
      acct = a; save(); dirty = true; render();
      M.toast("Demo account imported.");
    } catch (e) { M.toast("Could not import: " + M.esc(e.message), "toast-err"); }
  };
  window.addEventListener("storage", (ev) => { if (ev.key === KEY) { acct = load(); dirty = true; render(); } });

  // Live prices: the same real quotes the dashboard shows (crypto streams live).
  M.onLive((msg) => {
    if (msg.type !== "snapshot" && msg.type !== "quotes") return;
    msg.quotes.forEach((q) => quotes.set(q.symbol, q));
    dirty = true;
  });
  setInterval(() => { if (dirty) render(); recordEquity(false); }, 1000);
  render();
})();
