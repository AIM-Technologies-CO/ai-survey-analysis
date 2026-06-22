// Validation (backtest) tab. Reuses the global helpers declared in app.js
// ($, $$, api, escapeHtml, toast, fmtUSD, typeLabel, createLoader, getSessionId).
(function () {
  // Question types the AI can predict (mirror predictor.VALID_AD_HOC_TYPES).
  const PREDICTABLE = new Set(["multipleChoice", "checkBoxes", "shortAnswer", "numericAnswer"]);

  const btState = {
    sessionId: getSessionId(),
    surveyId: null,
    surveyName: null,
    filter: { date_from: null, date_to: null, include_all: false },
    eligible: null,
    cap: null,
    questions: [],        // [{sqlQuestionId, label, text, type, section}]
    seed: new Set(),      // selected seed sqlQuestionIds (numbers)
    exclude: new Set(),   // held-out qids the user removed from the prediction set
    preview: null,        // {perRespondentUsd, eligible, cap}
    loaded: false,
  };

  function btTrack(action, payload = {}) {
    api("/api/track", {
      method: "POST",
      body: JSON.stringify({ session_id: btState.sessionId, survey_id: btState.surveyId, action, payload }),
    }).catch(() => {});
  }

  // ---------- Step 1: surveys -------------------------------------------------
  let btSearchTimer = null;

  async function btLoadSurveys(search) {
    const root = $("#bt-survey-list");
    root.innerHTML = `<div class="hint"><span class="spinner"></span>Loading surveys…</div>`;
    try {
      const params = new URLSearchParams();
      const searching = !!(search && search.trim());
      if (searching) params.set("search", search.trim());
      params.set("limit", searching ? "50" : "15");
      const surveys = await api(`/api/surveys?${params.toString()}`);
      root.innerHTML = "";
      if (!surveys.length) {
        root.innerHTML = `<div class="hint">No surveys match.</div>`;
        $("#bt-survey-search-meta").textContent = "";
        return;
      }
      for (const s of surveys) {
        const tile = document.createElement("div");
        tile.className = "survey-tile";
        tile.innerHTML = `<div class="name">${escapeHtml(s.name)}</div>
          <div class="count">click to filter respondents</div>`;
        tile.onclick = () => btSelectSurvey(s, tile);
        root.appendChild(tile);
      }
      $("#bt-survey-search-meta").textContent = `${surveys.length} shown`;
    } catch (e) {
      root.innerHTML = `<div class="hint" style="color: var(--bad)">Error: ${escapeHtml(e.message)}</div>`;
    }
  }

  $("#bt-survey-search").oninput = (e) => {
    clearTimeout(btSearchTimer);
    const v = e.target.value;
    btSearchTimer = setTimeout(() => btLoadSurveys(v), 300);
  };

  async function btSelectSurvey(s, tile) {
    $$("#bt-survey-list .survey-tile").forEach(t => t.classList.remove("selected"));
    tile.classList.add("selected");
    btState.surveyId = s.id;
    btState.surveyName = s.name;
    btState.eligible = null;
    btState.questions = [];
    btState.seed = new Set();
    btState.exclude = new Set();
    btState.filter = { date_from: null, date_to: null, include_all: false };

    $("#bt-step-filter").classList.remove("hidden");
    $("#bt-step-seed").classList.add("hidden");
    $("#bt-step-preview").classList.add("hidden");
    $("#bt-step-generate").classList.add("hidden");
    $("#bt-eligible-meta").textContent = "";
    $("#bt-date-from").value = "";
    $("#bt-date-to").value = "";
    $("#bt-include-all").checked = false;

    btTrack("survey_selected", { name: s.name });

    try {
      const b = await api(`/api/surveys/${s.id}/date-bounds`);
      if (b.min) {
        const minD = b.min.slice(0, 10), maxD = b.max ? b.max.slice(0, 10) : minD;
        $("#bt-date-from").min = minD; $("#bt-date-from").max = maxD;
        $("#bt-date-to").min = minD; $("#bt-date-to").max = maxD;
        $("#bt-date-from").value = minD; $("#bt-date-to").value = maxD;
        $("#bt-eligible-meta").textContent = `submissions ${minD} → ${maxD}`;
      } else {
        $("#bt-eligible-meta").textContent = "no submitted respondents";
      }
    } catch (e) { /* non-fatal */ }

    $("#bt-step-filter").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------- Step 2: filter --------------------------------------------------
  function btReadFilter() {
    const all = $("#bt-include-all").checked;
    return {
      include_all: all,
      date_from: all ? null : ($("#bt-date-from").value || null),
      date_to: all ? null : ($("#bt-date-to").value || null),
    };
  }

  $("#bt-include-all").onchange = () => {
    const dis = $("#bt-include-all").checked;
    $("#bt-date-from").disabled = dis;
    $("#bt-date-to").disabled = dis;
  };

  $("#bt-apply-filter-btn").onclick = async () => {
    btState.filter = btReadFilter();
    const btn = $("#bt-apply-filter-btn");
    btn.disabled = true;
    $("#bt-eligible-meta").textContent = "counting…";
    try {
      const res = await api("/api/eligible-count", {
        method: "POST",
        body: JSON.stringify({ survey_id: btState.surveyId, filter: btState.filter, session_id: btState.sessionId }),
      });
      btState.eligible = res.eligible;
      btState.cap = res.cap;
      $("#bt-eligible-meta").textContent = `${res.eligible.toLocaleString()} eligible respondents (submitted, not excluded)`;
      if (res.eligible === 0) { toast("No eligible respondents for this filter"); return; }
      await btLoadQuestions();
      $("#bt-step-seed").classList.remove("hidden");
      $("#bt-generate-meta").textContent = `cohort ${res.eligible.toLocaleString()} · capped at ${res.cap}`;
      $("#bt-step-seed").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      $("#bt-eligible-meta").textContent = "";
      toast(`Filter failed: ${e.message}`);
    } finally {
      btn.disabled = false;
    }
  };

  // ---------- Step 3: seed-question picker ------------------------------------
  async function btLoadQuestions() {
    const root = $("#bt-seed-list");
    root.innerHTML = `<div class="hint" style="margin:0;padding:8px"><span class="spinner"></span>Loading questions…</div>`;
    const detail = await api(`/api/surveys/${btState.surveyId}`);
    btState.questions = (detail.questions || []).map(q => ({
      sqlQuestionId: q.sqlQuestionId,
      label: q.label, text: q.text, type: q.type, section: q.section,
      options: q.options || [],
    }));
    btState.seed = new Set();
    btState.exclude = new Set();
    $("#bt-q-search").value = "";
    btPaintSeed("");
    btUpdateSel();
  }

  // Predictable, non-seed questions = the candidate held-out set (before user removals).
  function btCandidates() {
    return btState.questions.filter(q => PREDICTABLE.has(q.type) && !btState.seed.has(q.sqlQuestionId));
  }

  function btPaintSeed(filter) {
    const f = (filter || "").trim().toLowerCase();
    const items = btState.questions.filter(q =>
      !f || (q.label || "").toLowerCase().includes(f) || (q.text || "").toLowerCase().includes(f));
    const root = $("#bt-seed-list");
    if (!items.length) {
      root.innerHTML = `<div class="hint" style="margin:0;padding:8px">No questions match “${escapeHtml(f)}”.</div>`;
      return;
    }
    // Group questions under their survey section, preserving first-seen (survey) order.
    const groups = [];
    const pos = {};
    for (const q of items) {
      const sec = (q.section || "").trim() || "Other";
      if (!(sec in pos)) { pos[sec] = groups.length; groups.push({ section: sec, rows: [] }); }
      groups[pos[sec]].rows.push(q);
    }
    const row = (q) => {
      const checked = btState.seed.has(q.sqlQuestionId) ? "checked" : "";
      const name = q.label || q.text || `Q${q.sqlQuestionId}`;
      const t = q.type ? `<span class="lt">${escapeHtml(typeLabel(q.type))}</span>` : "";
      const optLabels = (q.options || []).map(o => o.label).filter(Boolean);
      const tip = escapeHtml(btTip(q.text, optLabels));
      return `<label class="seg-litem" title="${tip}"><input type="checkbox" value="${q.sqlQuestionId}" ${checked} />` +
             `<span class="li-name">${escapeHtml(name)}</span>${t}</label>`;
    };
    root.innerHTML = groups.map(g =>
      `<div class="bt-seclabel">${escapeHtml(g.section)} <span class="bt-seccount">${g.rows.length}</span></div>` +
      g.rows.map(row).join("")
    ).join("");
    root.querySelectorAll("input").forEach(cb => cb.addEventListener("change", () => {
      const qid = parseInt(cb.value, 10);
      if (cb.checked) { btState.seed.add(qid); btState.exclude.delete(qid); }  // seeding clears any prior removal
      else btState.seed.delete(qid);
      btUpdateSel();
    }));
  }

  // The held-out questions (seed excluded). Tick one = "AI should NOT answer it" (exclude).
  function btPaintPredict() {
    const candidates = btCandidates();
    const block = $("#bt-predict-block");
    block.classList.toggle("hidden", candidates.length === 0);
    if (!candidates.length) return;

    const groups = [];
    const pos = {};
    for (const q of candidates) {
      const sec = (q.section || "").trim() || "Other";
      if (!(sec in pos)) { pos[sec] = groups.length; groups.push({ section: sec, rows: [] }); }
      groups[pos[sec]].rows.push(q);
    }
    const row = (q) => {
      const checked = btState.exclude.has(q.sqlQuestionId) ? "checked" : "";
      const name = q.label || q.text || `Q${q.sqlQuestionId}`;
      const t = q.type ? `<span class="lt">${escapeHtml(typeLabel(q.type))}</span>` : "";
      const optLabels = (q.options || []).map(o => o.label).filter(Boolean);
      const tip = escapeHtml(btTip(q.text, optLabels));
      return `<label class="seg-litem bt-exclitem" title="${tip}"><input type="checkbox" value="${q.sqlQuestionId}" ${checked} />` +
             `<span class="li-name">${escapeHtml(name)}</span>${t}</label>`;
    };
    $("#bt-predict-list").innerHTML = groups.map(g =>
      `<div class="bt-seclabel">${escapeHtml(g.section)} <span class="bt-seccount">${g.rows.length}</span></div>` +
      g.rows.map(row).join("")
    ).join("");
    $("#bt-predict-list").querySelectorAll("input").forEach(cb => cb.addEventListener("change", () => {
      const qid = parseInt(cb.value, 10);
      if (cb.checked) btState.exclude.add(qid); else btState.exclude.delete(qid);
      btUpdateSel();
    }));
  }

  function btUpdateSel() {
    const n = btState.seed.size;
    const candidates = btCandidates();
    const excluded = candidates.filter(q => btState.exclude.has(q.sqlQuestionId)).length;
    const toPredict = candidates.length - excluded;
    $("#bt-sel-count").textContent = `${n} seed · ${toPredict} to predict`;
    $("#bt-predict-count").textContent = `${toPredict} the AI will answer${excluded ? ` · ${excluded} excluded` : ""}`;
    $("#bt-restore-all").classList.toggle("hidden", excluded === 0);
    $("#bt-preview-btn").disabled = !(n > 0 && toPredict > 0);
    btPaintPredict();
  }

  $("#bt-all").addEventListener("click", () => {
    $("#bt-seed-list").querySelectorAll("input").forEach(cb => {
      const qid = parseInt(cb.value, 10);
      cb.checked = true; btState.seed.add(qid); btState.exclude.delete(qid);
    });
    btUpdateSel();
  });
  $("#bt-none").addEventListener("click", () => {
    btState.seed.clear();
    $("#bt-seed-list").querySelectorAll("input").forEach(cb => { cb.checked = false; });
    btUpdateSel();
  });
  $("#bt-restore-all").addEventListener("click", () => { btState.exclude.clear(); btUpdateSel(); });
  let btQTimer = null;
  $("#bt-q-search").addEventListener("input", (e) => {
    clearTimeout(btQTimer);
    const v = e.target.value;
    btQTimer = setTimeout(() => btPaintSeed(v), 120);
  });

  // ---------- Step 4: preview -------------------------------------------------
  const BT_MSGS = [
    "Sampling respondents…",
    "Feeding the AI their seed answers…",
    "Predicting the held-out answers…",
    "Comparing predictions to reality…",
    "Scoring the matches…",
  ];
  let btLoader = null;

  function btStartLoading() {
    btLoader = createLoader(BT_MSGS, "AI is predicting held-out answers for 10 respondents");
    const root = $("#bt-preview-area");
    root.innerHTML = "";
    root.appendChild(btLoader.node);
  }
  function btStopLoading() { if (btLoader) { btLoader.stop(); btLoader = null; } }

  $("#bt-preview-btn").onclick = async () => {
    if (!btState.seed.size) { toast("Pick at least one seed question"); return; }
    const btn = $("#bt-preview-btn");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>Predicting…`;
    $("#bt-step-preview").classList.remove("hidden");
    $("#bt-preview-meta").textContent = "";
    btStartLoading();
    try {
      const res = await api("/api/backtest/preview", {
        method: "POST",
        body: JSON.stringify({
          survey_id: btState.surveyId, filter: btState.filter,
          seed_qids: Array.from(btState.seed), exclude_qids: Array.from(btState.exclude),
          session_id: btState.sessionId,
        }),
      });
      btStopLoading();
      btRenderPreview(res);
      $("#bt-step-generate").classList.remove("hidden");
      $("#bt-step-preview").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      btStopLoading();
      $("#bt-preview-area").innerHTML = `<div class="hint" style="color: var(--bad)">Error: ${escapeHtml(e.message)}</div>`;
    } finally {
      btStopLoading();
      btn.disabled = false;
      btn.textContent = "Preview on 10 →";
    }
  };

  function accPct(x) { return (x == null) ? "—" : `${Math.round(x * 100)}%`; }

  // Tooltip text for a question label: full question text + its choices, one per line.
  function btTip(text, options) {
    const opts = (options || []).filter(Boolean);
    const optBlock = opts.length ? `\n\nChoices:\n${opts.map(o => `• ${o}`).join("\n")}` : "";
    return `${text || ""}${optBlock}`.trim();
  }

  // ----- swipeable respondent carousel (own state, separate from app.js) -----
  let btSlides = [];
  let btIndex = 0;

  function btMatchBadge(c) {
    if (!c.scored) return `<span class="bt-badge bt-na">open-ended</span>`;
    if (c.match) return `<span class="bt-badge bt-ok">✓ match</span>`;
    const ov = (c.overlap != null) ? ` ${Math.round(c.overlap * 100)}%` : "";
    return `<span class="bt-badge bt-bad">✗ miss${ov}</span>`;
  }

  function btBuildCard(r, idx) {
    const card = document.createElement("div");
    card.className = "preview-resp";

    // Seed answers (collapsed) — the persona the AI was given.
    const seedRows = (r.seed || []).map(a => {
      const label = a.label || "", qtext = a.text || "";
      const tip = btTip(qtext, a.options);
      const term = (label && qtext && label !== qtext)
        ? `<dt><details class="pv-q"><summary dir="auto" title="${escapeHtml(tip)}">${escapeHtml(label)}</summary><div class="pv-qfull" dir="auto">${escapeHtml(tip)}</div></details></dt>`
        : `<dt dir="auto" title="${escapeHtml(tip)}">${escapeHtml(label || qtext)}</dt>`;
      return `${term}<dd dir="auto">${escapeHtml(a.answer)}</dd>`;
    }).join("");

    const compHtml = (r.comparisons || []).map(c => {
      const refs = (c.references || []).map(rf =>
        `<span class="chip-ref"><b>${escapeHtml(rf.label)}</b> → <bdi>${escapeHtml(rf.answer)}</bdi></span>`).join("");
      const predicted = (c.predicted || []).map(v => `<span class="pill ai" dir="auto">${escapeHtml(v)}</span>`).join("") || "—";
      // Show the short label; reveal the full question text (and its choices) on hover.
      const qname = c.label || c.text || "";
      const tip = btTip(c.text, c.options);
      const hasMore = tip && tip !== qname;
      const qhtml = hasMore
        ? `<span class="bt-qlabel" title="${escapeHtml(tip)}" dir="auto">${escapeHtml(qname)}</span>`
        : `<span dir="auto">${escapeHtml(qname)}</span>`;
      return `<div class="bt-cmp ${c.scored ? (c.match ? "is-ok" : "is-bad") : "is-na"}">
        <div class="bt-cmp-head">
          <span class="pv-gq">${qhtml} <span class="qtype">(${escapeHtml(typeLabel(c.type))})</span></span>
          ${btMatchBadge(c)}
        </div>
        <div class="bt-cmp-cols">
          <div><label class="bt-lbl">Real</label><div><span class="pill" dir="auto">${escapeHtml(c.actual)}</span></div></div>
          <div><label class="bt-lbl">AI predicted</label><div>${predicted}</div></div>
        </div>
        ${c.reason ? `<div class="reason">"${escapeHtml(c.reason)}"</div>` : ""}
        ${refs ? `<div class="refrow"><span class="grounded">grounded in</span>${refs}</div>` : ""}
      </div>`;
    }).join("") || (r.error ? `<div class="hint" style="color: var(--bad); margin:0">${escapeHtml(r.error)}</div>`
                            : `<div class="hint" style="margin:0">No held-out answers to compare.</div>`);

    const dateChip = r.submitDate ? `<span class="pv-date">${escapeHtml(r.submitDate.slice(0, 10))}</span>` : "";
    const accChip = `<span class="bt-acc" title="${r.matched}/${r.scored} scored held-out questions matched">accuracy ${accPct(r.accuracy)}</span>`;
    card.innerHTML = `
      <div class="pv-head">
        <span class="pv-idx">${idx + 1}</span>
        <span class="pv-title">Respondent ${idx + 1}</span>
        <span class="pv-id">${escapeHtml(String(r.id).slice(-8))}</span>
        ${dateChip}
        ${accChip}
      </div>
      <details class="pv-real">
        <summary>Seed answers given to the AI (${(r.seed || []).length})</summary>
        ${seedRows ? `<dl class="pv-grid">${seedRows}</dl>` : `<div class="hint" style="margin:0">no seed answers</div>`}
      </details>
      <div class="bt-cmps">${compHtml}</div>`;
    return card;
  }

  function btShow(idx, dir) {
    if (!btSlides.length) return;
    btIndex = (idx + btSlides.length) % btSlides.length;
    btSlides.forEach((s, k) => s.classList.toggle("active", k === btIndex));
    const active = btSlides[btIndex];
    if (active && dir) {
      active.classList.remove("slide-l", "slide-r");
      void active.offsetWidth;
      active.classList.add(dir > 0 ? "slide-r" : "slide-l");
    }
    const counter = $("#bt-pv-counter");
    if (counter) counter.textContent = `Respondent ${btIndex + 1} of ${btSlides.length}`;
    const jump = $("#bt-pv-jump");
    if (jump && jump.value !== String(btIndex)) jump.value = String(btIndex);
  }

  function btRenderPreview(res) {
    let meta = `model: ${res.model} · showing ${res.sample} of ${(res.eligible || 0).toLocaleString()} eligible`;
    meta += ` · ${res.seed_count} seed → ${res.holdout_count} held-out`;
    meta += ` · overall accuracy ${accPct(res.accuracy)} (${res.matched}/${res.scored} scored)`;
    const c = res.cost;
    if (c && c.scored) {
      meta += ` · cost: ${fmtUSD(c.total_usd)} for ${c.scored} (≈ ${fmtUSD(c.per_respondent_usd)}/respondent)`;
      btState.preview = { perRespondentUsd: c.per_respondent_usd, eligible: res.eligible || 0, cap: res.cap || 0 };
      btRenderEstimate();
    }
    $("#bt-preview-meta").textContent = meta;

    const root = $("#bt-preview-area");
    root.innerHTML = "";
    btSlides = []; btIndex = 0;
    const results = res.results || [];
    if (!results.length) {
      root.innerHTML = `<div class="hint">No eligible respondents for this filter.</div>`;
      return;
    }

    const carousel = document.createElement("div");
    carousel.className = "pv-carousel";
    carousel.innerHTML = `
      <div class="pv-nav">
        <button class="pv-arrow bt-go" data-dir="-1" aria-label="Previous respondent">‹</button>
        <select class="pv-jump" id="bt-pv-jump" aria-label="Jump to respondent">
          ${results.map((r, k) => `<option value="${k}">Respondent ${k + 1}</option>`).join("")}
        </select>
        <button class="pv-arrow bt-go" data-dir="1" aria-label="Next respondent">›</button>
      </div>
      <div class="pv-viewport"><div class="pv-track" id="bt-pv-track"></div></div>
      <div class="pv-foot">
        <button class="pv-step bt-go" data-dir="-1" aria-label="Previous respondent">‹ Previous</button>
        <span class="pv-counter" id="bt-pv-counter">Respondent 1 of ${results.length}</span>
        <button class="pv-step bt-go" data-dir="1" aria-label="Next respondent">Next ›</button>
      </div>`;
    root.appendChild(carousel);

    const track = carousel.querySelector("#bt-pv-track");
    btSlides = results.map((r, idx) => {
      const slide = document.createElement("div");
      slide.className = "pv-slide";
      slide.appendChild(btBuildCard(r, idx));
      track.appendChild(slide);
      return slide;
    });

    carousel.querySelectorAll(".bt-go").forEach(b => {
      b.onclick = () => { const d = parseInt(b.dataset.dir, 10); btShow(btIndex + d, d); };
    });
    $("#bt-pv-jump").onchange = (e) => {
      const t = parseInt(e.target.value);
      btShow(t, t >= btIndex ? +1 : -1);
    };
    btShow(0);
  }

  function btRenderEstimate() {
    const el = $("#bt-generate-meta");
    if (!el) return;
    const p = btState.preview;
    if (!p || !p.perRespondentUsd) { el.textContent = ""; return; }
    const n = p.cap ? Math.min(p.eligible, p.cap) : p.eligible;
    const capNote = (p.cap && p.eligible > p.cap)
      ? ` (capped at ${p.cap.toLocaleString()} of ${p.eligible.toLocaleString()} eligible)` : "";
    el.textContent = `Est. cost: ~${fmtUSD(p.perRespondentUsd * n)} for ${n.toLocaleString()} respondent${n === 1 ? "" : "s"}${capNote}`;
  }

  // ---------- Step 5: run for all --------------------------------------------
  let btPollTimer = null;
  const BT_GEN_MSGS = [
    "Spinning up the run…",
    "Sampling the cohort…",
    "Predicting held-out answers…",
    "Scoring against real answers…",
    "Building your Excel export…",
  ];
  let btGenLoader = null;

  function btStartGenLoading() {
    btGenLoader = createLoader(BT_GEN_MSGS, "Starting…");
    const bar = document.createElement("div");
    bar.className = "prog-bar";
    bar.style.width = "100%";
    bar.style.maxWidth = "360px";
    bar.innerHTML = `<div class="prog-fill" style="width:0%"></div>`;
    btGenLoader.node.appendChild(bar);
    const root = $("#bt-progress-area");
    root.innerHTML = "";
    root.appendChild(btGenLoader.node);
  }
  function btStopGenLoading() { if (btGenLoader) { btGenLoader.stop(); btGenLoader = null; } }

  $("#bt-generate-btn").onclick = async () => {
    if (!btState.seed.size) { toast("Pick at least one seed question"); return; }
    const btn = $("#bt-generate-btn");
    btn.disabled = true;
    btStartGenLoading();
    try {
      const job = await api("/api/backtest/generate-all", {
        method: "POST",
        body: JSON.stringify({
          survey_id: btState.surveyId, filter: btState.filter,
          seed_qids: Array.from(btState.seed), exclude_qids: Array.from(btState.exclude),
          session_id: btState.sessionId,
        }),
      });
      btPollJob(job.id);
    } catch (e) {
      btStopGenLoading();
      $("#bt-progress-area").innerHTML = `<div class="hint" style="color: var(--bad)">Error: ${escapeHtml(e.message)}</div>`;
      btn.disabled = false;
    }
  };

  function btRenderProgress(j) {
    const root = $("#bt-progress-area");
    const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
    const cappedNote = j.capped ? ` (capped from ${(j.eligible || 0).toLocaleString()} eligible)` : "";

    if (j.state === "done") {
      btStopGenLoading();
      root.innerHTML = `
        <div class="loading">
          <div class="loader-msg" style="font-size:18px">✓ Done — ${j.ok} of ${j.total} respondents</div>
          <div class="loader-sub">${j.failed ? `${j.failed} failed · ` : ""}your Excel is ready</div>
          <a class="dl-btn" href="/api/jobs/${j.id}/download">⬇ Download Excel</a>
        </div>`;
      return;
    }
    if (j.state === "error") {
      btStopGenLoading();
      root.innerHTML = `<div class="hint" style="color: var(--bad)">Job failed: ${escapeHtml(j.error || "unknown")}</div>`;
      return;
    }
    if (!btGenLoader) btStartGenLoading();
    const sub = root.querySelector(".loader-sub");
    const fill = root.querySelector(".prog-fill");
    if (sub) sub.textContent = `${j.done}/${j.total}${cappedNote}${j.failed ? ` · ${j.failed} failed` : ""}`;
    if (fill) fill.style.width = pct + "%";
  }

  async function btPollJob(jobId) {
    if (btPollTimer) clearInterval(btPollTimer);
    const tick = async () => {
      try {
        const j = await api(`/api/jobs/${jobId}`);
        btRenderProgress(j);
        if (j.state === "done" || j.state === "error") {
          clearInterval(btPollTimer); btPollTimer = null;
          $("#bt-generate-btn").disabled = false;
        }
      } catch (e) {
        clearInterval(btPollTimer); btPollTimer = null;
        btStopGenLoading();
        $("#bt-progress-area").innerHTML = `<div class="hint" style="color: var(--bad)">Lost job: ${escapeHtml(e.message)}</div>`;
        $("#bt-generate-btn").disabled = false;
      }
    };
    await tick();
    btPollTimer = setInterval(tick, 1800);
  }

  // ---------- lazy init on first tab open ------------------------------------
  const btTabBtn = document.querySelector('[data-tab="panel-backtest"]');
  if (btTabBtn) {
    btTabBtn.addEventListener("click", () => {
      if (btState.loaded) return;
      btState.loaded = true;
      btLoadSurveys("");
    });
  }
})();
