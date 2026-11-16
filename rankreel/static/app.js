/* RankReel UI: vanilla JS, no build step. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const STORE_KEY = "rankreel.project.v1";
const COLORS = ["#FFFFFF", "#FFD400", "#FF3B30", "#34C759"]; // then "custom"
const MAX_SCALE = 45; // duration bar scale (s)
const MAX_CLIPS = 10;

// ------------------------------------------------------------------ state
const uid = () => Math.random().toString(16).slice(2, 14);
const emptyClip = (rank) => ({ id: uid(), url: "", start: "", end: "", rank, name: "", meta: null });

function freshState() {
  return {
    title: [
      { text: "RANKING", color: "#FFFFFF" }, { text: "THE", color: "#FFFFFF" },
      { text: "FUNNIEST", color: "#FFD400" }, { text: "MOMENTS", color: "#FF3B30" },
    ],
    clips: [1, 2, 3, 4, 5].map(emptyClip),
    playOrder: [5, 4, 3, 2, 1],
    style: {
      fit: "cover", end_hold_seconds: 0, transition: "none", transition_seconds: 0.25,
      transition_sfx: "core_sound_effect.wav", sfx_volume: 0.8,
      rank_active_color: "#FFD400", title_uppercase: true, loudnorm: true,
      rank_font_size: 46, rank_spacing: 78, rank_stroke: 5,
    },
  };
}

let state = load() || freshState();
let validation = null;
let previewStep = -1;
const clipFetches = new WeakMap();

function load() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw);
    if (!Array.isArray(s.clips) || !Array.isArray(s.title)) return null;
    // Older saves lack newer options; fill them in without adding a sound they didn't pick.
    s.style = compactRanks({ ...freshState().style, transition_sfx: "", ...(s.style || {}) });
    return s;
  } catch { return null; }
}
function persist() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch { /* private mode etc. */ }
}

// ------------------------------------------------------------------ helpers
function parseTime(v) {
  // Mirrors app/timeparse.py. Returns number, null (empty) or NaN (invalid).
  if (v === null || v === undefined) return null;
  const s = String(v).trim().replace(",", ".");
  if (s === "") return null;
  const m = s.match(/^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$/);
  if (!m) return NaN;
  const [, a, b, sec] = m;
  const secF = parseFloat(sec);
  if (a === undefined) return secF;
  if (secF >= 60) return NaN;
  if (b === undefined) return +a * 60 + secF;
  if (+b >= 60) return NaN;
  return +a * 3600 + +b * 60 + secF;
}
function fmtTime(t) {
  if (t === null || t === undefined || !Number.isFinite(Number(t))) return "";
  // Round first so 59.99s carries into the next minute instead of becoming 0:60.0.
  const tenths = Math.round(Number(t) * 10);
  const m = Math.floor(tenths / 600);
  const s = ((tenths % 600) / 10).toFixed(1).padStart(4, "0");
  return `${m}:${s}`;
}
const isUrl = (u) => /^https?:\/\/\S+$/i.test((u || "").trim());
const placeholderUrl = (c) => `https://placeholder.invalid/clip/${c.id}`;
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const clipByRank = (r) => state.clips.find((c) => c.rank === r);

async function api(path, body, method = body ? "POST" : "GET") {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") msg = j.detail;
      else if (j.detail?.errors) msg = j.detail.errors.join("\n");
      else if (Array.isArray(j.detail)) msg = j.detail.map((d) => `${d.loc?.slice(1).join(".")}: ${d.msg}`).join("\n");
    } catch { /* not json */ }
    throw new Error(msg);
  }
  return res;
}

/** Client-side problems the server can't see (empty URL, bad timestamp). */
function clientErrors() {
  const errs = [];
  for (const c of state.clips) {
    if (!isUrl(c.url)) errs.push(`#${c.rank}: paste a TikTok or Instagram Reel link.`);
    else if (!c.meta) errs.push(`#${c.rank}: click Fetch to download the clip.`);
    if (Number.isNaN(parseTime(c.start))) errs.push(`#${c.rank}: invalid start time "${c.start}".`);
    if (Number.isNaN(parseTime(c.end))) errs.push(`#${c.rank}: invalid end time "${c.end}".`);
  }
  return errs;
}

/** The project JSON the API expects. */
function toProject() {
  const num = (v) => { const t = parseTime(v); return Number.isNaN(t) ? null : t; };
  return {
    title: state.title.filter((t) => t.text.trim()),
    clips: state.clips.map((c) => ({
      id: c.id,
      url: isUrl(c.url) ? c.url.trim() : placeholderUrl(c),
      start: num(c.start),
      end: num(c.end),
      rank: c.rank,
      name: c.name,
      source_duration: c.meta?.duration ?? null,
    })),
    play_order: state.playOrder,
    style: { ...state.style, transition_sfx: state.style.transition_sfx || null },
  };
}

// ------------------------------------------------------------------ title
const titleInput = $("#titleInput");
const customColor = $("#customColor");
let customTarget = -1;

function renderTitle() {
  if (document.activeElement !== titleInput) titleInput.value = state.title.map((t) => t.text).join(" ");
  const box = $("#titleChips");
  box.style.setProperty("--chip-case", state.style.title_uppercase ? "uppercase" : "none");
  box.replaceChildren(...state.title.map((t, i) => {
    const b = document.createElement("button");
    b.className = "chip";
    b.textContent = t.text;
    b.style.color = t.color;
    b.title = `${t.color} (click to change)`;
    b.addEventListener("click", () => cycleColor(i));
    return b;
  }));
}

