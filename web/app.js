const $ = (s) => document.querySelector(s);
const state = {
  config: null, job: null, selected: 0, tab: "marked", editing: false,
  drawn: new Set(), audio: null, audioBtn: null, cache: new Map(), sheetSig: "",
};

const STAGES = { queued: "Waiting", reading: "Reading handwriting", marking: "Marking", done: "Marked", error: "Failed" };
const PLAY = '<svg viewBox="0 0 12 12"><path d="M2 1l9 5-9 5z"/></svg>';
const STOP = '<svg viewBox="0 0 12 12"><rect x="2" y="2" width="8" height="8"/></svg>';
// Hand-drawn circle: slightly open loop, like a teacher's pen.
const CIRCLE = '<svg viewBox="0 0 132 112" aria-hidden="true"><path d="M78 10 C 40 4, 8 24, 10 58 C 12 92, 52 106, 86 100 C 118 94, 128 64, 120 40 C 112 18, 86 6, 58 12"/></svg>';

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (n) => (Number.isInteger(+n) ? String(+n) : (+n).toFixed(1));
// OCR emits LaTeX for maths; render the common bits as plain text.
const tidy = (s) => String(s ?? "").replace(/\\n/g, "\n").replace(/\\times/g, "×").replace(/\\div/g, "÷")
  .replace(/\\cdot/g, "·").replace(/\\(?:text|mathrm)\{([^}]*)\}/g, "$1").replace(/\$/g, "").replace(/\^\{?2\}?/g, "²");
