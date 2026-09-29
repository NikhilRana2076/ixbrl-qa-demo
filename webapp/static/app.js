/* TagTrace — front end.
 * All server/model text is inserted with textContent (never innerHTML), so a
 * filing or model reply containing markup cannot inject script into the page. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const state = { model: "public", quota: null, filing: null, busy: false };

  function el(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "style") n.style.cssText = v;   // CSSOM is CSP-safe; style="" attributes are not
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const c of kids.flat()) if (c != null && c !== false) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return n;
  }

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", credentials: "same-origin",
                   headers: { "X-Requested-With": "fetch" } };
    if (opts.json) { init.method = "POST"; init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.json); }
    if (opts.form) { init.method = "POST"; init.body = opts.form; }
    let res, data;
    try { res = await fetch(path, init); data = await res.json(); }
    catch { throw { error: "network", message: "Could not reach the server. It may be waking up — try again in a few seconds." }; }
    if (!res.ok) throw data;
    return data;
  }

  // ------------------------------------------------------------------ quota / model
  function renderQuota(q) {
    if (!q) return;
    state.quota = q;
    $("#publicLeft").textContent = `${q.public_left} free question${q.public_left === 1 ? "" : "s"}`;
    const lockedBtn = document.querySelector('[data-model="locked"]');
    lockedBtn.classList.toggle("unlocked", q.unlocked);
    $("#lockedSub").textContent = q.unlocked ? `Unlocked · ${q.locked_left} questions left` : "Access code required";
    const box = $("#quota"); box.replaceChildren();
    if (q.service_paused) { box.append("Daily demo cap reached"); return; }
    const left = state.model === "locked" ? q.locked_left : q.public_left;
    const total = state.model === "locked" ? Math.max(left, 1) : q.public_limit;
    box.append(el("span", { text: `${left} question${left === 1 ? "" : "s"} left` }),
      el("span", { class: "quota-bar", "aria-hidden": "true" },
        el("i", { style: `width:${Math.max(0, Math.min(100, (left / total) * 100))}%` })));
  }

  function selectModel(m) {
    if (m === "locked" && !(state.quota && state.quota.unlocked)) { openUnlock(); return; }
    state.model = m;
    document.querySelectorAll(".model-opt").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.model === m)));
    renderQuota(state.quota);
  }
  document.querySelectorAll(".model-opt").forEach((b) => b.addEventListener("click", () => selectModel(b.dataset.model)));

  // ------------------------------------------------------------------ dialogs
  const unlockDlg = $("#unlockDialog"), quotaDlg = $("#quotaDialog");
  function openUnlock() { $("#codeError").hidden = true; $("#codeInput").value = ""; unlockDlg.showModal(); $("#codeInput").focus(); }
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => { unlockDlg.close(); quotaDlg.close(); }));
  $("#haveCode").addEventListener("click", () => { quotaDlg.close(); openUnlock(); });
  $("#unlockForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = $("#codeInput").value.trim();
    if (!code) return;
    $("#codeSubmit").disabled = true;
    try {
      const r = await api("/api/unlock", { json: { code } });
      renderQuota(r.quota); unlockDlg.close(); selectModel("locked");
    } catch (err) {
      $("#codeError").textContent = err.message || "That code did not work."; $("#codeError").hidden = false;
    } finally { $("#codeSubmit").disabled = false; }
  });
  function showQuotaWall(kind) {
    const t = { session_quota: "You've used your free questions", ip_quota: "Daily free limit reached",
                code_exhausted: "This access code is used up", service_paused: "The demo is resting for today" };
    $("#quotaTitle").textContent = t[kind] || t.session_quota;
    $("#quotaText").textContent = kind === "service_paused"
      ? "The demo has reached its daily usage cap, which keeps it free for everyone. Please come back tomorrow, or get in touch."
      : "Thanks for trying the demo. For more questions or a Claude access code, message me and I'll send one.";
    quotaDlg.showModal();
  }

  // ------------------------------------------------------------------ filing
  const maxMb = Number(document.body.dataset.maxMb || 25);
  function setLoading(on, title) {
    $("#uploadProgress").hidden = !on; $("#drop").hidden = on;
    $("#samples").hidden = on || !$("#sampleList").childElementCount;
    if (title) $("#progressTitle").textContent = title;
  }
  function showUploadError(msg) { const e = $("#uploadError"); e.textContent = msg; e.hidden = !msg; }

  async function uploadFile(file) {
    showUploadError("");
    if (!file) return;
    if (file.size > maxMb * 1024 * 1024) { showUploadError(`That file is ${(file.size / 1048576).toFixed(1)} MB; the limit is ${maxMb} MB.`); return; }
    const fd = new FormData(); fd.append("file", file);
    setLoading(true, `Parsing ${file.name}…`);
    try { const r = await api("/api/upload", { form: fd }); onFiling(r.filing); renderQuota(r.quota); }
    catch (err) { showUploadError(err.message || "Upload failed."); }
    finally { setLoading(false); }
  }
  async function loadSample(id, labelText) {
    showUploadError(""); setLoading(true, `Loading ${labelText}…`);
    try { const r = await api("/api/sample", { json: { id } }); onFiling(r.filing); renderQuota(r.quota); }
    catch (err) { showUploadError(err.message || "Could not load the sample."); }
    finally { setLoading(false); }
  }

  const drop = $("#drop"), input = $("#fileInput");
  input.addEventListener("change", () => uploadFile(input.files[0]));
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => uploadFile(e.dataTransfer.files[0]));

  $("#clearBtn").addEventListener("click", async () => {
    await api("/api/clear", { json: {} }).catch(() => {});
    onFiling(null);
  });

  document.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => {
    const q = b.dataset.q;
    $("#demo").scrollIntoView({ behavior: "smooth", block: "start" });
    if (state.filing) {
      $("#question").value = q; $("#question").focus({ preventScroll: true });
    } else {
      state.pendingQ = q;
      const hint = $("#filingHint");
      hint.textContent = `Load a filing first${$("#sampleList").childElementCount ? " (or try the sample below)" : ""}, and your question \u201c${q}\u201d will be ready to ask.`;
      hint.hidden = false;
    }
  }));

  function onFiling(f) {
    state.filing = f;
    fx.rows = null;
    if (f && state.pendingQ) { $("#question").value = state.pendingQ; state.pendingQ = null; $("#question").focus({ preventScroll: true }); }
    if (f) $("#filingHint").hidden = true;
    $("#loader").hidden = !!f; $("#overview").hidden = !f; $("#clearBtn").hidden = !f;
    $("#question").disabled = !f; $("#askBtn").disabled = !f;
    $("#question").placeholder = f ? "Ask about this filing, e.g. “What was profit before tax?”" : "Load a filing, then ask e.g. “What was revenue for the year?”";
    const sug = $("#suggestions"); sug.replaceChildren();
    if (!f) return;
    $("#ovEntity").textContent = f.entity;
    $("#ovFile").textContent = f.entity !== f.filename ? f.filename : "";
    $("#ovFramework").textContent = f.framework;
    $("#ovPeriod").textContent = f.period_end || "—";
    $("#ovFacts").textContent = f.n_facts.toLocaleString("en-GB");
    $("#ovNarr").textContent = f.n_narratives.toLocaleString("en-GB");
    $("#ovDim").textContent = `${f.pct_dimensional}% of facts`;
    $("#ovExt").textContent = f.n_extension.toLocaleString("en-GB");
    const ul = $("#ovMetrics"); ul.replaceChildren();
    for (const m of f.metrics) {
      const ch = m.change_pct;
      ul.append(el("li", { title: m.concept || "" },
        el("span", { class: "m-label", text: m.label }),
        el("span", { class: "m-value", text: m.value.display }),
        el("span", { class: "m-period", text: m.period }),
        ch == null ? el("span") : el("span", { class: "m-change " + (ch >= 0 ? "up" : "down"), text: `${ch >= 0 ? "▲" : "▼"} ${Math.abs(ch)}% vs prior` })));
    }
    if (!f.metrics.length) ul.append(el("li", {}, el("span", { class: "m-label muted", text: "No standard headline concepts tagged." })));
    const w = $("#ovWarnings"); w.replaceChildren(...(f.warnings || []).map((t) => el("p", { class: "note", text: t })));
    const rl = $("#ovRatios"); rl.replaceChildren();
    for (const r of f.ratios || []) {
      const pp = r.change_pp;
      const li = el("li", {},
        el("button", { class: "ratio-row", type: "button", "aria-expanded": "false" },
          el("span", { class: "m-label", text: r.label }),
          el("span", { class: "m-value" + (r.value < 0 ? " neg" : ""), text: r.display }),
          el("span", { class: "m-period", text: r.period }),
          pp == null ? el("span") : el("span", { class: "m-change " + (pp >= 0 ? "up" : "down"), text: `${pp >= 0 ? "▲" : "▼"} ${Math.abs(pp)} pp vs prior` })),
        el("p", { class: "ratio-formula", hidden: true },
          r.formula, el("br"), el("span", { class: "muted", text: `Tagged facts: ${r.inputs.map((i) => "F" + i.fact_id).join(", ")}` })));
      const btn = li.querySelector(".ratio-row"), fx = li.querySelector(".ratio-formula");
      btn.addEventListener("click", () => { fx.hidden = !fx.hidden; btn.setAttribute("aria-expanded", String(!fx.hidden)); });
      rl.append(li);
    }
    $("#ratiosBlock").hidden = !(f.ratios || []).length;
    if ((f.quick_asks || []).length) {
      sug.append(el("p", { class: "qa-title", text: "Quick asks" }));
      for (const g of f.quick_asks) {
        sug.append(el("div", { class: "qa-group" }, el("span", { class: "qa-label", text: g.group }),
          ...g.items.map((q) => el("button", { class: "chip", type: "button", text: q, onclick: () => ask(q) }))));
      }
    } else {
      for (const s of f.suggestions || []) sug.append(el("button", { class: "chip", type: "button", text: s, onclick: () => ask(s) }));
    }
  }

  // ------------------------------------------------------------------ fact explorer
  const factsDlg = $("#factsDialog");
  const fx = { rows: null };
  $("#exploreBtn").addEventListener("click", async () => {
    factsDlg.showModal();
    $("#fxSearch").focus();
    if (fx.rows) return renderFacts();
    $("#fxCount").textContent = "Loading…";
    try {
      const r = await api("/api/facts");
      fx.rows = r.facts;
      const periods = [...new Set(fx.rows.map((x) => x.period))];
      $("#fxPeriod").replaceChildren(el("option", { value: "", text: "All periods" }), ...periods.map((p) => el("option", { value: p, text: p })));
      renderFacts();
    } catch (err) { $("#fxCount").textContent = err.message || "Couldn't load the figures."; }
  });
  factsDlg.querySelector("[data-close-facts]").addEventListener("click", () => factsDlg.close());
  // "input" for the search box only: a "change" event fires when the box loses focus, which would
  // re-render the table between mousedown and click and swallow the click on an Ask button.
  $("#fxSearch").addEventListener("input", () => renderFacts());
  ["#fxPeriod", "#fxDims"].forEach((s) => $(s).addEventListener("change", () => renderFacts()));
  function renderFacts() {
    if (!fx.rows) return;
    const q = $("#fxSearch").value.trim().toLowerCase(), per = $("#fxPeriod").value, dims = $("#fxDims").checked;
    const words = q.split(/\s+/).filter(Boolean);
    const hits = fx.rows.filter((x) => (!per || x.period === per) && (dims || !x.dims.length) &&
      words.every((w) => (x.label + " " + x.concept + " " + x.dims.join(" ")).toLowerCase().includes(w)));
    const shown = hits.slice(0, 250);
    $("#fxBody").replaceChildren(...shown.map((x) => el("tr", {},
      el("td", { title: x.concept }, x.label, x.ext ? el("span", { class: "fx-ext", text: "company-specific" }) : null),
      el("td", { class: "num", title: x.exact, text: x.value }),
      el("td", { text: x.period }),
      el("td", { text: x.dims.join("; ") || "—" }),
      el("td", {}, el("button", { class: "link-btn", type: "button", text: "Ask", onclick: () => askAbout(x) })))));
    $("#fxCount").textContent = `${hits.length.toLocaleString("en-GB")} of ${fx.rows.length.toLocaleString("en-GB")} figures` +
      (hits.length > shown.length ? ` · showing the first ${shown.length}, search to narrow down` : "") +
      (!hits.length && (!dims || per) ? " · try ticking Include breakdowns or choosing All periods" : "");
  }
  function askAbout(x) {
    factsDlg.close();
    const when = x.period ? ` (${x.period.charAt(0).toLowerCase() + x.period.slice(1)})` : "";
    const dim = x.dims.length ? ` for ${x.dims.join(", ")}` : "";
    $("#question").value = `What was the value of ${x.label.toLowerCase()}${dim}${when}?`;
    $("#question").focus();
  }

  // ------------------------------------------------------------------ ask
  $("#askForm").addEventListener("submit", (e) => { e.preventDefault(); ask($("#question").value); });
  $("#question").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask($("#question").value); } });

  async function ask(q) {
    q = (q || "").trim();
    if (!q || state.busy) return;
    if (!state.filing) return;
    $("#askError").hidden = true;
    const feed = $("#feed"); feed.querySelector(".empty")?.remove();
    const pending = el("article", { class: "card pending" },
      el("header", { class: "card-head" }, el("p", { class: "card-q", text: q }), el("p", { class: "card-meta", text: modelLabel() })),
      el("div", { class: "card-body" }, el("div", { class: "spinner" }), "Retrieving tagged facts and checking the answer…"));
    feed.prepend(pending);
    state.busy = true; $("#askBtn").disabled = true; $("#question").value = "";
    try {
      const r = await api("/api/ask", { json: { question: q, model: state.model } });
      pending.replaceWith(renderCard(r)); renderQuota(r.quota);
    } catch (err) {
      pending.remove();
      if (err.quota) renderQuota(err.quota);
      if (["session_quota", "ip_quota", "code_exhausted", "service_paused"].includes(err.error)) showQuotaWall(err.error);
      else if (err.error === "locked") openUnlock();
      else { $("#askError").textContent = err.message || "Something went wrong."; $("#askError").hidden = false; $("#question").value = q; }
      if (!feed.childElementCount) feed.append(el("div", { class: "empty" }, el("p", { class: "empty-title", text: "Ask a question to see an evidence card." })));
    } finally { state.busy = false; $("#askBtn").disabled = !state.filing; }
  }
  const modelLabel = () => (state.model === "locked" ? "Claude Sonnet 4.6" : "GPT-5.6 Terra");

  // ------------------------------------------------------------------ cards
  function badge(st) {
    return el("div", {}, el("span", { class: `badge ${st.level}`, text: st.label }), el("p", { class: "status-detail", text: st.detail }));
  }
  function factTags(f) {
    return el("div", { class: "facts-row" },
      f.period && el("span", { class: "tag", text: f.period }),
      ...(f.dimensions || []).map((d) => el("span", { class: "tag dim", title: d.raw, text: `${d.axis}: ${d.member}` })),
      !(f.dimensions || []).length && el("span", { class: "tag", text: "Consolidated (no dimension)" }),
      el("span", { class: "tag", text: f.taxonomy }),
      el("span", { class: "tag src", title: `context ${f.context_id}`, text: `F${f.fact_id}` }));
  }

  function renderCard(r) {
    const body = el("div", { class: "card-body" });
    const head = el("header", { class: "card-head" },
      el("p", { class: "card-q", text: r.question }),
      el("p", { class: "card-meta", text: `${r.model} · ${r.elapsed_s}s` }));
    const card = el("article", { class: "card" }, head, body);
    const st = r.status || { level: "unverified", label: "Not verified", detail: "" };

    if (r.kind === "fact") {
      const f = r.fact;
      body.append(el("div", { class: "card-top" },
        el("div", {}, el("p", { class: "value", text: f.value.display }),
          f.value.exact !== f.value.display && el("p", { class: "value-exact", text: f.value.exact })),
        badge(st)));
      body.append(el("p", { class: "concept", text: f.label }), el("div", { class: "qname", text: f.concept }), factTags(f));
      if (f.displayed_as != null && f.scale != null) body.append(el("p", { class: "value-exact", text: `Displayed in the filing as “${f.displayed_as}” with scale ${f.scale}.` }));
      if (r.summary) body.append(el("p", { class: "summary", text: r.summary }));
      (r.warnings || []).forEach((w) => body.append(el("p", { class: "warn", text: w })));
      if (r.alternatives && r.alternatives.length) {
        body.append(el("details", { class: "more" },
          el("summary", { text: `Other tagged values for this concept and period (${r.alternatives.length})` }),
          el("p", { class: "muted small", text: "Filings often tag several defensible values for one concept, e.g. underlying vs statutory or different dimensional members." }),
          evTable(r.alternatives)));
      }
    } else if (r.kind === "computation") {
      body.append(el("div", { class: "card-top" },
        el("div", {}, el("p", { class: "value", text: r.value.display })), badge(st)));
      body.append(el("div", { class: "formula", text: r.formula }));
      body.append(el("div", { class: "inputs" }, ...r.inputs.map((f) =>
        el("div", { class: "input-row" }, el("span", { text: f.label }), el("span", { class: "v", text: f.value.exact }),
          el("span", { class: "p", text: [f.period, ...(f.dimensions || []).map((d) => d.member)].join(" · ") }),
          el("span", { class: "p", style: "text-align:right", text: `F${f.fact_id}` })))));
      if (r.summary) body.append(el("p", { class: "summary", text: r.summary }));
      (r.warnings || []).forEach((w) => body.append(el("p", { class: "warn", text: w })));
    } else if (r.kind === "narrative") {
      body.append(el("div", { class: "card-top" },
        el("div", {}, el("p", { class: "concept", style: "margin-top:0", text: r.source.label }),
          el("div", { class: "qname", text: r.source.concept })), badge(st)));
      if (r.summary) body.append(el("p", { class: "summary", text: r.summary }));
      if (r.quote) body.append(el("blockquote", { class: "quote", text: `“${r.quote}”` }));
      body.append(el("div", { class: "facts-row" },
        r.source.period && el("span", { class: "tag", text: r.source.period }),
        el("span", { class: "tag", text: r.source.taxonomy }),
        el("span", { class: "tag src", text: `N${r.source.narrative_id}` }),
        r.source.chars && el("span", { class: "tag", text: `${Number(r.source.chars).toLocaleString("en-GB")} chars tagged` })));
    } else if (r.kind === "summary") {
      body.append(el("div", { class: "card-top" }, el("p", { class: "concept", style: "margin-top:0", text: "Headline figures" }), badge(st)));
      body.append(el("ul", { class: "metrics" }, ...(r.metrics || []).map((m) => el("li", {},
        el("span", { class: "m-label", text: m.label }), el("span", { class: "m-value", text: m.value.display }),
        el("span", { class: "m-period", text: m.period }), el("span", { class: "m-change muted", text: m.fact_id ? `F${m.fact_id}` : "" })))));
    } else {
      // not_found or unverified
      body.append(el("div", { class: "card-top" },
        el("p", { class: "concept", style: "margin-top:0", text: r.kind === "not_found" ? "Not found in the tagged data" : "Answer could not be verified" }),
        badge(st)));
      if (r.summary) body.append(el("p", { class: "summary", text: r.summary }));
      if (r.kind === "not_found") body.append(el("p", { class: "muted small", text: "Declining to answer is intended behaviour: the system won't produce a figure the filing doesn't tag. Try naming the concept (e.g. “profit before tax”) or the period." }));
    }
    if (r.evidence && r.evidence.length && !window.__evidenceShown) {
      window.__evidenceShown = true;
      const cited = new Set([r.fact && r.fact.fact_id, ...((r.inputs || []).map((i) => i.fact_id))]);
      body.append(el("details", { class: "more" },
        el("summary", { text: `Retrieved evidence (${r.evidence.length} tagged fact${r.evidence.length === 1 ? "" : "s"})` }),
        r.search_terms && r.search_terms.length && el("p", { class: "muted small", text: `Search terms: ${r.search_terms.join(", ")}` }),
        evTable(r.evidence, cited)));
    }
    body.append(el("div", { class: "card-foot" },
      el("span", { text: "Always check figures against the filing before relying on them." }),
      el("button", { class: "link-btn copy", type: "button", text: "Copy citation", onclick: (e) => copyCitation(r, e.target) })));
    if (r.answer_id) body.append(voteRow(r.answer_id));
    return card;
  }

  function evTable(rows, cited = new Set()) {
    return el("table", { class: "ev-table" },
      el("thead", {}, el("tr", {}, ...["ID", "Concept", "Period", "Dimension", "Value"].map((h) => el("th", { text: h })))),
      el("tbody", {}, ...rows.map((f) => el("tr", { class: cited.has(f.fact_id) ? "cited" : "" },
        el("td", { text: `F${f.fact_id}` }),
        el("td", { title: f.concept, text: f.label }),
        el("td", { text: f.period }),
        el("td", { text: (f.dimensions || []).map((d) => `${d.axis}: ${d.member}`).join("; ") || "—" }),
        el("td", { class: "num", text: f.value.exact })))));
  }

  // Thumbs up/down. Only the answer id and the vote are sent; the server looks
  // up what was answered, so a vote can't be attached to a made-up answer.
  const SVGNS = "http://www.w3.org/2000/svg";
  function thumb(down) {
    const s = document.createElementNS(SVGNS, "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(SVGNS, "path");
    path.setAttribute("d", "M7 11v9H4v-9h3zm2 9h8.2a2 2 0 0 0 2-1.6l1.2-6A2 2 0 0 0 18.4 10H14V6a2 2 0 0 0-2-2l-3 7v9z");
    s.append(path);
    if (down) s.style.transform = "rotate(180deg)";
    return s;
  }
  function voteRow(answerId) {
    const note = el("span", { class: "vote-note", text: "Was this answer right?" });
    const row = el("div", { class: "vote-row" }, note);
    const buttons = ["up", "down"].map((v) => {
      const b = el("button", { class: "vote-btn", type: "button", "aria-pressed": "false",
        "aria-label": v === "up" ? "Yes, this answer was right" : "No, this answer was wrong",
        title: v === "up" ? "Right" : "Wrong" });
      b.append(thumb(v === "down"));
      b.addEventListener("click", async () => {
        buttons.forEach((x) => (x.disabled = true));
        try {
          await api("/api/feedback", { json: { answer_id: answerId, vote: v } });
          buttons.forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
          note.textContent = v === "up" ? "Thanks! Glad it helped." : "Thanks. This helps measure where it goes wrong.";
        } catch (err) {
          note.textContent = err.message || "Couldn't save your vote.";
        } finally { buttons.forEach((x) => (x.disabled = false)); }
      });
      return b;
    });
    row.append(...buttons);
    return row;
  }

  function copyCitation(r, btn) {
    let t = `Q: ${r.question}\n`;
    if (r.kind === "fact") t += `A: ${r.fact.value.exact} — ${r.fact.concept}, ${r.fact.period}, context ${r.fact.context_id} (fact F${r.fact.fact_id})`;
    else if (r.kind === "computation") t += `A: ${r.value.display} = ${r.formula}`;
    else if (r.kind === "narrative") t += `A: ${r.summary}\nSource: ${r.source.concept}${r.quote ? `\n“${r.quote}”` : ""}`;
    else t += `A: ${r.summary || r.status.label}`;
    t += `\nFiling: ${state.filing ? state.filing.entity : ""} · Status: ${r.status.label} · Model: ${r.model}`;
    navigator.clipboard?.writeText(t).then(() => { btn.textContent = "Copied"; setTimeout(() => (btn.textContent = "Copy citation"), 1500); });
  }

  // ------------------------------------------------------------------ boot
  (async () => {
    try {
      const s = await api("/api/state");
      renderQuota(s.quota);
      const list = $("#sampleList");
      for (const x of s.samples || []) list.append(el("button", { class: "chip", type: "button", text: x.label, onclick: () => loadSample(x.id, x.label) }));
      $("#samples").hidden = !list.childElementCount;
      if (s.filing) onFiling(s.filing);
    } catch (err) { showUploadError(err.message || ""); }
  })();
})();