function cycleColor(i) {
  const cur = state.title[i].color.toUpperCase();
  const idx = COLORS.indexOf(cur);
  if (idx === -1) { state.title[i].color = COLORS[0]; changed(); return; } // custom -> white
  if (idx < COLORS.length - 1) { state.title[i].color = COLORS[idx + 1]; changed(); return; }
  customTarget = i; // green -> custom
  customColor.value = "#00C8FF";
  customColor.click();
}
customColor.addEventListener("input", () => {
  if (customTarget < 0) return;
  state.title[customTarget].color = customColor.value.toUpperCase();
  changed();
});

titleInput.addEventListener("input", () => {
  const words = titleInput.value.split(/\s+/).filter(Boolean);
  const old = state.title;
  const sameCount = words.length === old.length; // editing a word in place keeps its color
  state.title = words.map((w, i) => {
    // Keep a word's color if the same word is still at that position (or moved by one).
    const same = [old[i], old[i - 1], old[i + 1]].find((t) => t && t.text === w);
    if (same) return { text: w, color: same.color };
    return { text: w, color: sameCount ? old[i].color : "#FFFFFF" };
  });
  changed();
});

// ------------------------------------------------------------------ clips
const clipsBox = $("#clips");

function renderClips() {
  const sorted = [...state.clips].sort((a, b) => a.rank - b.rank);
  const n = state.clips.length;
  const countSel = $("#clipCount");
  if (countSel.options.length !== MAX_CLIPS) {
    countSel.replaceChildren(...Array.from({ length: MAX_CLIPS }, (_, i) => new Option(String(i + 1), i + 1)));
  }
  countSel.value = n;
  $("#addClip").disabled = n >= MAX_CLIPS;
  // Reuse DOM nodes by id so videos/focus survive re-renders.
  const existing = new Map([...clipsBox.children].map((el) => [el.dataset.id, el]));
  const nodes = sorted.map((c) => {
    let el = existing.get(c.id);
    if (!el) el = buildClip(c);
    updateClip(el, c);
    return el;
  });
  // Only move rows that are out of place: detaching a row (as replaceChildren
  // does) drops focus, which would eat keystrokes typed while a fetch finishes.
  const keep = new Set(nodes);
  for (const el of [...clipsBox.children]) if (!keep.has(el)) el.remove();
  nodes.forEach((el, i) => { if (clipsBox.children[i] !== el) clipsBox.insertBefore(el, clipsBox.children[i] || null); });
}

function buildClip(c) {
  const el = $("#clipTpl").content.firstElementChild.cloneNode(true);
  el.dataset.id = c.id;
  const get = () => state.clips.find((x) => x.id === el.dataset.id);
  const video = $(".cv", el);

  $(".rank", el).addEventListener("change", (e) => setRank(get(), +e.target.value));
  $(".name", el).addEventListener("input", (e) => { get().name = e.target.value; changed({ clips: false }); });
  $(".remove", el).addEventListener("click", () => removeClip(get()));
  const urlIn = $(".url", el);
  urlIn.addEventListener("input", (e) => {
    const clip = get();
    if (clip.url.trim() !== e.target.value.trim()) {
      clip.meta = null; clip.error = null; clip.loading = false;
      clipFetches.delete(clip); // an older download must not update this row
    }
    clip.url = e.target.value;
    changed({ clips: false });
    updateClip(el, clip);
  });
  urlIn.addEventListener("keydown", (e) => { if (e.key === "Enter") fetchClip(get(), el); });
  urlIn.addEventListener("paste", () => setTimeout(() => isUrl(urlIn.value) && fetchClip(get(), el), 0));
  $(".fetch", el).addEventListener("click", () => fetchClip(get(), el));
  for (const k of ["start", "end"]) {
    $(`.${k}`, el).addEventListener("input", (e) => { get()[k] = e.target.value; changed({ clips: false }); updateClip(el, get()); });
  }
  $(".set-start", el).addEventListener("click", () => { const c = get(); c.start = fmtTime(video.currentTime); changed(); });
  $(".set-end", el).addEventListener("click", () => { const c = get(); c.end = fmtTime(video.currentTime); changed(); });
  $(".play-trim", el).addEventListener("click", () => playTrim(get(), video));
  return el;
}

