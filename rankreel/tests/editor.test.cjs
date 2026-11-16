const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = readFileSync(path.join(__dirname, "../static/app.js"), "utf8");

function element() {
  const children = new Map();
  return {
    dataset: {},
    listeners: new Map(),
    addEventListener(type, listener) { this.listeners.set(type, listener); },
    querySelector(selector) {
      if (!children.has(selector)) children.set(selector, element());
      return children.get(selector);
    },
  };
}

function editor() {
  const document = element();
  document.querySelectorAll = () => [];
  document.querySelector("#clipTpl").content = { firstElementChild: { cloneNode: element } };
  const context = vm.createContext({
    document,
    localStorage: { getItem: () => null },
    Audio: class {},
    setTimeout,
    clearTimeout,
    changed() {},
  });
  // Load the editor's production logic without its initial redraw and API calls.
  vm.runInContext(source.slice(0, source.indexOf("// ------------------------------------------------------------------ wiring")), context);
  vm.runInContext("updateClip = () => {};", context);
  const run = (code, bindings = {}) => {
    Object.assign(context, bindings);
    return vm.runInContext(code, context);
  };
  return { run };
}

const plain = (value) => JSON.parse(JSON.stringify(value));

test("loading and saving preserves custom layout and sound settings", () => {
  const { run } = editor();
  const style = {
    width: 720, height: 1280, fps: 24, font_path: "assets/fonts/Custom.ttf",
    title_area_height: 250, rank_top: 300, rank_font_size: 42, rank_color: "#34C759",
    transition_sfx: "custom.mp3", sfx_volume: 0.4, fit: "contain", loudnorm: false,
  };
  const project = {
    title: [{ text: "TOP", color: "#FFFFFF" }],
    clips: [{ id: "a", url: "https://example.com/a", rank: 1, start: 1.2, end: 4.5 }],
    play_order: [1], style,
  };
  const saved = plain(run("state = fromProject(project); toProject()", { project }));
  for (const [key, value] of Object.entries(style)) assert.deepEqual(saved.style[key], value, key);
  assert.equal(saved.clips[0].start, 1.2);
  assert.equal(saved.clips[0].end, 4.5);
});

test("opening an older project does not add an unrequested sound", () => {
  const { run } = editor();
  assert.equal(run("fromProject({ title: [], clips: [], play_order: [] }).style.transition_sfx"), "");
});

test("new projects use the supplied transition sound", () => {
  const { run } = editor();
  assert.equal(run("freshState().style.transition_sfx"), "core_sound_effect.wav");
});

test("formatted timestamps round-trip across minute and hour boundaries", () => {
  const { run } = editor();
  for (const [seconds, formatted, rounded] of [
    [0, "0:00.0", 0], [1.23, "0:01.2", 1.2], [59.94, "0:59.9", 59.9],
    [59.99, "1:00.0", 60], [119.99, "2:00.0", 120], [3599.99, "60:00.0", 3600],
  ]) {
    assert.equal(run("fmtTime(seconds)", { seconds }), formatted);
    assert.equal(run("parseTime(fmtTime(seconds))", { seconds }), rounded);
  }
  for (const value of [null, undefined, NaN, Infinity]) assert.equal(run("fmtTime(value)", { value }), "");
});

function downloadingClip() {
  const { run } = editor();
  const pending = [];
  run("api = () => new Promise((resolve, reject) => pending.push({ resolve, reject }));", { pending });
  const clip = { id: "a", url: "https://example.com/first", rank: 1, meta: null };
  const row = run("state.clips = [clip]; buildClip(clip)", { clip });
  const editUrl = (url) => row.querySelector(".url").listeners.get("input")({ target: { value: url } });
  const start = () => run("fetchClip(clip, row)", { clip, row });
  return { run, clip, pending, editUrl, start };
}

const response = (name) => ({
  json: async () => ({ duration: 3, creator: `@${name}`, preview_url: `/media/${name}.mp4`, has_audio: true }),
});

test("changing a URL ignores an old response and unlocks the row", async () => {
  const { clip, pending, editUrl, start } = downloadingClip();
  const first = start();
  editUrl("https://example.com/second");
  assert.equal(clip.loading, false);
  pending[0].resolve(response("first"));
  await first;
  assert.equal(clip.meta, null);
  const second = start();
  pending[1].resolve(response("second"));
  await second;
  assert.equal(clip.meta.preview_url, "/media/second.mp4");
});

for (const oldFails of [false, true]) {
  test(`an obsolete ${oldFails ? "error" : "response"} cannot replace a newer download`, async () => {
    const { clip, pending, editUrl, start } = downloadingClip();
    const first = start();
    editUrl("https://example.com/second");
    const second = start();
    pending[1].resolve(response("second"));
    await second;
    if (oldFails) pending[0].reject(new Error("old download failed"));
    else pending[0].resolve(response("first"));
    await first;
    assert.equal(clip.meta.preview_url, "/media/second.mp4");
    assert.equal(clip.error, null);
    assert.equal(clip.loading, false);
  });
}

test("an old completion does not clear a newer download's loading state", async () => {
  const { clip, pending, editUrl, start } = downloadingClip();
  const first = start();
  editUrl("https://example.com/second");
  const second = start();
  pending[0].resolve(response("first"));
  await first;
  assert.equal(clip.loading, true);
  assert.equal(clip.meta, null);
  pending[1].resolve(response("second"));
  await second;
  assert.equal(clip.loading, false);
  assert.equal(clip.meta.creator, "@second");
});

test("a removed clip ignores its pending download", async () => {
  const { run, clip, pending, start } = downloadingClip();
  const first = start();
  run("state.clips = [];");
  pending[0].resolve(response("first"));
  await first;
  assert.equal(clip.meta, null);
});
