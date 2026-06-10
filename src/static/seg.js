// Segmentation tab: tab switching + survey picker / upload / run / poll / report.
// Runs after app.js and reuses its globals: $, api, toast, escapeHtml.
(function () {
  const segState = {
    source: "mongo", ref: null, selected: new Set(),
    jobId: null, lastIdx: 0, timer: null, loaded: false,
  };

  function note(el, kind, html) {
    const c = kind === "error" ? "var(--bad)" : kind === "ok" ? "var(--good)" : "var(--muted)";
    el.innerHTML = `<div class="hint" style="margin:10px 0 0;color:${c}">${html}</div>`;
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
      $("#seg-source-status").innerHTML = "";
      renderCounts(d.counts);
      renderLabels(d.candidate_labels);
      showConfig();
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
    $("#seg-sel-count").textContent = `${segState.selected.size} selected`;
    $("#seg-run-btn").disabled = !(segState.ref && segState.selected.size > 0);
  }

  $("#seg-all").addEventListener("click", () => {
    $("#seg-labels").querySelectorAll("input").forEach((cb) => { cb.checked = true; segState.selected.add(cb.value); });
    updateSel();
  });
  $("#seg-none").addEventListener("click", () => {
    $("#seg-labels").querySelectorAll("input").forEach((cb) => { cb.checked = false; });
    segState.selected.clear();
    updateSel();
  });

  function showConfig() {
    $("#seg-config").classList.remove("hidden");
    updateSel();
    $("#seg-config").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---- run + poll ----
  $("#seg-run-btn").addEventListener("click", segRun);
  async function segRun() {
    if (!segState.ref || segState.selected.size === 0) return;
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
      const res = await fetch("/api/segmentation/runs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: segState.source, ref: segState.ref,
          segment_by: [...segState.selected], additional_details: $("#seg-details").value,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const d = await res.json();
      segState.jobId = d.job_id;
      note($("#seg-run-status"), "info", `<span class="spinner"></span>Analyst working… (job ${escapeHtml(d.job_id.slice(0, 8))})`);
      segState.timer = setInterval(segPoll, 1800);
      segPoll();
    } catch (e) {
      note($("#seg-run-status"), "error", `Error: ${escapeHtml(e.message)}`);
      btn.disabled = false;
      btn.textContent = "Run Segmentation →";
    }
  }

  const TERMINAL = { succeeded: 1, failed: 1, timed_out: 1, artifacts_missing: 1 };
  async function segPoll() {
    if (!segState.jobId) return;
    try {
      const d = await api(`/api/segmentation/runs/${segState.jobId}?since=${segState.lastIdx}`);
      const log = $("#seg-log");
      for (const ev of d.events) {
        const div = document.createElement("div");
        div.className = "l l-" + ev.kind;
        div.textContent = `[${ev.kind}] ${ev.message}`;
        log.appendChild(div);
      }
      segState.lastIdx += d.events.length;
      log.scrollTop = log.scrollHeight;
      if (TERMINAL[d.status]) {
        clearInterval(segState.timer);
        segState.timer = null;
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
        } else {
          note($("#seg-run-status"), "error", `Run ${escapeHtml(d.status)}: ${escapeHtml(d.error || "see log above")}${cost}`);
        }
      }
    } catch (e) {
      clearInterval(segState.timer);
      segState.timer = null;
      note($("#seg-run-status"), "error", `Polling error: ${escapeHtml(e.message)}`);
      $("#seg-run-btn").disabled = false;
      $("#seg-run-btn").textContent = "Run Segmentation →";
    }
  }
})();