function updateClip(el, c) {
  const n = state.clips.length;
  const sel = $(".rank", el);
  if (sel.options.length !== n) {
    sel.replaceChildren(...Array.from({ length: n }, (_, i) => new Option(`#${i + 1}`, i + 1)));
  }
  sel.value = c.rank;
  const setVal = (q, v) => { const i = $(q, el); if (document.activeElement !== i) i.value = v ?? ""; };
  setVal(".name", c.name);
  setVal(".url", c.url);
  setVal(".start", c.start);
  setVal(".end", c.end);
  $(".start", el).classList.toggle("bad", Number.isNaN(parseTime(c.start)));
  $(".end", el).classList.toggle("bad", Number.isNaN(parseTime(c.end)));

  const video = $(".cv", el);
  const fetched = !!c.meta;
  $(".thumb", el).hidden = !fetched;
  $(".trim-btns", el).hidden = !fetched;
  if (fetched && video.dataset.src !== c.meta.preview_url) {
    video.dataset.src = c.meta.preview_url;
    video.src = c.meta.preview_url;
  }
  $(".meta", el).textContent = fetched
    ? `${c.meta.creator || "unknown creator"} · ${fmtTime(c.meta.duration)} long${c.meta.has_audio ? "" : " · no audio"}`
    : "";
  const len = validation?.clip_lengths?.[c.id];
  const lenEl = $(".len", el);
  lenEl.textContent = len != null ? `${len.toFixed(1)}s` : "";
  lenEl.classList.toggle("long", len != null && len > 8);

  const st = $(".status", el);
  st.className = "status small";
  if (c.loading) { st.textContent = "Downloading…"; }
  else if (c.error) { st.textContent = c.error; st.classList.add("err"); }
  else st.textContent = "";
  el.classList.toggle("error", !!c.error);
  $(".fetch", el).disabled = !!c.loading;
  $(".fetch", el).textContent = fetched ? "Refetch" : "Fetch";
}

async function fetchClip(c, el) {
  if (!c || c.loading) return;
  if (!isUrl(c.url)) { c.error = "Paste a full TikTok or Instagram Reel link (https://…)."; updateClip(el, c); return; }
  const url = c.url.trim();
  const request = Symbol();
  clipFetches.set(c, request);
  const isCurrent = () => clipFetches.get(c) === request && c.url.trim() === url && state.clips.includes(c);
  c.loading = true; c.error = null;
  updateClip(el, c);
  try {
    const res = await api("/api/clips/fetch", { url });
    const j = await res.json();
    if (isCurrent()) {
      c.meta = { duration: j.duration, creator: j.creator, preview_url: j.preview_url, has_audio: j.has_audio };
    }
  } catch (e) {
    if (isCurrent()) {
      c.meta = null;
      c.error = e.message;
    }
  } finally {
    if (clipFetches.get(c) === request) {
      clipFetches.delete(c);
      c.loading = false;
      if (state.clips.includes(c)) changed();
    }
  }
}

function playTrim(c, video) {
  const start = parseTime(c.start) || 0;
  const end = parseTime(c.end);
  video.currentTime = start;
  video.play();
  const stop = () => {
    if (end != null && !isNaN(end) && video.currentTime >= end) { video.pause(); video.removeEventListener("timeupdate", stop); }
  };
  video.addEventListener("timeupdate", stop);
  video.addEventListener("pause", () => video.removeEventListener("timeupdate", stop), { once: true });
}

/** Changing a clip's rank swaps it with the clip that had that rank. */
function setRank(c, rank) {
  const other = clipByRank(rank);
  if (other && other !== c) other.rank = c.rank;
  c.rank = rank;
  changed();
}

function addClip() {
  const n = state.clips.length;
  if (n >= MAX_CLIPS) return;
  state.clips.push(emptyClip(n + 1));
  state.playOrder.unshift(n + 1); // new worst rank plays first
  changed();
}

/** Grow or shrink to n clips. Growing adds empty rows as the new lowest ranks;
 *  shrinking drops the lowest ranks (asks first if they have content). */
function setClipCount(n) {
  n = Math.max(1, Math.min(MAX_CLIPS, n));
  const dropped = state.clips.filter((c) => c.rank > n).sort((a, b) => a.rank - b.rank);
  const filled = dropped.filter((c) => c.url || c.name);
  if (filled.length) {
    const list = filled.map((c) => `#${c.rank}${c.name ? ` (${c.name})` : ""}`).join(", ");
    if (!confirm(`Going to ${n} clips removes ${list}. Continue?\n(To remove a different clip, use its ✕ instead.)`)) {
      renderClips();
      return;
    }
  }
  state.clips = state.clips.filter((c) => c.rank <= n);
  state.playOrder = state.playOrder.filter((r) => r <= n);
  while (state.clips.length < n) {
    const r = state.clips.length + 1;
    state.clips.push(emptyClip(r));
    state.playOrder.unshift(r);
  }
  changed();
}
$("#clipCount").addEventListener("change", (e) => setClipCount(+e.target.value));

function removeClip(c) {
  if (state.clips.length <= 1) return;
  if ((c.url || c.name) && !confirm(`Remove #${c.rank}${c.name ? ` (${c.name})` : ""}?`)) return;
  const r = c.rank;
  state.clips = state.clips.filter((x) => x !== c);
  for (const x of state.clips) if (x.rank > r) x.rank -= 1;
  state.playOrder = state.playOrder.filter((x) => x !== r).map((x) => (x > r ? x - 1 : x));
  changed();
}
$("#addClip").addEventListener("click", addClip);

// ------------------------------------------------------------------ playback order
const orderBox = $("#order");
let dragFrom = -1;