// Full-page OCR is markdown; keep the words, drop the markup.
const plain = (s) => tidy(s).replace(/!\[[^\]]*\]\([^)]*\)/g, "").replace(/^#{1,6}\s*/gm, "")
  .replace(/\*\*|__/g, "").replace(/^\s*\|?[-:| ]+\|?\s*$/gm, "").replace(/<[^>]+>/g, "").replace(/\n{3,}/g, "\n\n").trim();
const kb = (b) => (b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.ceil(b / 1e3) + " KB");
const letter = (p) => (p >= 90 ? "A+" : p >= 80 ? "A" : p >= 70 ? "B" : p >= 60 ? "C" : p >= 50 ? "D" : "F");

/** Marks as they stand after teacher review (falls back to the AI's). */
function effective(s) {
  const r = s.result, rv = s.review || {};
  const questions = r.questions.map((q, i) => {
    const t = rv.questions?.[i] || {};
    return { ...q, ai: q.awarded, aiFeedback: q.feedback,
             awarded: t.awarded ?? q.awarded, feedback: t.feedback ?? q.feedback };
  });
  const total = questions.reduce((a, q) => a + q.awarded, 0);
  const pct = r.total_max ? Math.round((1000 * total) / r.total_max) / 10 : 0;
  return { ...r, questions, total, pct, grade: letter(pct), approved: !!rv.approved,
           overall: rv.overall_feedback ?? r.overall_feedback, overallEdited: rv.overall_feedback != null,
           edited: questions.some((q) => q.awarded !== q.ai || q.feedback !== q.aiFeedback) || rv.overall_feedback != null };
}

async function init() {
  state.config = await (await fetch("/api/config")).json();
  $("#demo-badge").hidden = !state.config.demo;
  $("#signout").hidden = !state.config.auth;
  for (const sel of ["#language", "#feedback-language"]) {
    $(sel).innerHTML = Object.entries(state.config.languages).map(([c, n]) => `<option value="${c}">${n}</option>`).join("");
  }
  setupModels();
  $("#feedback-language").addEventListener("change", fillVoices);
  $("#narrate").addEventListener("change", fillVoices);
  fillVoices();
  setupDrop("qp", false);
  setupDrop("scripts", true);
  setupDrop("key", false, "Attach answer key file or <u>browse</u>");
  $("#setup").addEventListener("submit", submit);
  $("#new-batch").addEventListener("click", () => { location.hash = ""; showEmpty(); });
  window.addEventListener("hashchange", route);
  route();
}

async function route() {
  const id = new URLSearchParams(location.hash.slice(1)).get("batch");
  if (!id) return showEmpty();
  if (state.job?.id === id) return;
  const r = await fetch(`/api/jobs/${id}`);
  if (!r.ok) { location.hash = ""; return; }
  openJob(await r.json());
}

async function showEmpty() {
  stopAudio();
  state.job = null;
  $("#batch").hidden = true;
  $("#empty").hidden = false;
  const list = await (await fetch("/api/jobs")).json();
  $("#recent").hidden = !list.length;
  $("#recent-list").innerHTML = list.map((j) => `<li><a href="#batch=${j.id}">
    <b>${esc(j.name || "Untitled batch")}</b>
    <span>${j.scripts} script${j.scripts === 1 ? "" : "s"} · ${j.approved} checked · ${new Date(j.created * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</span></a></li>`).join("");
}

function openJob(job) {
  state.job = job;
  state.selected = 0;
  state.editing = false;
  state.drawn.clear();
  state.sheetSig = "";
  $("#empty").hidden = true;
  $("#batch").hidden = false;
  $("#csv").href = `/api/jobs/${job.id}/csv`;
  render();
  if (!["done", "error"].includes(job.status)) poll();
}

function setupModels() {
  const input = $("#model");
  let saved = null;
  try { saved = localStorage.getItem("redpen.model"); } catch {}
  input.value = saved || state.config.default_model;
  input.addEventListener("change", () => { try { localStorage.setItem("redpen.model", input.value.trim()); } catch {} });
  const fill = (ids) => { $("#model-list").innerHTML = ids.map((m) => `<option value="${esc(m)}"></option>`).join(""); };
  fill(state.config.suggested_models);
  // Full catalogue loads in the background; suggestions first so the common picks lead.
  fetch("/api/models").then((r) => r.json()).then((all) => {
    const top = state.config.suggested_models;
    fill([...top, ...all.filter((m) => !top.includes(m))]);
  }).catch(() => {});
}

function fillVoices() {
  const on = $("#narrate").checked;
  const lang = $("#feedback-language").value;
  const voices = state.config.voices[lang];
  $("#voice-field").hidden = !on || !voices;
  $("#voice-note").hidden = !on || !!voices;
  $("#voice-note").textContent = `Bulbul v4 Flash has no ${state.config.languages[lang]} voices. Pick another feedback language to hear it.`;
  if (voices) {
    $("#voice").innerHTML = voices.map((v) => {
      const [name, , ...style] = v.split("_");
      return `<option value="${v}">${name[0].toUpperCase() + name.slice(1)} · ${style.join(" ")}</option>`;
    }).join("");
  }
  if (state.job) { state.sheetSig = ""; render(); }
}

const narrationLang = () => state.job?.feedback_language || $("#feedback-language").value;
const narrationOn = () => $("#narrate").checked && !!state.config.voices[narrationLang()];

function setupDrop(id, multi, emptyText) {
  const input = $("#" + id), zone = input.closest(".drop"), list = $(`#${id}-list`);
  const show = () => {
    list.innerHTML = [...input.files].map((f) => `<li><span>${esc(f.name)}</span><span>${kb(f.size)}</span></li>`).join("");
    zone.querySelector(".drop-text").innerHTML = input.files.length
      ? (multi ? `${input.files.length} file${input.files.length > 1 ? "s" : ""} · <u>change</u>` : "<u>Replace file</u>")
      : emptyText || (multi ? "Drop files or <u>browse</u>" : "Drop file or <u>browse</u>");
  };
  input.addEventListener("change", show);
  ["dragenter", "dragover"].forEach((e) => zone.addEventListener(e, () => zone.classList.add("over")));
  ["dragleave", "drop"].forEach((e) => zone.addEventListener(e, () => zone.classList.remove("over")));
}

async function submit(ev) {
  ev.preventDefault();
  const err = $("#form-error");
  err.textContent = "";
  const fd = new FormData();
  fd.append("question_paper", $("#qp").files[0]);
  for (const f of $("#scripts").files) fd.append("scripts", f);
  fd.append("answer_key", $("#answer-key").value);
  if ($("#key").files[0]) fd.append("answer_key_file", $("#key").files[0]);
  fd.append("language", $("#language").value);
  fd.append("feedback_language", $("#feedback-language").value);
  fd.append("reasoning_effort", document.querySelector("input[name=effort]:checked").value);
  fd.append("model", $("#model").value.trim());

  $("#go").disabled = true;
  $("#go").textContent = "Marking…";
  try {
    const r = await fetch("/api/jobs", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const job = await r.json();
    history.replaceState(null, "", `#batch=${job.id}`);
    openJob(job);
    if (matchMedia("(max-width: 960px)").matches) $("#batch").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    err.textContent = `Couldn't start marking: ${e.message}`;
    resetButton();
  }
}

function resetButton() {
  $("#go").disabled = false;
  $("#go").textContent = "Mark scripts";
}

async function poll() {
  const id = state.job?.id;
  if (!id) return;
  const r = await fetch(`/api/jobs/${id}`);
  if (!r.ok || state.job?.id !== id) return;
  state.job = await r.json();
  render();
  if (["done", "error"].includes(state.job.status)) resetButton();
  else setTimeout(poll, 1500);
}

function render() {
  renderStats();
  renderRoster();
  const s = state.job.scripts[state.selected];
  const sig = [state.job.id, state.selected, s?.stage, state.job.status, s?.review?.updated, state.tab, state.editing].join("|");
  if (sig !== state.sheetSig) { state.sheetSig = sig; renderSheet(); }
}

function renderStats() {
  const done = state.job.scripts.filter((s) => s.result).map(effective);
  const pcts = done.map((e) => e.pct);
  const avg = pcts.length ? pcts.reduce((a, b) => a + b, 0) / pcts.length : null;
  const stats = [
    [`${done.length}/${state.job.scripts.length}`, "scripts marked"],
    [`${done.filter((e) => e.approved).length}/${done.length}`, "checked by you"],
    [avg === null ? "—" : `${num(avg)}%`, "class average"],
    [pcts.length ? `${num(Math.min(...pcts))}–${num(Math.max(...pcts))}%` : "—", "lowest – highest"],
  ];
  if (state.job.status === "preparing") stats.unshift(["…", "reading question paper & key"]);
  $("#stats").innerHTML = stats.map(([b, s]) => `<div class="stat"><b>${b}</b><span>${s}</span></div>`).join("");
}

function renderRoster() {
  $("#roster").innerHTML = state.job.scripts.map((s, i) => {
    const e = s.result && effective(s);
    const busy = s.stage === "reading" || s.stage === "marking";
    const right = e
      ? `<span class="mini">${num(e.total)}/${num(e.total_max)}</span>`
      : `<span class="stage ${s.stage === "error" ? "error" : ""}">${busy ? '<i class="dot"></i>' : ""}${STAGES[s.stage]}</span>`;
    const sub = e ? `<small>${e.approved ? '<i class="checked">Checked</i>' : "AI marked"} · Grade ${esc(e.grade)}</small>` : "";
    return `<li><button type="button" data-i="${i}" aria-current="${i === state.selected}">
      <span class="who"><b>${esc(e?.student_name || s.file)}</b>${sub}</span>${right}</button></li>`;
  }).join("");
  $("#roster").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    if (state.editing && !confirm("Discard your unsaved changes to this script?")) return;
    state.selected = +b.dataset.i;
    state.editing = false;
    stopAudio();
    render();
  }));
}

