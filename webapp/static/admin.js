(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get() { try { return sessionStorage.getItem("tt_admin") || ""; } catch (e) { return ""; } },
    set(v) { try { sessionStorage.setItem("tt_admin", v); } catch (e) { /* private mode */ } },
  };
  let token = store.get();
  if (token) $("token").value = token;

  const auth = () => ({ headers: { Authorization: "Bearer " + token } });
  const table = (rows, head) => rows.length
    ? `<table><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>` +
      rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("") + "</tbody></table>"
    : '<p class="muted">Nothing yet.</p>';
  const kv = (obj) => Object.entries(obj || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => [esc(k), esc(v)]);

  function render(d) {
    const t = d.totals, q = d.questions;
    $("storage").className = "note" + (d.storage.persistent ? "" : " warn");
    $("storage").textContent = d.storage.persistent
      ? "Stored in your database: this history survives restarts and deploys."
      : "Stored in a temporary file: this history is LOST when the server restarts. Add DATABASE_URL on Render to keep it.";
    const cards = [
      [t.visitors, "visitors"], [t.filings_loaded, "filings loaded"], [t.filings_uploaded, "of them uploaded"],
      [t.filings_sample, "of them samples"], [t.questions, "questions asked"],
      [q.pct_verified == null ? "–" : q.pct_verified + "%", "answers with a verified badge"],
      [q.pct_not_in_data == null ? "–" : q.pct_not_in_data + "%", "answered 'not in tagged data'"],
      [t.votes_up + " / " + t.votes_down, "thumbs up / down"],
      [d.visit_minutes.median == null ? "–" : d.visit_minutes.median + " min", "median visit length"],
      [q.median_response_s == null ? "–" : q.median_response_s + " s", "median answer time"],
      [q.per_asking_visitor ?? "–", "questions per asking visitor"],
      ["$" + t.est_cost_usd, "estimated model spend"],
    ];
    $("cards").innerHTML = cards.map(([n, l]) => `<div class="card"><b>${esc(n)}</b><span>${esc(l)}</span></div>`).join("");

    const f = d.funnel, top = Math.max(f.visitors, 1);
    $("funnel").innerHTML = [["Visitors", f.visitors], ["Loaded a filing", f.loaded_a_filing],
      ["Asked a question", f.asked_a_question], ["Rated an answer", f.voted]].map(([l, n]) =>
      `<div class="fstep"><span>${l}</span><div class="bar" data-w="${Math.round(100 * n / top)}"></div><span>${n}</span></div>`).join("");

    const maxQ = Math.max(1, ...d.per_day.map((x) => Math.max(x.questions, x.visitors)));
    $("days-chart").innerHTML = d.per_day.map((x) =>
      `<div class="brow"><span>${esc(x.day.slice(5))}</span><div><div class="bar" data-w="${Math.round(100 * x.visitors / maxQ)}"></div>` +
      `<div class="bar q" data-w="${Math.round(100 * x.questions / maxQ)}"></div></div><span>${x.visitors} visitors · ${x.questions} q</span></div>`).join("") ||
      '<p class="muted">Nothing yet.</p>';

    $("q-tables").innerHTML = "<p class='muted'>By model</p>" + table(kv(q.by_model), ["Model", "Questions"]) +
      "<p class='muted'>By badge</p>" + table(kv(q.by_badge), ["Badge", "Questions"]) +
      "<p class='muted'>Refused</p>" + table(kv(d.blocked_reasons), ["Reason", "Times"]);
    $("f-tables").innerHTML = "<p class='muted'>Most loaded</p>" + table(d.top_filings.map(([k, v]) => [esc(k), v]), ["Company", "Loads"]) +
      "<p class='muted'>Framework</p>" + table(kv(d.frameworks), ["Framework", "Filings"]) +
      `<p class="muted">Failed loads: ${t.filings_failed} · median parse ${d.parse_seconds.median ?? "–"} s</p>`;

    $("recent").innerHTML = (d.recent_questions.length
      ? "<thead><tr><th>When (UTC)</th><th>Model</th><th>Filing</th><th>Question</th><th>Answer shown</th><th>Badge</th><th>Vote</th></tr></thead><tbody>" +
        d.recent_questions.map((r) => `<tr><td>${esc(new Date(r.ts * 1000).toISOString().slice(5, 16).replace("T", " "))}</td><td>${esc(r.model)}</td>` +
          `<td>${esc(r.entity)}</td><td>${esc(r.question || "(not stored)")}</td><td>${esc(r.answer || r.kind)}<br><span class="muted">${esc(r.concept || "")}</span></td>` +
          `<td>${esc(r.level)}</td><td class="${r.vote}">${r.vote === "up" ? "👍" : r.vote === "down" ? "👎" : ""}</td></tr>`).join("") + "</tbody>"
      : "<tbody><tr><td>Nothing yet.</td></tr></tbody>");

    const codes = Object.entries(d.codes);
    $("codes").innerHTML = table(codes.map(([k, v]) => [esc(k), v.questions]), ["Code id (first 8 of hash)", "Questions used"]) +
      `<p class="muted">Unlock attempts: ${esc(JSON.stringify(d.unlock_attempts))}</p>`;
    // The page's CSP forbids style="" attributes, so widths are applied through the DOM.
    document.querySelectorAll(".bar[data-w]").forEach((el) => { el.style.width = el.dataset.w + "%"; });
    $("report").hidden = false;
    $("csv").disabled = false;
  }

  async function load() {
    token = $("token").value.trim();
    $("msg").textContent = "Loading…";
    try {
      const r = await fetch("/api/admin/stats?days=" + $("days").value, auth());
      if (!r.ok) {
        $("msg").textContent = r.status === 401 ? "Wrong token." : r.status === 429 ? "Too many attempts. Try later." : "Could not load (" + r.status + ").";
        return;
      }
      store.set(token);
      $("msg").textContent = "";
      render(await r.json());
    } catch (e) { $("msg").textContent = "Network error."; }
  }

  $("login").addEventListener("submit", (e) => { e.preventDefault(); load(); });
  $("csv").addEventListener("click", async () => {
    const r = await fetch("/api/admin/export.csv?days=" + $("days").value, auth());
    if (!r.ok) { $("msg").textContent = "Could not export."; return; }
    const url = URL.createObjectURL(await r.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: "tagtrace-events.csv" });
    document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  });
  if (token) load();
})();