function renderOrder() {
  const nodes = [];
  state.playOrder.forEach((rank, i) => {
    if (i) { const a = document.createElement("span"); a.className = "arrow"; a.textContent = "→"; nodes.push(a); }
    const c = clipByRank(rank);
    const chip = document.createElement("div");
    chip.className = "ochip";
    chip.draggable = true;
    chip.tabIndex = 0;
    chip.dataset.index = i;
    chip.innerHTML = `<span class="pos">${i + 1}.</span><b>#${rank}</b><span></span>`;
    chip.lastElementChild.textContent = c?.name || "";
    chip.title = `Plays ${ordinal(i + 1)}: #${rank} ${c?.name || ""}`;
    chip.addEventListener("dragstart", (e) => { dragFrom = i; chip.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(i)); });
    chip.addEventListener("dragend", () => { dragFrom = -1; chip.classList.remove("dragging"); });
    chip.addEventListener("dragover", (e) => { e.preventDefault(); chip.classList.add("over"); });
    chip.addEventListener("dragleave", () => chip.classList.remove("over"));
    chip.addEventListener("drop", (e) => { e.preventDefault(); chip.classList.remove("over"); moveOrder(dragFrom, i); });
    chip.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" && i > 0) { moveOrder(i, i - 1); focusChip(i - 1); }
      if (e.key === "ArrowRight" && i < state.playOrder.length - 1) { moveOrder(i, i + 1); focusChip(i + 1); }
    });
    nodes.push(chip);
  });
  orderBox.replaceChildren(...nodes);
}
const focusChip = (i) => requestAnimationFrame(() => orderBox.querySelector(`[data-index="${i}"]`)?.focus());
function ordinal(n) {
  const teen = n % 100 >= 11 && n % 100 <= 13;
  return n + (teen ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th"));
}

function moveOrder(from, to) {
  if (from < 0 || from === to) return;
  const o = state.playOrder;
  const [r] = o.splice(from, 1);
  o.splice(to, 0, r);
  changed();
}

function presetOrder(kind, n) {
  const worst = Array.from({ length: n }, (_, i) => n - i); // [n..1]
  if (kind !== "suspense" || n < 3) return worst;
  if (n === 3) return [2, 3, 1];
  // Strong opener: e.g. N=5 -> 4, 2, 5, 3, 1 (#2 early, #1 last).
  const others = worst.slice(0, -1); // [n..2]
  const rest = others.filter((r) => r !== others[1] && r !== 2 && r !== others[0]);
  return [others[1], 2, others[0], ...rest, 1];
}
document.querySelectorAll("[data-preset]").forEach((b) =>
  b.addEventListener("click", () => { state.playOrder = presetOrder(b.dataset.preset, state.clips.length); changed(); }));

// ------------------------------------------------------------------ style options
const opt = {
  fit: $("#optFit"), hold: $("#optHold"), tx: $("#optTx"), txDur: $("#optTxDur"), sfxVol: $("#optSfxVol"), sfx: $("#optSfx"), active: $("#optActive"), upper: $("#optUpper"), loud: $("#optLoud"),
};
function renderStyle() {
  opt.fit.value = state.style.fit;
  const rankSel = $("#optRankSize");
  const size = String(state.style.rank_font_size);
  if (![...rankSel.options].some((o) => o.value === size)) rankSel.add(new Option(`Custom (${size}px)`, size));
  rankSel.value = size;
  opt.hold.value = String(state.style.end_hold_seconds);
  opt.tx.value = state.style.transition;
  opt.txDur.value = String(state.style.transition_seconds);
  opt.txDur.disabled = state.style.transition === "none";
  opt.sfx.value = state.style.transition_sfx || "";
  opt.sfxVol.value = String(state.style.sfx_volume);
  opt.sfxVol.disabled = !state.style.transition_sfx;
  $("#sfxDelete").hidden = !sfxList.some((f) => f.name === state.style.transition_sfx && !f.builtin);
  opt.active.value = state.style.rank_active_color;
  opt.upper.checked = state.style.title_uppercase;
  opt.loud.checked = state.style.loudnorm;
  applyPreviewFit();
}
opt.fit.addEventListener("change", () => { state.style.fit = opt.fit.value; changed(); });
$("#optRankSize").addEventListener("change", (e) => { setRankSize(+e.target.value); changed(); });
opt.hold.addEventListener("change", () => { state.style.end_hold_seconds = +opt.hold.value; changed(); });
opt.tx.addEventListener("change", () => { state.style.transition = opt.tx.value; changed(); playTransition({ audition: true }); });
opt.txDur.addEventListener("change", () => { state.style.transition_seconds = +opt.txDur.value; changed(); playTransition({ audition: true }); });
opt.sfx.addEventListener("change", () => { state.style.transition_sfx = opt.sfx.value; changed(); playSfx(); });
opt.sfxVol.addEventListener("change", () => { state.style.sfx_volume = +opt.sfxVol.value; changed(); playSfx(); });

const sfxAudio = new Audio();
/** Play the chosen transition sound (audition, and on cuts in the live preview). */
let sfxStopTimers = [];
/** Play the transition sound like the render does: from its start for as long as the
 *  visual transition lasts (whole sound when there's no transition), then a short fade. */
function playSfx() {
  const name = state.style.transition_sfx;
  if (!name) return;
  const src = `/sfx/${encodeURIComponent(name)}`;
  if (!sfxAudio.src.endsWith(src)) sfxAudio.src = src;
  const vol = Math.min(1, state.style.sfx_volume / 1.2);
  sfxStopTimers.forEach(clearTimeout);
  sfxStopTimers = [];
  sfxAudio.volume = vol;
  sfxAudio.currentTime = 0;
  sfxAudio.play().catch(() => {});
  if (state.style.transition === "none") return;
  const lenMs = state.style.transition_seconds * 1000;
  const fadeMs = Math.min(60, lenMs / 3);
  const steps = 6;
  for (let k = 1; k <= steps; k++) {
    sfxStopTimers.push(setTimeout(() => { sfxAudio.volume = vol * (1 - k / steps); },
      lenMs - fadeMs + (fadeMs * k) / steps));
  }
  sfxStopTimers.push(setTimeout(() => { sfxAudio.pause(); sfxAudio.volume = vol; }, lenMs + 5));
}
const sfxLabel = (f) => {
  const base = f.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ");
  return base.charAt(0).toUpperCase() + base.slice(1);
};
opt.active.addEventListener("input", () => { state.style.rank_active_color = opt.active.value.toUpperCase(); changed(); });
opt.upper.addEventListener("change", () => { state.style.title_uppercase = opt.upper.checked; changed(); });
opt.loud.addEventListener("change", () => { state.style.loudnorm = opt.loud.checked; changed(); });

const MAX_SFX_MB = 10;
const SFX_ALIASES = { "core_sound_effect.mp3": "core_sound_effect.wav" }; // keep in sync with app/config.py
let sfxList = []; // [{name, builtin}]

async function loadSfx() {
  try {
    sfxList = await (await api("/api/sfx")).json();
  } catch { return; }
  const group = (label, items) => {
    const g = document.createElement("optgroup");
    g.label = label;
    for (const f of items) g.append(new Option(sfxLabel(f.name), f.name));
    return g;
  };
  const builtin = sfxList.filter((f) => f.builtin);
  const custom = sfxList.filter((f) => !f.builtin);
  opt.sfx.replaceChildren(new Option("None", ""), group("Built-in", builtin));
  if (custom.length) opt.sfx.append(group("Your sounds", custom));
  const names = sfxList.map((f) => f.name);
  const renamed = SFX_ALIASES[state.style.transition_sfx];
  if (renamed && !names.includes(state.style.transition_sfx) && names.includes(renamed)) {
    state.style.transition_sfx = renamed; // project saved with the sound's old name
    persist();
  }
  if (state.style.transition_sfx && !names.includes(state.style.transition_sfx)) {
    sfxMessage(`"${state.style.transition_sfx}" is no longer available; sound set to None.`, true);
    state.style.transition_sfx = "";
    persist();
  }
  renderStyle();
}

function sfxMessage(text, isErr = false) {
  const el = $("#sfxMsg");
  el.textContent = text;
  el.classList.toggle("err", isErr);
}

$("#sfxUpload").addEventListener("click", () => $("#sfxFile").click());
$("#sfxFile").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = ""; // allow picking the same file again
  if (!file) return;
  if (file.size > MAX_SFX_MB * 1024 * 1024) { sfxMessage(`That file is over ${MAX_SFX_MB} MB.`, true); return; }
  sfxMessage(`Uploading ${file.name}…`);
  try {
    const res = await fetch(`/api/sfx?filename=${encodeURIComponent(file.name)}`, {
      method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file,
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof j.detail === "string" ? j.detail : `Upload failed (${res.status})`);
    await loadSfx();
    state.style.transition_sfx = j.name;
    changed();
    sfxMessage(`Added "${sfxLabel(j.name)}" (${j.duration.toFixed(1)}s).`);
    playSfx();
  } catch (err) {
    sfxMessage(err.message, true);
  }
});