function toolbar(s) {
  const tabs = [["marked", "Marked sheet"], ["writing", "Student's writing"], ["scan", "Original scan"]];
  const e = s.result && effective(s);
  const status = !e ? "" : e.approved ? '<span class="status ok">Checked by you</span>'
    : e.edited ? '<span class="status">Draft edits</span>' : '<span class="status">AI marked · not checked</span>';
  const action = e && !state.editing && state.tab === "marked"
    ? `<button type="button" class="secondary" id="review">${e.approved ? "Edit marks" : "Review marks"}</button>` : "";
  return `<div class="toolbar">
    <div class="tabs" role="tablist">${tabs.map(([k, l]) =>
      `<button type="button" role="tab" data-tab="${k}" aria-selected="${state.tab === k}" ${state.editing && k !== "marked" ? "disabled" : ""}>${l}</button>`).join("")}</div>
    <div class="toolbar-right">${status}${action}</div></div>`;
}

function renderSheet() {
  const s = state.job.scripts[state.selected];
  const sheet = $("#sheet");
  if (!s) return;
  let body;
  if (state.tab === "scan") body = scanView(s);
  else if (s.stage === "error") {
    body = `<div class="paper"><h2>${esc(s.file)}</h2><p>This script couldn't be marked. Check the file is a clear scan under 10 pages per PDF, then mark it again.</p><p class="err">${esc(s.error)}</p></div>`;
  } else if (!s.result) {
    const msg = state.job.status === "error" ? `Marking stopped: ${esc(state.job.error)}`
      : s.stage === "marking" ? "Marking answers…" : s.stage === "reading" ? "Reading handwriting…"
      : ["preparing", "queued"].includes(state.job.status) ? "Reading the question paper…" : "Waiting its turn…";
    body = `<div class="paper"><div class="waiting">${msg}</div></div>`;
  } else body = state.tab === "writing" ? writingView(s) : markedView(s);

  sheet.innerHTML = toolbar(s) + body;
  sheet.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { state.tab = b.dataset.tab; stopAudio(); render(); }));
  $("#review")?.addEventListener("click", () => { state.editing = true; render(); sheet.querySelector(".mark-input")?.focus(); });
  sheet.querySelectorAll(".listen").forEach((b) => b.addEventListener("click", () => toggleNarration(b, effective(s))));
  if (state.editing) wireEditor(s);
}

