// News page: filters, live updates (a "new articles" notice arrives over the live connection).
(function () {
  "use strict";
  const M = window.MLW;
  const ids = ["q", "symbol", "asset_class", "source", "hours", "has_breakdown", "sort"];
  const el = (id) => document.getElementById(id);

  // Remember filters for this browser only (a convenience; safe if storage is blocked).
  try { const saved = JSON.parse(localStorage.getItem("mlw-news-filters") || "{}"); ids.forEach((k) => { if (saved[k] !== undefined && el(k)) el(k).value = saved[k]; }); } catch (e) { /* ignore */ }

  function params() {
    const p = new URLSearchParams();
    ids.forEach((k) => { const v = el(k).value; if (v !== "") p.set(k, v); });
    p.set("limit", "150");
    return p;
  }

  async function load() {
    try { const s = {}; ids.forEach((k) => { s[k] = el(k).value; }); localStorage.setItem("mlw-news-filters", JSON.stringify(s)); } catch (e) { /* ignore */ }
    const list = el("list");
    try {
      const d = await M.api("/api/news?" + params().toString());
      list.innerHTML = d.articles.length ? d.articles.map((a) => M.renderArticle(a)).join("")
        : '<p class="muted">No articles match these filters yet. News is fetched every few minutes.</p>';
      el("news-status").textContent = d.last_run ? `Last fetch: ${M.shortJakarta(new Date(d.last_run))}.` : "First fetch is running…";
      el("new-pill").hidden = true;
    } catch (e) { list.innerHTML = `<p class="warn-text">Could not load news: ${M.esc(e.message)}</p>`; }
  }

  async function loadSources() {
    try {
      const d = await M.api("/api/news/sources");
      const cur = el("source").value;
      el("source").innerHTML = '<option value="">All sources</option>' + d.sources.map((s) => `<option${s === cur ? " selected" : ""}>${M.esc(s)}</option>`).join("");
    } catch (e) { /* ignore */ }
  }

  let timer = null;
  ids.forEach((k) => el(k).addEventListener(k === "q" ? "input" : "change", () => { clearTimeout(timer); timer = setTimeout(load, k === "q" ? 300 : 0); }));
  el("new-pill").querySelector("button").onclick = load;
  if (el("refresh")) el("refresh").onclick = async () => {
    el("refresh").disabled = true; el("refresh").textContent = "Fetching…";
    try { const r = await M.api("/api/news/refresh", { method: "POST" }); M.toast(`Fetched ${r.fetched} items, ${r.new} new.`); await load(); await loadSources(); }
    catch (e) { M.toast("Fetch failed: " + M.esc(e.message), "toast-err"); }
    el("refresh").disabled = false; el("refresh").textContent = "Fetch news now";
  };
  M.onLive((msg) => { if (msg.type === "news" && msg.new > 0) { if (window.scrollY < 200) load(); else el("new-pill").hidden = false; } });
  loadSources().then(load);
  setInterval(load, 120000);
})();