$("#sfxDelete").addEventListener("click", async () => {
  const name = state.style.transition_sfx;
  if (!name || !confirm(`Delete the sound "${sfxLabel(name)}"? Projects using it will need another sound.`)) return;
  try {
    await api(`/api/sfx/${encodeURIComponent(name)}`, undefined, "DELETE");
    state.style.transition_sfx = "";
    await loadSfx();
    changed();
    sfxMessage(`Deleted "${sfxLabel(name)}".`);
  } catch (err) { sfxMessage(err.message, true); }
});

// ------------------------------------------------------------------ validation + duration
const runValidate = debounce(async () => {
  try {
    const res = await api("/api/validate", toProject());
    validation = await res.json();
  } catch (e) {
    validation = { total_seconds: 0, known_total: false, clip_lengths: {}, errors: [e.message], warnings: [], hints: [], over_limit: false };
  }
  renderValidation();
  for (const el of clipsBox.children) updateClip(el, state.clips.find((c) => c.id === el.dataset.id));
}, 200);

function renderValidation() {
  const v = validation || { total_seconds: 0, errors: [], warnings: [], hints: [], over_limit: false, known_total: true };
  const total = v.total_seconds || 0;
  $("#durTotal").textContent = `${total.toFixed(1)}s${v.known_total ? "" : "+"}`;
  $("#durFill").style.width = `${Math.min(100, (total / MAX_SCALE) * 100)}%`;
  $(".duration").classList.toggle("over", !!v.over_limit);
  $("#durMsg").textContent = v.over_limit ? "Over 40s — trim clips" : "";

  const cErr = clientErrors();
  const items = [
    ...[...new Set([...cErr, ...v.errors])].map((t) => ["error", t]),
    ...v.warnings.filter((w) => !/exceeds/.test(w)).map((t) => ["warning", t]),
    ...v.hints.map((t) => ["hint", t]),
  ];
  $("#messages").replaceChildren(...items.map(([cls, t]) => {
    const li = document.createElement("li"); li.className = cls; li.textContent = t; return li;
  }));
  $("#renderBtn").disabled = rendering || cErr.length > 0 || v.errors.length > 0;
}