function scanView(s) {
  const url = `/api/jobs/${state.job.id}/scripts/${state.selected}/file`;
  const ext = s.file.split(".").pop().toLowerCase();
  const inner = ext === "pdf" ? `<iframe src="${url}" title="Original scan of ${esc(s.file)}"></iframe>`
    : ext === "zip" ? `<p class="scan-note">This script was uploaded as a ZIP of pages. <a href="${url}" download="${esc(s.file)}">Download it</a> to view.</p>`
    : `<img src="${url}" alt="Original scan of ${esc(s.file)}">`;
  return `<div class="scan">${inner}</div>`;
}

function writingView(s) {
  const r = s.result;
  return `<div class="paper">
    <div class="sheet-head"><div><h2>${esc(r.student_name || "Unnamed student")}</h2><small>As read by Sarvam Vision · ${esc(s.file)}</small></div></div>
    <div class="manuscript">${esc(plain(r.ocr_text))}</div></div>`;
}

function markedView(s) {
  const e = effective(s);
  const ed = state.editing;
  const listen = (key, label) => narrationOn() && !ed
    ? `<button type="button" class="listen" data-key="${key}" data-state="idle">${PLAY}<span>${label}</span></button>` : "";
  const firstDraw = !state.drawn.has(state.selected);
  state.drawn.add(state.selected);

  const qs = e.questions.map((q, i) => {
    const full = q.awarded >= q.max_marks && q.max_marks > 0;
    const ans = tidy(q.student_answer).trim();
    const showMax = !/marks?\b/i.test(q.question || "");
    const changed = q.awarded !== q.ai;
    const mark = ed
      ? `<input class="mark-input" type="number" inputmode="decimal" min="0" max="${q.max_marks}" step="0.5" value="${q.awarded}" data-i="${i}" aria-label="Marks for question ${esc(q.number || i + 1)}, out of ${num(q.max_marks)}"><span class="of">/${num(q.max_marks)}</span>
         <span class="ai-was" data-ai="${i}" ${changed ? "" : "hidden"}>AI gave ${num(q.ai)}</span>`
      : `${changed ? `<s class="was">${num(q.ai)}</s>` : ""}${num(q.awarded)}/${num(q.max_marks)}<span class="tick" aria-hidden="true">${full ? "✓" : q.awarded === 0 ? "✗" : "½✓"}</span>`;
    const remark = ed
      ? `<textarea class="remark-input" data-i="${i}" rows="2" aria-label="Remark for question ${esc(q.number || i + 1)}">${esc(q.feedback)}</textarea>`
      : `<p>${esc(q.feedback)}${q.feedback !== q.aiFeedback ? ' <i class="edited">edited</i>' : ""}</p>${listen(`q${i}`, "Listen")}`;
    return `<section class="q">
      <div class="q-mark ${ed ? "editing" : ""}" ${ed ? "" : `aria-label="${num(q.awarded)} of ${num(q.max_marks)} marks"`}>${mark}</div>
      <p class="q-title"><b>Q${esc(q.number || i + 1)}</b>${esc(tidy(q.question))}${showMax ? `<span class="max">[${num(q.max_marks)} marks]</span>` : ""}</p>
      <p class="answer ${ans ? "" : "empty-ans"}">${ans ? esc(ans) : "Not attempted"}</p>
      <div class="remark">${remark}</div>
    </section>`;
  }).join("");

  const overall = ed
    ? `<textarea class="remark-input overall-input" rows="3" aria-label="Teacher's remark">${esc(e.overall)}</textarea>`
    : `<p>${esc(e.overall)}${e.overallEdited ? ' <i class="edited">edited</i>' : ""}</p>${listen("summary", "Listen to summary")}`;

  const bar = ed ? `<div class="review-bar" role="region" aria-label="Review">
      <span class="live-total">Total <b id="live-total">${num(e.total)}</b>/${num(e.total_max)} · <span id="live-grade">${e.grade}</span></span>
      <button type="button" class="ghost" id="reset-ai">Reset to AI marks</button>
      <button type="button" class="ghost" id="cancel">Cancel</button>
      <button type="button" class="secondary" id="save-draft">Save draft</button>
      <button type="button" class="primary" id="approve">Approve marks</button>
      <p class="form-error" id="review-error" role="alert"></p>
    </div>` : "";

  return `<div class="paper ${ed ? "is-editing" : ""}">
    <div class="sheet-head">
      <div><h2>${esc(e.student_name || "Unnamed student")}</h2><small>${esc(s.file)} · marked by ${esc(e.model || state.job.model)}</small></div>
      <div class="total ${firstDraw ? "draw" : ""}" aria-label="Total ${num(e.total)} of ${num(e.total_max)}, grade ${esc(e.grade)}">
        ${CIRCLE}<div class="num"><span><span id="circle-total">${num(e.total)}</span>/${num(e.total_max)}</span><small id="circle-grade">${esc(e.grade)} · ${e.pct}%</small></div>
        ${e.approved && !ed ? '<span class="stamp">Checked</span>' : ""}
      </div>
    </div>
    ${qs}
    <div class="overall"><h3>Teacher's remark</h3>${overall}</div>
    ${bar}
  </div>`;
}

