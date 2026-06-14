// Segmentation tab: tab switching + survey picker / upload / run / poll / report.
// Runs after app.js and reuses its globals: $, api, toast, escapeHtml.
(function () {
  const MIN_Q = 3;
  const segState = {
    source: "mongo", ref: null, selected: new Set(), mode: "ai", aiPlan: null,
    jobId: null, lastIdx: 0, timer: null, loaded: false, recountTimer: null,
  };

  function note(el, kind, html) {
    const c = kind === "error" ? "var(--bad)" : kind === "ok" ? "var(--good)" : "var(--muted)";
    el.innerHTML = `<div class="hint" style="margin:10px 0 0;color:${c}">${html}</div>`;
  }

  // Plain-language presentation of each event kind for the live activity log.
  const KIND_META = {
    status:         { icon: "•",  label: "" },
    tool_use:       { icon: "⚙",  label: "" },
    tool_result:    { icon: "↳",  label: "" },
    assistant_text: { icon: "🧠", label: "Analyst" },
    result:         { icon: "✓",  label: "" },
    error:          { icon: "⚠",  label: "" },
  };

  function appendLog(log, ev) {
    const meta = KIND_META[ev.kind] || { icon: "•", label: "" };
    const div = document.createElement("div");
    div.className = "l l-" + ev.kind;
    const lbl = meta.label ? `<span class="lbl">${escapeHtml(meta.label)}</span> ` : "";
    div.innerHTML = `<span class="ic">${meta.icon}</span> ${lbl}<span class="msg">${escapeHtml(ev.message)}</span>`;
    log.appendChild(div);
  }

  // ---- top tab switching ----
  document.querySelectorAll(".tab").forEach((t) => {
    t.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      t.classList.add("active");
      const panel = document.getElementById(t.dataset.tab);
      if (panel) panel.classList.add("active");
      if (t.dataset.tab === "panel-seg" && !segState.loaded) {
        segState.loaded = true;
        segSearch("");
      }
    });
  });

  // ---- source toggle (DB | upload) ----
  document.querySelectorAll(".seg-src").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".seg-src").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      segState.source = b.dataset.src;
      $("#seg-db").classList.toggle("hidden", segState.source !== "mongo");
      $("#seg-upload").classList.toggle("hidden", segState.source !== "upload");
      segState.ref = null;
      segState.selected.clear();
      $("#seg-config").classList.add("hidden");
      $("#seg-source-status").innerHTML = "";
    });
  });

  // ---- DB survey search (reuses shared /api/surveys) ----
  let segTimer = null;
  $("#seg-survey-search").addEventListener("input", (e) => {
    clearTimeout(segTimer);
    const v = e.target.value;
    segTimer = setTimeout(() => segSearch(v), 300);
  });

  async function segSearch(q) {
    const root = $("#seg-survey-list");
    root.innerHTML = `<div class="hint"><span class="spinner"></span>Loading surveys…</div>`;
    try {
      const params = new URLSearchParams();
      const searching = !!(q && q.trim());
      if (searching) params.set("search", q.trim());
      params.set("limit", searching ? "50" : "15");
      const surveys = await api(`/api/surveys?${params.toString()}`);
      root.innerHTML = "";
      if (!surveys.length) {
        root.innerHTML = `<div class="hint">No surveys match.</div>`;
        return;
      }
      for (const sv of surveys) {
        const tile = document.createElement("div");
        tile.className = "survey-tile";
        tile.innerHTML = `<div class="name">${escapeHtml(sv.name)}</div><div class="count">click to load questions</div>`;
        tile.onclick = () => selectSeg(sv, tile);
        root.appendChild(tile);
      }
      $("#seg-survey-meta").textContent = `${surveys.length} shown`;
    } catch (e) {
      root.innerHTML = `<div class="hint" style="color:var(--bad)">Error: ${escapeHtml(e.message)}</div>`;
    }
  }

  async function selectSeg(sv, tile) {
    document.querySelectorAll("#seg-survey-list .survey-tile").forEach((x) => x.classList.remove("selected"));
    tile.classList.add("selected");
    note($("#seg-source-status"), "info", `<span class="spinner"></span>Loading ${escapeHtml(sv.name)}…`);
    try {
      const d = await api(`/api/segmentation/surveys/${sv.id}`);
      segState.ref = d.id;
      segState.dateBounds = d.date_bounds || null;
      $("#seg-source-status").innerHTML = "";
      renderCounts(d.counts);
      renderLabels(d.candidate_labels);
      showConfig();
      initDateFilter();
    } catch (e) {
      note($("#seg-source-status"), "error", `Error: ${escapeHtml(e.message)}`);
    }
  }

  function renderCounts(c) {
    $("#seg-counts").innerHTML =
      `<div class="seg-chip"><b>${c.total}</b>total</div>` +
      `<div class="seg-chip"><b>${c.submitted}</b>submitted</div>` +
      `<div class="seg-chip"><b>${c.usable}</b>usable (analyzed)</div>`;
  }

  // ---- upload ----
  $("#seg-upload-btn").addEventListener("click", segUpload);
  async function segUpload() {
    const f = $("#seg-file").files[0];
    if (!f) { toast("Choose a file first"); return; }
    note($("#seg-source-status"), "info", `<span class="spinner"></span>Inspecting ${escapeHtml(f.name)}…`);
    const fd = new FormData();
    fd.append("file", f);
    try {
      const res = await fetch("/api/segmentation/upload", { method: "POST", body: fd });
      if (!res.ok) throw new Error(await res.text());
      const d = await res.json();
      segState.ref = d.upload_id;
      const fl = d.filters;
      let msg = `Loaded <strong>${escapeHtml(d.sheet_name)}</strong>. Filter: status=<strong>${escapeHtml(fl.status_filter_value)}</strong>` +
        (fl.status_detected ? ` (col ${escapeHtml(fl.status_column)})` : " <em>(no status column)</em>") +
        (fl.exclude_detected ? `, exclude col ${escapeHtml(fl.exclude_column)}` : ", <em>no exclude column</em>") + ".";
      note($("#seg-source-status"), d.warnings.length ? "error" : "ok",
        msg + (d.warnings.length ? "<br>" + d.warnings.map(escapeHtml).join("<br>") : ""));
      $("#seg-counts").innerHTML = `<div class="seg-chip"><b>${d.filters.estimated_rows ?? "?"}</b>rows (approx)</div>`;
      renderLabels(d.candidate_labels.map((l) => ({ label: l })));
      showConfig();
    } catch (e) {
      note($("#seg-source-status"), "error", `Error: ${escapeHtml(e.message)}`);
    }
  }

  // ---- labels ----
  function renderLabels(labels) {
    segState.selected.clear();
    $("#seg-labels").innerHTML = labels.map((l) => {
      const lbl = typeof l === "string" ? l : l.label;
      const t = (l && l.type) ? `<span class="lt">${escapeHtml(l.type)}</span>` : "";
      const tip = (l && l.question_text) ? escapeHtml(l.question_text) : "";
      return `<label class="seg-litem" title="${tip}"><input type="checkbox" value="${escapeHtml(lbl)}"><span>${escapeHtml(lbl)} ${t}</span></label>`;
    }).join("");
    $("#seg-labels").querySelectorAll("input").forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) segState.selected.add(cb.value);
        else segState.selected.delete(cb.value);
        updateSel();
      });
    });
    updateSel();
  }

  function updateSel() {
    const n = segState.selected.size;
    const hint = n > 0 && n < MIN_Q ? ` (pick at least ${MIN_Q})` : "";
    $("#seg-sel-count").textContent = `${n} selected${hint}`;
    const ready = !!segState.ref && (
      segState.mode === "ai"
        ? !!(segState.aiPlan && segState.aiPlan.length >= MIN_Q)  // must preview the plan first
        : n >= MIN_Q
    );
    $("#seg-run-btn").disabled = !ready;
  }

  $("#seg-none").addEventListener("click", () => {
    $("#seg-labels").querySelectorAll("input").forEach((cb) => { cb.checked = false; });
    segState.selected.clear();
    updateSel();
  });

  // ---- segment-by mode (AI plan vs manual min-3) ----
  function resetAiPlan() {
    segState.aiPlan = null;
    const out = $("#seg-ai-plan"); if (out) out.innerHTML = "";
    const btn = $("#seg-suggest-btn"); if (btn) { btn.disabled = false; btn.textContent = "✨ Preview the AI's plan"; }
  }

  document.querySelectorAll(".seg-mode").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".seg-mode").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      segState.mode = b.dataset.mode;
      const manual = segState.mode === "manual";
      $("#seg-manual").classList.toggle("hidden", !manual);
      $("#seg-ai-note").classList.toggle("hidden", manual);
      resetAiPlan();
      updateSel();
    });
  });

  // ---- AI plan preview ("let the AI choose") ----
  $("#seg-suggest-btn").addEventListener("click", suggestAxes);
  async function suggestAxes() {
    if (!segState.ref) return;
    const btn = $("#seg-suggest-btn"), out = $("#seg-ai-plan");
    btn.disabled = true; btn.textContent = "Thinking…";
    out.innerHTML = `<div class="hint"><span class="spinner"></span>Asking the AI which axes to segment by…</div>`;
    try {
      const d = await api("/api/segmentation/suggest-axes", {
        method: "POST", body: JSON.stringify({ source: segState.source, ref: segState.ref }),
      });
      const axes = d.axes || [];
      segState.aiPlan = axes.map((a) => a.label);
      const list = axes.map((a) =>
        `<li class="seg-axis"><span class="ax-name">${escapeHtml(a.label)}</span>` +
        (a.reason ? `<span class="ax-why">${escapeHtml(a.reason)}</span>` : "") + `</li>`).join("");
      const enough = segState.aiPlan.length >= MIN_Q;
      out.innerHTML =
        (d.approach ? `<div class="seg-approach">${escapeHtml(d.approach)}</div>` : "") +
        `<ul class="seg-axes-list">${list}</ul>` +
        `<div class="hint" style="margin-top:6px;color:${enough ? "var(--muted)" : "var(--bad)"}">` +
        (enough
          ? `These ${axes.length} axes will seed the run. Click Run to proceed, or re-suggest.`
          : `Only ${axes.length} usable axis(es) — need at least ${MIN_Q}. Try re-suggesting.`) +
        `</div>`;
    } catch (e) {
      segState.aiPlan = null;
      out.innerHTML = `<div class="hint" style="color:var(--bad)">Couldn't get a plan: ${escapeHtml(e.message)}</div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = segState.aiPlan && segState.aiPlan.length ? "↻ Re-suggest" : "✨ Preview the AI's plan";
      updateSel();
    }
  }

  // ---- date range filter (database source only) ----
  function initDateFilter() {
    const wrap = $("#seg-datefilter");
    if (segState.source !== "mongo") { wrap.classList.add("hidden"); return; }
    wrap.classList.remove("hidden");
    const b = segState.dateBounds || {};
    const min = b.min ? b.min.slice(0, 10) : "";
    const max = b.max ? b.max.slice(0, 10) : "";
    const from = $("#seg-date-from"), to = $("#seg-date-to");
    for (const el of [from, to]) { if (min) el.min = min; if (max) el.max = max; }
    from.value = min; to.value = max;
    $("#seg-include-all").checked = false;
    recountEligible();
  }

  function recountEligible() {
    if (segState.source !== "mongo" || !segState.ref) return;
    clearTimeout(segState.recountTimer);
    segState.recountTimer = setTimeout(async () => {
      const includeAll = $("#seg-include-all").checked;
      const meta = $("#seg-eligible-meta");
      const p = new URLSearchParams({ include_all: includeAll });
      if (!includeAll) {
        if ($("#seg-date-from").value) p.set("date_from", $("#seg-date-from").value);
        if ($("#seg-date-to").value) p.set("date_to", $("#seg-date-to").value);
      }
      meta.textContent = "counting…";
      try {
        const d = await api(`/api/segmentation/surveys/${segState.ref}/eligible?${p.toString()}`);
        meta.textContent = `${d.eligible} respondents in range`;
      } catch (e) {
        meta.textContent = "";
      }
    }, 300);
  }

  ["seg-date-from", "seg-date-to"].forEach((id) => $("#" + id).addEventListener("change", recountEligible));
  $("#seg-include-all").addEventListener("change", (e) => {
    const on = e.target.checked;
    $("#seg-date-from").disabled = on;
    $("#seg-date-to").disabled = on;
    recountEligible();
  });

  function showConfig() {
    $("#seg-config").classList.remove("hidden");
    if (segState.source !== "mongo") $("#seg-datefilter").classList.add("hidden");
    resetAiPlan();  // a fresh survey/upload invalidates any previous AI plan
    updateSel();
    $("#seg-config").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---- run + poll ----
  $("#seg-run-btn").addEventListener("click", segRun);
  async function segRun() {
    if (!segState.ref) return;
    if (segState.mode === "manual" && segState.selected.size < MIN_Q) return;
    if (segState.mode === "ai" && !(segState.aiPlan && segState.aiPlan.length >= MIN_Q)) return;
    const btn = $("#seg-run-btn");
    btn.disabled = true;
    btn.textContent = "Starting…";
    $("#seg-results").classList.remove("hidden");
    $("#seg-report-wrap").classList.add("hidden");
    $("#seg-actions").classList.add("hidden");
    const log = $("#seg-log");
    log.classList.remove("hidden");
    log.innerHTML = "";
    segState.lastIdx = 0;
    note($("#seg-run-status"), "info", "<span class=\"spinner\"></span>Submitting run…");
    try {
      const includeAll = segState.source !== "mongo" || $("#seg-include-all").checked;
      const payload = {
        source: segState.source, ref: segState.ref,
        segment_by: segState.mode === "ai" ? (segState.aiPlan || []) : [...segState.selected],
        additional_details: $("#seg-details").value,
        include_all: includeAll,
      };
      if (segState.source === "mongo" && !includeAll) {
        payload.date_from = $("#seg-date-from").value || null;
        payload.date_to = $("#seg-date-to").value || null;
      }
      const res = await fetch("/api/segmentation/runs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await res.text());
      const d = await res.json();
      segState.jobId = d.job_id;
      note($("#seg-run-status"), "info", `<span class="spinner"></span>Analyst working… (job ${escapeHtml(d.job_id.slice(0, 8))})`);
      showCancel(true);
      segState.timer = setInterval(segPoll, 1800);
      segPoll();
    } catch (e) {
      note($("#seg-run-status"), "error", `Error: ${escapeHtml(e.message)}`);
      btn.disabled = false;
      btn.textContent = "Run Segmentation →";
    }
  }

  // ---- cancel ----
  function showCancel(on) {
    const cb = $("#seg-cancel-btn");
    cb.classList.toggle("hidden", !on);
    if (on) { cb.disabled = false; cb.textContent = "■ Cancel run"; }
  }

  $("#seg-cancel-btn").addEventListener("click", segCancel);
  async function segCancel() {
    if (!segState.jobId) return;
    const cb = $("#seg-cancel-btn");
    cb.disabled = true;
    cb.textContent = "Cancelling…";
    try {
      await api(`/api/segmentation/runs/${segState.jobId}/cancel`, { method: "POST" });
      note($("#seg-run-status"), "info", "<span class=\"spinner\"></span>Cancelling — stopping the analyst…");
    } catch (e) {
      toast(`Couldn't cancel: ${e.message}`);
      cb.disabled = false;
      cb.textContent = "■ Cancel run";
    }
  }

  const TERMINAL = { succeeded: 1, failed: 1, timed_out: 1, artifacts_missing: 1, cancelled: 1 };
  async function segPoll() {
    if (!segState.jobId) return;
    try {
      const d = await api(`/api/segmentation/runs/${segState.jobId}?since=${segState.lastIdx}`);
      const log = $("#seg-log");
      for (const ev of d.events) appendLog(log, ev);
      segState.lastIdx += d.events.length;
      log.scrollTop = log.scrollHeight;
      if (TERMINAL[d.status]) {
        clearInterval(segState.timer);
        segState.timer = null;
        showCancel(false);
        const btn = $("#seg-run-btn");
        btn.disabled = false;
        btn.textContent = "Run Segmentation →";
        const cost = d.cost_usd ? ` · ~$${d.cost_usd.toFixed(2)} · ${d.num_turns} turns` : "";
        if (d.status === "succeeded") {
          note($("#seg-run-status"), "ok", `✓ Segmentation complete${cost}`);
          if (d.report_url) {
            $("#seg-report-frame").src = d.report_url;
            $("#seg-open-report").href = d.report_url;
            $("#seg-report-wrap").classList.remove("hidden");
          }
          if (d.pptx_url) { $("#seg-dl-pptx").href = d.pptx_url; $("#seg-dl-pptx").style.display = ""; }
          else { $("#seg-dl-pptx").style.display = "none"; }
          $("#seg-actions").classList.remove("hidden");
        } else if (d.status === "cancelled") {
          note($("#seg-run-status"), "info", `Run cancelled${cost}`);
        } else {
          note($("#seg-run-status"), "error", `Run ${escapeHtml(d.status)}: ${escapeHtml(d.error || "see log above")}${cost}`);
        }
      }
    } catch (e) {
      clearInterval(segState.timer);
      segState.timer = null;
      showCancel(false);
      note($("#seg-run-status"), "error", `Polling error: ${escapeHtml(e.message)}`);
      $("#seg-run-btn").disabled = false;
      $("#seg-run-btn").textContent = "Run Segmentation →";
    }
  }
})();