// ------------------------------------------------------------------ preview
const pv = $("#pv");
const pvOverlay = $("#pvOverlay");
const overlayCache = new Map(); // key -> objectURL
let overlayGen = 0;
let playing = false;

function renderStepButtons() {
  const box = $("#stepBtns");
  const mk = (label, step, title) => {
    const b = document.createElement("button");
    b.textContent = label; b.title = title;
    b.classList.toggle("on", step === previewStep);
    b.addEventListener("click", () => { stopPlayAll(); showStep(step); });
    return b;
  };
  box.replaceChildren(
    mk("0", -1, "Before any clip: numbers only"),
    ...state.playOrder.map((r, i) => mk(`#${r}`, i, `Plays ${ordinal(i + 1)}`)),
  );
}

async function overlayUrl(step) {
  const project = toProject();
  const key = JSON.stringify([project.title, project.clips.map((c) => [c.rank, c.name]), project.play_order, project.style, step]);
  if (overlayCache.has(key)) return overlayCache.get(key);
  const res = await api("/api/preview-overlay", { project, step });
  const url = URL.createObjectURL(await res.blob());
  if (overlayCache.size > 40) { const [k, u] = overlayCache.entries().next().value; URL.revokeObjectURL(u); overlayCache.delete(k); }
  overlayCache.set(key, url);
  return url;
}

async function showStep(step) {
  previewStep = step;
  renderStepButtons();
  const gen = ++overlayGen;
  const rank = step >= 0 ? state.playOrder[step] : state.playOrder[0];
  const c = clipByRank(rank);
  $("#pvEmpty").hidden = !!c?.meta;
  if (c?.meta) {
    if (pv.dataset.src !== c.meta.preview_url) { pv.dataset.src = c.meta.preview_url; pv.src = c.meta.preview_url; }
    const start = parseTime(c.start) || 0;
    if (!playing) { pv.pause(); seek(pv, start); }
  } else { pv.removeAttribute("src"); pv.dataset.src = ""; pv.load(); }
  try {
    const url = await overlayUrl(step);
    if (gen === overlayGen) pvOverlay.src = url;
  } catch { /* validation shows the error */ }
}

// ---- preview fit: mirrors effective_fit() in app/compose.py
const MAX_FILL_CROP = 0.06;
const pvBg = $("#pvBg");

function effectiveFit(w, h) {
  const fit = state.style.fit;
  if (fit !== "cover") return fit;
  if (!w || !h) return "crop";
  const src = w / h, dst = 9 / 16;
  return 1 - Math.min(src, dst) / Math.max(src, dst) <= MAX_FILL_CROP ? "crop" : "blur";
}

function applyPreviewFit() {
  const mode = effectiveFit(pv.videoWidth, pv.videoHeight);
  const phone = $("#phone");
  phone.classList.toggle("whole", mode !== "crop");
  phone.classList.toggle("blur", mode === "blur");
  if (mode === "blur") paintBackdrop();
}

/** Blurred backdrop for "show whole video": a tiny cover-scaled copy of the current frame. */
function paintBackdrop() {
  if (!pv.videoWidth) return;
  const ctx = pvBg.getContext("2d");
  const W = pvBg.width, H = pvBg.height;
  const s = Math.max(W / pv.videoWidth, H / pv.videoHeight);
  const w = pv.videoWidth * s, h = pv.videoHeight * s;
  try { ctx.drawImage(pv, (W - w) / 2, (H - h) / 2, w, h); } catch { /* frame not ready */ }
}
pv.addEventListener("loadedmetadata", applyPreviewFit);
pv.addEventListener("seeked", () => $("#phone").classList.contains("blur") && paintBackdrop());
let lastBackdrop = 0;
pv.addEventListener("timeupdate", () => {
  if (!$("#phone").classList.contains("blur")) return;
  const now = performance.now();
  if (now - lastBackdrop > 200) { lastBackdrop = now; paintBackdrop(); }
});

function seek(video, t) {
  if (video.readyState >= 1) video.currentTime = t;
  else video.addEventListener("loadedmetadata", () => { video.currentTime = t; }, { once: true });
}

const refreshPreview = debounce(() => { if (!playing) showStep(Math.min(previewStep, state.playOrder.length - 1)); }, 250);