function wireEditor(s) {
  const sheet = $("#sheet");
  const e = effective(s);
  const inputs = [...sheet.querySelectorAll(".mark-input")];
  const update = () => {
    const total = inputs.reduce((a, inp) => a + (parseFloat(inp.value) || 0), 0);
    const pct = e.total_max ? (100 * total) / e.total_max : 0;
    $("#live-total").textContent = num(total);
    $("#live-grade").textContent = letter(pct);
    $("#circle-total").textContent = num(total);
    $("#circle-grade").textContent = `${letter(pct)} · ${Math.round(pct * 10) / 10}%`;
    inputs.forEach((inp) => {
      const i = +inp.dataset.i, v = parseFloat(inp.value);
      sheet.querySelector(`[data-ai="${i}"]`).hidden = v === e.questions[i].ai;
      inp.setAttribute("aria-invalid", String(!(v >= 0 && v <= e.questions[i].max_marks)));
    });
  };
  inputs.forEach((inp) => inp.addEventListener("input", update));
  $("#reset-ai").addEventListener("click", () => {
    inputs.forEach((inp) => { inp.value = e.questions[+inp.dataset.i].ai; });
    sheet.querySelectorAll(".remark-input[data-i]").forEach((t) => { t.value = e.questions[+t.dataset.i].aiFeedback; });
    sheet.querySelector(".overall-input").value = s.result.overall_feedback;
    update();
  });
  $("#cancel").addEventListener("click", () => { state.editing = false; render(); });
  $("#save-draft").addEventListener("click", () => saveReview(s, false));
  $("#approve").addEventListener("click", () => saveReview(s, true));
}

