/**
 * Owner scenario I9, a write's side: this browser keeps each folder's scan for half an hour (web/tagpup/cache.js), and a
 * folder's scan is recursive, so a photo is held by the scan of its own folder and by the scan of every folder above it.
 * The owner opens a subfolder, then its parent, and shifts a camera's Date Taken there (or the reverse: the parent first,
 * the shift in the subfolder). Opened again within the half hour, with no scan, the other folder shows the new dates in
 * the server's order and the new stamps, and a save from it is sent with the stamp the file has now -- never the old one,
 * which the server would refuse as changed on disk (#3308). Another library's kept scan of the same folder is its own
 * (#1359) and is not touched; a kept scan that cannot be written back (local storage full) is forgotten, so the folder is
 * scanned when opened again, never shown from the records before the write.
 *
 * Contract table row 23 (pages -> the page's saved scan cache), cache.js photosWritten. The replies are the real entry
 * point's (fixtures/shift_replies.json, harness.mjs shiftedAs); the records carry `order` as the scan's reply does.
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, closeAllApps, openFolder, click, flush, photoRecord, pageExports, SHIFT, shiftedAs, SAVE, saved,
  lastSave,
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

function server() {
  return new FakeServer()
    .first(SHIFT, SHIFTED)
    .first(SAVE, saved())
    .on("/api/folder/scan", (url) => (url.includes(encodeURIComponent(CHILD)) ? SCANS[CHILD] : SCANS[PARENT])
      .map((record) => ({ ...record })))
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" });
}

async function page(t, { library = "alpha", storage = {} } = {}) {
  const fake = server();
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