/** CSS approximation of the rendered transition (footage only; the overlay stays still). */
function playTransition({ out = false, audition = false } = {}) {
  const kind = state.style.transition;
  const ms = state.style.transition_seconds * 1000;
  const fx = $("#pvFx");
  const ease = "cubic-bezier(.2,.7,.3,1)";
  // Same timing as the render: the sound starts when the visual transition starts:
  // at the cut, or for "dip to black" when the screen starts darkening.
  if (audition || (kind === "fade" ? out : !out)) playSfx();
  if (kind === "none") return;
  if (kind === "shake") {
    const frames = [];
    for (let k = 0; k <= 10; k++) {
      const a = 5 * (1 - k / 10); // % of frame, decaying
      frames.push({ transform: `scale(${1 + 0.1 * (1 - k / 10)}) translate(${(k % 2 ? 1 : -1) * a}%, ${(k % 3 ? -1 : 1) * a * 0.7}%)` });
    }
    pv.animate(frames, { duration: ms });
    return;
  }
  if (kind === "fade") {
    fx.style.background = "#000";
    fx.animate(out ? [{ opacity: 0 }, { opacity: 1 }] : [{ opacity: 1 }, { opacity: 0 }], { duration: ms / 2 });
    return;
  }
  if (out) return;
  if (kind === "flash" || kind === "punch") {
    fx.style.background = "#fff";
    fx.animate([{ opacity: 1 }, { opacity: 0 }], { duration: ms });
  }
  if (kind === "zoom" || kind === "punch") pv.animate([{ transform: "scale(1.18)" }, { transform: "scale(1)" }], { duration: ms, easing: ease });
  if (kind === "slide") pv.animate([{ transform: "translateX(100%)" }, { transform: "translateX(0)" }], { duration: ms, easing: ease });
}

/** Play every segment in order with its overlay: a rough live preview of the render. */
async function playAll() {
  if (playing) { stopPlayAll(); return; }
  const steps = state.playOrder.map((r) => clipByRank(r));
  if (steps.some((c) => !c?.meta)) { alert("Fetch every clip first."); return; }
  playing = true;
  $("#playAll").textContent = "■ Stop";
  // Pre-render all overlays so switching is instant.
  await Promise.all(steps.map((_, i) => overlayUrl(i).catch(() => null)));
  for (let i = 0; i < steps.length && playing; i++) {
    await showStep(i);
    const c = steps[i];
    const start = parseTime(c.start) || 0;
    const end = parseTime(c.end) ?? c.meta.duration;
    await playSegment(start, end, { txIn: i > 0, txOut: i < steps.length - 1 });
  }
  stopPlayAll();
}
function playSegment(start, end, { txIn = false, txOut = false } = {}) {
  return new Promise((resolve) => {
    let outPlayed = false;
    const outAt = end - state.style.transition_seconds / 2;
    const done = () => { pv.removeEventListener("timeupdate", tick); pv.removeEventListener("ended", done); resolve(); };
    const tick = () => {
      if (txOut && !outPlayed && pv.currentTime >= outAt) { outPlayed = true; playTransition({ out: true }); }
      if (!playing || pv.currentTime >= end) { pv.pause(); done(); }
    };
    const go = () => {
      pv.currentTime = start;
      pv.play().then(() => txIn && playTransition()).catch(done);
      pv.addEventListener("timeupdate", tick); pv.addEventListener("ended", done);
    };
    if (pv.readyState >= 1) go(); else pv.addEventListener("loadedmetadata", go, { once: true });
  });
}
function stopPlayAll() {
  playing = false;
  pv.pause();
  $("#playAll").textContent = "▶ Play preview";
}
$("#playAll").addEventListener("click", playAll);

// ------------------------------------------------------------------ render
let rendering = false;
$("#renderBtn").addEventListener("click", async () => {
  if (rendering) return;
  rendering = true;
  renderValidation();
  $("#renderBox").hidden = false;
  $("#resultBox").hidden = true;
  setProgress(0, "Starting…");
  try {
    const { job_id } = await (await api("/api/render", toProject())).json();
    for (;;) {
      await new Promise((r) => setTimeout(r, 700));
      const st = await (await api(`/api/render/${job_id}`)).json();
      setProgress(st.progress, st.message);
      if (st.status === "done") { showResult(st); break; }
      if (st.status === "error") throw new Error(st.error);
    }
  } catch (e) {
    setProgress(0, "");
    $("#progMsg").textContent = `Render failed: ${e.message}`;
    $("#progMsg").style.color = "var(--red)";
  } finally {
    rendering = false;
    renderValidation();
  }
});
function setProgress(f, msg) {
  $("#progFill").style.width = `${Math.round(f * 100)}%`;
  $("#progMsg").textContent = msg ? `${Math.round(f * 100)}% · ${msg}` : "";
  $("#progMsg").style.color = "";
}
function showResult(st) {
  $("#resultBox").hidden = false;
  $("#resultVideo").src = st.output_url;
  $("#downloadLink").href = `${st.output_url}?download=1`;
  $("#creditsText").value = st.credits_text || "";
  $("#progMsg").textContent = `Done · saved as output/${st.output_url.split("/").pop()}`;
}
$("#copyCredits").addEventListener("click", async () => {
  const ta = $("#creditsText");
  try { await navigator.clipboard.writeText(ta.value); }
  catch { ta.select(); document.execCommand("copy"); }
  $("#copyCredits").textContent = "Copied ✓";
  setTimeout(() => ($("#copyCredits").textContent = "Copy credits"), 1500);
});