async function saveReview(s, approved) {
  const sheet = $("#sheet"), err = $("#review-error");
  const r = s.result;
  const questions = [...sheet.querySelectorAll(".mark-input")].map((inp) => {
    const i = +inp.dataset.i, v = parseFloat(inp.value);
    const fb = sheet.querySelector(`.remark-input[data-i="${i}"]`).value.trim();
    return { awarded: v === r.questions[i].awarded ? null : v, feedback: fb === r.questions[i].feedback ? null : fb };
  });
  const bad = questions.findIndex((q, i) => {
    const v = q.awarded ?? r.questions[i].awarded;
    return !(v >= 0 && v <= r.questions[i].max_marks);
  });
  if (bad >= 0) {
    err.textContent = `Q${r.questions[bad].number || bad + 1} needs marks between 0 and ${num(r.questions[bad].max_marks)}.`;
    sheet.querySelector(`.mark-input[data-i="${bad}"]`).focus();
    return;
  }
  const overall = sheet.querySelector(".overall-input").value.trim();
  const body = { questions, overall_feedback: overall === r.overall_feedback ? null : overall, approved };
  const res = await fetch(`/api/jobs/${state.job.id}/scripts/${state.selected}/review`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) { err.textContent = `Couldn't save: ${(await res.json()).detail}`; return; }
  state.job.scripts[state.selected] = await res.json();
  state.editing = false;
  render();
  if (approved) {
    // Move on to the next script still waiting for a check.
    const next = state.job.scripts.findIndex((x, i) => i > state.selected && x.result && !x.review?.approved);
    if (next >= 0) setTimeout(() => { state.selected = next; render(); }, 700);
  }
}

function narrationText(e, key) {
  if (key === "summary") return e.overallEdited ? e.overall : (e.narration || e.overall);
  return e.questions[+key.slice(1)].feedback;
}

function stopAudio() {
  if (state.audio) { state.audio.cancelled = true; state.audio.el?.pause(); }
  if (state.audioBtn) setBtn(state.audioBtn, "idle");
  state.audio = state.audioBtn = null;
}

function setBtn(b, st) {
  b.dataset.state = st;
  b.firstElementChild.outerHTML = st === "playing" ? STOP : PLAY;
  b.lastElementChild.textContent = st === "loading" ? "Preparing audio…" : st === "playing" ? "Stop" : (b.dataset.key === "summary" ? "Listen to summary" : "Listen");
}

async function toggleNarration(btn, e) {
  if (btn.dataset.state !== "idle") { stopAudio(); return; }
  stopAudio();
  const text = narrationText(e, btn.dataset.key);
  const lang = narrationLang(), voice = $("#voice").value;
  const cacheKey = `${voice}|${text}`;
  const session = { cancelled: false, el: null };
  state.audio = session;
  state.audioBtn = btn;
  setBtn(btn, "loading");
  try {
    let clips = state.cache.get(cacheKey);
    if (!clips) {
      const res = await fetch("/api/narrate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, language: lang, speaker: voice }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      clips = (await res.json()).audios;
      state.cache.set(cacheKey, clips);
    }
    if (session.cancelled) return;
    setBtn(btn, "playing");
    for (const c of clips) {
      if (session.cancelled) return;
      session.el = new Audio(`data:audio/wav;base64,${c}`);
      await new Promise((ok, bad) => { session.el.onended = ok; session.el.onpause = ok; session.el.onerror = bad; session.el.play().catch(bad); });
    }
  } catch (err) {
    alert(`Couldn't play narration: ${err.message}`);
  }
  if (state.audio === session) stopAudio();
}

init();
