const $ = (s) => document.querySelector(s);
const state = { config: null, job: null, selected: 0, drawn: new Set(), audio: null, audioBtn: null, cache: new Map() };

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
const kb = (b) => (b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.ceil(b / 1e3) + " KB");

async function init() {
  state.config = await (await fetch("/api/config")).json();
  $("#demo-badge").hidden = !state.config.demo;
  for (const sel of ["#language", "#feedback-language"]) {
    $(sel).innerHTML = Object.entries(state.config.languages).map(([c, n]) => `<option value="${c}">${n}</option>`).join("");
  }
  $("#feedback-language").addEventListener("change", fillVoices);
  $("#narrate").addEventListener("change", fillVoices);
  fillVoices();
  setupDrop("qp", false);
  setupDrop("scripts", true);
  setupDrop("key", false, "Attach answer key file or <u>browse</u>");
  $("#setup").addEventListener("submit", submit);
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
  if (state.job) renderSheet();
}

function narrationOn() {
  return $("#narrate").checked && !!state.config.voices[$("#feedback-language").value];
}

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

  $("#go").disabled = true;
  $("#go").textContent = "Marking…";
  try {
    const r = await fetch("/api/jobs", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    state.job = await r.json();
    state.selected = 0;
    state.drawn.clear();
    $("#empty").hidden = true;
    $("#batch").hidden = false;
    $("#csv").href = `/api/jobs/${state.job.id}/csv`;
    render();
    if (matchMedia("(max-width: 960px)").matches) $("#batch").scrollIntoView({ behavior: "smooth" });
    poll();
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
  const id = state.job.id;
  const r = await fetch(`/api/jobs/${id}`);
  if (!r.ok || state.job?.id !== id) return;
  state.job = await r.json();
  render();
  if (state.job.status === "done" || state.job.status === "error") resetButton();
  else setTimeout(poll, 1500);
}

function render() {
  renderStats();
  renderRoster();
  const s = state.job.scripts[state.selected];
  const sig = `${state.job.id}|${state.selected}|${s?.stage}|${state.job.status}`;
  if (sig !== state.sheetSig) { state.sheetSig = sig; renderSheet(); }
}

function renderStats() {
  const done = state.job.scripts.filter((s) => s.result);
  const pcts = done.map((s) => s.result.percentage);
  const avg = pcts.length ? pcts.reduce((a, b) => a + b, 0) / pcts.length : null;
  const stats = [
    [`${done.length}/${state.job.scripts.length}`, "scripts marked"],
    [avg === null ? "—" : `${num(avg)}%`, "class average"],
    [pcts.length ? `${num(Math.max(...pcts))}%` : "—", "highest"],
    [pcts.length ? `${num(Math.min(...pcts))}%` : "—", "lowest"],
  ];
  if (state.job.status === "preparing") stats.unshift(["…", "reading question paper & key"]);
  $("#stats").innerHTML = stats.map(([b, s]) => `<div class="stat"><b>${b}</b><span>${s}</span></div>`).join("");
}

function renderRoster() {
  $("#roster").innerHTML = state.job.scripts.map((s, i) => {
    const r = s.result;
    const busy = s.stage === "reading" || s.stage === "marking";
    const right = r
      ? `<span class="mini">${num(r.total_awarded)}/${num(r.total_max)}</span>`
      : `<span class="stage ${s.stage === "error" ? "error" : ""}">${busy ? '<i class="dot"></i>' : ""}${STAGES[s.stage]}</span>`;
    return `<li><button type="button" data-i="${i}" aria-current="${i === state.selected}">
      <span class="who"><b>${esc(r?.student_name || s.file)}</b>${r ? `<small>${esc(s.file)} · Grade ${esc(r.grade)}</small>` : ""}</span>
      ${right}</button></li>`;
  }).join("");
  $("#roster").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    state.selected = +b.dataset.i;
    stopAudio();
    render();
  }));
}

function renderSheet() {
  const s = state.job.scripts[state.selected];
  const sheet = $("#sheet");
  if (!s) return;
  if (s.stage === "error") {
    sheet.innerHTML = `<h2>${esc(s.file)}</h2><p>This script couldn't be marked. Check the file is a clear scan under 10 pages per PDF, then mark it again.</p><p class="err">${esc(s.error)}</p>`;
    return;
  }
  if (!s.result) {
    const msg = state.job.status === "error" ? `Marking stopped: ${esc(state.job.error)}`
      : s.stage === "marking" ? "Marking answers…" : s.stage === "reading" ? "Reading handwriting…"
      : state.job.status === "preparing" || state.job.status === "queued" ? "Reading the question paper…" : "Waiting its turn…";
    sheet.innerHTML = `<div class="waiting">${msg}</div>`;
    return;
  }
  const r = s.result;
  const listen = (key, label) => narrationOn()
    ? `<button type="button" class="listen" data-key="${key}" data-state="idle">${PLAY}<span>${label}</span></button>` : "";
  const firstDraw = !state.drawn.has(state.selected);
  state.drawn.add(state.selected);

  const qs = (r.questions || []).map((q, i) => {
    const full = q.awarded >= q.max_marks && q.max_marks > 0;
    const zero = q.awarded === 0;
    const ans = tidy(q.student_answer).trim();
    const showMax = !/marks?\b/i.test(q.question || "");
    return `<section class="q">
      <div class="q-mark" aria-label="${num(q.awarded)} of ${num(q.max_marks)} marks">
        ${num(q.awarded)}/${num(q.max_marks)}<span class="tick" aria-hidden="true">${full ? "✓" : zero ? "✗" : "½✓"}</span>
      </div>
      <p class="q-title"><b>Q${esc(q.number || i + 1)}</b>${esc(tidy(q.question))}${showMax ? `<span class="max">[${num(q.max_marks)} marks]</span>` : ""}</p>
      <p class="answer ${ans ? "" : "empty-ans"}">${ans ? esc(ans) : "Not attempted"}</p>
      <div class="remark"><p>${esc(q.feedback)}</p>${listen(`q${i}`, "Listen")}</div>
    </section>`;
  }).join("");

  sheet.innerHTML = `
    <div class="sheet-head">
      <div><h2>${esc(r.student_name || "Unnamed student")}</h2><small>${esc(s.file)}</small></div>
      <div class="total ${firstDraw ? "draw" : ""}" aria-label="Total ${num(r.total_awarded)} of ${num(r.total_max)}, grade ${esc(r.grade)}">
        ${CIRCLE}<div class="num">${num(r.total_awarded)}/${num(r.total_max)}<small>${esc(r.grade)} · ${r.percentage}%</small></div>
      </div>
    </div>
    ${qs}
    <div class="overall"><h3>Teacher's remark</h3><p>${esc(r.overall_feedback)}</p>${listen("summary", "Listen to summary")}</div>
    <details class="ocr"><summary>What Sarvam Vision read</summary><pre>${esc(r.ocr_text)}</pre></details>`;

  sheet.querySelectorAll(".listen").forEach((b) => b.addEventListener("click", () => toggleNarration(b, r)));
}

function narrationText(r, key) {
  if (key === "summary") return r.narration || r.overall_feedback;
  const q = r.questions[+key.slice(1)];
  return q.feedback;
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

async function toggleNarration(btn, r) {
  if (btn.dataset.state !== "idle") { stopAudio(); return; }
  stopAudio();
  const text = narrationText(r, btn.dataset.key);
  const lang = $("#feedback-language").value, voice = $("#voice").value;
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
  } catch (e) {
    alert(`Couldn't play narration: ${e.message}`);
  }
  if (state.audio === session) stopAudio();
}

init();
