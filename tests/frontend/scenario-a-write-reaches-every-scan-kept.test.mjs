/**
 * Owner scenario I9, a write's side: this browser keeps each folder's scan for half an hour (web/tagpup/cache.js), and a
 * folder's scan is recursive, so a photo is held by the scan of its own folder and by the scan of every folder above it.
 * The owner opens a subfolder, then its parent, and shifts a camera's Date Taken there (or the reverse: the parent first,
 * the shift in the subfolder). Opened again within the half hour, with no scan, the other folder shows the new dates in
 * the server's order and the new stamps, and a save from it is sent with the stamp the file has now -- never the old one,
 * which the server would refuse as changed on disk (#3308). Another library's kept scan of the same folder is its own
 * (#1359) and is not touched; a kept scan that cannot be written back (local storage full) is forgotten, so the folder is
 * scanned when opened again, never shown from the records before the write. The page reloaded, or the library switched,
 * while the shift is out never takes its reply: every kept scan holding its photos is forgotten (#3323). A save of one
 * photo (its title, its Date Taken) reaches every kept scan holding it the same, re-keyed when its caption renamed it, and
 * its new Date Taken moves it in the open folder at once, as a shift does (owner, 2026-11-22); its reply taken after the
 * folder was left still reaches that folder's kept scan. A Smart Rename of the subfolder whose names renumber (an old
 * name of one photo the new name of another) leaves the kept parent holding each photo once, under its new name (#3329),
 * and each photo's own suggestions under its new name, in the open folder, its kept scan and the kept parent's (#3336);
 * one that swaps two names the same.
 * A tag write's side of the same: scenario-tagging-many-photos-at-once.test.mjs.
 *
 * Contract table row 23 (pages -> the page's saved scan cache), cache.js photosWritten, recordsWritten and photosGone. The
 * replies are the real entry point's (fixtures/shift_replies.json, harness.mjs shiftedAs; fixtures/save_replies.json,
 * saved and savedAs; fixtures/rename_replies.json, renamedAs); the records carry `order` as the scan's reply does.
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, closeAllApps, openFolder, click, flush, photoRecord, pageExports, SHIFT, shiftedAs, SAVE, saved,
  lastSave, savedAs, SAVE_REPLIES, renamedAs,
} from "./harness.mjs";

afterEach(() => closeAllApps());

const PARENT = "D:\\Pictures\\Regatta";
const CHILD = "D:\\Pictures\\Regatta\\Heats";
const CAMERA = "Tidewater TX R6m2";
const OTHER_CAMERA = "Lanternfly Pixel 8 Pro";

// A record as a scan's reply gives it: `order`, the server's key of its place (tagpup.core.dates taken_sort_key).
const at = (folder, filename, own) => {
  const record = photoRecord({ path: `${folder}\\${filename}`, filename, camera: OTHER_CAMERA, ...own });
  return { order: record.taken ?? `mtime_${record.mtime}`, ...record };
};
const dated = (taken) => ({ taken, order: taken });

// The subfolder: two photos of the camera shifted, one of another between them.
const H1 = at(CHILD, "IMG_0011.jpg", { camera: CAMERA, taken: "2020:06:01 10:10:00" });
const H3 = at(CHILD, "IMG_0013.jpg", { taken: "2020:06:01 10:15:00" });
const H2 = at(CHILD, "IMG_0012.jpg", { camera: CAMERA, taken: "2020:06:01 10:20:00" });
// The parent's own, and its scan holding the subfolder's: in the server's order, the undated after.
const P1 = at(PARENT, "IMG_0001.jpg", { taken: "2020:06:01 10:00:00" });
const P2 = at(PARENT, "IMG_0002.jpg", { taken: "2020:06:01 10:30:00" });
const P3 = at(PARENT, "IMG_0003.jpg", {});
const SCANS = { [CHILD]: [H1, H3, H2], [PARENT]: [P1, H1, H3, H2, P2, P3] };

// The camera moved by 30 minutes, as the server gives the two photos back (in the order sent: H1, H2).
const SHIFTED = shiftedAs("shifted", [dated("2020:06:01 10:40:00"), dated("2020:06:01 10:50:00")]);

// What Suggest found, by folder: the suggestions the server gives the page for a folder (a parent's hold its subfolders').
function server(shift, save, scans = SCANS, rename = "chained", suggested = {}) {
  return new FakeServer()
    .first(SHIFT, shift)
    .first(SAVE, save)
    .on("/api/folder/scan", (url) => (url.includes(encodeURIComponent(CHILD)) ? scans[CHILD] : scans[PARENT])
      .map((record) => ({ ...record })))
    .on("/api/folder/rename-photos", renamedAs(rename, CHILD))
    .on("/api/photo/rotate", { ...SAVE_REPLIES.rotated_picture_changed, pictures_refused: 0 })
    .on("/api/photo/delete", { success: true })
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", (url) => {
      const found = suggested[url.includes(encodeURIComponent(CHILD)) ? CHILD : PARENT];
      return found ? { status: "completed", suggestions: structuredClone(found) } : { status: "idle" };
    });
}

async function page(t, { library = "alpha", storage = {}, shift = SHIFTED, save = saved(), scans = SCANS, rename = "chained",
  suggested = {} } = {}) {
  const fake = server(shift, save, scans, rename, suggested);
  const ctx = await loadApp("tagpup", {
    t, server: fake, url: `http://localhost:8090/${library}/`,
    before: (window) => Object.entries(storage).forEach(([key, value]) => window.localStorage.setItem(key, value)),
  });
  ctx.fake = fake;
  ctx.window.alert = () => {};
  ctx.window.confirm = () => true;
  const $ = (id) => ctx.document.getElementById(id);
  ctx.$ = $;
  ctx.open = async (folder) => {
    await openFolder(ctx, folder);
    await flush(ctx.window, 10);
  };
  ctx.scansOf = (folder) => fake.calls.filter((c) => c.url.includes("/api/folder/scan")
    && (folder === CHILD) === c.url.includes(encodeURIComponent(CHILD))).length;
  ctx.shown = () => [...pageExports(ctx.window, "web/tagpup/state.js").state.folderPhotos]
    .map((p) => [p.filename, p.taken, p.mtime, p.size]);
  ctx.shift = async () => {
    click(ctx.window, $("btn-toggle-timeshift"));
    $("timeshift-camera-select").value = CAMERA;
    $("timeshift-minutes-input").value = "30";
    const apply = $("btn-apply-timeshift");
    apply.disabled = false;
    click(ctx.window, apply);
    await flush(ctx.window, 10);
  };
  // The photo's panel opened from its folder's list, and its title saved with the panel's Save.
  ctx.select = async (record) => {
    click(ctx.window, [...ctx.document.querySelectorAll(".photo-item-file")].find((el) => el.dataset.path === record.path));
    await flush(ctx.window, 6);
  };
  ctx.saveTitle = async (title) => {
    ctx.$("input-photo-title").value = title;
    click(ctx.window, ctx.$("btn-save-title"));
    await flush(ctx.window, 10);
  };
  ctx.kept = () => Object.keys(ctx.storage()).filter((key) => key.startsWith("tagpup_cache_alpha/"));
  ctx.storage = () => Object.fromEntries(Object.keys(ctx.window.localStorage)
    .map((key) => [key, ctx.window.localStorage.getItem(key)]));
  return ctx;
}

const BEFORE = (record) => [record.filename, record.taken, record.mtime, record.size];
// The two photos written: their new dates, and the stamp the reply gives (fixtures/shift_replies.json).
const H1_AFTER = ["IMG_0011.jpg", "2020:06:01 10:40:00", 1, 1];
const H2_AFTER = ["IMG_0012.jpg", "2020:06:01 10:50:00", 1, 1];

test("a subfolder kept, its parent shifted: the subfolder opened again shows the new dates in the server's order, and a save from it sends the new stamp", async (t) => {
  const ctx = await page(t);
  await ctx.open(CHILD);
  await ctx.open(PARENT);
  await ctx.shift();
  assert.deepEqual(Object.keys(ctx.fake.calls.find((c) => SHIFT(c.url, c.body)).body.changes), [H1.path, H2.path]);
  assert.deepEqual(ctx.shown(), [BEFORE(P1), BEFORE(H3), BEFORE(P2), H1_AFTER, H2_AFTER, BEFORE(P3)],
    "the open parent in the server's order, its undated photo last");

  await ctx.open(CHILD);
  assert.equal(ctx.scansOf(CHILD), 1, "drawn from the scan kept, not scanned again");
  assert.deepEqual(ctx.shown(), [BEFORE(H3), H1_AFTER, H2_AFTER], "the kept subfolder took the shift, in the server's order");

  click(ctx.window, [...ctx.document.querySelectorAll(".photo-item-file")].find((el) => el.dataset.path === H1.path));
  await flush(ctx.window, 6);
  ctx.$("input-photo-title").value = "Start line";
  click(ctx.window, ctx.$("btn-save-title"));
  await flush(ctx.window, 10);
  const sent = lastSave(ctx.fake);
  assert.equal(sent.path, H1.path);
  assert.deepEqual(sent.stamp, { mtime: 1, size: 1 }, "the save names the file as the shift left it");
});

test("a parent kept, its subfolder shifted: the parent opened again shows the new dates in the server's order", async (t) => {
  const ctx = await page(t);
  await ctx.open(PARENT);
  await ctx.open(CHILD);
  await ctx.shift();
  assert.deepEqual(ctx.shown(), [BEFORE(H3), H1_AFTER, H2_AFTER]);

  const scans = ctx.scansOf(PARENT);
  await ctx.open(PARENT);
  assert.equal(ctx.scansOf(PARENT), scans, "drawn from the scan kept, not scanned again");
  assert.deepEqual(ctx.shown(), [BEFORE(P1), BEFORE(H3), BEFORE(P2), H1_AFTER, H2_AFTER, BEFORE(P3)]);
});

test("another library's kept scan of the same folder is its own: a shift in this library leaves it as it was", async (t) => {
  // The other library looked at the subfolder in this browser first (two libraries can hold one folder).
  const other = await page(t, { library: "beta" });
  await other.open(CHILD);
  const storage = other.storage();
  const betaKey = Object.keys(storage).find((key) => key.startsWith("tagpup_cache_beta/"));
  assert.ok(betaKey, "the other library kept no scan of the subfolder");

  const ctx = await page(t, { storage });
  await ctx.open(CHILD);
  await ctx.open(PARENT);
  await ctx.shift();
  const after = ctx.storage();
  assert.equal(after[betaKey], storage[betaKey], "the other library's kept scan was changed");
  const alphaKey = Object.keys(after).find((key) => key.startsWith("tagpup_cache_alpha/") && key.endsWith("\\heats"));
  assert.deepEqual(JSON.parse(after[alphaKey]).photos.map(BEFORE), [BEFORE(H3), H1_AFTER, H2_AFTER],
    "this library's kept scan of the subfolder took the shift");
});

test("a kept scan that cannot be written back is forgotten, and the folder opened again is scanned, not shown as before", async (t) => {
  const ctx = await page(t);
  await ctx.open(CHILD);
  await ctx.open(PARENT);
  const keys = Object.keys(ctx.storage()).filter((key) => key.startsWith("tagpup_cache_alpha/"));
  assert.equal(keys.length, 2, "both folders kept");

  // Local storage full: no kept scan can be written back while the reply is taken.
  const storage = Object.getPrototypeOf(ctx.window.localStorage);
  const real = storage.setItem;
  storage.setItem = function (key, value) {
    if (String(key).startsWith("tagpup_cache_")) throw new ctx.window.DOMException("The quota has been exceeded.", "QuotaExceededError");
    return real.call(this, key, value);
  };
  try {
    await ctx.shift();
  } finally {
    storage.setItem = real;
  }
  assert.deepEqual(Object.keys(ctx.storage()).filter((key) => key.startsWith("tagpup_cache_alpha/")), [],
    "a kept scan holding the photos as they were before the shift is left");

  await ctx.open(CHILD);
  assert.equal(ctx.scansOf(CHILD), 2, "the subfolder was not scanned again");
});

test("the page left while a shift is out forgets every kept scan holding its photos", async (t) => {
  // The reply never comes: the page reloaded, or another library was chosen (a page load), with the shift out.
  const ctx = await page(t, { shift: () => new Promise(() => {}) });
  await ctx.open(CHILD);
  await ctx.open(PARENT);
  await ctx.shift();
  assert.ok(ctx.fake.calls.some((c) => SHIFT(c.url, c.body)), "the shift was not sent");
  ctx.window.dispatchEvent(new ctx.window.Event("pagehide"));
  assert.deepEqual(Object.keys(ctx.storage()).filter((key) => key.startsWith("tagpup_cache_alpha/")), [],
    "a kept scan holding the photos as they were before the shift is left");
});

test("the page left while a single save is out forgets every kept scan holding its photo", async (t) => {
  // A title save of one photo, its reply never taken: the page reloaded, or another library chosen, with it out.
  const ctx = await page(t, { save: () => new Promise(() => {}) });
  await ctx.open(PARENT);
  await ctx.open(CHILD);
  await ctx.select(H1);
  await ctx.saveTitle("Start line");
  assert.equal(lastSave(ctx.fake).path, H1.path, "the save was not sent");
  assert.equal(ctx.kept().length, 2, "both folders kept");
  ctx.window.dispatchEvent(new ctx.window.Event("pagehide"));
  assert.deepEqual(ctx.kept(), [], "a kept scan holding the photo as it was before the save is left");
});

test("a photo turned from its subfolder: the kept parent names the stamp the turn left", async (t) => {
  const ctx = await page(t);
  await ctx.open(PARENT);
  await ctx.open(CHILD);
  await ctx.select(H1);
  click(ctx.window, ctx.$("btn-rotate-right"));
  await flush(ctx.window, 10);
  const record = keptOf(ctx, PARENT).find((p) => p.path === H1.path);
  assert.deepEqual([record.mtime, record.size], [1, 1], "the kept parent names the stamp from before the turn");
});

test("a photo deleted from its subfolder: no kept scan holds it, and the parent opened again is scanned", async (t) => {
  const ctx = await page(t);
  await ctx.open(PARENT);
  await ctx.open(CHILD);
  await ctx.select(H1);
  click(ctx.window, ctx.$("btn-delete-photo"));
  await flush(ctx.window, 10);
  assert.deepEqual(ctx.kept().map((key) => JSON.parse(ctx.storage()[key]).photos.some((p) => p.path === H1.path)), [false],
    "a kept scan still holds the photo deleted");
  await ctx.open(PARENT);
  assert.equal(ctx.scansOf(PARENT), 2, "the parent was drawn from a kept scan holding the photo deleted");
});

const keptOf = (ctx, folder) => JSON.parse(ctx.storage()[`tagpup_cache_alpha/${folder.toLowerCase()}`]).photos;
const STAMP = (record) => [record.filename, record.title, record.mtime, record.size];

test("a save's reply taken after its folder was left reaches that folder's kept scan and the one open, and the next save names the new stamp", async (t) => {
  const held = [];
  const reply = saved();
  const ctx = await page(t, { save: (url, body) => new Promise((resolve) => held.push(() => resolve(reply(url, body)))) });
  await ctx.open(PARENT);
  await ctx.open(CHILD);
  await ctx.select(H1);
  await ctx.saveTitle("Start line");
  await ctx.open(PARENT);
  assert.equal(ctx.scansOf(PARENT), 1, "the parent was scanned again, not drawn from its kept scan");
  held.shift()();
  await flush(ctx.window, 10);
  const after = ["IMG_0011.jpg", "Start line", H1.mtime + 1, H1.size];
  assert.deepEqual(STAMP(pageExports(ctx.window, "web/tagpup/state.js").state.folderPhotos.find((p) => p.path === H1.path)),
    after, "the open parent's record of the photo kept the stamp from before the save");
  assert.deepEqual(STAMP(keptOf(ctx, PARENT).find((p) => p.path === H1.path)), after);
  assert.deepEqual(STAMP(keptOf(ctx, CHILD).find((p) => p.path === H1.path)), after,
    "the folder the save was made in kept the record from before it");

  await ctx.open(CHILD);
  assert.equal(ctx.scansOf(CHILD), 1, "the subfolder was scanned again, not drawn from its kept scan");
  await ctx.select(H1);
  await ctx.saveTitle("Finish line");
  assert.deepEqual(lastSave(ctx.fake).stamp, { mtime: H1.mtime + 1, size: H1.size }, "the next save names the old stamp");
});

test("a title save that renames its photo re-keys it in the kept parent: opened again, a save from it names the new name and stamp", async (t) => {
  const renamed = `${CHILD}\\Regatta - Start line.jpg`;
  const ctx = await page(t, { save: saved({ new_path: renamed }) });
  await ctx.open(PARENT);
  await ctx.open(CHILD);
  await ctx.select(H1);
  await ctx.saveTitle("Start line");
  const kept = keptOf(ctx, PARENT);
  assert.equal(kept.filter((p) => p.path === H1.path).length, 0, "the kept parent holds the photo by the name it had");
  assert.deepEqual(STAMP(kept.find((p) => p.path === renamed)), ["Regatta - Start line.jpg", "Start line", H1.mtime + 1, H1.size]);

  await ctx.open(PARENT);
  assert.equal(ctx.scansOf(PARENT), 1, "the parent was scanned again, not drawn from its kept scan");
  await ctx.select({ path: renamed });
  await ctx.saveTitle("Finish line");
  const sent = lastSave(ctx.fake);
  assert.equal(sent.path, renamed);
  assert.deepEqual(sent.stamp, { mtime: H1.mtime + 1, size: H1.size });
});

test("a Date Taken saved moves the photo to its new place at once, in the open folder and the kept parent, as the server orders it", async (t) => {
  const later = "2020:06:01 10:25:00";
  const ctx = await page(t, { save: savedAs(SAVE_REPLIES.dated, { taken: later, order: later }) });
  await ctx.open(PARENT);
  await ctx.open(CHILD);
  await ctx.select(H1);
  click(ctx.window, ctx.$("btn-edit-date-taken"));
  ctx.$("input-date-taken").value = "2020-06-01T10:25:00";
  click(ctx.window, ctx.$("btn-save-date-modal"));
  await flush(ctx.window, 10);
  const moved = ["IMG_0011.jpg", later, 1, 1];
  assert.deepEqual(ctx.shown(), [BEFORE(H3), BEFORE(H2), moved], "the photo stayed where its old date put it");
  assert.deepEqual([...ctx.document.querySelectorAll(".photo-item-file")].map((el) => el.dataset.path),
    [H3.path, H2.path, H1.path], "the folder's list was not drawn in the new order");
  assert.deepEqual(keptOf(ctx, PARENT).map(BEFORE), [BEFORE(P1), BEFORE(H3), BEFORE(H2), moved, BEFORE(P2), BEFORE(P3)],
    "the kept parent holds the old date, in the old order");
});

// The subfolder before a Smart Rename that renumbers (fixtures/rename_replies.json `chained`): a photo taken first, and two
// Smart-Renamed after it, each moving up one: IMG_0001 -> Regatta - 1, Regatta - 1 -> Regatta - 2, Regatta - 2 -> Regatta - 3.
const R0 = at(CHILD, "IMG_0001.jpg", { taken: "2020:06:01 10:00:00" });
const R1 = at(CHILD, "Regatta - 1.jpg", { taken: "2020:06:01 10:10:00" });
const R2 = at(CHILD, "Regatta - 2.jpg", { taken: "2020:06:01 10:20:00" });
const RENUMBERED = { [CHILD]: [R0, R1, R2], [PARENT]: [P1, R0, R1, R2, P2, P3] };

// The open folder's photos all selected and Smart-Renamed "Regatta" from its panel.
async function renameAll(ctx) {
  for (const box of ctx.document.querySelectorAll(".thumbnail-card .thumbnail-checkbox")) click(ctx.window, box);
  ctx.$("rename-grouping-input").value = "Regatta";
  const apply = ctx.$("btn-apply-rename");
  apply.disabled = false;   // enabled by the panel's own toggle, which is not under test
  click(ctx.window, apply);
  await flush(ctx.window, 10);
}

const offer = (tag) => ({ tags: [{ tag, score: 0.9 }], people: [], title: "" });
// Each suggestion by the photo it is for, by its tag: the page's, or a kept scan's.
const offered = (suggestions) => Object.fromEntries(Object.entries(suggestions).map(([path, s]) => [path, s.tags[0].tag]));
const suggestionsOf = (ctx) => pageExports(ctx.window, "web/tagpup/state.js").state.folderSuggestions;
const keptSuggestionsOf = (ctx, folder) => JSON.parse(ctx.storage()[`tagpup_cache_alpha/${folder.toLowerCase()}`]).suggestions;
const named = (filename) => `${CHILD}\\${filename}`;

test("a Smart Rename that renumbers the subfolder leaves the kept parent holding each photo once, under its new name and stamp", async (t) => {
  const ctx = await page(t, { scans: RENUMBERED });
  await ctx.open(PARENT);
  await ctx.open(CHILD);
  await renameAll(ctx);
  assert.deepEqual(new Set(ctx.fake.calls.find((c) => c.url.includes("rename-photos")).body.photo_paths),
    new Set([R0.path, R1.path, R2.path]), "the whole subfolder was not sent");
  const renamed = [["Regatta - 1.jpg", "2020:06:01 10:00:00", 1, 1], ["Regatta - 2.jpg", "2020:06:01 10:10:00", 1, 1],
    ["Regatta - 3.jpg", "2020:06:01 10:20:00", 1, 1]];
  assert.deepEqual(ctx.shown(), renamed, "the open subfolder as the reply gave it");
  const NAMED = (record) => [record.filename, record.taken, record.mtime, record.size, record.path.split("\\").pop()];
  assert.deepEqual(keptOf(ctx, PARENT).map(NAMED), [BEFORE(P1), ...renamed, BEFORE(P2), BEFORE(P3)]
    .map(([filename, ...rest]) => [filename, ...rest, filename]),
  "the kept parent holds a photo twice, or one gone, or a record under a name it no longer has");

  await ctx.open(PARENT);
  assert.equal(ctx.scansOf(PARENT), 1, "the parent was scanned again, not drawn from its kept scan");
  assert.deepEqual(ctx.shown(), [BEFORE(P1), ...renamed, BEFORE(P2), BEFORE(P3)]);
});

test("a Smart Rename that renumbers the subfolder moves each photo's own suggestions to its new name, in the open folder and every scan kept", async (t) => {
  const own = { [R0.path]: offer("Trips/Start"), [R1.path]: offer("Trips/Middle"), [R2.path]: offer("Trips/Finish") };
  const ctx = await page(t, { scans: RENUMBERED,
    suggested: { [CHILD]: own, [PARENT]: { ...own, [P1.path]: offer("Trips/Harbour") } } });
  await ctx.open(PARENT);
  await ctx.open(CHILD);
  assert.deepEqual(offered(suggestionsOf(ctx)), offered(own), "the subfolder's suggestions before the rename");
  await renameAll(ctx);
  const after = { [named("Regatta - 1.jpg")]: "Trips/Start", [named("Regatta - 2.jpg")]: "Trips/Middle",
    [named("Regatta - 3.jpg")]: "Trips/Finish" };
  assert.deepEqual(offered(suggestionsOf(ctx)), after, "a photo holds another's suggestions, or lost its own");
  assert.deepEqual(offered(keptSuggestionsOf(ctx, CHILD)), after, "the open folder's kept scan");
  assert.deepEqual(offered(keptSuggestionsOf(ctx, PARENT)), { [P1.path]: "Trips/Harbour", ...after }, "the kept parent's");
});

// The subfolder before a Smart Rename that swaps two names (fixtures/rename_replies.json `swapped`): Regatta - 1 was dated
// after Regatta - 2, so each takes the other's name; Regatta - 3 keeps its own.
const W1 = at(CHILD, "Regatta - 1.jpg", { taken: "2020:06:01 10:15:00" });
const W2 = at(CHILD, "Regatta - 2.jpg", { taken: "2020:06:01 10:05:00" });
const W3 = at(CHILD, "Regatta - 3.jpg", { taken: "2020:06:01 10:20:00" });
const SWAPPING = { [CHILD]: [W2, W1, W3], [PARENT]: [P1, W2, W1, W3, P2, P3] };

test("a Smart Rename that swaps two names: each photo keeps its own suggestions and records under the other's old name", async (t) => {
  const own = { [W1.path]: offer("Trips/Late"), [W2.path]: offer("Trips/Early"), [W3.path]: offer("Trips/Finish") };
  const ctx = await page(t, { scans: SWAPPING, rename: "swapped", suggested: { [CHILD]: own, [PARENT]: own } });
  await ctx.open(PARENT);
  await ctx.open(CHILD);
  await renameAll(ctx);
  const after = { [named("Regatta - 1.jpg")]: "Trips/Early", [named("Regatta - 2.jpg")]: "Trips/Late",
    [named("Regatta - 3.jpg")]: "Trips/Finish" };
  assert.deepEqual(offered(suggestionsOf(ctx)), after, "a photo holds another's suggestions, or lost its own");
  assert.deepEqual(offered(keptSuggestionsOf(ctx, CHILD)), after, "the open folder's kept scan");
  assert.deepEqual(offered(keptSuggestionsOf(ctx, PARENT)), after, "the kept parent's");
  const swapped = [["Regatta - 1.jpg", "2020:06:01 10:05:00", 2, 2], ["Regatta - 2.jpg", "2020:06:01 10:15:00", 2, 2],
    ["Regatta - 3.jpg", "2020:06:01 10:20:00", 2, 2]];
  assert.deepEqual(ctx.shown(), swapped, "the open subfolder as the reply gave it");
  assert.deepEqual(keptOf(ctx, PARENT).map((record) => [record.filename, record.taken, record.mtime, record.size]),
    [BEFORE(P1), ...swapped, BEFORE(P2), BEFORE(P3)], "the kept parent holds a photo twice, or under the other's name");
});
