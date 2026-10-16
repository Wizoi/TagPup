/**
 * Owner scenario I9: TagPup is updated while this browser still keeps the scan an older page made of a folder (a folder's
 * scan is kept in local storage for half an hour, web/tagpup/cache.js). The new page reads records of the shape it knows;
 * a kept scan of another shape -- no version at all (a page from before the version), or version 1 (records with no
 * `camera`, so Shift Date Taken listed every photo as "Unknown Camera") -- must be scanned again, its records never
 * shown. A kept scan of the current version is used as it is (the control: the check is not "never use a scan").
 *
 * Contract table row 23 (pages -> the page's saved scan cache). Mutation: folder.js's check without
 * `cacheEntry.version === CACHE_VERSION` fails the two older shapes below.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, openFolder, closeAllApps, pageExports } from "./harness.mjs";

const FOLDER = "D:/Library/2020";
const PHOTOS = [
  photoRecord({ filename: "a.jpg", tags: [], people: [] }),
  photoRecord({ filename: "b.jpg", tags: [], people: [] }),
];

function server() {
  return new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["alpha"] })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", () => PHOTOS.map((p) => ({ ...p, tags: [], people: [] })))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" });
}

const kept = (window) => Object.fromEntries(Object.keys(window.localStorage).map((k) => [k, window.localStorage.getItem(k)]));
const names = (ctx) => [...pageExports(ctx.window, "web/tagpup/state.js").state.folderPhotos].map((p) => p.filename).sort();
const scans = (ctx) => ctx.server.urls().filter((u) => u.includes("/api/folder/scan")).length;

/** This browser's storage after the current page scanned the folder, with its kept scan rewritten by `older`. */
async function keptBy(t, older) {
  const first = await loadApp("tagpup", { t, url: "http://localhost:8090/alpha/", server: server() });
  await openFolder(first, FOLDER);
  const storage = kept(first.window);
  const key = Object.keys(storage).find((k) => k.startsWith("tagpup_cache_alpha/"));
  assert.ok(key, "the page kept no scan of the folder");
  storage[key] = JSON.stringify(older(JSON.parse(storage[key])));
  return storage;
}

/** The page loaded again on the folder, over that storage. */
async function reopen(t, storage) {
  const ctx = await loadApp("tagpup", {
    t, server: server(), url: "http://localhost:8090/alpha/?path=" + encodeURIComponent(FOLDER),
    before: (window) => Object.entries(storage).forEach(([k, v]) => window.localStorage.setItem(k, v)),
  });
  await flush(ctx.window, 10);
  return ctx;
}

// What an older page kept: its own records (one photo, no camera), and its version or none.
const olderShape = (version) => (entry) => {
  const old = { ...entry, photos: [{ filename: "old.jpg", path: FOLDER + "/old.jpg", tags: [], people: [] }] };
  if (version === undefined) delete old.version;
  else old.version = version;
  return old;
};

afterEach(() => closeAllApps());

describe("a folder whose kept scan an older page made, opened after an update", () => {
  for (const [what, version] of [["with no version", undefined], ["of version 1", 1]]) {
    test(`a kept scan ${what} is scanned again, its records not shown`, async (t) => {
      const ctx = await reopen(t, await keptBy(t, olderShape(version)));
      assert.equal(scans(ctx), 1, "the folder was not scanned again");
      assert.deepEqual(names(ctx), ["a.jpg", "b.jpg"], "the older page's records were shown");
    });
  }

  test("a kept scan of the current shape is used, with no scan", async (t) => {
    const ctx = await reopen(t, await keptBy(t, (entry) => entry));
    assert.equal(scans(ctx), 0, "a scan kept by this page was not used");
    assert.deepEqual(names(ctx), ["a.jpg", "b.jpg"]);
  });
});
