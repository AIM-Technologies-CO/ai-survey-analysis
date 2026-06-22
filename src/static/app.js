// ---------- session + state ----------------------------------------------

function getSessionId() {
  let s = localStorage.getItem("ssd_session_id");
  if (!s) {
    s = (crypto.randomUUID ? crypto.randomUUID() : "s-" + Math.random().toString(36).slice(2) + Date.now());
    localStorage.setItem("ssd_session_id", s);
  }
  return s;
}

const state = {
  sessionId: getSessionId(),
  surveyId: null,
  surveyName: null,
  filter: { date_from: null, date_to: null, include_all: false },
  eligible: null,
  cap: null,
  questions: [],   // {id, text, type, options[], rationale, source:'ai'|'manual', include}
  nextId: 1,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function toast(msg, ms = 2800) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), ms);
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res.json();
}

function track(action, payload = {}) {
  // fire-and-forget breadcrumb
  api("/api/track", {
    method: "POST",
    body: JSON.stringify({ session_id: state.sessionId, survey_id: state.surveyId, action, payload }),
  }).catch(() => {});
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// ---------- Step 1: surveys (live search) ----------------------------------

let surveySearchTimer = null;

async function loadSurveys(search) {
  const root = $("#survey-list");
  root.innerHTML = `<div class="hint"><span class="spinner"></span>Loading surveys…</div>`;
  try {
    const params = new URLSearchParams();
    const searching = !!(search && search.trim());
    if (searching) params.set("search", search.trim());
    // Initial list stays short (15); a search widens it so matches aren't cut off.
    params.set("limit", searching ? "50" : "15");
    const surveys = await api(`/api/surveys?${params.toString()}`);
    root.innerHTML = "";
    if (!surveys.length) {
      root.innerHTML = `<div class="hint">No surveys match.</div>`;
      $("#survey-search-meta").textContent = "";
      return;
    }
    for (const s of surveys) {
      const tile = document.createElement("div");
      tile.className = "survey-tile";
      tile.innerHTML = `<div class="name">${escapeHtml(s.name)}</div>
        <div class="count">click to filter respondents</div>`;
      tile.onclick = () => selectSurvey(s, tile);
      root.appendChild(tile);
    }
    $("#survey-search-meta").textContent = `${surveys.length} shown`;
  } catch (e) {
    root.innerHTML = `<div class="hint" style="color: var(--bad)">Error: ${escapeHtml(e.message)}</div>`;
  }
}

$("#survey-search").oninput = (e) => {
  clearTimeout(surveySearchTimer);
  const v = e.target.value;
  surveySearchTimer = setTimeout(() => loadSurveys(v), 300);
};

async function selectSurvey(s, tile) {
  $$(".survey-tile").forEach(t => t.classList.remove("selected"));
  tile.classList.add("selected");
  state.surveyId = s.id;
  state.surveyName = s.name;
  state.eligible = null;
  state.questions = [];
  state.filter = { date_from: null, date_to: null, include_all: false };

  $("#step-filter").classList.remove("hidden");
  $("#step-questions").classList.add("hidden");
  $("#step-preview").classList.add("hidden");
  $("#step-generate").classList.add("hidden");
  $("#eligible-meta").textContent = "";
  $("#date-from").value = "";
  $("#date-to").value = "";
  $("#include-all").checked = false;

  track("survey_selected", { name: s.name });

  // Prefill the date pickers from the cohort's submit-date bounds.
  try {
    const b = await api(`/api/surveys/${s.id}/date-bounds`);
    if (b.min) {
      const minD = b.min.slice(0, 10), maxD = b.max ? b.max.slice(0, 10) : minD;
      $("#date-from").min = minD; $("#date-from").max = maxD;
      $("#date-to").min = minD; $("#date-to").max = maxD;
      $("#date-from").value = minD; $("#date-to").value = maxD;
      $("#eligible-meta").textContent = `submissions ${minD} → ${maxD}`;
    } else {
      $("#eligible-meta").textContent = "no submitted respondents";
    }
  } catch (e) { /* non-fatal */ }

  $("#step-filter").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------- Step 2: filter -------------------------------------------------

function readFilter() {
  const all = $("#include-all").checked;
  return {
    include_all: all,
    date_from: all ? null : ($("#date-from").value || null),
    date_to: all ? null : ($("#date-to").value || null),
  };
}

$("#include-all").onchange = () => {
  const dis = $("#include-all").checked;
  $("#date-from").disabled = dis;
  $("#date-to").disabled = dis;
};

$("#apply-filter-btn").onclick = async () => {
  state.filter = readFilter();
  const btn = $("#apply-filter-btn");
  btn.disabled = true;
  $("#eligible-meta").textContent = "counting…";
  try {
    const res = await api("/api/eligible-count", {
      method: "POST",
      body: JSON.stringify({ survey_id: state.surveyId, filter: state.filter, session_id: state.sessionId }),
    });
    state.eligible = res.eligible;
    state.cap = res.cap;
    $("#eligible-meta").textContent = `${res.eligible.toLocaleString()} eligible respondents (submitted, not excluded)`;
    if (res.eligible === 0) {
      toast("No eligible respondents for this filter");
      return;
    }
    $("#step-questions").classList.remove("hidden");
    if (state.questions.length === 0) addCustomQuestion();
    else renderQuestions();
    $("#generate-meta").textContent = `cohort ${res.eligible.toLocaleString()} · capped at ${res.cap}`;
    $("#step-questions").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    $("#eligible-meta").textContent = "";
    toast(`Filter failed: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
};

// ---------- Step 3: questions (generate + manual + select) -----------------

const QTYPES = [
  { value: "multipleChoice", label: "Single answer (pick one)" },
  { value: "checkBoxes", label: "Multiple answers (pick many)" },
  { value: "shortAnswer", label: "Open ended (free text)" },
  { value: "numericAnswer", label: "Numeric answer" },
];

// friendly display name for a question type (value stays the API type)
const TYPE_LABELS = {
  multipleChoice: "Single answer",
  checkBoxes: "Multiple answers",
  shortAnswer: "Open ended",
  numericAnswer: "Numeric",
};
const typeLabel = (t) => TYPE_LABELS[t] || t || "";

function addCustomQuestion() {
  state.questions.push({
    id: state.nextId++, text: "", type: "multipleChoice", options: ["", ""],
    rationale: "", source: "manual", include: true,
  });
  renderQuestions();
}

function removeQuestion(id) {
  state.questions = state.questions.filter(q => q.id !== id);
  renderQuestions();
}

function renderQuestions() {
  const root = $("#question-list");
  root.innerHTML = "";
  if (!state.questions.length) {
    root.innerHTML = `<div class="hint">No questions yet — generate with AI or add your own.</div>`;
    return;
  }
  for (const q of state.questions) {
    const row = document.createElement("div");
    row.className = "cq-row" + (q.include ? " on" : "");
    const needsOptions = q.type === "multipleChoice" || q.type === "checkBoxes";
    row.innerHTML = `
      <div class="cq-head">
        <label class="inc"><input type="checkbox" class="cq-inc" data-id="${q.id}" ${q.include ? "checked" : ""} /> include</label>
        <span class="src ${q.source}">${q.source === "ai" ? "✨ AI" : "✍ manual"}</span>
        <select class="cq-type" data-id="${q.id}">
          ${QTYPES.map(t => `<option value="${t.value}" ${t.value === q.type ? "selected" : ""}>${t.label}</option>`).join("")}
        </select>
        <button class="cq-remove ghost" data-id="${q.id}" title="Remove">×</button>
      </div>
      <textarea class="cq-text" data-id="${q.id}" placeholder="e.g. Would you buy an electric car this year?">${escapeHtml(q.text)}</textarea>
      <div class="cq-options-wrap ${needsOptions ? "" : "hidden"}">
        <label>Options (one per line):</label>
        <textarea class="cq-options" data-id="${q.id}" placeholder="Option 1&#10;Option 2">${escapeHtml((q.options || []).join("\n"))}</textarea>
      </div>
      ${q.rationale ? `<div class="cq-rationale">✨ ${escapeHtml(q.rationale)}</div>` : ""}
      ${q.source === "ai" ? (
        (q.grounding && q.grounding.length)
          ? `<div class="cq-grounding">🔗 grounded in: ${q.grounding.map(escapeHtml).join(", ")}</div>`
          : `<div class="cq-grounding weak">⚠ weakly grounded — answers may rely on assumptions; review before generating</div>`
      ) : ""}
    `;
    root.appendChild(row);
  }

  root.querySelectorAll(".cq-inc").forEach(el => {
    el.onchange = (e) => {
      const q = state.questions.find(x => x.id === parseInt(e.target.dataset.id));
      if (q) { q.include = e.target.checked; e.target.closest(".cq-row").classList.toggle("on", q.include); }
    };
  });
  root.querySelectorAll(".cq-type").forEach(el => {
    el.onchange = (e) => {
      const q = state.questions.find(x => x.id === parseInt(e.target.dataset.id));
      if (q) { q.type = e.target.value; renderQuestions(); }
    };
  });
  root.querySelectorAll(".cq-text").forEach(el => {
    el.oninput = (e) => {
      const q = state.questions.find(x => x.id === parseInt(e.target.dataset.id));
      if (q) q.text = e.target.value;
    };
  });
  root.querySelectorAll(".cq-options").forEach(el => {
    el.oninput = (e) => {
      const q = state.questions.find(x => x.id === parseInt(e.target.dataset.id));
      if (q) q.options = e.target.value.split("\n").map(s => s.trim()).filter(Boolean);
    };
  });
  root.querySelectorAll(".cq-remove").forEach(el => {
    el.onclick = (e) => removeQuestion(parseInt(e.currentTarget.dataset.id));
  });
}

// Shared centered loader: a gold ring + rotating status messages. Returns {node, stop}.
function createLoader(messages, subText) {
  const wrap = document.createElement("div");
  wrap.className = "loading";
  wrap.innerHTML =
    `<div class="loader-ring" aria-hidden="true"></div>
     <div class="loader-msg"></div>
     <div class="loader-sub"></div>`;
  const msgEl = wrap.querySelector(".loader-msg");
  msgEl.textContent = messages[0];
  wrap.querySelector(".loader-sub").textContent = subText || "";
  let i = 0;
  const timer = setInterval(() => {
    if (!msgEl.isConnected) { clearInterval(timer); return; }
    i = (i + 1) % messages.length;
    msgEl.textContent = messages[i];
    msgEl.style.animation = "none";
    void msgEl.offsetWidth;        // restart the fade-in
    msgEl.style.animation = "";
  }, 1800);
  return { node: wrap, stop: () => clearInterval(timer) };
}

const SUGGEST_MSGS = [
  "Reviewing the survey…",
  "Spotting gaps in the existing questions…",
  "Drafting fresh angles…",
  "Avoiding duplicates…",
  "Writing clear question text…",
];

async function suggestQuestions() {
  if (!state.surveyId) { toast("Pick a survey first"); return; }
  const btn = $("#suggest-btn");
  const orig = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Thinking…`;
  // centered loader between the question list and the action buttons
  const loader = createLoader(SUGGEST_MSGS, "Generating new questions with AI");
  $("#question-list").insertAdjacentElement("afterend", loader.node);
  try {
    const n = parseInt($("#suggest-count").value) || 5;
    // Send every question already in the builder (AI-generated + manual) so the
    // model proposes genuinely new ones instead of repeating earlier suggestions.
    const already = state.questions.map(q => (q.text || "").trim()).filter(Boolean);
    const res = await api("/api/suggest", {
      method: "POST",
      body: JSON.stringify({ survey_id: state.surveyId, n, already }),
    });
    if (!res.questions || !res.questions.length) { toast("No questions returned"); return; }
    // APPEND (generate more), don't replace
    for (const q of res.questions) {
      state.questions.push({
        id: state.nextId++, text: q.text, type: q.type,
        options: q.options && q.options.length ? q.options : ["", ""],
        rationale: q.rationale || "", grounding: q.grounding || [],
        source: "ai", include: true,
      });
    }
    renderQuestions();
    track("questions_generated", {
      count: res.questions.length,
      questions: res.questions.map(q => ({ text: q.text, type: q.type, rationale: q.rationale })),
    });
    toast(`${res.questions.length} questions added`);
  } catch (e) {
    toast(`Generate failed: ${e.message}`);
  } finally {
    loader.stop();
    loader.node.remove();
    btn.disabled = false;
    btn.textContent = orig;
  }
}

function selectedQuestions() {
  const out = [];
  for (const q of state.questions) {
    if (!q.include) continue;
    const text = (q.text || "").trim();
    if (!text) continue;
    const opts = (q.options || []).map(o => o.trim()).filter(Boolean);
    if ((q.type === "multipleChoice" || q.type === "checkBoxes") && opts.length < 2) {
      throw new Error(`"${text.slice(0, 30)}…" needs at least 2 options`);
    }
    out.push({ id: q.id, text, type: q.type, options: opts });
  }
  return out;
}

// ---------- Step 4: preview on 10 -----------------------------------------

const PREVIEW_MSGS = [
  "Sampling respondents…",
  "Reading their real survey answers…",
  "Reasoning as each respondent…",
  "Writing natural Egyptian-Arabic replies…",
  "Grounding answers in the survey…",
  "Polishing the results…",
];
let previewLoader = null;

function startPreviewLoading() {
  previewLoader = createLoader(PREVIEW_MSGS, "AI is answering for 10 sampled respondents");
  const root = $("#preview-area");
  root.innerHTML = "";
  root.appendChild(previewLoader.node);
}

function stopPreviewLoading() {
  if (previewLoader) { previewLoader.stop(); previewLoader = null; }
}

$("#preview-btn").onclick = async () => {
  let selected;
  try { selected = selectedQuestions(); } catch (e) { toast(e.message); return; }
  if (!selected.length) { toast("Tick at least one question with text"); return; }

  const btn = $("#preview-btn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Previewing…`;
  $("#step-preview").classList.remove("hidden");
  $("#preview-meta").textContent = "";
  startPreviewLoading();
  try {
    const res = await api("/api/preview", {
      method: "POST",
      body: JSON.stringify({
        survey_id: state.surveyId, filter: state.filter,
        questions: selected, session_id: state.sessionId,
      }),
    });
    stopPreviewLoading();
    renderPreview(res);
    $("#step-generate").classList.remove("hidden");
    $("#step-preview").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    stopPreviewLoading();
    $("#preview-area").innerHTML = `<div class="hint" style="color: var(--bad)">Error: ${escapeHtml(e.message)}</div>`;
  } finally {
    stopPreviewLoading();
    btn.disabled = false;
    btn.textContent = "Preview on 10 →";
  }
};

// ----- swipeable respondent carousel (one card at a time) -----
let pvSlides = [];
let pvIndex = 0;

function buildRespondentCard(r, idx) {
  const card = document.createElement("div");
  card.className = "preview-resp";

  const realCount = (r.real_answers || []).length;
  // Group real answers by their survey section (preserving first-seen order) and show
  // the label as a collapsible term that reveals the full question text.
  const realGroups = [];
  const secPos = {};
  for (const a of (r.real_answers || [])) {
    const sec = (a.section || "").trim() || "Other";
    if (!(sec in secPos)) { secPos[sec] = realGroups.length; realGroups.push({ section: sec, rows: [] }); }
    realGroups[secPos[sec]].rows.push(a);
  }
  const showSecHeads = realGroups.length > 1 || (realGroups[0] && realGroups[0].section !== "Other");
  const realLines = realGroups.map(grp => {
    const head = showSecHeads ? `<div class="pv-sec">${escapeHtml(grp.section)}</div>` : "";
    const rows = grp.rows.map(a => {
      const label = a.label || "";
      const qtext = a.text || "";
      const term = (label && qtext && label !== qtext)
        ? `<dt><details class="pv-q"><summary dir="auto">${escapeHtml(label)}</summary><div class="pv-qfull" dir="auto">${escapeHtml(qtext)}</div></details></dt>`
        : `<dt dir="auto">${escapeHtml(label || qtext)}</dt>`;
      return `${term}<dd dir="auto">${escapeHtml(a.answer)}</dd>`;
    }).join("");
    return head + rows;
  }).join("");

  const genHtml = (r.generated || []).map(g => {
    const refs = (g.references || []).map(rf =>
      `<span class="chip-ref"><b>${escapeHtml(rf.label)}</b> → <bdi>${escapeHtml(rf.answer)}</bdi></span>`).join("");
    return `<div class="pv-gen-item">
      <div class="pv-gq">${escapeHtml(g.text)} <span class="qtype">(${escapeHtml(typeLabel(g.type))})</span></div>
      <div>${(g.answer || []).map(v => `<span class="pill ai" dir="auto">${escapeHtml(v)}</span>`).join("") || "—"}</div>
      ${g.reason ? `<div class="reason">"${escapeHtml(g.reason)}"</div>` : ""}
      ${refs ? `<div class="refrow"><span class="grounded">grounded in</span>${refs}</div>` : ""}
    </div>`;
  }).join("") || (r.error ? `<div class="hint" style="color: var(--bad); margin:0">${escapeHtml(r.error)}</div>` : "");

  const dateChip = r.submitDate ? `<span class="pv-date">${escapeHtml(r.submitDate.slice(0, 10))}</span>` : "";
  card.innerHTML = `
    <div class="pv-head">
      <span class="pv-idx">${idx + 1}</span>
      <span class="pv-title">Respondent ${idx + 1}</span>
      <span class="pv-id">${escapeHtml(String(r.id).slice(-8))}</span>
      ${dateChip}
    </div>
    <div class="pv-cols">
      <div class="pv-col">
        <details class="pv-real" open>
          <summary>Real answers (${realCount})</summary>
          ${realLines ? `<dl class="pv-grid">${realLines}</dl>` : `<div class="hint" style="margin:0">no recorded answers</div>`}
        </details>
      </div>
      <div class="pv-col ai">
        <label>AI-generated answers</label>
        ${genHtml}
      </div>
    </div>`;
  return card;
}

function pvShow(idx, dir) {
  if (!pvSlides.length) return;
  pvIndex = (idx + pvSlides.length) % pvSlides.length;   // wrap around
  pvSlides.forEach((s, k) => s.classList.toggle("active", k === pvIndex));
  const active = pvSlides[pvIndex];
  if (active && dir) {
    active.classList.remove("slide-l", "slide-r");
    void active.offsetWidth;                              // restart the slide animation
    active.classList.add(dir > 0 ? "slide-r" : "slide-l");
  }
  const counter = $("#pv-counter");
  if (counter) counter.textContent = `Respondent ${pvIndex + 1} of ${pvSlides.length}`;
  const jump = $("#pv-jump");
  if (jump && jump.value !== String(pvIndex)) jump.value = String(pvIndex);
}

function attachSwipe(el) {
  let x0 = null, y0 = null;
  el.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
  el.addEventListener("touchend", (e) => {
    if (x0 == null) return;
    const dx = e.changedTouches[0].clientX - x0;
    const dy = e.changedTouches[0].clientY - y0;
    x0 = null;
    if (Math.abs(dx) < 45 || Math.abs(dx) < Math.abs(dy)) return;  // ignore taps / vertical scrolls
    pvShow(pvIndex + (dx < 0 ? 1 : -1), dx < 0 ? +1 : -1);
  }, { passive: true });
}

function renderPreview(res) {
  let meta = `model: ${res.model} · showing ${res.sample} of ${(res.eligible || 0).toLocaleString()} eligible`;
  const c = res.cost;
  if (c && c.scored) {
    meta += ` · cost: ${fmtUSD(c.total_usd)} for ${c.scored} (≈ ${fmtUSD(c.per_respondent_usd)}/respondent)`;
    state.preview = {
      perRespondentUsd: c.per_respondent_usd,
      eligible: res.eligible || 0,
      cap: res.cap || 0,
    };
    renderGenerateEstimate();
  }
  $("#preview-meta").textContent = meta;

  const root = $("#preview-area");
  root.innerHTML = "";
  pvSlides = [];
  pvIndex = 0;
  const results = res.results || [];
  if (!results.length) {
    root.innerHTML = `<div class="hint">No eligible respondents for this filter.</div>`;
    return;
  }

  const carousel = document.createElement("div");
  carousel.className = "pv-carousel";
  carousel.innerHTML = `
    <div class="pv-nav">
      <button class="pv-arrow pv-go" data-dir="-1" aria-label="Previous respondent">‹</button>
      <select class="pv-jump" id="pv-jump" aria-label="Jump to respondent">
        ${results.map((r, k) => `<option value="${k}">Respondent ${k + 1}</option>`).join("")}
      </select>
      <button class="pv-arrow pv-go" data-dir="1" aria-label="Next respondent">›</button>
    </div>
    <div class="pv-viewport"><div class="pv-track" id="pv-track"></div></div>
    <div class="pv-foot">
      <button class="pv-step pv-go" data-dir="-1" aria-label="Previous respondent">‹ Previous</button>
      <span class="pv-counter" id="pv-counter">Respondent 1 of ${results.length}</span>
      <button class="pv-step pv-go" data-dir="1" aria-label="Next respondent">Next ›</button>
    </div>`;
  root.appendChild(carousel);

  const track = carousel.querySelector("#pv-track");
  pvSlides = results.map((r, idx) => {
    const slide = document.createElement("div");
    slide.className = "pv-slide";
    slide.appendChild(buildRespondentCard(r, idx));
    track.appendChild(slide);
    return slide;
  });

  carousel.querySelectorAll(".pv-go").forEach(b => {
    b.onclick = () => { const d = parseInt(b.dataset.dir, 10); pvShow(pvIndex + d, d); };
  });
  $("#pv-jump").onchange = (e) => {
    const t = parseInt(e.target.value);
    pvShow(t, t >= pvIndex ? +1 : -1);
  };
  attachSwipe(carousel.querySelector(".pv-viewport"));

  pvShow(0);
}

function fmtUSD(x) {
  if (x == null || isNaN(x)) return "—";
  return "$" + (x >= 1 ? x.toFixed(2) : x.toFixed(4));
}

// Estimate the full generate-all cost from the preview's per-respondent cost,
// scaled by the cohort size that will actually run (eligible, capped).
function renderGenerateEstimate() {
  const el = $("#generate-meta");
  if (!el) return;
  const p = state.preview;
  if (!p || !p.perRespondentUsd) { el.textContent = ""; return; }
  const n = p.cap ? Math.min(p.eligible, p.cap) : p.eligible;
  const capNote = (p.cap && p.eligible > p.cap)
    ? ` (capped at ${p.cap.toLocaleString()} of ${p.eligible.toLocaleString()} eligible)`
    : "";
  el.textContent = `Est. cost: ~${fmtUSD(p.perRespondentUsd * n)} for ${n.toLocaleString()} respondent${n === 1 ? "" : "s"}${capNote}`;
}

// ---------- Step 5: generate for all --------------------------------------

let pollTimer = null;

const GENERATE_MSGS = [
  "Spinning up the run…",
  "Sampling the cohort…",
  "Reasoning as each respondent…",
  "Writing natural Egyptian-Arabic replies…",
  "Grounding answers in the survey…",
  "Building your Excel export…",
];
let generateLoader = null;

function startGenerateLoading() {
  generateLoader = createLoader(GENERATE_MSGS, "Starting…");
  // a real progress bar lives inside the centered loader, under the rotating message
  const bar = document.createElement("div");
  bar.className = "prog-bar";
  bar.style.width = "100%";
  bar.style.maxWidth = "360px";
  bar.innerHTML = `<div class="prog-fill" style="width:0%"></div>`;
  generateLoader.node.appendChild(bar);
  const root = $("#progress-area");
  root.innerHTML = "";
  root.appendChild(generateLoader.node);
}

function stopGenerateLoading() {
  if (generateLoader) { generateLoader.stop(); generateLoader = null; }
}

$("#generate-btn").onclick = async () => {
  let selected;
  try { selected = selectedQuestions(); } catch (e) { toast(e.message); return; }
  if (!selected.length) { toast("Tick at least one question with text"); return; }

  const btn = $("#generate-btn");
  btn.disabled = true;
  startGenerateLoading();
  try {
    const job = await api("/api/generate-all", {
      method: "POST",
      body: JSON.stringify({
        survey_id: state.surveyId, filter: state.filter,
        questions: selected, session_id: state.sessionId,
      }),
    });
    pollJob(job.id);
  } catch (e) {
    stopGenerateLoading();
    $("#progress-area").innerHTML = `<div class="hint" style="color: var(--bad)">Error: ${escapeHtml(e.message)}</div>`;
    btn.disabled = false;
  }
};

function renderProgress(j) {
  const root = $("#progress-area");
  const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
  const cappedNote = j.capped ? ` (capped from ${(j.eligible || 0).toLocaleString()} eligible)` : "";

  if (j.state === "done") {
    stopGenerateLoading();
    root.innerHTML = `
      <div class="loading">
        <div class="loader-msg" style="font-size:18px">✓ Done — ${j.ok} of ${j.total} respondents</div>
        <div class="loader-sub">${j.failed ? `${j.failed} failed · ` : ""}your Excel is ready</div>
        <a class="dl-btn" href="/api/jobs/${j.id}/download">⬇ Download Excel</a>
      </div>`;
    return;
  }
  if (j.state === "error") {
    stopGenerateLoading();
    root.innerHTML = `<div class="hint" style="color: var(--bad)">Job failed: ${escapeHtml(j.error || "unknown")}</div>`;
    return;
  }

  // running / pending: keep the centered loader, update the live count + progress bar in place
  if (!generateLoader) startGenerateLoading();
  const sub = root.querySelector(".loader-sub");
  const fill = root.querySelector(".prog-fill");
  if (sub) sub.textContent = `${j.done}/${j.total}${cappedNote}${j.failed ? ` · ${j.failed} failed` : ""}`;
  if (fill) fill.style.width = pct + "%";
}

async function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  const tick = async () => {
    try {
      const j = await api(`/api/jobs/${jobId}`);
      renderProgress(j);
      if (j.state === "done" || j.state === "error") {
        clearInterval(pollTimer); pollTimer = null;
        $("#generate-btn").disabled = false;
      }
    } catch (e) {
      clearInterval(pollTimer); pollTimer = null;
      stopGenerateLoading();
      $("#progress-area").innerHTML = `<div class="hint" style="color: var(--bad)">Lost job: ${escapeHtml(e.message)}</div>`;
      $("#generate-btn").disabled = false;
    }
  };
  await tick();
  pollTimer = setInterval(tick, 1800);
}

// ---------- wiring ---------------------------------------------------------

$("#add-q-btn").onclick = addCustomQuestion;
$("#suggest-btn").onclick = suggestQuestions;

loadSurveys();
