// Quiz page: one question at a time, instant feedback with explanations and source links.
(function () {
  "use strict";
  const M = window.MLW;
  const box = document.getElementById("quiz-box");
  let mode = new URLSearchParams(location.search).get("mode") || "today";
  let queue = [], idx = 0, score = { right: 0, total: 0 };

  const EMPTY = M.isStatic ? {
    today: "No new questions from today's news yet. New ones are written automatically several times a day.",
    concepts: "No unanswered concept or lesson questions yet. New ones are written automatically several times a day.",
  } : {
    today: "No new questions from today's news. Use “Quiz me” below on a top story to create some.",
    concepts: "No unanswered concept or lesson questions. Open a lesson or glossary entry and click “Quiz me”.",
  };
  Object.assign(EMPTY, {
    review: "Nothing is due for review right now. Missed questions come back here after about 10 minutes; correct ones after 1, 6, then more days.",
    weak: "No weak topics yet - answer more questions first.",
  });

  function setTabs() {
    document.querySelectorAll("[data-mode]").forEach((b) => {
      const on = b.dataset.mode === mode;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on);
    });
  }

  async function load() {
    setTabs();
    box.innerHTML = '<p class="muted">Loading…</p>';
    try {
      const d = await M.api(`/api/quiz/next?mode=${mode}&limit=10`);
      queue = d.questions; idx = 0; score = { right: 0, total: 0 };
      show();
    } catch (e) { box.innerHTML = `<p class="warn-text">${M.esc(e.message)}</p>`; }
  }

  function sourceLink(s) {
    if (!s) return "";
    if (s.type === "article") return `Source: <a href="${M.base}/news/${s.id}">${M.esc(s.title)}</a> (${M.esc(s.source_name)}) · <a href="${M.esc(s.url)}" target="_blank" rel="noopener noreferrer">original ↗</a>`;
    if (s.type === "lesson") return `Source: lesson <a href="${M.base}/learn/${s.id}">${M.esc(s.title)}</a>`;
    if (s.type === "concept") return `Source: glossary <a href="${M.base}/glossary/${encodeURIComponent(s.slug)}">${M.esc(s.title)}</a>`;
    return "";
  }

  function show() {
    if (idx >= queue.length) {
      box.innerHTML = queue.length
        ? `<h2>Done: ${score.right} of ${score.total} correct</h2><p class="small muted">Missed questions will come back in Review.</p><button class="btn" id="again" type="button">Load more</button>`
        : `<p class="muted">${EMPTY[mode]}</p>`;
      const again = document.getElementById("again");
      if (again) again.onclick = load;
      return;
    }
    const q = queue[idx];
    box.innerHTML = `<p class="small muted">Question ${idx + 1} of ${queue.length} · ${M.esc(q.difficulty || "")}</p>
      <h2 class="q-text">${M.esc(q.question)}</h2>
      <div class="options">${q.options.map((o, i) => `<button class="option" type="button" data-i="${i}"><span class="opt-letter">${"ABCD"[i]}</span> ${M.esc(o)}</button>`).join("")}</div>
      <div id="feedback"></div>`;
    box.querySelectorAll(".option").forEach((b) => b.onclick = () => answer(q, Number(b.dataset.i)));
  }

  async function answer(q, chosen) {
    box.querySelectorAll(".option").forEach((b) => b.disabled = true);
    try {
      const r = await M.api("/api/quiz/answer", { method: "POST", body: { question_id: q.id, chosen } });
      score.total += 1; if (r.correct) score.right += 1;
      box.querySelectorAll(".option").forEach((b) => {
        const i = Number(b.dataset.i);
        if (i === r.correct_index) b.classList.add("right");
        else if (i === chosen) b.classList.add("wrong");
        const why = (r.option_explanations || [])[i];
        if (why) b.insertAdjacentHTML("beforeend", `<span class="opt-why">${M.esc(why)}</span>`);
      });
      const next = new Date(r.next_review_at);
      document.getElementById("feedback").innerHTML = `
        <p class="${r.correct ? "up" : "down"}"><strong>${r.correct ? "✔ Correct" : "✖ Not quite"}</strong></p>
        <p>${M.esc(r.explanation || "")}</p>
        <p class="small muted">${sourceLink(r.source)}</p>
        <p class="small muted">Next review: ${M.esc(M.dayJakarta(next))}</p>
        <button class="btn" id="next" type="button">Next question →</button>`;
      document.getElementById("next").onclick = () => { idx += 1; show(); };
    } catch (e) { M.toast("Could not save the answer: " + M.esc(e.message), "toast-err"); }
  }

  document.querySelectorAll("[data-mode]").forEach((b) => b.onclick = () => {
    mode = b.dataset.mode; history.replaceState(null, "", `?mode=${mode}`); load();
  });
  document.querySelectorAll(".js-gen").forEach((b) => b.onclick = async () => {
    b.disabled = true; b.textContent = "Working…";
    try {
      const r = await M.api("/api/quiz/generate", { method: "POST", body: { source_type: "article", source_id: b.dataset.id, n: 3 } });
      M.toast(`${r.kept} question(s) ready${r.discarded ? `, ${r.discarded} discarded by the checker` : ""}.`);
      mode = "today"; load();
      b.textContent = "Done";
    } catch (e) { M.toast("Quiz failed: " + M.esc(e.message), "toast-err"); b.disabled = false; b.textContent = "Quiz me"; }
  });
  load();
})();