// ------------------------------------------------------------------ projects
const projectSelect = $("#projectSelect");
async function loadProjectList() {
  try {
    const list = await (await api("/api/projects")).json();
    projectSelect.replaceChildren(new Option("Load project…", ""),
      ...list.map((p) => new Option(`${p.title || p.name} · ${p.modified.replace("T", " ")}`, p.name)));
  } catch { /* ignore */ }
}
/** Replace the editor with a project (from the saved list or an import) and fetch its clips. */
function loadProjectData(p) {
  stopPlayAll();
  state = fromProject(p);
  for (const c of state.clips) if (isUrl(c.url)) refetchQuietly(c);
  previewStep = -1;
  clipsBox.replaceChildren();
  changed();
  loadSfx(); // resets the sound to None if the project names one that isn't installed
}

projectSelect.addEventListener("change", async () => {
  const name = projectSelect.value;
  if (!name) return;
  try {
    loadProjectData(await (await api(`/api/projects/${encodeURIComponent(name)}`)).json());
  } catch (e) { alert(`Couldn't load: ${e.message}`); }
  projectSelect.value = "";
});

// ------------------------------------------------------------------ import popup
const importDlg = $("#importDlg");
const importText = $("#importText");
const importMsg = $("#importMsg");

$("#importBtn").addEventListener("click", () => {
  importMsg.textContent = "";
  importDlg.showModal();
  importText.focus();
});
$("#importCancel").addEventListener("click", () => importDlg.close());
importDlg.addEventListener("click", (e) => { if (e.target === importDlg) importDlg.close(); }); // click outside
$("#importFileBtn").addEventListener("click", () => $("#importFile").click());
$("#importFile").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (file) { importText.value = await file.text(); importMsg.textContent = ""; }
});
importText.addEventListener("input", () => { importMsg.textContent = ""; });
importText.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("#importForm").requestSubmit(); }
});

$("#importForm").addEventListener("submit", async (e) => {
  e.preventDefault(); // keep the dialog open until the import succeeds
  importMsg.textContent = "";
  const go = $("#importGo");
  go.disabled = true;
  try {
    const res = await fetch("/api/projects/import", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: importText.value }),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof j.detail === "string" ? j.detail : `Import failed (${res.status})`);
    const hasWork = state.clips.some((c) => c.url || c.name);
    if (hasWork && !confirm("Replace the project that's in the editor now? (Save it first if you need it.)")) return;
    if ($("#importSave").checked) {
      const name = j.title.map((t) => t.text).join(" ");
      await api("/api/projects", { name, project: j, unique: true });
      loadProjectList();
    }
    loadProjectData(j);
    importDlg.close();
    importText.value = "";
  } catch (err) {
    importMsg.textContent = err.message;
  } finally {
    go.disabled = false;
  }
});
function fromProject(p) {
  const base = freshState();
  return {
    title: p.title,
    clips: p.clips.map((c) => ({
      id: c.id || uid(), url: c.url, rank: c.rank, name: c.name || "",
      start: c.start != null ? fmtTime(c.start) : "", end: c.end != null ? fmtTime(c.end) : "", meta: null,
    })),
    playOrder: p.play_order,
    style: compactRanks({ ...base.style, ...(p.style || {}), transition_sfx: p.style?.transition_sfx || "" }),
  };
}

/** Same rule as the server (models.Style): the old default 64px/110px list becomes the compact one. */
function compactRanks(style) {
  if (style.rank_font_size === 64 && style.rank_spacing === 110) {
    style = { ...style, rank_font_size: 46, rank_spacing: 78 };
    if (style.rank_stroke === 6) style.rank_stroke = 5;
  }
  return style;
}

/** Rank text size: row spacing and outline follow the text size. */
function setRankSize(size) {
  state.style.rank_font_size = size;
  state.style.rank_spacing = Math.round(size * 1.7);
  state.style.rank_stroke = size >= 52 ? 6 : 5;
}
async function refetchQuietly(c) {
  // Clips in the server cache come back instantly; others download.
  const el = [...clipsBox.children].find((x) => x.dataset.id === c.id);
  if (el) fetchClip(c, el); else setTimeout(() => refetchQuietly(c), 50);
}
$("#saveBtn").addEventListener("click", async () => {
  const suggested = state.title.map((t) => t.text).join(" ").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
  const raw = prompt("Project name:", suggested);
  if (!raw) return;
  const name = raw.normalize("NFKD").replace(/[^\x00-\x7f]/g, "").replace(/[^a-zA-Z0-9]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase().slice(0, 64) || "project";
  try {
    const existing = await (await api("/api/projects")).json();
    if (existing.some((p) => p.name === name) && !confirm(`A project called "${name}" already exists. Overwrite it?`)) return;
    await api("/api/projects", { name, project: toProject() });
    await loadProjectList();
    $("#saveBtn").textContent = "Saved ✓";
    setTimeout(() => ($("#saveBtn").textContent = "Save"), 1500);
  } catch (e) { alert(`Couldn't save: ${e.message}`); }
});
$("#newBtn").addEventListener("click", () => {
  if (!confirm("Start a new project? The current one is cleared (save it first if needed).")) return;
  state = freshState();
  previewStep = -1;
  clipsBox.replaceChildren();
  changed();
});

// ------------------------------------------------------------------ wiring
function changed({ clips = true } = {}) {
  persist();
  renderTitle();
  if (clips) renderClips();
  renderOrder();
  renderStyle();
  renderStepButtons();
  renderValidation();
  runValidate();
  refreshPreview();
}

for (const c of state.clips) c.loading = false; // a reload mid-download leaves this set
changed();
loadSfx();
loadProjectList();
